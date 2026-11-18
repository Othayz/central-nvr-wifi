#!/usr/bin/env python3
"""
Testes do laço de reconexão do StreamWorker (Issue #27).

Os backends reais (PyAV/OpenCV) são substituídos por funções falsas que devolvem StreamOutcome, e
a espera é registrada em vez de dormir. run() é chamado de forma síncrona.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core.camera import CameraDevice, ConnectionState
from central_nvr.core import stream_worker as sw
from central_nvr.core.stream_worker import StreamOutcome, StreamWorker


def _camera():
    return CameraDevice(id="c1", name="Cam", ip="192.168.1.10", rtsp_port=554, rtsp_path="/live/ch0",
                        username="admin", password="x", manufacturer="Genérico")


class Harness:
    """Monta um worker com backends e espera falsos."""

    def __init__(self, decide, max_calls=6, **worker_kwargs):
        self.calls = []
        self.waits = []
        self.states = []
        self.decide = decide
        self.max_calls = max_calls
        self.worker = StreamWorker(camera=worker_kwargs.pop("camera", _camera()), **worker_kwargs)
        self.worker.state_changed.connect(lambda st, msg: self.states.append((st, msg)))
        self.worker._wait = self.waits.append

    def runner(self, backend):
        def _run(url, transport="udp"):
            self.calls.append((backend, url, transport))
            if len(self.calls) >= self.max_calls:
                self.worker._is_running = False
                return StreamOutcome.STOPPED
            return self.decide(backend, url, transport)
        return _run

    def run(self):
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "HAS_CV2", True), \
                patch.object(self.worker, "_run_pyav_stream", self.runner("PyAV")), \
                patch.object(self.worker, "_run_opencv_stream", self.runner("OpenCV")):
            self.worker.run()
        return self


class ReconnectTest(unittest.TestCase):
    def test_stream_that_ends_immediately_waits_between_attempts(self):
        """Stream que termina logo não gera mais de uma tentativa por intervalo configurado."""
        h = Harness(lambda b, u, t: StreamOutcome.ENDED, max_calls=5, reconnect_interval_sec=4.0).run()
        attempts = len(h.calls) - 1  # a última chamada só encerra o teste
        self.assertEqual(len(h.waits), attempts)
        for wait in h.waits:
            self.assertGreaterEqual(wait, 4.0 * 0.8)

    def test_reconnect_tries_last_working_url_and_transport_first(self):
        """Após conectar em /onvif1 via TCP, a reconexão tenta primeiro /onvif1 via TCP."""
        def decide(backend, url, transport):
            if backend == "PyAV" and url.endswith("/onvif1") and transport == "tcp":
                return StreamOutcome.ENDED
            return StreamOutcome.NOT_CONNECTED

        h = Harness(decide, max_calls=10**6, rtsp_transport="udp")
        # Encerra o teste na primeira reconexão bem-sucedida
        original = h.decide

        def decide_and_stop(backend, url, transport):
            result = original(backend, url, transport)
            if result == StreamOutcome.ENDED and h.worker.camera.resolved_stream is not None and len(h.waits) >= 1:
                h.worker._is_running = False
                return StreamOutcome.STOPPED
            return result

        h.decide = decide_and_stop
        h.run()
        first_success = next(i for i, c in enumerate(h.calls) if c[1].endswith("/onvif1") and c[2] == "tcp")
        reconnect_call = h.calls[first_success + 1]
        self.assertEqual(reconnect_call[0], "PyAV")
        self.assertTrue(reconnect_call[1].endswith("/onvif1"))
        self.assertEqual(reconnect_call[2], "tcp")

    def test_auth_failure_stops_without_scanning_paths(self):
        """Erro de autenticação não dispara a varredura dos 12 caminhos."""
        h = Harness(lambda b, u, t: StreamOutcome.AUTH_FAILED, max_calls=100).run()
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(h.waits, [])
        self.assertTrue(any(st == ConnectionState.ERROR and "Credenciais" in msg for st, msg in h.states))
        self.assertEqual(h.worker.camera.last_error, "Credenciais inválidas")

    def test_backoff_grows_and_is_capped(self):
        h = Harness(lambda b, u, t: StreamOutcome.NOT_CONNECTED, max_calls=10**6, reconnect_interval_sec=2.0)
        # Cada rodada completa testa todas as URLs × transportes × backends; para após 8 rodadas
        original_wait = h.waits.append

        def wait(seconds):
            original_wait(seconds)
            if len(h.waits) >= 8:
                h.worker._is_running = False

        h.worker._wait = wait
        h.run()
        self.assertEqual(len(h.waits), 8)
        self.assertLess(h.waits[0], h.waits[2])
        self.assertLessEqual(max(h.waits), 60.0 * 1.2)
        self.assertGreaterEqual(h.waits[0], 2.0 * 0.8)

    def test_auto_reconnect_disabled_runs_single_cycle(self):
        h = Harness(lambda b, u, t: StreamOutcome.ENDED, max_calls=100, auto_reconnect=False).run()
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(h.waits, [])

    def test_stale_resolved_url_is_ignored(self):
        cam = _camera()
        cam.resolved_stream = ("PyAV", "rtsp://outra-camera/onvif1", "tcp")
        h = Harness(lambda b, u, t: StreamOutcome.NOT_CONNECTED, max_calls=3, camera=cam).run()
        self.assertNotEqual(h.calls[0][1], "rtsp://outra-camera/onvif1")


class TimeoutOptionTest(unittest.TestCase):
    def test_uses_timeout_on_modern_ffmpeg(self):
        with patch.object(sw.av, "library_versions", {"libavformat": (61, 1, 100)}):
            self.assertEqual(sw._pyav_socket_timeout_option(), {"timeout": "5000000"})

    def test_uses_stimeout_on_old_ffmpeg(self):
        with patch.object(sw.av, "library_versions", {"libavformat": (58, 76, 100)}):
            self.assertEqual(sw._pyav_socket_timeout_option(), {"stimeout": "5000000"})


if __name__ == "__main__":
    unittest.main()
