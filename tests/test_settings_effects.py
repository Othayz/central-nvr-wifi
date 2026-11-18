#!/usr/bin/env python3
"""
Testes da Issue #34: toda opção visível em Configurações tem efeito, settings.json é gravado uma
única vez e o padrão de transporte RTSP é TCP em todos os pontos.
"""
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import DEFAULT_RTSP_TRANSPORT, CameraDevice, StreamStats
from central_nvr.core.config import ConfigManager
from central_nvr.core.stream_worker import StreamWorker
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_grid import CameraGridWidget
from central_nvr.ui.camera_view import CameraViewWidget, ViewOptions


def _cam(i=1):
    return CameraDevice(id=f"c{i}", name=f"Cam {i}", ip=f"192.0.2.{i}")


class FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, ConfigManager.DEFAULT_SETTINGS.get(key, default))

    def set(self, key, value):
        self[key] = value


class QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patchers = [patch.object(sw.av, "open", side_effect=OSError("offline")),
                         patch.object(sw, "HAS_CV2", False),
                         patch("central_nvr.core.config.is_keyring_available", return_value=False)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        drain_retired_threads(5000)
        for p in self.patchers:
            p.stop()

    def _view(self, options):
        view = CameraViewWidget(_cam(), options=options)
        self.addCleanup(lambda: (view.stop(), view.deleteLater(),
                                 QCoreApplication.sendPostedEvents(view, QEvent.Type.DeferredDelete)))
        return view


class WorkerOptionsTest(QtCase):
    def test_options_reach_worker(self):
        view = self._view(ViewOptions(buffer_size_ms=400, reconnect_interval_sec=9,
                                      auto_reconnect=False, motion_detection=False))
        self.assertEqual(view.worker.buffer_size_ms, 400)
        self.assertEqual(view.worker.reconnect_interval_sec, 9)
        self.assertFalse(view.worker.auto_reconnect)
        self.assertFalse(view.worker.motion_detection)

    def test_motion_detection_disabled_emits_nothing(self):
        worker = StreamWorker(camera=_cam(), motion_detection=False)
        events = []
        worker.motion_detected.connect(lambda cid, on: events.append(on))
        for value in (0, 255, 0, 255, 0, 255):
            worker._evaluate_motion(np.full((36, 64, 3), value, dtype=np.uint8), 64, 36)
        self.assertEqual(events, [])

    def test_motion_detection_enabled_detects(self):
        worker = StreamWorker(camera=_cam(), motion_detection=True)
        events = []
        worker.motion_detected.connect(lambda cid, on: events.append(on))
        for value in (0, 0, 0, 255, 255, 255):
            worker._evaluate_motion(np.full((36, 64, 3), value, dtype=np.uint8), 64, 36)
        self.assertIn(True, events)

    def test_live_apply_updates_running_worker(self):
        view = self._view(ViewOptions())
        view.apply_options(ViewOptions(motion_detection=False, reconnect_interval_sec=12, auto_reconnect=False))
        self.assertFalse(view.worker.motion_detection)
        self.assertEqual(view.worker.reconnect_interval_sec, 12)
        self.assertFalse(view.worker.auto_reconnect)


class OsdAndSnapshotTest(QtCase):
    def test_osd_flags(self):
        view = self._view(ViewOptions(show_osd_fps=False, show_osd_bitrate=True, show_osd_timestamp=False))
        view._on_stats_updated(StreamStats(fps=24.0, bitrate_kbps=900, codec="H.264"))
        text = view.lbl_metrics.text()
        self.assertNotIn("FPS", text)
        self.assertIn("900 kbps", text)
        self.assertNotRegex(text, r"\d\d:\d\d:\d\d")

        view.apply_options(ViewOptions(show_osd_fps=True, show_osd_bitrate=False, show_osd_timestamp=True))
        text = view.lbl_metrics.text()
        self.assertIn("24.0 FPS", text)
        self.assertNotIn("kbps", text)
        self.assertRegex(text, r"\d\d:\d\d:\d\d")

    def test_snapshot_goes_to_configured_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            view = self._view(ViewOptions(snapshot_dir=tmp))
            with patch.object(view.worker, "isRunning", return_value=True), \
                    patch.object(view.worker, "take_snapshot") as take:
                view._take_snapshot()
            self.assertEqual(Path(take.call_args[0][0]).parent, Path(tmp))

    def test_fullscreen_snapshot_dir(self):
        from central_nvr.ui.fullscreen_view import FullscreenPlayerWindow
        with tempfile.TemporaryDirectory() as tmp:
            win = FullscreenPlayerWindow(camera=_cam(), snapshot_dir=Path(tmp))
            self.assertEqual(win.snapshot_dir, Path(tmp))
            win.deleteLater()


class GridOptionsTest(QtCase):
    def test_options_change_without_buffer_keeps_stream(self):
        grid = CameraGridWidget(view_options=ViewOptions())
        self.addCleanup(lambda: (grid.stop_all(), grid.deleteLater()))
        cams = [_cam(1)]
        grid.set_cameras(cams)
        worker = grid.camera_views["c1"].worker
        grid.set_view_options(ViewOptions(show_osd_fps=False, motion_detection=False))
        grid.set_cameras(cams)
        self.assertIs(grid.camera_views["c1"].worker, worker)
        self.assertFalse(worker.motion_detection)

    def test_buffer_change_reopens_stream(self):
        grid = CameraGridWidget(view_options=ViewOptions())
        self.addCleanup(lambda: (grid.stop_all(), grid.deleteLater()))
        cams = [_cam(1)]
        grid.set_cameras(cams)
        worker = grid.camera_views["c1"].worker
        grid.set_view_options(ViewOptions(buffer_size_ms=500))
        grid.set_cameras(cams)
        self.assertIsNot(grid.camera_views["c1"].worker, worker)
        self.assertEqual(grid.camera_views["c1"].worker.buffer_size_ms, 500)


class SettingsDialogTest(QtCase):
    def _dialog(self, cfg):
        from central_nvr.ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(config_mgr=cfg)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_every_visible_option_is_saved(self):
        cfg = FakeConfig()
        dialog = self._dialog(cfg)
        dialog.chk_auto_reconnect.setChecked(False)
        dialog.chk_osd_fps.setChecked(False)
        dialog.spin_discovery_timeout.setValue(7.5)
        dialog.chk_port_scan_default.setChecked(False)
        values = dialog.collect_values()
        self.assertFalse(values["auto_reconnect"])
        self.assertFalse(values["show_osd_fps"])
        self.assertEqual(values["ws_discovery_timeout"], 7.5)
        self.assertFalse(values["enable_port_scan"])
        self.assertNotIn("recordings_dir", values)

    def test_recordings_dir_not_visible(self):
        dialog = self._dialog(FakeConfig())
        dialog.show()
        self.assertFalse(dialog.txt_rec_dir.isVisible())
        dialog.close()

    def test_saving_writes_settings_once(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("central_nvr.core.config.get_config_dir", return_value=Path(tmp)):
            cfg = ConfigManager()
            dialog = self._dialog(cfg)
            with patch.object(cfg, "save_settings", wraps=cfg.save_settings) as save, \
                    patch("central_nvr.ui.settings_dialog.QMessageBox"):
                dialog._save_values()
            self.assertEqual(save.call_count, 1)

    def test_discovery_dialog_uses_config(self):
        from central_nvr.ui.discovery_dialog import DiscoveryDialog
        dialog = DiscoveryDialog(discovery_timeout=7.5, enable_port_scan=False)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.chk_port_scan.isChecked())
        self.assertEqual(dialog.discovery_timeout, 7.5)


class TransportDefaultTest(unittest.TestCase):
    def test_tcp_everywhere(self):
        self.assertEqual(DEFAULT_RTSP_TRANSPORT, "tcp")
        self.assertEqual(ConfigManager.DEFAULT_SETTINGS["rtsp_transport"], "tcp")
        self.assertEqual(CameraDevice(id="x", name="x", ip="1.1.1.1").rtsp_transport, "tcp")
        self.assertEqual(CameraDevice.from_dict({}).rtsp_transport, "tcp")
        for cls in (StreamWorker, CameraViewWidget, CameraGridWidget):
            default = inspect.signature(cls.__init__).parameters["rtsp_transport"].default
            self.assertEqual(default, "tcp", cls.__name__)

    def test_no_hardcoded_auto_transport_fallback(self):
        root = Path(__file__).resolve().parent.parent / "src" / "central_nvr"
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('get("rtsp_transport", "auto")', text, path.name)


if __name__ == "__main__":
    unittest.main()
