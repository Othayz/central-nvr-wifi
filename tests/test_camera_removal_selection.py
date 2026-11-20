#!/usr/bin/env python3
"""
Testes de regressão da Issue #52:
Sincronização dos painéis PTZ e de Gravações ao remover um Dispositivo Cadastrado.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import central_nvr.core.config as cfg_mod
import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import ConfigManager
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.main_window import MainWindow


class _FakeOnvifClient:
    """Cliente ONVIF determinístico para testes sem tráfego de rede."""

    def __init__(self, ip, port=80, username="", password="", timeout=4.0):
        self.ip = ip
        self.port = port
        self.media_service_url = f"http://{ip}:{port}/onvif/media_service"
        self.ptz_service_url = f"http://{ip}:{port}/onvif/ptz_service"

    def get_capabilities(self):
        return {}

    def get_profiles(self):
        return [{"token": "profile_1"}]

    def ptz_get_presets(self):
        return []


def _registered_camera(cam_id: str, name: str, ip: str) -> CameraDevice:
    return CameraDevice(
        id=cam_id,
        name=name,
        ip=ip,
        port=80,
        rtsp_url="demo://",
        has_ptz=True,
    )


class CameraRemovalSelectionSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        config_dir = base / "config"
        data_dir = base / "data"
        config_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        self._patchers = [
            patch.object(cfg_mod, "get_config_dir", return_value=config_dir),
            patch.object(cfg_mod, "get_data_dir", return_value=data_dir),
            patch("central_nvr.ui.playback_view.get_data_dir", return_value=data_dir),
            patch.object(cfg_mod, "is_keyring_available", return_value=False),
            patch.object(cfg_mod, "HAS_KEYRING", False),
            patch.object(sw, "HAS_CV2", False),
            patch("central_nvr.ui.ptz_controller.OnvifClient", _FakeOnvifClient),
            patch(
                "central_nvr.ui.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
        ]
        for patcher in self._patchers:
            patcher.start()

        self.config_mgr = ConfigManager()
        self.config_mgr.set("periodic_update_check", False)

        cam_a = _registered_camera("A", "Portaria", "192.0.2.10")
        cam_b = _registered_camera("B", "Garagem", "192.0.2.11")
        self.config_mgr.add_or_update_device(cam_a.to_dict())
        self.config_mgr.add_or_update_device(cam_b.to_dict())

        self.window = MainWindow(config_mgr=self.config_mgr)
        self.app.processEvents()

    def tearDown(self):
        try:
            self.window.close()
            self.app.processEvents()
            drained = drain_retired_threads(5000)
            self.window.deleteLater()
            QCoreApplication.sendPostedEvents(self.window, QEvent.Type.DeferredDelete)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(drained, "Todas as threads de streaming aposentadas devem encerrar")
        finally:
            for patcher in reversed(self._patchers):
                patcher.stop()
            self._tmpdir.cleanup()

    def test_removing_selected_camera_syncs_panels_to_remaining_selection(self):
        """Remover o Dispositivo Cadastrado selecionado B sincroniza PTZ e Gravações para A."""
        self.window._select_camera_by_id("B")
        self.app.processEvents()
        self.assertEqual(self.window.camera_grid.selected_camera_id, "B")
        self.assertIsNotNone(self.window.ptz_panel.camera)
        self.assertEqual(self.window.ptz_panel.camera.id, "B")
        self.assertIsNotNone(self.window.recordings_panel.camera)
        self.assertEqual(self.window.recordings_panel.camera.id, "B")

        self.window._remove_camera_by_id("B")
        self.app.processEvents()

        self.assertEqual(self.window.camera_grid.selected_camera_id, "A")
        self.assertIsNotNone(self.window.ptz_panel.camera)
        self.assertEqual(self.window.ptz_panel.camera.id, "A")
        self.assertIsNotNone(self.window.recordings_panel.camera)
        self.assertEqual(self.window.recordings_panel.camera.id, "A")

    def test_removing_unselected_camera_keeps_panels_on_selected_camera(self):
        """Remover o Dispositivo Cadastrado não selecionado B mantém PTZ e Gravações em A."""
        self.window._select_camera_by_id("A")
        self.app.processEvents()

        self.window._remove_camera_by_id("B")
        self.app.processEvents()

        self.assertEqual(self.window.camera_grid.selected_camera_id, "A")
        self.assertIsNotNone(self.window.ptz_panel.camera)
        self.assertEqual(self.window.ptz_panel.camera.id, "A")
        self.assertIsNotNone(self.window.recordings_panel.camera)
        self.assertEqual(self.window.recordings_panel.camera.id, "A")

    def test_removing_last_remaining_camera_clears_selection_and_panels(self):
        """Remover o último Dispositivo Cadastrado limpa seleção, painéis e desabilita controles PTZ."""
        self.window._select_camera_by_id("B")
        self.window._remove_camera_by_id("B")
        self.app.processEvents()

        self.window._remove_camera_by_id("A")
        self.app.processEvents()

        self.assertIsNone(self.window.camera_grid.selected_camera_id)
        self.assertIsNone(self.window.ptz_panel.camera)
        self.assertFalse(self.window.ptz_panel.controls_frame.isEnabled())
        self.assertIsNone(self.window.recordings_panel.camera)

    def test_select_camera_by_id_none_or_unknown_clears_panels(self):
        """_select_camera_by_id limpa os painéis quando recebe None ou ID ausente."""
        self.window._select_camera_by_id("A")
        self.app.processEvents()
        self.assertIsNotNone(self.window.ptz_panel.camera)
        self.assertIsNotNone(self.window.recordings_panel.camera)

        self.window._select_camera_by_id(None)
        self.assertIsNone(self.window.ptz_panel.camera)
        self.assertFalse(self.window.ptz_panel.controls_frame.isEnabled())
        self.assertIsNone(self.window.recordings_panel.camera)

        self.window._select_camera_by_id("A")
        self.assertIsNotNone(self.window.ptz_panel.camera)
        self.window._select_camera_by_id("INEXISTENTE")
        self.assertIsNone(self.window.ptz_panel.camera)
        self.assertFalse(self.window.ptz_panel.controls_frame.isEnabled())
        self.assertIsNone(self.window.recordings_panel.camera)


if __name__ == "__main__":
    unittest.main()
