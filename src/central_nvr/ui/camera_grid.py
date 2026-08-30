"""
Grid Responsivo Multi-Câmeras para visualização em mosaico dinâmico e inteligente.
Garante preenchimento de 100% da área útil sem desperdício de espaço ou slots vazios desnecessários:
- 1 Câmera: Ocupa 100% da tela.
- 2 Câmeras: Uma em cima e outra embaixo (2 linhas x 1 coluna) ocupando 100% da largura e altura.
- 3 Câmeras: Distribuídas proporcionalmente sem slots vazios.
- 4 Câmeras: Grid 2x2.
- 5 ou 6 Câmeras: Grid 2x3.
- 7 a 9 Câmeras: Grid 3x3.
"""
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

from central_nvr.core.camera import CameraDevice
from central_nvr.ui.camera_view import CameraViewWidget


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

    def __init__(
        self,
        hw_accel: str = "vaapi",
        rtsp_transport: str = "auto",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.hw_accel = hw_accel
        self.rtsp_transport = rtsp_transport
        
        self.grid_layout_mode = "auto"  # "auto", "1x1", "2x2", "3x3"
        self.previous_grid_mode = "auto"
        self.selected_camera_id: Optional[str] = None
        
        self.camera_views: Dict[str, CameraViewWidget] = {}
        self.camera_devices: List[CameraDevice] = []
        self.empty_slots: List[EmptySlotWidget] = []

        self.layout = QGridLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(6)

    def set_cameras(self, cameras: List[CameraDevice]):
        """Atualiza a lista de câmeras e reconstrói o grid."""
        # Parar e limpar views antigas de forma segura
        for view in list(self.camera_views.values()):
            try:
                view.selected.disconnect()
                view.double_clicked.disconnect()
                view.ptz_requested.disconnect()
                view.snapshot_taken.disconnect()
                view.fullscreen_requested.disconnect()
                view.rename_requested.disconnect()
            except Exception:
                pass
            view.stop()
            view.close()
            view.deleteLater()
        self.camera_views.clear()

        self.camera_devices = cameras

        # Instanciar novos widgets de visualização
        for cam in cameras:
            view = CameraViewWidget(
                camera=cam,
                hw_accel=self.hw_accel,
                rtsp_transport=self.rtsp_transport,
                parent=self,
            )
            view.selected.connect(self._on_camera_selected)
            view.double_clicked.connect(self._on_camera_double_clicked)
            view.ptz_requested.connect(lambda cid: self.ptz_requested.emit(cid))
            view.snapshot_taken.connect(lambda cid, fp: self.snapshot_taken.emit(cid, fp))
            view.fullscreen_requested.connect(lambda cid: self.fullscreen_requested.emit(cid))
            view.rename_requested.connect(lambda cid, name: self.camera_renamed.emit(cid, name))
            self.camera_views[cam.id] = view

        # Selecionar a primeira se houver
        if cameras:
            self.selected_camera_id = cameras[0].id
            self.select_camera(cameras[0].id)
        else:
            self.selected_camera_id = None

        self.apply_layout(self.grid_layout_mode)

    def set_layout_mode(self, mode: str):
        """Altera a disposição das câmeras (auto, 1x1, 2x2, 3x3)."""
        if mode != "1x1":
            self.previous_grid_mode = mode
        self.grid_layout_mode = mode
        self.apply_layout(mode)

    def apply_layout(self, mode: str = "auto"):
        """
        Aplica o layout com preenchimento completo sem desperdício de espaço:
        - 0 Câmeras: Slot amigável de boas-vindas.
        - 1 Câmera: Ocupa 100% da área útil (1 linha x 1 coluna).
        - 2 Câmeras: Câmera 1 no topo e Câmera 2 na base (2 linhas x 1 coluna), ocupando 100% do espaço.
        - 3 Câmeras: 2 em cima (50% de largura cada) e 1 embaixo (100% de largura).
        - 4 Câmeras: Grid 2x2 (2 linhas x 2 colunas).
        - 5 ou 6 Câmeras: Grid 2x3.
        - 7 a 9 Câmeras: Grid 3x3.
        """
        # Limpar itens do QGridLayout
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        # Limpar layout e reiniciar estrutura

        # Limpar slots vazios anteriores
        for slot in self.empty_slots:
            slot.deleteLater()
        self.empty_slots.clear()

        # Ocultar todas as views antes de reposicionar
        for v in self.camera_views.values():
            v.hide()

        views_list = list(self.camera_views.values())
        num_cams = len(views_list)

        # Modo 1x1 explícito
        if mode == "1x1":
            self.layout.setRowStretch(0, 1)
            self.layout.setColumnStretch(0, 1)
            if self.selected_camera_id and self.selected_camera_id in self.camera_views:
                view = self.camera_views[self.selected_camera_id]
            elif views_list:
                view = views_list[0]
            else:
                slot = EmptySlotWidget(1, parent=self)
                slot.add_clicked.connect(self.add_camera_requested.emit)
                self.empty_slots.append(slot)
                self.layout.addWidget(slot, 0, 0)
                slot.show()
                return

            self.layout.addWidget(view, 0, 0)
            view.show()
            return

        # Modo sem nenhuma câmera cadastrada
        if num_cams == 0:
            self.layout.setRowStretch(0, 1)
            self.layout.setColumnStretch(0, 1)
            slot = EmptySlotWidget(1, parent=self)
            slot.add_clicked.connect(self.add_camera_requested.emit)
            self.empty_slots.append(slot)
            self.layout.addWidget(slot, 0, 0)
            slot.show()
            return

        # Modo com 1 única câmera conectada: Ocupa 100% da área útil
        if num_cams == 1:
            self.layout.setRowStretch(0, 1)
            self.layout.setColumnStretch(0, 1)
            view = views_list[0]
            self.layout.addWidget(view, 0, 0)
            view.show()
            return

        # Modo com exatamente 2 câmeras conectadas: 1 em cima e 1 embaixo (2 linhas x 1 coluna)
        if num_cams == 2:
            self.layout.setRowStretch(0, 1)
            self.layout.setRowStretch(1, 1)
            self.layout.setColumnStretch(0, 1)

            self.layout.addWidget(views_list[0], 0, 0)
            self.layout.addWidget(views_list[1], 1, 0)
            views_list[0].show()
            views_list[1].show()
            return

        # Modo com 3 câmeras conectadas: 2 no topo e 1 na base ocupando largura total
        if num_cams == 3:
            self.layout.setRowStretch(0, 1)
            self.layout.setRowStretch(1, 1)
            self.layout.setColumnStretch(0, 1)
            self.layout.setColumnStretch(1, 1)

            self.layout.addWidget(views_list[0], 0, 0, 1, 1)
            self.layout.addWidget(views_list[1], 0, 1, 1, 1)
            self.layout.addWidget(views_list[2], 1, 0, 1, 2)
            views_list[0].show()
            views_list[1].show()
            views_list[2].show()
            return

        # Modo com 4 câmeras conectadas: Grid 2x2
        if num_cams == 4 or mode == "2x2":
            self.layout.setRowStretch(0, 1)
            self.layout.setRowStretch(1, 1)
            self.layout.setColumnStretch(0, 1)
            self.layout.setColumnStretch(1, 1)

            for idx, view in enumerate(views_list[:4]):
                r = idx // 2
                c = idx % 2
                self.layout.addWidget(view, r, c)
                view.show()
            return

        # Modo com 5 ou 6 câmeras conectadas: Grid 2x3
        if num_cams in (5, 6):
            self.layout.setRowStretch(0, 1)
            self.layout.setRowStretch(1, 1)
            self.layout.setColumnStretch(0, 1)
            self.layout.setColumnStretch(1, 1)
            self.layout.setColumnStretch(2, 1)

            if num_cams == 5:
                self.layout.addWidget(views_list[0], 0, 0, 1, 1)
                self.layout.addWidget(views_list[1], 0, 1, 1, 1)
                self.layout.addWidget(views_list[2], 0, 2, 1, 1)
                self.layout.addWidget(views_list[3], 1, 0, 1, 1)
                self.layout.addWidget(views_list[4], 1, 1, 1, 2)
                for v in views_list[:5]:
                    v.show()
            else:
                for idx, view in enumerate(views_list[:6]):
                    r = idx // 3
                    c = idx % 3
                    self.layout.addWidget(view, r, c)
                    view.show()
            return

        # Modo com 7 ou mais câmeras conectadas: Grid 3x3
        for r in range(3):
            self.layout.setRowStretch(r, 1)
        for c in range(3):
            self.layout.setColumnStretch(c, 1)

        for idx, view in enumerate(views_list[:9]):
            r = idx // 3
            c = idx % 3
            self.layout.addWidget(view, r, c)
            view.show()

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
