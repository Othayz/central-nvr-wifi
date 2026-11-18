#!/usr/bin/env python3
"""
Testes da parte 1 da Issue #25: câmeras fora da tela pausadas (sem decodificar) e desconectadas
após N minutos; "último frame vence"; redimensionamento do frame no worker.
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice, ConnectionState
from central_nvr.core.stream_worker import StreamWorker
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_grid import CameraGridWidget


def _camera(i=1):
    return CameraDevice(id=f"c{i}", name=f"Cam {i}", ip=f"192.0.2.{i}", rtsp_path="/onvif1")


class FakeFrame:
    def to_ndarray(self, format="rgb24"):
        return np.zeros((72, 128, 3), dtype=np.uint8)


class FakePacket:
    def __init__(self, keyframe, log):
        self.is_keyframe = keyframe
        self.size = 1000
        self.log = log

    def decode(self):
        self.log.append(self.is_keyframe)
        return [FakeFrame()]


class FakeStream:
    type = "video"
    thread_type = "SLICE"

    class codec_context:
        name = "h264"


class FakeContainer:
    def __init__(self, packets):
        self.streams = [FakeStream()]
        self._packets = packets

    def demux(self, stream):
        yield from self._packets()

    def close(self):
        pass


class PauseDecodingTest(unittest.TestCase):
    def test_paused_pyav_reads_packets_without_decoding_and_resumes_on_keyframe(self):
        worker = StreamWorker(camera=_camera())
        worker._is_running = True
        decoded = []
        read = []

        def packets():
            for i, key in enumerate([True, False, False, False, False, True, False]):
                read.append(i)
                if i == 3:
                    worker.resume()  # volta para a tela no meio de um GOP
                yield FakePacket(key, decoded)

        worker.pause()
        states = []
        worker.state_changed.connect(lambda st, msg: states.append(st))
        with patch.object(sw.av, "open", return_value=FakeContainer(packets)):
            worker._run_pyav_stream("rtsp://x/onvif1", transport="tcp")

        self.assertEqual(len(read), 7, "todos os pacotes são lidos (sessão mantida viva)")
        # Pausada nos pacotes 0..2; retomada no 3 espera o keyframe 5 → decodifica 5 e 6
        self.assertEqual(decoded, [True, False])
        self.assertIn(ConnectionState.PAUSED, states)

    def test_paused_opencv_does_not_open_connection(self):
        worker = StreamWorker(camera=_camera())
        worker._is_running = True
        worker.pause()
        with patch.object(sw, "cv2", create=True) as cv2:
            outcome = worker._run_opencv_stream("rtsp://x/onvif1")
        self.assertEqual(outcome, sw.StreamOutcome.PAUSED)
        cv2.VideoCapture.assert_not_called()


class LatestFrameTest(unittest.TestCase):
    def test_only_latest_frame_is_kept_and_notified_once(self):
        worker = StreamWorker(camera=_camera())
        notified = []
        worker.frame_available.connect(lambda: notified.append(1))
        for value in (10, 20, 30):
            frame = np.full((36, 64, 3), value, dtype=np.uint8)
            worker._process_and_emit_frame(frame, 64, 36, 40.0)
        self.assertEqual(len(notified), 1)
        latest = worker.take_latest_frame()
        self.assertEqual(latest.pixelColor(0, 0).red(), 30)
        self.assertIsNone(worker.take_latest_frame())
        worker._process_and_emit_frame(np.zeros((36, 64, 3), dtype=np.uint8), 64, 36, 40.0)
        self.assertEqual(len(notified), 2)

    def test_frame_is_resized_in_worker(self):
        worker = StreamWorker(camera=_camera())
        worker.set_target_size(320, 180)
        worker._process_and_emit_frame(np.zeros((1080, 1920, 3), dtype=np.uint8), 1920, 1080, 40.0)
        frame = worker.take_latest_frame()
        self.assertLessEqual(frame.width(), 320)
        self.assertLessEqual(frame.height(), 180)

    def test_full_resolution_when_no_target(self):
        worker = StreamWorker(camera=_camera())
        worker.set_target_size(0, 0)
        worker._process_and_emit_frame(np.zeros((1080, 1920, 3), dtype=np.uint8), 1920, 1080, 40.0)
        self.assertEqual(worker.take_latest_frame().width(), 1920)


class GridHiddenCamerasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Sem rede: PyAV falha na hora e o OpenCV (que bloquearia em hosts inalcançáveis) fica fora
        self.patchers = [patch.object(sw.av, "open", side_effect=OSError("offline")),
                         patch.object(sw, "HAS_CV2", False)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        self.grid.stop_all()
        self.assertTrue(drain_retired_threads(5000))
        self.grid.deleteLater()
        QCoreApplication.sendPostedEvents(self.grid, QEvent.Type.DeferredDelete)
        for p in self.patchers:
            p.stop()

    def _pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_cameras_off_screen_are_paused_then_disconnected(self):
        self.grid = CameraGridWidget(hidden_timeout_min=0.002)  # ~120 ms
        self.grid.set_cameras([_camera(i) for i in range(1, 13)])
        views = list(self.grid.camera_views.values())
        visible, hidden = views[:9], views[9:]

        for v in visible:
            self.assertFalse(v.worker.is_paused)
        for v in hidden:
            self.assertTrue(v.worker.is_paused)

        self._pump(0.4)
        for v in hidden:
            self.assertIsNone(v.worker, "câmera fora da tela deve ser desconectada após o tempo configurado")
            self.assertTrue(v.is_suspended)
        for v in visible:
            self.assertIsNotNone(v.worker)

    def test_camera_back_on_screen_resumes(self):
        self.grid = CameraGridWidget(hidden_timeout_min=5)
        cams = [_camera(i) for i in range(1, 3)]
        self.grid.set_cameras(cams)
        self.grid.set_layout_mode("1x1")
        other = self.grid.camera_views["c2"]
        self.assertTrue(other.worker.is_paused)
        self.grid.set_layout_mode("auto")
        self.assertFalse(other.worker.is_paused)

    def test_suspended_camera_reconnects_when_visible(self):
        self.grid = CameraGridWidget(hidden_timeout_min=0.002)
        self.grid.set_cameras([_camera(i) for i in range(1, 3)])
        self.grid.set_layout_mode("1x1")
        other = self.grid.camera_views["c2"]
        self._pump(0.4)
        self.assertIsNone(other.worker)
        self.grid.set_layout_mode("auto")
        self.assertIsNotNone(other.worker)
        self.assertFalse(other.worker.is_paused)


class SettingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hidden_timeout_setting_default_and_saved(self):
        from central_nvr.core.config import ConfigManager
        from central_nvr.ui.settings_dialog import SettingsDialog
        self.assertEqual(ConfigManager.DEFAULT_SETTINGS["hidden_camera_timeout_min"], 5)

        class FakeConfig(dict):
            def get(self, key, default=None):
                return super().get(key, ConfigManager.DEFAULT_SETTINGS.get(key, default))

            def set(self, key, value):
                self[key] = value

        cfg = FakeConfig()
        # A consulta ao keyring pode bloquear no DBus de um ambiente sem sessão gráfica
        with patch("central_nvr.core.config.is_keyring_available", return_value=False):
            dialog = SettingsDialog(config_mgr=cfg)
        self.assertEqual(dialog.spin_hidden_timeout.value(), 5)
        dialog.spin_hidden_timeout.setValue(12)
        with patch("central_nvr.ui.settings_dialog.QMessageBox"):
            dialog._save_values()
        self.assertEqual(cfg["hidden_camera_timeout_min"], 12)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
