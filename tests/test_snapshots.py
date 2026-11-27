#!/usr/bin/env python3
"""
Testes de geração de nomes únicos, fila e gravação segura de snapshots (Issue #67).
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from central_nvr.core.camera import CameraDevice
from central_nvr.core.snapshots import (
    next_snapshot_path,
    open_unique_snapshot,
    sanitize_snapshot_camera_id,
)
from central_nvr.core.stream_worker import StreamWorker
from central_nvr.ui.fullscreen_view import FullscreenPlayerWindow


class SnapshotNamingAndExclusivityTest(unittest.TestCase):
    """Critérios 1, 3 e 4 no helper compartilhado de snapshots."""

    def test_same_second_different_milliseconds_produce_distinct_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            t1 = datetime.datetime(2026, 11, 27, 15, 30, 45, 120_000)
            t2 = datetime.datetime(2026, 11, 27, 15, 30, 45, 450_000)
            p1 = next_snapshot_path(tmp, "cam-01", now=t1)
            p2 = next_snapshot_path(tmp, "cam-01", now=t2)
            self.assertNotEqual(p1, p2)
            self.assertEqual(p1.name, "snap_cam-01_20261127_153045_120.jpg")
            self.assertEqual(p2.name, "snap_cam-01_20261127_153045_450.jpg")

    def test_existing_file_is_never_overwritten_and_gets_incremental_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            t = datetime.datetime(2026, 11, 27, 15, 30, 45, 500_000)
            first = next_snapshot_path(base, "cam1", now=t)
            first.write_bytes(b"original-content")

            # next_snapshot_path avança para _1 quando já existe
            second = next_snapshot_path(base, "cam1", now=t)
            self.assertEqual(second.name, "snap_cam1_20261127_153045_500_1.jpg")

            # open_unique_snapshot nunca sobrescreve mesmo se receber o caminho já existente
            actual, fp = open_unique_snapshot(first)
            with fp:
                fp.write(b"new-content")
            self.assertEqual(actual.name, "snap_cam1_20261127_153045_500_1.jpg")
            self.assertEqual(first.read_bytes(), b"original-content")
            self.assertEqual(actual.read_bytes(), b"new-content")

    def test_camera_id_is_sanitized_with_snap_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            t = datetime.datetime(2026, 11, 27, 15, 30, 45, 10_000)
            path = next_snapshot_path(base, "../../../etc/passwd:1", now=t)
            self.assertEqual(path.parent, base)
            self.assertEqual(sanitize_snapshot_camera_id("../../../etc/passwd:1"), "_________etc_passwd_1")
            self.assertTrue(path.name.startswith("snap__________etc_passwd_1_"))
            self.assertTrue(path.name.endswith(".jpg"))


class StreamWorkerSnapshotQueueTest(unittest.TestCase):
    """Critérios 2 e 3: fila limitada no worker, sem perder pedidos nem sobrescrever arquivos."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _worker(self):
        return StreamWorker(
            CameraDevice(id="cam_q", name="Portaria", ip="192.0.2.40"),
            auto_reconnect=False,
            motion_detection=False,
        )

    def test_two_queued_snapshots_before_frame_create_two_files_even_with_same_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self._worker()
            saved_paths = []
            worker.snapshot_saved.connect(saved_paths.append, Qt.ConnectionType.DirectConnection)

            target = Path(tmp) / "snap_cam_q_20261127_153045_100.jpg"
            worker.take_snapshot(str(target))
            worker.take_snapshot(str(target))

            rgb = np.full((32, 48, 3), 120, dtype=np.uint8)
            worker._process_and_emit_frame(rgb, 48, 32, 0.0)

            self.assertEqual(len(saved_paths), 2)
            self.assertEqual(len(set(saved_paths)), 2)
            for p in saved_paths:
                fp = Path(p)
                self.assertTrue(fp.is_file())
                self.assertGreater(fp.stat().st_size, 0)
            self.assertEqual(
                {Path(p).name for p in saved_paths},
                {
                    "snap_cam_q_20261127_153045_100.jpg",
                    "snap_cam_q_20261127_153045_100_1.jpg",
                },
            )

    def test_worker_never_overwrites_preexisting_file_and_emits_actual_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self._worker()
            saved_paths = []
            worker.snapshot_saved.connect(saved_paths.append, Qt.ConnectionType.DirectConnection)

            existing = Path(tmp) / "snap_cam_q_20261127_153045_200.jpg"
            existing.write_bytes(b"arquivo-previo-intacto")

            worker.take_snapshot(str(existing))
            rgb = np.full((32, 48, 3), 200, dtype=np.uint8)
            worker._process_and_emit_frame(rgb, 48, 32, 0.0)

            self.assertEqual(len(saved_paths), 1)
            actual = Path(saved_paths[0])
            self.assertNotEqual(actual, existing)
            self.assertEqual(actual.name, "snap_cam_q_20261127_153045_200_1.jpg")
            self.assertEqual(existing.read_bytes(), b"arquivo-previo-intacto")
            self.assertGreater(actual.stat().st_size, 0)

    def test_snapshot_queue_respects_max_pending_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = self._worker()
            saved_paths = []
            worker.snapshot_saved.connect(saved_paths.append, Qt.ConnectionType.DirectConnection)

            limit = worker.MAX_PENDING_SNAPSHOTS
            for idx in range(limit + 5):
                worker.take_snapshot(str(Path(tmp) / f"snap_cam_q_{idx:03d}.jpg"))

            rgb = np.zeros((16, 16, 3), dtype=np.uint8)
            worker._process_and_emit_frame(rgb, 16, 16, 0.0)

            self.assertEqual(len(saved_paths), limit)
            self.assertEqual(len(list(Path(tmp).glob("*.jpg"))), limit)


class FullscreenSnapshotUniquenessTest(unittest.TestCase):
    """Critérios 1, 3 e 4 na janela em tela cheia (incluindo toast com nome real gravado)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_fullscreen_snapshots_in_same_second_save_distinct_files_and_report_real_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_dir = Path(tmp)
            cam = CameraDevice(id="../cam/fs", name="Entrada", ip="192.0.2.50")
            win = FullscreenPlayerWindow(camera=cam, snapshot_dir=snap_dir)
            self.addCleanup(lambda: (
                win.close(),
                win.deleteLater(),
                QCoreApplication.sendPostedEvents(win, QEvent.Type.DeferredDelete),
            ))

            frame = QImage(64, 48, QImage.Format.Format_RGB888)
            frame.fill(0x224466)
            win.set_frame(frame)

            frozen = datetime.datetime(2026, 11, 27, 15, 30, 45, 789_000)

            class FrozenDateTime(datetime.datetime):
                @classmethod
                def now(cls, tz=None):
                    return frozen

            with patch("central_nvr.core.snapshots.datetime.datetime", FrozenDateTime):
                win._take_snapshot()
                toast1 = win.lbl_toast.text()
                win._take_snapshot()
                toast2 = win.lbl_toast.text()

            files = sorted(p.name for p in snap_dir.glob("*.jpg"))
            self.assertEqual(
                files,
                [
                    "snap____cam_fs_20261127_153045_789.jpg",
                    "snap____cam_fs_20261127_153045_789_1.jpg",
                ],
            )
            self.assertIn(files[0], toast1)
            self.assertIn(files[1], toast2)
            for name in files:
                self.assertGreater((snap_dir / name).stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
