#!/usr/bin/env python3
"""
Testes da Issue #37: a abertura não espera o GitHub, a checagem periódica não abre diálogo modal,
workers são liberados, o log tem limite e /releases/latest usa ETag.
"""
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from central_nvr.core import updater
from central_nvr.core.updater import ReleaseInfo

STARTUP_SCRIPT = textwrap.dedent("""
    import sys, time
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from central_nvr import app
    from central_nvr.core import updater
    from central_nvr.ui.main_window import MainWindow

    started = time.monotonic()

    def github_unreachable(*args, **kwargs):
        time.sleep(8)  # rede sem saída que não rejeita a conexão
        raise RuntimeError("timeout")

    original_show = MainWindow.show

    def show_and_close(window):
        # Impresso aqui: a thread presa na "rede" faz o processo terminar com os._exit
        print("SHOWN_AFTER", time.monotonic() - started, flush=True)
        original_show(window)
        QTimer.singleShot(100, window.close)

    sys.argv = ["central-nvr"]
    with patch.object(updater, "fetch_latest_release", github_unreachable), \\
         patch.object(MainWindow, "show", show_and_close), \\
         patch.object(app, "SHUTDOWN_THREAD_TIMEOUT_MS", 200):
        app.main()
""")


class StartupTest(unittest.TestCase):
    def test_main_window_opens_without_waiting_for_github(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(SRC), HOME=home,
                       XDG_CONFIG_HOME=os.path.join(home, ".config"), XDG_DATA_HOME=os.path.join(home, ".local/share"))
            proc = subprocess.run([sys.executable, "-c", STARTUP_SCRIPT], env=env, capture_output=True,
                                  text=True, timeout=60)
        line = next((l for l in proc.stdout.splitlines() if l.startswith("SHOWN_AFTER")), "")
        self.assertTrue(line, proc.stdout + proc.stderr)
        self.assertLess(float(line.split()[1]), 5.0, "a janela esperou a verificação de atualização")


def _release(version="9.9.9"):
    return ReleaseInfo(tag_name=f"v{version}", version=version, title="", body="", html_url="",
                       published_at="", is_newer=True)


class MainWindowUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from central_nvr.core.config import ConfigManager
        from central_nvr.ui.main_window import MainWindow
        self.cfg = MagicMock(spec=ConfigManager)
        defaults = dict(ConfigManager.DEFAULT_SETTINGS, periodic_update_check=False)
        self.cfg.get.side_effect = lambda k, d=None: defaults.get(k, d)
        self.cfg.devices = []
        with patch("central_nvr.ui.main_window.ConfigManager", return_value=self.cfg):
            self.window = MainWindow(config_mgr=self.cfg)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_update_found_does_not_open_modal(self):
        with patch("central_nvr.ui.main_window.UpdateDialog") as dialog:
            self.window._on_background_update_found(_release())
        dialog.assert_not_called()
        self.assertFalse(self.window.btn_sb_update.isHidden())
        self.assertIn("9.9.9", self.window.btn_sb_update.text())

    def test_update_threads_do_not_accumulate(self):
        with patch.object(updater, "fetch_latest_release", return_value=None):
            for _ in range(5):
                self.window._run_background_update_check()
                deadline = time.monotonic() + 5
                while self.window._periodic_worker is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.app.processEvents()
                from PySide6.QtCore import QCoreApplication, QEvent
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        workers = [t for t in self.window.findChildren(QThread) if type(t).__name__ == "UpdateCheckWorker"]
        self.assertEqual(workers, [])

    def test_event_log_is_bounded(self):
        self.assertGreaterEqual(self.window.MAX_LOG_ITEMS, 100)
        self.window.MAX_LOG_ITEMS = 20  # limite pequeno só para o teste ser rápido
        for i in range(70):
            self.window._log_event("Teste", f"evento {i}")
        self.assertEqual(self.window.list_logs.count(), 20)
        self.assertIn("evento 69", self.window.list_logs.item(19).text())

    def test_minimum_periodic_interval_is_one_hour(self):
        defaults = {"periodic_update_check": True, "periodic_update_interval_min": 2}
        self.cfg.get.side_effect = lambda k, d=None: defaults.get(k, d)
        self.window._reconfigure_periodic_update_checker()
        self.assertEqual(self.window.update_periodic_timer.interval(), 60 * 60 * 1000)


class ETagTest(unittest.TestCase):
    def setUp(self):
        updater._ETAG_CACHE.clear()

    def test_second_request_is_conditional_and_304_uses_cache(self):
        first = MagicMock(status_code=200, headers={"ETag": '"abc"'})
        first.json.return_value = {"tag_name": "v1.2.3", "assets": []}
        not_modified = MagicMock(status_code=304, headers={})
        with patch("requests.get", side_effect=[first, not_modified]) as get:
            a = updater.fetch_latest_release(repo="o/r", token="t")
            b = updater.fetch_latest_release(repo="o/r", token="t")
        self.assertEqual(get.call_args_list[1][1]["headers"]["If-None-Match"], '"abc"')
        self.assertEqual(a.version, b.version)


if __name__ == "__main__":
    unittest.main()
