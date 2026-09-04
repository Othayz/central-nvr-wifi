"""
Janela Principal (MainWindow) da Central NVR WiFi para Linux.
Integra Topbar, Gerenciador de Dispositivos (Sidebar), Grid de Câmeras, PTZ e Timeline.
"""
import datetime
import logging
import os
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import ConfigManager, get_data_dir
from central_nvr.ui.camera_grid import CameraGridWidget
from central_nvr.ui.discovery_dialog import DiscoveryDialog
from central_nvr.ui.playback_view import PlaybackWidget
from central_nvr.ui.ptz_controller import PTZControllerWidget
from central_nvr.ui.settings_dialog import SettingsDialog
from central_nvr.ui.styles import DARK_THEME_QSS, LIGHT_THEME_QSS, get_theme_qss
from central_nvr.ui.update_dialog import UpdateDialog
from central_nvr.core.updater import ReleaseInfo, UpdateCheckWorker

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Janela Principal do Aplicativo Central NVR WiFi.
    """

    def __init__(self, config_mgr: ConfigManager):
        super().__init__()
        self.config_mgr = config_mgr
        self.current_theme = self.config_mgr.get("theme", "dark").lower()
        self.setWindowTitle("Central NVR WiFi - Monitoramento e Controle de Câmeras IP")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)

        # Definir ícone da janela
        from pathlib import Path
        from PySide6.QtGui import QIcon
        icon_path = Path(__file__).parent / "assets" / "central-nvr.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.cameras: List[CameraDevice] = []
        self._load_cameras_from_config()

        self._setup_ui()
        self._setup_clock_timer()
        self._setup_periodic_update_checker()
        self._populate_device_tree()

    def _load_cameras_from_config(self):
        """Instancia os objetos CameraDevice a partir da configuração salva."""
        self.cameras = [CameraDevice.from_dict(d) for d in self.config_mgr.devices]

    def _setup_ui(self):
        """Constrói toda a estrutura visual da janela principal."""
        self.setStyleSheet(get_theme_qss(self.current_theme))

        # Widget Central Principal
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_vbox = QVBoxLayout(central_widget)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(0)

        # 1. Top Header Bar
        self.top_header = self._create_top_header()
        main_vbox.addWidget(self.top_header)

        # 2. Splitter Central (Sidebar Esquerda + Grid e Timeline à Direita)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(2)
        self.splitter.setStyleSheet("QSplitter::handle { background-color: #334155; }")

        # Sidebar Esquerda
        self.sidebar = self._create_sidebar()
        self.splitter.addWidget(self.sidebar)

        # Workspace da Direita (Grid de Câmeras + Timeline)
        self.right_workspace = QWidget()
        right_vbox = QVBoxLayout(self.right_workspace)
        right_vbox.setContentsMargins(6, 6, 6, 0)
        right_vbox.setSpacing(6)

        # Grid Multi-Câmeras
        hw_accel = self.config_mgr.get("hw_accel", "vaapi")
        rtsp_transport = self.config_mgr.get("rtsp_transport", "tcp")
        self.camera_grid = CameraGridWidget(
            hw_accel=hw_accel,
            rtsp_transport=rtsp_transport,
            parent=self,
        )
        self.camera_grid.camera_selected.connect(self._on_camera_selected)
        self.camera_grid.ptz_requested.connect(self._on_ptz_requested)
        self.camera_grid.snapshot_taken.connect(self._on_snapshot_saved)
        self.camera_grid.add_camera_requested.connect(self._open_discovery_dialog)
        self.camera_grid.layout_mode_changed.connect(self._on_layout_mode_changed_by_grid)
        self.camera_grid.fullscreen_requested.connect(self._on_camera_fullscreen_requested)
        self.camera_grid.camera_renamed.connect(self._on_camera_renamed)
        right_vbox.addWidget(self.camera_grid, stretch=1)

        self.splitter.addWidget(self.right_workspace)
        self.splitter.setStretchFactor(0, 0)  # Sidebar largura fixa
        self.splitter.setStretchFactor(1, 1)  # Grid expansível ocupando 100% da tela
        self.splitter.setSizes([310, 970])

        main_vbox.addWidget(self.splitter, stretch=1)

        # Botão Flutuante de Saída de Tela Cheia do Mosaico
        self.btn_floating_exit_fullscreen = QPushButton("✕ Sair da Tela Cheia (ESC / F11)", self.right_workspace)
        self.btn_floating_exit_fullscreen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_floating_exit_fullscreen.setStyleSheet("""
            QPushButton {
                background-color: rgba(15, 23, 42, 0.95);
                color: #38BDF8;
                border: 1px solid #0284C7;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 700;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0284C7;
                color: #FFFFFF;
            }
        """)
        self.btn_floating_exit_fullscreen.clicked.connect(self._exit_grid_fullscreen)
        self.btn_floating_exit_fullscreen.hide()

        # 3. Status Bar
        self._setup_status_bar()

        # Iniciar Grid com Câmeras
        self.camera_grid.set_cameras(self.cameras)
        initial_layout = self.config_mgr.get("grid_layout", "auto")
        self.camera_grid.set_layout_mode(initial_layout)

    def _create_top_header(self) -> QWidget:
        """Cria a barra superior com ações de layout, scan e configurações."""
        header = QFrame()
        header.setObjectName("topHeaderWidget")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 6, 12, 6)
        h_layout.setSpacing(10)

        # Logo e Título
        title_box = QHBoxLayout()
        title_box.setSpacing(8)

        lbl_icon = QLabel()
        from pathlib import Path
        from PySide6.QtGui import QPixmap
        icon_path = Path(__file__).parent / "assets" / "central-nvr.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl_icon.setPixmap(pix)
        else:
            lbl_icon.setText("NVR")
            lbl_icon.setStyleSheet("color: #38BDF8; font-weight: bold; background-color: #0F172A; padding: 2px 6px; border-radius: 4px; font-size: 11px;")
        title_box.addWidget(lbl_icon)

        lbl_title = QLabel("Central NVR WiFi")
        lbl_title.setObjectName("appLogoTitle")
        title_box.addWidget(lbl_title)

        lbl_ver = QLabel("v1.0")
        lbl_ver.setObjectName("appVersionBadge")
        title_box.addWidget(lbl_ver)
        h_layout.addLayout(title_box)

        h_layout.addSpacing(20)

        # Botão Unificado de Adição / Varredura de Câmeras
        btn_add = QPushButton("+ Adicionar Câmera")
        btn_add.setProperty("class", "primary-btn")
        btn_add.setToolTip("Buscar câmeras na rede (ONVIF) ou adicionar manualmente por IP/RTSP")
        btn_add.clicked.connect(self._open_discovery_dialog)
        h_layout.addWidget(btn_add)

        h_layout.addStretch()

        # Seletores de Layout do Grid
        lbl_layout = QLabel("Layout:")
        lbl_layout.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 600;")
        h_layout.addWidget(lbl_layout)

        self.btn_grp_layout = QButtonGroup(self)
        self.layout_buttons = {}
        cur_layout = self.config_mgr.get("grid_layout", "auto").lower()
        for mode_title, mode_key in [("Auto", "auto"), ("1x1", "1x1"), ("2x2", "2x2"), ("3x3", "3x3")]:
            b = QPushButton(mode_title)
            b.setProperty("class", "header-btn")
            b.setCheckable(True)
            if mode_key == cur_layout:
                b.setChecked(True)
            b.clicked.connect(lambda checked, m=mode_key: self._change_grid_layout(m))
            self.btn_grp_layout.addButton(b)
            self.layout_buttons[mode_key] = b
            h_layout.addWidget(b)

        # Botão Tela Cheia (Fullscreen)
        btn_fullscreen = QPushButton("Tela Cheia")
        btn_fullscreen.setProperty("class", "header-btn")
        btn_fullscreen.setToolTip("Pressione F11 para alternar Tela Cheia")
        btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        h_layout.addWidget(btn_fullscreen)

        # Botão Menu Seletor de Temas
        self.btn_theme_menu = QPushButton("🎨 Temas ▾")
        self.btn_theme_menu.setProperty("class", "header-btn")
        self.btn_theme_menu.setToolTip("Escolha o tema visual da interface (Salvo automaticamente)")
        
        self.theme_menu = QMenu(self)
        self.act_theme_dark = QAction("🌙 Tema Escuro (Dark Mode)", self)
        self.act_theme_dark.setCheckable(True)
        self.act_theme_dark.setChecked(self.current_theme == "dark")
        self.act_theme_dark.triggered.connect(lambda: self._set_theme("dark"))
        self.theme_menu.addAction(self.act_theme_dark)

        self.act_theme_light = QAction("☀️ Tema Claro (Light Mode)", self)
        self.act_theme_light.setCheckable(True)
        self.act_theme_light.setChecked(self.current_theme == "light")
        self.act_theme_light.triggered.connect(lambda: self._set_theme("light"))
        self.theme_menu.addAction(self.act_theme_light)

        self.btn_theme_menu.setMenu(self.theme_menu)
        h_layout.addWidget(self.btn_theme_menu)

        # Botão de Atualizações
        btn_updates = QPushButton("🔄 Atualizações")
        btn_updates.setProperty("class", "header-btn")
        btn_updates.setToolTip("Verificar se há novas versões disponíveis no GitHub")
        btn_updates.clicked.connect(self._open_update_dialog)
        h_layout.addWidget(btn_updates)

        # Botão Configurações
        btn_settings = QPushButton("Configurações")
        btn_settings.setProperty("class", "header-btn")
        btn_settings.clicked.connect(self._open_settings_dialog)
        h_layout.addWidget(btn_settings)

        # Relógio do Sistema em Tempo Real
        self.lbl_clock = QLabel("00:00:00")
        self.lbl_clock.setObjectName("systemClockLabel")
        h_layout.addWidget(self.lbl_clock)

        return header

    def _create_sidebar(self) -> QWidget:
        """Cria o painel lateral com abas dedicadas para Câmeras, Controle PTZ, Gravações e Logs."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebarWidget")
        v_layout = QVBoxLayout(sidebar)
        v_layout.setContentsMargins(6, 6, 6, 6)
        v_layout.setSpacing(6)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setObjectName("sidebarTabs")

        # =====================================================================
        # Aba 1: Lista e Gerenciamento de Câmeras
        # =====================================================================
        tab_cameras = QWidget()
        cam_layout = QVBoxLayout(tab_cameras)
        cam_layout.setContentsMargins(8, 8, 8, 8)
        cam_layout.setSpacing(8)

        # Cabeçalho da Lista
        self.lbl_sb_header = QLabel("Dispositivos Conectados")
        self.lbl_sb_header.setObjectName("sidebarHeaderTitle")
        cam_layout.addWidget(self.lbl_sb_header)

        # Barra de Ações Rápidas da Lista de Câmeras
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(6)

        btn_sb_add = QPushButton("+ Adicionar Câmera")
        btn_sb_add.setProperty("class", "primary-btn")
        btn_sb_add.setToolTip("Buscar na rede via ONVIF ou adicionar manualmente")
        btn_sb_add.clicked.connect(self._open_discovery_dialog)
        actions_layout.addWidget(btn_sb_add)

        btn_sb_del = QPushButton("- Excluir")
        btn_sb_del.setToolTip("Remover a câmera selecionada")
        btn_sb_del.clicked.connect(self._remove_selected_camera)
        actions_layout.addWidget(btn_sb_del)

        cam_layout.addLayout(actions_layout)

        # Árvore / Lista de Câmeras ocupando 100% da altura
        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderHidden(True)
        self.device_tree.setAnimated(True)
        self.device_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.device_tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.device_tree.itemClicked.connect(self._on_tree_item_clicked)
        cam_layout.addWidget(self.device_tree, stretch=1)

        self.sidebar_tabs.addTab(tab_cameras, "Câmeras")

        # =====================================================================
        # Aba 2: Painel de Controle PTZ
        # =====================================================================
        self.ptz_panel = PTZControllerWidget(parent=self)
        self.ptz_panel.command_executed.connect(self._log_event)
        self.sidebar_tabs.addTab(self.ptz_panel, "Controle PTZ")

        # =====================================================================
        # Aba 3: Painel de Gravações & Playback (Timeline + Vídeos Salvos)
        # =====================================================================
        self.recordings_panel = PlaybackWidget(parent=self)
        self.recordings_panel.playback_requested.connect(
            lambda cname, tstr: self._log_event("Playback", f"Reproduzindo {cname} em {tstr}")
        )
        self.sidebar_tabs.addTab(self.recordings_panel, "Gravações")

        # =====================================================================
        # Aba 4: Painel de Logs de Eventos
        # =====================================================================
        tab_logs = QWidget()
        logs_layout = QVBoxLayout(tab_logs)
        logs_layout.setContentsMargins(8, 8, 8, 8)
        logs_layout.setSpacing(6)

        lbl_logs_title = QLabel("Histórico de Eventos do Sistema:")
        lbl_logs_title.setObjectName("mutedLabel")
        logs_layout.addWidget(lbl_logs_title)

        self.list_logs = QListWidget()
        logs_layout.addWidget(self.list_logs, stretch=1)

        self.sidebar_tabs.addTab(tab_logs, "Logs de Eventos")

        v_layout.addWidget(self.sidebar_tabs)
        return sidebar

    def _setup_status_bar(self):
        """Configura a barra de status inferior."""
        status_bar = self.statusBar()
        
        self.lbl_sb_hw = QLabel(f"Aceleração: {self.config_mgr.get('hw_accel', 'vaapi').upper()} (Ativo)")
        self.lbl_sb_hw.setStyleSheet("color: #38BDF8; font-weight: 600; padding: 0 8px;")
        status_bar.addWidget(self.lbl_sb_hw)

        self.lbl_sb_streams = QLabel(f"Câmeras Conectadas: {len(self.cameras)}")
        self.lbl_sb_streams.setStyleSheet("color: #4ADE80; font-weight: 600; padding: 0 8px;")
        status_bar.addWidget(self.lbl_sb_streams)

        self.btn_sb_update = QPushButton("⚡ Atualização Disponível")
        self.btn_sb_update.setStyleSheet("background-color: #16A34A; color: #FFFFFF; font-weight: 700; font-size: 11px; padding: 2px 8px; border-radius: 4px;")
        self.btn_sb_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sb_update.clicked.connect(self._open_update_dialog)
        self.btn_sb_update.hide()
        status_bar.addWidget(self.btn_sb_update)

        status_bar.addPermanentWidget(QLabel("Central NVR WiFi"))

    def _setup_clock_timer(self):
        """Atualiza o relógio no topo a cada 1 segundo."""
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

    def _update_clock(self):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        self.lbl_clock.setText(now_str)

    def _populate_device_tree(self):
        """Preenche a árvore de dispositivos mostrando apenas o que está cadastrado."""
        self.device_tree.clear()

        if hasattr(self, "lbl_sb_header"):
            self.lbl_sb_header.setText(f"Dispositivos Conectados ({len(self.cameras)})")

        if not self.cameras:
            empty_item = QTreeWidgetItem(self.device_tree)
            empty_item.setText(0, "(Nenhuma câmera conectada)")
            hint_item = QTreeWidgetItem(empty_item)
            hint_item.setText(0, "Clique em '+ Adicionar' para buscar")
            empty_item.setExpanded(True)
            self._log_event("Sistema", "Pronto. Nenhuma câmera conectada.")
            return

        # Nó Raiz NVR Central
        nvr_root = QTreeWidgetItem(self.device_tree)
        nvr_root.setText(0, f"Central NVR ({len(self.cameras)} Câmeras)")
        nvr_root.setExpanded(True)

        for cam in self.cameras:
            cam_item = QTreeWidgetItem(nvr_root)
            ptz_tag = " [PTZ]" if cam.has_ptz else ""
            cam_item.setText(0, f"[Ao Vivo] {cam.name} ({cam.ip}){ptz_tag}")
            cam_item.setData(0, Qt.ItemDataRole.UserRole, cam.id)

        self._log_event("Sistema", f"{len(self.cameras)} câmera(s) carregada(s).")

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Ao clicar em uma câmera na árvore, foca no grid e ativa PTZ."""
        cam_id = item.data(0, Qt.ItemDataRole.UserRole)
        if cam_id:
            self._select_camera_by_id(cam_id)

    def _show_tree_context_menu(self, position):
        """Menu de contexto ao clicar com o botão direito em uma câmera da lista."""
        item = self.device_tree.itemAt(position)
        if not item:
            return
        cam_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not cam_id:
            return

        cam = next((c for c in self.cameras if c.id == cam_id), None)
        if not cam:
            return

        menu = QMenu(self)
        act_view = menu.addAction(f"Focar / Visualizar: {cam.name}")
        act_rename = menu.addAction("✏️ Renomear Câmera...")
        act_ptz = menu.addAction("Abrir Controle PTZ")
        menu.addSeparator()
        act_del = menu.addAction("Excluir Câmera")

        action = menu.exec(self.device_tree.viewport().mapToGlobal(position))
        if action == act_view:
            self._select_camera_by_id(cam_id)
        elif action == act_rename:
            new_name, ok = QInputDialog.getText(
                self,
                "Renomear Câmera",
                f"Digite o novo nome para a câmera ({cam.ip}):",
                text=cam.name,
            )
            if ok and new_name.strip() and new_name.strip() != cam.name:
                self._on_camera_renamed(cam.id, new_name.strip())
        elif action == act_ptz:
            self._select_camera_by_id(cam_id)
            if hasattr(self, "sidebar_tabs"):
                self.sidebar_tabs.setCurrentIndex(1)
        elif action == act_del:
            self._remove_camera_by_id(cam_id)

    def _on_camera_renamed(self, camera_id: str, new_name: str):
        """Atualiza o nome da câmera em todos os componentes do sistema e salva nas configurações."""
        cam = next((c for c in self.cameras if c.id == camera_id), None)
        if not cam:
            return
        old_name = cam.name
        cam.name = new_name

        # 1. Salvar no ConfigManager (devices.json)
        self.config_mgr.rename_device(camera_id, new_name)

        # 2. Atualizar no grid de câmeras
        self.camera_grid.update_camera_name(camera_id, new_name)

        # 3. Atualizar na árvore lateral (device_tree)
        self._populate_device_tree()

        # 4. Atualizar no painel PTZ se for a câmera selecionada
        if hasattr(self, "ptz_panel") and self.ptz_panel.camera and self.ptz_panel.camera.id == camera_id:
            self.ptz_panel.set_camera(cam)

        # 5. Atualizar no painel de Gravações / Timeline se for a câmera selecionada
        if hasattr(self, "recordings_panel") and self.recordings_panel.camera and self.recordings_panel.camera.id == camera_id:
            self.recordings_panel.set_camera(cam)

        # 6. Registrar nos Logs e na Barra de Status
        self._log_event("Dispositivo", f"Câmera renomeada: '{old_name}' ➔ '{new_name}'")
        self.statusBar().showMessage(f"Câmera renomeada para '{new_name}' com sucesso!", 4000)

    def _remove_selected_camera(self):
        """Remove a câmera que estiver atualmente selecionada."""
        selected_item = self.device_tree.currentItem()
        if not selected_item:
            if self.camera_grid.selected_camera_id:
                self._remove_camera_by_id(self.camera_grid.selected_camera_id)
            else:
                QMessageBox.information(self, "Aviso", "Selecione uma câmera na lista para remover.")
            return

        cam_id = selected_item.data(0, Qt.ItemDataRole.UserRole)
        if not cam_id:
            if self.camera_grid.selected_camera_id:
                cam_id = self.camera_grid.selected_camera_id
            else:
                return

        self._remove_camera_by_id(cam_id)

    def _remove_camera_by_id(self, cam_id: str):
        """Confirma e remove uma câmera das configurações e da visualização."""
        cam = next((c for c in self.cameras if c.id == cam_id), None)
        cam_name = cam.name if cam else cam_id

        reply = QMessageBox.question(
            self,
            "Remover Câmera",
            f"Deseja realmente remover a câmera '{cam_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.config_mgr.remove_device(cam_id)
            self._load_cameras_from_config()
            self.camera_grid.set_cameras(self.cameras)
            self._populate_device_tree()
            self.lbl_sb_streams.setText(f"Câmeras Conectadas: {len(self.cameras)}")
            if not self.cameras:
                self.ptz_panel.set_camera(None)
                if hasattr(self, "recordings_panel"):
                    self.recordings_panel.set_camera(None)
            self._log_event("Dispositivo", f"Câmera '{cam_name}' removida com sucesso.")

    def _on_camera_selected(self, camera_id: str):
        """Quando o usuário clica em um viewport no grid."""
        self._select_camera_by_id(camera_id)

    def _select_camera_by_id(self, camera_id: str):
        cam = next((c for c in self.cameras if c.id == camera_id), None)
        if cam:
            self.ptz_panel.set_camera(cam)
            if hasattr(self, "recordings_panel"):
                self.recordings_panel.set_camera(cam)
            self.camera_grid.select_camera(camera_id)

    def _on_ptz_requested(self, camera_id: str):
        self._select_camera_by_id(camera_id)
        if hasattr(self, "sidebar_tabs"):
            self.sidebar_tabs.setCurrentIndex(1)

    def _on_snapshot_saved(self, camera_id: str, filepath: str):
        self._log_event("Snapshot", f"Foto salva em: {os.path.basename(filepath)}")
        self.statusBar().showMessage(f"Snapshot salvo com sucesso: {filepath}", 4000)
        if hasattr(self, "recordings_panel"):
            self.recordings_panel.refresh_recordings()

    def _log_event(self, source: str, message: str):
        """Adiciona entrada formatada no painel de logs."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        item_text = f"[{ts}] {source}: {message}"
        item = QListWidgetItem(item_text)
        self.list_logs.addItem(item)
        self.list_logs.scrollToBottom()

    def _change_grid_layout(self, mode: str):
        self.config_mgr.set("grid_layout", mode)
        self.camera_grid.set_layout_mode(mode)
        self._log_event("Layout", f"Grade alterada para modo {mode}")

    def _on_layout_mode_changed_by_grid(self, mode: str):
        """Sincroniza os botões do cabeçalho quando o layout muda via clique duplo."""
        self.config_mgr.set("grid_layout", mode.lower())
        if hasattr(self, "layout_buttons") and mode.lower() in self.layout_buttons:
            self.layout_buttons[mode.lower()].setChecked(True)
        self._log_event("Layout", f"Grade alternada para {mode}")

    def _on_camera_fullscreen_requested(self, camera_id: str):
        """Abre a janela exclusiva de tela cheia para a câmera selecionada."""
        self._select_camera_by_id(camera_id)
        if hasattr(self, "camera_grid") and camera_id in self.camera_grid.camera_views:
            view = self.camera_grid.camera_views[camera_id]
            view._open_fullscreen_player()

    def _enter_grid_fullscreen(self):
        """Coloca todas as câmeras conectadas em tela cheia (ocultando sidebar, header e status bar)."""
        if hasattr(self, "top_header") and self.top_header:
            self.top_header.hide()
        if hasattr(self, "sidebar") and self.sidebar:
            self.sidebar.hide()
        if self.statusBar():
            self.statusBar().hide()
        if hasattr(self, "camera_grid"):
            self.camera_grid.layout.setContentsMargins(0, 0, 0, 0)
        self.showFullScreen()
        if hasattr(self, "btn_floating_exit_fullscreen"):
            self.btn_floating_exit_fullscreen.show()
            self.btn_floating_exit_fullscreen.raise_()
            self.btn_floating_exit_fullscreen.move(
                max(10, self.right_workspace.width() - self.btn_floating_exit_fullscreen.width() - 16), 12
            )
        self._log_event("Exibição", "Mosaico com todas as câmeras expandido em Tela Cheia.")

    def _exit_grid_fullscreen(self):
        """Restaura a visualização padrão com sidebar, header e status bar."""
        if hasattr(self, "btn_floating_exit_fullscreen"):
            self.btn_floating_exit_fullscreen.hide()
        if hasattr(self, "top_header") and self.top_header:
            self.top_header.show()
        if hasattr(self, "sidebar") and self.sidebar:
            self.sidebar.show()
        if self.statusBar():
            self.statusBar().show()
        if hasattr(self, "camera_grid"):
            self.camera_grid.layout.setContentsMargins(4, 4, 4, 4)
        self.showNormal()
        self._log_event("Exibição", "Tela Cheia encerrada, retornando à visualização com painéis.")

    def _toggle_fullscreen(self):
        """Alterna o modo de Tela Cheia do Mosaico (todas as câmeras conectadas)."""
        if self.isFullScreen():
            self._exit_grid_fullscreen()
        else:
            self._enter_grid_fullscreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "btn_floating_exit_fullscreen") and self.btn_floating_exit_fullscreen.isVisible():
            self.btn_floating_exit_fullscreen.move(
                max(10, self.right_workspace.width() - self.btn_floating_exit_fullscreen.width() - 16), 12
            )
            self.btn_floating_exit_fullscreen.raise_()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self._toggle_fullscreen()
        elif event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self._exit_grid_fullscreen()
        super().keyPressEvent(event)

    def _open_discovery_dialog(self):
        dialog = DiscoveryDialog(parent=self)
        dialog.device_added.connect(self._add_discovered_camera)
        dialog.exec()

    def _add_discovered_camera(self, camera: CameraDevice):
        self.config_mgr.add_or_update_device(camera.to_dict())
        self._load_cameras_from_config()
        self.camera_grid.set_cameras(self.cameras)
        self._populate_device_tree()
        self.lbl_sb_streams.setText(f"Fluxos Ativos: {len(self.cameras)}")
        self._log_event("Dispositivo", f"Câmera '{camera.name}' adicionada ao sistema.")

    def _set_theme(self, theme_name: str):
        """Aplica o tema selecionado (dark ou light) e salva nas preferências."""
        theme = (theme_name or "dark").lower()
        self.current_theme = theme
        self.config_mgr.set("theme", theme)
        self.config_mgr.save_settings()
        self.setStyleSheet(get_theme_qss(theme))

        if hasattr(self, "act_theme_dark"):
            self.act_theme_dark.setChecked(theme == "dark")
        if hasattr(self, "act_theme_light"):
            self.act_theme_light.setChecked(theme == "light")

        self._log_event("Aparência", f"Tema alterado para '{theme.capitalize()}'")

    def _toggle_theme(self):
        """Alterna entre Tema Claro e Tema Escuro."""
        next_theme = "dark" if self.current_theme == "light" else "light"
        self._set_theme(next_theme)

    def _open_settings_dialog(self):
        dialog = SettingsDialog(config_mgr=self.config_mgr, parent=self)
        if dialog.exec():
            # Atualizar parâmetros de tema, hardware e reconectar
            theme = self.config_mgr.get("theme", "dark").lower()
            if theme != self.current_theme:
                self.current_theme = theme
                self.setStyleSheet(get_theme_qss(self.current_theme))
                self._set_theme(theme)
            hw_accel = self.config_mgr.get("hw_accel", "vaapi")
            self.lbl_sb_hw.setText(f"Aceleração: {hw_accel.upper()} (Ativo)")
            self.camera_grid.hw_accel = hw_accel
            self.camera_grid.rtsp_transport = self.config_mgr.get("rtsp_transport", "auto")
            self.camera_grid.set_cameras(self.cameras)


    def _setup_periodic_update_checker(self):
        """Configura a verificação periódica em segundo plano (a cada 10 minutos)."""
        self._notified_version: Optional[str] = None
        self._periodic_worker: Optional[UpdateCheckWorker] = None
        
        enabled = self.config_mgr.get("periodic_update_check", True)
        if not enabled:
            return

        interval_min = self.config_mgr.get("periodic_update_interval_min", 10)
        interval_ms = max(1, interval_min) * 60 * 1000

        self.update_periodic_timer = QTimer(self)
        self.update_periodic_timer.timeout.connect(self._run_background_update_check)
        self.update_periodic_timer.start(interval_ms)
        logger.debug(f"Verificação periódica de atualizações ativa (intervalo: {interval_min} min).")

    def _run_background_update_check(self):
        """Dispara consulta assíncrona ao GitHub em segundo plano."""
        repo = self.config_mgr.get("github_repo", "Othayz/central-nvr-wifi")
        token = self.config_mgr.get("github_token", "").strip() or None
        self._periodic_worker = UpdateCheckWorker(repo=repo, token=token, parent=self)
        self._periodic_worker.update_available.connect(self._on_background_update_found)
        self._periodic_worker.start()

    def _on_background_update_found(self, release: ReleaseInfo):
        """Abre o diálogo de atualização quando a checagem de 10 minutos encontra uma nova versão."""
        if hasattr(self, "btn_sb_update"):
            self.btn_sb_update.setText(f"⚡ Nova Versão v{release.version} Disponível!")
            self.btn_sb_update.show()

        self._log_event("Atualização", f"Nova versão v{release.version} encontrada no GitHub.")

        # Notificar o usuário abrindo a janela de atualização (apenas uma vez por versão lançada)
        if self._notified_version != release.version:
            self._notified_version = release.version
            repo = self.config_mgr.get("github_repo", "Othayz/central-nvr-wifi")
            token = self.config_mgr.get("github_token", "").strip() or None
            dialog = UpdateDialog(repo=repo, release_info=release, token=token, parent=self)
            dialog.exec()

    def _open_update_dialog(self):
        """Abre a janela de verificação de atualização sob demanda do usuário."""
        repo = self.config_mgr.get("github_repo", "Othayz/central-nvr-wifi")
        token = self.config_mgr.get("github_token", "").strip() or None
        dialog = UpdateDialog(repo=repo, token=token, parent=self)
        dialog.exec()

    def closeEvent(self, event):
        """Encerra threads de forma graciosa ao fechar a janela."""
        self.camera_grid.stop_all()
        super().closeEvent(event)
