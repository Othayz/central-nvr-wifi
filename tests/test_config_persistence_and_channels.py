#!/usr/bin/env python3
"""
Testes de regressão da Issue #36:
- Coexistência de múltiplos canais no mesmo IP/porta em ConfigManager e DiscoveryDialog.
- Limpeza de credenciais órfãs no keyring ao trocar ID ou remover Dispositivo Cadastrado.
- Propagação de erros de gravação (OSError/PermissionError) com log ERROR e QMessageBox.critical.
- Prevenção de double-close do descritor de arquivo em _secure_write_json.
- Validação de schema em ConfigManager.load().
- Leitura somente-leitura do token em resolve_github_token sem instanciar ConfigManager.
"""
import json
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
from PySide6.QtWidgets import QApplication

import central_nvr.core.config as cfg_mod
import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import ConfigManager, _secure_write_json
from central_nvr.core.threads import drain_retired_threads
from central_nvr.core.updater import resolve_github_token
from central_nvr.ui import discovery_dialog
from central_nvr.ui.discovery_dialog import DiscoveryDialog
from central_nvr.ui.main_window import MainWindow


class ConfigChannelsAndKeyringTest(unittest.TestCase):
    """Coexistência de múltiplos canais, limpeza de keyring e validação de schema."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmpdir.name) / "config"
        self.data_dir = Path(self._tmpdir.name) / "data"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keyring_store = {}

        def _set_pw(cam_id: str, pw: str) -> bool:
            if pw:
                self.keyring_store[cam_id] = pw
            else:
                self.keyring_store.pop(cam_id, None)
            return True

        def _get_pw(cam_id: str):
            return self.keyring_store.get(cam_id)

        def _del_pw(cam_id: str) -> bool:
            return self.keyring_store.pop(cam_id, None) is not None

        self._patchers = [
            patch.object(cfg_mod, "get_config_dir", return_value=self.config_dir),
            patch.object(cfg_mod, "get_data_dir", return_value=self.data_dir),
            patch.object(cfg_mod, "is_keyring_available", return_value=True),
            patch.object(cfg_mod, "set_keyring_password", side_effect=_set_pw),
            patch.object(cfg_mod, "get_keyring_password", side_effect=_get_pw),
            patch.object(cfg_mod, "delete_keyring_password", side_effect=_del_pw),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    def test_two_channels_same_ip_port_with_colliding_ids_coexist(self):
        """Dois canais com mesmo IP/porta e caminhos RTSP distintos coexistem mesmo com IDs colidentes."""
        cfg = ConfigManager()
        ch1 = {
            "id": "cam_192_168_1_10_80",
            "name": "NVR Canal 1",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/ch1",
            "password": "senha-ch1",
        }
        ch2 = {
            "id": "cam_192_168_1_10_80",
            "name": "NVR Canal 2",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/ch2",
            "password": "senha-ch2",
        }

        self.assertTrue(cfg.add_or_update_device(ch1))
        self.assertTrue(cfg.add_or_update_device(ch2))

        # O dicionário passado pelo chamador não deve ser mutado
        self.assertEqual(ch2["id"], "cam_192_168_1_10_80")

        self.assertEqual(len(cfg.devices), 2)
        paths = {d["rtsp_path"] for d in cfg.devices}
        ids = {d["id"] for d in cfg.devices}
        self.assertEqual(paths, {"/ch1", "/ch2"})
        self.assertEqual(len(ids), 2)
        self.assertIn("cam_192_168_1_10_80", ids)

        # Cada canal preserva sua própria senha no keyring
        by_path = {d["rtsp_path"]: d for d in cfg.devices}
        self.assertEqual(self.keyring_store.get(by_path["/ch1"]["id"]), "senha-ch1")
        self.assertEqual(self.keyring_store.get(by_path["/ch2"]["id"]), "senha-ch2")

        # Recarregar do disco mantém ambos os canais e suas credenciais
        reloaded = ConfigManager()
        self.assertEqual(len(reloaded.devices), 2)
        reloaded_by_path = {d["rtsp_path"]: d for d in reloaded.devices}
        self.assertEqual(reloaded_by_path["/ch1"]["password"], "senha-ch1")
        self.assertEqual(reloaded_by_path["/ch2"]["password"], "senha-ch2")

    def test_missing_device_id_is_generated_without_mutating_input(self):
        """Dispositivo Cadastrado sem ID recebe ID gerado automaticamente sem mutar a entrada."""
        cfg = ConfigManager()
        raw = {"name": "Sem ID", "ip": "192.168.1.50", "port": 80, "rtsp_path": "/live/ch0"}
        self.assertTrue(cfg.add_or_update_device(raw))
        self.assertNotIn("id", raw)
        self.assertEqual(len(cfg.devices), 1)
        self.assertTrue(cfg.devices[0]["id"].startswith("cam_"))

    def test_replacing_channel_with_new_id_cleans_old_keyring_and_remove_cleans_keyring(self):
        """Trocar o ID de um mesmo canal apaga a senha do ID antigo; remove_device limpa o keyring."""
        cfg = ConfigManager()
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_10",
            "name": "Portão",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/live/ch0",
            "password": "senha-antiga",
        }))
        self.assertEqual(self.keyring_store.get("cam_192_168_1_10"), "senha-antiga")

        # Mesmo canal (IP, porta e assinatura RTSP), mas com novo ID
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_10_80",
            "name": "Portão Atualizado",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/live/ch0",
            "password": "senha-nova",
        }))
        self.assertEqual(len(cfg.devices), 1)
        self.assertEqual(cfg.devices[0]["id"], "cam_192_168_1_10_80")
        self.assertNotIn("cam_192_168_1_10", self.keyring_store)
        self.assertEqual(self.keyring_store.get("cam_192_168_1_10_80"), "senha-nova")

        # Remover o Dispositivo Cadastrado limpa a senha do keyring e retorna True
        self.assertTrue(cfg.remove_device("cam_192_168_1_10_80"))
        self.assertEqual(cfg.devices, [])
        self.assertNotIn("cam_192_168_1_10_80", self.keyring_store)

    def test_load_validates_settings_and_devices_schema(self):
        """settings.json não-dict e entradas inválidas em devices.json não quebram o carregamento."""
        (self.config_dir / "settings.json").write_text(json.dumps(["invalido"]), encoding="utf-8")
        (self.config_dir / "devices.json").write_text(
            json.dumps(["lixo", 42, None, {"id": "cam_ok", "ip": "192.168.1.20", "name": "OK"}]),
            encoding="utf-8",
        )
        cfg = ConfigManager()
        self.assertEqual(cfg.get("theme"), "dark")
        self.assertEqual(len(cfg.devices), 1)
        self.assertEqual(cfg.devices[0]["id"], "cam_ok")

        # devices.json como objeto JSON em vez de lista resulta em lista vazia
        (self.config_dir / "devices.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        cfg2 = ConfigManager()
        self.assertEqual(cfg2.devices, [])


class ConfigWriteErrorsAndMainWindowTest(unittest.TestCase):
    """Propagação de erros de escrita em ConfigManager e alerta crítico na MainWindow."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self.config_dir = base / "config"
        self.data_dir = base / "data"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._patchers = [
            patch.object(cfg_mod, "get_config_dir", return_value=self.config_dir),
            patch.object(cfg_mod, "get_data_dir", return_value=self.data_dir),
            patch("central_nvr.ui.playback_view.get_data_dir", return_value=self.data_dir),
            patch.object(cfg_mod, "is_keyring_available", return_value=False),
            patch.object(cfg_mod, "HAS_KEYRING", False),
            patch.object(sw, "HAS_CV2", False),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self._tmpdir.cleanup()

    def test_write_errors_set_last_write_error_return_false_and_log_error(self):
        """OSError/PermissionError em settings.json ou devices.json grava last_write_error, retorna False e loga ERROR."""
        cfg = ConfigManager()

        with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("Permissão negada em settings")):
            with self.assertLogs(cfg_mod.logger, level="ERROR") as logs:
                ok_set = cfg.set("theme", "light")
                ok_upd = cfg.update({"theme": "dark"})
        self.assertFalse(ok_set)
        self.assertFalse(ok_upd)
        self.assertIn("Permissão negada em settings", cfg.last_write_error or "")
        self.assertTrue(any("ERROR" in m and "configurações" in m for m in logs.output))

        # Sucesso subsequente limpa last_write_error
        self.assertTrue(cfg.set("theme", "light"))
        self.assertIsNone(cfg.last_write_error)

        with patch.object(cfg_mod, "_secure_write_json", side_effect=OSError("Disco cheio")):
            with self.assertLogs(cfg_mod.logger, level="ERROR") as logs:
                ok_add = cfg.add_or_update_device({"id": "cam_err", "name": "Falha", "ip": "192.0.2.1"})
                ok_rem = cfg.remove_device("cam_err")
        self.assertFalse(ok_add)
        self.assertFalse(ok_rem)
        self.assertIn("Disco cheio", cfg.last_write_error or "")
        self.assertTrue(any("ERROR" in m and "dispositivos" in m for m in logs.output))

    def test_main_window_shows_critical_dialog_on_device_write_failure(self):
        """MainWindow._add_discovered_camera exibe QMessageBox.critical e registra evento ao falhar gravação."""
        cfg = ConfigManager()
        cfg.set("periodic_update_check", False)
        window = MainWindow(config_mgr=cfg)
        self.app.processEvents()
        try:
            cam = CameraDevice(
                id="cam_192_0_2_50_80",
                name="Garagem",
                ip="192.0.2.50",
                port=80,
                rtsp_url="demo://",
            )
            with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("somente leitura")), \
                    patch("central_nvr.ui.main_window.QMessageBox.critical") as critical_mock:
                window._add_discovered_camera(cam)
                self.app.processEvents()

            critical_mock.assert_called_once()
            args = critical_mock.call_args[0]
            self.assertEqual(args[1], "Erro ao Salvar Dispositivo")
            self.assertIn("somente leitura", args[2])

            log_entries = [window.list_logs.item(i).text() for i in range(window.list_logs.count())]
            self.assertTrue(any("somente leitura" in entry for entry in log_entries))
        finally:
            window.close()
            self.app.processEvents()
            drained = drain_retired_threads(5000)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(drained)

    def test_manual_discovery_dialog_two_channels_same_ip_port_coexist(self):
        """Dois canais cadastrados manualmente no mesmo IP/porta com caminhos RTSP distintos coexistem."""
        cfg = ConfigManager()
        with patch.object(discovery_dialog, "QMessageBox"):
            dlg = DiscoveryDialog()
            self.addCleanup(dlg.deleteLater)
            self.addCleanup(dlg.close)
            added = []
            dlg.device_added.connect(added.append)

            dlg.txt_man_ip.setText("192.168.1.10")
            dlg.spin_man_port.setValue(80)
            dlg.txt_man_name.setText("Canal 1")
            dlg.txt_man_rtsp_path.setText("/ch1")
            dlg._add_manual_device()

            dlg.txt_man_name.setText("Canal 2")
            dlg.txt_man_rtsp_path.setText("/ch2")
            dlg._add_manual_device()

        self.assertEqual(len(added), 2)
        self.assertNotEqual(added[0].id, added[1].id)
        for cam in added:
            self.assertTrue(cfg.add_or_update_device(cam.to_dict()))
        self.assertEqual(len(cfg.devices), 2)
        self.assertEqual({d["rtsp_path"] for d in cfg.devices}, {"/ch1", "/ch2"})


class SecureWriteJsonDescriptorTest(unittest.TestCase):
    """Garante ausência de double-close do descritor em _secure_write_json."""

    def test_no_double_close_when_exception_occurs_after_open_takes_ownership(self):
        """Após open(fd, ...) assumir o fd, erro em json.dump ou os.replace não chama os.close(fd)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "devices.json"
            with patch.object(cfg_mod.os, "close", wraps=os.close) as close_mock, \
                    patch.object(cfg_mod.json, "dump", side_effect=OSError("falha ao serializar")):
                with self.assertRaises(OSError):
                    _secure_write_json(target, [{"id": "1"}])
            close_mock.assert_not_called()

            # Também quando a exceção ocorre depois do bloco with (em os.replace)
            with patch.object(cfg_mod.os, "close", wraps=os.close) as close_mock, \
                    patch.object(cfg_mod.os, "replace", side_effect=OSError("falha no replace")):
                with self.assertRaises(OSError):
                    _secure_write_json(target, [{"id": "1"}])
            close_mock.assert_not_called()

    def test_closes_fd_once_if_open_fails_before_taking_ownership(self):
        """Se open(fd, ...) falhar antes de assumir posse, os.close(fd) é chamado exatamente uma vez."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "devices.json"
            with patch.object(cfg_mod.os, "close", wraps=os.close) as close_mock, \
                    patch("builtins.open", side_effect=OSError("open falhou")):
                with self.assertRaises(OSError):
                    _secure_write_json(target, [{"id": "1"}])
            close_mock.assert_called_once()


class ResolveGithubTokenReadOnlyTest(unittest.TestCase):
    """resolve_github_token lê keyring/settings.json sem instanciar ConfigManager."""

    def test_reads_from_keyring_without_instantiating_config_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False), \
                patch.object(cfg_mod, "ConfigManager", side_effect=AssertionError("ConfigManager não deve ser instanciado")), \
                patch.object(cfg_mod, "is_keyring_available", return_value=True), \
                patch.object(cfg_mod, "get_keyring_pat", return_value="ghp_from_keyring"), \
                patch.object(cfg_mod, "get_config_dir", return_value=Path(tmpdir)):
            self.assertEqual(resolve_github_token(), "ghp_from_keyring")

    def test_falls_back_to_settings_json_readonly_without_instantiating_config_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            settings_file = cfg_dir / "settings.json"
            settings_file.write_text(json.dumps({"github_token": "ghp_from_file"}), encoding="utf-8")

            with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False), \
                    patch.object(cfg_mod, "ConfigManager", side_effect=AssertionError("ConfigManager não deve ser instanciado")), \
                    patch.object(cfg_mod, "is_keyring_available", return_value=True), \
                    patch.object(cfg_mod, "get_keyring_pat", return_value=None), \
                    patch.object(cfg_mod, "get_config_dir", return_value=cfg_dir):
                self.assertEqual(resolve_github_token(), "ghp_from_file")

            # O arquivo não foi reescrito nem migrado em segundo plano
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(data["github_token"], "ghp_from_file")


if __name__ == "__main__":
    unittest.main()
