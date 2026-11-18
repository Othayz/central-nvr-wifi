#!/usr/bin/env python3
"""
Testes da Issue #33: a imagem parada depois de uma queda não pode passar por "ao vivo".
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice, ConnectionState
from central_nvr.core.stream_worker import StreamWorker
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_view import CameraViewWidget


def _frame():
    img = QImage(64, 36, QImage.Format.Format_RGB888)
    img.fill(0)
    return img


class SignalLossTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patchers = [patch.object(sw.av, "open", side_effect=OSError("offline")),
                         patch.object(sw, "HAS_CV2", False)]
        for p in self.patchers:
            p.start()
        self.view = CameraViewWidget(CameraDevice(id="c1", name="Portão", ip="192.0.2.1"))
        self.states = []
        self.view.display_state_changed.connect(lambda cid, st: self.states.append(st))

    def tearDown(self):
        if getattr(self.view, "_fullscreen_win", None):
            self.view._fullscreen_win.close()
        self.view.stop()
        drain_retired_threads(5000)
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(self.view, QEvent.Type.DeferredDelete)
        for p in self.patchers:
            p.stop()

    def _go_live(self):
        self.view._on_state_changed(ConnectionState.STREAMING, "Ao Vivo")
        self.view._on_frame_received(_frame())

    def test_initial_label_is_not_live(self):
        view = CameraViewWidget(CameraDevice(id="c2", name="X", ip="192.0.2.2"))
        self.assertNotEqual(view.lbl_status.text(), "AO VIVO")
        view.stop()
        view.deleteLater()

    def test_error_after_frames_shows_warning_over_last_frame(self):
        self._go_live()
        self.assertEqual(self.view.signal_warning(), "")
        self.view._on_state_changed(ConnectionState.ERROR, "Câmera Offline")
        self.assertIsNotNone(self.view.current_frame)
        self.assertRegex(self.view.signal_warning(), r"^Sem sinal desde \d\d:\d\d:\d\d$")
        self.assertEqual(self.states[-1], "Sem sinal")

    def test_reconnecting_also_warns(self):
        self._go_live()
        self.view._on_state_changed(ConnectionState.RECONNECTING, "Reconectando")
        self.assertTrue(self.view.signal_warning())

    def test_warning_cleared_when_live_again(self):
        self._go_live()
        self.view._on_state_changed(ConnectionState.ERROR, "x")
        self._go_live()
        self.assertEqual(self.view.signal_warning(), "")
        self.assertEqual(self.states[-1], "Ao Vivo")

    def test_watchdog_flags_stalled_stream(self):
        self._go_live()
        last = self.view._last_frame_monotonic
        self.view._check_stall(now=last + 1)
        self.assertTrue(self.view.is_live())
        self.view._check_stall(now=last + self.view.STALL_TIMEOUT_SEC + 0.1)
        self.assertFalse(self.view.is_live())
        self.assertEqual(self.view.lbl_status.text(), "SEM SINAL")
        self.assertTrue(self.view.signal_warning())
        # Um frame novo volta ao vivo
        self.view._on_frame_received(_frame())
        self.assertTrue(self.view.is_live())

    def test_paused_camera_is_not_flagged(self):
        self._go_live()
        self.view._on_state_changed(ConnectionState.PAUSED, "Pausada")
        self.assertEqual(self.view.signal_warning(), "")
        self.assertEqual(self.states[-1], "Pausada")

    def test_fullscreen_stops_showing_live_when_stream_drops(self):
        self._go_live()
        self.view._open_fullscreen_player()
        win = self.view._fullscreen_win
        self.assertIn("AO VIVO", win.lbl_title.text())
        self.view._on_state_changed(ConnectionState.ERROR, "Câmera Offline")
        self.assertNotIn("AO VIVO", win.lbl_title.text())
        self.assertIn("SEM SINAL", win.lbl_title.text())


class LastFrameTimeTest(unittest.TestCase):
    def test_worker_updates_last_frame_time(self):
        worker = StreamWorker(camera=CameraDevice(id="c1", name="X", ip="192.0.2.1"))
        before = time.time()
        worker._process_and_emit_frame(np.zeros((36, 64, 3), dtype=np.uint8), 64, 36, 40.0)
        self.assertGreaterEqual(worker.stats.last_frame_time, before)


if __name__ == "__main__":
    unittest.main()
