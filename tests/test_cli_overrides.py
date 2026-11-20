#!/usr/bin/env python3
"""
Testes de regressão da Issue #57:
Garante que as flags de CLI --hw-accel e --transport apliquem overrides apenas em memória
para a execução atual, sem gravar ou sobrescrever as preferências persistidas em settings.json.
"""
import argparse
import contextlib
import io
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

from central_nvr.app import apply_cli_overrides, parse_arguments
from central_nvr.core.config import ConfigManager

try:
    from PySide6.QtWidgets import QApplication
    from central_nvr.ui.settings_dialog import SettingsDialog
    HAS_PYSIDE6 = True
except ImportError:
    QApplication = None
    SettingsDialog = None
    HAS_PYSIDE6 = False


class CliOverridesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        self.settings_path = self.config_dir / "settings.json"

        self._patchers = [
            patch("central_nvr.core.config.get_config_dir", return_value=self.config_dir),
            patch("central_nvr.core.config.is_keyring_available", return_value=False),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def _write_persisted_settings(self, data: dict) -> str:
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        self.settings_path.write_text(raw, encoding="utf-8")
        return raw

    def test_overrides_update_in_memory_without_modifying_settings_file(self):
        initial_raw = self._write_persisted_settings(
            {
                "hw_accel": "vaapi",
                "rtsp_transport": "tcp",
                "theme": "dark",
            }
        )
        config_mgr = ConfigManager()
        args = argparse.Namespace(hw_accel="cpu", transport="udp")

        with patch.object(config_mgr, "set", wraps=config_mgr.set) as mock_set, \
                patch.object(config_mgr, "save_settings", wraps=config_mgr.save_settings) as mock_save:
            apply_cli_overrides(config_mgr, args)

        # Sessão atual enxerga os overrides em memória
        self.assertEqual(config_mgr.get("hw_accel"), "cpu")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")
        self.assertEqual(config_mgr.get("theme"), "dark")

        # Nenhuma gravação em disco ocorre
        mock_set.assert_not_called()
        mock_save.assert_not_called()
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), initial_raw)

        # Nova instância continua lendo os valores originalmente persistidos
        reloaded = ConfigManager()
        self.assertEqual(reloaded.get("hw_accel"), "vaapi")
        self.assertEqual(reloaded.get("rtsp_transport"), "tcp")

    def test_overrides_do_not_create_settings_file_when_absent(self):
        self.assertFalse(self.settings_path.exists())
        config_mgr = ConfigManager()
        args = argparse.Namespace(hw_accel="cuda", transport="udp")

        with patch.object(config_mgr, "save_settings", wraps=config_mgr.save_settings) as mock_save:
            apply_cli_overrides(config_mgr, args)

        self.assertEqual(config_mgr.get("hw_accel"), "cuda")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")
        mock_save.assert_not_called()
        self.assertFalse(self.settings_path.exists())

    def test_partial_override_preserves_other_persisted_setting(self):
        self._write_persisted_settings({"hw_accel": "cuda", "rtsp_transport": "udp"})
        config_mgr = ConfigManager()

        apply_cli_overrides(config_mgr, argparse.Namespace(hw_accel="cpu", transport=None))
        self.assertEqual(config_mgr.get("hw_accel"), "cpu")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")

        reloaded = ConfigManager()
        self.assertEqual(reloaded.get("hw_accel"), "cuda")
        self.assertEqual(reloaded.get("rtsp_transport"), "udp")

    def test_none_args_keep_existing_values_and_do_not_write(self):
        initial_raw = self._write_persisted_settings(
            {
                "hw_accel": "auto",
                "rtsp_transport": "udp",
            }
        )
        config_mgr = ConfigManager()
        args = argparse.Namespace(hw_accel=None, transport=None)

        with patch.object(config_mgr, "set", wraps=config_mgr.set) as mock_set, \
                patch.object(config_mgr, "save_settings", wraps=config_mgr.save_settings) as mock_save:
            apply_cli_overrides(config_mgr, args)

        self.assertEqual(config_mgr.get("hw_accel"), "auto")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")
        mock_set.assert_not_called()
        mock_save.assert_not_called()
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), initial_raw)

    def test_missing_attributes_on_namespace_are_ignored(self):
        config_mgr = ConfigManager()
        original_hw = config_mgr.get("hw_accel")
        original_transport = config_mgr.get("rtsp_transport")

        with patch.object(config_mgr, "save_settings", wraps=config_mgr.save_settings) as mock_save:
            apply_cli_overrides(config_mgr, argparse.Namespace())

        self.assertEqual(config_mgr.get("hw_accel"), original_hw)
        self.assertEqual(config_mgr.get("rtsp_transport"), original_transport)
        mock_save.assert_not_called()

    def test_parse_arguments_help_documents_in_memory_override(self):
        buf = io.StringIO()
        with patch.object(sys, "argv", ["central-nvr", "--help"]), \
                contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                parse_arguments()

        self.assertEqual(ctx.exception.code, 0)
        help_text = " ".join(buf.getvalue().split())
        self.assertIn("--hw-accel", help_text)
        self.assertIn("--transport", help_text)
        self.assertEqual(help_text.count("apenas nesta execução"), 2)
        self.assertEqual(help_text.count("sem sobrescrever as preferências salvas"), 2)

    def test_saving_unrelated_settings_after_cli_overrides_preserves_persisted_values(self):
        self._write_persisted_settings(
            {
                "hw_accel": "vaapi",
                "rtsp_transport": "tcp",
                "theme": "dark",
                "grid_layout": "auto",
            }
        )
        config_mgr = ConfigManager()
        apply_cli_overrides(config_mgr, argparse.Namespace(hw_accel="cpu", transport="udp"))

        # Gravar chaves não relacionadas durante a mesma sessão não pode vazar os overrides para o disco
        config_mgr.set("grid_layout", "2x2")
        config_mgr.update({"theme": "light", "buffer_size_ms": 300})

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["grid_layout"], "2x2")
        self.assertEqual(saved["theme"], "light")
        self.assertEqual(saved["buffer_size_ms"], 300)
        self.assertEqual(saved["hw_accel"], "vaapi")
        self.assertEqual(saved["rtsp_transport"], "tcp")

        # Sessão atual continua aplicando os overrides da CLI em memória
        self.assertEqual(config_mgr.get("hw_accel"), "cpu")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")
        self.assertEqual(config_mgr.get_persisted("hw_accel"), "vaapi")
        self.assertEqual(config_mgr.get_persisted("rtsp_transport"), "tcp")

        # Execução seguinte lê os valores originalmente persistidos
        reloaded = ConfigManager()
        self.assertEqual(reloaded.get("hw_accel"), "vaapi")
        self.assertEqual(reloaded.get("rtsp_transport"), "tcp")
        self.assertEqual(reloaded.get("grid_layout"), "2x2")
        self.assertEqual(reloaded.get("theme"), "light")


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 não disponível neste ambiente")
class SettingsDialogCliOverridesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        self.settings_path = self.config_dir / "settings.json"

        self._patchers = [
            patch("central_nvr.core.config.get_config_dir", return_value=self.config_dir),
            patch("central_nvr.core.config.is_keyring_available", return_value=False),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def _write_persisted_settings(self, data: dict) -> None:
        raw = json.dumps(data, indent=2, ensure_ascii=False)
        self.settings_path.write_text(raw, encoding="utf-8")

    def _dialog(self, config_mgr: ConfigManager) -> SettingsDialog:
        dialog = SettingsDialog(config_mgr=config_mgr)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_dialog_shows_persisted_values_warns_cli_override_and_preserves_on_unrelated_save(self):
        self._write_persisted_settings(
            {
                "hw_accel": "cuda",
                "rtsp_transport": "tcp",
                "buffer_size_ms": 150,
            }
        )
        config_mgr = ConfigManager()
        apply_cli_overrides(config_mgr, argparse.Namespace(hw_accel="cpu", transport="udp"))

        dialog = self._dialog(config_mgr)

        # Diálogo exibe as preferências persistidas e avisa sobre o override de CLI ativo
        self.assertEqual(dialog.combo_hw.currentData(), "cuda")
        self.assertEqual(dialog.combo_proto.currentData(), "tcp")
        self.assertFalse(dialog.lbl_cli_override_notice.isHidden())
        self.assertIn("--hw-accel cpu", dialog.lbl_cli_override_notice.text())
        self.assertIn("--transport udp", dialog.lbl_cli_override_notice.text())

        # Alterar apenas outra opção e salvar não vaza o override nem o desativa na sessão atual
        dialog.spin_buffer.setValue(250)
        with patch.object(config_mgr, "save_settings", wraps=config_mgr.save_settings) as mock_save, \
                patch("central_nvr.ui.settings_dialog.QMessageBox"):
            dialog._save_values()

        self.assertEqual(mock_save.call_count, 1)
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["buffer_size_ms"], 250)
        self.assertEqual(saved["hw_accel"], "cuda")
        self.assertEqual(saved["rtsp_transport"], "tcp")
        self.assertEqual(config_mgr.get("hw_accel"), "cpu")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")

    def test_dialog_with_hw_accel_auto_cli_override_does_not_overwrite_to_vaapi(self):
        self._write_persisted_settings(
            {
                "hw_accel": "cuda",
                "rtsp_transport": "tcp",
                "buffer_size_ms": 150,
            }
        )
        config_mgr = ConfigManager()
        apply_cli_overrides(config_mgr, argparse.Namespace(hw_accel="auto", transport=None))

        dialog = self._dialog(config_mgr)
        self.assertEqual(dialog.combo_hw.currentData(), "cuda")
        self.assertFalse(dialog.lbl_cli_override_notice.isHidden())
        self.assertIn("--hw-accel auto", dialog.lbl_cli_override_notice.text())

        dialog.spin_buffer.setValue(400)
        with patch("central_nvr.ui.settings_dialog.QMessageBox"):
            dialog._save_values()

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["buffer_size_ms"], 400)
        self.assertEqual(saved["hw_accel"], "cuda")
        self.assertEqual(config_mgr.get("hw_accel"), "auto")

    def test_dialog_with_persisted_hw_accel_auto_does_not_overwrite_to_vaapi(self):
        self._write_persisted_settings(
            {
                "hw_accel": "auto",
                "rtsp_transport": "tcp",
                "buffer_size_ms": 150,
            }
        )
        config_mgr = ConfigManager()
        apply_cli_overrides(config_mgr, argparse.Namespace(hw_accel="cpu", transport=None))

        dialog = self._dialog(config_mgr)
        dialog.spin_buffer.setValue(350)
        with patch("central_nvr.ui.settings_dialog.QMessageBox"):
            dialog._save_values()

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["buffer_size_ms"], 350)
        self.assertEqual(saved["hw_accel"], "auto")
        self.assertEqual(config_mgr.get("hw_accel"), "cpu")

    def test_dialog_explicit_change_clears_only_changed_cli_override(self):
        self._write_persisted_settings(
            {
                "hw_accel": "vaapi",
                "rtsp_transport": "tcp",
            }
        )
        config_mgr = ConfigManager()
        apply_cli_overrides(config_mgr, argparse.Namespace(hw_accel="cpu", transport="udp"))

        dialog = self._dialog(config_mgr)
        idx_cuda = dialog.combo_hw.findData("cuda")
        self.assertGreaterEqual(idx_cuda, 0)
        dialog.combo_hw.setCurrentIndex(idx_cuda)

        with patch("central_nvr.ui.settings_dialog.QMessageBox"):
            dialog._save_values()

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["hw_accel"], "cuda")
        self.assertEqual(saved["rtsp_transport"], "tcp")

        # Override limpo apenas para hw_accel (alterado explicitamente); --transport continua ativo
        self.assertFalse(config_mgr.has_override("hw_accel"))
        self.assertTrue(config_mgr.has_override("rtsp_transport"))
        self.assertEqual(config_mgr.get("hw_accel"), "cuda")
        self.assertEqual(config_mgr.get("rtsp_transport"), "udp")

    def test_dialog_hides_cli_override_notice_when_no_override_is_active(self):
        self._write_persisted_settings({"hw_accel": "vaapi", "rtsp_transport": "tcp"})
        config_mgr = ConfigManager()

        dialog = self._dialog(config_mgr)
        self.assertTrue(dialog.lbl_cli_override_notice.isHidden())


if __name__ == "__main__":
    unittest.main()
