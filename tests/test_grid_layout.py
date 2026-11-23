#!/usr/bin/env python3
"""
Testes da grade: área ocupada após trocar de layout (Issue #24) e paginação (Issue #46).
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
    return [CameraDevice(id=f"c{i}", name=f"Cam {i}", ip=f"192.0.2.{i}") for i in range(1, n + 1)]


class GridTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patchers = [patch.object(sw.av, "open", side_effect=OSError("offline")),
                         patch.object(sw, "HAS_CV2", False)]
        for p in self.patchers:
            p.start()
        self.grid = CameraGridWidget()
        self.grid.resize(1200, 800)
        self.grid.show()

    def tearDown(self):
        self.grid.stop_all()
        self.assertTrue(drain_retired_threads(5000))
        self.grid.close()
        self.grid.deleteLater()
        QCoreApplication.sendPostedEvents(self.grid, QEvent.Type.DeferredDelete)
        for p in self.patchers:
            p.stop()

    def _settle(self):
        for _ in range(5):
            self.app.processEvents()
        self.grid.layout.activate()
        self.app.processEvents()

    def _visible_ids(self):
        return [cid for cid, v in self.grid.camera_views.items() if not v.isHidden()]


class StretchResetTest(GridTestBase):
    def test_1x1_after_four_cameras_fills_grid(self):
        """#24: 4 câmeras → 1x1 → a view ocupa toda a área da grade."""
        self.grid.set_cameras(_cams(4))
        self._settle()
        self.grid.set_layout_mode("1x1")
        self._settle()
        view = self.grid.camera_views[self._visible_ids()[0]]
        self.assertGreater(view.width(), 1100)
        self.assertGreater(view.height(), 700)

    def test_nine_to_two_cameras_share_full_area(self):
        """#24: 9 → 2 câmeras → as duas views dividem 100% da área."""
        self.grid.set_cameras(_cams(9))
        self._settle()
        self.grid.set_cameras(_cams(2))
        self._settle()
        views = [self.grid.camera_views[cid] for cid in self._visible_ids()]
        self.assertEqual(len(views), 2)
        for v in views:
            self.assertGreater(v.width(), 1100)
        self.assertGreater(sum(v.height() for v in views), 700)

    def test_fixed_3x3_mode_with_few_cameras_keeps_grid(self):
        self.grid.set_cameras(_cams(2))
        self.grid.set_layout_mode("3x3")
        self._settle()
        views = [self.grid.camera_views[cid] for cid in self._visible_ids()]
        self.assertEqual(len(views), 2)
        for v in views:
            self.assertLess(v.width(), 450)  # uma das 3 colunas


class PaginationTest(GridTestBase):
    def test_all_cameras_reachable_through_pages(self):
        self.grid.set_cameras(_cams(12))
        self.grid.set_layout_mode("3x3")
        self.assertEqual(self.grid.page_count(), 2)
        self.assertFalse(self.grid.page_bar.isHidden())
        self.assertEqual(self.grid.lbl_page.text(), "Página 1/2")
        self.assertEqual(self._visible_ids(), [f"c{i}" for i in range(1, 10)])

        self.grid.next_page()
        self.assertEqual(self._visible_ids(), ["c10", "c11", "c12"])
        self.assertEqual(self.grid.lbl_page.text(), "Página 2/2")
        self.assertFalse(self.grid.btn_next_page.isEnabled())

    def test_changing_page_pauses_leaving_and_resumes_entering(self):
        self.grid.set_cameras(_cams(12))
        self.grid.set_layout_mode("3x3")
        views = self.grid.camera_views
        self.assertFalse(views["c1"].worker.is_paused)
        self.assertTrue(views["c10"].worker.is_paused)
        self.grid.next_page()
        self.assertTrue(views["c1"].worker.is_paused)
        self.assertFalse(views["c10"].worker.is_paused)

    def test_layout_change_recalculates_pages(self):
        self.grid.set_cameras(_cams(12))
        self.grid.set_layout_mode("3x3")
        self.assertEqual(self.grid.page_count(), 2)
        self.grid.set_layout_mode("2x2")
        self.assertEqual(self.grid.page_count(), 3)
        self.assertEqual(self.grid.lbl_page.text(), "Página 1/3")

    def test_auto_mode_paginates_above_nine(self):
        self.grid.set_cameras(_cams(10))
        self.assertEqual(self.grid.page_count(), 2)
        self.grid.next_page()
        self.assertEqual(self._visible_ids(), ["c10"])

    def test_page_bar_hidden_with_single_page(self):
        self.grid.set_cameras(_cams(4))
        self.assertTrue(self.grid.page_bar.isHidden())

    def test_1x1_pages_step_through_cameras(self):
        self.grid.set_cameras(_cams(3))
        self.grid.set_layout_mode("1x1")
        self.assertEqual(self._visible_ids(), ["c1"])
        self.grid.next_page()
        self.assertEqual(self._visible_ids(), ["c2"])
        self.assertEqual(self.grid.selected_camera_id, "c2")

    def test_removing_cameras_clamps_page(self):
        self.grid.set_cameras(_cams(12))
        self.grid.set_layout_mode("3x3")
        self.grid.next_page()
        self.grid.set_cameras(_cams(5))
        self.assertEqual(self.grid.current_page, 0)
        self.assertEqual(len(self._visible_ids()), 5)

    def test_select_camera_on_other_page_switches_page_and_syncs_state(self):
        """#81: seleção programática de câmera em outra página muda de página sem emitir camera_selected."""
        self.grid.set_cameras(_cams(5))
        self.grid.set_layout_mode("2x2")
        views = self.grid.camera_views
        emitted = []
        self.grid.camera_selected.connect(emitted.append)

        # Câmera na mesma página mantém a página atual
        self.grid.select_camera("c2")
        self.assertEqual(self.grid.current_page, 0)
        self.assertTrue(views["c2"].is_selected)
        self.assertFalse(views["c1"].is_selected)
        self.assertEqual(self.grid.lbl_page.text(), "Página 1/2")
        self.assertEqual(emitted, [])

        # Câmera na página 1 muda de página, exibe/retoma c5 e oculta/pausa c1..c4
        self.grid.select_camera("c5")
        self.assertEqual(self.grid.current_page, 1)
        self.assertEqual(self._visible_ids(), ["c5"])
        self.assertFalse(views["c5"].isHidden())
        self.assertTrue(views["c5"].is_selected)
        self.assertTrue(views["c1"].isHidden())
        self.assertFalse(views["c2"].is_selected)
        self.assertTrue(views["c1"].worker.is_paused)
        self.assertFalse(views["c5"].worker.is_paused)
        self.assertEqual(self.grid.lbl_page.text(), "Página 2/2")
        self.assertTrue(self.grid.btn_prev_page.isEnabled())
        self.assertFalse(self.grid.btn_next_page.isEnabled())
        self.assertEqual(emitted, [])

        # Voltar para câmera da página 0 restaura a página 0
        self.grid.select_camera("c1")
        self.assertEqual(self.grid.current_page, 0)
        self.assertEqual(self._visible_ids(), ["c1", "c2", "c3", "c4"])
        self.assertTrue(views["c1"].is_selected)
        self.assertFalse(views["c5"].is_selected)
        self.assertFalse(views["c1"].worker.is_paused)
        self.assertTrue(views["c5"].worker.is_paused)
        self.assertEqual(self.grid.lbl_page.text(), "Página 1/2")
        self.assertEqual(emitted, [])

    def test_select_camera_in_1x1_keeps_existing_behavior(self):
        """#81: em modo 1x1, select_camera mantém a troca direta de câmera sem emitir camera_selected."""
        self.grid.set_cameras(_cams(5))
        self.grid.set_layout_mode("1x1")
        views = self.grid.camera_views
        emitted = []
        self.grid.camera_selected.connect(emitted.append)

        self.grid.select_camera("c4")
        self.assertEqual(self._visible_ids(), ["c4"])
        self.assertTrue(views["c4"].is_selected)
        self.assertFalse(views["c1"].is_selected)
        self.assertEqual(self.grid.lbl_page.text(), "Página 4/5")
        self.assertEqual(emitted, [])


if __name__ == "__main__":
    unittest.main()
