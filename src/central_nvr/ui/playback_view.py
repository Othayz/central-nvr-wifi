"""
Painel Integrado de Gravações, Snapshots e Histórico de Eventos com suporte a temas.
"""
import datetime
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import get_data_dir
from central_nvr.ui.timeline_bar import TimelineBarWidget

logger = logging.getLogger(__name__)


class PlaybackWidget(QWidget):
    """
    Painel lateral integrado de visualização de linha do tempo e reprodução de vídeos.
    """

    playback_requested = Signal(str, str)  # (camera_name, time_str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.camera: Optional[CameraDevice] = None
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # 1. Card com Indicador da Câmera Ativa
        header_card = QFrame()
        header_card.setObjectName("cardFrame")
        h_layout = QVBoxLayout(header_card)
        h_layout.setContentsMargins(10, 10, 10, 10)
        h_layout.setSpacing(4)

        lbl_head = QLabel("CÂMERA SELECIONADA:")
        lbl_head.setObjectName("mutedLabel")
        h_layout.addWidget(lbl_head)

        self.lbl_cam_name = QLabel("Nenhuma Câmera Selecionada")
        self.lbl_cam_name.setObjectName("cardAccentTitle")
        self.lbl_cam_name.setWordWrap(True)
        h_layout.addWidget(self.lbl_cam_name)

        layout.addWidget(header_card)

        # 2. Seção de Linha do Tempo (24 Horas)
        lbl_tl_title = QLabel("Linha do Tempo (Hoje)")
        lbl_tl_title.setObjectName("sectionTitle")
        layout.addWidget(lbl_tl_title)

        self.timeline = TimelineBarWidget()
        layout.addWidget(self.timeline)

        # 3. Controles de Reprodução e Pastas
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(6)

        btn_play = QPushButton("▶ Reproduzir")
        btn_play.setProperty("class", "primary-btn")
        btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_play.clicked.connect(self._on_play_clicked)
        ctrl_layout.addWidget(btn_play)

        btn_open_folder = QPushButton("📁 Abrir Pasta")
        btn_open_folder.setProperty("class", "card-action-btn")
        btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open_folder.clicked.connect(self._open_media_folder)
        ctrl_layout.addWidget(btn_open_folder)

        btn_sync_sd = QPushButton("💾 Cartão SD (Profile G)")
        btn_sync_sd.setProperty("class", "card-action-btn")
        btn_sync_sd.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_sync_sd.setToolTip("Consultar gravações mantidas no armazenamento local (Cartão SD) da câmera")
        btn_sync_sd.clicked.connect(self._check_edge_storage)
        ctrl_layout.addWidget(btn_sync_sd)

        layout.addLayout(ctrl_layout)

        # 4. Lista de Últimas Gravações e Fotos Salvas
        header_rec_layout = QHBoxLayout()
        lbl_rec_title = QLabel("Últimas Gravações e Fotos")
        lbl_rec_title.setObjectName("sectionTitle")
        header_rec_layout.addWidget(lbl_rec_title)

        header_rec_layout.addStretch()

        btn_refresh = QPushButton("🔄 Atualizar")
        btn_refresh.setProperty("class", "link-btn")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh_recordings)
        header_rec_layout.addWidget(btn_refresh)

        layout.addLayout(header_rec_layout)

        self.list_recordings = QListWidget()
        self.list_recordings.setMinimumHeight(180)
        self.list_recordings.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_recordings)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        self.refresh_recordings()

    def set_camera(self, camera: Optional[CameraDevice]):
        """Atualiza a câmera selecionada no painel de gravações."""
        self.camera = camera
        if camera:
            self.lbl_cam_name.setText(f"{camera.name} ({camera.ip})")
            self.timeline.set_camera_name(camera.name)
        else:
            self.lbl_cam_name.setText("Nenhuma Câmera Selecionada")
            self.timeline.set_camera_name("Geral")
        self.refresh_recordings()

    def refresh_recordings(self):
        """Varre o diretório de dados para listar vídeos e fotos gravados."""
        self.list_recordings.clear()
        base_dir = get_data_dir()
        rec_dir = base_dir / "recordings"
        snap_dir = base_dir / "snapshots"

        rec_dir.mkdir(parents=True, exist_ok=True)
        snap_dir.mkdir(parents=True, exist_ok=True)

        files = []
        for p in rec_dir.glob("*.mp4"):
            files.append((p.stat().st_mtime, p, "Vídeo"))
        for p in snap_dir.glob("*.jpg"):
            files.append((p.stat().st_mtime, p, "Foto"))

        # Ordenar pelos mais recentes
        files.sort(key=lambda x: x[0], reverse=True)

        if not files:
            empty_item = QListWidgetItem("Nenhuma gravação ou foto salva no momento.")
            empty_item.setForeground(Qt.GlobalColor.gray)
            self.list_recordings.addItem(empty_item)
            return

        for mtime, fpath, ftype in files[:20]:
            dt = datetime.datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S")
            size_mb = fpath.stat().st_size / (1024 * 1024)
            icon = "🎥" if ftype == "Vídeo" else "📷"
            item_text = f"{icon} [{ftype}] {fpath.name}\n     Data: {dt} | Tamanho: {size_mb:.2f} MB"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, str(fpath))
            self.list_recordings.addItem(item)

    def _on_item_double_clicked(self, item: QListWidgetItem):
        fpath = item.data(Qt.ItemDataRole.UserRole)
        if fpath and os.path.exists(fpath):
            try:
                subprocess.Popen(["xdg-open", fpath])
            except Exception as e:
                QMessageBox.warning(self, "Aviso", f"Não foi possível abrir o arquivo: {e}")

    def _open_media_folder(self):
        base_dir = get_data_dir()
        try:
            subprocess.Popen(["xdg-open", str(base_dir)])
        except Exception as e:
            QMessageBox.warning(self, "Aviso", f"Não foi possível abrir a pasta de mídia: {e}")

    def _on_play_clicked(self):
        progress = self.timeline.playhead_progress
        hours = int(progress * 24)
        minutes = int((progress * 24 * 60) % 60)
        time_str = f"{hours:02d}:{minutes:02d}"
        cam_name = self.camera.name if self.camera else "Câmera"
        self.playback_requested.emit(cam_name, time_str)

    def _check_edge_storage(self):
        """Consulta o serviço ONVIF Profile G da câmera para listar gravações locais no cartão SD."""
        if not self.camera:
            QMessageBox.information(self, "Edge Storage", "Selecione uma câmera para consultar gravações no cartão SD.")
            return

        from central_nvr.core.onvif_client import OnvifClient
        try:
            client = OnvifClient(
                ip=self.camera.ip,
                port=self.camera.port,
                username=self.camera.username,
                password=self.camera.password,
                timeout=3.0,
            )
            client.get_capabilities()
            summary = client.get_recording_summary()
            recs = client.find_recordings(max_matches=10)

            if recs:
                lines = [f"Gravações encontradas no cartão SD ({self.camera.name}):", ""]
                for i, r in enumerate(recs, 1):
                    lines.append(f"{i}. Token: {r.get('token')}")
                    lines.append(f"   Replay RTSP: {r.get('replay_uri') or 'Nativo'}")
                QMessageBox.information(self, "ONVIF Profile G - Cartão SD", "\n".join(lines))
            elif summary.get("num_recordings", 0) > 0:
                msg = (
                    f"A câmera reportou {summary['num_recordings']} gravações no cartão SD.\n"
                    f"Histórico de: {summary.get('earliest_time', 'N/A')} até {summary.get('latest_time', 'N/A')}."
                )
                QMessageBox.information(self, "ONVIF Profile G - Cartão SD", msg)
            else:
                QMessageBox.information(
                    self,
                    "ONVIF Profile G - Cartão SD",
                    f"Nenhuma gravação no cartão SD ou o dispositivo ({self.camera.name}) não expõe ONVIF Profile G."
                )
        except Exception as e:
            QMessageBox.warning(self, "ONVIF Profile G", f"Falha na consulta ao serviço de Edge Storage: {e}")
