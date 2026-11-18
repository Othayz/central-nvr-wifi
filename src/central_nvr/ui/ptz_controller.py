"""
Widget de Controle PTZ (Pan / Tilt / Zoom / Presets) com suporte nativo a câmeras Wi-Fi ONVIF.
Suporte completo a movimentação direcional contínua de 8 eixos, zoom contínuo e posições salvas.
"""
import logging
import threading
from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
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
from central_nvr.core.onvif_client import OnvifClient, OnvifError, OnvifFaultError
from central_nvr.core.ptz_queue import SerialCommandQueue

logger = logging.getLogger(__name__)


class PTZControllerWidget(QWidget):
    """
    Painel de controle motorizado PTZ com D-Pad direcional de 8 eixos,
    ajuste de velocidade de rotação e gerenciamento de posições predefinidas (presets).
    """

    command_executed = Signal(str, str)  # (camera_name, command_str)
    command_failed = Signal(str, str)    # (camera_name, descrição da falha)
    onvif_port_detected = Signal(str, int)  # (camera_id, porta ONVIF que respondeu)
    presets_loaded = Signal(list)        # lista de presets recebidos via ONVIF
    preset_saved = Signal(str, str)      # (preset_name, preset_token)

    # Sinais internos: entregam resultados das threads de fundo com a geração da seleção
    _client_ready = Signal(int, object, object)    # (geração, chave, cliente ou None)
    _presets_ready = Signal(int, list)             # (geração, presets)
    _preset_saved_gen = Signal(int, str, str)      # (geração, nome, token)

    # Intervalo de renovação do ContinuousMove (o Timeout enviado é de 2 s)
    MOVE_RENEWAL_MS = 1000
    STOP_RETRIES = 2

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.camera: Optional[CameraDevice] = None
        self.client: Optional[OnvifClient] = None
        self.ptz_speed: float = 0.5  # Velocidade padrão (50%)

        # Cada seleção incrementa a geração; resultados de gerações antigas são descartados
        self._selection_gen = 0
        self._client_cache: Dict[Tuple[str, str, int, str, str], OnvifClient] = {}
        self._queues: Dict[object, SerialCommandQueue] = {}
        self._renew_args: Optional[Tuple[float, float, float, str]] = None
        self._move_renewal_timer = QTimer(self)
        self._move_renewal_timer.setInterval(self.MOVE_RENEWAL_MS)
        self._move_renewal_timer.timeout.connect(self._renew_move)

        self.presets_loaded.connect(self._on_presets_loaded)
        self.preset_saved.connect(self._on_preset_saved)
        self._client_ready.connect(self._on_client_ready)
        self._presets_ready.connect(self._on_presets_ready)
        self._preset_saved_gen.connect(self._on_preset_saved_gen)

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

        # Controles agrupados: desabilitados sem câmera selecionada ou sem suporte a PTZ
        outer_layout = layout
        self.controls_frame = QWidget()
        layout = QVBoxLayout(self.controls_frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

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

        # 3. Controles de Zoom (In / Out)
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)

        self.btn_zoom_out = QPushButton("🔍− Zoom Out")
        self.btn_zoom_out.setProperty("class", "card-action-btn")
        self.btn_zoom_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_zoom_out.pressed.connect(lambda: self._start_zoom_move(-1.0))
        self.btn_zoom_out.released.connect(self._stop_ptz)
        zoom_row.addWidget(self.btn_zoom_out)

        self.btn_zoom_in = QPushButton("🔍+ Zoom In")
        self.btn_zoom_in.setProperty("class", "card-action-btn")
        self.btn_zoom_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_zoom_in.pressed.connect(lambda: self._start_zoom_move(1.0))
        self.btn_zoom_in.released.connect(self._stop_ptz)
        zoom_row.addWidget(self.btn_zoom_in)

        layout.addLayout(zoom_row)

        # 4. Velocidade de Rotação do Motor (Pan / Tilt / Zoom)
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

        self.controls_frame.setEnabled(False)
        outer_layout.addWidget(self.controls_frame)
        outer_layout.addStretch()

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    # ------------------------------------------------------------------
    # Seleção de câmera e cliente ONVIF (Issue #22)
    # ------------------------------------------------------------------

    @staticmethod
    def _client_key(camera: CameraDevice) -> Tuple[str, str, int, str, str]:
        """Chave do cache: muda se a câmera for editada (IP, porta ou credenciais)."""
        return (camera.id, camera.ip, int(camera.port), camera.username or "", camera.password or "")

    def set_camera(self, camera: Optional[CameraDevice]):
        """Define a câmera alvo para envio de comandos PTZ com auto-detecção de porta ONVIF."""
        self._stop_move_renewal()
        self._selection_gen += 1
        gen = self._selection_gen
        self.camera = camera
        self.client = None
        self.combo_presets.clear()

        if not camera:
            self.lbl_cam_target.setText("Nenhuma Câmera Selecionada")
            self.controls_frame.setEnabled(False)
            return

        ptz_status = " (Suporta PTZ)" if camera.has_ptz else " (PTZ Desativado)"
        self.lbl_cam_target.setText(f"{camera.name}{ptz_status}")
        self.controls_frame.setEnabled(bool(camera.has_ptz))

        key = self._client_key(camera)
        cached = self._client_cache.get(key)
        if cached is not None:
            self._adopt_detected_port(cached)
            self.client = cached
            self._request_presets(gen, key, cached)
            return

        def _init_client():
            client = self._probe_client(camera)
            # Entrega na thread da interface; o slot descarta resultados de seleções antigas
            self._client_ready.emit(gen, key, client)

        threading.Thread(target=_init_client, daemon=True).start()

    def _probe_client(self, camera: CameraDevice) -> Optional[OnvifClient]:
        """
        Executa em thread de fundo: encontra a porta que responde ONVIF de verdade (Issue #44).
        Uma porta só é aceita com perfis reais em GetProfiles. Não altera `camera`.
        """
        ports_to_try = [camera.port]
        for fallback in (5000, 80):
            if fallback not in ports_to_try:
                ports_to_try.append(fallback)

        for p in ports_to_try:
            c = OnvifClient(
                ip=camera.ip,
                port=p,
                username=camera.username,
                password=camera.password,
                timeout=2.5,
            )
            try:
                c.get_capabilities()
            except OnvifFaultError:
                pass  # Sem GetCapabilities: tenta os endpoints padrão
            except OnvifError as e:
                logger.debug(f"ONVIF não respondeu em {camera.ip}:{p}: {e}")
                continue
            try:
                if c.get_profiles():
                    return c
            except OnvifError as e:
                logger.debug(f"GetProfiles falhou em {camera.ip}:{p}: {e}")
        return None

    def _on_client_ready(self, gen: int, key: object, client: object):
        """Slot (thread da interface): aceita o cliente só se a seleção ainda for a mesma."""
        if client is not None:
            self._client_cache[key] = client
        if gen != self._selection_gen or self.camera is None:
            return
        if client is None:
            self.controls_frame.setEnabled(False)
            self.command_failed.emit(self.camera.name, "PTZ indisponível: nenhuma porta ONVIF respondeu")
            return

        self._adopt_detected_port(client)
        self.client = client
        self._request_presets(gen, key, client)

    def _adopt_detected_port(self, client: OnvifClient):
        """
        Porta detectada diferente da configurada: atualizada aqui (thread da interface) e persistida
        por quem ouve o sinal. Vale também para cliente vindo do cache, criado numa seleção anterior
        que mudou antes de o probe responder.
        """
        if self.camera is None or client.port == self.camera.port:
            return
        self.camera.port = client.port
        self._client_cache[self._client_key(self.camera)] = client
        self.onvif_port_detected.emit(self.camera.id, int(client.port))

    def _queue_for(self, key: object) -> SerialCommandQueue:
        q = self._queues.get(key)
        if q is None:
            q = SerialCommandQueue(name=str(key[0]) if isinstance(key, tuple) else "ptz")
            self._queues[key] = q
        return q

    def shutdown(self):
        """Encerra as filas de comandos (chamado ao fechar a janela)."""
        self._stop_move_renewal()
        for q in self._queues.values():
            q.shutdown()
        self._queues.clear()

    # ------------------------------------------------------------------
    # Envio serial de comandos (Issue #23)
    # ------------------------------------------------------------------

    def _submit(self, action: Callable[[OnvifClient], object], success_msg: Optional[str], failure_msg: str,
                retries: int = 0) -> bool:
        """
        Enfileira um comando para a câmera selecionada. Os comandos de uma câmera rodam em série,
        na ordem de envio. `success_msg=None` não registra sucesso (renovações do movimento).
        """
        if not self.camera or not self.client:
            return False
        camera_name = self.camera.name
        client = self.client
        key = self._client_key(self.camera)

        def _run():
            ok = False
            for _ in range(retries + 1):
                try:
                    ok = bool(action(client))
                except Exception as e:
                    logger.error(f"Erro no comando PTZ ({camera_name}): {e}")
                    ok = False
                if ok:
                    break
            if ok:
                if success_msg:
                    self.command_executed.emit(camera_name, success_msg)
            else:
                self.command_failed.emit(camera_name, failure_msg)

        self._queue_for(key).submit(_run)
        return True

    def _on_speed_changed(self, value: int):
        self.ptz_speed = value / 10.0
        self.lbl_speed_val.setText(f"{int(self.ptz_speed * 100)}%")

    def _send_move(self, pan: float, tilt: float, zoom: float, success_msg: Optional[str], failure_msg: str):
        self._submit(
            lambda c: c.ptz_continuous_move(pan_speed=pan, tilt_speed=tilt, zoom_speed=zoom),
            success_msg,
            failure_msg,
        )

    def _begin_move(self, pan: float, tilt: float, zoom: float, success_msg: str, failure_msg: str):
        if not self.camera or not self.client:
            return
        self._send_move(pan, tilt, zoom, success_msg, failure_msg)
        # O ContinuousMove leva Timeout: enquanto o botão estiver pressionado, renova o comando
        self._renew_args = (pan, tilt, zoom, failure_msg)
        self._move_renewal_timer.start()

    def _renew_move(self):
        if self._renew_args is None:
            self._move_renewal_timer.stop()
            return
        pan, tilt, zoom, failure_msg = self._renew_args
        self._send_move(pan, tilt, zoom, None, failure_msg)

    def _stop_move_renewal(self):
        self._move_renewal_timer.stop()
        self._renew_args = None

    def _start_ptz_move(self, pan: float, tilt: float):
        """Envia comando ONVIF ContinuousMove para girar a câmera."""
        p_speed = pan * self.ptz_speed
        t_speed = tilt * self.ptz_speed
        self._begin_move(
            p_speed, t_speed, 0.0,
            f"Movimento PTZ: Pan={p_speed:.2f} Tilt={t_speed:.2f}",
            f"FALHA no movimento PTZ (Pan={p_speed:.2f} Tilt={t_speed:.2f})",
        )

    def _start_zoom_move(self, direction: float):
        """Envia comando ONVIF ContinuousMove para zoom in (+1.0) ou zoom out (-1.0)."""
        z_speed = direction * self.ptz_speed
        action = "Zoom In" if direction > 0 else "Zoom Out"
        self._begin_move(
            0.0, 0.0, z_speed,
            f"Comando PTZ: {action} ({z_speed:+.2f})",
            f"FALHA no comando PTZ: {action}",
        )

    def _stop_ptz(self):
        """Envia comando ONVIF Stop para parar o motor; reenvia em caso de falha."""
        self._stop_move_renewal()
        self._submit(
            lambda c: c.ptz_stop(),
            "PTZ Parado",
            "FALHA ao parar o PTZ — a câmera para sozinha ao fim do Timeout do movimento",
            retries=self.STOP_RETRIES,
        )

    def _request_presets(self, gen: int, key: object, client: OnvifClient):
        """Carrega os presets da câmera na fila serial dela."""

        def _run():
            try:
                presets = client.ptz_get_presets()
            except Exception as e:
                logger.debug(f"Presets ONVIF não disponíveis: {e}")
                return
            if presets:
                self._presets_ready.emit(gen, presets)

        self._queue_for(key).submit(_run)

    def _on_presets_ready(self, gen: int, presets: list):
        """Atualiza a combobox de presets no thread principal, se a seleção não mudou."""
        if gen != self._selection_gen:
            return
        self.presets_loaded.emit(presets)

    def _on_presets_loaded(self, presets: list):
        """Atualiza a combobox de presets no thread principal do Qt."""
        self.combo_presets.clear()
        for p in presets:
            self.combo_presets.addItem(p["name"], p["token"])

    def _goto_preset(self):
        """Move para a posição predefinida selecionada."""
        if self.combo_presets.count() == 0:
            return
        token = self.combo_presets.currentData()
        if not token:
            return
        name = self.combo_presets.currentText()
        self._submit(
            lambda c: c.ptz_goto_preset(str(token)),
            f"Indo para Posição: {name}",
            f"FALHA ao ir para a posição: {name}",
        )

    def _save_preset(self):
        """Salva a posição atual como novo preset na câmera."""
        if not self.camera or not self.client:
            return
        name, ok = QInputDialog.getText(self, "Salvar Posição PTZ", "Nome da posição predefinida:")
        if not (ok and name):
            return
        gen = self._selection_gen

        def _action(c: OnvifClient) -> bool:
            token = c.ptz_set_preset(name)
            if not token:
                return False
            self._preset_saved_gen.emit(gen, name, str(token))
            return True

        self._submit(_action, f"Posição '{name}' salva!", f"FALHA ao salvar a posição '{name}'")

    def _on_preset_saved_gen(self, gen: int, name: str, token: str):
        if gen != self._selection_gen:
            return
        self.preset_saved.emit(name, token)

    def _on_preset_saved(self, name: str, token: str):
        """Adiciona novo preset à combobox no thread principal do Qt."""
        self.combo_presets.addItem(name, token)
