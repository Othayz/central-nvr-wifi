"""
Diálogo de Configurações Gerais da Central NVR WiFi.
Permite configurar decodificação por hardware (VA-API), caminhos de gravação, rede e atualizações.
"""
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from central_nvr import __version__
from central_nvr.core.camera import DEFAULT_RTSP_TRANSPORT
from central_nvr.core.config import ConfigManager
from central_nvr.ui.update_dialog import UpdateDialog


class SettingsDialog(QDialog):
    """
    Diálogo modal de preferências da aplicação.
    """

    def __init__(self, config_mgr: ConfigManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self._loaded_hw_accel: Optional[str] = None
        self._loaded_rtsp_transport: Optional[str] = None
        self._unrepresented_hw_accel: Optional[str] = None
        self.setWindowTitle("Configurações do Sistema - Central NVR WiFi")
        self.resize(580, 520)

        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Usar ScrollArea para garantir que caiba confortavelmente em qualquer resolução
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # 0. Grupo Aparência
        app_group = QGroupBox("Aparência e Interface")
        app_form = QFormLayout(app_group)

        self.combo_theme = QComboBox()
        self.combo_theme.addItem("Tema Escuro (Dark Mode)", "dark")
        self.combo_theme.addItem("Tema Claro (Light Mode)", "light")
        app_form.addRow("Tema Visual:", self.combo_theme)
        layout.addWidget(app_group)

        # 1. Grupo Streaming e Hardware
        hw_group = QGroupBox("Vídeo e Aceleração por Hardware")
        hw_form = QFormLayout(hw_group)

        self.combo_hw = QComboBox()
        self.combo_hw.addItem("VA-API (Linux Hardware - Intel/AMD)", "vaapi")
        self.combo_hw.addItem("CPU / Software (Universal)", "cpu")
        self.combo_hw.addItem("NVIDIA CUDA / NVDEC", "cuda")
        self.combo_hw.currentIndexChanged.connect(self._on_hw_selection_changed)
        self.combo_hw.activated.connect(self._on_hw_selection_changed)
        hw_form.addRow("Decodificação por GPU:", self.combo_hw)

        self.combo_proto = QComboBox()
        self.combo_proto.addItem("Auto (Adaptativo: UDP prioritário com Failover TCP)", "auto")
        self.combo_proto.addItem("TCP Interleaved (Mais estável contra perda Wi-Fi)", "tcp")
        self.combo_proto.addItem("UDP Unicast (Menor latência bruta)", "udp")
        hw_form.addRow("Transporte RTSP:", self.combo_proto)

        self.lbl_cli_override_notice = QLabel()
        self.lbl_cli_override_notice.setWordWrap(True)
        self.lbl_cli_override_notice.setStyleSheet("color: #F59E0B; font-size: 11px; font-weight: 600;")
        self.lbl_cli_override_notice.setVisible(False)
        hw_form.addRow("", self.lbl_cli_override_notice)

        self.chk_motion = QCheckBox("Habilitar Detecção de Movimento (Edge-AI)")
        self.chk_motion.setChecked(True)
        hw_form.addRow("Visão Computacional:", self.chk_motion)

        self.spin_buffer = QSpinBox()
        self.spin_buffer.setRange(0, 2000)
        self.spin_buffer.setSuffix(" ms")
        self.spin_buffer.setValue(150)
        hw_form.addRow("Buffer de Baixa Latência:", self.spin_buffer)

        # OSD (Issue #34)
        self.chk_osd_timestamp = QCheckBox("Data/hora")
        self.chk_osd_fps = QCheckBox("FPS")
        self.chk_osd_bitrate = QCheckBox("Bitrate")
        osd_row = QHBoxLayout()
        for chk in (self.chk_osd_timestamp, self.chk_osd_fps, self.chk_osd_bitrate):
            chk.setChecked(True)
            osd_row.addWidget(chk)
        osd_row.addStretch()
        hw_form.addRow("Exibir no OSD:", osd_row)

        layout.addWidget(hw_group)

        # 2. Grupo Armazenamento
        stor_group = QGroupBox("Diretórios de Armazenamento")
        stor_form = QFormLayout(stor_group)

        snap_layout = QHBoxLayout()
        self.txt_snap_dir = QLineEdit()
        snap_layout.addWidget(self.txt_snap_dir)
        btn_snap_browse = QPushButton("Procurar...")
        btn_snap_browse.clicked.connect(self._browse_snap_dir)
        snap_layout.addWidget(btn_snap_browse)
        stor_form.addRow("Fotos / Snapshots:", snap_layout)

        # "Gravações de Vídeo" fica oculto até existir gravação (Issue #32 / #34)
        self.txt_rec_dir = QLineEdit()
        self.txt_rec_dir.setVisible(False)

        layout.addWidget(stor_group)

        # 3. Grupo Padrões de Conexão
        conn_group = QGroupBox("Padrões de Conexão")
        conn_form = QFormLayout(conn_group)

        self.txt_default_user = QLineEdit("")
        conn_form.addRow("Usuário Padrão:", self.txt_default_user)

        self.spin_reconn = QSpinBox()
        self.spin_reconn.setRange(1, 60)
        self.spin_reconn.setSuffix(" s")
        self.spin_reconn.setValue(5)
        conn_form.addRow("Intervalo de Reconexão:", self.spin_reconn)

        self.chk_auto_reconnect = QCheckBox("Reconectar automaticamente quando a câmera cair")
        self.chk_auto_reconnect.setChecked(True)
        conn_form.addRow("", self.chk_auto_reconnect)

        self.spin_discovery_timeout = QDoubleSpinBox()
        self.spin_discovery_timeout.setRange(1.0, 30.0)
        self.spin_discovery_timeout.setSingleStep(0.5)
        self.spin_discovery_timeout.setSuffix(" s")
        self.spin_discovery_timeout.setValue(3.0)
        conn_form.addRow("Tempo de busca WS-Discovery:", self.spin_discovery_timeout)

        self.chk_port_scan_default = QCheckBox("Sondar portas RTSP/HTTP na descoberta de rede")
        self.chk_port_scan_default.setChecked(True)
        conn_form.addRow("", self.chk_port_scan_default)

        self.spin_hidden_timeout = QSpinBox()
        self.spin_hidden_timeout.setRange(1, 240)
        self.spin_hidden_timeout.setSuffix(" min")
        self.spin_hidden_timeout.setValue(5)
        self.spin_hidden_timeout.setToolTip(
            "Câmeras fora da tela ficam pausadas (conexão mantida, sem decodificar) e são "
            "desconectadas após este tempo, liberando banda do Wi-Fi."
        )
        conn_form.addRow("Desconectar câmeras fora da tela após:", self.spin_hidden_timeout)

        layout.addWidget(conn_group)

        # 4. Grupo Atualizações do Aplicativo (GitHub)
        up_group = QGroupBox("Atualizações do Aplicativo (GitHub Releases)")
        up_form = QFormLayout(up_group)

        self.chk_startup_update = QCheckBox("Verificar se há nova versão automaticamente ao abrir o app")
        up_form.addRow(self.chk_startup_update)

        self.chk_periodic_update = QCheckBox("Verificar atualizações periodicamente em segundo plano")
        up_form.addRow(self.chk_periodic_update)

        self.spin_update_interval = QSpinBox()
        # Mínimo de 1 h: o app roda 24/7 e cada checagem consome a cota da API do GitHub (Issue #37)
        self.spin_update_interval.setRange(60, 1440)
        self.spin_update_interval.setSuffix(" minutos")
        self.spin_update_interval.setValue(60)
        up_form.addRow("Intervalo de Checagem:", self.spin_update_interval)

        self.txt_github_repo = QLineEdit("Othayz/central-nvr-wifi")
        up_form.addRow("Repositório GitHub:", self.txt_github_repo)

        self.txt_github_token = QLineEdit()
        self.txt_github_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_github_token.setPlaceholderText("ghp_... (opcional, para repositórios privados)")
        up_form.addRow("GitHub Token (PAT):", self.txt_github_token)

        from central_nvr.core.config import is_keyring_available
        if is_keyring_available():
            lbl_keyring_status = QLabel("🔒 Cofre de Senhas do Sistema (Keyring) ativo.")
            lbl_keyring_status.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 600;")
        else:
            lbl_keyring_status = QLabel("⚠️ Aviso: Keyring do sistema não detectado. Credenciais salvas localmente.")
            lbl_keyring_status.setStyleSheet("color: #F59E0B; font-size: 11px; font-weight: 600;")
        up_form.addRow("", lbl_keyring_status)

        check_now_layout = QHBoxLayout()
        lbl_cur_v = QLabel(f"Versão Instalada: <b>v{__version__}</b>")
        check_now_layout.addWidget(lbl_cur_v)
        check_now_layout.addStretch()

        btn_check_now = QPushButton("🔍 Verificar Agora no GitHub")
        btn_check_now.clicked.connect(self._check_updates_now)
        check_now_layout.addWidget(btn_check_now)
        up_form.addRow(check_now_layout)

        layout.addWidget(up_group)
        layout.addStretch()

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

        # Botões de Ação Salvar / Cancelar
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = QPushButton("Salvar Alterações")
        btn_save.setProperty("class", "primary-btn")
        btn_save.clicked.connect(self._save_values)
        btn_box.addWidget(btn_save)

        main_layout.addLayout(btn_box)

    def _get_persisted(self, key: str, default=None):
        """Lê o valor persistido (compatível com mocks que expõem apenas get())."""
        getter = getattr(self.config_mgr, "get_persisted", self.config_mgr.get)
        return getter(key, default)

    def _on_hw_selection_changed(self, _index: int):
        self._unrepresented_hw_accel = None

    def _update_cli_override_notice(self):
        has_override = getattr(self.config_mgr, "has_override", None)
        get_override = getattr(self.config_mgr, "get_override", None)
        if not callable(has_override) or not callable(get_override):
            self.lbl_cli_override_notice.setVisible(False)
            return

        active_flags = []
        if has_override("hw_accel"):
            active_flags.append(f"--hw-accel {get_override('hw_accel')}")
        if has_override("rtsp_transport"):
            active_flags.append(f"--transport {get_override('rtsp_transport')}")

        if active_flags:
            flags_str = ", ".join(active_flags)
            self.lbl_cli_override_notice.setText(
                f"Override de CLI ativo nesta execução ({flags_str}). "
                "Os campos acima exibem as preferências salvas; alterá-los aqui substitui o override na sessão atual."
            )
            self.lbl_cli_override_notice.setVisible(True)
        else:
            self.lbl_cli_override_notice.setText("")
            self.lbl_cli_override_notice.setVisible(False)

    def _load_values(self):
        theme = self._get_persisted("theme", "dark")
        idx_t = self.combo_theme.findData(theme)
        if idx_t >= 0:
            self.combo_theme.setCurrentIndex(idx_t)

        hw = self._get_persisted("hw_accel", "vaapi")
        self._loaded_hw_accel = hw
        idx = self.combo_hw.findData(hw)
        if idx >= 0:
            self._unrepresented_hw_accel = None
            self.combo_hw.blockSignals(True)
            try:
                self.combo_hw.setCurrentIndex(idx)
            finally:
                self.combo_hw.blockSignals(False)
        else:
            # Preserva valores persistidos fora do combo (ex.: "auto") se o usuário não alterar o campo
            self._unrepresented_hw_accel = hw

        proto = self._get_persisted("rtsp_transport", DEFAULT_RTSP_TRANSPORT)
        self._loaded_rtsp_transport = proto
        idx_p = self.combo_proto.findData(proto)
        if idx_p >= 0:
            self.combo_proto.setCurrentIndex(idx_p)

        self._update_cli_override_notice()
        self.chk_motion.setChecked(self._get_persisted("enable_motion_detection", True))

        self.spin_buffer.setValue(self._get_persisted("buffer_size_ms", 150))
        self.txt_snap_dir.setText(self._get_persisted("snapshot_dir", ""))
        self.txt_rec_dir.setText(self._get_persisted("recordings_dir", ""))
        self.txt_default_user.setText(self._get_persisted("default_username", "admin"))
        self.spin_reconn.setValue(self._get_persisted("reconnect_interval_sec", 5))
        self.spin_hidden_timeout.setValue(int(self._get_persisted("hidden_camera_timeout_min", 5)))
        self.chk_auto_reconnect.setChecked(bool(self._get_persisted("auto_reconnect", True)))
        self.spin_discovery_timeout.setValue(float(self._get_persisted("ws_discovery_timeout", 3.0)))
        self.chk_port_scan_default.setChecked(bool(self._get_persisted("enable_port_scan", True)))
        self.chk_osd_timestamp.setChecked(bool(self._get_persisted("show_osd_timestamp", True)))
        self.chk_osd_fps.setChecked(bool(self._get_persisted("show_osd_fps", True)))
        self.chk_osd_bitrate.setChecked(bool(self._get_persisted("show_osd_bitrate", True)))

        # Valores de atualização
        self.chk_startup_update.setChecked(self._get_persisted("check_updates_on_startup", True))
        self.chk_periodic_update.setChecked(self._get_persisted("periodic_update_check", True))
        self.spin_update_interval.setValue(self._get_persisted("periodic_update_interval_min", 60))
        self.txt_github_repo.setText(self._get_persisted("github_repo", "Othayz/central-nvr-wifi"))
        self.txt_github_token.setText(self._get_persisted("github_token", ""))

    def collect_values(self) -> dict:
        """Valores da tela, prontos para gravar de uma vez com ConfigManager.update()."""
        hw_value = (
            self._unrepresented_hw_accel
            if self._unrepresented_hw_accel is not None
            else self.combo_hw.currentData()
        )
        return {
            "theme": self.combo_theme.currentData(),
            "hw_accel": hw_value,
            "rtsp_transport": self.combo_proto.currentData(),
            "enable_motion_detection": self.chk_motion.isChecked(),
            "buffer_size_ms": self.spin_buffer.value(),
            "show_osd_timestamp": self.chk_osd_timestamp.isChecked(),
            "show_osd_fps": self.chk_osd_fps.isChecked(),
            "show_osd_bitrate": self.chk_osd_bitrate.isChecked(),
            "snapshot_dir": self.txt_snap_dir.text().strip(),
            "default_username": self.txt_default_user.text().strip(),
            "reconnect_interval_sec": self.spin_reconn.value(),
            "auto_reconnect": self.chk_auto_reconnect.isChecked(),
            "ws_discovery_timeout": self.spin_discovery_timeout.value(),
            "enable_port_scan": self.chk_port_scan_default.isChecked(),
            "hidden_camera_timeout_min": self.spin_hidden_timeout.value(),
            "check_updates_on_startup": self.chk_startup_update.isChecked(),
            "periodic_update_check": self.chk_periodic_update.isChecked(),
            "periodic_update_interval_min": self.spin_update_interval.value(),
            "github_repo": self.txt_github_repo.text().strip() or "Othayz/central-nvr-wifi",
            "github_token": self.txt_github_token.text().strip(),
        }

    def _save_values(self):
        values = self.collect_values()
        # Uma única gravação de settings.json (antes eram ~14, uma por chave)
        saved = self.config_mgr.update(values)
        if saved is False:
            err = getattr(self.config_mgr, "last_write_error", None) or "erro de E/S"
            QMessageBox.critical(
                self,
                "Erro ao Salvar Configurações",
                f"Não foi possível salvar as configurações: {err}",
            )
            return

        # Limpa o override de CLI somente para chaves que o usuário alterou explicitamente na tela
        clear_override = getattr(self.config_mgr, "clear_override", None)
        if callable(clear_override):
            if values["hw_accel"] != self._loaded_hw_accel:
                clear_override("hw_accel")
            if values["rtsp_transport"] != self._loaded_rtsp_transport:
                clear_override("rtsp_transport")

        QMessageBox.information(self, "Configurações", "Configurações salvas com sucesso!")
        self.accept()

    def _browse_snap_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Snapshots", self.txt_snap_dir.text())
        if path:
            self.txt_snap_dir.setText(path)

    def _check_updates_now(self):
        repo = self.txt_github_repo.text().strip() or "Othayz/central-nvr-wifi"
        token = self.txt_github_token.text().strip() or None
        dialog = UpdateDialog(repo=repo, token=token, parent=self)
        dialog.exec()
