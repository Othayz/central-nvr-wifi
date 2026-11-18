#!/usr/bin/env python3
"""
Testes do cadastro de Dispositivo Descoberto com consulta ONVIF (Issue #45).
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from central_nvr.core.camera import CameraDevice
from central_nvr.core.onvif_client import OnvifAuthError, OnvifConnectionError
from central_nvr.core.onvif_enrich import FALLBACK_WARNING, enrich_from_onvif


class FakeClient:
    behavior = "ok"  # "ok" | "down" | "auth" | "no_uri"
    instances = []

    def __init__(self, ip, port=80, username="", password="", timeout=4.0):
        self.ip, self.port = ip, port
        self.has_ptz = False
        FakeClient.instances.append(self)

    def get_device_information(self):
        if FakeClient.behavior == "down":
            raise OnvifConnectionError("recusado")
        return {"manufacturer": "Intelbras", "model": "IM5"}

    def get_capabilities(self):
        if FakeClient.behavior == "auth":
            raise OnvifAuthError("401")
        self.has_ptz = True
        return {}

    def get_profiles(self):
        if FakeClient.behavior == "no_uri":
            return [{"token": "main", "stream_uri": ""}]
        return [
            {"token": "main", "stream_uri": f"rtsp://{self.ip}:8554/cam/realmonitor?channel=1&subtype=0"},
            {"token": "sub", "stream_uri": f"rtsp://{self.ip}:8554/cam/realmonitor?channel=1&subtype=1"},
        ]


def _camera():
    return CameraDevice(id="cam_10_0_0_5", name="Portão", ip="10.0.0.5", port=80,
                        rtsp_path="/live/ch0", username="admin", password="x", has_ptz=False)


class EnrichTest(unittest.TestCase):
    def setUp(self):
        FakeClient.behavior = "ok"
        FakeClient.instances = []
        self.patcher = patch("central_nvr.core.onvif_enrich.OnvifClient", FakeClient)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_registers_uri_from_get_stream_uri(self):
        cam = _camera()
        self.assertIsNone(enrich_from_onvif(cam))
        self.assertEqual(cam.rtsp_url, "rtsp://10.0.0.5:8554/cam/realmonitor?channel=1&subtype=0")
        self.assertEqual(cam.rtsp_port, 8554)
        self.assertEqual(cam.rtsp_path, "/cam/realmonitor?channel=1&subtype=0")
        self.assertEqual(cam.substream_url, "rtsp://10.0.0.5:8554/cam/realmonitor?channel=1&subtype=1")
        self.assertEqual(cam.manufacturer, "Intelbras")
        self.assertEqual(cam.model, "IM5")
        # A URL usada para conectar leva as credenciais
        self.assertEqual(cam.get_full_rtsp_url(),
                         "rtsp://admin:x@10.0.0.5:8554/cam/realmonitor?channel=1&subtype=0")
        self.assertEqual(cam.get_substream_url(),
                         "rtsp://admin:x@10.0.0.5:8554/cam/realmonitor?channel=1&subtype=1")

    def test_has_ptz_reflects_device(self):
        cam = _camera()
        enrich_from_onvif(cam)
        self.assertTrue(cam.has_ptz)

    def test_failure_keeps_deduced_path_and_warns(self):
        FakeClient.behavior = "down"
        cam = _camera()
        cam.has_ptz = True
        warning = enrich_from_onvif(cam)
        self.assertIn(FALLBACK_WARNING, warning)
        self.assertEqual(cam.rtsp_path, "/live/ch0")
        self.assertEqual(cam.rtsp_url, "")
        self.assertFalse(cam.has_ptz)

    def test_auth_failure_is_described(self):
        FakeClient.behavior = "auth"
        warning = enrich_from_onvif(_camera())
        self.assertIn("credenciais recusadas", warning)

    def test_missing_uri_keeps_deduced_path(self):
        FakeClient.behavior = "no_uri"
        cam = _camera()
        warning = enrich_from_onvif(cam)
        self.assertIn(FALLBACK_WARNING, warning)
        self.assertEqual(cam.rtsp_path, "/live/ch0")


class DialogEnrichTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeClient.behavior = "ok"
        self.patcher = patch("central_nvr.core.onvif_enrich.OnvifClient", FakeClient)
        self.patcher.start()
        from central_nvr.ui import discovery_dialog
        self.msg_patch = patch.object(discovery_dialog, "QMessageBox")
        self.msgbox = self.msg_patch.start()
        self.dialog = discovery_dialog.DiscoveryDialog()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.msg_patch.stop()
        self.patcher.stop()

    def _add_row(self, ip):
        self.dialog._on_device_found({"ip": ip, "port": 80, "name": f"Cam {ip}", "source": "ONVIF"})
        self.dialog.table.item(self.dialog.table.rowCount() - 1, 0).setCheckState(Qt.CheckState.Checked)

    def _run_add(self):
        added = []
        self.dialog.device_added.connect(added.append)
        main_thread_calls = []
        with patch("central_nvr.ui.discovery_dialog.enrich_from_onvif",
                   side_effect=lambda cam: (main_thread_calls.append(
                       QApplication.instance().thread().isCurrentThread()), enrich_from_onvif(cam))[1]):
            self.dialog._add_selected_devices()
            deadline = time.monotonic() + 3
            while not added and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        return added, main_thread_calls

    def test_added_camera_uses_onvif_uri_off_ui_thread(self):
        self._add_row("10.0.0.5")
        added, main_thread_calls = self._run_add()
        self.assertEqual(len(added), 1)
        self.assertTrue(added[0].rtsp_url.startswith("rtsp://10.0.0.5:8554/"))
        self.assertEqual(main_thread_calls, [False])
        self.msgbox.information.assert_called_once()

    def test_onvif_failure_shows_warning(self):
        FakeClient.behavior = "down"
        self._add_row("10.0.0.6")
        added, _ = self._run_add()
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].rtsp_path, "/live/ch0")
        self.msgbox.warning.assert_called_once()
        self.assertIn(FALLBACK_WARNING, self.msgbox.warning.call_args[0][2])


if __name__ == "__main__":
    unittest.main()
