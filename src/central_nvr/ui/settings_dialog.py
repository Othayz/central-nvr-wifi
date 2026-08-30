"""
Diálogo de Configurações Gerais da Central NVR WiFi.
Permite configurar decodificação por hardware (VA-API), caminhos de gravação e rede.
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.config import ConfigManager


class SettingsDialog(QDialog):
    """
    Diálogo modal de preferências da aplicação.
    """

    def __init__(self, config_mgr: ConfigManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.setWindowTitle("Configurações do Sistema - Central NVR WiFi")
        self.resize(550, 420)

        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
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
        layout.addStretch()

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

        layout.addLayout(btn_box)

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
