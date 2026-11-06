#!/usr/bin/env python3
"""Teste E2E local do fluxo de atualização (checksum -> download -> instalação)."""
import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication, QMessageBox

from central_nvr.core.updater import ReleaseAsset, ReleaseInfo
from central_nvr.ui.update_dialog import UpdateDialog

APP_NAME = "Central_NVR_WiFi-9.9.9-x86_64.AppImage"
PAYLOAD = b"#!/bin/sh\necho new-version\n" * 200


class _FakeResponse:
    def __init__(self, data):
        self.status_code = 200 if data is not None else 404
        self._data = data or b""
        self.headers = {"content-length": str(len(self._data))}
        self.text = self._data.decode("utf-8", "ignore")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._data), 4096):
            yield self._data[i:i + 4096]


def _make_server(files):
    """Simula a rede em memória (sem sockets): registra os caminhos requisitados."""
    hits = []

    def fake_get(url, *args, **kwargs):
        path = url.split("//", 1)[-1].split("/", 1)[-1]
        hits.append("/" + path)
        return _FakeResponse(files.get(path))

    p = patch("central_nvr.core.updater.requests.get", side_effect=fake_get)
    p.start()
    return p, hits


def _wait(app, cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return cond()


class UpdateFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _run(self, files, asset_size=len(PAYLOAD)):
        srv, hits = _make_server(files)
        self.addCleanup(srv.stop)
        base = "http://fake.test"
        assets = [ReleaseAsset(name=APP_NAME, size=asset_size, download_url=f"{base}/{APP_NAME}")]
        if "sha256sums.txt" in files:
            assets.append(ReleaseAsset(name="sha256sums.txt", size=1, download_url=f"{base}/sha256sums.txt"))
        rel = ReleaseInfo(tag_name="v9.9.9", version="9.9.9", title="v9.9.9", body="", html_url="", published_at="", assets=assets, is_newer=True)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        running = Path(tmp.name) / "running.AppImage"
        running.write_text("old")
        patches = [
            patch.dict(os.environ, {"APPIMAGE": str(running), "XDG_CACHE_HOME": tmp.name}),
            patch.object(QMessageBox, "critical", return_value=None),
            patch.object(QMessageBox, "information", return_value=None),
            patch("central_nvr.ui.update_dialog.QDesktopServices.openUrl"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        dlg = UpdateDialog(release_info=rel)
        return dlg, running, hits, tmp

    def test_valid_release_installs_with_backup(self):
        sums = f"{hashlib.sha256(PAYLOAD).hexdigest()}  {APP_NAME}\n".encode()
        dlg, running, hits, tmp = self._run({APP_NAME: PAYLOAD, "sha256sums.txt": sums})
        dlg._on_action_clicked()
        self.assertTrue(_wait(self.app, lambda: dlg.downloaded_file_path is not None))
        self.assertEqual(hits[0], "/sha256sums.txt")  # checksum antes do download
        dlg._install_package()
        self.assertEqual(running.read_bytes(), PAYLOAD)
        self.assertEqual((Path(tmp.name) / "running.AppImage.bak").read_text(), "old")

    def test_missing_checksum_blocks_before_download(self):
        dlg, running, hits, _ = self._run({APP_NAME: PAYLOAD})
        dlg._on_action_clicked()
        self.assertTrue(_wait(self.app, lambda: dlg.btn_action.isEnabled()))
        self.assertNotIn(f"/{APP_NAME}", hits)
        self.assertIsNone(dlg.downloaded_file_path)
        self.assertEqual(running.read_text(), "old")

    def test_hash_mismatch_rejected(self):
        sums = f"{'0' * 64}  {APP_NAME}\n".encode()
        dlg, running, hits, _ = self._run({APP_NAME: PAYLOAD, "sha256sums.txt": sums})
        dlg._on_action_clicked()
        self.assertTrue(_wait(self.app, lambda: dlg.downloaded_file_path is not None))
        dlg._install_package()
        self.assertEqual(running.read_text(), "old")

    def test_size_mismatch_discards_download(self):
        sums = f"{hashlib.sha256(PAYLOAD).hexdigest()}  {APP_NAME}\n".encode()
        dlg, running, hits, tmp = self._run({APP_NAME: PAYLOAD, "sha256sums.txt": sums}, asset_size=1)
        dlg._on_action_clicked()
        self.assertTrue(_wait(self.app, lambda: dlg.btn_action.text() == "Tentar Novamente"))
        self.assertIsNone(dlg.downloaded_file_path)
        leftovers = [f for _, _, fs in os.walk(tmp.name) for f in fs if f.endswith((".part", ".AppImage")) and f != "running.AppImage"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
