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


if __name__ == "__main__":
    unittest.main()
