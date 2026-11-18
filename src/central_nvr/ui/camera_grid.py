"""
Grid Responsivo Multi-Câmeras para visualização em mosaico dinâmico e inteligente.
Garante preenchimento de 100% da área útil sem desperdício de espaço ou slots vazios desnecessários:
- 1 Câmera: Ocupa 100% da tela.
- 2 Câmeras: Uma em cima e outra embaixo (2 linhas x 1 coluna) ocupando 100% da largura e altura.
- 3 Câmeras: Distribuídas proporcionalmente sem slots vazios.
- 4 Câmeras: Grid 2x2.
- 5 ou 6 Câmeras: Grid 2x3.
- 7 a 9 Câmeras: Grid 3x3.

Com mais câmeras do que cabem no layout, a grade é paginada (◀ ▶): cada página tem o tamanho do
layout atual (1x1 → 1, 2x2 → 4, 3x3 e automático → 9). Câmeras de outras páginas ficam pausadas.
"""
import math
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import DEFAULT_RTSP_TRANSPORT, CameraDevice
from central_nvr.ui.camera_view import CameraViewWidget, ViewOptions


class EmptySlotWidget(QFrame):
    """
    Slot exibido apenas quando não há nenhuma câmera conectada.
    """

    add_clicked = Signal()

    def __init__(self, slot_number: int = 1, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.slot_number = slot_number
        self.setObjectName("emptySlotContainer")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(200, 140)

        self.setStyleSheet("""
            QFrame#emptySlotContainer {
                background-color: #080D18;
                border: 2px dashed #24324D;
                border-radius: 8px;
            }
            QFrame#emptySlotContainer:hover {
                border-color: #38BDF8;
                background-color: #0C1424;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        layout.addStretch()

        # Ícone e Mensagem Central
        lbl_msg = QLabel("📷 <b>Nenhuma Câmera Conectada</b>")
        lbl_msg.setStyleSheet("color: #94A3B8; font-size: 14px; font-weight: 600;")
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_msg)

        lbl_sub = QLabel("Clique abaixo para adicionar sua primeira câmera IP ou buscar na rede local")
        lbl_sub.setStyleSheet("color: #64748B; font-size: 11px;")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_sub)

        layout.addSpacing(10)

        # Botão Central de Adição
        btn_add = QPushButton("+ Adicionar / Buscar Câmeras")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: 1px solid #38BDF8;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: 700;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
        """)
        btn_add.clicked.connect(self.add_clicked.emit)
        layout.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.add_clicked.emit()
        super().mouseDoubleClickEvent(event)


class CameraGridWidget(QWidget):
    """
    Container gerenciador de visualizações em mosaico dinâmico (Grid).
    Adapta a disposição de acordo com a quantidade exata de câmeras conectadas,
    garantindo que 100% da área útil seja aproveitada sem slots pretos residuais.
    """

    camera_selected = Signal(str)  # camera_id
    ptz_requested = Signal(str)  # camera_id
    snapshot_taken = Signal(str, str)  # (camera_id, filepath)
    add_camera_requested = Signal()
    layout_mode_changed = Signal(str)  # "auto", "1x1", "2x2", "3x3"
    fullscreen_requested = Signal(str)  # camera_id
    camera_renamed = Signal(str, str)  # (camera_id, new_name)
    camera_display_state_changed = Signal(str, str)  # (camera_id, "Ao Vivo" / "Sem sinal" / ...)

    def __init__(
        self,
        hw_accel: str = "vaapi",
        rtsp_transport: str = DEFAULT_RTSP_TRANSPORT,
        hidden_timeout_min: float = 5.0,
        view_options: Optional[ViewOptions] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.hw_accel = hw_accel
        self.rtsp_transport = rtsp_transport
        self.hidden_timeout_min = hidden_timeout_min
        self.view_options = view_options or ViewOptions()
        
        self.grid_layout_mode = "auto"  # "auto", "1x1", "2x2", "3x3"
        self.previous_grid_mode = "auto"
        self.selected_camera_id: Optional[str] = None
        
        self.camera_views: Dict[str, CameraViewWidget] = {}
        self.camera_devices: List[CameraDevice] = []
        self.empty_slots: List[EmptySlotWidget] = []
        self._filler_cells: List[QFrame] = []
        self.current_page = 0
        self._last_profile_key: Optional[tuple] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._grid_host = QWidget(self)
        self.layout = QGridLayout(self._grid_host)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(6)
        outer.addWidget(self._grid_host, 1)

        # Barra de paginação (Issue #46): só aparece quando há mais de uma página
        self.page_bar = QWidget(self)
        page_layout = QHBoxLayout(self.page_bar)
        page_layout.setContentsMargins(4, 0, 4, 4)
        page_layout.addStretch()
        self.btn_prev_page = QPushButton("◀")
        self.btn_prev_page.setFixedWidth(36)
        self.btn_prev_page.setToolTip("Página anterior")
        self.btn_prev_page.clicked.connect(self.previous_page)
        page_layout.addWidget(self.btn_prev_page)
        self.lbl_page = QLabel("")
        self.lbl_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page.setMinimumWidth(110)
        page_layout.addWidget(self.lbl_page)
        self.btn_next_page = QPushButton("▶")
        self.btn_next_page.setFixedWidth(36)
        self.btn_next_page.setToolTip("Próxima página")
        self.btn_next_page.clicked.connect(self.next_page)
        page_layout.addWidget(self.btn_next_page)
        page_layout.addStretch()
        self.page_bar.hide()
        outer.addWidget(self.page_bar)

    def set_cameras(self, cameras: List[CameraDevice]):
        """
        Atualiza a lista de câmeras e reconstrói o grid.

        Views de câmeras que não mudaram são mantidas com o stream em execução; só as removidas
        (ou reconfiguradas) são encerradas e só as novas são conectadas.
        """
        previous_views = self.camera_views
        self.camera_views = {}

        for cam in cameras:
            if cam.id in self.camera_views:
                continue
            view = previous_views.get(cam.id)
            if view is not None and self._can_keep_stream(view, cam):
                del previous_views[cam.id]
                if view.camera.name != cam.name:
                    view.update_camera_name(cam.name)
                if view.options != self.view_options:
                    view.apply_options(self.view_options)
            else:
                view = self._create_view(cam)
            self.camera_views[cam.id] = view

        for view in previous_views.values():
            self._dispose_view(view)

        self.camera_devices = cameras

        # Manter a seleção atual se a câmera continuar cadastrada; senão, selecionar a primeira
        if self.selected_camera_id not in self.camera_views:
            self.selected_camera_id = cameras[0].id if cameras else None
        if self.selected_camera_id:
            self.select_camera(self.selected_camera_id)

        self.apply_layout(self.grid_layout_mode)

    def _can_keep_stream(self, view: CameraViewWidget, camera: CameraDevice) -> bool:
        """Indica se o stream aberto pela view continua válido para esta câmera e para as opções do grid."""
        return (
            view.camera.stream_settings() == camera.stream_settings()
            and view.hw_accel == self.hw_accel
            and view.rtsp_transport == self.rtsp_transport
            and view.options.buffer_size_ms == self.view_options.buffer_size_ms
        )

    def _create_view(self, camera: CameraDevice) -> CameraViewWidget:
        view = CameraViewWidget(
            camera=camera,
            hw_accel=self.hw_accel,
            rtsp_transport=self.rtsp_transport,
            hidden_timeout_min=self.hidden_timeout_min,
            options=self.view_options,
            parent=self,
        )
        view.selected.connect(self._on_camera_selected)
        view.double_clicked.connect(self._on_camera_double_clicked)
        view.ptz_requested.connect(lambda cid: self.ptz_requested.emit(cid))
        view.snapshot_taken.connect(lambda cid, fp: self.snapshot_taken.emit(cid, fp))
        view.fullscreen_requested.connect(lambda cid: self.fullscreen_requested.emit(cid))
        view.rename_requested.connect(lambda cid, name: self.camera_renamed.emit(cid, name))
        view.display_state_changed.connect(self.camera_display_state_changed.emit)
        return view

    def display_state_of(self, camera_id: str) -> str:
        view = self.camera_views.get(camera_id)
        return view.display_state if view else "Conectando"

    def _dispose_view(self, view: CameraViewWidget):
        """Encerra o stream e descarta a view de forma segura."""
        try:
            view.selected.disconnect()
            view.double_clicked.disconnect()
            view.ptz_requested.disconnect()
            view.snapshot_taken.disconnect()
            view.fullscreen_requested.disconnect()
            view.rename_requested.disconnect()
            view.display_state_changed.disconnect()
        except Exception:
            pass
        view.stop()
        view.close()
        # Tirar do layout mantendo um pai: assim apply_layout() não a transforma em janela
        # órfã com destruição pendente, e ela é destruída com o grid se o event loop não rodar antes.
        self.layout.removeWidget(view)
        view.deleteLater()

    def set_layout_mode(self, mode: str):
        """Altera a disposição das câmeras (auto, 1x1, 2x2, 3x3)."""
        if mode != "1x1":
            self.previous_grid_mode = mode
        self.grid_layout_mode = mode
        self.apply_layout(mode)

    # ------------------------------------------------------------------
    # Paginação (Issue #46)
    # ------------------------------------------------------------------

    @staticmethod
    def page_capacity(mode: str) -> int:
        return {"1x1": 1, "2x2": 4, "3x3": 9}.get(mode, 9)

    def page_count(self) -> int:
        return max(1, math.ceil(len(self.camera_views) / self.page_capacity(self.grid_layout_mode)))

    def set_page(self, page: int):
        if self.grid_layout_mode == "1x1":
            # Em 1x1 cada página é uma câmera: navegar troca a câmera selecionada
            ids = list(self.camera_views)
            if not ids:
                return
            page = max(0, min(page, len(ids) - 1))
            self._on_camera_selected(ids[page])
            self.apply_layout("1x1")
            return
        self.current_page = max(0, min(page, self.page_count() - 1))
        self.apply_layout(self.grid_layout_mode)

    def next_page(self):
        self.set_page(self._displayed_page() + 1)

    def previous_page(self):
        self.set_page(self._displayed_page() - 1)

    def _displayed_page(self) -> int:
        if self.grid_layout_mode == "1x1":
            ids = list(self.camera_views)
            return ids.index(self.selected_camera_id) if self.selected_camera_id in ids else 0
        return self.current_page

    def _update_page_bar(self):
        pages = self.page_count()
        current = self._displayed_page()
        self.page_bar.setVisible(pages > 1)
        self.lbl_page.setText(f"Página {current + 1}/{pages}")
        self.btn_prev_page.setEnabled(current > 0)
        self.btn_next_page.setEnabled(current < pages - 1)

    # ------------------------------------------------------------------
    # Disposição
    # ------------------------------------------------------------------

    def apply_layout(self, mode: str = "auto"):
        """Reposiciona as views e pausa as que ficaram fora da tela (Issue #25)."""
        self._apply_layout_impl(mode)
        self._update_page_bar()
        self._sync_on_screen()
        self._apply_stream_profiles(mode)

    def _apply_stream_profiles(self, mode: str):
        """
        Sub-stream nas miniaturas (mais de uma câmera na tela) e stream principal com uma só
        (Issue #47). Uma troca de layout reaplica o automático por cima da escolha manual.
        """
        visible = [v for v in self.camera_views.values() if not v.isHidden()]
        prefer_sub = len(visible) > 1
        profile_key = (mode, len(visible))
        layout_changed = profile_key != self._last_profile_key
        self._last_profile_key = profile_key
        for view in self.camera_views.values():
            view.apply_auto_stream_profile(prefer_sub, layout_changed)

    def _sync_on_screen(self):
        for view in self.camera_views.values():
            view.set_on_screen(not view.isHidden())

    def set_view_options(self, options: ViewOptions):
        """Novas opções de Configurações; o buffer só vale após set_cameras() reabrir os streams."""
        self.view_options = options

    def set_hidden_timeout(self, minutes: float):
        self.hidden_timeout_min = minutes
        for view in self.camera_views.values():
            view.set_hidden_timeout(minutes)

    def _reset_grid(self):
        """
        Esvazia o QGridLayout e zera o stretch de todas as linhas e colunas (Issue #24): uma linha
        ou coluna sem widget mas com stretch continuaria recebendo espaço.
        """
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        for r in range(self.layout.rowCount()):
            self.layout.setRowStretch(r, 0)
        for c in range(self.layout.columnCount()):
            self.layout.setColumnStretch(c, 0)

        for slot in self.empty_slots:
            slot.deleteLater()
        self.empty_slots.clear()
        for cell in self._filler_cells:
            cell.deleteLater()
        self._filler_cells.clear()

        for v in self.camera_views.values():
            v.hide()

    def _place(self, widget: QWidget, row: int, col: int, row_span: int = 1, col_span: int = 1):
        self.layout.addWidget(widget, row, col, row_span, col_span)
        widget.show()

    def _stretch(self, rows: int, cols: int):
        for r in range(rows):
            self.layout.setRowStretch(r, 1)
        for c in range(cols):
            self.layout.setColumnStretch(c, 1)

    def _show_empty_slot(self):
        self._stretch(1, 1)
        slot = EmptySlotWidget(1, parent=self._grid_host)
        slot.add_clicked.connect(self.add_camera_requested.emit)
        self.empty_slots.append(slot)
        self._place(slot, 0, 0)

    def _apply_layout_impl(self, mode: str = "auto"):
        """
        Aplica o layout com preenchimento completo sem desperdício de espaço:
        - 0 Câmeras: Slot amigável de boas-vindas.
        - Modo 1x1: só a câmera selecionada, ocupando toda a área.
        - Modos 2x2 / 3x3: grade fixa com as câmeras da página atual.
        - Automático: disposição adaptada à quantidade de câmeras da página (até 9).
        """
        self._reset_grid()
        views_list = list(self.camera_views.values())

        if not views_list:
            self._show_empty_slot()
            return

        if mode == "1x1":
            if self.selected_camera_id and self.selected_camera_id in self.camera_views:
                view = self.camera_views[self.selected_camera_id]
            else:
                view = views_list[0]
            self._stretch(1, 1)
            self._place(view, 0, 0)
            return

        capacity = self.page_capacity(mode)
        self.current_page = max(0, min(self.current_page, self.page_count() - 1))
        page_views = views_list[self.current_page * capacity:(self.current_page + 1) * capacity]

        if mode in ("2x2", "3x3"):
            size = 2 if mode == "2x2" else 3
            self._stretch(size, size)
            for idx in range(size * size):
                r, c = divmod(idx, size)
                if idx < len(page_views):
                    self._place(page_views[idx], r, c)
                else:
                    filler = QFrame(self._grid_host)
                    filler.setObjectName("emptyGridCell")
                    filler.setStyleSheet("QFrame#emptyGridCell { background-color: #080D18; border-radius: 6px; }")
                    self._filler_cells.append(filler)
                    self._place(filler, r, c)
            return

        self._arrange_auto(page_views)

    def _arrange_auto(self, views: List[CameraViewWidget]):
        """Disposição automática sem slots vazios para 1 a 9 câmeras."""
        n = len(views)
        if n == 1:
            self._stretch(1, 1)
            self._place(views[0], 0, 0)
        elif n == 2:
            # 1 em cima e 1 embaixo, ocupando 100% da largura
            self._stretch(2, 1)
            self._place(views[0], 0, 0)
            self._place(views[1], 1, 0)
        elif n == 3:
            # 2 no topo e 1 na base ocupando a largura total
            self._stretch(2, 2)
            self._place(views[0], 0, 0)
            self._place(views[1], 0, 1)
            self._place(views[2], 1, 0, 1, 2)
        elif n == 4:
            self._stretch(2, 2)
            for idx, view in enumerate(views):
                self._place(view, *divmod(idx, 2))
        elif n in (5, 6):
            self._stretch(2, 3)
            if n == 5:
                for idx, view in enumerate(views[:3]):
                    self._place(view, 0, idx)
                self._place(views[3], 1, 0)
                self._place(views[4], 1, 1, 1, 2)
            else:
                for idx, view in enumerate(views):
                    self._place(view, *divmod(idx, 3))
        else:
            self._stretch(3, 3)
            for idx, view in enumerate(views[:9]):
                self._place(view, *divmod(idx, 3))

    def _on_camera_selected(self, camera_id: str):
        """Destaca a câmera selecionada e emite sinal para MainWindow."""
        if self.selected_camera_id != camera_id:
            self.selected_camera_id = camera_id
            for cid, view in self.camera_views.items():
                view.set_selected(cid == camera_id)
            
            # Se estivermos em 1x1, atualizar a câmera exibida
            if self.grid_layout_mode == "1x1":
                self.apply_layout("1x1")

            self.camera_selected.emit(camera_id)

    def _on_camera_double_clicked(self, camera_id: str):
        """Alterna suavemente entre 1x1 (maximizado) e o modo de grade dinâmico."""
        self.selected_camera_id = camera_id
        if self.grid_layout_mode == "1x1":
            target_mode = self.previous_grid_mode or "auto"
            self.set_layout_mode(target_mode)
            self.layout_mode_changed.emit(target_mode)
        else:
            self.set_layout_mode("1x1")
            self.layout_mode_changed.emit("1x1")

    def select_camera(self, camera_id: str):
        """Seleciona programaticamente uma câmera sem disparar loop de eventos."""
        self.selected_camera_id = camera_id
        for cid, view in self.camera_views.items():
            view.set_selected(cid == camera_id)
        if self.grid_layout_mode == "1x1":
            self.apply_layout("1x1")

    def update_camera_name(self, camera_id: str, new_name: str):
        """Atualiza dinamicamente o nome da câmera na visualização do grid e nos dados internos."""
        if camera_id in self.camera_views:
            self.camera_views[camera_id].update_camera_name(new_name)
        for cam in self.camera_devices:
            if cam.id == camera_id:
                cam.name = new_name

    def stop_all(self):
        """Interrompe todas as threads de streaming ao fechar o app."""
        for view in self.camera_views.values():
            view.stop()
            view.close()
