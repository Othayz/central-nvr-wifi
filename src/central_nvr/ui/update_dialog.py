#!/usr/bin/env python3
"""
Interface Gráfica para Verificação e Instalação de Atualizações via GitHub Releases.
Inclui StartupUpdateDialog (Modo Splash de Inicialização) e UpdateDialog (Modo Periódico/Manual).
"""
import html
import logging
import os
import tempfile
from typing import Optional

from PySide6.QtCore import QEventLoop, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from central_nvr import __version__
from central_nvr.core.config import get_data_dir
from central_nvr.core.updater import (
    AssetDownloadWorker,
    DEFAULT_GITHUB_REPO,
    ReleaseAsset,
    ReleaseInfo,
    UpdateCheckWorker,
    find_best_asset_for_system,
    install_downloaded_package,
)

logger = logging.getLogger(__name__)


def _format_markdown_to_html(md_text: str) -> str:
    """Converte markdown simples de changelog em HTML com estilo elegante."""
    if not md_text:
        return "<i>Nenhuma nota de versão disponibilizada.</i>"
    
    lines = md_text.splitlines()
    html_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            html_lines.append(f"<h4 style='color:#0284C7; margin:8px 0 4px 0;'>{html.escape(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h3 style='color:#0284C7; margin:10px 0 6px 0;'>{html.escape(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h2 style='color:#0284C7; margin:12px 0 8px 0;'>{html.escape(stripped[2:])}</h2>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            content = html.escape(stripped[2:])
            html_lines.append(f"<li style='margin-bottom:3px;'>{content}</li>")
        elif stripped.startswith("> "):
            content = html.escape(stripped[2:])
            html_lines.append(f"<blockquote style='color:#94A3B8; margin:4px 0; padding-left:8px; border-left:3px solid #0284C7;'>{content}</blockquote>")
        elif not stripped:
            html_lines.append("<br>")
        else:
            html_lines.append(f"<p style='margin:2px 0;'>{html.escape(stripped)}</p>")

    return "".join(html_lines)


class StartupUpdateDialog(QDialog):
    """
    Tela de Inicialização / Splash que verifica automaticamente se há uma nova versão no GitHub.
    - Se encontrar: pergunta ao usuário se deseja atualizar (com changelog e download).
    - Se não encontrar (ou falhar/offline): fecha suavemente e inicia a aplicação normalmente.
    """

    def __init__(self, repo: str = DEFAULT_GITHUB_REPO, token: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.repo = repo
        self.token = token
        self.release_info: Optional[ReleaseInfo] = None
        self.best_asset: Optional[ReleaseAsset] = None
        self.download_worker: Optional[AssetDownloadWorker] = None
        self.downloaded_file_path: Optional[str] = None
        self.should_open_main_window = True

        self.setWindowTitle("Central NVR WiFi")
        self.setFixedSize(500, 260)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._setup_ui()
        self._start_check()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # 1. Cabeçalho com Logo e Título
        header_layout = QHBoxLayout()
        header_layout.setSpacing(16)

        self.lbl_logo = QLabel()
        from pathlib import Path
        icon_path = Path(__file__).parent / "assets" / "central-nvr.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.lbl_logo.setPixmap(pix)
        header_layout.addWidget(self.lbl_logo)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)

        lbl_app_name = QLabel("Central NVR WiFi")
        lbl_app_name.setStyleSheet("font-size: 18px; font-weight: 800; color: #0284C7;")
        title_vbox.addWidget(lbl_app_name)

        self.lbl_current_ver = QLabel(f"Versão Atual: v{__version__}")
        self.lbl_current_ver.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 600;")
        title_vbox.addWidget(self.lbl_current_ver)

        header_layout.addLayout(title_vbox)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # 2. Área de Status / Carregamento
        self.status_container = QFrame()
        self.status_container.setStyleSheet("background-color: rgba(15, 23, 42, 0.04); border-radius: 8px; padding: 10px;")
        status_layout = QVBoxLayout(self.status_container)
        status_layout.setContentsMargins(8, 8, 8, 8)
        status_layout.setSpacing(8)

        self.lbl_status = QLabel("Verificando se há atualizações no GitHub...")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #334155;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        status_layout.addWidget(self.progress_bar)

        layout.addWidget(self.status_container)

        # 3. Painel de Atualização (inicialmente oculto)
        self.update_panel = QWidget()
        self.update_panel.hide()
        up_layout = QVBoxLayout(self.update_panel)
        up_layout.setContentsMargins(0, 0, 0, 0)
        up_layout.setSpacing(8)

        self.lbl_update_headline = QLabel("🎉 Nova versão encontrada!")
        self.lbl_update_headline.setStyleSheet("font-size: 14px; font-weight: 700; color: #16A34A;")
        up_layout.addWidget(self.lbl_update_headline)

        self.txt_changelog = QTextBrowser()
        self.txt_changelog.setFixedHeight(120)
        self.txt_changelog.setOpenExternalLinks(True)
        self.txt_changelog.setStyleSheet("background-color: #F8FAFC; border: 1px solid #CBD5E1; border-radius: 6px; padding: 6px; font-size: 11px;")
        up_layout.addWidget(self.txt_changelog)

        layout.addWidget(self.update_panel)

        # 4. Barra de Ações Inferior
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(10)

        self.btn_github = QPushButton("🔗 Ver no GitHub")
        self.btn_github.clicked.connect(self._open_github_release)
        self.btn_github.hide()
        self.btn_layout.addWidget(self.btn_github)

        self.btn_layout.addStretch()

        self.btn_skip = QPushButton("Continuar para o Aplicativo")
        self.btn_skip.clicked.connect(self._proceed_to_app)
        self.btn_skip.hide()
        self.btn_layout.addWidget(self.btn_skip)

        self.btn_update = QPushButton("⬇️ Baixar e Instalar Agora")
        self.btn_update.setProperty("class", "primary-btn")
        self.btn_update.clicked.connect(self._start_download_and_install)
        self.btn_update.hide()
        self.btn_layout.addWidget(self.btn_update)

        layout.addLayout(self.btn_layout)

    def _start_check(self):
        self.check_worker = UpdateCheckWorker(repo=self.repo, token=self.token, parent=self)
        self.check_worker.update_available.connect(self._on_update_found)
        self.check_worker.no_update_available.connect(self._on_no_update)
        self.check_worker.check_failed.connect(self._on_check_failed)
        self.check_worker.start()

    def _on_no_update(self, release_info: Optional[ReleaseInfo]):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"✓ Aplicativo atualizado (v{__version__}). Abrindo...")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #16A34A;")
        QTimer.singleShot(700, self._proceed_to_app)

    def _on_check_failed(self, error_msg: str):
        logger.debug(f"Checagem inicial de versão: {error_msg}")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.lbl_status.setText("Iniciando Central NVR WiFi...")
        QTimer.singleShot(400, self._proceed_to_app)

    def _on_update_found(self, release: ReleaseInfo):
        self.release_info = release
        self.best_asset = find_best_asset_for_system(release.assets)

        self.setFixedSize(540, 430)
        self.status_container.hide()
        self.update_panel.show()

        asset_info = f" ({self.best_asset.name} - {self.best_asset.formatted_size})" if self.best_asset else ""
        self.lbl_update_headline.setText(f"🎉 Nova versão v{release.version} disponível!{asset_info}")
        self.lbl_current_ver.setText(f"Versão Atual: v{__version__}  ➔  Nova: v{release.version}")

        html_body = f"<b>Publicado em:</b> {release.formatted_date}<br><hr style='border:0; border-top:1px solid #E2E8F0;'/>"
        html_body += _format_markdown_to_html(release.body)
        self.txt_changelog.setHtml(html_body)

        self.btn_github.show()
        self.btn_skip.show()
        self.btn_update.show()

    def _open_github_release(self):
        if self.release_info and self.release_info.html_url:
            QDesktopServices.openUrl(QUrl(self.release_info.html_url))

    def _proceed_to_app(self):
        self.should_open_main_window = True
        self.accept()

    def _start_download_and_install(self):
        if not self.release_info:
            return

        if not self.best_asset:
            if self.release_info.html_url:
                QDesktopServices.openUrl(QUrl(self.release_info.html_url))
            QMessageBox.information(
                self,
                "Atualização",
                f"A página da versão v{self.release_info.version} foi aberta no navegador para download manual.",
            )
            self._proceed_to_app()
            return

        self.status_container.show()
        self.lbl_status.setText(f"Baixando {self.best_asset.name}...")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600; color: #0284C7;")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.btn_skip.setEnabled(False)
        self.btn_update.setEnabled(False)
        self.btn_update.setText("Baixando...")

        from central_nvr.core.updater import get_updates_dir
        dest_dir = get_updates_dir()
        dest_file = os.path.join(str(dest_dir), self.best_asset.name)

        self.download_worker = AssetDownloadWorker(
            download_url=self.best_asset.download_url,
            destination_path=dest_file,
            token=self.token,
            parent=self,
        )
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.download_finished.connect(self._on_download_finished)
        self.download_worker.download_failed.connect(self._on_download_failed)
        self.download_worker.start()

    def _on_download_progress(self, downloaded: int, total: int, speed_kbps: float):
        if total > 0:
            pct = int((downloaded / total) * 100)
            self.progress_bar.setValue(pct)
            dl_mb = downloaded / (1024 * 1024)
            tot_mb = total / (1024 * 1024)
            self.lbl_status.setText(f"Baixando: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({pct}%) • {speed_kbps:.0f} KB/s")

    def _on_download_finished(self, file_path: str):
        self.downloaded_file_path = file_path
        self.progress_bar.setValue(100)
        self.lbl_status.setText("✓ Download concluído com sucesso!")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #16A34A;")

        self.btn_skip.setEnabled(True)
        self.btn_skip.setText("Fechar")
        self.btn_update.setEnabled(True)
        self.btn_update.setText("⚡ Instalar Agora")
        try:
            self.btn_update.clicked.disconnect()
        except Exception:
            pass
        self.btn_update.clicked.connect(self._execute_installation)

        reply = QMessageBox.question(
            self,
            "Atualização Baixada",
            f"O pacote {os.path.basename(file_path)} foi baixado com sucesso!\n\n"
            "Deseja iniciar a instalação agora?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._execute_installation()

    def _on_download_failed(self, error_msg: str):
        self.lbl_status.setText(f"❌ Erro no download: {error_msg}")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 700; color: #EF4444;")
        self.btn_skip.setEnabled(True)
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Tentar Novamente")
        try:
            self.btn_update.clicked.disconnect()
        except Exception:
            pass
        self.btn_update.clicked.connect(self._start_download_and_install)

    def _execute_installation(self):
        if not self.downloaded_file_path or not os.path.exists(self.downloaded_file_path):
            QMessageBox.warning(self, "Erro", "Arquivo de atualização não encontrado.")
            return

        from central_nvr.core.updater import get_expected_sha256_for_asset
        expected_sha = None
        if self.release_info and self.best_asset:
            expected_sha = get_expected_sha256_for_asset(self.release_info, self.best_asset.name, token=self.token)

        success, msg = install_downloaded_package(self.downloaded_file_path, expected_sha256=expected_sha)
        if success:
            QMessageBox.information(
                self,
                "Instalação Iniciada",
                f"{msg}\n\nO aplicativo será encerrado para concluir a instalação.",
            )
            self.should_open_main_window = False
            self.reject()
        else:
            QMessageBox.critical(self, "Falha na Instalação", f"Não foi possível iniciar o instalador:\n{msg}")



    def closeEvent(self, event):
        self._cleanup_workers()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_workers()
        super().reject()

    def _cleanup_workers(self):
        if hasattr(self, "check_worker") and self.check_worker and self.check_worker.isRunning():
            try:
                self.check_worker.quit()
                self.check_worker.wait(300)
            except Exception:
                pass
        if hasattr(self, "download_worker") and self.download_worker and self.download_worker.isRunning():
            try:
                self.download_worker.cancel()
                self.download_worker.wait(500)
            except Exception:
                pass


class UpdateDialog(QDialog):
    """
    Diálogo para verificação manual ou notificação periódica (a cada 10 minutos).
    """

    def __init__(
        self,
        repo: str = DEFAULT_GITHUB_REPO,
        release_info: Optional[ReleaseInfo] = None,
        token: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.token = token
        self.release_info = release_info
        self.best_asset = find_best_asset_for_system(release_info.assets) if release_info else None
        self.download_worker: Optional[AssetDownloadWorker] = None
        self.downloaded_file_path: Optional[str] = None

        self.setWindowTitle("Atualização de Software - Central NVR WiFi")
        self.resize(540, 440)
        self.setMinimumSize(480, 360)

        self._setup_ui()

        if self.release_info is None:
            self._start_check()
        else:
            self._display_release(self.release_info)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Header
        header_box = QHBoxLayout()
        lbl_icon = QLabel()
        from pathlib import Path
        icon_path = Path(__file__).parent / "assets" / "central-nvr.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl_icon.setPixmap(pix)
        header_box.addWidget(lbl_icon)

        title_vbox = QVBoxLayout()
        self.lbl_dialog_title = QLabel("Atualizações da Central NVR WiFi")
        self.lbl_dialog_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #0284C7;")
        title_vbox.addWidget(self.lbl_dialog_title)

        self.lbl_version_status = QLabel(f"Versão instalada: v{__version__}")
        self.lbl_version_status.setStyleSheet("font-size: 11px; color: #64748B;")
        title_vbox.addWidget(self.lbl_version_status)

        header_box.addLayout(title_vbox)
        header_box.addStretch()
        layout.addLayout(header_box)

        # Área de Status e Barra de Progresso
        self.status_box = QFrame()
        self.status_box.setStyleSheet("background-color: rgba(15, 23, 42, 0.04); border-radius: 6px; padding: 6px;")
        s_layout = QVBoxLayout(self.status_box)
        self.lbl_status = QLabel("Consultando releases no GitHub...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        s_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        s_layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_box)

        # Área de Changelog
        self.txt_changelog = QTextBrowser()
        self.txt_changelog.setOpenExternalLinks(True)
        self.txt_changelog.setStyleSheet("background-color: #F8FAFC; border: 1px solid #CBD5E1; border-radius: 6px; padding: 8px;")
        layout.addWidget(self.txt_changelog, stretch=1)

        # Botões Inferiores
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_github = QPushButton("🔗 Ver no GitHub")
        self.btn_github.clicked.connect(self._open_github)
        btn_layout.addWidget(self.btn_github)

        btn_layout.addStretch()

        self.btn_close = QPushButton("Fechar")
        self.btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_close)

        self.btn_action = QPushButton("⬇️ Baixar e Instalar")
        self.btn_action.setProperty("class", "primary-btn")
        self.btn_action.clicked.connect(self._on_action_clicked)
        btn_layout.addWidget(self.btn_action)

        layout.addLayout(btn_layout)

    def _start_check(self):
        self.status_box.show()
        self.progress_bar.setRange(0, 0)
        self.lbl_status.setText("Verificando se há novas versões no GitHub...")
        self.btn_action.setEnabled(False)

        self.worker = UpdateCheckWorker(repo=self.repo, token=self.token, parent=self)
        self.worker.update_available.connect(self._display_release)
        self.worker.no_update_available.connect(self._on_up_to_date)
        self.worker.check_failed.connect(self._on_check_error)
        self.worker.start()

    def _display_release(self, release: ReleaseInfo):
        self.release_info = release
        self.best_asset = find_best_asset_for_system(release.assets)

        self.status_box.hide()
        self.lbl_dialog_title.setText(f"🎉 Nova versão disponível: v{release.version}")
        self.lbl_dialog_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #16A34A;")
        self.lbl_version_status.setText(f"Instalada: v{__version__}  ➔  Disponível: v{release.version}")

        asset_str = f"<p><b>Pacote recomendado:</b> <code>{self.best_asset.name}</code> ({self.best_asset.formatted_size})</p>" if self.best_asset else ""
        html_body = f"<b>Data de Lançamento:</b> {release.formatted_date}{asset_str}<hr style='border:0; border-top:1px solid #CBD5E1;'/>"
        html_body += _format_markdown_to_html(release.body)
        self.txt_changelog.setHtml(html_body)

        self.btn_action.show()
        self.btn_action.setEnabled(True)
        self.btn_action.setText("⬇️ Baixar e Instalar")

    def _on_up_to_date(self, release: Optional[ReleaseInfo]):
        self.status_box.show()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"✓ A Central NVR WiFi está atualizada na versão mais recente (v{__version__}).")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 700; color: #16A34A;")
        self.txt_changelog.setHtml("<p style='text-align:center; color:#64748B; margin-top:30px;'>Você já está usando a versão mais recente do aplicativo.<br>Nenhuma ação necessária.</p>")
        self.btn_action.setEnabled(False)
        self.btn_action.hide()

    def _on_check_error(self, err: str):
        self.status_box.show()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        first_line = err.splitlines()[0] if err else "Erro desconhecido"
        self.lbl_status.setText(f"Não foi possível verificar atualizações: {first_line}")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600; color: #EF4444;")
        self.btn_action.setEnabled(False)

        formatted_err = err.replace("\n", "<br>")
        self.txt_changelog.setHtml(f"""
        <div style='padding: 14px; font-family: sans-serif; color: #334155; line-height: 1.6;'>
            <h3 style='color: #DC2626; margin-top: 0;'>⚠️ Falha ao verificar atualizações</h3>
            <p style='background-color: #FEE2E2; border-left: 4px solid #EF4444; padding: 10px; border-radius: 4px; color: #991B1B;'>
                <b>Diagnóstico:</b> {formatted_err}
            </p>
            <h4 style='margin-bottom: 6px; color: #0F172A;'>Como resolver:</h4>
            <ul style='margin-top: 0; padding-left: 20px;'>
                <li><b>Se o projeto for de código aberto:</b> No GitHub, vá em <i>Settings &gt; General &gt; Danger Zone &gt; Change visibility</i> e mude para <b>Public</b>.</li>
                <li><b>Se desejar manter o repositório Privado:</b> Acesse o menu <i>Configurações &gt; Atualizações do Aplicativo</i> e insira um <b>GitHub Token (PAT)</b>.</li>
                <li><b>Se nenhuma versão foi lançada ainda:</b> No GitHub, acesse a aba <i>Releases</i> e publique a primeira versão (ex: <code>v1.0.0</code>) anexando os instaladores.</li>
            </ul>
        </div>
        """)

    def _open_github(self):
        url = self.release_info.html_url if self.release_info else f"https://github.com/{self.repo}/releases"
        QDesktopServices.openUrl(QUrl(url))

    def _on_action_clicked(self):
        if not self.release_info or not self.best_asset:
            self._open_github()
            return

        self.status_box.show()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.lbl_status.setText(f"Iniciando download de {self.best_asset.name}...")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600; color: #0284C7;")
        self.btn_action.setEnabled(False)
        self.btn_action.setText("Baixando...")

        dest_dir = os.path.join(tempfile.gettempdir(), "central_nvr_updates")
        os.makedirs(dest_dir, exist_ok=True)
        dest_file = os.path.join(dest_dir, self.best_asset.name)

        self.download_worker = AssetDownloadWorker(
            download_url=self.best_asset.download_url,
            destination_path=dest_file,
            parent=self,
        )
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.download_finished.connect(self._on_download_finished)
        self.download_worker.download_failed.connect(self._on_download_failed)
        self.download_worker.start()

    def _on_download_progress(self, dl: int, tot: int, spd: float):
        if tot > 0:
            pct = int((dl / tot) * 100)
            self.progress_bar.setValue(pct)
            self.lbl_status.setText(f"Baixando: {dl/(1024*1024):.1f} MB / {tot/(1024*1024):.1f} MB ({pct}%) • {spd:.0f} KB/s")

    def _on_download_finished(self, file_path: str):
        self.downloaded_file_path = file_path
        self.progress_bar.setValue(100)
        self.lbl_status.setText("✓ Pacote baixado com sucesso!")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 700; color: #16A34A;")

        self.btn_action.setEnabled(True)
        self.btn_action.setText("⚡ Instalar Pacote")
        try:
            self.btn_action.clicked.disconnect()
        except Exception:
            pass
        self.btn_action.clicked.connect(self._install_package)

    def _on_download_failed(self, err: str):
        self.lbl_status.setText(f"Falha no download: {err}")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 700; color: #EF4444;")
        self.btn_action.setEnabled(True)
        self.btn_action.setText("Tentar Novamente")

    def _install_package(self):
        if self.downloaded_file_path and os.path.exists(self.downloaded_file_path):
            success, msg = install_downloaded_package(self.downloaded_file_path)
            if success:
                QMessageBox.information(self, "Instalação", f"{msg}\n\nO aplicativo deve ser reiniciado após a instalação.")
                self.accept()
            else:
                QMessageBox.critical(self, "Erro", f"Falha ao iniciar instalação: {msg}")

    def closeEvent(self, event):
        self._cleanup_workers()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_workers()
        super().reject()

    def _cleanup_workers(self):
        if hasattr(self, "check_worker") and self.check_worker and self.check_worker.isRunning():
            try:
                self.check_worker.quit()
                self.check_worker.wait(300)
            except Exception:
                pass
        if hasattr(self, "download_worker") and self.download_worker and self.download_worker.isRunning():
            try:
                self.download_worker.cancel()
                self.download_worker.wait(500)
            except Exception:
                pass
