#!/usr/bin/env python3
"""
Testes do perfil de stream automático (Issue #47): sub-stream nas miniaturas, principal no 1x1 e
na tela cheia; a escolha manual HD/SD vale até a próxima troca de layout.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_grid import CameraGridWidget


def _cams(n):
    return [CameraDevice(id=f"c{i}", name=f"Cam {i}", ip=f"192.0.2.{i}", rtsp_path="/onvif1",
                         manufacturer="Yoosee") for i in range(1, n + 1)]


class AutoProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patchers = [patch.object(sw.av, "open", side_effect=OSError("offline")),
                         patch.object(sw, "HAS_CV2", False)]
        for p in self.patchers:
            p.start()
        self.grid = CameraGridWidget()

    def tearDown(self):
        for v in self.grid.camera_views.values():
            if getattr(v, "_fullscreen_win", None):
                v._fullscreen_win.close()
        self.grid.stop_all()
        self.assertTrue(drain_retired_threads(5000))
        self.grid.deleteLater()
        QCoreApplication.sendPostedEvents(self.grid, QEvent.Type.DeferredDelete)
        for p in self.patchers:
            p.stop()

    def _prefs(self):
        return {cid: v.worker.prefer_substream for cid, v in self.grid.camera_views.items() if v.worker}

    def test_thumbnails_use_substream(self):
        self.grid.set_cameras(_cams(4))
        self.grid.set_layout_mode("2x2")
        self.assertTrue(all(self._prefs().values()))
        self.grid.set_layout_mode("3x3")
        self.assertTrue(all(self._prefs().values()))

    def test_single_view_uses_main_stream(self):
        self.grid.set_cameras(_cams(4))
        self.grid.set_layout_mode("1x1")
        selected = self.grid.camera_views[self.grid.selected_camera_id]
        self.assertFalse(selected.worker.prefer_substream)

        self.grid.set_cameras(_cams(1))
        self.grid.set_layout_mode("auto")
        self.assertFalse(self._prefs()["c1"])

    def test_fullscreen_uses_main_then_restores(self):
        self.grid.set_cameras(_cams(4))
        self.grid.set_layout_mode("2x2")
        view = self.grid.camera_views["c1"]
        view._open_fullscreen_player()
        self.assertFalse(view.worker.prefer_substream)
        view._fullscreen_win.close()
        self.assertTrue(view.worker.prefer_substream)

    def test_manual_choice_prevails_until_layout_change(self):
        self.grid.set_cameras(_cams(4))
        self.grid.set_layout_mode("2x2")
        view = self.grid.camera_views["c1"]
        view._toggle_stream_profile()  # operador força HD
        self.assertFalse(view.worker.prefer_substream)

        self.grid.select_camera("c2")  # nada muda de layout
        self.grid.apply_layout("2x2")
        self.assertFalse(view.worker.prefer_substream)

        self.grid.set_layout_mode("3x3")  # troca de layout reaplica o automático
        self.assertTrue(view.worker.prefer_substream)

    def test_new_worker_starts_with_grid_profile(self):
        self.grid.set_cameras(_cams(4))
        self.grid.set_layout_mode("2x2")
        view = self.grid.camera_views["c3"]
        view.reconnect()  # a nova sessão só abre quando a anterior encerra
        import time
        deadline = time.monotonic() + 5
        while view.worker is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(view.worker.prefer_substream)


if __name__ == "__main__":
    unittest.main()
