"""
Diálogo de Descoberta de Rede (WS-Discovery ONVIF e Sondagem de Portas) e Adição de Câmeras.
"""
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import CameraDevice
from central_nvr.scanner.discovery import NetworkScanner


class ScannerWorker(QThread):
    """Worker thread para execução não-bloqueante do escaneamento de rede."""

    device_found = Signal(dict)
    progress_updated = Signal(int, str)
    scan_finished = Signal(list)

    def __init__(self, timeout: float = 4.0, enable_port_scan: bool = True, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.timeout = timeout
        self.enable_port_scan = enable_port_scan
        self.scanner = NetworkScanner()

    def stop(self):
        self.scanner.stop_scan()
        self.wait(1000)

    def run(self):
        devices = self.scanner.scan_network(
            timeout=self.timeout,
            enable_port_scan=self.enable_port_scan,
            on_device_found=lambda dev: self.device_found.emit(dev),
            on_progress=lambda pct, msg: self.progress_updated.emit(pct, msg),
        )
        self.scan_finished.emit(devices)


class DiscoveryDialog(QDialog):
    """
    Diálogo para descoberta automática via ONVIF WS-Discovery e adição manual de câmeras.
    """

    device_added = Signal(object)  # CameraDevice

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Gerenciamento de Dispositivos e Descoberta de Rede")
        self.resize(780, 520)
        self.discovered_list: List[Dict[str, Any]] = []
        self.worker: Optional[ScannerWorker] = None

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        tabs = QTabWidget()

        # =====================================================================
        # Aba 1: Descoberta Automática (WS-Discovery + Sonda de Portas)
        # =====================================================================
        tab_discovery = QWidget()
        disc_layout = QVBoxLayout(tab_discovery)
        disc_layout.setSpacing(10)

        # Barra de Controles de Varredura
        ctrl_layout = QHBoxLayout()
        self.btn_scan = QPushButton("🔍 Iniciar Varredura de Rede")
        self.btn_scan.setProperty("class", "primary-btn")
        self.btn_scan.clicked.connect(self._toggle_scan)
        ctrl_layout.addWidget(self.btn_scan)

        self.chk_port_scan = QCheckBox("Sondagem de Portas RTSP/HTTP")
        self.chk_port_scan.setChecked(True)
        ctrl_layout.addWidget(self.chk_port_scan)

        ctrl_layout.addStretch()

        disc_layout.addLayout(ctrl_layout)

        # Barra de Progresso e Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)
        disc_layout.addWidget(self.progress_bar)

        self.lbl_scan_status = QLabel("Clique em 'Iniciar Varredura' para localizar câmeras ONVIF e NVRs.")
        self.lbl_scan_status.setObjectName("mutedLabel")
        disc_layout.addWidget(self.lbl_scan_status)

        # Tabela de Dispositivos Encontrados
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Sel.", "IP", "Porta", "Fabricante", "Modelo / Nome", "Origem"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        disc_layout.addWidget(self.table)

        # Configuração de Credenciais Padrão para Adição em Lote
        cred_box = QGroupBox("Credenciais de Acesso para os Dispositivos Selecionados")
        cred_layout = QHBoxLayout(cred_box)
        
        cred_layout.addWidget(QLabel("Usuário:"))
        self.txt_disc_user = QLineEdit("admin")
        cred_layout.addWidget(self.txt_disc_user)

        cred_layout.addWidget(QLabel("Senha:"))
        self.txt_disc_pass = QLineEdit()
        self.txt_disc_pass.setEchoMode(QLineEdit.EchoMode.Password)
        cred_layout.addWidget(self.txt_disc_pass)

        disc_layout.addWidget(cred_box)

        # Botão de Ação para Adicionar
        btn_add_layout = QHBoxLayout()
        btn_add_layout.addStretch()
        self.btn_add_selected = QPushButton("➕ Adicionar Dispositivos Selecionados")
        self.btn_add_selected.setProperty("class", "primary-btn")
        self.btn_add_selected.clicked.connect(self._add_selected_devices)
        btn_add_layout.addWidget(self.btn_add_selected)

        disc_layout.addLayout(btn_add_layout)
        tabs.addTab(tab_discovery, "Descoberta Automática (ONVIF)")

        # =====================================================================
        # Aba 2: Adição Manual de Câmera (IP / RTSP)
        # =====================================================================
        tab_manual = QWidget()
        manual_layout = QVBoxLayout(tab_manual)

        form = QFormLayout()
        form.setSpacing(8)

        # Seletor de Modelo / Perfil Rápido
        self.combo_preset = QComboBox()
        self.combo_preset.addItem("Yoosee (Porta 5000 | RTSP /onvif1)", "yoosee")
        self.combo_preset.addItem("Intelbras / Dahua (Porta 80 | RTSP /cam/realmonitor...)", "intelbras")
        self.combo_preset.addItem("Hikvision (Porta 80 | RTSP /Streaming/Channels/101)", "hikvision")
        self.combo_preset.addItem("V380 / Genérico (Porta 80 | RTSP /live/ch0)", "v380")
        self.combo_preset.addItem("Personalizado", "custom")
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("Perfil do Fabricante:", self.combo_preset)

        self.txt_man_name = QLineEdit("Câmera Yoosee")
        form.addRow("Nome da Câmera:", self.txt_man_name)

        self.txt_man_ip = QLineEdit("192.168.1.2")
        form.addRow("Endereço IP:", self.txt_man_ip)

        self.spin_man_port = QSpinBox()
        self.spin_man_port.setRange(1, 65535)
        self.spin_man_port.setValue(5000)
        form.addRow("Porta ONVIF:", self.spin_man_port)

        self.spin_man_rtsp_port = QSpinBox()
        self.spin_man_rtsp_port.setRange(1, 65535)
        self.spin_man_rtsp_port.setValue(554)
        form.addRow("Porta RTSP:", self.spin_man_rtsp_port)

        self.txt_man_rtsp_path = QLineEdit("/onvif1")
        form.addRow("Caminho RTSP:", self.txt_man_rtsp_path)

        self.txt_man_user = QLineEdit("admin")
        form.addRow("Usuário:", self.txt_man_user)

        self.txt_man_pass = QLineEdit()
        self.txt_man_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_man_pass.setPlaceholderText("Senha de Conexão NVR (configurada no app Yoosee)")
        form.addRow("Senha (NVR):", self.txt_man_pass)

        self.chk_man_ptz = QCheckBox("Suporta Controle PTZ (Girar e Inclinar)")
        self.chk_man_ptz.setChecked(True)
        form.addRow("", self.chk_man_ptz)

        manual_layout.addLayout(form)

        # Caixa de Dica para Câmeras Yoosee
        self.lbl_yoosee_hint = QLabel(
            "📌 <b>Atenção Câmeras Yoosee:</b> No app do celular Yoosee, acesse as "
            "<b>Configurações da Câmera → Conexões NVR (ou Segurança)</b>, ative a chave "
            "e crie uma <b>Senha de Conexão NVR</b> (ex: 123456). Digite essa mesma senha acima."
        )
        self.lbl_yoosee_hint.setWordWrap(True)
        self.lbl_yoosee_hint.setObjectName("infoBanner")
        manual_layout.addWidget(self.lbl_yoosee_hint)

        manual_layout.addStretch()

        btn_man_add = QPushButton("Adicionar Câmera Manualmente")
        btn_man_add.setProperty("class", "primary-btn")
        btn_man_add.clicked.connect(self._add_manual_device)
        manual_layout.addWidget(btn_man_add)

        tabs.addTab(tab_manual, "Adicionar Manualmente")

        main_layout.addWidget(tabs)

        # Botão Fechar
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.accept)
        bottom_layout.addWidget(btn_close)
        main_layout.addLayout(bottom_layout)

    def _toggle_scan(self):
        """Inicia ou cancela a varredura."""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_scan.setText("🔍 Iniciar Varredura de Rede")
            self.lbl_scan_status.setText("Varredura cancelada.")
            return

        self.table.setRowCount(0)
        self.discovered_list.clear()
        self.progress_bar.setValue(0)
        self.btn_scan.setText("⏹ Cancelar Varredura")

        self.worker = ScannerWorker(
            timeout=4.0,
            enable_port_scan=self.chk_port_scan.isChecked(),
            parent=self,
        )
        self.worker.device_found.connect(self._on_device_found)
        self.worker.progress_updated.connect(self._on_progress_updated)
        self.worker.scan_finished.connect(self._on_scan_finished)
        self.worker.start()

    def _on_device_found(self, dev: Dict[str, Any]):
        """Insere dispositivo descoberto na tabela."""
        self.discovered_list.append(dev)
        row = self.table.rowCount()
        self.table.insertRow(row)

        chk_item = QTableWidgetItem()
        chk_item.setCheckState(Qt.CheckState.Checked)
        chk_item.setData(Qt.ItemDataRole.UserRole, dev)
        self.table.setItem(row, 0, chk_item)

        self.table.setItem(row, 1, QTableWidgetItem(dev.get("ip", "")))
        self.table.setItem(row, 2, QTableWidgetItem(str(dev.get("port", 80))))
        self.table.setItem(row, 3, QTableWidgetItem(dev.get("manufacturer", "Genérico")))
        self.table.setItem(row, 4, QTableWidgetItem(dev.get("name", "Câmera ONVIF")))
        self.table.setItem(row, 5, QTableWidgetItem(dev.get("source", "ONVIF")))

    def _on_progress_updated(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.lbl_scan_status.setText(msg)

    def _on_scan_finished(self, devices: List[Dict[str, Any]]):
        self.btn_scan.setText("🔍 Iniciar Varredura de Rede")
        self.progress_bar.setValue(100)
        self.lbl_scan_status.setText(f"Varredura finalizada. {len(devices)} dispositivos encontrados.")

    def _add_selected_devices(self):
        """Adiciona todos os dispositivos marcados na tabela."""
        user = self.txt_disc_user.text().strip() or "admin"
        pwd = self.txt_disc_pass.text()

        count = 0
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                dev_data = chk.data(Qt.ItemDataRole.UserRole)
                if not dev_data:
                    continue
                dev_port = dev_data.get("port", 80)
                is_yoosee = (dev_port == 5000 or dev_data.get("manufacturer", "").lower() == "yoosee")
                rtsp_path = "/onvif1" if is_yoosee else "/live/ch0"
                mfg = "Yoosee" if is_yoosee else dev_data.get("manufacturer", "Genérico")

                camera = CameraDevice(
                    id=f"cam_{dev_data['ip'].replace('.', '_')}",
                    name=dev_data.get("name", f"Câmera {dev_data['ip']}"),
                    ip=dev_data["ip"],
                    port=dev_port,
                    rtsp_port=dev_data.get("rtsp_port", 554),
                    rtsp_path=rtsp_path,
                    username=user,
                    password=pwd,
                    manufacturer=mfg,
                    model=dev_data.get("model", "Câmera ONVIF"),
                    onvif_endpoint=dev_data.get("onvif_endpoint", ""),
                    has_ptz=True,
                    enabled=True,
                )
                self.device_added.emit(camera)
                count += 1

        if count > 0:
            QMessageBox.information(self, "Sucesso", f"{count} dispositivo(s) adicionado(s) com sucesso!")
            self.accept()
        else:
            QMessageBox.warning(self, "Aviso", "Selecione ao menos um dispositivo para adicionar.")

    def _on_preset_changed(self, index: int):
        """Preenche automaticamente os campos de acordo com o modelo selecionado."""
        preset = self.combo_preset.currentData()
        if preset == "yoosee":
            self.txt_man_name.setText("Câmera Yoosee")
            self.spin_man_port.setValue(5000)
            self.spin_man_rtsp_port.setValue(554)
            self.txt_man_rtsp_path.setText("/onvif1")
            self.txt_man_user.setText("admin")
            self.lbl_yoosee_hint.setVisible(True)
        elif preset == "intelbras":
            self.txt_man_name.setText("Câmera Intelbras")
            self.spin_man_port.setValue(80)
            self.spin_man_rtsp_port.setValue(554)
            self.txt_man_rtsp_path.setText("/cam/realmonitor?channel=1&subtype=0")
            self.txt_man_user.setText("admin")
            self.lbl_yoosee_hint.setVisible(False)
        elif preset == "hikvision":
            self.txt_man_name.setText("Câmera Hikvision")
            self.spin_man_port.setValue(80)
            self.spin_man_rtsp_port.setValue(554)
            self.txt_man_rtsp_path.setText("/Streaming/Channels/101")
            self.txt_man_user.setText("admin")
            self.lbl_yoosee_hint.setVisible(False)
        elif preset == "v380":
            self.txt_man_name.setText("Câmera V380 / Genérica")
            self.spin_man_port.setValue(80)
            self.spin_man_rtsp_port.setValue(554)
            self.txt_man_rtsp_path.setText("/live/ch0")
            self.txt_man_user.setText("admin")
            self.lbl_yoosee_hint.setVisible(False)
        else:
            self.lbl_yoosee_hint.setVisible(False)

    def _add_manual_device(self):
        """Adiciona câmera preenchida no formulário manual."""
        ip = self.txt_man_ip.text().strip()
        if not ip:
            QMessageBox.warning(self, "Aviso", "O endereço IP é obrigatório.")
            return

        preset = self.combo_preset.currentData()
        mfg = "Yoosee" if preset == "yoosee" else ("Intelbras" if preset == "intelbras" else ("Hikvision" if preset == "hikvision" else "Genérico"))

        camera = CameraDevice(
            id=f"cam_{ip.replace('.', '_')}_{self.spin_man_port.value()}",
            name=self.txt_man_name.text().strip() or "Câmera Manual",
            ip=ip,
            port=self.spin_man_port.value(),
            rtsp_port=self.spin_man_rtsp_port.value(),
            rtsp_path=self.txt_man_rtsp_path.text().strip(),
            username=self.txt_man_user.text().strip(),
            password=self.txt_man_pass.text(),
            manufacturer=mfg,
            model=f"Câmera {mfg}",
            has_ptz=self.chk_man_ptz.isChecked(),
            enabled=True,
        )
        self.device_added.emit(camera)
        QMessageBox.information(self, "Sucesso", f"Câmera '{camera.name}' adicionada com sucesso!")
        self.accept()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        super().closeEvent(event)
