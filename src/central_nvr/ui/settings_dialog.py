"""
Diálogo de Configurações Gerais da Central NVR WiFi.
Permite configurar decodificação por hardware (VA-API), caminhos de gravação, rede e atualizações.
"""
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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
from central_nvr.core.config import ConfigManager
from central_nvr.ui.update_dialog import UpdateDialog


class SettingsDialog(QDialog):
    """
    Diálogo modal de preferências da aplicação.
    """

    def __init__(self, config_mgr: ConfigManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config_mgr = config_mgr
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
        hw_form.addRow("Decodificação por GPU:", self.combo_hw)

        self.combo_proto = QComboBox()
        self.combo_proto.addItem("Auto (Adaptativo: UDP prioritário com Failover TCP)", "auto")
        self.combo_proto.addItem("TCP Interleaved (Mais estável contra perda Wi-Fi)", "tcp")
        self.combo_proto.addItem("UDP Unicast (Menor latência bruta)", "udp")
        hw_form.addRow("Transporte RTSP:", self.combo_proto)

        self.chk_motion = QCheckBox("Habilitar Detecção de Movimento (Edge-AI)")
        self.chk_motion.setChecked(True)
        hw_form.addRow("Visão Computacional:", self.chk_motion)

        self.spin_buffer = QSpinBox()
        self.spin_buffer.setRange(0, 2000)
        self.spin_buffer.setSuffix(" ms")
        self.spin_buffer.setValue(150)
        hw_form.addRow("Buffer de Baixa Latência:", self.spin_buffer)

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

        rec_layout = QHBoxLayout()
        self.txt_rec_dir = QLineEdit()
        rec_layout.addWidget(self.txt_rec_dir)
        btn_rec_browse = QPushButton("Procurar...")
        btn_rec_browse.clicked.connect(self._browse_rec_dir)
        rec_layout.addWidget(btn_rec_browse)
        stor_form.addRow("Gravações de Vídeo:", rec_layout)

        layout.addWidget(stor_group)

        # 3. Grupo Padrões de Conexão
        conn_group = QGroupBox("Padrões de Conexão")
        conn_form = QFormLayout(conn_group)

        self.txt_default_user = QLineEdit("admin")
        conn_form.addRow("Usuário Padrão:", self.txt_default_user)

        self.spin_reconn = QSpinBox()
        self.spin_reconn.setRange(1, 60)
        self.spin_reconn.setSuffix(" s")
        self.spin_reconn.setValue(5)
        conn_form.addRow("Intervalo de Reconexão:", self.spin_reconn)

        layout.addWidget(conn_group)

        # 4. Grupo Atualizações do Aplicativo (GitHub)
        up_group = QGroupBox("Atualizações do Aplicativo (GitHub Releases)")
        up_form = QFormLayout(up_group)

        self.chk_startup_update = QCheckBox("Verificar se há nova versão automaticamente ao abrir o app")
        up_form.addRow(self.chk_startup_update)

        self.chk_periodic_update = QCheckBox("Verificar atualizações periodicamente em segundo plano")
        up_form.addRow(self.chk_periodic_update)

        self.spin_update_interval = QSpinBox()
        self.spin_update_interval.setRange(2, 1440)
        self.spin_update_interval.setSuffix(" minutos")
        self.spin_update_interval.setValue(10)
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

    def _load_values(self):
        theme = self.config_mgr.get("theme", "dark")
        idx_t = self.combo_theme.findData(theme)
        if idx_t >= 0:
            self.combo_theme.setCurrentIndex(idx_t)

        hw = self.config_mgr.get("hw_accel", "vaapi")
        idx = self.combo_hw.findData(hw)
        if idx >= 0:
            self.combo_hw.setCurrentIndex(idx)

        proto = self.config_mgr.get("rtsp_transport", "auto")
        idx_p = self.combo_proto.findData(proto)
        if idx_p >= 0:
            self.combo_proto.setCurrentIndex(idx_p)
        self.chk_motion.setChecked(self.config_mgr.get("enable_motion_detection", True))

        self.spin_buffer.setValue(self.config_mgr.get("buffer_size_ms", 150))
        self.txt_snap_dir.setText(self.config_mgr.get("snapshot_dir", ""))
        self.txt_rec_dir.setText(self.config_mgr.get("recordings_dir", ""))
        self.txt_default_user.setText(self.config_mgr.get("default_username", "admin"))
        self.spin_reconn.setValue(self.config_mgr.get("reconnect_interval_sec", 5))

        # Valores de atualização
        self.chk_startup_update.setChecked(self.config_mgr.get("check_updates_on_startup", True))
        self.chk_periodic_update.setChecked(self.config_mgr.get("periodic_update_check", True))
        self.spin_update_interval.setValue(self.config_mgr.get("periodic_update_interval_min", 10))
        self.txt_github_repo.setText(self.config_mgr.get("github_repo", "Othayz/central-nvr-wifi"))
        self.txt_github_token.setText(self.config_mgr.get("github_token", ""))

    def _save_values(self):
        self.config_mgr.set("theme", self.combo_theme.currentData())
        self.config_mgr.set("hw_accel", self.combo_hw.currentData())
        self.config_mgr.set("rtsp_transport", self.combo_proto.currentData())
        self.config_mgr.set("enable_motion_detection", self.chk_motion.isChecked())
        self.config_mgr.set("buffer_size_ms", self.spin_buffer.value())
        self.config_mgr.set("snapshot_dir", self.txt_snap_dir.text().strip())
        self.config_mgr.set("recordings_dir", self.txt_rec_dir.text().strip())
        self.config_mgr.set("default_username", self.txt_default_user.text().strip())
        self.config_mgr.set("reconnect_interval_sec", self.spin_reconn.value())

        # Salvar parâmetros de atualização
        self.config_mgr.set("check_updates_on_startup", self.chk_startup_update.isChecked())
        self.config_mgr.set("periodic_update_check", self.chk_periodic_update.isChecked())
        self.config_mgr.set("periodic_update_interval_min", self.spin_update_interval.value())
        self.config_mgr.set("github_repo", self.txt_github_repo.text().strip() or "Othayz/central-nvr-wifi")
        self.config_mgr.set("github_token", self.txt_github_token.text().strip())

        QMessageBox.information(self, "Configurações", "Configurações salvas com sucesso!")
        self.accept()

    def _browse_snap_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Snapshots", self.txt_snap_dir.text())
        if path:
            self.txt_snap_dir.setText(path)

    def _browse_rec_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Gravações", self.txt_rec_dir.text())
        if path:
            self.txt_rec_dir.setText(path)

    def _check_updates_now(self):
        repo = self.txt_github_repo.text().strip() or "Othayz/central-nvr-wifi"
        token = self.txt_github_token.text().strip() or None
        dialog = UpdateDialog(repo=repo, token=token, parent=self)
        dialog.exec()
