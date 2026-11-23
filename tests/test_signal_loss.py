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
from central_nvr.core.stream_worker import StreamOutcome, StreamWorker
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

    def test_motion_alert_hidden_when_leaving_streaming(self):
        """Issue #77: sair de STREAMING deve esconder lbl_motion e remover a borda vermelha."""
        non_streaming = (
            ConnectionState.ERROR,
            ConnectionState.RECONNECTING,
            ConnectionState.DISCONNECTED,
            ConnectionState.PAUSED,
            ConnectionState.CONNECTING,
        )
        for state in non_streaming:
            with self.subTest(state=state):
                self._go_live()
                self.view._on_motion_detected("c1", True)
                self.assertFalse(self.view.lbl_motion.isHidden())
                self.assertIn("#EF4444", self.view.styleSheet())

                self.view._on_state_changed(state, "Fora do ar")
                self.assertTrue(self.view.lbl_motion.isHidden())
                self.assertNotIn("#EF4444", self.view.styleSheet())

                # Sinal atrasado de movimento fora de STREAMING não reacende o alerta
                self.view._on_motion_detected("c1", True)
                self.assertTrue(self.view.lbl_motion.isHidden())
                self.assertNotIn("#EF4444", self.view.styleSheet())

    def test_motion_alert_hidden_on_watchdog_stall(self):
        """Issue #77: perda de sinal detectada pelo watchdog também limpa o alerta de movimento."""
        self._go_live()
        self.view._on_motion_detected("c1", True)
        self.assertFalse(self.view.lbl_motion.isHidden())
        self.assertIn("#EF4444", self.view.styleSheet())

        last = self.view._last_frame_monotonic
        self.view._check_stall(now=last + self.view.STALL_TIMEOUT_SEC + 0.1)
        self.assertTrue(self.view.lbl_motion.isHidden())
        self.assertNotIn("#EF4444", self.view.styleSheet())


class LastFrameTimeTest(unittest.TestCase):
    def test_worker_updates_last_frame_time(self):
        worker = StreamWorker(camera=CameraDevice(id="c1", name="X", ip="192.0.2.1"))
        before = time.time()
        worker._process_and_emit_frame(np.zeros((36, 64, 3), dtype=np.uint8), 64, 36, 40.0)
        self.assertGreaterEqual(worker.stats.last_frame_time, before)


def _trigger_motion(worker: StreamWorker, width: int = 64, height: int = 36):
    """Alimenta frames distintos em _evaluate_motion até disparar detecção de movimento."""
    dark = np.zeros((height, width, 3), dtype=np.uint8)
    bright = np.full((height, width, 3), 255, dtype=np.uint8)
    for frame in (dark, dark, dark, bright, bright, bright):
        worker._evaluate_motion(frame, width, height)


class MotionCleanupAndMemoryTest(unittest.TestCase):
    def _worker(self, **kwargs) -> StreamWorker:
        return StreamWorker(
            camera=CameraDevice(id="c1", name="Portão", ip="192.0.2.1"),
            motion_detection=True,
            **kwargs,
        )

    def test_stream_drop_emits_motion_false_once_and_clears_state(self):
        """Issue #77: queda do stream com movimento ativo desliga o alerta uma vez e zera flags."""
        worker = self._worker(auto_reconnect=False)
        events = []
        worker.motion_detected.connect(lambda cid, active: events.append((cid, active)))

        def connect_then_drop():
            _trigger_motion(worker)
            self.assertTrue(worker._motion_active)
            self.assertGreater(worker._motion_cooldown, 0)
            self.assertIsNotNone(worker._prev_sample_matrix)
            return StreamOutcome.ENDED

        with patch.object(worker, "_connect_once", side_effect=connect_then_drop):
            worker.run()

        self.assertEqual(events, [("c1", True), ("c1", False)])
        self.assertFalse(worker._motion_active)
        self.assertEqual(worker._motion_cooldown, 0)
        self.assertIsNone(worker._prev_sample_matrix)
        self.assertFalse(worker.stats.motion_detected)

    def test_stream_stop_emits_motion_false_once_and_clears_state(self):
        """Issue #77: encerramento do stream (STOPPED) também desliga movimento ativo."""
        worker = self._worker(auto_reconnect=True)
        events = []
        worker.motion_detected.connect(lambda cid, active: events.append((cid, active)))

        def connect_then_stop():
            _trigger_motion(worker)
            worker.request_stop()
            return StreamOutcome.STOPPED

        with patch.object(worker, "_connect_once", side_effect=connect_then_stop):
            worker.run()

        self.assertEqual(events, [("c1", True), ("c1", False)])
        self.assertFalse(worker._motion_active)
        self.assertEqual(worker._motion_cooldown, 0)
        self.assertIsNone(worker._prev_sample_matrix)

    def test_reset_stats_clears_motion_and_sample_buffer(self):
        """Issues #77 e #84: _reset_stats emite motion_detected(False) uma vez e libera _prev_sample_matrix."""
        worker = self._worker()
        events = []
        stats_snapshots = []
        worker.motion_detected.connect(lambda cid, active: events.append((cid, active)))
        worker.stats_updated.connect(stats_snapshots.append)

        _trigger_motion(worker)
        self.assertTrue(worker._motion_active)
        self.assertIsNotNone(worker._prev_sample_matrix)

        worker._reset_stats()
        worker._reset_stats()  # segunda chamada não deve reemitir False

        self.assertEqual(events, [("c1", True), ("c1", False)])
        self.assertFalse(worker._motion_active)
        self.assertEqual(worker._motion_cooldown, 0)
        self.assertIsNone(worker._prev_sample_matrix)
        self.assertFalse(stats_snapshots[-1].motion_detected)

    def test_prev_sample_matrix_does_not_retain_full_frame_and_detects_motion(self):
        """Issue #84: _prev_sample_matrix é cópia int16 desvinculada do frame RGB e detecta movimento real."""
        worker = self._worker()
        events = []
        worker.motion_detected.connect(lambda cid, active: events.append((cid, active)))

        frame_a = np.zeros((360, 640, 3), dtype=np.uint8)
        for _ in range(3):
            worker._evaluate_motion(frame_a, 640, 360)

        sample_a = worker._prev_sample_matrix
        self.assertIsNotNone(sample_a)
        self.assertEqual(sample_a.dtype, np.int16)
        self.assertIsNone(sample_a.base)
        self.assertFalse(np.shares_memory(frame_a, sample_a))

        # Mutar frame_a não pode alterar a amostra armazenada
        frame_a[:] = 200
        self.assertTrue(np.all(sample_a == 0))

        # Diferença real no frame seguinte deve disparar detecção de movimento
        frame_b = np.full((360, 640, 3), 255, dtype=np.uint8)
        for _ in range(3):
            worker._evaluate_motion(frame_b, 640, 360)

        self.assertEqual(events, [("c1", True)])
        sample_b = worker._prev_sample_matrix
        self.assertIsNotNone(sample_b)
        self.assertEqual(sample_b.dtype, np.int16)
        self.assertIsNone(sample_b.base)
        self.assertFalse(np.shares_memory(frame_b, sample_b))

        worker._reset_stats()
        self.assertIsNone(worker._prev_sample_matrix)


if __name__ == "__main__":
    unittest.main()
