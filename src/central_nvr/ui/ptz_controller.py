"""
Widget de Controle PTZ (Pan / Tilt / Presets) com suporte nativo a câmeras Wi-Fi ONVIF.
Design limpo, sem botões de zoom supérfluos para câmeras de lente fixa e com botão PARAR desobstruído.
"""
import logging
import threading
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import CameraDevice
from central_nvr.core.onvif_client import OnvifClient

logger = logging.getLogger(__name__)


class PTZControllerWidget(QWidget):
    """
    Painel de controle motorizado PTZ com D-Pad direcional de 8 eixos,
    ajuste de velocidade de rotação e gerenciamento de posições predefinidas (presets).
    """

    command_executed = Signal(str, str)  # (camera_name, command_str)
    presets_loaded = Signal(list)        # lista de presets recebidos via ONVIF
    preset_saved = Signal(str, str)      # (preset_name, preset_token)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.camera: Optional[CameraDevice] = None
        self.client: Optional[OnvifClient] = None
        self.ptz_speed: float = 0.5  # Velocidade padrão (50%)

        self.presets_loaded.connect(self._on_presets_loaded)
        self.preset_saved.connect(self._on_preset_saved)

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

        self.lbl_cam_target = QLabel("Nenhuma Câmera Selecionada")
        self.lbl_cam_target.setObjectName("cardAccentTitle")
        self.lbl_cam_target.setWordWrap(True)
        h_layout.addWidget(self.lbl_cam_target)

        layout.addWidget(header_card)

        # 2. D-Pad Direcional (8 Eixos + Parar)
        lbl_dpad_sec = QLabel("Controle Direcional (Pan / Tilt)")
        lbl_dpad_sec.setObjectName("sectionTitle")
        layout.addWidget(lbl_dpad_sec)

        dpad_frame = QFrame()
        dpad_frame.setObjectName("dpadFrame")
        dpad_grid = QGridLayout(dpad_frame)
        dpad_grid.setContentsMargins(10, 10, 10, 10)
        dpad_grid.setSpacing(6)

        # Mapeamento dos 8 eixos de rotação (Pan, Tilt)
        directions = [
            ("◤", 0, 0, -1.0, 1.0),
            ("▲", 0, 1, 0.0, 1.0),
            ("◥", 0, 2, 1.0, 1.0),
            ("◀", 1, 0, -1.0, 0.0),
            ("PARAR", 1, 1, 0.0, 0.0),
            ("▶", 1, 2, 1.0, 0.0),
            ("◣", 2, 0, -1.0, -1.0),
            ("▼", 2, 1, 0.0, -1.0),
            ("◢", 2, 2, 1.0, -1.0),
        ]

        for label, r, c, pan, tilt in directions:
            btn = QPushButton(label)
            btn.setFixedSize(58, 46)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

            if label == "PARAR":
                btn.setProperty("class", "dpad-stop-btn")
                btn.setStyleSheet("QPushButton { padding: 0px; font-size: 11px; font-weight: bold; }")
                btn.clicked.connect(self._stop_ptz)
            else:
                btn.setProperty("class", "dpad-btn")
                btn.setStyleSheet("QPushButton { padding: 0px; font-size: 14px; font-weight: bold; }")
                btn.pressed.connect(lambda p=pan, t=tilt: self._start_ptz_move(p, t))
                btn.released.connect(self._stop_ptz)

            dpad_grid.addWidget(btn, r, c)

        layout.addWidget(dpad_frame)

        # 3. Velocidade de Rotação do Motor (Pan / Tilt)
        speed_frame = QFrame()
        speed_frame.setObjectName("cardFrame")
        sp_layout = QVBoxLayout(speed_frame)
        sp_layout.setContentsMargins(10, 10, 10, 10)
        sp_layout.setSpacing(6)

        sp_header = QHBoxLayout()
        lbl_sp_title = QLabel("Velocidade do Motor:")
        lbl_sp_title.setObjectName("mutedLabel")
        sp_header.addWidget(lbl_sp_title)

        self.lbl_speed_val = QLabel("50%")
        self.lbl_speed_val.setObjectName("cardAccentTitle")
        sp_header.addWidget(self.lbl_speed_val, alignment=Qt.AlignmentFlag.AlignRight)
        sp_layout.addLayout(sp_header)

        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(1, 10)
        self.slider_speed.setValue(5)
        self.slider_speed.valueChanged.connect(self._on_speed_changed)
        sp_layout.addWidget(self.slider_speed)

        layout.addWidget(speed_frame)

        # 4. Seção de Presets (Posições Salvas de Rotação)
        lbl_presets_sec = QLabel("Posições Salvas (Presets)")
        lbl_presets_sec.setObjectName("sectionTitle")
        layout.addWidget(lbl_presets_sec)

        preset_frame = QFrame()
        preset_frame.setObjectName("cardFrame")
        p_layout = QVBoxLayout(preset_frame)
        p_layout.setContentsMargins(10, 10, 10, 10)
        p_layout.setSpacing(8)

        self.combo_presets = QComboBox()
        self.combo_presets.addItem("Posição 1 (Início)", "1")
        p_layout.addWidget(self.combo_presets)

        btn_preset_row = QHBoxLayout()
        btn_preset_row.setSpacing(6)

        btn_goto = QPushButton("Ir para Posição")
        btn_goto.setProperty("class", "primary-btn")
        btn_goto.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_goto.clicked.connect(self._goto_preset)
        btn_preset_row.addWidget(btn_goto)

        btn_save_preset = QPushButton("+ Salvar Atual")
        btn_save_preset.setProperty("class", "card-action-btn")
        btn_save_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_preset.clicked.connect(self._save_preset)
        btn_preset_row.addWidget(btn_save_preset)

        p_layout.addLayout(btn_preset_row)
        layout.addWidget(preset_frame)

        layout.addStretch()

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def set_camera(self, camera: Optional[CameraDevice]):
        """Define a câmera alvo para envio de comandos PTZ com auto-detecção de porta ONVIF."""
        self.camera = camera
        if camera:
            ptz_status = " (Suporta PTZ)" if camera.has_ptz else " (PTZ Desativado)"
            self.lbl_cam_target.setText(f"{camera.name}{ptz_status}")

            def _init_client():
                ports_to_try = [camera.port]
                if 5000 not in ports_to_try:
                    ports_to_try.append(5000)
                if 80 not in ports_to_try:
                    ports_to_try.append(80)

                active_client = None
                for p in ports_to_try:
                    try:
                        c = OnvifClient(
                            ip=camera.ip,
                            port=p,
                            username=camera.username,
                            password=camera.password,
                            timeout=2.5,
                        )
                        c.get_capabilities()
                        c.get_profiles()
                        if c.media_service_url or c.ptz_service_url:
                            active_client = c
                            if p != camera.port:
                                camera.port = p
                            break
                    except Exception:
                        continue

                self.client = active_client
                if self.client:
                    self._load_camera_presets()

            threading.Thread(target=_init_client, daemon=True).start()
        else:
            self.lbl_cam_target.setText("Nenhuma Câmera Selecionada")
            self.client = None

    def _on_speed_changed(self, value: int):
        self.ptz_speed = value / 10.0
        self.lbl_speed_val.setText(f"{int(self.ptz_speed * 100)}%")

    def _start_ptz_move(self, pan: float, tilt: float):
        """Envia comando ONVIF ContinuousMove para girar a câmera."""
        if not self.camera or not self.client:
            return

        p_speed = pan * self.ptz_speed
        t_speed = tilt * self.ptz_speed

        def _send():
            try:
                self.client.ptz_continuous_move(pan_speed=p_speed, tilt_speed=t_speed, zoom_speed=0.0)
                self.command_executed.emit(self.camera.name, f"Movimento PTZ: Pan={p_speed:.2f} Tilt={t_speed:.2f}")
            except Exception as e:
                logger.error(f"Erro no comando PTZ: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def _stop_ptz(self):
        """Envia comando ONVIF Stop para parar o motor imediatamente."""
        if not self.camera or not self.client:
            return

        def _send():
            try:
                self.client.ptz_stop()
                self.command_executed.emit(self.camera.name, "PTZ Parado")
            except Exception as e:
                logger.error(f"Erro ao parar PTZ: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def _load_camera_presets(self):
        """Carrega os presets salvos da câmera via ONVIF em thread de fundo."""
        if not self.client:
            return
        try:
            presets = self.client.ptz_get_presets()
            if presets:
                self.presets_loaded.emit(presets)
        except Exception as e:
            logger.debug(f"Presets ONVIF não disponíveis: {e}")

    def _on_presets_loaded(self, presets: list):
        """Atualiza a combobox de presets no thread principal do Qt."""
        self.combo_presets.clear()
        for p in presets:
            self.combo_presets.addItem(p["name"], p["token"])

    def _goto_preset(self):
        """Move para a posição predefinida selecionada."""
        if not self.client or self.combo_presets.count() == 0:
            return
        token = self.combo_presets.currentData() or str(self.combo_presets.currentIndex() + 1)
        name = self.combo_presets.currentText()

        def _send():
            try:
                self.client.ptz_goto_preset(str(token))
                self.command_executed.emit(self.camera.name, f"Indo para Posição: {name}")
            except Exception as e:
                logger.error(f"Erro ao ir para preset: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def _save_preset(self):
        """Salva a posição atual como novo preset na câmera."""
        if not self.camera or not self.client:
            return
        name, ok = QInputDialog.getText(self, "Salvar Posição PTZ", "Nome da posição predefinida:")
        if ok and name:
            def _send():
                try:
                    token = self.client.ptz_set_preset(name)
                    self.preset_saved.emit(name, token)
                    self.command_executed.emit(self.camera.name, f"Posição '{name}' salva!")
                except Exception as e:
                    logger.error(f"Erro ao salvar preset: {e}")

            threading.Thread(target=_send, daemon=True).start()

    def _on_preset_saved(self, name: str, token: str):
        """Adiciona novo preset à combobox no thread principal do Qt."""
        self.combo_presets.addItem(name, token)
