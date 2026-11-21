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

import central_nvr.core.config as cfg_mod
from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import ConfigManager, _secure_write_json

try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    import central_nvr.core.stream_worker as sw
    from central_nvr.core.threads import drain_retired_threads
    from central_nvr.core.updater import resolve_github_token
    from central_nvr.ui import discovery_dialog
    from central_nvr.ui.discovery_dialog import DiscoveryDialog
    from central_nvr.ui.main_window import MainWindow
    from central_nvr.ui.settings_dialog import SettingsDialog
    HAS_PYSIDE6 = True
except ImportError:
    QCoreApplication = None
    QEvent = None
    QApplication = None
    sw = None
    drain_retired_threads = None
    resolve_github_token = None
    discovery_dialog = None
    DiscoveryDialog = None
    MainWindow = None
    SettingsDialog = None
    HAS_PYSIDE6 = False


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

    def test_two_channels_same_ip_port_path_with_different_rtsp_ports_coexist(self):
        """Canais no mesmo IP/porta ONVIF e mesmo rtsp_path mas portas RTSP distintas coexistem."""
        cfg = ConfigManager()
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_10_80",
            "name": "RTSP 554",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_port": 554,
            "rtsp_path": "/live/ch0",
            "password": "p1",
        }))
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_10_80",
            "name": "RTSP 8554",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_port": 8554,
            "rtsp_path": "/live/ch0",
            "password": "p2",
        }))
        self.assertEqual(len(cfg.devices), 2)
        self.assertEqual({d["rtsp_port"] for d in cfg.devices}, {554, 8554})

    def test_updating_same_id_when_onvif_discovers_rtsp_url_updates_instead_of_duplicating(self):
        """Redescobrir via ONVIF uma câmera antes sem rtsp_url atualiza o registro em vez de duplicar."""
        cfg = ConfigManager()
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_10_80",
            "name": "Entrada",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_port": 554,
            "rtsp_path": "/live/ch0",
            "rtsp_url": "",
        }))
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_10_80",
            "name": "Entrada ONVIF",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_port": 554,
            "rtsp_path": "/Streaming/Channels/101",
            "rtsp_url": "rtsp://192.168.1.10:554/Streaming/Channels/101",
        }))
        self.assertEqual(len(cfg.devices), 1)
        self.assertEqual(cfg.devices[0]["name"], "Entrada ONVIF")
        self.assertEqual(cfg.devices[0]["rtsp_url"], "rtsp://192.168.1.10:554/Streaming/Channels/101")

    def test_id_collision_during_update_preserves_distinct_ids(self):
        """Atualizar canal B informando um ID já usado pelo canal A preserva ambos com IDs distintos."""
        cfg = ConfigManager()
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_a",
            "name": "Canal A",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/ch1",
        }))
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_b",
            "name": "Canal B",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/ch2",
        }))
        # Atualiza Canal B tentando reutilizar o ID de Canal A
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_a",
            "name": "Canal B Atualizado",
            "ip": "192.168.1.10",
            "port": 80,
            "rtsp_path": "/ch2",
        }))
        self.assertEqual(len(cfg.devices), 2)
        by_path = {d["rtsp_path"]: d for d in cfg.devices}
        self.assertEqual(by_path["/ch1"]["id"], "cam_a")
        self.assertEqual(by_path["/ch2"]["id"], "cam_b")
        self.assertEqual(by_path["/ch2"]["name"], "Canal B Atualizado")

    def test_updating_legacy_device_without_id_cleans_ip_keyring_entry(self):
        """Dispositivo legado sem id em devices.json limpa a senha camera_<ip> ao ganhar novo id."""
        (self.config_dir / "devices.json").write_text(
            json.dumps([{"name": "Legada", "ip": "192.168.1.77", "port": 80, "rtsp_path": "/live/ch0"}]),
            encoding="utf-8",
        )
        self.keyring_store["192.168.1.77"] = "senha-legada"
        cfg = ConfigManager()
        self.assertEqual(cfg.devices[0]["password"], "senha-legada")

        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_192_168_1_77_80",
            "name": "Legada Atualizada",
            "ip": "192.168.1.77",
            "port": 80,
            "rtsp_path": "/live/ch0",
            "password": "senha-nova",
        }))
        self.assertNotIn("192.168.1.77", self.keyring_store)
        self.assertEqual(self.keyring_store.get("cam_192_168_1_77_80"), "senha-nova")

    def test_remove_device_by_ip_cleans_keyring_for_all_matching_ids(self):
        """remove_device() por IP remove os canais daquele IP e apaga suas senhas por id no keyring."""
        cfg = ConfigManager()
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_ch1",
            "name": "Ch1",
            "ip": "192.168.1.88",
            "port": 80,
            "rtsp_path": "/ch1",
            "password": "s1",
        }))
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_ch2",
            "name": "Ch2",
            "ip": "192.168.1.88",
            "port": 80,
            "rtsp_path": "/ch2",
            "password": "s2",
        }))
        self.assertTrue(cfg.remove_device("192.168.1.88"))
        self.assertEqual(cfg.devices, [])
        self.assertNotIn("cam_ch1", self.keyring_store)
        self.assertNotIn("cam_ch2", self.keyring_store)

    def test_write_failure_rolls_back_memory_and_keyring_mutations(self):
        """Falha de gravação em devices.json/settings.json não altera memória nem destrói senhas no keyring."""
        cfg = ConfigManager()
        self.assertTrue(cfg.add_or_update_device({
            "id": "cam_orig",
            "name": "Original",
            "ip": "192.168.1.90",
            "port": 80,
            "rtsp_path": "/live/ch0",
            "password": "senha-original",
        }))

        with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("somente leitura")):
            # Falha ao trocar ID do canal: mantém ID e senha antigos no keyring e sem órfão do novo ID
            self.assertFalse(cfg.add_or_update_device({
                "id": "cam_novo_id",
                "name": "Novo Nome",
                "ip": "192.168.1.90",
                "port": 80,
                "rtsp_path": "/live/ch0",
                "password": "senha-nova",
            }))
            self.assertEqual(len(cfg.devices), 1)
            self.assertEqual(cfg.devices[0]["id"], "cam_orig")
            self.assertEqual(cfg.devices[0]["name"], "Original")
            self.assertEqual(self.keyring_store.get("cam_orig"), "senha-original")
            self.assertNotIn("cam_novo_id", self.keyring_store)

            # Falha em rename_device e update_device não muta self.devices
            self.assertFalse(cfg.rename_device("cam_orig", "Renomeada"))
            self.assertEqual(cfg.devices[0]["name"], "Original")
            self.assertFalse(cfg.update_device("cam_orig", {"port": 8080}))
            self.assertEqual(cfg.devices[0]["port"], 80)

            # Falha em remove_device preserva registro e senha no keyring
            self.assertFalse(cfg.remove_device("cam_orig"))
            self.assertEqual(len(cfg.devices), 1)
            self.assertEqual(self.keyring_store.get("cam_orig"), "senha-original")

            # Falha em set/update preserva settings em memória
            self.assertFalse(cfg.set("theme", "light"))
            self.assertEqual(cfg.get("theme"), "dark")
            self.assertFalse(cfg.update({"theme": "light", "buffer_size_ms": 500}))
            self.assertEqual(cfg.get("theme"), "dark")
            self.assertEqual(cfg.get("buffer_size_ms"), 150)

    def test_default_data_dirs_respect_patched_get_data_dir_and_keyring_writes_are_selective(self):
        """snapshot_dir/recordings_dir são resolvidos em __init__ e save_devices só grava senhas alteradas."""
        cfg = ConfigManager()
        self.assertEqual(cfg.get("snapshot_dir"), str(self.data_dir / "snapshots"))
        self.assertEqual(cfg.get("recordings_dir"), str(self.data_dir / "recordings"))

        with patch.object(cfg_mod, "set_keyring_password", wraps=cfg_mod.set_keyring_password) as set_pw_mock:
            self.assertTrue(cfg.add_or_update_device({
                "id": "cam_1",
                "name": "Cam 1",
                "ip": "192.168.1.11",
                "port": 80,
                "password": "pw1",
            }))
            # Gravou exatamente 1 vez (não 2x)
            self.assertEqual(set_pw_mock.call_count, 1)

            # Adicionar segunda câmera não regrava a senha inalterada da primeira
            set_pw_mock.reset_mock()
            self.assertTrue(cfg.add_or_update_device({
                "id": "cam_2",
                "name": "Cam 2",
                "ip": "192.168.1.12",
                "port": 80,
                "password": "pw2",
            }))
            self.assertEqual(set_pw_mock.call_count, 1)
            self.assertEqual(set_pw_mock.call_args[0], ("cam_2", "pw2"))


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 não disponível neste ambiente")
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

        self.assertTrue(cfg.add_or_update_device({"id": "cam_err", "name": "Existente", "ip": "192.0.2.1"}))

        with patch.object(cfg_mod, "_secure_write_json", side_effect=OSError("Disco cheio")):
            with self.assertLogs(cfg_mod.logger, level="ERROR") as logs:
                ok_add = cfg.add_or_update_device({"id": "cam_err_2", "name": "Falha", "ip": "192.0.2.2"})
                ok_ren = cfg.rename_device("cam_err", "Outro Nome")
                ok_upd_dev = cfg.update_device("cam_err", {"port": 8080})
                ok_rem = cfg.remove_device("cam_err")
        self.assertFalse(ok_add)
        self.assertFalse(ok_ren)
        self.assertFalse(ok_upd_dev)
        self.assertFalse(ok_rem)
        self.assertEqual(len(cfg.devices), 1)
        self.assertEqual(cfg.devices[0]["name"], "Existente")
        self.assertIn("Disco cheio", cfg.last_write_error or "")
        self.assertTrue(any("ERROR" in m and "dispositivos" in m for m in logs.output))

    def test_discovery_dialog_and_main_window_do_not_add_or_confirm_on_write_failure(self):
        """Falha de gravação via DiscoveryDialog exibe erro crítico, não exibe Sucesso e mantém window.cameras limpa."""
        cfg = ConfigManager()
        cfg.set("periodic_update_check", False)
        window = MainWindow(config_mgr=cfg)
        self.app.processEvents()
        dlg = None
        try:
            dlg = DiscoveryDialog(parent=window, on_device_added=window._add_discovered_camera)

            dlg.txt_man_ip.setText("192.0.2.50")
            dlg.spin_man_port.setValue(80)
            dlg.txt_man_name.setText("Garagem")
            dlg.txt_man_rtsp_path.setText("/live/ch0")

            with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("somente leitura")), \
                    patch("central_nvr.ui.main_window.QMessageBox.critical") as critical_mock, \
                    patch.object(discovery_dialog.QMessageBox, "information") as info_mock, \
                    patch.object(dlg, "accept") as accept_mock:
                dlg._add_manual_device()
                self.app.processEvents()

            critical_mock.assert_called_once()
            args = critical_mock.call_args[0]
            self.assertEqual(args[1], "Erro ao Salvar Dispositivo")
            self.assertIn("somente leitura", args[2])
            info_mock.assert_not_called()
            accept_mock.assert_not_called()
            self.assertEqual(window.cameras, [])
            self.assertEqual(cfg.devices, [])

            # Também no fluxo assíncrono _on_enrich_finished
            cam = CameraDevice(id="cam_enr", name="Quintal", ip="192.0.2.51", port=80)
            with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("somente leitura")), \
                    patch("central_nvr.ui.main_window.QMessageBox.critical") as critical_mock, \
                    patch.object(discovery_dialog.QMessageBox, "information") as info_mock, \
                    patch.object(discovery_dialog.QMessageBox, "warning") as warn_mock, \
                    patch.object(dlg, "accept") as accept_mock:
                dlg._on_enrich_finished([(cam, None)])
                self.app.processEvents()

            critical_mock.assert_called_once()
            info_mock.assert_not_called()
            warn_mock.assert_not_called()
            accept_mock.assert_not_called()
            self.assertEqual(window.cameras, [])

            log_entries = [window.list_logs.item(i).text() for i in range(window.list_logs.count())]
            self.assertTrue(any("somente leitura" in entry for entry in log_entries))
        finally:
            if dlg is not None:
                dlg.close()
                dlg.deleteLater()
            window.close()
            self.app.processEvents()
            drained = drain_retired_threads(5000)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(drained)

    def test_rename_remove_and_settings_dialog_propagate_write_errors(self):
        """Falha de gravação em renomear, remover e SettingsDialog mostra erro e preserva UI."""
        cfg = ConfigManager()
        cfg.set("periodic_update_check", False)
        cfg.add_or_update_device({
            "id": "cam_1",
            "name": "Portão",
            "ip": "192.0.2.60",
            "port": 80,
            "rtsp_url": "demo://",
        })
        window = MainWindow(config_mgr=cfg)
        self.app.processEvents()
        sdlg = None
        try:
            with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("disco travado")), \
                    patch("central_nvr.ui.main_window.QMessageBox.critical") as critical_mock:
                window._on_camera_renamed("cam_1", "Novo Nome")
            critical_mock.assert_called_once()
            self.assertEqual(window.cameras[0].name, "Portão")

            from PySide6.QtWidgets import QMessageBox as QtMessageBox
            with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("disco travado")), \
                    patch("central_nvr.ui.main_window.QMessageBox.question", return_value=QtMessageBox.StandardButton.Yes), \
                    patch("central_nvr.ui.main_window.QMessageBox.critical") as critical_mock:
                window._remove_camera_by_id("cam_1")
            critical_mock.assert_called_once()
            self.assertEqual(len(window.cameras), 1)

            sdlg = SettingsDialog(config_mgr=cfg, parent=window)
            with patch.object(cfg_mod, "_secure_write_json", side_effect=PermissionError("disco travado")), \
                    patch("central_nvr.ui.settings_dialog.QMessageBox.critical") as critical_mock, \
                    patch("central_nvr.ui.settings_dialog.QMessageBox.information") as info_mock, \
                    patch.object(sdlg, "accept") as accept_mock:
                sdlg._save_values()
            critical_mock.assert_called_once()
            info_mock.assert_not_called()
            accept_mock.assert_not_called()
        finally:
            if sdlg is not None:
                sdlg.close()
                sdlg.deleteLater()
            window.close()
            self.app.processEvents()
            drained = drain_retired_threads(5000)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(drained)

    def test_manual_discovery_dialog_two_channels_same_ip_port_coexist(self):
        """Dois canais cadastrados manualmente no mesmo IP/porta com paths ou portas RTSP distintos coexistem."""
        cfg = ConfigManager()
        with patch.object(discovery_dialog, "QMessageBox"):
            dlg = DiscoveryDialog()
            self.addCleanup(dlg.deleteLater)
            self.addCleanup(dlg.close)
            added = []
            dlg.device_added.connect(added.append)

            dlg.txt_man_ip.setText("192.168.1.10")
            dlg.spin_man_port.setValue(80)
            dlg.spin_man_rtsp_port.setValue(554)
            dlg.txt_man_name.setText("Canal 1")
            dlg.txt_man_rtsp_path.setText("/ch1")
            dlg._add_manual_device()

            dlg.txt_man_name.setText("Canal 2")
            dlg.txt_man_rtsp_path.setText("/ch2")
            dlg._add_manual_device()

            dlg.txt_man_name.setText("Canal 3 Porta 8554")
            dlg.spin_man_rtsp_port.setValue(8554)
            dlg.txt_man_rtsp_path.setText("/ch1")
            dlg._add_manual_device()

        self.assertEqual(len(added), 3)
        self.assertEqual(len({c.id for c in added}), 3)
        for cam in added:
            self.assertTrue(cfg.add_or_update_device(cam.to_dict()))
        self.assertEqual(len(cfg.devices), 3)


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


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 não disponível neste ambiente")
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
