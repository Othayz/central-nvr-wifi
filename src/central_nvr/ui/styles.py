"""
Definições Globais de Estilos QSS (Dark & Light Theme) para a Central NVR WiFi.
Design moderno com paleta de cores harmoniosa, alto contraste e tipografia refinada.
Garante 100% de legibilidade em todos os textos, menus de contexto, listas e caixas de diálogo.
"""

DARK_THEME_QSS = """
/* =========================================================================
   Reset Global & Base (Tema Escuro)
   ========================================================================= */

* {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 12px;
    color: #F1F5F9;
}

QMainWindow, QWidget, QDialog {
    background-color: #0B1120;
    color: #F1F5F9;
}

QWidget:focus {
    outline: none;
}

QSplitter::handle {
    background-color: #1E293B;
}

QToolTip {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #38BDF8;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}

/* =========================================================================
   Top Bar / Header
   ========================================================================= */

#topHeaderWidget {
    background-color: #0F172A;
    border-bottom: 1px solid #1E293B;
    min-height: 48px;
    max-height: 48px;
}

#appLogoTitle {
    font-size: 15px;
    font-weight: 700;
    color: #38BDF8;
}

#appVersionBadge {
    font-size: 10px;
    color: #94A3B8;
    background-color: #1E293B;
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
}

#systemClockLabel {
    font-size: 12px;
    font-weight: 700;
    color: #F8FAFC;
    background-color: #0B1120;
    border: 1px solid #1E293B;
    border-radius: 6px;
    padding: 4px 10px;
}

/* =========================================================================
   Botões e Controles
   ========================================================================= */

QPushButton {
    background-color: #1E293B;
    color: #F1F5F9;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 5px 12px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #334155;
    border-color: #475569;
    color: #FFFFFF;
}

QPushButton:pressed {
    background-color: #0F172A;
}

QPushButton:disabled {
    background-color: #1E293B;
    color: #64748B;
    border-color: #1E293B;
}

/* Botões Primários */
QPushButton.primary-btn {
    background-color: #2563EB;
    border: 1px solid #3B82F6;
    color: #FFFFFF;
    font-weight: 600;
}

QPushButton.primary-btn:hover {
    background-color: #1D4ED8;
    border-color: #60A5FA;
}

QPushButton.primary-btn:pressed {
    background-color: #1E40AF;
}

QPushButton.primary-btn:disabled {
    background-color: #334155;
    border-color: #475569;
    color: #64748B;
}

/* Botões de Cabeçalho */
QPushButton.header-btn {
    background-color: #0B1120;
    border: 1px solid #1E293B;
    border-radius: 6px;
    padding: 5px 11px;
    font-weight: 600;
    color: #CBD5E1;
}

QPushButton.header-btn:hover {
    background-color: #1E293B;
    border-color: #38BDF8;
    color: #38BDF8;
}

QPushButton.header-btn:checked {
    background-color: #0284C7;
    border-color: #38BDF8;
    color: #FFFFFF;
}

QPushButton.card-action-btn {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #F1F5F9;
    padding: 6px 10px;
    font-weight: 600;
    font-size: 11px;
}

QPushButton.card-action-btn:hover {
    background-color: #334155;
    border-color: #38BDF8;
    color: #38BDF8;
}

QPushButton.dpad-btn {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #F1F5F9;
    font-size: 14px;
    font-weight: bold;
}

QPushButton.dpad-btn:hover {
    background-color: #2563EB;
    border-color: #38BDF8;
    color: #FFFFFF;
}

QPushButton.dpad-stop-btn {
    background-color: #7F1D1D;
    border: 1px solid #EF4444;
    border-radius: 6px;
    color: #FEE2E2;
    font-size: 10px;
    font-weight: bold;
}

QPushButton.dpad-stop-btn:hover {
    background-color: #DC2626;
    color: #FFFFFF;
}

QPushButton.link-btn {
    background-color: transparent;
    border: none;
    color: #38BDF8;
    font-size: 11px;
    font-weight: 600;
}

QPushButton.link-btn:hover {
    color: #FFFFFF;
}

/* =========================================================================
   Sidebar e Navegação por Abas
   ========================================================================= */

#sidebarWidget {
    background-color: #0F172A;
    border-right: 1px solid #1E293B;
}

#sidebarHeaderTitle {
    color: #F8FAFC;
    font-weight: 700;
    font-size: 13px;
}

#sectionTitle, .section-title {
    color: #E2E8F0;
    font-size: 12px;
    font-weight: 700;
}

#mutedLabel {
    color: #94A3B8;
    font-size: 10px;
    font-weight: 600;
}

#cardAccentTitle {
    color: #38BDF8;
    font-size: 13px;
    font-weight: 700;
}

#cardFrame, #dpadFrame, #timelineBar {
    background-color: #0B1120;
    border: 1px solid #1E293B;
    border-radius: 8px;
}

#infoBanner {
    background-color: #0F172A;
    border: 1px solid #0284C7;
    color: #93C5FD;
    padding: 8px;
    border-radius: 6px;
    font-size: 11px;
}

QTabWidget::pane {
    border: 1px solid #1E293B;
    background-color: #0F172A;
    border-radius: 6px;
}

QTabBar::tab {
    background-color: #0B1120;
    border: 1px solid #1E293B;
    color: #94A3B8;
    padding: 7px 12px;
    font-weight: 600;
    font-size: 11px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}

QTabBar::tab:hover {
    background-color: #1E293B;
    color: #38BDF8;
}

QTabBar::tab:selected {
    background-color: #0F172A;
    border-bottom: 2px solid #38BDF8;
    color: #F8FAFC;
    font-weight: 700;
}

/* =========================================================================
   Listas, Árvores e Tabelas
   ========================================================================= */

QTreeWidget, QListWidget, QTableWidget {
    background-color: #0B1120;
    border: 1px solid #1E293B;
    border-radius: 6px;
    color: #E2E8F0;
}

QTreeWidget::item, QListWidget::item, QTableWidget::item {
    padding: 6px 8px;
    min-height: 28px;
    color: #E2E8F0;
}

QTreeWidget::item:hover, QListWidget::item:hover, QTableWidget::item:hover {
    background-color: #1E293B;
    color: #FFFFFF;
}

QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {
    background-color: #1D4ED8;
    color: #FFFFFF;
    font-weight: 600;
}

QHeaderView::section {
    background-color: #0F172A;
    color: #94A3B8;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #1E293B;
    border-bottom: 1px solid #1E293B;
    font-weight: 600;
}

/* =========================================================================
   Formulários, Menus e Diálogos
   ========================================================================= */

QLineEdit, QSpinBox, QComboBox {
    background-color: #0B1120;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 5px 8px;
    color: #F8FAFC;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #38BDF8;
    background-color: #0F172A;
    color: #FFFFFF;
}

QComboBox QAbstractItemView {
    background-color: #0F172A;
    border: 1px solid #334155;
    selection-background-color: #2563EB;
    color: #F8FAFC;
}

QGroupBox {
    border: 1px solid #1E293B;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 700;
    color: #38BDF8;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

QMenu {
    background-color: #0F172A;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 4px;
    color: #F8FAFC;
}

QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
    color: #F8FAFC;
    font-size: 12px;
    background-color: transparent;
}

QMenu::item:selected {
    background-color: #2563EB;
    color: #FFFFFF;
}

QMenu::separator {
    height: 1px;
    background-color: #334155;
    margin: 4px 6px;
}

QDialog, QMessageBox, QInputDialog {
    background-color: #0F172A;
    color: #F8FAFC;
}

QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel {
    color: #F8FAFC;
}

/* =========================================================================
   Status Bar & Scrollbars
   ========================================================================= */

QStatusBar {
    background-color: #0F172A;
    border-top: 1px solid #1E293B;
    color: #94A3B8;
    font-size: 11px;
    padding: 2px 8px;
}

QScrollBar:vertical {
    background: #0B1120;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #334155;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #475569;
}
"""

LIGHT_THEME_QSS = """
/* =========================================================================
   Reset Global & Base (Tema Claro)
   ========================================================================= */

* {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 12px;
    color: #0F172A;
}

QMainWindow, QWidget, QDialog {
    background-color: #F1F5F9;
    color: #0F172A;
}

QWidget:focus {
    outline: none;
}

QSplitter::handle {
    background-color: #CBD5E1;
}

QToolTip {
    background-color: #FFFFFF;
    color: #0F172A;
    border: 1px solid #0284C7;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
}

/* =========================================================================
   Top Bar / Header
   ========================================================================= */

#topHeaderWidget {
    background-color: #FFFFFF;
    border-bottom: 1px solid #E2E8F0;
    min-height: 48px;
    max-height: 48px;
}

#appLogoTitle {
    font-size: 15px;
    font-weight: 700;
    color: #0284C7;
}

#appVersionBadge {
    font-size: 10px;
    color: #0369A1;
    background-color: #E0F2FE;
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
}

#systemClockLabel {
    font-size: 12px;
    font-weight: 700;
    color: #0F172A;
    background-color: #F8FAFC;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 4px 10px;
}

/* =========================================================================
   Botões e Controles
   ========================================================================= */

QPushButton {
    background-color: #F8FAFC;
    color: #0F172A;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 5px 12px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #E2E8F0;
    border-color: #94A3B8;
    color: #0F172A;
}

QPushButton:pressed {
    background-color: #CBD5E1;
}

QPushButton:disabled {
    background-color: #F1F5F9;
    color: #94A3B8;
    border-color: #E2E8F0;
}

/* Botões Primários */
QPushButton.primary-btn {
    background-color: #2563EB;
    border: 1px solid #1D4ED8;
    color: #FFFFFF;
    font-weight: 600;
}

QPushButton.primary-btn:hover {
    background-color: #1D4ED8;
    border-color: #1E40AF;
    color: #FFFFFF;
}

QPushButton.primary-btn:pressed {
    background-color: #1E40AF;
    color: #FFFFFF;
}

QPushButton.primary-btn:disabled {
    background-color: #E2E8F0;
    border-color: #CBD5E1;
    color: #94A3B8;
}

/* Botões de Cabeçalho */
QPushButton.header-btn {
    background-color: #F8FAFC;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 5px 11px;
    font-weight: 600;
    color: #334155;
}

QPushButton.header-btn:hover {
    background-color: #E0F2FE;
    border-color: #38BDF8;
    color: #0284C7;
}

QPushButton.header-btn:checked {
    background-color: #0284C7;
    border-color: #0284C7;
    color: #FFFFFF;
}

QPushButton.card-action-btn {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    color: #0F172A;
    padding: 6px 10px;
    font-weight: 600;
    font-size: 11px;
}

QPushButton.card-action-btn:hover {
    background-color: #E0F2FE;
    border-color: #38BDF8;
    color: #0284C7;
}

QPushButton.dpad-btn {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    color: #0F172A;
    font-size: 14px;
    font-weight: bold;
}

QPushButton.dpad-btn:hover {
    background-color: #2563EB;
    border-color: #2563EB;
    color: #FFFFFF;
}

QPushButton.dpad-stop-btn {
    background-color: #FEE2E2;
    border: 1px solid #EF4444;
    border-radius: 6px;
    color: #B91C1C;
    font-size: 10px;
    font-weight: bold;
}

QPushButton.dpad-stop-btn:hover {
    background-color: #EF4444;
    color: #FFFFFF;
}

QPushButton.link-btn {
    background-color: transparent;
    border: none;
    color: #0284C7;
    font-size: 11px;
    font-weight: 600;
}

QPushButton.link-btn:hover {
    color: #0369A1;
}

/* =========================================================================
   Sidebar e Navegação por Abas
   ========================================================================= */

#sidebarWidget {
    background-color: #FFFFFF;
    border-right: 1px solid #E2E8F0;
}

#sidebarHeaderTitle {
    color: #0F172A;
    font-weight: 700;
    font-size: 13px;
}

#sectionTitle, .section-title {
    color: #0F172A;
    font-size: 12px;
    font-weight: 700;
}

#mutedLabel {
    color: #64748B;
    font-size: 10px;
    font-weight: 600;
}

#cardAccentTitle {
    color: #0284C7;
    font-size: 13px;
    font-weight: 700;
}

#cardFrame, #dpadFrame, #timelineBar {
    background-color: #F8FAFC;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
}

#infoBanner {
    background-color: #EFF6FF;
    border: 1px solid #3B82F6;
    color: #1E40AF;
    padding: 8px;
    border-radius: 6px;
    font-size: 11px;
}

QTabWidget::pane {
    border: 1px solid #E2E8F0;
    background-color: #FFFFFF;
    border-radius: 6px;
}

QTabBar::tab {
    background-color: #F1F5F9;
    border: 1px solid #E2E8F0;
    color: #64748B;
    padding: 7px 12px;
    font-weight: 600;
    font-size: 11px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}

QTabBar::tab:hover {
    background-color: #E2E8F0;
    color: #0284C7;
}

QTabBar::tab:selected {
    background-color: #FFFFFF;
    border-bottom: 2px solid #0284C7;
    color: #0F172A;
    font-weight: 700;
}

/* =========================================================================
   Listas, Árvores e Tabelas
   ========================================================================= */

QTreeWidget, QListWidget, QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    color: #0F172A;
}

QTreeWidget::item, QListWidget::item, QTableWidget::item {
    padding: 6px 8px;
    min-height: 28px;
    color: #0F172A;
}

QTreeWidget::item:hover, QListWidget::item:hover, QTableWidget::item:hover {
    background-color: #F1F5F9;
    color: #0F172A;
}

QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {
    background-color: #2563EB;
    color: #FFFFFF;
    font-weight: 600;
}

QHeaderView::section {
    background-color: #F8FAFC;
    color: #475569;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #E2E8F0;
    border-bottom: 1px solid #E2E8F0;
    font-weight: 600;
}

/* =========================================================================
   Formulários, Menus e Diálogos
   ========================================================================= */

QLineEdit, QSpinBox, QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 5px 8px;
    color: #0F172A;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #0284C7;
    background-color: #FFFFFF;
    color: #0F172A;
}

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    selection-background-color: #2563EB;
    color: #0F172A;
}

QGroupBox {
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 700;
    color: #0284C7;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

QMenu {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 4px;
    color: #0F172A;
}

QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
    color: #0F172A;
    font-size: 12px;
    background-color: transparent;
}

QMenu::item:selected {
    background-color: #0284C7;
    color: #FFFFFF;
}

QMenu::separator {
    height: 1px;
    background-color: #E2E8F0;
    margin: 4px 6px;
}

QDialog, QMessageBox, QInputDialog {
    background-color: #FFFFFF;
    color: #0F172A;
}

QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel {
    color: #0F172A;
}

/* =========================================================================
   Status Bar & Scrollbars
   ========================================================================= */

QStatusBar {
    background-color: #FFFFFF;
    border-top: 1px solid #E2E8F0;
    color: #64748B;
    font-size: 11px;
    padding: 2px 8px;
}

QScrollBar:vertical {
    background: #F1F5F9;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #CBD5E1;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #94A3B8;
}
"""


def get_theme_qss(theme_name: str = "dark") -> str:
    """Retorna o stylesheet QSS correspondente ao nome do tema."""
    if (theme_name or "").lower() == "light":
        return LIGHT_THEME_QSS
    return DARK_THEME_QSS
