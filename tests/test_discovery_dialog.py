#!/usr/bin/env python3
"""
Testes do diálogo de descoberta (Issue #30, parte A): hosts achados só por porta vêm desmarcados,
IP manual validado e usuário padrão vindo da configuração `default_username`.
"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from central_nvr.ui import discovery_dialog
from central_nvr.ui.discovery_dialog import DiscoveryDialog


class DiscoveryDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.msg_patch = patch.object(discovery_dialog, "QMessageBox")
        self.msgbox = self.msg_patch.start()

    def tearDown(self):
        self.msg_patch.stop()
        self.app.processEvents()

    def _dialog(self, **kwargs):
        dialog = DiscoveryDialog(**kwargs)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_ws_discovery_rows_checked_port_scan_rows_unchecked(self):
        dialog = self._dialog()
        dialog._on_device_found({"ip": "10.0.0.5", "port": 80, "method": "ws-discovery", "source": "ONVIF"})
        dialog._on_device_found({"ip": "10.0.0.9", "port": 554, "method": "port-scan", "source": "Porta"})
        self.assertEqual(dialog.table.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertEqual(dialog.table.item(1, 0).checkState(), Qt.CheckState.Unchecked)

    def test_unknown_origin_is_not_checked(self):
        dialog = self._dialog()
        dialog._on_device_found({"ip": "10.0.0.7", "port": 80})
        self.assertEqual(dialog.table.item(0, 0).checkState(), Qt.CheckState.Unchecked)

    def test_invalid_manual_ip_is_rejected(self):
        dialog = self._dialog()
        added = []
        dialog.device_added.connect(added.append)
        for bad in ("192.168.1.300", "10.0.0.5/../x", "cam@evil", "1.2.3"):
            dialog.txt_man_ip.setText(bad)
            dialog._add_manual_device()
        self.assertEqual(added, [])
        self.assertEqual(self.msgbox.warning.call_count, 4)
        self.assertIn("inválido", self.msgbox.warning.call_args[0][2])

    def test_valid_manual_ip_is_added(self):
        dialog = self._dialog()
        added = []
        dialog.device_added.connect(added.append)
        dialog.txt_man_ip.setText(" 192.168.1.20 ")
        dialog._add_manual_device()
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].ip, "192.168.1.20")

    def test_user_field_uses_default_username(self):
        dialog = self._dialog(default_username="operador")
        self.assertEqual(dialog.txt_disc_user.text(), "operador")
        self.assertEqual(dialog.txt_man_user.text(), "operador")

    def test_user_field_falls_back_to_admin_when_unset(self):
        dialog = self._dialog(default_username="")
        self.assertEqual(dialog.txt_disc_user.text(), "admin")


if __name__ == "__main__":
    unittest.main()
