#!/usr/bin/env python3
"""
Testes do controle PTZ (Issues #22 e #23).

- #22: seleção trocada durante a inicialização ONVIF não pode deixar o cliente apontando para a
  câmera anterior; o cliente de cada câmera fica em cache.
- #23: comandos de uma câmera são enviados em série (Move antes de Stop), o ContinuousMove leva
  Timeout e falhas aparecem como falha.
"""
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtWidgets import QApplication

from central_nvr.core.camera import CameraDevice
from central_nvr.core.onvif_client import OnvifClient, OnvifConnectionError
from central_nvr.core.ptz_queue import SerialCommandQueue


def _wait_until(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def _pump(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


class FakeOnvifClient:
    """Cliente ONVIF falso: a câmera 'lenta' demora para responder à inicialização."""

    init_count = 0
    calls = []
    lock = threading.Lock()
    slow_ips = set()
    fail_commands = False
    responding_ports = None  # None = todas as portas respondem ONVIF

    def _check_port(self):
        if FakeOnvifClient.responding_ports is not None and self.port not in FakeOnvifClient.responding_ports:
            raise OnvifConnectionError(f"porta {self.port} fechada")

    def __init__(self, ip, port=80, username="", password="", timeout=4.0):
        self.ip = ip
        self.port = port
        self.media_service_url = f"http://{ip}:{port}/onvif/media_service"
        self.ptz_service_url = f"http://{ip}:{port}/onvif/ptz_service"

    def get_capabilities(self):
        with FakeOnvifClient.lock:
            FakeOnvifClient.init_count += 1
        if self.ip in FakeOnvifClient.slow_ips:
            time.sleep(0.4)
        self._check_port()
        return {}

    def get_profiles(self):
        self._check_port()
        return [{"token": "p1"}]

    def ptz_get_presets(self):
        return []

    def _record(self, name, delay=0.0):
        if delay:
            time.sleep(delay)
        with FakeOnvifClient.lock:
            FakeOnvifClient.calls.append((self.ip, name))
        return not FakeOnvifClient.fail_commands

    def ptz_continuous_move(self, pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
        # O Move é mais lento que o Stop: sem fila serial, o Stop chegaria primeiro
        return self._record("move", delay=0.15)

    def ptz_stop(self):
        return self._record("stop")

    def ptz_set_preset(self, name):
        return None if FakeOnvifClient.fail_commands else "tok"


def _camera(cam_id, ip):
    return CameraDevice(id=cam_id, name=f"Camera {cam_id}", ip=ip, port=80, has_ptz=True)


class SerialCommandQueueTest(unittest.TestCase):
    def test_commands_run_in_submission_order(self):
        queue = SerialCommandQueue(name="test")
        order = []
        done = threading.Event()
        queue.submit(lambda: (time.sleep(0.1), order.append("move")))
        queue.submit(lambda: order.append("stop"))
        queue.submit(done.set)
        self.assertTrue(done.wait(2.0))
        self.assertEqual(order, ["move", "stop"])
        queue.shutdown()

    def test_exception_in_command_does_not_stop_queue(self):
        queue = SerialCommandQueue(name="test")
        done = threading.Event()

        def boom():
            raise RuntimeError("falha de rede")

        queue.submit(boom)
        queue.submit(done.set)
        self.assertTrue(done.wait(2.0))
        queue.shutdown()


class ContinuousMoveTimeoutTest(unittest.TestCase):
    def test_continuous_move_sends_timeout(self):
        client = OnvifClient(ip="10.0.0.5", port=80)
        client.active_profile_token = "p1"
        bodies = []
        with patch.object(client, "_send_soap_request", side_effect=lambda url, body, action="": bodies.append(body) or "<ok/>"):
            client.ptz_continuous_move(pan_speed=0.5)
        self.assertIn("<tptz:Timeout>PT2S</tptz:Timeout>", bodies[0])
        # Ordem do schema ONVIF: ProfileToken, Velocity, Timeout
        self.assertLess(bodies[0].index("</tptz:Velocity>"), bodies[0].index("<tptz:Timeout>"))


class PTZControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeOnvifClient.init_count = 0
        FakeOnvifClient.calls = []
        FakeOnvifClient.slow_ips = set()
        FakeOnvifClient.fail_commands = False
        FakeOnvifClient.responding_ports = None
        self.patcher = patch("central_nvr.ui.ptz_controller.OnvifClient", FakeOnvifClient)
        self.patcher.start()
        from central_nvr.ui.ptz_controller import PTZControllerWidget
        self.widget = PTZControllerWidget()

    def tearDown(self):
        self.widget.shutdown()
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()
        self.patcher.stop()

    def test_out_of_order_init_keeps_client_on_latest_selection(self):
        """#22: A (lenta) e logo depois B (rápida) → o cliente aponta para B."""
        FakeOnvifClient.slow_ips = {"10.0.0.1"}
        cam_a, cam_b = _camera("a", "10.0.0.1"), _camera("b", "10.0.0.2")
        self.widget.set_camera(cam_a)
        self.widget.set_camera(cam_b)

        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        # Espera a inicialização lenta de A terminar e tentar sobrescrever o cliente
        self.assertTrue(_wait_until(self.app, lambda: FakeOnvifClient.init_count >= 2))
        _pump(self.app, 0.6)
        self.assertEqual(self.widget.client.ip, "10.0.0.2")

    def test_selecting_same_camera_reuses_cached_client(self):
        """#22: a segunda seleção da mesma câmera não refaz GetCapabilities/GetProfiles."""
        cam_a = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam_a)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        first_client = self.widget.client

        self.widget.set_camera(_camera("b", "10.0.0.2"))
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None and self.widget.client.ip == "10.0.0.2"))

        self.widget.set_camera(cam_a)
        self.assertIs(self.widget.client, first_client)
        self.assertEqual(FakeOnvifClient.init_count, 2)

    def test_changed_credentials_invalidate_cache(self):
        cam_a = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam_a)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        edited = _camera("a", "10.0.0.1")
        edited.password = "nova"
        self.widget.set_camera(edited)
        self.assertTrue(_wait_until(self.app, lambda: FakeOnvifClient.init_count == 2))

    def test_press_then_release_sends_move_before_stop(self):
        """#23: pressed seguido imediatamente de released sempre envia Move antes de Stop."""
        self.widget.set_camera(_camera("a", "10.0.0.1"))
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))

        self.widget._start_ptz_move(1.0, 0.0)
        self.widget._stop_ptz()

        self.assertTrue(_wait_until(self.app, lambda: len(FakeOnvifClient.calls) >= 2))
        self.assertEqual([c[1] for c in FakeOnvifClient.calls[:2]], ["move", "stop"])

    def test_failed_command_is_reported_as_failure(self):
        """#23: falha de comando é emitida como falha, não como sucesso."""
        self.widget.set_camera(_camera("a", "10.0.0.1"))
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        FakeOnvifClient.fail_commands = True

        failures, successes = [], []
        self.widget.command_failed.connect(lambda cam, msg: failures.append(msg))
        self.widget.command_executed.connect(lambda cam, msg: successes.append(msg))

        self.widget._stop_ptz()
        self.assertTrue(_wait_until(self.app, lambda: len(failures) >= 1))
        self.assertEqual(successes, [])
        # Stop é reenviado antes de desistir
        self.assertGreaterEqual(sum(1 for c in FakeOnvifClient.calls if c[1] == "stop"), 2)

    def test_failed_set_preset_does_not_add_preset(self):
        self.widget.set_camera(_camera("a", "10.0.0.1"))
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        FakeOnvifClient.fail_commands = True
        failures = []
        self.widget.command_failed.connect(lambda cam, msg: failures.append(msg))
        with patch("central_nvr.ui.ptz_controller.QInputDialog.getText", return_value=("Portão", True)):
            self.widget._save_preset()
        self.assertTrue(_wait_until(self.app, lambda: len(failures) >= 1))
        self.assertEqual(self.widget.combo_presets.count(), 0)

    def test_closed_port_falls_back_to_5000(self):
        """#44: porta configurada fechada e 5000 respondendo → o cliente usa a 5000."""
        FakeOnvifClient.responding_ports = {5000}
        detected = []
        self.widget.onvif_port_detected.connect(lambda cam_id, port: detected.append((cam_id, port)))
        cam = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        self.assertEqual(self.widget.client.port, 5000)
        # Persistência: porta atualizada na thread da interface e sinal para salvar
        self.assertEqual(cam.port, 5000)
        self.assertEqual(detected, [("a", 5000)])

    def test_detected_port_is_cached_under_new_port(self):
        FakeOnvifClient.responding_ports = {5000}
        cam = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        self.widget.set_camera(None)
        self.widget.set_camera(cam)  # agora com port=5000
        self.assertIsNotNone(self.widget.client)

    def test_no_port_responds_means_no_client(self):
        """#44: nenhuma porta responde → PTZ indisponível, sem cliente ativo."""
        FakeOnvifClient.responding_ports = set()
        unavailable = []
        self.widget.command_failed.connect(lambda cam, msg: unavailable.append(msg))
        self.widget.set_camera(_camera("a", "10.0.0.1"))
        self.assertTrue(_wait_until(self.app, lambda: len(unavailable) >= 1))
        self.assertIsNone(self.widget.client)
        self.assertFalse(self.widget.controls_frame.isEnabled())

    def test_port_detected_after_selection_changed_is_persisted_on_return(self):
        """#44: probe de A termina depois de selecionar B; ao voltar para A a porta é aplicada e salva."""
        FakeOnvifClient.responding_ports = {5000}
        FakeOnvifClient.slow_ips = {"10.0.0.1"}
        detected = []
        self.widget.onvif_port_detected.connect(lambda cam_id, port: detected.append((cam_id, port)))
        cam_a, cam_b = _camera("a", "10.0.0.1"), _camera("b", "10.0.0.2")
        self.widget.set_camera(cam_a)
        self.widget.set_camera(cam_b)
        # O probe lento de A (porta 80 fechada, depois 5000) termina e vai para o cache
        self.assertTrue(_wait_until(
            self.app, lambda: any(k[0] == "a" for k in self.widget._client_cache), timeout=5))
        self.assertEqual(cam_a.port, 80)
        self.widget.set_camera(cam_a)
        self.assertEqual(self.widget.client.port, 5000)
        self.assertEqual(cam_a.port, 5000)
        self.assertIn(("a", 5000), detected)

    def test_probe_does_not_mutate_camera_in_background(self):
        FakeOnvifClient.responding_ports = {5000}
        cam = _camera("a", "10.0.0.1")
        client = self.widget._probe_client(cam)
        self.assertEqual(client.port, 5000)
        self.assertEqual(cam.port, 80)

    def test_no_fictitious_initial_preset(self):
        self.assertEqual(self.widget.combo_presets.count(), 0)

    def test_controls_disabled_without_ptz(self):
        cam = _camera("a", "10.0.0.1")
        cam.has_ptz = False
        self.widget.set_camera(cam)
        self.assertFalse(self.widget.controls_frame.isEnabled())
        self.widget.set_camera(_camera("b", "10.0.0.2"))
        self.assertTrue(self.widget.controls_frame.isEnabled())
        self.widget.set_camera(None)
        self.assertFalse(self.widget.controls_frame.isEnabled())

    def test_no_ptz_camera_does_not_probe_or_emit_failure(self):
        """#54: Dispositivo Cadastrado sem PTZ não inicia sondagem ONVIF nem emite command_failed."""
        failures = []
        self.widget.command_failed.connect(lambda cam, msg: failures.append((cam, msg)))

        cam = _camera("no_ptz", "10.0.0.9")
        cam.has_ptz = False

        for _ in range(3):
            self.widget.set_camera(cam)
            _pump(self.app, 0.05)
            self.widget.set_camera(None)
            self.widget.set_camera(cam)

        _pump(self.app, 0.15)
        self.assertEqual(FakeOnvifClient.init_count, 0)
        self.assertIsNone(self.widget.client)
        self.assertFalse(self.widget.controls_frame.isEnabled())
        self.assertEqual(failures, [])

    def test_negative_cache_avoids_repeated_probes_and_expires_after_ttl(self):
        """#54: falha de sondagem ONVIF entra em cache negativo e só re-sonda após o TTL."""
        FakeOnvifClient.responding_ports = set()
        failures = []
        self.widget.command_failed.connect(lambda cam, msg: failures.append((cam, msg)))

        cam_a = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam_a)
        self.assertTrue(_wait_until(self.app, lambda: len(failures) == 1))
        self.assertFalse(self.widget.controls_frame.isEnabled())
        self.assertIsNone(self.widget.client)
        first_init_count = FakeOnvifClient.init_count
        self.assertGreater(first_init_count, 0)

        # Selecionar outra câmera (ou None) e retornar: cache negativo evita nova sondagem e spam
        self.widget.set_camera(None)
        self.widget.set_camera(cam_a)
        _pump(self.app, 0.15)
        self.assertEqual(FakeOnvifClient.init_count, first_init_count)
        self.assertEqual(len(failures), 1)
        self.assertFalse(self.widget.controls_frame.isEnabled())
        self.assertIsNone(self.widget.client)

        # Expirar o TTL do cache negativo permite nova sondagem
        key = self.widget._client_key(cam_a)
        self.assertIn(key, self.widget._failed_cache)
        self.widget._failed_cache[key] -= self.widget.NEGATIVE_CACHE_TTL_SEC + 1.0

        self.widget.set_camera(cam_a)
        self.assertTrue(_wait_until(self.app, lambda: len(failures) == 2))
        self.assertGreater(FakeOnvifClient.init_count, first_init_count)

    def test_credential_change_and_remove_camera_evict_cache_and_shutdown_queues(self):
        """#54: editar credenciais/porta ou remover câmera limpa caches e encerra filas seriais antigas."""
        cam_a = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam_a)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))

        self.widget._stop_ptz()
        self.assertTrue(_wait_until(self.app, lambda: len(FakeOnvifClient.calls) >= 1))

        key_1 = self.widget._client_key(cam_a)
        self.assertIn(key_1, self.widget._client_cache)
        self.assertIn(key_1, self.widget._queues)
        old_queue_1 = self.widget._queues[key_1]
        self.assertTrue(old_queue_1._thread.is_alive())

        # Editar senha invalida a chave antiga e encerra a fila serial antiga
        cam_a.password = "nova"
        self.widget.set_camera(cam_a)
        self.assertNotIn(key_1, self.widget._client_cache)
        self.assertNotIn(key_1, self.widget._queues)
        self.assertTrue(_wait_until(self.app, lambda: not old_queue_1._thread.is_alive()))
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))

        # Novo comando cria fila para a chave nova; remove_camera limpa tudo e encerra a thread
        self.widget._stop_ptz()
        self.assertTrue(_wait_until(self.app, lambda: len(FakeOnvifClient.calls) >= 2))
        key_2 = self.widget._client_key(cam_a)
        self.assertIn(key_2, self.widget._client_cache)
        self.assertIn(key_2, self.widget._queues)
        old_queue_2 = self.widget._queues[key_2]
        self.assertTrue(old_queue_2._thread.is_alive())

        self.widget.remove_camera("a")
        self.assertNotIn(key_2, self.widget._client_cache)
        self.assertNotIn(key_2, self.widget._queues)
        self.assertFalse(any(k[0] == "a" for k in self.widget._failed_cache))
        self.assertTrue(_wait_until(self.app, lambda: not old_queue_2._thread.is_alive()))

    def test_detected_port_evicts_old_port_key_and_queue(self):
        """#54: adotar porta ONVIF detectada limpa a chave da porta anterior de caches e filas."""
        FakeOnvifClient.responding_ports = {5000}
        cam = _camera("a", "10.0.0.1")
        old_key = self.widget._client_key(cam)
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        new_key = self.widget._client_key(cam)
        self.assertNotEqual(old_key, new_key)
        self.assertNotIn(old_key, self.widget._client_cache)
        self.assertNotIn(old_key, self.widget._queues)
        self.assertIn(new_key, self.widget._client_cache)
        self.assertIn(new_key, self.widget._queues)

    def test_slow_move_skips_overlapping_renewals_and_resumes_after_completion(self):
        """#74: com cliente bloqueado, renovações não se acumulam; ao concluir, retoma e Stop segue ao soltar."""
        cam = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        key = self.widget._client_key(cam)

        started = [threading.Event(), threading.Event()]
        gates = [threading.Event(), threading.Event()]
        for g in gates:
            self.addCleanup(g.set)

        move_entries = 0
        entries_lock = threading.Lock()

        def blocking_move(pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
            nonlocal move_entries
            with entries_lock:
                idx = move_entries
                move_entries += 1
            if idx < len(started):
                started[idx].set()
                gates[idx].wait(timeout=3.0)
            with FakeOnvifClient.lock:
                FakeOnvifClient.calls.append((cam.ip, "move"))
            return True

        timer_ticks = 0

        def count_tick():
            nonlocal timer_ticks
            timer_ticks += 1

        self.widget.client.ptz_continuous_move = blocking_move
        self.widget._move_renewal_timer.setInterval(30)
        self.widget._move_renewal_timer.timeout.connect(count_tick)

        # Primeiro movimento inicia e fica bloqueado
        self.widget._start_ptz_move(1.0, 0.0)
        self.assertTrue(started[0].wait(2.0))

        # Vários disparos do timer enquanto o 1º movimento ainda está em voo não podem enfileirar novos moves
        self.assertTrue(_wait_until(self.app, lambda: timer_ticks >= 3))
        self.widget._move_renewal_timer.stop()
        gates[0].set()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(FakeOnvifClient.calls) == 1
                and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )
        self.assertEqual(move_entries, 1)
        self.assertEqual([c[1] for c in FakeOnvifClient.calls], ["move"])

        # Após concluir o 1º movimento, a próxima renovação do timer volta a ser enviada
        self.widget._move_renewal_timer.start()
        self.assertTrue(_wait_until(self.app, lambda: started[1].is_set()))
        self.assertEqual(move_entries, 2)

        # Novos disparos com a 2ª chamada bloqueada também são pulados; soltar o botão envia Stop
        ticks_before_second = timer_ticks
        self.assertTrue(_wait_until(self.app, lambda: timer_ticks >= ticks_before_second + 3))
        self.widget._stop_ptz()
        gates[1].set()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(FakeOnvifClient.calls) >= 3
                and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )
        self.assertEqual([c[1] for c in FakeOnvifClient.calls], ["move", "move", "stop"])

    def test_failed_move_releases_in_flight_flag_for_next_renewal(self):
        """#74: falha ou exceção no ContinuousMove libera o controle para a próxima renovação."""
        cam = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        key = self.widget._client_key(cam)

        attempts = []

        def flaky_move(pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                return False
            if len(attempts) == 2:
                raise RuntimeError("timeout SOAP simulado")
            return True

        failures = []
        self.widget.command_failed.connect(lambda name, msg: failures.append(msg))
        self.widget.client.ptz_continuous_move = flaky_move
        self.widget._move_renewal_timer.setInterval(60000)

        # 1ª tentativa retorna False -> deve emitir falha e liberar renovação
        self.widget._start_ptz_move(1.0, 0.0)
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(failures) == 1 and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )

        # 2ª tentativa (renovação) lança exceção -> deve emitir falha e liberar renovação
        self.widget._renew_move()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(failures) == 2 and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )

        # 3ª tentativa (renovação) volta a ser enviada e conclui com sucesso
        self.widget._renew_move()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(attempts) == 3 and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )
        self.assertEqual(len(failures), 2)

    def test_begin_move_always_sends_even_when_previous_move_in_flight(self):
        """#74: _begin_move sempre envia mesmo com ContinuousMove anterior em voo; renovações aguardam ambos."""
        cam = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        key = self.widget._client_key(cam)

        started = [threading.Event(), threading.Event(), threading.Event()]
        gates = [threading.Event(), threading.Event()]
        for g in gates:
            self.addCleanup(g.set)

        idx_counter = 0
        counter_lock = threading.Lock()

        def gated_move(pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
            nonlocal idx_counter
            with counter_lock:
                idx = idx_counter
                idx_counter += 1
            if idx < len(started):
                started[idx].set()
            if idx < len(gates):
                gates[idx].wait(timeout=3.0)
            with FakeOnvifClient.lock:
                FakeOnvifClient.calls.append((cam.ip, f"move:{pan_speed:.1f}"))
            return True

        self.widget.client.ptz_continuous_move = gated_move
        self.widget._move_renewal_timer.setInterval(60000)

        # Pressiona direita (move 1 bloqueado), solta (stop) e imediatamente pressiona esquerda (move 2)
        self.widget._start_ptz_move(1.0, 0.0)
        self.assertTrue(started[0].wait(2.0))
        self.widget._stop_ptz()
        self.widget._start_ptz_move(-1.0, 0.0)
        self.assertEqual(self.widget._moves_in_flight.get(key), 2)

        # Libera o 1º movimento: Stop executa, _move_finished do 1º é processado e o 2º fica bloqueado
        gates[0].set()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: started[1].is_set() and self.widget._moves_in_flight.get(key) == 1,
            )
        )
        self.assertEqual([c[1] for c in FakeOnvifClient.calls], ["move:0.5", "stop"])

        # Mesmo após o 1º movimento terminar, o 2º ainda está em voo -> renovação deve ser pulada
        self.widget._renew_move()
        gates[1].set()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(FakeOnvifClient.calls) == 3
                and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )
        self.assertEqual(idx_counter, 2)

        # Agora todos os movimentos terminaram -> próxima renovação é enviada
        self.widget._renew_move()
        self.assertTrue(_wait_until(self.app, lambda: started[2].is_set()))
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: len(FakeOnvifClient.calls) == 4
                and self.widget._moves_in_flight.get(key, 0) == 0,
            )
        )
        self.assertEqual(
            [c[1] for c in FakeOnvifClient.calls],
            ["move:0.5", "stop", "move:-0.5", "move:-0.5"],
        )

    def test_in_flight_tracking_is_per_camera(self):
        """#74: movimento bloqueado em uma câmera não impede renovações de outra câmera."""
        cam_a, cam_b = _camera("a", "10.0.0.1"), _camera("b", "10.0.0.2")
        self.widget.set_camera(cam_a)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        key_a = self.widget._client_key(cam_a)

        started_a = threading.Event()
        gate_a = threading.Event()
        self.addCleanup(gate_a.set)

        def blocking_move_a(pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
            started_a.set()
            gate_a.wait(timeout=3.0)
            with FakeOnvifClient.lock:
                FakeOnvifClient.calls.append((cam_a.ip, "move"))
            return True

        self.widget.client.ptz_continuous_move = blocking_move_a
        self.widget._move_renewal_timer.setInterval(60000)
        self.widget._start_ptz_move(1.0, 0.0)
        self.assertTrue(started_a.wait(2.0))

        # Troca para câmera B enquanto A continua bloqueada
        self.widget.set_camera(cam_b)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None and self.widget.client.ip == cam_b.ip))
        key_b = self.widget._client_key(cam_b)
        self.widget._move_renewal_timer.setInterval(60000)
        self.widget._start_ptz_move(0.0, 1.0)
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: (cam_b.ip, "move") in FakeOnvifClient.calls
                and self.widget._moves_in_flight.get(key_b, 0) == 0,
            )
        )

        # Renovação de B deve ser enviada normalmente mesmo com A em voo
        self.widget._renew_move()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: sum(1 for c in FakeOnvifClient.calls if c == (cam_b.ip, "move")) == 2
                and self.widget._moves_in_flight.get(key_b, 0) == 0,
            )
        )
        gate_a.set()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: (cam_a.ip, "move") in FakeOnvifClient.calls
                and self.widget._moves_in_flight.get(key_a, 0) == 0,
            )
        )

    def test_late_move_finished_from_evicted_queue_does_not_decrement_new_move(self):
        """#74: conclusão tardia de uma fila descartada não decrementa o movimento em voo da nova fila."""
        cam = _camera("a", "10.0.0.1")
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        key = self.widget._client_key(cam)

        old_started = threading.Event()
        old_gate = threading.Event()
        new_started = threading.Event()
        new_gate = threading.Event()
        self.addCleanup(old_gate.set)
        self.addCleanup(new_gate.set)

        def old_blocking_move(pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
            old_started.set()
            old_gate.wait(timeout=3.0)
            return True

        def new_blocking_move(pan_speed=0.0, tilt_speed=0.0, zoom_speed=0.0):
            new_started.set()
            new_gate.wait(timeout=3.0)
            with FakeOnvifClient.lock:
                FakeOnvifClient.calls.append((cam.ip, "new_move"))
            return True

        self.widget.client.ptz_continuous_move = old_blocking_move
        self.widget._move_renewal_timer.setInterval(60000)
        self.widget._start_ptz_move(1.0, 0.0)
        self.assertTrue(old_started.wait(2.0))
        old_queue = self.widget._queues[key]

        # Descarta a fila antiga enquanto o movimento antigo continua bloqueado
        self.widget.remove_camera(cam.id)
        self.assertNotIn(key, self.widget._queues)
        self.assertNotIn(key, self.widget._moves_in_flight)

        # Re-seleciona o Dispositivo Cadastrado na mesma chave e inicia um movimento novo em nova fila
        self.widget.set_camera(cam)
        self.assertTrue(_wait_until(self.app, lambda: self.widget.client is not None))
        self.assertEqual(self.widget._client_key(cam), key)
        self.widget.client.ptz_continuous_move = new_blocking_move
        self.widget._move_renewal_timer.setInterval(60000)
        self.widget._start_ptz_move(-1.0, 0.0)
        self.assertTrue(new_started.wait(2.0))
        new_queue = self.widget._queues[key]
        self.assertIsNot(new_queue, old_queue)
        self.assertEqual(self.widget._moves_in_flight.get(key), 1)

        # Libera o movimento antigo e aguarda a entrega do sinal _move_finished da fila descartada
        old_finished_signals = []
        self.widget._move_finished.connect(lambda *args: old_finished_signals.append(args))
        old_gate.set()
        self.assertTrue(
            _wait_until(
                self.app,
                lambda: not old_queue._thread.is_alive() and len(old_finished_signals) == 1,
            )
        )

        # A conclusão tardia não pode decrementar o movimento em voo da nova fila
        self.assertEqual(self.widget._moves_in_flight.get(key), 1)

        # Renovação continua pulada enquanto o movimento novo está bloqueado
        self.widget._renew_move()
        new_gate.set()
        self.assertTrue(_wait_until(self.app, lambda: self.widget._moves_in_flight.get(key, 0) == 0))
        self.assertEqual(FakeOnvifClient.calls, [(cam.ip, "new_move")])


if __name__ == "__main__":
    unittest.main()
