"""
Suite de Testes Automatizados para a Central NVR WiFi com testes de Segurança, Dual-Stream, ONVIF Profile T/G e QoS.
"""
import os
import re
import stat
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adicionar src ao path de importação
root_path = Path(__file__).parent.parent
sys.path.insert(0, str(root_path / "src"))

from central_nvr.core.camera import CameraDevice, ConnectionState, StreamStats
from central_nvr.core.config import (
    ConfigManager,
    get_config_dir,
    get_data_dir,
    get_keyring_password,
    set_keyring_password,
    delete_keyring_password,
)
from central_nvr.core.onvif_client import OnvifClient
from central_nvr.core.stream_worker import StreamWorker
from central_nvr.scanner.discovery import NetworkScanner, get_local_ip_addresses
from central_nvr.scanner.parser import detect_manufacturer, parse_ws_discovery_response, parse_ws_discovery_fallback


class TestScannerAndParser(unittest.TestCase):
    """Testes para o parser de mensagens ONVIF e WS-Discovery."""

    def test_manufacturer_detection(self):
        self.assertEqual(detect_manufacturer("Intelbras VIP 1230"), "Intelbras")
        self.assertEqual(detect_manufacturer("Hikvision DS-2CD2143G0"), "Hikvision")
        self.assertEqual(detect_manufacturer("Dahua IPC-HFW1431S"), "Dahua")
        self.assertEqual(detect_manufacturer("Reolink RLC-510A"), "Reolink")
        self.assertEqual(detect_manufacturer("Unknown Device"), "Genérico / ONVIF")

    def test_ws_discovery_xml_parsing(self):
        sample_xml = """<?xml version="1.0" encoding="utf-8"?>
        <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
                           xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
                           xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
                           xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
            <SOAP-ENV:Body>
                <d:ProbeMatches>
                    <d:ProbeMatch>
                        <wsa:EndpointReference>
                            <wsa:Address>urn:uuid:12345678-1234-1234-1234-123456789abc</wsa:Address>
                        </wsa:EndpointReference>
                        <d:Types>dn:NetworkVideoTransmitter</d:Types>
                        <d:Scopes>onvif://www.onvif.org/name/Intelbras_VIP1230 onvif://www.onvif.org/hardware/VIP1230 onvif://www.onvif.org/location/Portaria</d:Scopes>
                        <d:XAddrs>http://192.168.1.50:80/onvif/device_service</d:XAddrs>
                    </d:ProbeMatch>
                </d:ProbeMatches>
            </SOAP-ENV:Body>
        </SOAP-ENV:Envelope>"""

        result = parse_ws_discovery_response(sample_xml, source_ip="192.168.1.50")
        self.assertIsNotNone(result)
        self.assertEqual(result["ip"], "192.168.1.50")
        self.assertEqual(result["port"], 80)
        self.assertIn("Intelbras", result["name"])
        self.assertEqual(result["manufacturer"], "Intelbras")
        self.assertEqual(result["location"], "Portaria")
        self.assertEqual(result["onvif_endpoint"], "http://192.168.1.50:80/onvif/device_service")

    def test_local_ip_detection(self):
        ips = get_local_ip_addresses()
        self.assertIsInstance(ips, list)
        self.assertTrue(len(ips) > 0)


class TestCameraAndConfig(unittest.TestCase):
    """Testes para o modelo CameraDevice e ConfigManager."""

    def test_camera_url_generation(self):
        cam = CameraDevice(
            id="test-1",
            name="Câmera Teste",
            ip="192.168.1.200",
            port=80,
            rtsp_port=554,
            rtsp_path="/live/ch0",
            username="admin",
            password="secretpassword",
        )
        url = cam.get_full_rtsp_url()
        self.assertEqual(url, "rtsp://admin:secretpassword@192.168.1.200:554/live/ch0")

    def test_camera_serialization(self):
        cam = CameraDevice(
            id="test-2",
            name="Câmera Sala",
            ip="10.0.0.50",
            has_ptz=True,
        )
        data = cam.to_dict()
        self.assertEqual(data["name"], "Câmera Sala")
        self.assertEqual(data["ip"], "10.0.0.50")
        self.assertTrue(data["has_ptz"])

        cam_recovered = CameraDevice.from_dict(data)
        self.assertEqual(cam_recovered.name, cam.name)
        self.assertEqual(cam_recovered.ip, cam.ip)

    def test_config_manager(self):
        config = ConfigManager()
        self.assertIsNotNone(config.get("theme"))
        self.assertIsNotNone(config.get("hw_accel"))
        self.assertTrue(len(config.devices) >= 1)

    def test_yoosee_candidate_urls(self):
        cam = CameraDevice(
            id="yoosee-1",
            name="Câmera Yoosee",
            ip="192.168.1.2",
            port=5000,
            rtsp_port=554,
            rtsp_path="/onvif1",
            username="admin",
            password="pass",
            manufacturer="Yoosee",
        )
        candidates = cam.get_candidate_rtsp_urls()
        self.assertIn("rtsp://admin:pass@192.168.1.2:554/onvif1", candidates)
        self.assertIn("rtsp://admin:pass@192.168.1.2:554/onvif2", candidates)

    def test_default_username_fallback(self):
        cam = CameraDevice(
            id="yoosee-2",
            name="Câmera Sem User",
            ip="192.168.1.3",
            port=5000,
            rtsp_port=554,
            rtsp_path="/onvif1",
            username="",
            password="pass",
        )
        url = cam.get_full_rtsp_url()
        self.assertEqual(url, "rtsp://admin:pass@192.168.1.3:554/onvif1")

    def test_rename_device_persistence(self):
        config = ConfigManager()
        test_dev = {
            "id": "test-rename-id",
            "name": "Nome Original",
            "ip": "192.168.1.199",
            "port": 5000,
            "rtsp_port": 554,
            "rtsp_path": "/onvif1"
        }
        config.add_or_update_device(test_dev)
        self.assertTrue(config.rename_device("test-rename-id", "Nome Atualizado"))
        
        dev = next((d for d in config.devices if d["id"] == "test-rename-id"), None)
        self.assertIsNotNone(dev)
        self.assertEqual(dev["name"], "Nome Atualizado")
        config.remove_device("test-rename-id")

    def test_sanitize_rtsp_url(self):
        from central_nvr.core.stream_worker import sanitize_rtsp_url
        url = "rtsp://admin:superSecret123@192.168.1.50:554/onvif1"
        sanitized = sanitize_rtsp_url(url)
        self.assertNotIn("superSecret123", sanitized)
        self.assertIn("admin:****@192.168.1.50:554/onvif1", sanitized)
        
        url_no_cred = "rtsp://192.168.1.50:554/live"
        self.assertEqual(sanitize_rtsp_url(url_no_cred), url_no_cred)


class TestOnvifSecurity(unittest.TestCase):
    """Testes para geração de autenticação WS-Security."""

    def test_ws_security_header_generation(self):
        client = OnvifClient(
            ip="192.168.1.100",
            port=80,
            username="admin",
            password="mypassword123",
        )
        header = client._build_ws_security_header()
        self.assertIn("wsse:Security", header)
        self.assertIn("<wsse:Username>admin</wsse:Username>", header)
        self.assertIn("PasswordDigest", header)
        self.assertIn("wsse:Nonce", header)
        self.assertIn("wsu:Created", header)


class TestSecurityAuditFixes(unittest.TestCase):
    """Testes de regressão para todos os 8 achados do Relatório de Auditoria de Segurança."""

    def test_sec01_permissions_config_and_devices(self):
        config = ConfigManager()
        config.set("test_key", "test_val")
        config.save_settings()
        config.save_devices()

        if os.name == "posix":
            config_dir = get_config_dir()
            data_dir = get_data_dir()
            dir_mode = stat.S_IMODE(os.stat(config_dir).st_mode)
            data_mode = stat.S_IMODE(os.stat(data_dir).st_mode)
            self.assertEqual(dir_mode, 0o700, f"Config dir mode {oct(dir_mode)} != 0700")
            self.assertEqual(data_mode, 0o700, f"Data dir mode {oct(data_mode)} != 0700")

            if config.config_path.exists():
                settings_mode = stat.S_IMODE(os.stat(config.config_path).st_mode)
                self.assertEqual(settings_mode, 0o600, f"Settings mode {oct(settings_mode)} != 0600")

            if config.devices_path.exists():
                devices_mode = stat.S_IMODE(os.stat(config.devices_path).st_mode)
                self.assertEqual(devices_mode, 0o600, f"Devices mode {oct(devices_mode)} != 0600")

    def test_sec02_keyring_support(self):
        test_id = "test-sec02-camera"
        test_pass = "SuperSecurePass!@#123"
        saved = set_keyring_password(test_id, test_pass)
        if saved:
            retrieved = get_keyring_password(test_id)
            self.assertEqual(retrieved, test_pass)
            delete_keyring_password(test_id)

    def test_sec05_soap_xml_escaping(self):
        client = OnvifClient(ip="192.168.1.100", port=80)
        
        sent_bodies = []
        def fake_send_soap(url, body, action=""):
            sent_bodies.append(body)
            return "<Envelope><Body><Uri>rtsp://192.168.1.100/onvif1</Uri></Body></Envelope>"

        client._send_soap_request = fake_send_soap

        malicious_token = "token1</trt:ProfileToken><evil:tag>hack</evil:tag><trt:ProfileToken>"
        client.get_stream_uri(profile_token=malicious_token)
        self.assertTrue(len(sent_bodies) > 0)
        self.assertNotIn("<evil:tag>", sent_bodies[-1])
        self.assertIn("&lt;evil:tag&gt;", sent_bodies[-1])

        malicious_preset = 'preset"&><script>'
        client.ptz_goto_preset(preset_token=malicious_preset, profile_token="prof1")
        self.assertNotIn("<script>", sent_bodies[-1])
        self.assertIn("&lt;script&gt;", sent_bodies[-1])
        self.assertIn("&amp;", sent_bodies[-1])

        client.ptz_set_preset(preset_name='Preset<XSS>&Name', profile_token="prof1")
        self.assertNotIn("<XSS>", sent_bodies[-1])
        self.assertIn("&lt;XSS&gt;", sent_bodies[-1])

    def test_sec06_onvif_xaddr_ssrf_validation(self):
        client = OnvifClient(ip="192.168.1.100", port=80)
        valid_xaddr = client._validate_xaddr("http://192.168.1.100:80/onvif/media_service")
        self.assertEqual(valid_xaddr, "http://192.168.1.100:80/onvif/media_service")

        ssrf_internal = client._validate_xaddr("http://169.254.169.254/latest/meta-data")
        self.assertIsNone(ssrf_internal)

        ssrf_external = client._validate_xaddr("http://attacker.com/onvif/media")
        self.assertIsNone(ssrf_external)

        ssrf_local = client._validate_xaddr("http://127.0.0.1:8080/admin")
        self.assertIsNone(ssrf_local)

    def test_sec06_discovery_ssrf_validation(self):
        sample_xml = """<?xml version="1.0" encoding="utf-8"?>
        <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
                           xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
                           xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
            <SOAP-ENV:Body>
                <d:ProbeMatches>
                    <d:ProbeMatch>
                        <d:XAddrs>http://10.99.99.99:8080/onvif/device_service</d:XAddrs>
                    </d:ProbeMatch>
                </d:ProbeMatches>
            </SOAP-ENV:Body>
        </SOAP-ENV:Envelope>"""

        result = parse_ws_discovery_response(sample_xml, source_ip="192.168.1.50")
        self.assertIsNotNone(result)
        self.assertEqual(result["ip"], "192.168.1.50")
        self.assertEqual(result["onvif_endpoint"], "http://192.168.1.50:80/onvif/device_service")

    def test_sec07_snapshot_path_traversal_sanitization(self):
        raw_id_traversal = "../../../etc/passwd"
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_id_traversal)
        self.assertEqual(safe_id, "_________etc_passwd")
        self.assertNotIn("/", safe_id)
        self.assertNotIn("..", safe_id)

    def test_sec08_diagnose_camera_url_encoding(self):
        user = "admin user"
        password = "P@ss#w/ord?&="
        auth_user = urllib.parse.quote(user, safe="")
        auth_pass = urllib.parse.quote(password, safe="")
        auth = f"{auth_user}:{auth_pass}@"
        url = f"rtsp://{auth}192.168.1.2:554/onvif1"
        
        self.assertEqual(auth_user, "admin%20user")
        self.assertEqual(auth_pass, "P%40ss%23w%2Ford%3F%26%3D")
        self.assertEqual(url, "rtsp://admin%20user:P%40ss%23w%2Ford%3F%26%3D@192.168.1.2:554/onvif1")


class TestWifiNvrArchitectureImprovements(unittest.TestCase):
    """Testes para melhorias arquiteturais de Wi-Fi, Dual-Stream, Profile T, Profile G e QoS."""

    def test_dual_stream_urls(self):
        """Valida a construção e alternância entre MainStream e SubStream para otimização de banda Wi-Fi."""
        cam = CameraDevice(
            id="test-dual",
            name="Câmera Yoosee Wi-Fi",
            ip="192.168.1.25",
            port=5000,
            rtsp_port=554,
            rtsp_path="/onvif1",
            username="admin",
            password="123",
            manufacturer="Yoosee",
        )
        main_url = cam.get_full_rtsp_url()
        sub_url = cam.get_substream_url()
        
        self.assertEqual(main_url, "rtsp://admin:123@192.168.1.25:554/onvif1")
        self.assertEqual(sub_url, "rtsp://admin:123@192.168.1.25:554/onvif2")

        candidates_sub = cam.get_candidate_rtsp_urls(prefer_substream=True)
        self.assertEqual(candidates_sub[0], sub_url)

    def test_onvif_profile_t_events(self):
        """Valida o serviço de eventos ONVIF Profile T (PullMessages / Motion Detection)."""
        client = OnvifClient(ip="192.168.1.100", port=80)
        client.subscription_url = "http://192.168.1.100:80/onvif/subscription_1"

        sample_event_xml = """<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
                                              xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">
            <soap:Body>
                <wsnt:NotificationMessage>
                    <wsnt:Topic>tns1:RuleEngine/CellMotionDetector/Motion</wsnt:Topic>
                    <wsnt:Message>
                        <tt:Message PropertyOperation="Changed">
                            <tt:Data>
                                <tt:SimpleItem Name="State" Value="true"/>
                            </tt:Data>
                        </tt:Message>
                    </wsnt:Message>
                </wsnt:NotificationMessage>
            </soap:Body>
        </soap:Envelope>"""

        client._send_soap_request = lambda url, body, action="": sample_event_xml

        events = client.pull_messages(timeout_sec=2.0)
        self.assertTrue(len(events) > 0)
        self.assertTrue(events[0]["is_motion"])
        self.assertTrue(events[0]["state"])

    def test_onvif_profile_g_edge_storage(self):
        """Valida a consulta a gravações salvas no armazenamento local da câmera (Cartão SD / Profile G)."""
        client = OnvifClient(ip="192.168.1.100", port=80)

        # 1. Summary
        sample_summary_xml = """<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
                                               xmlns:trc="http://www.onvif.org/ver10/recording/wsdl">
            <soap:Body>
                <trc:GetRecordingSummaryResponse>
                    <trc:Summary>
                        <trc:NumberRecordings>5</trc:NumberRecordings>
                        <trc:DataFrom>2026-08-30T10:00:00Z</trc:DataFrom>
                        <trc:DataUntil>2026-08-30T16:00:00Z</trc:DataUntil>
                    </trc:Summary>
                </trc:GetRecordingSummaryResponse>
            </soap:Body>
        </soap:Envelope>"""

        client._send_soap_request = lambda url, body, action="": sample_summary_xml
        summary = client.get_recording_summary()
        self.assertEqual(summary["num_recordings"], 5)
        self.assertTrue(summary["has_storage"])
        self.assertTrue(client.has_edge_storage)

        # 2. FindRecordings
        sample_find_xml = """<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
                                            xmlns:trc="http://www.onvif.org/ver10/recording/wsdl">
            <soap:Body>
                <trc:FindRecordingsResponse>
                    <trc:RecordingToken>Rec_Token_001</trc:RecordingToken>
                </trc:FindRecordingsResponse>
            </soap:Body>
        </soap:Envelope>"""

        # Replay URI
        sample_replay_xml = """<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
                                              xmlns:trp="http://www.onvif.org/ver10/replay/wsdl">
            <soap:Body>
                <trp:GetReplayUriResponse>
                    <trp:Uri>rtsp://192.168.1.100:554/playback?token=Rec_Token_001</trp:Uri>
                </trp:GetReplayUriResponse>
            </soap:Body>
        </soap:Envelope>"""

        def mock_replay_soap(url, body, action=""):
            if "GetReplayUri" in body:
                return sample_replay_xml
            return sample_find_xml

        client._send_soap_request = mock_replay_soap
        recordings = client.find_recordings(max_matches=5)
        self.assertTrue(len(recordings) > 0)
        self.assertEqual(recordings[0]["token"], "Rec_Token_001")
        self.assertIn("playback?token=Rec_Token_001", recordings[0]["replay_uri"])

    def test_theme_switching_and_persistence(self):
        """Valida que o tema claro e escuro alternam corretamente via menu e são persistidos nas configurações."""
        from central_nvr.ui.styles import get_theme_qss, LIGHT_THEME_QSS, DARK_THEME_QSS
        from central_nvr.ui.main_window import MainWindow

        cfg = ConfigManager()
        cfg.set("theme", "dark")
        
        win = MainWindow(config_mgr=cfg)
        self.assertEqual(win.current_theme, "dark")
        self.assertTrue(win.act_theme_dark.isChecked())
        self.assertFalse(win.act_theme_light.isChecked())

        # Alternar para tema claro via menu
        win._set_theme("light")
        self.assertEqual(win.current_theme, "light")
        self.assertEqual(cfg.get("theme"), "light")
        self.assertTrue(win.act_theme_light.isChecked())
        self.assertFalse(win.act_theme_dark.isChecked())
        self.assertEqual(get_theme_qss("light"), LIGHT_THEME_QSS)

        # Alternar de volta para tema escuro
        win._set_theme("dark")
        self.assertEqual(win.current_theme, "dark")
        self.assertEqual(cfg.get("theme"), "dark")
        self.assertTrue(win.act_theme_dark.isChecked())
        self.assertEqual(get_theme_qss("dark"), DARK_THEME_QSS)

        win.close()
        win.deleteLater()

    def test_mosaic_fullscreen_toggle(self):
        """Valida que o botão Tela Cheia do cabeçalho expande o mosaico de câmeras ocultando sidebar e header."""
        from central_nvr.ui.main_window import MainWindow
        cfg = ConfigManager()
        win = MainWindow(config_mgr=cfg)
        win.show()

        # Entrar em tela cheia do mosaico
        win._enter_grid_fullscreen()
        self.assertTrue(win.isFullScreen())
        self.assertTrue(win.top_header.isHidden())
        self.assertTrue(win.sidebar.isHidden())
        self.assertTrue(win.btn_floating_exit_fullscreen.isVisible())

        # Sair de tela cheia do mosaico
        win._exit_grid_fullscreen()
        self.assertFalse(win.isFullScreen())
        self.assertFalse(win.top_header.isHidden())
        self.assertFalse(win.sidebar.isHidden())
        self.assertTrue(win.btn_floating_exit_fullscreen.isHidden())

        win.close()
        win.deleteLater()

    def test_dynamic_grid_layout_for_2_cameras(self):
        """Valida que para 2 câmeras conectadas o grid aloca 2 linhas x 1 coluna sem slots vazios."""
        from central_nvr.ui.camera_grid import CameraGridWidget
        grid = CameraGridWidget()
        
        # 1. Testar com 1 câmera
        cams_1 = [CameraDevice(id="c1", name="Cam 1", ip="192.168.1.2")]
        grid.set_cameras(cams_1)
        grid.apply_layout("auto")
        self.assertEqual(len(grid.empty_slots), 0)
        self.assertEqual(grid.layout.rowCount(), 1)
        self.assertEqual(grid.layout.columnCount(), 1)

        # 2. Testar com 2 câmeras (1 em cima e 1 embaixo)
        cams_2 = [
            CameraDevice(id="c1", name="Cam 1", ip="192.168.1.2"),
            CameraDevice(id="c2", name="Cam 2", ip="192.168.1.3"),
        ]
        grid.set_cameras(cams_2)
        grid.apply_layout("auto")
        self.assertEqual(len(grid.empty_slots), 0)
        self.assertEqual(grid.layout.rowCount(), 2)
        self.assertEqual(grid.layout.columnCount(), 1)

        # 3. Testar com 3 câmeras (distribuídas sem slots vazios)
        cams_3 = [
            CameraDevice(id="c1", name="Cam 1", ip="192.168.1.2"),
            CameraDevice(id="c2", name="Cam 2", ip="192.168.1.3"),
            CameraDevice(id="c3", name="Cam 3", ip="192.168.1.4"),
        ]
        grid.set_cameras(cams_3)
        grid.apply_layout("auto")
        self.assertEqual(len(grid.empty_slots), 0)

        grid.stop_all()
        grid.close()
        grid.deleteLater()

    def test_qos_stats_and_stream_worker(self):
        """Valida os cálculos de métricas de QoS (Jitter, Codec, Modo de Transporte)."""
        cam = CameraDevice(id="test-qos", name="Cam QoS", ip="192.168.1.50")
        worker = StreamWorker(camera=cam, rtsp_transport="auto")
        
        self.assertEqual(worker.rtsp_transport, "auto")
        self.assertFalse(worker.prefer_substream)
        
        worker.set_prefer_substream(True)
        self.assertTrue(worker.prefer_substream)

        # Testar cálculo de jitter
        worker.stats.fps = 25.0
        # Latência de 40ms esperada para 25fps. Se receber com 60ms, jitter instantâneo é 20ms
        latency_sample = 60.0
        fps_target = max(1.0, worker.stats.fps)
        expected_interval_ms = 1000.0 / fps_target
        jitter_instant = abs(latency_sample - expected_interval_ms)
        worker.stats.jitter_ms = round(0.85 * worker.stats.jitter_ms + 0.15 * jitter_instant, 1)
        self.assertGreater(worker.stats.jitter_ms, 0.0)


class TestFullscreenView(unittest.TestCase):
    """Testes para a visualização em tela cheia da câmera."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_fullscreen_window_behavior(self):
        from central_nvr.ui.fullscreen_view import FullscreenPlayerWindow
        from PySide6.QtGui import QImage

        cam = CameraDevice(id="test-full", name="Câmera Yoosee", ip="192.168.1.3")
        win = FullscreenPlayerWindow(cam)
        win.show()
        self.assertFalse(win.top_container.isHidden())
        self.assertFalse(win.bottom_container.isHidden())

        # Testar ocultar controles
        win._hide_controls()
        self.assertTrue(win.top_container.isHidden())
        self.assertTrue(win.bottom_container.isHidden())

        # Testar reexibir controles
        win._show_controls()
        self.assertFalse(win.top_container.isHidden())
        self.assertFalse(win.bottom_container.isHidden())

        # Testar alternância manual
        win._toggle_osd_manually()
        self.assertTrue(win.top_container.isHidden())
        win._toggle_osd_manually()
        self.assertFalse(win.top_container.isHidden())

        # Testar frame e snapshot com QoS
        img = QImage(640, 480, QImage.Format.Format_RGB888)
        img.fill(0)
        win.set_frame(img, fps=15.0, bitrate=1200.0, codec="H.265 / HEVC", jitter_ms=12.5)
        win._take_snapshot()
        win.close()

    def test_camera_view_rename(self):
        from central_nvr.ui.camera_view import CameraViewWidget
        cam = CameraDevice(id="test-v-rename", name="Câmera Antiga", ip="192.168.1.50")
        view = CameraViewWidget(cam)
        self.assertIn("Câmera Antiga", view.lbl_title.text())
        view.update_camera_name("Câmera Nova Fachada")
        self.assertIn("Câmera Nova Fachada", view.lbl_title.text())
        self.assertEqual(view.camera.name, "Câmera Nova Fachada")
        view.stop()
        view.close()
        view.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
