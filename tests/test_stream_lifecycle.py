#!/usr/bin/env python3
"""
Testes do ciclo de vida das threads de streaming (#21): remover, adicionar ou reconfigurar câmeras
com um stream travado na rede não pode abortar o processo nem congelar a interface.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as stream_worker
from central_nvr.core.camera import CameraDevice
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_grid import CameraGridWidget

# Roda o aplicativo real (central_nvr.app.main) com uma câmera cadastrada que nunca responde:
# a abertura do stream fica bloqueada, como em um host fora do ar. O operador remove a câmera
# e depois fecha a janela.
APP_SESSION_SCRIPT = textwrap.dedent("""
    import os, sys, time
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    import central_nvr.core.stream_worker as stream_worker
    from central_nvr import app
    from central_nvr.ui.main_window import MainWindow

    def unresponsive_camera(*args, **kwargs):
        time.sleep(3.0)
        raise OSError("timeout")

    if os.environ.get("TEST_SHUTDOWN_TIMEOUT_MS"):
        app.SHUTDOWN_THREAD_TIMEOUT_MS = int(os.environ["TEST_SHUTDOWN_TIMEOUT_MS"])

    original_show = MainWindow.show

    def show_then_operate(window):
        original_show(window)
        QTimer.singleShot(200, lambda: window._remove_camera_by_id("cam_offline"))
        QTimer.singleShot(600, window.close)

    sys.argv = ["central-nvr", "--no-update-check"]
    with patch.object(stream_worker.av, "open", unresponsive_camera), \\
         patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \\
         patch.object(MainWindow, "show", show_then_operate):
        exit_code = app.main()
    print("SESSION_FINISHED", flush=True)
    sys.exit(exit_code)
""")


class RemoveUnresponsiveCameraTest(unittest.TestCase):
    def _run_app_session(self, **extra_env):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config" / "central-nvr"
            config_dir.mkdir(parents=True)
            (config_dir / "devices.json").write_text(json.dumps([
                {"id": "cam_offline", "name": "Portaria", "ip": "192.0.2.10", "rtsp_path": "/onvif1"},
            ]))
            env = dict(os.environ)
            env.update({
                "HOME": tmp,
                "XDG_CONFIG_HOME": str(Path(tmp) / "config"),
                "XDG_DATA_HOME": str(Path(tmp) / "data"),
                "XDG_CACHE_HOME": str(Path(tmp) / "cache"),
                "PYTHONPATH": str(SRC_DIR),
                "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
                "QT_QPA_PLATFORM": "offscreen",
            })
            env.update(extra_env)
            return subprocess.run(
                [sys.executable, "-c", APP_SESSION_SCRIPT],
                env=env, capture_output=True, text=True, timeout=60,
            )

    def test_removing_camera_with_blocked_stream_does_not_abort_the_app(self):
        proc = self._run_app_session()

        self.assertNotIn("Destroyed while thread", proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("SESSION_FINISHED", proc.stdout)

    def test_app_exits_cleanly_even_if_a_stream_never_unblocks_in_time(self):
        """Stream ainda preso na rede quando o limite de espera do encerramento se esgota."""
        proc = self._run_app_session(TEST_SHUTDOWN_TIMEOUT_MS="200")

        self.assertNotIn("Destroyed while thread", proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])


def _demo_camera(cam_id: str) -> CameraDevice:
    """Câmera com sinal de demonstração local (não usa rede). Cada chamada cria um objeto novo,
    como acontece quando a janela principal recarrega os dispositivos da configuração."""
    return CameraDevice(id=cam_id, name=f"Câmera {cam_id}", ip="192.0.2.1", rtsp_url="demo://")


class CameraGridStreamLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.grid = CameraGridWidget()
        self.addCleanup(self._dispose_grid)

    def _dispose_grid(self):
        self.grid.stop_all()
        self.assertTrue(drain_retired_threads(10000), "threads de streaming não terminaram")
        # Destruir o grid agora: sem event loop, deleteLater() ficaria pendente até o encerramento
        # do interpretador, quando a ordem de destruição dos objetos Qt é arbitrária.
        grid, self.grid = self.grid, None
        grid.deleteLater()
        QCoreApplication.sendPostedEvents(grid, QEvent.Type.DeferredDelete)

    def test_removing_camera_with_blocked_stream_does_not_freeze_the_interface(self):
        stream_is_blocked = threading.Event()
        unblock = threading.Event()

        def unresponsive_camera(*args, **kwargs):
            stream_is_blocked.set()
            unblock.wait(10)
            raise OSError("timeout")

        self.addCleanup(unblock.set)
        with patch.object(stream_worker.av, "open", unresponsive_camera):
            self.grid.set_cameras([CameraDevice(id="offline", name="Portaria", ip="192.0.2.10")])
            self.assertTrue(stream_is_blocked.wait(5))

            started = time.monotonic()
            self.grid.set_cameras([])
            elapsed = time.monotonic() - started

            unblock.set()
            self.assertTrue(drain_retired_threads(10000))

        self.assertLess(elapsed, 0.5)

    def test_adding_or_removing_a_camera_keeps_other_streams_running(self):
        self.grid.set_cameras([_demo_camera("a"), _demo_camera("b")])
        stream_a = self.grid.camera_views["a"].worker

        self.grid.set_cameras([_demo_camera("a")])  # "b" removida
        self.assertEqual(list(self.grid.camera_views), ["a"])
        self.assertIs(self.grid.camera_views["a"].worker, stream_a)

        self.grid.set_cameras([_demo_camera("a"), _demo_camera("c")])  # "c" adicionada
        self.assertEqual(list(self.grid.camera_views), ["a", "c"])
        self.assertIs(self.grid.camera_views["a"].worker, stream_a)
        self.assertTrue(stream_a.isRunning())

    def test_reconfigured_camera_gets_a_new_stream(self):
        self.grid.set_cameras([_demo_camera("a"), _demo_camera("b")])
        stream_a = self.grid.camera_views["a"].worker
        stream_b = self.grid.camera_views["b"].worker

        with_new_password = _demo_camera("a")
        with_new_password.password = "nova-senha"
        self.grid.set_cameras([with_new_password, _demo_camera("b")])

        self.assertIsNot(self.grid.camera_views["a"].worker, stream_a)
        self.assertEqual(self.grid.camera_views["a"].worker.camera.password, "nova-senha")
        self.assertIs(self.grid.camera_views["b"].worker, stream_b)

    def test_changing_the_transport_setting_reconnects_the_streams(self):
        self.grid.set_cameras([_demo_camera("a")])
        stream_a = self.grid.camera_views["a"].worker

        self.grid.rtsp_transport = "udp"  # como faz a janela principal ao salvar as Configurações
        self.grid.set_cameras([_demo_camera("a")])

        self.assertIsNot(self.grid.camera_views["a"].worker, stream_a)
        self.assertEqual(self.grid.camera_views["a"].worker.rtsp_transport, "udp")


if __name__ == "__main__":
    unittest.main()
