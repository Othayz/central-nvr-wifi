#!/usr/bin/env python3
"""
Testes de regressão das Issues #52 e #80:
Sincronização dos painéis PTZ e de Gravações na seleção inicial, no cadastro/atualização
e ao remover um Dispositivo Cadastrado.
"""
import os
import sys
import tempfile
import time
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


def _registered_camera(
    cam_id: str,
    name: str,
    ip: str,
    port: int = 80,
    password: str = "",
    has_ptz: bool = True,
) -> CameraDevice:
    return CameraDevice(
        id=cam_id,
        name=name,
        ip=ip,
        port=port,
        password=password,
        rtsp_url="demo://",
        has_ptz=has_ptz,
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

    def test_removing_camera_cleans_ptz_cache_and_queue(self):
        """#54: remover Dispositivo Cadastrado limpa cache ONVIF e encerra a SerialCommandQueue."""
        self.window._select_camera_by_id("B")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and self.window.ptz_panel.client is None:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNotNone(self.window.ptz_panel.client)

        cam_b = self.window.ptz_panel.camera
        key_b = self.window.ptz_panel._client_key(cam_b)
        self.assertIn(key_b, self.window.ptz_panel._client_cache)
        self.assertIn(key_b, self.window.ptz_panel._queues)
        old_queue = self.window.ptz_panel._queues[key_b]

        self.window._remove_camera_by_id("B")
        self.app.processEvents()

        self.assertNotIn(key_b, self.window.ptz_panel._client_cache)
        self.assertNotIn(key_b, self.window.ptz_panel._queues)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and old_queue._thread.is_alive():
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(old_queue._thread.is_alive())


class InitialAndDiscoverySelectionSyncTest(unittest.TestCase):
    """Regressão da Issue #80: seleção inicial e cadastro/recadastro sincronizam PTZ e Gravações."""

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
        ]
        for patcher in self._patchers:
            patcher.start()

        self.config_mgr = ConfigManager()
        self.config_mgr.set("periodic_update_check", False)
        self._windows = []

    def _create_window(self) -> MainWindow:
        window = MainWindow(config_mgr=self.config_mgr)
        self._windows.append(window)
        self.app.processEvents()
        return window

    def _wait_for_ptz_client(self, window: MainWindow, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and window.ptz_panel.client is None:
            self.app.processEvents()
            time.sleep(0.01)

    def tearDown(self):
        try:
            for window in self._windows:
                window.close()
                self.app.processEvents()
            drained = drain_retired_threads(5000)
            for window in self._windows:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(drained, "Todas as threads de streaming aposentadas devem encerrar")
        finally:
            for patcher in reversed(self._patchers):
                patcher.stop()
            self._tmpdir.cleanup()

    def test_single_registered_camera_syncs_panels_on_startup(self):
        """Com 1 Dispositivo Cadastrado, abrir a MainWindow já sincroniza PTZ e Gravações."""
        cam_a = _registered_camera("A", "Portaria", "192.0.2.10", has_ptz=True)
        self.config_mgr.add_or_update_device(cam_a.to_dict())

        window = self._create_window()
        self._wait_for_ptz_client(window)

        loaded_cam = window.cameras[0]
        self.assertEqual(window.camera_grid.selected_camera_id, "A")
        self.assertIs(window.ptz_panel.camera, loaded_cam)
        self.assertEqual(window.ptz_panel.lbl_cam_target.text(), "Portaria (Suporta PTZ)")
        self.assertTrue(window.ptz_panel.controls_frame.isEnabled())
        self.assertIs(window.recordings_panel.camera, loaded_cam)
        self.assertEqual(window.recordings_panel.lbl_cam_name.text(), "Portaria (192.0.2.10)")

    def test_adding_first_camera_via_discovery_syncs_panels(self):
        """Cadastrar o primeiro Dispositivo Cadastrado sincroniza PTZ e Gravações imediatamente."""
        window = self._create_window()
        self.assertIsNone(window.camera_grid.selected_camera_id)
        self.assertIsNone(window.ptz_panel.camera)
        self.assertIsNone(window.recordings_panel.camera)

        cam_a = _registered_camera("A", "Portaria", "192.0.2.10", has_ptz=True)
        added = window._add_discovered_camera(cam_a)
        self.app.processEvents()
        self._wait_for_ptz_client(window)

        self.assertTrue(added)
        loaded_cam = window.cameras[0]
        self.assertEqual(window.camera_grid.selected_camera_id, "A")
        self.assertIs(window.ptz_panel.camera, loaded_cam)
        self.assertEqual(window.ptz_panel.lbl_cam_target.text(), "Portaria (Suporta PTZ)")
        self.assertTrue(window.ptz_panel.controls_frame.isEnabled())
        self.assertIs(window.recordings_panel.camera, loaded_cam)
        self.assertEqual(window.recordings_panel.lbl_cam_name.text(), "Portaria (192.0.2.10)")

    def test_readding_selected_camera_updates_panels_to_new_device_instance(self):
        """Recadastrar um Dispositivo Cadastrado já selecionado atualiza os painéis para a nova instância."""
        cam_old = _registered_camera(
            "A",
            "Portaria",
            "192.0.2.10",
            port=80,
            password="antiga",
            has_ptz=True,
        )
        self.config_mgr.add_or_update_device(cam_old.to_dict())

        window = self._create_window()
        self._wait_for_ptz_client(window)

        old_instance = window.cameras[0]
        self.assertIs(window.ptz_panel.camera, old_instance)
        self.assertIs(window.recordings_panel.camera, old_instance)

        cam_updated = _registered_camera(
            "A",
            "Portaria Atualizada",
            "192.0.2.10",
            port=8080,
            password="nova",
            has_ptz=False,
        )
        saved = window._add_discovered_camera(cam_updated)
        self.app.processEvents()

        self.assertTrue(saved)
        new_instance = next(c for c in window.cameras if c.id == "A")
        self.assertIsNot(new_instance, old_instance)
        self.assertEqual(window.camera_grid.selected_camera_id, "A")
        self.assertIs(window.ptz_panel.camera, new_instance)
        self.assertEqual(window.ptz_panel.camera.port, 8080)
        self.assertEqual(window.ptz_panel.camera.password, "nova")
        self.assertFalse(window.ptz_panel.camera.has_ptz)
        self.assertFalse(window.ptz_panel.controls_frame.isEnabled())
        self.assertEqual(window.ptz_panel.lbl_cam_target.text(), "Portaria Atualizada (PTZ Desativado)")
        self.assertIs(window.recordings_panel.camera, new_instance)
        self.assertEqual(window.recordings_panel.lbl_cam_name.text(), "Portaria Atualizada (192.0.2.10)")


if __name__ == "__main__":
    unittest.main()
