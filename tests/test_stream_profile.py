#!/usr/bin/env python3
"""
Testes da troca HD/SD (Issue #26): a preferência reconecta com o sub-stream, sem espera, e o
rótulo mostra o stream que de fato conectou.
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice, ConnectionState
from central_nvr.core.stream_worker import StreamOutcome, StreamWorker
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_view import CameraViewWidget


def _camera():
    return CameraDevice(id="c1", name="Cam", ip="192.168.1.10", rtsp_port=554, rtsp_path="/onvif1",
                        username="admin", password="x", manufacturer="Yoosee")


class WorkerSwitchTest(unittest.TestCase):
    def _run(self, worker, runner):
        waits = []
        worker._wait = waits.append
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "HAS_CV2", False), \
                patch.object(worker, "_run_pyav_stream", runner):
            worker.run()
        return waits

    def test_next_url_after_switch_is_substream(self):
        worker = StreamWorker(camera=_camera())
        calls, variants = [], []
        worker.stream_variant.connect(lambda active, requested: variants.append((active, requested)))

        def runner(url, transport="udp"):
            calls.append(url)
            worker._current_url = url
            if len(calls) == 1:
                worker._announce_streaming("Ao Vivo")
                worker.set_prefer_substream(True)   # operador clica em HD/SD
                return worker._end_outcome(frames_seen=10)
            worker._announce_streaming("Ao Vivo")
            worker._is_running = False
            return worker._end_outcome(frames_seen=10)

        waits = self._run(worker, runner)
        self.assertEqual(calls[0], "rtsp://admin:x@192.168.1.10:554/onvif1")
        self.assertEqual(calls[1], worker.camera.get_substream_url())
        self.assertEqual(waits, [], "a troca não deve esperar o intervalo de reconexão")
        self.assertEqual(variants, [(False, False), (True, True)])

    def test_substream_unavailable_falls_back_to_main_and_reports_it(self):
        worker = StreamWorker(camera=_camera(), prefer_substream=True)
        variants = []
        worker.stream_variant.connect(lambda active, requested: variants.append((active, requested)))
        sub = worker.camera.get_substream_url()

        def runner(url, transport="udp"):
            if url == sub:
                return StreamOutcome.NOT_CONNECTED
            worker._announce_streaming("Ao Vivo")
            worker._is_running = False
            return StreamOutcome.STOPPED

        self._run(worker, runner)
        self.assertEqual(variants, [(False, True)])

    def test_switch_clears_resolved_stream(self):
        cam = _camera()
        cam.resolved_stream = ("PyAV", "rtsp://admin:x@192.168.1.10:554/onvif1", "tcp")
        worker = StreamWorker(camera=cam)
        worker.set_prefer_substream(True)
        self.assertIsNone(cam.resolved_stream)

    @unittest.skipUnless(sw.HAS_AV, "PyAV não disponível neste ambiente")
    def test_switch_during_hw_fallback_returns_restart_instead_of_stopped(self):
        """Issue #69: troca HD/SD entre tentativa de hardware e software deve retornar RESTART."""
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        sentinel_hw = object()
        open_calls = []

        def fake_open(url, mode="r", options=None, timeout=5.0, **kwargs):
            open_calls.append((url, kwargs.get("hwaccel")))
            if "hwaccel" in kwargs:
                worker.set_prefer_substream(True)
                raise RuntimeError("VAAPI device initialization failed")
            raise AssertionError("não deveria abrir software na URL antiga após troca HD/SD")

        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=fake_open):
            outcome = worker._run_pyav_stream("rtsp://admin:x@192.168.1.10:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.RESTART)
        self.assertEqual(open_calls, [("rtsp://admin:x@192.168.1.10:554/onvif1", sentinel_hw)])

    @unittest.skipUnless(sw.HAS_AV, "PyAV não disponível neste ambiente")
    def test_stop_during_hw_fallback_returns_stopped(self):
        """Parada real durante fallback hardware -> software continua devolvendo STOPPED."""
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        sentinel_hw = object()
        open_calls = []

        def fake_open(url, mode="r", options=None, timeout=5.0, **kwargs):
            open_calls.append((url, kwargs.get("hwaccel")))
            if "hwaccel" in kwargs:
                worker.request_stop()
                raise RuntimeError("VAAPI device initialization failed")
            raise AssertionError("não deveria abrir software após pedido de parada")

        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=fake_open):
            outcome = worker._run_pyav_stream("rtsp://admin:x@192.168.1.10:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.STOPPED)
        self.assertEqual(open_calls, [("rtsp://admin:x@192.168.1.10:554/onvif1", sentinel_hw)])

    @unittest.skipUnless(sw.HAS_AV, "PyAV não disponível neste ambiente")
    def test_switch_during_hw_decode_fallback_returns_restart(self):
        """Issue #69: troca HD/SD ao cair para CPU por erros de decodificação em HW retorna RESTART."""
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        sentinel_hw = object()
        open_calls = []

        class FailingPacket:
            def __init__(self, trigger_switch=False):
                self.size = 1024
                self.is_keyframe = True
                self._trigger_switch = trigger_switch

            def decode(self):
                if self._trigger_switch:
                    worker.set_prefer_substream(True)
                raise ValueError("erro de decodificação em hardware")

        class FakeContainer:
            def __init__(self, packets):
                stream = types.SimpleNamespace(
                    type="video",
                    thread_type="SLICE",
                    codec_context=types.SimpleNamespace(name="hevc", is_hwaccel=True),
                )
                self.streams = [stream]
                self._packets = packets
                self.closed = False

            def demux(self, stream):
                yield from self._packets

            def close(self):
                self.closed = True

        n_errors = StreamWorker.MAX_HW_STARTUP_DECODE_ERRORS
        packets = [FailingPacket(trigger_switch=(i == n_errors - 1)) for i in range(n_errors)]
        container = FakeContainer(packets)

        def fake_open(url, mode="r", options=None, timeout=5.0, **kwargs):
            open_calls.append((url, kwargs.get("hwaccel")))
            if "hwaccel" in kwargs:
                return container
            raise AssertionError("não deveria abrir software na URL antiga após troca HD/SD")

        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=fake_open):
            outcome = worker._run_pyav_stream("rtsp://admin:x@192.168.1.10:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.RESTART)
        self.assertEqual(open_calls, [("rtsp://admin:x@192.168.1.10:554/onvif1", sentinel_hw)])
        self.assertTrue(container.closed)

    @unittest.skipUnless(sw.HAS_AV, "PyAV não disponível neste ambiente")
    def test_worker_reconnects_to_substream_when_switched_during_hw_fallback(self):
        """Issue #69: worker.run() não termina e reconecta direto no sub-stream ao trocar durante fallback HW."""
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        waits, states, open_calls = [], [], []
        worker._wait = waits.append
        worker.state_changed.connect(lambda st, msg: states.append((st, msg)))
        sentinel_hw = object()
        sub_url = worker.camera.get_substream_url()

        def fake_open(url, mode="r", options=None, timeout=5.0, **kwargs):
            open_calls.append((url, kwargs.get("hwaccel")))
            if len(open_calls) == 1:
                worker.set_prefer_substream(True)
                raise RuntimeError("VAAPI device initialization failed")
            worker.request_stop()
            raise OSError("encerrando teste após abrir sub-stream")

        with patch.object(sw, "HAS_AV", True), patch.object(sw, "HAS_CV2", False), \
                patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=fake_open):
            worker.run()

        self.assertEqual(
            open_calls,
            [
                ("rtsp://admin:x@192.168.1.10:554/onvif1", sentinel_hw),
                (sub_url, sentinel_hw),
            ],
        )
        self.assertEqual(waits, [], "a troca HD/SD não deve esperar o intervalo de reconexão")
        self.assertTrue(
            any(st == ConnectionState.CONNECTING and "HD/SD" in msg for st, msg in states),
            states,
        )


class ViewLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Sem rede: o worker real fica tentando abrir e falha logo
        self.patcher = patch.object(sw.av, "open", side_effect=OSError("offline"))
        self.cv2_patcher = patch.object(sw, "HAS_CV2", False)
        self.patcher.start()
        self.cv2_patcher.start()
        self.view = CameraViewWidget(_camera())

    def tearDown(self):
        try:
            self.view.dispose()
            self.assertTrue(drain_retired_threads(5000))
            self.view.deleteLater()
            QCoreApplication.sendPostedEvents(self.view, QEvent.Type.DeferredDelete)
        finally:
            self.cv2_patcher.stop()
            self.patcher.stop()

    def test_label_does_not_change_to_sd_before_connecting(self):
        self.view._toggle_stream_profile()
        self.assertNotEqual(self.view.btn_stream_profile.text(), "SD")

    def test_label_shows_sd_only_when_substream_in_use(self):
        self.view._on_stream_variant(True, True)
        self.assertEqual(self.view.btn_stream_profile.text(), "SD")
        self.assertTrue(self.view.lbl_notice.isHidden())

    def test_fallback_to_main_shows_hd_and_notice(self):
        self.view._on_stream_variant(False, True)
        self.assertEqual(self.view.btn_stream_profile.text(), "HD")
        self.assertFalse(self.view.lbl_notice.isHidden())
        self.assertIn("Sub-stream indisponível", self.view.lbl_notice.text())


if __name__ == "__main__":
    unittest.main()
