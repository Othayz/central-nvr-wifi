"""
Barra de Linha do Tempo (Timeline) para visualização de gravações e eventos de detecção de movimento.
Inspirado na interface de referência da Central NVR.
"""
import datetime
from typing import List, Optional

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TimelineBarWidget(QFrame):
    """
    Componente visual que exibe uma linha do tempo de 24 horas com marcações
    de vídeo gravado contínuo e eventos de detecção de movimento (Motion).
    """

    time_scrubbed = Signal(datetime.time)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.camera_name = "Câmera 1 (Sala de Estar)"
        self.playhead_progress = 0.65  # Posição do cursor (0.0 a 1.0)
        self.show_motion_events = True
        
        self.setObjectName("timelineBar")
        self.setFixedHeight(85)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        # Header da Timeline com controles
        top_layout = QHBoxLayout()
        self.lbl_cam = QLabel(f"<b>Timeline:</b> {self.camera_name}")
        self.lbl_cam.setObjectName("cardAccentTitle")
        top_layout.addWidget(self.lbl_cam)

        top_layout.addStretch()

        self.chk_motion = QCheckBox("Detecção de Movimento (Motion)")
        self.chk_motion.setChecked(True)
        self.chk_motion.stateChanged.connect(self._on_motion_toggled)
        top_layout.addWidget(self.chk_motion)

        layout.addLayout(top_layout)

        # Canvas da Linha do Tempo
        self.canvas = TimelineCanvas(self)
        layout.addWidget(self.canvas)

    def set_camera_name(self, name: str):
        self.camera_name = name
        self.lbl_cam.setText(f"<b>Timeline:</b> {name}")
        self.canvas.update()

    def _on_motion_toggled(self, state: int):
        self.show_motion_events = (state == Qt.CheckState.Checked.value)
        self.canvas.show_motion = self.show_motion_events
        self.canvas.update()


class TimelineCanvas(QWidget):
    """Área gráfica de pintura da régua de 24h e faixas de gravação."""

    def __init__(self, parent: TimelineBarWidget):
        super().__init__(parent)
        self.parent_bar = parent
        self.show_motion = True
        self.setMouseTracking(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        w = rect.width()
        h = rect.height()

        # Fundo da régua
        painter.fillRect(rect, QColor("#0F172A"))

        # Desenhar marcas de horas (00:00 até 24:00 de 2 em 2 horas)
        painter.setPen(QPen(QColor("#475569"), 1))
        painter.setFont(QFont("Arial", 8))

        hours = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]
        for hour in hours:
            x = int((hour / 24.0) * (w - 20)) + 10
            # Traço superior
            painter.drawLine(x, 0, x, 6)
            painter.drawText(x - 12, 16, f"{hour:02d}:00")
            # Linha guia sutil
            painter.setPen(QPen(QColor("#1E293B"), 1))
            painter.drawLine(x, 18, x, h)
            painter.setPen(QPen(QColor("#475569"), 1))

        # Faixa de Gravação (Barra azulada de vídeo contínuo)
        track_y = 24
        track_h = 18
        track_rect = QRectF(10, track_y, w - 20, track_h)
        painter.fillRect(track_rect, QColor("#1E293B"))

        # Blocos de gravação contínua (Simulados em tons de azul)
        rec_brush = QBrush(QColor("#2563EB"))
        rec_segments = [(0.1, 0.45), (0.50, 0.85), (0.88, 0.98)]
        for start_pct, end_pct in rec_segments:
            seg_x = 10 + start_pct * (w - 20)
            seg_w = (end_pct - start_pct) * (w - 20)
            painter.fillRect(QRectF(seg_x, track_y, seg_w, track_h), rec_brush)

        # Eventos de movimento (Motion - marcações amarelas/vermelhas)
        if self.show_motion:
            motion_brush = QBrush(QColor("#EF4444"))
            motion_points = [0.12, 0.25, 0.38, 0.52, 0.54, 0.70, 0.72, 0.80, 0.92]
            for p in motion_points:
                px = 10 + p * (w - 20)
                painter.fillRect(QRectF(px - 2, track_y, 4, track_h), motion_brush)

        # Cursor do Playhead (Linha amarela/branca com ponteiro)
        playhead_x = 10 + self.parent_bar.playhead_progress * (w - 20)
        painter.setPen(QPen(QColor("#FBBF24"), 2))
        painter.drawLine(int(playhead_x), 0, int(playhead_x), h)
        
        # Triângulo indicador no topo do cursor
        painter.setBrush(QBrush(QColor("#FBBF24")))
        painter.drawPolygon([
            QPoint(int(playhead_x) - 5, 0),
            QPoint(int(playhead_x) + 5, 0),
            QPoint(int(playhead_x), 7),
        ])

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            w = self.width() - 20
            click_x = max(0, min(event.position().x() - 10, w))
            self.parent_bar.playhead_progress = click_x / w
            self.update()
