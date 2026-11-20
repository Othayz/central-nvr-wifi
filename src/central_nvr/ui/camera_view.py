"""
Widget de Viewport de Câmera Individual com OSD, Controles em Tempo Real e Renomeação Rápida.
"""
import datetime
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter
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

from central_nvr.core.camera import DEFAULT_RTSP_TRANSPORT, CameraDevice, ConnectionState, StreamStats
from central_nvr.core.stream_worker import StreamWorker
from central_nvr.core.threads import retire_thread


@dataclass(frozen=True)
class ViewOptions:
    """Opções de Configurações aplicadas às views de câmera (Issue #34)."""
    buffer_size_ms: int = 150
    motion_detection: bool = True
    reconnect_interval_sec: float = 5.0
    auto_reconnect: bool = True
    snapshot_dir: str = ""
    show_osd_fps: bool = True
    show_osd_bitrate: bool = True
    show_osd_timestamp: bool = True

    @classmethod
    def from_config(cls, config) -> "ViewOptions":
        return cls(
            buffer_size_ms=int(config.get("buffer_size_ms", 150)),
            motion_detection=bool(config.get("enable_motion_detection", True)),
            reconnect_interval_sec=float(config.get("reconnect_interval_sec", 5)),
            auto_reconnect=bool(config.get("auto_reconnect", True)),
            snapshot_dir=str(config.get("snapshot_dir", "") or ""),
            show_osd_fps=bool(config.get("show_osd_fps", True)),
            show_osd_bitrate=bool(config.get("show_osd_bitrate", True)),
            show_osd_timestamp=bool(config.get("show_osd_timestamp", True)),
        )

    def resolved_snapshot_dir(self) -> Path:
        if self.snapshot_dir:
            return Path(self.snapshot_dir).expanduser()
        from central_nvr.core.config import get_data_dir
        return get_data_dir() / "snapshots"


class CameraViewWidget(QFrame):
    """
    Exibe o feed de vídeo ao vivo de uma única câmera com On-Screen Display (OSD),
    estatísticas de rede e atalhos rápidos de controle (Snapshot, PTZ, Renomear, Tela Cheia).
    """

    # Estado exibido ("Ao Vivo", "Sem sinal", "Pausada", "Conectando") para a árvore lateral
    display_state_changed = Signal(str, str)  # (camera_id, estado)
    selected = Signal(str)  # camera_id
    double_clicked = Signal(str)  # camera_id
    ptz_requested = Signal(str)  # camera_id
    snapshot_taken = Signal(str, str)  # (camera_id, filepath)
    rename_requested = Signal(str, str)  # (camera_id, new_name)

    def __init__(
        self,
        camera: CameraDevice,
        hw_accel: str = "vaapi",
        rtsp_transport: str = DEFAULT_RTSP_TRANSPORT,
        hidden_timeout_min: float = 5.0,
        options: Optional[ViewOptions] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.camera = camera
        self.hw_accel = hw_accel
        self.rtsp_transport = rtsp_transport
        self.options = options or ViewOptions()

        # Perda de sinal (Issue #33): instante em que o vídeo deixou de ser ao vivo e watchdog de
        # stream travado (STREAMING sem frames novos)
        self._signal_lost_at: Optional[datetime.datetime] = None
        self._last_frame_monotonic: Optional[float] = None
        self._stalled = False
        self._display_state = "Conectando"
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._check_stall)
        self._watchdog.start()

        # Perfil de stream escolhido pela grade (Issue #47) e escolha manual pelo botão HD/SD,
        # que prevalece até a próxima troca de layout
        self._auto_prefer_sub = False
        self._manual_override = False
        self._manual_prefer_sub = False

        # Câmera fora da tela (Issue #25): pausada e, após este tempo, desconectada
        self._on_screen = True
        self._suspended = False
        self._hidden_timer = QTimer(self)
        self._hidden_timer.setSingleShot(True)
        self._hidden_timer.timeout.connect(self._suspend_hidden)
        self.set_hidden_timeout(hidden_timeout_min)
        
        self.is_selected = False
        self.is_maximized_view = False
        self.current_frame: Optional[QImage] = None
        self.stats = StreamStats()
        self.connection_state = ConnectionState.DISCONNECTED
        self.status_message = "Desconectado"
        self.worker: Optional[StreamWorker] = None
        self._restart_after: Optional[StreamWorker] = None  # sessão anterior aguardada por reconnect()

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

        self.lbl_hw_accel = QLabel("CPU")
        self.lbl_hw_accel.setStyleSheet("color: #38BDF8; background-color: rgba(15, 23, 42, 0.85); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        top_bar.addWidget(self.lbl_hw_accel)

        self.lbl_status = QLabel("CONECTANDO")
        self.lbl_status.setStyleSheet("color: #FBBF24; background-color: rgba(146, 64, 14, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        top_bar.addWidget(self.lbl_status)

        main_layout.addLayout(top_bar)
        main_layout.addStretch()

        # Bottom Bar do OSD
        bottom_bar = QHBoxLayout()

        self.lbl_metrics = QLabel("")
        self.lbl_metrics.setStyleSheet("color: #94A3B8; background-color: rgba(15, 23, 42, 0.85); padding: 3px 8px; border-radius: 4px; font-size: 10px; font-family: monospace;")
        self._render_metrics()
        bottom_bar.addWidget(self.lbl_metrics)

        # Aviso temporário (ex.: sub-stream indisponível)
        self.lbl_notice = QLabel("")
        self.lbl_notice.setStyleSheet("color: #000000; background-color: rgba(245, 158, 11, 0.95); padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        self.lbl_notice.hide()
        bottom_bar.addWidget(self.lbl_notice)

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
        self.btn_ptz.clicked.connect(self._on_ptz_clicked)
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
        self.btn_fullscreen.clicked.connect(self._on_maximize_clicked)
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

    def _on_ptz_clicked(self):
        self.ptz_requested.emit(self.camera.id)

    def _on_maximize_clicked(self):
        self.double_clicked.emit(self.camera.id)

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

        act_rename = menu.addAction("✏️ Renomear Câmera...")
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
            self._fullscreen_win = FullscreenPlayerWindow(
                camera=self.camera, snapshot_dir=self.options.resolved_snapshot_dir(), parent=None
            )
            self._fullscreen_win.closed.connect(self._on_fullscreen_closed)
            self._fullscreen_win.set_osd_options(self.options)
        # Tela cheia de uma câmera: stream principal em resolução original. Entrar na tela cheia é
        # uma troca de layout: reaplica o automático por cima da escolha manual (Issue #47)
        self._manual_override = False
        if self.worker:
            self.worker.set_prefer_substream(False)
        if self.current_frame:
            self._fullscreen_win.set_frame(
                self.current_frame,
                fps=self.stats.fps,
                bitrate=self.stats.bitrate_kbps,
                codec=self.stats.codec
            )
        self._fullscreen_win.set_live(self.is_live(), self.signal_warning())
        self._fullscreen_win.showFullScreen()

    # ------------------------------------------------------------------
    # Câmera fora da tela (Issue #25)
    # ------------------------------------------------------------------

    def set_hidden_timeout(self, minutes: float):
        self._hidden_timer.setInterval(max(1, int(float(minutes) * 60_000)))

    def set_on_screen(self, on_screen: bool):
        """
        Chamado pela grade: fora da tela, a câmera é pausada (volta na hora) e, depois do tempo
        configurado, desconectada para liberar banda; ao voltar, reconecta.
        """
        if on_screen == self._on_screen:
            return
        self._on_screen = on_screen
        if on_screen:
            self._hidden_timer.stop()
            if self._suspended:
                self._start_stream()
            elif self.worker:
                self.worker.resume()  # o perfil HD/SD é reaplicado pela grade logo em seguida
        else:
            if self.worker:
                self.worker.pause()
            self._hidden_timer.start()

    @property
    def is_suspended(self) -> bool:
        return self._suspended

    def _suspend_hidden(self):
        if self._on_screen or self.worker is None:
            return
        self.stop()
        self._suspended = True
        self.status_message = "Desconectada (fora da tela)"

    def _sync_target_size(self):
        """Informa ao worker o tamanho de exibição; tela cheia aberta pede a resolução original."""
        if not self.worker:
            return
        fullscreen = getattr(self, "_fullscreen_win", None)
        if fullscreen is not None and fullscreen.isVisible():
            self.worker.set_target_size(0, 0)
        else:
            self.worker.set_target_size(self.width(), self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_target_size()

    def _on_frame_available(self):
        w = self.worker
        if w is None:
            return
        frame = w.take_latest_frame()
        if frame is not None:
            self._on_frame_received(frame)

    def apply_options(self, options: ViewOptions):
        """
        Aplica opções de Configurações sem reabrir o stream: detecção de movimento, reconexão,
        OSD e pasta de fotos valem na hora (o buffer exige reconexão, decidida pela grade).
        """
        self.options = options
        if self.worker:
            self.worker.motion_detection = options.motion_detection
            self.worker.reconnect_interval_sec = max(0.5, float(options.reconnect_interval_sec))
            self.worker.auto_reconnect = options.auto_reconnect
        if not options.motion_detection and hasattr(self, "lbl_motion"):
            self.lbl_motion.hide()
        fullscreen = getattr(self, "_fullscreen_win", None)
        if fullscreen is not None:
            fullscreen.snapshot_dir = options.resolved_snapshot_dir()
            fullscreen.set_osd_options(options)
        self._render_metrics()

    # ------------------------------------------------------------------
    # Perfil de stream automático (Issue #47)
    # ------------------------------------------------------------------

    def _fullscreen_open(self) -> bool:
        fullscreen = getattr(self, "_fullscreen_win", None)
        return fullscreen is not None and fullscreen.isVisible()

    def apply_auto_stream_profile(self, prefer_substream: bool, layout_changed: bool):
        """
        Chamado pela grade: sub-stream nas miniaturas, principal no 1x1. Uma escolha manual pelo
        botão HD/SD prevalece até a próxima troca de layout.
        """
        self._auto_prefer_sub = prefer_substream
        if layout_changed:
            self._manual_override = False
        # Fora da tela a câmera está pausada: aplica quando voltar, sem reconectar à toa
        if self._fullscreen_open() or not self._on_screen:
            return
        desired = self._manual_prefer_sub if self._manual_override else prefer_substream
        if self.worker and self.worker.prefer_substream != desired:
            self.worker.set_prefer_substream(desired)

    def _on_fullscreen_closed(self):
        # Sair da tela cheia também é troca de layout: volta ao perfil automático da grade
        self._sync_target_size()
        self._manual_override = False
        if self.worker:
            self.worker.set_prefer_substream(self._auto_prefer_sub)

    def _start_stream(self):
        """Inicia a thread de captura e decodificação do fluxo RTSP."""
        if self._manual_override:
            prefer_sub = self._manual_prefer_sub
        else:
            prefer_sub = self._auto_prefer_sub and not self._fullscreen_open()
        self.worker = StreamWorker(
            camera=self.camera,
            hw_accel=self.hw_accel,
            rtsp_transport=self.rtsp_transport,
            buffer_size_ms=self.options.buffer_size_ms,
            prefer_substream=prefer_sub,
            reconnect_interval_sec=self.options.reconnect_interval_sec,
            auto_reconnect=self.options.auto_reconnect,
            motion_detection=self.options.motion_detection,
            parent=self,
        )
        self._suspended = False
        if not self._on_screen:
            self.worker.pause()
            self._hidden_timer.start()
        self._sync_target_size()
        self.worker.frame_available.connect(self._on_frame_available)
        self.worker.stats_updated.connect(self._on_stats_updated)
        self.worker.state_changed.connect(self._on_state_changed)
        self.worker.snapshot_saved.connect(self._on_snapshot_saved)
        self.worker.motion_detected.connect(self._on_motion_detected)
        self.worker.stream_variant.connect(self._on_stream_variant)
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
        self._sync_target_size()  # tela cheia pode ter sido aberta/fechada
        self.current_frame = q_img
        self._last_frame_monotonic = time.monotonic()
        if self._stalled:
            self._stalled = False
            self._set_live(True)
        if hasattr(self, "_fullscreen_win") and self._fullscreen_win and self._fullscreen_win.isVisible():
            self._fullscreen_win.set_frame(
                q_img,
                fps=self.stats.fps,
                bitrate=self.stats.bitrate_kbps,
                codec=self.stats.codec
            )
        self.update()

    def _update_hw_accel_badge(self):
        """Atualiza o selo de aceleração de hardware conforme o estado real do decodificador."""
        if hasattr(self, "lbl_hw_accel"):
            self.lbl_hw_accel.setText("VA-API" if self.stats.hw_accel_active else "CPU")

    def _on_stats_updated(self, stats: StreamStats):
        """Atualiza a exibição de FPS, Bitrate, Codec, aceleração de hardware e QoS no OSD."""
        self.stats = stats
        self._update_hw_accel_badge()
        self._render_metrics()

    def _render_metrics(self):
        """Monta o OSD conforme show_osd_fps / show_osd_bitrate / show_osd_timestamp."""
        stats = self.stats
        parts = []
        if self.options.show_osd_timestamp:
            parts.append(datetime.datetime.now().strftime("%H:%M:%S"))
        if self.options.show_osd_fps:
            parts.append(f"{stats.fps} FPS")
        if self.options.show_osd_bitrate and stats.bitrate_kbps > 0:
            parts.append(f"{int(stats.bitrate_kbps)} kbps")
        parts.append(stats.codec if stats.codec else "H.264")
        if stats.jitter_ms > 0:
            parts.append(f"Jitter: {stats.jitter_ms}ms")
        self.lbl_metrics.setText(" | ".join(parts))

    def _on_motion_detected(self, camera_id: str, is_detected: bool):
        """Atualiza o alerta visual de detecção de movimento em tempo real."""
        if hasattr(self, "lbl_motion"):
            if is_detected and self.options.motion_detection:
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
        if not self.worker:
            return
        new_pref = not self.worker.prefer_substream
        self._manual_override = True
        self._manual_prefer_sub = new_pref
        self.worker.set_prefer_substream(new_pref)
        # O rótulo só muda quando o novo stream conectar (_on_stream_variant)
        self.btn_stream_profile.setText("…")
        self.btn_stream_profile.setToolTip("Trocando de stream...")

    def _on_stream_variant(self, substream_active: bool, substream_requested: bool):
        """Mostra o stream que de fato conectou; avisa se o sub-stream pedido não estava disponível."""
        self.btn_stream_profile.setText("SD" if substream_active else "HD")
        self.btn_stream_profile.setStyleSheet(
            "background-color: rgba(245, 158, 11, 0.9); color: #000000; font-weight: bold; border-radius: 4px; border: none;"
            if substream_active else
            "background-color: rgba(30, 41, 59, 0.9); border: 1px solid #475569; color: #38BDF8; font-weight: 700; border-radius: 4px;"
        )
        if substream_requested and not substream_active:
            msg = "Sub-stream indisponível — exibindo o stream principal (HD)"
            self.btn_stream_profile.setToolTip(msg)
            self._show_notice(msg)
        else:
            self.btn_stream_profile.setToolTip("Alternar entre Stream Principal (HD/4K) e Sub-Stream (SD/Baixa Banda)")

    def _show_notice(self, text: str, duration_ms: int = 6000):
        self.lbl_notice.setText(text)
        self.lbl_notice.show()
        QTimer.singleShot(duration_ms, self.lbl_notice.hide)

    # ------------------------------------------------------------------
    # Perda de sinal (Issue #33)
    # ------------------------------------------------------------------

    STALL_TIMEOUT_SEC = 5.0

    def _set_live(self, live: bool):
        """Marca o início/fim da perda de sinal e propaga para a tela cheia e a árvore."""
        if live:
            self._signal_lost_at = None
            self._publish_display_state("Ao Vivo")
        else:
            if self._signal_lost_at is None and self.current_frame is not None:
                self._signal_lost_at = datetime.datetime.now()
            self._publish_display_state("Sem sinal")
        fullscreen = getattr(self, "_fullscreen_win", None)
        if fullscreen is not None:
            fullscreen.set_live(live, self.signal_warning())
        self.update()

    def _publish_display_state(self, label: str):
        if label != self._display_state:
            self._display_state = label
            self.display_state_changed.emit(self.camera.id, label)

    @property
    def display_state(self) -> str:
        return self._display_state

    def is_live(self) -> bool:
        return self.connection_state == ConnectionState.STREAMING and not self._stalled

    def signal_warning(self) -> str:
        """Aviso sobreposto ao último frame quando o vídeo não é mais ao vivo ("" se ao vivo)."""
        if self.is_live() or self.connection_state == ConnectionState.PAUSED:
            return ""
        if self._signal_lost_at is not None:
            return f"Sem sinal desde {self._signal_lost_at.strftime('%H:%M:%S')}"
        return ""

    def _check_stall(self, now: Optional[float] = None):
        """Watchdog: STREAMING sem frame novo por STALL_TIMEOUT_SEC → perda de sinal."""
        if self.connection_state != ConnectionState.STREAMING or self._last_frame_monotonic is None:
            return
        now = time.monotonic() if now is None else now
        if not self._stalled and now - self._last_frame_monotonic >= self.STALL_TIMEOUT_SEC:
            self._stalled = True
            self.lbl_status.setText("SEM SINAL")
            self.lbl_status.setStyleSheet("color: #F87171; background-color: rgba(153, 27, 27, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
            self._set_live(False)

    def _on_state_changed(self, state: ConnectionState, message: str):
        """Atualiza a indicação visual de conexão."""
        self.connection_state = state
        self.status_message = message
        if state == ConnectionState.STREAMING:
            self._stalled = False
            self._last_frame_monotonic = time.monotonic()
            self._set_live(True)
        elif state == ConnectionState.PAUSED:
            self.stats.hw_accel_active = False
            self._publish_display_state("Pausada")
        elif state == ConnectionState.CONNECTING and self.current_frame is None:
            self.stats.hw_accel_active = False
            self._publish_display_state("Conectando")
        else:
            self.stats.hw_accel_active = False
            self._set_live(False)

        self._update_hw_accel_badge()

        if state == ConnectionState.STREAMING:
            self.lbl_status.setText("AO VIVO")
            self.lbl_status.setStyleSheet("color: #4ADE80; background-color: rgba(22, 101, 52, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        elif state == ConnectionState.CONNECTING or state == ConnectionState.RECONNECTING:
            self.lbl_status.setText("CONECTANDO")
            self.lbl_status.setStyleSheet("color: #FBBF24; background-color: rgba(146, 64, 14, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        elif state == ConnectionState.PAUSED:
            self.lbl_status.setText("PAUSADA")
            self.lbl_status.setStyleSheet("color: #CBD5E1; background-color: rgba(51, 65, 85, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        elif state == ConnectionState.ERROR:
            self.lbl_status.setText("ERRO")
            self.lbl_status.setStyleSheet("color: #F87171; background-color: rgba(153, 27, 27, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")
        else:
            self.lbl_status.setText("OFFLINE")
            self.lbl_status.setStyleSheet("color: #94A3B8; background-color: rgba(51, 65, 85, 0.9); padding: 3px 6px; border-radius: 4px; font-size: 10px; font-weight: bold;")

    def _take_snapshot(self):
        """Dispara a captura de foto do frame atual sanitizando o ID da câmera contra Path Traversal."""
        snap_dir = self.options.resolved_snapshot_dir()
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(self.camera.id))
        filename = f"snap_{safe_id}_{ts}.jpg"
        filepath = str(snap_dir / filename)
        if self.worker and self.worker.isRunning():
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
                warning = self.signal_warning()
                if warning:
                    # Escurece a imagem parada e sobrepõe o aviso: não pode passar por "ao vivo"
                    painter.fillRect(dest_rect, QColor(0, 0, 0, 170))
                    painter.setPen(QColor("#F87171"))
                    painter.setFont(QFont("Arial", 13, QFont.Weight.Bold))
                    painter.drawText(dest_rect, Qt.AlignmentFlag.AlignCenter, f"⚠ {warning}")
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

        if self._restart_after is not None:
            try:
                self._restart_after.finished.disconnect(self._on_previous_session_finished)
            except (RuntimeError, TypeError):
                pass
            self._restart_after = None
        if self.worker:
            w = self.worker
            self.worker = None
            for worker_signal in (w.frame_available, w.stats_updated, w.state_changed, w.snapshot_saved,
                                  w.motion_detected, w.stream_variant):
                try:
                    worker_signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            # A thread pode estar bloqueada na rede por segundos: não esperar por ela (congelaria a
            # interface) nem deixá-la ser destruída junto com este widget (abortaria o processo).
            w.request_stop()
            retire_thread(w)

    def dispose(self):
        """Para timers e stream e desconecta sinais internos para quebrar ciclos de referência."""
        self.stop()
        self._watchdog.stop()
        self._hidden_timer.stop()
        for internal_signal in (
            self._watchdog.timeout,
            self._hidden_timer.timeout,
            self.lbl_title.customContextMenuRequested,
            self.btn_stream_profile.clicked,
            self.btn_snapshot.clicked,
            self.btn_ptz.clicked,
            self.btn_fullscreen.clicked,
            self.btn_truescreen.clicked,
        ):
            try:
                internal_signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def reconnect(self):
        """
        Reabre o stream sem bloquear a interface e sem manter duas sessões RTSP na mesma câmera
        (muitas câmeras Wi-Fi aceitam apenas uma): a nova conexão só começa quando a anterior encerra.
        """
        previous = self.worker
        # Reconexão pedida pelo operador: refaz a busca completa de caminhos/transportes
        self.camera.resolved_stream = None
        self.stop()
        if previous is None or not previous.isRunning():
            self._start_stream()
            return
        self._restart_after = previous
        previous.finished.connect(self._on_previous_session_finished)
        if previous.isFinished():  # encerrou antes de o sinal ser conectado
            self._start_stream_after(previous)

    def _on_previous_session_finished(self):
        self._start_stream_after(self.sender())

    def _start_stream_after(self, finished_session):
        # stop() ou outro reconnect() depois do pedido cancelam este reinício
        if self._restart_after is not None and finished_session is self._restart_after:
            self._restart_after = None
            self._start_stream()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
