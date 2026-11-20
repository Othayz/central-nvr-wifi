"""
Painel Integrado de Gravações, Snapshots e Histórico de Eventos com suporte a temas.
"""
import datetime
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional, Union

from PySide6.QtCore import QObject, Qt, QThread, Signal
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
from central_nvr.core.onvif_client import OnvifClient
from central_nvr.core.threads import retire_thread
from central_nvr.ui.timeline_bar import TimelineBarWidget

logger = logging.getLogger(__name__)

_BTN_SYNC_SD_LABEL = "💾 Cartão SD (Profile G)"


class EdgeStorageWorker(QThread):
    """Consulta gravações locais (ONVIF Profile G) do Dispositivo Cadastrado fora da thread da UI."""

    query_finished = Signal(object, object, str)  # (summary, recordings, error_message)

    def __init__(self, camera: CameraDevice, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._ip = camera.ip
        self._port = camera.port
        self._username = camera.username
        self._password = camera.password
        self.camera_name = camera.name

    def run(self):
        try:
            client = OnvifClient(
                ip=self._ip,
                port=self._port,
                username=self._username,
                password=self._password,
                timeout=3.0,
            )
            client.get_capabilities()
            summary = client.get_recording_summary()
            recs = client.find_recordings(max_matches=10)
            self.query_finished.emit(summary, recs, "")
        except Exception as e:
            self.query_finished.emit(None, None, str(e))


class PlaybackWidget(QWidget):
    """
    Painel lateral integrado de visualização de linha do tempo e reprodução de vídeos.
    """

    playback_requested = Signal(str, str)  # (camera_name, time_str)

    def __init__(
        self,
        snapshot_dir: Optional[Union[Path, str]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.camera: Optional[CameraDevice] = None
        self._snapshot_dir: Optional[Path] = (
            Path(snapshot_dir).expanduser() if snapshot_dir else None
        )
        self._edge_worker: Optional[EdgeStorageWorker] = None
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

        self.btn_sync_sd = QPushButton(_BTN_SYNC_SD_LABEL)
        self.btn_sync_sd.setProperty("class", "card-action-btn")
        self.btn_sync_sd.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync_sd.setToolTip("Consultar gravações mantidas no armazenamento local (Cartão SD) da câmera")
        self.btn_sync_sd.clicked.connect(self._check_edge_storage)
        ctrl_layout.addWidget(self.btn_sync_sd)

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

    def resolved_snapshot_dir(self) -> Path:
        """Retorna a pasta de snapshots configurada ou o diretório padrão de dados."""
        return self._snapshot_dir if self._snapshot_dir is not None else get_data_dir() / "snapshots"

    def set_snapshot_dir(self, snapshot_dir: Optional[Union[Path, str]]) -> None:
        """Atualiza a pasta de snapshots monitorada pelo painel e recarrega a lista."""
        self._snapshot_dir = Path(snapshot_dir).expanduser() if snapshot_dir else None
        self.refresh_recordings()

    def refresh_recordings(self):
        """Varre o diretório de dados para listar vídeos e fotos gravados."""
        self.list_recordings.clear()
        base_dir = get_data_dir()
        rec_dir = base_dir / "recordings"
        snap_dir = self.resolved_snapshot_dir()

        files = []
        try:
            rec_dir.mkdir(parents=True, exist_ok=True)
            for p in rec_dir.glob("*.mp4"):
                st = p.stat()
                files.append((st.st_mtime, st.st_size, p, "Vídeo"))
        except OSError as e:
            logger.warning(f"Não foi possível listar gravações em '{rec_dir}': {e}")

        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            for p in snap_dir.glob("*.jpg"):
                st = p.stat()
                files.append((st.st_mtime, st.st_size, p, "Foto"))
        except OSError as e:
            logger.warning(f"Não foi possível listar snapshots em '{snap_dir}': {e}")

        # Ordenar pelos mais recentes
        files.sort(key=lambda x: x[0], reverse=True)

        if not files:
            empty_item = QListWidgetItem("Nenhuma gravação ou foto salva no momento.")
            empty_item.setForeground(Qt.GlobalColor.gray)
            self.list_recordings.addItem(empty_item)
            return

        for mtime, size_bytes, fpath, ftype in files[:20]:
            dt = datetime.datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S")
            size_mb = size_bytes / (1024 * 1024)
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
        """Consulta de forma assíncrona o serviço ONVIF Profile G do Dispositivo Cadastrado."""
        if not self.camera:
            QMessageBox.information(self, "Edge Storage", "Selecione uma câmera para consultar gravações no cartão SD.")
            return
        if self._edge_worker is not None:
            return

        self.btn_sync_sd.setEnabled(False)
        self.btn_sync_sd.setText("Consultando SD...")

        worker = EdgeStorageWorker(self.camera, parent=self)
        self._edge_worker = worker
        worker.query_finished.connect(self._on_edge_storage_result)
        worker.start()

    def _on_edge_storage_result(self, summary, recs, error_msg: str):
        """Recebe o resultado da consulta ao Cartão SD na thread principal e atualiza a UI."""
        worker, self._edge_worker = self._edge_worker, None
        camera_name = worker.camera_name if worker is not None else (self.camera.name if self.camera else "")
        if worker is not None:
            try:
                worker.query_finished.disconnect(self._on_edge_storage_result)
            except (RuntimeError, TypeError):
                pass
            retire_thread(worker)

        self.btn_sync_sd.setEnabled(True)
        self.btn_sync_sd.setText(_BTN_SYNC_SD_LABEL)

        if error_msg:
            QMessageBox.warning(
                self,
                "ONVIF Profile G",
                f"Falha na consulta ao serviço de Edge Storage: {error_msg}",
            )
            return

        if recs:
            lines = [f"Gravações encontradas no cartão SD ({camera_name}):", ""]
            for i, r in enumerate(recs, 1):
                lines.append(f"{i}. Token: {r.get('token')}")
                lines.append(f"   Replay RTSP: {r.get('replay_uri') or 'Nativo'}")
            QMessageBox.information(self, "ONVIF Profile G - Cartão SD", "\n".join(lines))
        elif summary and summary.get("num_recordings", 0) > 0:
            msg = (
                f"A câmera reportou {summary['num_recordings']} gravações no cartão SD.\n"
                f"Histórico de: {summary.get('earliest_time') or 'N/A'} até {summary.get('latest_time') or 'N/A'}."
            )
            QMessageBox.information(self, "ONVIF Profile G - Cartão SD", msg)
        else:
            QMessageBox.information(
                self,
                "ONVIF Profile G - Cartão SD",
                f"Nenhuma gravação no cartão SD ou o dispositivo ({camera_name}) não expõe ONVIF Profile G.",
            )

    def _cancel_edge_storage(self):
        """Desvincula e aposenta uma consulta Profile G em andamento sem bloquear a interface."""
        worker, self._edge_worker = self._edge_worker, None
        if worker is None:
            return
        try:
            worker.query_finished.disconnect(self._on_edge_storage_result)
        except (RuntimeError, TypeError):
            pass
        worker.requestInterruption()
        retire_thread(worker)

    def closeEvent(self, event):
        self._cancel_edge_storage()
        super().closeEvent(event)
