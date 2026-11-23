#!/usr/bin/env python3
"""Teste E2E local do fluxo de atualização (checksum -> download -> instalação)."""
import gc
import hashlib
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from central_nvr.core.threads import drain_retired_threads
from central_nvr.core.updater import (
    AssetDownloadWorker,
    ChecksumFetchWorker,
    ReleaseAsset,
    ReleaseInfo,
    UpdateCheckWorker,
)
from central_nvr.ui.settings_dialog import SettingsDialog
from central_nvr.ui.update_dialog import StartupUpdateDialog, UpdateDialog

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

    def setUp(self):
        # Os workers (QThread) alocam objetos Python e podem disparar a coleta de lixo cíclica na
        # própria thread. Se ela recolher um diálogo de teste anterior, o destrutor C++ do widget
        # roda fora da thread principal e o processo cai com segfault. Por isso o lixo antigo é
        # coletado agora, na thread principal, e a coleta automática fica desligada enquanto
        # houver workers vivos (religada no tearDown).
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)
        self._dialogs = []

    def tearDown(self):
        for dlg in self._dialogs:
            self._dispose_dialog(dlg)
        self._dialogs.clear()
        gc.enable()
        gc.collect()  # na thread principal, com todos os workers já encerrados

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
        self._dialogs.append(dlg)  # descartado no tearDown, com os workers já encerrados
        return dlg, running, hits, tmp

    def _dispose_dialog(self, dlg):
        workers = [
            w for w in (dlg.checksum_worker, dlg.download_worker, dlg.check_worker)
            if w is not None
        ]
        dlg._cleanup_workers()
        for worker in workers:
            worker.wait(5000)
        drain_retired_threads(5000)
        dlg.close()
        dlg.deleteLater()
        self.app.processEvents()

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

        # Ao clicar no botão após o bloqueio, deve abrir a release no navegador e não entrar em loop
        with patch("central_nvr.ui.update_dialog.QDesktopServices.openUrl") as mock_open:
            dlg.btn_action.click()
            mock_open.assert_called_once()
            self.assertEqual(dlg.btn_action.text(), "Abrir página da release")

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

    def test_download_goes_to_private_updates_dir(self):
        """O pacote baixado fica no diretório privado (0700) do usuário, nunca em /tmp compartilhado (#15)."""
        sums = f"{hashlib.sha256(PAYLOAD).hexdigest()}  {APP_NAME}\n".encode()
        dlg, running, hits, tmp = self._run({APP_NAME: PAYLOAD, "sha256sums.txt": sums})
        dlg._on_action_clicked()
        self.assertTrue(_wait(self.app, lambda: dlg.downloaded_file_path is not None))

        updates_dir = Path(tmp.name) / "central-nvr" / "updates"
        self.assertEqual(Path(dlg.downloaded_file_path), updates_dir / APP_NAME)
        self.assertEqual(stat.S_IMODE(updates_dir.stat().st_mode), 0o700)

    def test_startup_missing_checksum_redirects_browser(self):
        base = "http://fake.test"
        assets = [ReleaseAsset(name=APP_NAME, size=len(PAYLOAD), download_url=f"{base}/{APP_NAME}")]
        rel = ReleaseInfo(tag_name="v9.9.9", version="9.9.9", title="v9.9.9", body="", html_url="https://github.com/test", published_at="", assets=assets, is_newer=True)

        with patch.object(StartupUpdateDialog, "_start_check", return_value=None):
            dlg = StartupUpdateDialog(repo="test/test")
            self._dialogs.append(dlg)
            dlg.release_info = rel
            dlg.best_asset = assets[0]

            with patch.object(QMessageBox, "critical", return_value=None), \
                 patch("central_nvr.ui.update_dialog.QDesktopServices.openUrl") as mock_open:
                dlg._on_checksum_missing()
                self.assertEqual(dlg.btn_update.text(), "Abrir página da release")
                mock_open.reset_mock()
                dlg.btn_update.click()
                mock_open.assert_called_once()

    def _make_release(self) -> ReleaseInfo:
        base = "http://fake.test"
        assets = [ReleaseAsset(name=APP_NAME, size=len(PAYLOAD), download_url=f"{base}/{APP_NAME}")]
        return ReleaseInfo(
            tag_name="v9.9.9",
            version="9.9.9",
            title="v9.9.9",
            body="",
            html_url="https://github.com/test/repo/releases/tag/v9.9.9",
            published_at="",
            assets=assets,
            is_newer=True,
        )

    def test_close_during_checksum_ready_does_not_start_download(self):
        """Fechar o diálogo com ChecksumFetchWorker ativo desconecta checksum_ready (Issue #83)."""
        started = threading.Event()
        release_gate = threading.Event()
        self.addCleanup(release_gate.set)

        def slow_checksum(*args, **kwargs):
            started.set()
            release_gate.wait(5)
            return "a" * 64

        dlg = UpdateDialog(release_info=self._make_release())
        self._dialogs.append(dlg)

        with patch("central_nvr.core.updater.resolve_checksum_or_none", side_effect=slow_checksum), \
             patch.object(dlg, "_begin_download") as mock_download:
            dlg._on_action_clicked()
            self.assertTrue(started.wait(5))
            worker = dlg.checksum_worker
            self.assertIsNotNone(worker)

            dlg.close()
            self.assertIsNone(dlg.checksum_worker)

            release_gate.set()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()

            mock_download.assert_not_called()
            self.assertIsNone(dlg.download_worker)
            self.assertIsNone(dlg.expected_sha256)

    def test_close_during_checksum_missing_does_not_alert_or_open_browser(self):
        """Fechar o diálogo com ChecksumFetchWorker ativo desconecta checksum_missing (Issue #83)."""
        started = threading.Event()
        release_gate = threading.Event()
        self.addCleanup(release_gate.set)

        def slow_missing_checksum(*args, **kwargs):
            started.set()
            release_gate.wait(5)
            return None

        dlg = UpdateDialog(release_info=self._make_release())
        self._dialogs.append(dlg)

        with patch("central_nvr.core.updater.resolve_checksum_or_none", side_effect=slow_missing_checksum), \
             patch.object(QMessageBox, "critical") as mock_critical, \
             patch("central_nvr.ui.update_dialog.QDesktopServices.openUrl") as mock_open:
            dlg._on_action_clicked()
            self.assertTrue(started.wait(5))
            worker = dlg.checksum_worker
            self.assertIsNotNone(worker)

            dlg.reject()
            self.assertIsNone(dlg.checksum_worker)

            release_gate.set()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()

            mock_critical.assert_not_called()
            mock_open.assert_not_called()

    def test_close_during_check_worker_disconnects_signals_and_unifies_attribute(self):
        """UpdateDialog usa self.check_worker e desconecta seus sinais ao fechar (Issue #83)."""
        started = threading.Event()
        release_gate = threading.Event()
        self.addCleanup(release_gate.set)
        rel = self._make_release()

        def slow_fetch(*args, **kwargs):
            started.set()
            release_gate.wait(5)
            return rel

        with patch("central_nvr.core.updater.fetch_latest_release", side_effect=slow_fetch), \
             patch.object(UpdateDialog, "_display_release") as mock_display, \
             patch.object(UpdateDialog, "_on_up_to_date") as mock_up_to_date, \
             patch.object(UpdateDialog, "_on_check_error") as mock_error:
            dlg = UpdateDialog(repo="o/r")
            self._dialogs.append(dlg)
            self.assertTrue(started.wait(5))
            self.assertIsNotNone(dlg.check_worker)
            self.assertFalse(hasattr(dlg, "worker"))
            worker = dlg.check_worker

            dlg.close()
            self.assertIsNone(dlg.check_worker)

            # Emite os demais sinais enquanto o worker ainda está vivo e libera o run()
            worker.no_update_available.emit(None)
            worker.check_failed.emit("falha tardia")
            release_gate.set()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()

            mock_display.assert_not_called()
            mock_up_to_date.assert_not_called()
            mock_error.assert_not_called()
            self.assertIsNone(dlg.release_info)

    def test_close_during_download_worker_disconnects_signals(self):
        """Fechar o diálogo com AssetDownloadWorker ativo desconecta todos os seus sinais (Issue #83)."""
        started = threading.Event()
        release_gate = threading.Event()
        self.addCleanup(release_gate.set)

        def slow_download(*args, **kwargs):
            started.set()
            release_gate.wait(5)
            return True

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        with patch.dict(os.environ, {"XDG_CACHE_HOME": tmp.name}), \
             patch("central_nvr.core.updater.download_asset", side_effect=slow_download), \
             patch.object(UpdateDialog, "_on_download_progress") as mock_progress, \
             patch.object(UpdateDialog, "_on_download_finished") as mock_finished, \
             patch.object(UpdateDialog, "_on_download_failed") as mock_failed:
            dlg = UpdateDialog(release_info=self._make_release())
            self._dialogs.append(dlg)
            dlg.expected_sha256 = "a" * 64
            dlg._begin_download()
            self.assertTrue(started.wait(5))
            worker = dlg.download_worker
            self.assertIsNotNone(worker)

            dlg.close()
            self.assertIsNone(dlg.download_worker)

            worker.progress.emit(10, 100, 50.0)
            worker.download_finished.emit("/caminho/falso.AppImage")
            worker.download_failed.emit("erro tardio")
            release_gate.set()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()

            mock_progress.assert_not_called()
            mock_finished.assert_not_called()
            mock_failed.assert_not_called()
            self.assertIsNone(dlg.downloaded_file_path)

    def test_accept_cleans_up_and_disconnects_workers(self):
        """accept() também executa _cleanup_workers() e desconecta sinais tardios (Issue #83)."""
        started_checksum = threading.Event()
        gate_checksum = threading.Event()
        self.addCleanup(gate_checksum.set)

        def slow_checksum(*args, **kwargs):
            started_checksum.set()
            gate_checksum.wait(5)
            return "a" * 64

        dlg = UpdateDialog(release_info=self._make_release())
        self._dialogs.append(dlg)

        with patch("central_nvr.core.updater.resolve_checksum_or_none", side_effect=slow_checksum), \
             patch.object(dlg, "_begin_download") as mock_download:
            dlg._on_action_clicked()
            self.assertTrue(started_checksum.wait(5))
            worker = dlg.checksum_worker
            self.assertIsNotNone(worker)

            dlg.accept()
            self.assertIsNone(dlg.checksum_worker)

            gate_checksum.set()
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()
            mock_download.assert_not_called()

        started_check = threading.Event()
        gate_check = threading.Event()
        self.addCleanup(gate_check.set)
        rel = self._make_release()

        def slow_fetch(*args, **kwargs):
            started_check.set()
            gate_check.wait(5)
            return rel

        with patch("central_nvr.core.updater.fetch_latest_release", side_effect=slow_fetch), \
             patch.object(StartupUpdateDialog, "_on_update_found") as mock_found:
            startup_dlg = StartupUpdateDialog(repo="o/r")
            self._dialogs.append(startup_dlg)
            self.assertTrue(started_check.wait(5))
            check_worker = startup_dlg.check_worker
            self.assertIsNotNone(check_worker)

            startup_dlg.accept()
            self.assertIsNone(startup_dlg.check_worker)

            gate_check.set()
            self.assertTrue(check_worker.wait(5000))
            self.app.processEvents()
            mock_found.assert_not_called()

    def test_settings_check_updates_now_sets_delete_on_close(self):
        """SettingsDialog._check_updates_now() aplica WA_DeleteOnClose no UpdateDialog (Issue #83)."""
        cfg = MagicMock()
        cfg.get.side_effect = lambda k, d=None: d
        del cfg.get_persisted
        del cfg.has_override
        del cfg.get_override

        settings_dlg = SettingsDialog(config_mgr=cfg)
        self.addCleanup(settings_dlg.deleteLater)

        opened = []

        def fake_exec(dialog_self):
            opened.append(dialog_self)
            return 0

        with patch.object(UpdateDialog, "_start_check", return_value=None), \
             patch.object(UpdateDialog, "exec", fake_exec):
            settings_dlg._check_updates_now()

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
        self._dialogs.append(opened[0])

    def _emit_from_worker_thread(self, signal, *args):
        """Emite sinal a partir de outra thread para enfileirar QMetaCallEvent na thread principal."""
        emitter = threading.Thread(target=signal.emit, args=args)
        emitter.start()
        emitter.join(timeout=5)
        self.assertFalse(emitter.is_alive())

    def test_queued_checksum_ready_before_close_does_not_start_download(self):
        """Evento checksum_ready já enfileirado antes de close() não inicia AssetDownloadWorker (Issue #83)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        dlg = UpdateDialog(release_info=self._make_release())
        self._dialogs.append(dlg)

        with patch.dict(os.environ, {"XDG_CACHE_HOME": tmp.name}), \
             patch.object(ChecksumFetchWorker, "start", return_value=None), \
             patch("central_nvr.ui.update_dialog.AssetDownloadWorker") as mock_dl_cls:
            mock_dl_cls.return_value.isRunning.return_value = False
            dlg._on_action_clicked()
            worker = dlg.checksum_worker
            self.assertIsNotNone(worker)

            # Enfileira o evento na thread principal sem processEvents() antes de fechar
            self._emit_from_worker_thread(worker.checksum_ready, "a" * 64)
            dlg.close()
            self.app.processEvents()

            mock_dl_cls.assert_not_called()
            self.assertIsNone(dlg.download_worker)
            self.assertIsNone(dlg.expected_sha256)

    def test_queued_checksum_missing_before_reject_does_not_alert_or_open_browser(self):
        """Evento checksum_missing já enfileirado antes de reject() não abre alerta nem navegador (Issue #83)."""
        dlg = UpdateDialog(release_info=self._make_release())
        self._dialogs.append(dlg)

        with patch.object(ChecksumFetchWorker, "start", return_value=None), \
             patch.object(QMessageBox, "critical") as mock_critical, \
             patch("central_nvr.ui.update_dialog.QDesktopServices.openUrl") as mock_open:
            dlg._on_action_clicked()
            worker = dlg.checksum_worker
            self.assertIsNotNone(worker)

            # Enfileira o evento na thread principal sem processEvents() antes de rejeitar
            self._emit_from_worker_thread(worker.checksum_missing)
            dlg.reject()
            self.app.processEvents()

            mock_critical.assert_not_called()
            mock_open.assert_not_called()

    def test_queued_download_finished_before_close_in_startup_dialog_does_not_prompt(self):
        """Evento download_finished já enfileirado antes de close() no StartupUpdateDialog não abre diálogo (Issue #83)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rel = self._make_release()

        with patch.dict(os.environ, {"XDG_CACHE_HOME": tmp.name}), \
             patch.object(StartupUpdateDialog, "_start_check", return_value=None), \
             patch.object(AssetDownloadWorker, "start", return_value=None), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as mock_question, \
             patch.object(QMessageBox, "critical") as mock_critical, \
             patch("central_nvr.ui.update_dialog.QDesktopServices.openUrl") as mock_open:
            dlg = StartupUpdateDialog(repo="test/test")
            self._dialogs.append(dlg)
            dlg.release_info = rel
            dlg.best_asset = rel.assets[0]
            dlg.expected_sha256 = "a" * 64
            dlg._begin_download()
            worker = dlg.download_worker
            self.assertIsNotNone(worker)

            # Enfileira o evento na thread principal sem processEvents() antes de fechar
            self._emit_from_worker_thread(worker.download_finished, "/caminho/falso.AppImage")
            dlg.close()
            self.app.processEvents()

            mock_question.assert_not_called()
            mock_critical.assert_not_called()
            mock_open.assert_not_called()
            self.assertIsNone(dlg.downloaded_file_path)

    def test_queued_worker_signals_after_close_are_ignored_across_dialogs(self):
        """Demais sinais de worker já enfileirados antes do fechamento são ignorados (Issue #83)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rel = self._make_release()

        with patch.dict(os.environ, {"XDG_CACHE_HOME": tmp.name}), \
             patch.object(UpdateCheckWorker, "start", return_value=None), \
             patch.object(AssetDownloadWorker, "start", return_value=None):
            update_dlg = UpdateDialog(repo="o/r")
            self._dialogs.append(update_dlg)
            check_worker = update_dlg.check_worker
            self.assertIsNotNone(check_worker)
            status_before = update_dlg.lbl_status.text()

            self._emit_from_worker_thread(check_worker.update_available, rel)
            self._emit_from_worker_thread(check_worker.no_update_available, rel)
            self._emit_from_worker_thread(check_worker.check_failed, "erro enfileirado")
            update_dlg.close()
            self.app.processEvents()

            self.assertIsNone(update_dlg.release_info)
            self.assertEqual(update_dlg.lbl_status.text(), status_before)

            startup_dlg = StartupUpdateDialog(repo="o/r")
            self._dialogs.append(startup_dlg)
            startup_check = startup_dlg.check_worker
            self.assertIsNotNone(startup_check)
            startup_status_before = startup_dlg.lbl_status.text()

            with patch("central_nvr.ui.update_dialog.QTimer.singleShot") as mock_timer:
                self._emit_from_worker_thread(startup_check.update_available, rel)
                self._emit_from_worker_thread(startup_check.no_update_available, rel)
                self._emit_from_worker_thread(startup_check.check_failed, "erro enfileirado")
                startup_dlg.reject()
                self.app.processEvents()

                mock_timer.assert_not_called()
                self.assertIsNone(startup_dlg.release_info)
                self.assertEqual(startup_dlg.lbl_status.text(), startup_status_before)

            dl_dlg = UpdateDialog(release_info=rel)
            self._dialogs.append(dl_dlg)
            dl_dlg.expected_sha256 = "a" * 64
            dl_dlg._begin_download()
            dl_worker = dl_dlg.download_worker
            self.assertIsNotNone(dl_worker)
            dl_status_before = dl_dlg.lbl_status.text()

            self._emit_from_worker_thread(dl_worker.progress, 50, 100, 10.0)
            self._emit_from_worker_thread(dl_worker.download_finished, "/caminho/falso.AppImage")
            self._emit_from_worker_thread(dl_worker.download_failed, "erro enfileirado")
            dl_dlg.close()
            self.app.processEvents()

            self.assertIsNone(dl_dlg.downloaded_file_path)
            self.assertEqual(dl_dlg.lbl_status.text(), dl_status_before)


if __name__ == "__main__":
    unittest.main()
