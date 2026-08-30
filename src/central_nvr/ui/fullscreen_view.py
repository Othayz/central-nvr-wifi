"""
Janela de Visualização em Tela Cheia Exclusiva para Câmera Individual.
Exibe 100% da tela do monitor apenas com o vídeo da câmera, com controles
OSD inteligentes que se ocultam automaticamente para não atrapalhar a visão do vídeo.
"""
import datetime
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import get_data_dir


class FullscreenPlayerWindow(QWidget):
    """
    Janela exclusiva sem bordas (Frameless Fullscreen) dedicada a uma única câmera.
    - Oculta controles OSD e cursor do mouse automaticamente após 2.5s de inatividade
      para garantir que nenhum texto se sobreponha à data/hora gravada no vídeo da câmera.
    - Movimentar o mouse reexibe os controles instantaneamente.
    - Pressione ESC, F11, Q ou dê duplo clique para fechar a visualização.
    - Pressione H ou O para alternar visibilidade do OSD manualmente.
    - Pressione S para tirar foto/snapshot instantâneo do fluxo ao vivo.
    """

    def __init__(self, camera: CameraDevice, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.camera = camera
        self.current_frame: Optional[QImage] = None
        self.fps: float = 0.0
        self.bitrate_kbps: float = 0.0
        self.codec: str = "H.265 / HEVC"
        self.osd_force_hidden: bool = False

        self.setWindowTitle(f"Central NVR - {camera.name} (Tela Cheia)")
        self.setStyleSheet("background-color: #000000;")
        self.setMouseTracking(True)

        self._setup_ui()
        self._setup_autohide()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)

        # 1. Container Superior OSD (Flutuante)
        self.top_container = QWidget(self)
        self.top_container.setStyleSheet("""
            QWidget {
                background: transparent;
            }
        """)
        top_layout = QHBoxLayout(self.top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        # Título da Câmera
        self.lbl_title = QLabel(f"🔴 AO VIVO: {self.camera.name} ({self.camera.ip})")
        self.lbl_title.setStyleSheet("""
            background-color: rgba(15, 23, 42, 0.85);
            color: #F8FAFC;
            font-size: 13px;
            font-weight: bold;
            padding: 7px 14px;
            border-radius: 6px;
            border: 1px solid rgba(56, 189, 248, 0.4);
        """)
        top_layout.addWidget(self.lbl_title)

        top_layout.addStretch()

        # Métricas de Resolução e FPS
        self.lbl_metrics = QLabel("1080p | 15 FPS")
        self.lbl_metrics.setStyleSheet("""
            background-color: rgba(15, 23, 42, 0.85);
            color: #38BDF8;
            font-size: 12px;
            font-weight: 600;
            padding: 7px 12px;
            border-radius: 6px;
            border: 1px solid rgba(51, 65, 85, 0.6);
        """)
        top_layout.addWidget(self.lbl_metrics)

        # Botão Snapshot Rápido
        self.btn_snapshot = QPushButton("📷 Foto (S)")
        self.btn_snapshot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_snapshot.setToolTip("Salvar captura de tela da câmera (Atalho: S)")
        self.btn_snapshot.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.85);
                color: #F1F5F9;
                font-weight: 600;
                font-size: 12px;
                padding: 7px 12px;
                border-radius: 6px;
                border: 1px solid #475569;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38BDF8;
                color: #FFFFFF;
            }
        """)
        self.btn_snapshot.clicked.connect(self._take_snapshot)
        top_layout.addWidget(self.btn_snapshot)

        # Botão Ocultar/Exibir OSD
        self.btn_toggle_osd = QPushButton("👁 OSD (H)")
        self.btn_toggle_osd.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_osd.setToolTip("Ocultar ou fixar os textos informativos (Atalho: H)")
        self.btn_toggle_osd.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.85);
                color: #94A3B8;
                font-weight: 600;
                font-size: 12px;
                padding: 7px 12px;
                border-radius: 6px;
                border: 1px solid #475569;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #F8FAFC;
            }
        """)
        self.btn_toggle_osd.clicked.connect(self._toggle_osd_manually)
        top_layout.addWidget(self.btn_toggle_osd)

        # Botão Sair da Tela Cheia
        btn_close = QPushButton("✕ Sair (ESC)")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("Voltar para a grade da Central NVR (Atalho: ESC)")
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: rgba(220, 38, 38, 0.85);
                color: #FFFFFF;
                font-weight: bold;
                font-size: 12px;
                padding: 7px 14px;
                border-radius: 6px;
                border: 1px solid #EF4444;
            }
            QPushButton:hover {
                background-color: #DC2626;
                border-color: #F87171;
            }
        """)
        btn_close.clicked.connect(self.close)
        top_layout.addWidget(btn_close)

        root_layout.addWidget(self.top_container)
        root_layout.addStretch()

        # 2. Toast Notificação Flutuante de Feedback (Centralizado)
        self.lbl_toast = QLabel("", self)
        self.lbl_toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_toast.setStyleSheet("""
            background-color: rgba(15, 23, 42, 0.92);
            color: #4ADE80;
            font-size: 13px;
            font-weight: bold;
            padding: 8px 18px;
            border-radius: 20px;
            border: 1px solid #22C55E;
        """)
        self.lbl_toast.hide()

        # 3. Container Inferior com Dicas Rápidas
        self.bottom_container = QWidget(self)
        self.bottom_container.setStyleSheet("""
            QWidget {
                background: transparent;
            }
        """)
        bottom_layout = QHBoxLayout(self.bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addStretch()

        self.lbl_hint = QLabel("Pressione [ESC] para sair • [H] Ocultar/Mostrar Textos • [S] Tirar Foto • Clique Duplo para Voltar")
        self.lbl_hint.setStyleSheet("""
            background-color: rgba(15, 23, 42, 0.75);
            color: #94A3B8;
            font-size: 11px;
            font-weight: 500;
            padding: 6px 14px;
            border-radius: 6px;
            border: 1px solid rgba(51, 65, 85, 0.4);
        """)
        bottom_layout.addWidget(self.lbl_hint)
        bottom_layout.addStretch()

        root_layout.addWidget(self.bottom_container)

    def _setup_autohide(self):
        """Configura o timer para auto-ocultação dos controles e cursor após 2.5s sem mover o mouse."""
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(2500)
        self.hide_timer.timeout.connect(self._hide_controls)

        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.setInterval(2000)
        self.toast_timer.timeout.connect(self.lbl_toast.hide)

    def _hide_controls(self):
        """Oculta as barras OSD e o cursor do mouse para visualização 100% limpa da câmera."""
        self.top_container.hide()
        self.bottom_container.hide()
        self.setCursor(Qt.CursorShape.BlankCursor)

    def _show_controls(self):
        """Exibe os controles OSD e restaura o cursor do mouse ao detectar interação."""
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if not self.osd_force_hidden:
            self.top_container.show()
            self.bottom_container.show()

    def _toggle_osd_manually(self):
        """Permite ao usuário forçar o modo OSD totalmente limpo ou reativar os textos."""
        self.osd_force_hidden = not self.osd_force_hidden
        if self.osd_force_hidden:
            self.top_container.hide()
            self.bottom_container.hide()
            self.btn_toggle_osd.setText("👁 OSD (Oculto)")
            self._show_toast("👁 Textos OSD Ocultados (Pressione H para reexibir)")
        else:
            self.top_container.show()
            self.bottom_container.show()
            self.btn_toggle_osd.setText("👁 OSD (H)")
            self._show_toast("👁 Textos OSD Visíveis")
            self.hide_timer.start(2500)

    def _show_toast(self, text: str):
        """Exibe mensagem toast rápida no centro inferior da tela."""
        self.lbl_toast.setText(text)
        self.lbl_toast.adjustSize()
        # Posicionar acima da barra inferior
        tx = (self.width() - self.lbl_toast.width()) // 2
        ty = self.height() - 90
        self.lbl_toast.move(tx, ty)
        self.lbl_toast.show()
        self.lbl_toast.raise_()
        self.toast_timer.start(2000)

    def _take_snapshot(self):
        """Salva snapshot direto do frame atual em alta qualidade com sanitização contra Path Traversal."""
        if self.current_frame and not self.current_frame.isNull():
            snap_dir = get_data_dir() / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(self.camera.id))
            filename = f"snap_{safe_id}_{ts}.jpg"
            filepath = str(snap_dir / filename)
            saved = self.current_frame.save(filepath, "JPEG", 95)
            if saved:
                self._show_toast(f"📸 Foto salva com sucesso! ({filename})")
            else:
                self._show_toast("⚠️ Falha ao salvar foto.")
        else:
            self._show_toast("⚠️ Sem sinal de vídeo para capturar foto.")

    def update_camera_name(self, new_name: str):
        """Atualiza dinamicamente o nome da câmera na visualização de tela cheia."""
        self.camera.name = new_name
        self.lbl_title.setText(f"🔴 AO VIVO: {new_name} ({self.camera.ip})")
        self.setWindowTitle(f"Central NVR - {new_name} (Tela Cheia)")
        self.update()

    def set_frame(self, frame: QImage, fps: float = 0.0, bitrate: float = 0.0, codec: str = "", jitter_ms: float = 0.0):
        """Recebe novo frame e atualiza métricas de QoS no OSD."""
        self.current_frame = frame
        if fps > 0:
            self.fps = fps
        if bitrate > 0:
            self.bitrate_kbps = bitrate
        if codec:
            self.codec = codec
        if frame and not frame.isNull():
            w, h = frame.width(), frame.height()
            bitrate_str = f" | {int(self.bitrate_kbps)} kbps" if self.bitrate_kbps > 0 else ""
            jitter_str = f" | Jitter: {jitter_ms:.1f}ms" if jitter_ms > 0 else ""
            self.lbl_metrics.setText(f"{w}x{h} | {self.fps:.1f} FPS{bitrate_str} | {self.codec}{jitter_str}")
        self.update()

    def showEvent(self, event):
        """Ao abrir em tela cheia, exibe controles e inicia contagem regressiva para auto-hide."""
        super().showEvent(event)
        self._show_controls()
        self.hide_timer.start(2500)

    def mouseMoveEvent(self, event):
        """Ao mover o mouse, reativa os controles e reinicia o timer de ocultação."""
        self._show_controls()
        self.hide_timer.start(2500)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        """Clique simples reativa controles e reinicia timer."""
        self._show_controls()
        self.hide_timer.start(2500)
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        """Reposiciona toast se a janela redimensionar."""
        super().resizeEvent(event)
        if self.lbl_toast.isVisible():
            tx = (self.width() - self.lbl_toast.width()) // 2
            ty = self.height() - 90
            self.lbl_toast.move(tx, ty)

    def paintEvent(self, event):
        """Desenha a imagem da câmera cobrindo 100% da tela do monitor com proporção mantida."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))

        if self.current_frame and not self.current_frame.isNull():
            target_rect = self.rect()
            img_w = self.current_frame.width()
            img_h = self.current_frame.height()

            if img_w > 0 and img_h > 0:
                scale = min(target_rect.width() / img_w, target_rect.height() / img_h)
                dest_w = int(img_w * scale)
                dest_h = int(img_h * scale)
                dest_x = (target_rect.width() - dest_w) // 2
                dest_y = (target_rect.height() - dest_h) // 2
                dest_rect = QRect(dest_x, dest_y, dest_w, dest_h)
                painter.drawImage(dest_rect, self.current_frame)
        else:
            painter.setPen(QColor("#64748B"))
            painter.setFont(QFont("Arial", 16))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"Aguardando sinal de vídeo da câmera...\n({self.camera.name})",
            )

        painter.end()
        super().paintEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_F11, Qt.Key.Key_Q):
            self.close()
        elif key in (Qt.Key.Key_H, Qt.Key.Key_O, Qt.Key.Key_Tab):
            self._toggle_osd_manually()
        elif key in (Qt.Key.Key_S, Qt.Key.Key_F):
            self._take_snapshot()
        elif key == Qt.Key.Key_Space:
            self._toggle_osd_manually()
        else:
            self._show_controls()
            self.hide_timer.start(2500)
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.close()
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event):
        """Ao fechar, garante que o cursor retorne ao normal e os timers parem."""
        self.hide_timer.stop()
        self.toast_timer.stop()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().closeEvent(event)
