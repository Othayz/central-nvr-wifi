#!/usr/bin/env python3
"""
Testes de regressão da Issue #55:
1. Timeout compatível com FFmpeg >= 5 no fallback OpenCV (`timeout` e CAP_PROP_*_TIMEOUT_MSEC).
2. Coleta de lixo cíclica confinada à thread principal via `install_main_thread_gc`.
3. Quebra de ciclos de referência ao descartar `CameraViewWidget` e estresse concorrente.
"""
import gc
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.app as app_mod
import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice
from central_nvr.core.stream_worker import StreamOutcome, StreamWorker
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_grid import CameraGridWidget
from central_nvr.ui.camera_view import CameraViewWidget


class OpenCVTimeoutTest(unittest.TestCase):
    def setUp(self):
        self._prev_capture_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev_capture_opts is None:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._prev_capture_opts

    def test_opencv_fallback_sets_timeout_option_and_passes_timeout_properties(self):
        fake_cap = MagicMock()
        fake_cap.isOpened.return_value = False
        capture_calls = []

        def fake_video_capture(*args):
            capture_calls.append(args)
            return fake_cap

        fake_cv2 = types.SimpleNamespace(
            CAP_FFMPEG=1900,
            CAP_PROP_BUFFERSIZE=38,
            CAP_PROP_OPEN_TIMEOUT_MSEC=53,
            CAP_PROP_READ_TIMEOUT_MSEC=54,
            VideoCapture=fake_video_capture,
        )

        cam = CameraDevice(id="cv1", name="Garagem", ip="192.0.2.30")
        worker = StreamWorker(camera=cam)

        with patch.object(sw, "cv2", fake_cv2, create=True):
            outcome = worker._run_opencv_stream("rtsp://192.0.2.30:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.NOT_CONNECTED)
        opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
        self.assertIn("timeout;5000000", opts)
        self.assertNotIn("stimeout;5000000", opts)
        self.assertEqual(
            capture_calls,
            [("rtsp://192.0.2.30:554/onvif1", 1900, [53, 5000, 54, 5000])],
        )
        fake_cap.set.assert_called_once_with(38, 1)
        fake_cap.release.assert_called_once()

    def test_opencv_fallback_handles_type_error_on_timeout_params(self):
        fake_cap = MagicMock()
        fake_cap.isOpened.return_value = False
        capture_calls = []

        def fake_video_capture(*args):
            capture_calls.append(args)
            if len(args) == 3:
                raise TypeError("params not supported")
            return fake_cap

        fake_cv2 = types.SimpleNamespace(
            CAP_FFMPEG=1900,
            CAP_PROP_BUFFERSIZE=38,
            CAP_PROP_OPEN_TIMEOUT_MSEC=53,
            CAP_PROP_READ_TIMEOUT_MSEC=54,
            VideoCapture=fake_video_capture,
        )

        worker = StreamWorker(camera=CameraDevice(id="cv2", name="Quintal", ip="192.0.2.31"))
        with patch.object(sw, "cv2", fake_cv2, create=True):
            outcome = worker._run_opencv_stream("rtsp://192.0.2.31:554/onvif1", transport="udp")

        self.assertEqual(outcome, StreamOutcome.NOT_CONNECTED)
        self.assertEqual(len(capture_calls), 2)
        self.assertEqual(capture_calls[1], ("rtsp://192.0.2.31:554/onvif1", 1900))


class MainThreadGcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_install_main_thread_gc_disables_auto_gc_runs_on_timer_and_restores(self):
        gc.enable()
        self.addCleanup(gc.enable)

        timer, cleanup = app_mod.install_main_thread_gc(self.app, interval_ms=20)
        self.addCleanup(cleanup)

        self.assertFalse(gc.isenabled(), "GC automático deve permanecer desabilitado enquanto ativo")
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 20)

        observed_threads = []
        real_collect = gc.collect

        def tracked_collect(*args, **kwargs):
            observed_threads.append(threading.current_thread())
            return real_collect(*args, **kwargs)

        with patch.object(app_mod.gc, "collect", side_effect=tracked_collect):
            deadline = time.monotonic() + 2.0
            while not observed_threads and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

        self.assertGreaterEqual(len(observed_threads), 1, "timer deve disparar gc.collect() na thread principal")
        for thread in observed_threads:
            self.assertIs(thread, threading.main_thread())

        cleanup()
        self.assertFalse(timer.isActive())
        self.assertTrue(gc.isenabled(), "cleanup() deve restaurar gc.enable()")


class WidgetDisposeAndStressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_camera_view_dispose_stops_timers_and_breaks_button_connections(self):
        cam = CameraDevice(id="disp-1", name="Entrada", ip="192.0.2.40", rtsp_url="demo://")
        view = CameraViewWidget(cam)
        ptz_events = []
        max_events = []
        view.ptz_requested.connect(ptz_events.append)
        view.double_clicked.connect(max_events.append)

        view.btn_ptz.click()
        view.btn_fullscreen.click()
        self.assertEqual(ptz_events, ["disp-1"])
        self.assertEqual(max_events, ["disp-1"])

        view.dispose()
        self.assertFalse(view._watchdog.isActive())
        self.assertFalse(view._hidden_timer.isActive())

        view.btn_ptz.click()
        view.btn_fullscreen.click()
        self.assertEqual(ptz_events, ["disp-1"])
        self.assertEqual(max_events, ["disp-1"])

        self.assertTrue(drain_retired_threads(5000))
        view.deleteLater()
        QCoreApplication.sendPostedEvents(view, QEvent.Type.DeferredDelete)

    def test_stress_create_and_dispose_views_while_background_threads_allocate_cycles(self):
        gc.enable()
        self.addCleanup(gc.enable)

        _, cleanup_gc = app_mod.install_main_thread_gc(self.app, interval_ms=30)
        self.addCleanup(cleanup_gc)

        stop_allocators = threading.Event()
        allocator_errors = []

        def allocate_cycles():
            try:
                while not stop_allocators.is_set():
                    batch = []
                    for _ in range(150):
                        node = []
                        node.append(node)
                        cycle_dict = {"ref": node}
                        cycle_dict["self"] = cycle_dict
                        batch.append(cycle_dict)
                    del batch
                    time.sleep(0.001)
            except BaseException as exc:
                allocator_errors.append(exc)

        allocators = [threading.Thread(target=allocate_cycles, daemon=True) for _ in range(3)]
        for t in allocators:
            t.start()

        grid = CameraGridWidget()
        try:
            for iteration in range(30):
                cams = [
                    CameraDevice(
                        id=f"stress-{iteration}-a",
                        name=f"Cam {iteration} A",
                        ip="192.0.2.50",
                        rtsp_url="demo://",
                    ),
                    CameraDevice(
                        id=f"stress-{iteration}-b",
                        name=f"Cam {iteration} B",
                        ip="192.0.2.51",
                        rtsp_url="demo://",
                    ),
                ]
                grid.set_cameras(cams)
                self.app.processEvents()
                if iteration % 5 == 0:
                    grid.set_cameras([])
                    self.app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        finally:
            stop_allocators.set()
            for t in allocators:
                t.join(timeout=5)
            grid.set_cameras([])
            grid.stop_all()
            drained = drain_retired_threads(10000)
            grid.close()
            grid.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            cleanup_gc()

        self.assertEqual(allocator_errors, [])
        self.assertTrue(drained, "todas as threads de streaming aposentadas devem terminar")
        self.assertTrue(gc.isenabled())


if __name__ == "__main__":
    unittest.main()
