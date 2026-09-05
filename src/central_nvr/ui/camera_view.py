import html
"""
Widget de Viewport de Câmera Individual com OSD, Controles em Tempo Real e Renomeação Rápida.
"""
import datetime
import os
import re
import time
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import CameraDevice, ConnectionState, StreamStats
from central_nvr.core.stream_worker import StreamWorker


class CameraViewWidget(QFrame):
    """
    Exibe o feed de vídeo ao vivo de uma única câmera com On-Screen Display (OSD),
    estatísticas de rede e atalhos rápidos de controle (Snapshot, PTZ, Renomear, Tela Cheia).
    """

    selected = Signal(str)  # camera_id
    double_clicked = Signal(str)  # camera_id
    ptz_requested = Signal(str)  # camera_id
    snapshot_taken = Signal(str, str)  # (camera_id, filepath)
    fullscreen_requested = Signal(str)  # camera_id
    rename_requested = Signal(str, str)  # (camera_id, new_name)

    def __init__(
        self,
        camera: CameraDevice,
        hw_accel: str = "vaapi",
        rtsp_transport: str = "tcp",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.camera = camera
        self.hw_accel = hw_accel
        self.rtsp_transport = rtsp_transport
        
        self.is_selected = False
        self.is_maximized_view = False
        self.current_frame: Optional[QImage] = None
        self.stats = StreamStats()
        self.connection_state = ConnectionState.DISCONNECTED
        self.status_message = "Desconectado"

        self.setObjectName("cameraContainer")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(240, 160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._setup_ui()
        self._start_stream()

    def _setup_ui(self):
        """Inicializa os layouts e componentes visuais do viewport."""
        self.setStyleSheet("""
            QFrame#cameraContainer {
                background-color: #050B14;
                border: 1px solid #1E293B;
                border-radius: 6px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Top Bar do OSD
        top_bar = QHBoxLayout()
        
        self.lbl_title = QLabel(f"<b>{html.escape(self.camera.name)}</b>")
        self.lbl_title.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_title.setToolTip("Clique com o botão direito para Renomear esta câmera")
        self.lbl_title.setStyleSheet("""
            QLabel {
                color: #F8FAFC;
                background-color: rgba(15, 23, 42, 0.85);
                padding: 3px 8px;
                border-radius: 4px;
                font-size: 11px;
                border: 1px solid transparent;
            }
            QLabel:hover {
                background-color: rgba(30, 41, 59, 0.95);
                border: 1px solid #38BDF8;
                color: #38BDF8;
            }
        """)
        self.lbl_title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lbl_title.customContextMenuRequested.connect(self._show_title_context_menu)
        top_bar.addWidget(self.lbl_title)

        top_bar.addStretch()

        self.lbl_motion = QLabel("🔴 MOVIMENTO")
        self.lbl_motion.setStyleSheet("color: #FFFFFF; background-color: rgba(220, 38, 38, 0.95); padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        self.lbl_motion.hide()
        top_bar.addWidget(self.lbl_motion)

        self.lbl_hw_accel = QLabel("VA-API" if self.hw_accel == "vaapi" else "CPU")
        self.lbl_hw_accel.setStyleSheet("color: #38BDF8; background-color: rgba(15, 23, 42, 0.85); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        top_bar.addWidget(self.lbl_hw_accel)

        self.lbl_status = QLabel("AO VIVO")
        self.lbl_status.setStyleSheet("color: #4ADE80; background-color: rgba(22, 101, 52, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        top_bar.addWidget(self.lbl_status)

        main_layout.addLayout(top_bar)
        main_layout.addStretch()

        # Bottom Bar do OSD
        bottom_bar = QHBoxLayout()

        self.lbl_metrics = QLabel("0.0 FPS | 0 kbps | 0 ms")
        self.lbl_metrics.setStyleSheet("color: #94A3B8; background-color: rgba(15, 23, 42, 0.85); padding: 3px 8px; border-radius: 4px; font-size: 10px; font-family: monospace;")
        bottom_bar.addWidget(self.lbl_metrics)

        bottom_bar.addStretch()

        # Botão Perfil Dual-Stream (HD / SD)
        self.btn_stream_profile = QPushButton("HD")
        self.btn_stream_profile.setToolTip("Alternar entre Stream Principal (HD/4K) e Sub-Stream (SD/Baixa Banda)")
        self.btn_stream_profile.setFixedSize(32, 24)
        self.btn_stream_profile.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.9);
                border: 1px solid #475569;
                border-radius: 4px;
                color: #38BDF8;
                font-size: 10px;
                font-weight: 700;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38BDF8;
                color: #FFFFFF;
            }
        """)
        self.btn_stream_profile.clicked.connect(self._toggle_stream_profile)
        bottom_bar.addWidget(self.btn_stream_profile)

        # Botão Snapshot
        self.btn_snapshot = QPushButton("Foto")
        self.btn_snapshot.setToolTip("Capturar Foto / Snapshot")
        self.btn_snapshot.setFixedSize(36, 24)
        self.btn_snapshot.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.9);
                border: 1px solid #475569;
                border-radius: 4px;
                color: #F1F5F9;
                font-size: 10px;
                font-weight: 600;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38BDF8;
                color: #FFFFFF;
            }
        """)
        self.btn_snapshot.clicked.connect(self._take_snapshot)
        bottom_bar.addWidget(self.btn_snapshot)

        # Botão PTZ
        self.btn_ptz = QPushButton("PTZ")
        self.btn_ptz.setToolTip("Abrir Controles PTZ (Movimento/Zoom)")
        self.btn_ptz.setFixedSize(36, 24)
        self.btn_ptz.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.9);
                border: 1px solid #475569;
                border-radius: 4px;
                color: #F1F5F9;
                font-size: 10px;
                font-weight: 600;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38BDF8;
                color: #FFFFFF;
            }
        """)
        self.btn_ptz.clicked.connect(lambda: self.ptz_requested.emit(self.camera.id))
        bottom_bar.addWidget(self.btn_ptz)

        # Botão Maximizar (Grid 1x1)
        self.btn_fullscreen = QPushButton("Max")
        self.btn_fullscreen.setToolTip("Maximizar na Grade (Grid 1x1)")
        self.btn_fullscreen.setFixedSize(36, 24)
        self.btn_fullscreen.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.9);
                border: 1px solid #475569;
                border-radius: 4px;
                color: #F1F5F9;
                font-size: 10px;
                font-weight: 600;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #38BDF8;
                color: #FFFFFF;
            }
        """)
        self.btn_fullscreen.clicked.connect(lambda: self.double_clicked.emit(self.camera.id))
        bottom_bar.addWidget(self.btn_fullscreen)

        # Botão Tela Cheia Real (Sem menus)
        self.btn_truescreen = QPushButton("Tela")
        self.btn_truescreen.setToolTip("Modo Tela Cheia Completo no Monitor (F11 / Exclusivo)")
        self.btn_truescreen.setFixedSize(40, 24)
        self.btn_truescreen.setStyleSheet("""
            QPushButton {
                background-color: rgba(15, 23, 42, 0.95);
                border: 1px solid #0284C7;
                border-radius: 4px;
                color: #38BDF8;
                font-size: 10px;
                font-weight: 700;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #0284C7;
                border-color: #38BDF8;
                color: #FFFFFF;
            }
        """)
        self.btn_truescreen.clicked.connect(self._open_fullscreen_player)
        bottom_bar.addWidget(self.btn_truescreen)

        main_layout.addLayout(bottom_bar)

    def prompt_rename(self):
        """Abre janela de diálogo para renomear a câmera e emite sinal."""
        new_name, ok = QInputDialog.getText(
            self,
            "Renomear Câmera",
            f"Digite o novo nome para a câmera ({self.camera.ip}):",
            text=self.camera.name,
        )
        if ok and new_name.strip():
            clean_name = new_name.strip()
            if clean_name != self.camera.name:
                self.rename_requested.emit(self.camera.id, clean_name)

    def _show_context_menu(self, global_pos=None):
        """Exibe menu de contexto com opções rápidas incluindo renomear câmera."""
        menu = QMenu(self)

        act_rename = menu.addAction(f"✏️ Renomear Câmera...")
        menu.addSeparator()
        act_snap = menu.addAction("📸 Tirar Foto / Snapshot")
        act_ptz = menu.addAction("🎮 Abrir Controle PTZ")
        act_max = menu.addAction("🔲 Maximizar Grade (1x1)")
        act_full = menu.addAction("🖥️ Tela Cheia no Monitor")

        pos = global_pos or QCursor.pos()
        action = menu.exec(pos)

        if action == act_rename:
            self.prompt_rename()
        elif action == act_snap:
            self._take_snapshot()
        elif action == act_ptz:
            self.ptz_requested.emit(self.camera.id)
        elif action == act_max:
            self.double_clicked.emit(self.camera.id)
        elif action == act_full:
            self._open_fullscreen_player()

    def _show_title_context_menu(self, point: QPoint):
        self._show_context_menu(self.lbl_title.mapToGlobal(point))

    def contextMenuEvent(self, event):
        self._show_context_menu(event.globalPos())
        event.accept()

    def update_camera_name(self, new_name: str):
        """Atualiza dinamicamente o nome da câmera no viewport e na tela cheia ativa."""
        self.camera.name = new_name
        self.lbl_title.setText(f"<b>{html.escape(new_name)}</b>")
        if hasattr(self, "_fullscreen_win") and self._fullscreen_win:
            self._fullscreen_win.update_camera_name(new_name)

    def _open_fullscreen_player(self):
        """Abre uma janela 100% tela cheia exclusiva apenas com o vídeo da câmera (sem menus/barras do programa)."""
        from central_nvr.ui.fullscreen_view import FullscreenPlayerWindow
        if not hasattr(self, "_fullscreen_win") or self._fullscreen_win is None:
            self._fullscreen_win = FullscreenPlayerWindow(camera=self.camera, parent=None)
        if self.current_frame:
            self._fullscreen_win.set_frame(
                self.current_frame,
                fps=self.stats.fps,
                bitrate=self.stats.bitrate_kbps,
                codec=self.stats.codec
            )
        self._fullscreen_win.showFullScreen()

    def _start_stream(self):
        """Inicia a thread de captura e decodificação do fluxo RTSP."""
        self.worker = StreamWorker(
            camera=self.camera,
            hw_accel=self.hw_accel,
            rtsp_transport=self.rtsp_transport,
            parent=self,
        )
        self.worker.frame_received.connect(self._on_frame_received)
        self.worker.stats_updated.connect(self._on_stats_updated)
        self.worker.state_changed.connect(self._on_state_changed)
        self.worker.snapshot_saved.connect(self._on_snapshot_saved)
        if hasattr(self.worker, "motion_detected"):
            self.worker.motion_detected.connect(self._on_motion_detected)
        self.worker.start()

    def set_selected(self, selected: bool):
        """Marca o viewport como selecionado com destaque visual."""
        self.is_selected = selected
        if selected:
            self.setStyleSheet("""
                QFrame#cameraContainer {
                    background-color: #050B14;
                    border: 2px solid #38BDF8;
                    border-radius: 6px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#cameraContainer {
                    background-color: #050B14;
                    border: 1px solid #1E293B;
                    border-radius: 6px;
                }
            """)
        self.update()

    def _on_frame_received(self, q_img: QImage):
        """Recebe novo frame e repassa para a tela cheia se estiver aberta."""
        self.current_frame = q_img
        if hasattr(self, "_fullscreen_win") and self._fullscreen_win and self._fullscreen_win.isVisible():
            self._fullscreen_win.set_frame(
                q_img,
                fps=self.stats.fps,
                bitrate=self.stats.bitrate_kbps,
                codec=self.stats.codec
            )
        self.update()

    def _on_stats_updated(self, stats: StreamStats):
        """Atualiza a exibição de FPS, Bitrate, Codec e QoS no OSD."""
        self.stats = stats
        codec_str = stats.codec if stats.codec else "H.264"
        jitter_str = f" | Jitter: {stats.jitter_ms}ms" if stats.jitter_ms > 0 else ""
        self.lbl_metrics.setText(f"{stats.fps} FPS | {int(stats.bitrate_kbps)} kbps | {codec_str}{jitter_str}")

    def _on_motion_detected(self, camera_id: str, is_detected: bool):
        """Atualiza o alerta visual de detecção de movimento em tempo real."""
        if hasattr(self, "lbl_motion"):
            if is_detected:
                self.lbl_motion.show()
                if not self.is_selected:
                    self.setStyleSheet("""
                        QFrame#cameraContainer {
                            background-color: #050B14;
                            border: 2px solid #EF4444;
                            border-radius: 6px;
                        }
                    """)
            else:
                self.lbl_motion.hide()
                if not self.is_selected:
                    self.setStyleSheet("""
                        QFrame#cameraContainer {
                            background-color: #050B14;
                            border: 1px solid #1E293B;
                            border-radius: 6px;
                        }
                    """)

    def _toggle_stream_profile(self):
        """Alterna entre MainStream (HD) e SubStream (SD) para controle de banda."""
        if not hasattr(self, "worker") or not self.worker:
            return
        new_pref = not self.worker.prefer_substream
        self.worker.set_prefer_substream(new_pref)
        self.btn_stream_profile.setText("SD" if new_pref else "HD")
        self.btn_stream_profile.setStyleSheet(
            "background-color: rgba(245, 158, 11, 0.9); color: #000000; font-weight: bold; border-radius: 4px; border: none;"
            if new_pref else
            "background-color: rgba(30, 41, 59, 0.9); border: 1px solid #475569; color: #38BDF8; font-weight: 700; border-radius: 4px;"
        )

    def _on_state_changed(self, state: ConnectionState, message: str):
        """Atualiza a indicação visual de conexão."""
        self.connection_state = state
        self.status_message = message

        if state == ConnectionState.STREAMING:
            self.lbl_status.setText("AO VIVO")
            self.lbl_status.setStyleSheet("color: #4ADE80; background-color: rgba(22, 101, 52, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        elif state == ConnectionState.CONNECTING or state == ConnectionState.RECONNECTING:
            self.lbl_status.setText("CONECTANDO")
            self.lbl_status.setStyleSheet("color: #FBBF24; background-color: rgba(146, 64, 14, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        elif state == ConnectionState.ERROR:
            self.lbl_status.setText("ERRO")
            self.lbl_status.setStyleSheet("color: #F87171; background-color: rgba(153, 27, 27, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        else:
            self.lbl_status.setText("OFFLINE")
            self.lbl_status.setStyleSheet("color: #94A3B8; background-color: rgba(51, 65, 85, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")

    def _take_snapshot(self):
        """Dispara a captura de foto do frame atual sanitizando o ID da câmera contra Path Traversal."""
        from central_nvr.core.config import get_data_dir
        snap_dir = get_data_dir() / "snapshots"
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(self.camera.id))
        filename = f"snap_{safe_id}_{ts}.jpg"
        filepath = str(snap_dir / filename)
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.take_snapshot(filepath)

    def _on_snapshot_saved(self, filepath: str):
        self.snapshot_taken.emit(self.camera.id, filepath)

    def paintEvent(self, event):
        """Renderiza o frame de vídeo com proporção mantida (Aspect Ratio)."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Desenhar frame de vídeo se disponível
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
            # Fundo sem sinal elegante
            painter.fillRect(self.rect(), QColor("#0A0F1D"))
            painter.setPen(QColor("#475569"))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                f"Sinal de Vídeo: {self.status_message}\n({self.camera.ip})",
            )

        painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.camera.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.camera.id)
        super().mouseDoubleClickEvent(event)

    def stop(self):
        """Para a thread de streaming e desconecta sinais de forma segura."""
        if hasattr(self, "_fullscreen_win") and self._fullscreen_win:
            try:
                self._fullscreen_win.close()
                self._fullscreen_win = None
            except Exception:
                pass

        if hasattr(self, "worker") and self.worker:
            w = self.worker
            self.worker = None
            try:
                w.frame_received.disconnect()
                w.stats_updated.disconnect()
                w.state_changed.disconnect()
                w.snapshot_saved.disconnect()
            except Exception:
                pass
            if w.isRunning():
                w.stop()
                w.wait(500)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
