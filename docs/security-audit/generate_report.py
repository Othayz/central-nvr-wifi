#!/usr/bin/env python3
"""
Gerador do Relatório de Auditoria de Segurança em PDF para a Central NVR WiFi.
Gera relatório executivo e técnico em conformidade com os requisitos da auditoria:
- Paleta visual padronizada (Crítica: #B91C1C, Alta: #EA580C, Média: #D97706, Baixa: #2563EB, Pontos Fortes: #059669)
- Gráficos de rosca (Severidade) e barras (Categorias)
- Tabela detalhada de achados com chips coloridos
- Seção de GitHub Issues prontas para cópia
- Numeração dinâmica de páginas (Página X de Y) e margens A4 de ~2cm
"""
import io
import os
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class NumberedCanvas(canvas.Canvas):
    """Canvas de dois passos para numeração dinâmica de páginas 'Página X de Y'."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, page_count):
        self.saveState()

        # Omitir cabeçalho e rodapé na capa (página 1)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#64748B"))

            # Cabeçalho
            self.drawString(20 * mm, 283 * mm, "RELATÓRIO DE AUDITORIA DE SEGURANÇA — CENTRAL NVR WIFI")
            self.setFont("Helvetica", 8)
            self.drawRightString(190 * mm, 283 * mm, "SETEMBRO / 2026")

            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(20 * mm, 280 * mm, 190 * mm, 280 * mm)

            # Rodapé
            self.line(20 * mm, 16 * mm, 190 * mm, 16 * mm)
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawString(20 * mm, 11 * mm, "Central NVR WiFi — Auditoria de Código e Segurança Técnica")
            page_text = f"Página {self._pageNumber} de {page_count}"
            self.drawRightString(190 * mm, 11 * mm, page_text)

        self.restoreState()


def generate_charts(output_dir: Path):
    """Gera gráficos de alta resolução para o relatório executivo."""
    donut_path = output_dir / "chart_donut_severity.png"
    bar_path = output_dir / "chart_bar_categories.png"

    # 1. Gráfico de Rosca por Severidade
    labels = ["Crítica\n(0)", "Alta\n(3)", "Média\n(4)", "Baixa\n(1)"]
    counts = [0, 3, 4, 1]
    chart_colors = ["#B91C1C", "#EA580C", "#D97706", "#2563EB"]

    plot_labels = ["Alta (37.5%)", "Média (50%)", "Baixa (12.5%)"]
    plot_counts = [3, 4, 1]
    plot_colors = ["#EA580C", "#D97706", "#2563EB"]

    fig, ax = plt.subplots(figsize=(3.4, 2.5), dpi=300)
    wedges, texts, autotexts = ax.pie(
        plot_counts,
        labels=plot_labels,
        colors=plot_colors,
        autopct="%1.0f%%",
        pctdistance=0.75,
        startangle=140,
        wedgeprops=dict(width=0.45, edgecolor="white", linewidth=2),
        textprops=dict(fontsize=7.5, color="#1E293B", fontweight="bold"),
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontsize(8)
        at.set_weight("bold")

    # Texto no centro da rosca
    ax.text(
        0, 0,
        "8\nAchados",
        horizontalalignment="center",
        verticalalignment="center",
        fontsize=10,
        fontweight="bold",
        color="#0F172A",
    )

    ax.set_title("Achados por Severidade (Total: 8)", fontsize=9.5, fontweight="bold", pad=8, color="#0F172A")
    plt.tight_layout()
    plt.savefig(donut_path, transparent=False, facecolor="#F8FAFC", bbox_inches="tight")
    plt.close()

    # 2. Gráfico de Barras por Categoria
    categories = [
        "1. Banco / Isolamento",
        "2. Privilégios / Root",
        "3. IDOR / Arquivos",
        "4. Chaves / Plaintext",
        "5. Inputs / XSS & XML",
    ]
    cat_counts = [1, 1, 1, 2, 3]
    cat_colors = ["#EA580C", "#EA580C", "#EA580C", "#D97706", "#D97706"]

    fig, ax = plt.subplots(figsize=(4.2, 2.5), dpi=300)
    y_pos = np.arange(len(categories))
    bars = ax.barh(y_pos, cat_counts, color=cat_colors, height=0.55, edgecolor="none", zorder=3)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(categories, fontsize=7.5, fontweight="bold", color="#1E293B")
    ax.invert_yaxis()
    ax.set_xlabel("Número de Achados", fontsize=8, fontweight="bold", color="#334155")
    ax.set_xlim(0, 4)
    ax.set_xticks(range(0, 5))
    ax.grid(axis="x", linestyle="--", alpha=0.5, color="#CBD5E1", zorder=0)
    ax.set_axisbelow(True)
    ax.set_title("Achados por Categoria Auditada", fontsize=9.5, fontweight="bold", pad=8, color="#0F172A")

    for bar in bars:
        width = bar.get_width()
        ax.text(
            width + 0.1,
            bar.get_y() + bar.get_height() / 2,
            f"{int(width)}",
            ha="left",
            va="center",
            fontsize=8,
            fontweight="bold",
            color="#1E293B",
        )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#94A3B8")
    ax.spines["bottom"].set_color("#94A3B8")

    plt.tight_layout()
    plt.savefig(bar_path, transparent=False, facecolor="#F8FAFC", bbox_inches="tight")
    plt.close()

    return donut_path, bar_path


def build_pdf_report(pdf_target_path: Path, script_dir: Path):
    """Constrói o relatório completo em PDF."""
    pdf_target_path.parent.mkdir(parents=True, exist_ok=True)
    donut_img_path, bar_img_path = generate_charts(script_dir)

    # Configuração da página: A4 com margens de 20mm (~2cm)
    margin = 20 * mm
    doc = SimpleDocTemplate(
        str(pdf_target_path),
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )

    styles = getSampleStyleSheet()

    # Definição de Estilos Personalizados
    title_style = ParagraphStyle(
        "CoverTitle",
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#475569"),
        spaceAfter=14,
    )
    h1_style = ParagraphStyle(
        "ReportH1",
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=True,
    )
    h2_style = ParagraphStyle(
        "ReportH2",
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=7,
        spaceAfter=4,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
        spaceAfter=5,
    )
    body_compact = ParagraphStyle(
        "ReportBodyCompact",
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#334155"),
    )
    code_style = ParagraphStyle(
        "CodeBlock",
        fontName="Courier",
        fontSize=6.8,
        leading=8.5,
        textColor=colors.HexColor("#0F172A"),
        backColor=colors.HexColor("#F1F5F9"),
        borderColor=colors.HexColor("#CBD5E1"),
        borderWidth=0.5,
        borderPadding=4,
        spaceAfter=5,
    )

    # Chips de Severidade Coloridos
    badge_critica = "<font color='#FFFFFF' backcolor='#B91C1C'><b>&nbsp;CRÍTICA&nbsp;</b></font>"
    badge_alta = "<font color='#FFFFFF' backcolor='#EA580C'><b>&nbsp;ALTA&nbsp;</b></font>"
    badge_media = "<font color='#FFFFFF' backcolor='#D97706'><b>&nbsp;MÉDIA&nbsp;</b></font>"
    badge_baixa = "<font color='#FFFFFF' backcolor='#2563EB'><b>&nbsp;BAIXA&nbsp;</b></font>"
    badge_forte = "<font color='#FFFFFF' backcolor='#059669'><b>&nbsp;PROTEGIDO&nbsp;</b></font>"

    story = []

    # =========================================================================
    # PÁGINA 1: CAPA + ESCOPO + NOTA METODOLÓGICA
    # =========================================================================
    story.append(Paragraph("<font color='#2563EB'><b>[ AUDITORIA TÉCNICA DE CÓDIGO E SEGURANÇA ]</b></font>", ParagraphStyle("CoverTag", fontName="Helvetica-Bold", fontSize=9, leading=12, spaceAfter=4)))
    story.append(Paragraph("Relatório de Auditoria de Segurança — Central NVR WiFi", title_style))
    story.append(Paragraph("Auditoria Estática de Vulnerabilidades, Controle de Acesso, Isolamento e Riscos de Rede", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2.5, color=colors.HexColor("#2563EB"), spaceAfter=8))

    meta_data = [
        [Paragraph("<b>Projeto:</b>", body_compact), Paragraph("Central NVR WiFi (Desktop Linux)", body_compact), Paragraph("<b>Data da Auditoria:</b>", body_compact), Paragraph("05 de Setembro de 2026", body_compact)],
        [Paragraph("<b>Repositório:</b>", body_compact), Paragraph("Othayz/central-nvr-wifi (branch main)", body_compact), Paragraph("<b>Versão Base:</b>", body_compact), Paragraph("v1.0.0 / v1.1.0", body_compact)],
        [Paragraph("<b>Linguagem / UI:</b>", body_compact), Paragraph("Python 3.10+ / PySide6 (Qt 6)", body_compact), Paragraph("<b>Auditor:</b>", body_compact), Paragraph("Engenharia de Segurança de Software", body_compact)],
    ]
    t_meta = Table(meta_data, colWidths=[24 * mm, 62 * mm, 32 * mm, 52 * mm])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 4 * mm))

    # Escopo Auditado
    story.append(Paragraph("1. Escopo Auditado", h1_style))
    scope_text = (
        "A auditoria cobriu 100% dos arquivos do projeto (8.362 linhas de código em 27 módulos), abrangendo os componentes "
        "do núcleo (<code>src/central_nvr/core/</code>), interface gráfica (<code>src/central_nvr/ui/</code>), scanner de rede "
        "(<code>src/central_nvr/scanner/</code>), rotinas de empacotamento Linux Debian e RPM (<code>packaging/</code>), "
        "scripts de instalação e diagnóstico (<code>install_ubuntu.sh</code>, <code>scripts/</code>) e automações de CI/CD (<code>.github/workflows/</code>)."
    )
    story.append(Paragraph(scope_text, body_style))
    story.append(Spacer(1, 2 * mm))

    # Nota Metodológica e Mapeamento para a Stack
    story.append(Paragraph("2. Nota Metodológica e Mapeamento para a Stack Detectada", h1_style))
    methodology_p1 = (
        "Como a Central NVR WiFi é uma aplicação desktop nativa monousuário para Linux em Python e PySide6 com comunicação em rede "
        "via protocolos de CFTV (ONVIF WS-Discovery, SOAP e streaming RTSP), não há servidor HTTP intermediário nem banco relacional tradicional. "
        "Cada uma das cinco categorias clássicas de auditoria foi rigorosamente adaptada para o equivalente tecnológico da stack:"
    )
    story.append(Paragraph(methodology_p1, body_style))
    story.append(Spacer(1, 2 * mm))

    stack_map_data = [
        [Paragraph("<b>Categoria Padrão</b>", body_style), Paragraph("<b>Equivalente Mapeado na Stack Central NVR WiFi</b>", body_style)],
        [Paragraph("<b>1. Banco sem Tranca</b>", body_style), Paragraph("Camada de persistência local XDG (<code>settings.json</code>, <code>devices.json</code>), permissões POSIX de arquivos/diretórios e isolamento multiusuário Linux.", body_style)],
        [Paragraph("<b>2. Permissão no Navegador</b>", body_style), Paragraph("Transição e elevação de privilégios no sistema operacional: chamadas a <code>pkexec</code> (apt/dnf), scripts <code>install_ubuntu.sh</code> e permissões em <code>/opt/central-nvr</code>.", body_style)],
        [Paragraph("<b>3. IDOR / Referência Direta</b>", body_style), Paragraph("Manipulação de caminhos em nomes de arquivos, snapshots, gravações e descompactação de pacotes (Path Traversal / Tar Slip na extração local).", body_style)],
        [Paragraph("<b>4. Chaves Expostas</b>", body_style), Paragraph("Tokens (GitHub PAT), credenciais de câmeras IP salvas no disco, credenciais padrão em scripts e segredos no histórico git ou CI/CD.", body_style)],
        [Paragraph("<b>5. Inputs sem Tratamento</b>", body_style), Paragraph("Injeção de markup RichText/HTML em <code>QLabel</code> do PySide6 a partir de dados de rede, injeção em SOAP e parsing de XML não confiável (DoS via Entity Expansion).", body_style)],
    ]
    t_stack = Table(stack_map_data, colWidths=[48 * mm, 122 * mm])
    t_stack.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_stack)

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 2: RESUMO EXECUTIVO + GRÁFICOS + PONTOS FORTES E FRACOS
    # =========================================================================
    story.append(Paragraph("3. Resumo Executivo", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=5))

    exec_summary = (
        "A auditoria identificou um total de <b>8 achados de segurança verificados no código real</b> "
        "(0 Críticos, 3 Altos, 4 Médios e 1 Baixo). O aplicativo demonstra excelente higiene defensiva em áreas críticas de rede, "
        "como proteção Anti-SSRF contra câmeras maliciosas, escape XML estrito em envelopes SOAP e sanitização regex contra Path Traversal em snapshots. "
        "No entanto, vulnerabilidades significativas foram identificadas no <b>subsistema de atualização automática de pacotes</b> "
        "(risco de Tar Slip e execução de instalador como root via <code>pkexec</code> sobre diretório <code>/tmp</code> compartilhado sem verificação de hash) "
        "e no <b>armazenamento de credenciais de câmeras em texto claro</b> decorrente da ausência do pacote <code>keyring</code> nas dependências."
    )
    story.append(Paragraph(exec_summary, body_style))
    story.append(Spacer(1, 2 * mm))

    # Tabela de Métricas Rápidas
    metrics_table_data = [
        [
            Paragraph("<font color='#B91C1C'><b>CRÍTICA</b></font><br/><b>0</b> achados", ParagraphStyle("M1", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#EA580C'><b>ALTA</b></font><br/><b>3</b> achados", ParagraphStyle("M2", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#D97706'><b>MÉDIA</b></font><br/><b>4</b> achados", ParagraphStyle("M3", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#2563EB'><b>BAIXA</b></font><br/><b>1</b> achado", ParagraphStyle("M4", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#059669'><b>PONTOS FORTES</b></font><br/><b>6</b> verificados", ParagraphStyle("M5", alignment=1, fontSize=8.5, leading=11)),
        ]
    ]
    t_metrics = Table(metrics_table_data, colWidths=[34 * mm, 34 * mm, 34 * mm, 34 * mm, 34 * mm])
    t_metrics.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#FEF2F2")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FFF7ED")),
        ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#FFFBEB")),
        ("BACKGROUND", (3, 0), (3, 0), colors.HexColor("#EFF6FF")),
        ("BACKGROUND", (4, 0), (4, 0), colors.HexColor("#ECFDF5")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t_metrics)
    story.append(Spacer(1, 3 * mm))

    # Gráficos Lado a Lado
    charts_table_data = [
        [Image(str(donut_img_path), width=78 * mm, height=58 * mm), Image(str(bar_img_path), width=90 * mm, height=58 * mm)]
    ]
    t_charts = Table(charts_table_data, colWidths=[82 * mm, 88 * mm])
    t_charts.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t_charts)
    story.append(Spacer(1, 3 * mm))

    # Seção 4: Pontos Fortes e Riscos Centrais
    story.append(Paragraph("4. Pontos Fortes de Segurança (Verificados no Código)", h2_style))
    strengths_text = (
        "• <b>Permissões Estritas POSIX (0700/0600) e Gravação Atômica:</b> Em <code>src/central_nvr/core/config.py:70-98, 112-127</code>, "
        "as pastas de dados/configurações usam <code>0700</code> e arquivos JSON usam <code>0600</code> com <code>os.replace</code> atômico.<br/>"
        "• <b>Proteção Anti-SSRF em SOAP e Discovery:</b> Em <code>src/central_nvr/core/onvif_client.py:64-84</code> e <code>src/central_nvr/scanner/parser.py:62-73</code>, "
        "endpoints XAddr retornados são restritos ao IP da câmera, barrando pivoteamento contra hosts internos (169.254.169.254 / localhost).<br/>"
        "• <b>Escape XML Estrito em Parâmetros SOAP:</b> Em <code>src/central_nvr/core/onvif_client.py:353, 533, 556, 575, 610, 623</code>, "
        "todas as strings de controle de mídia e PTZ utilizam <code>html.escape()</code>, prevenindo injeções XML contra dispositivos.<br/>"
        "• <b>Sanitização Regex contra Path Traversal em Snapshots:</b> Em <code>src/central_nvr/ui/camera_view.py:433</code> e <code>fullscreen_view.py:261</code>, "
        "o identificador da câmera é higienizado com <code>re.sub(r'[^a-zA-Z0-9_-]', '_', camera.id)</code>.<br/>"
        "• <b>Ofuscação de Credenciais em Logs RTSP:</b> Em <code>src/central_nvr/core/stream_worker.py:65-68</code>, senhas em URLs RTSP são mascaradas com <code>:****@</code>.<br/>"
        "• <b>Instalador com Permissões Root Corretas:</b> Em <code>install_ubuntu.sh:73-74</code>, <code>/opt/central-nvr</code> pertence a <code>root:root</code> (modo 755), prevenindo LPE local."
    )
    story.append(Paragraph(strengths_text, body_style))
    story.append(Spacer(1, 2 * mm))

    story.append(Paragraph("5. Principais Riscos e Pontos Fracos Centrais", h2_style))
    weaknesses_text = (
        "• <b>Execução de Atualizações não Validadas como Root (LPE):</b> Chamada a <code>pkexec apt/dnf</code> sobre pacotes baixados em <code>/tmp</code> compartilhado sem verificação de hash SHA-256.<br/>"
        "• <b>Arbitrary File Overwrite / Tar Slip:</b> Extração de arquivos compactados em <code>updater.py</code> sem validação canônica de caminho, permitindo sobrescrita de arquivos da home.<br/>"
        "• <b>Armazenamento de Senhas em Texto Puro:</b> Ausência da dependência <code>keyring</code> forçando salvamento de senhas em claro em <code>devices.json</code>.<br/>"
        "• <b>Injeção de RichText/Markup:</b> Uso de <code>QLabel(f'&lt;b&gt;{name}&lt;/b&gt;')</code> com nomes não sanitizados recebidos via rede UDP multicast."
    )
    story.append(Paragraph(weaknesses_text, body_style))

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 3: TABELA DETALHADA DE ACHADOS + PLANO DE AÇÃO (P1, P2, P3)
    # =========================================================================
    story.append(Paragraph("6. Tabela Detalhada de Achados de Segurança", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=5))

    findings_table_data = [
        [
            Paragraph("<b>Sev.</b>", ParagraphStyle("TH1", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
            Paragraph("<b>ID / Categoria</b>", ParagraphStyle("TH2", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
            Paragraph("<b>Arquivo : Linhas</b>", ParagraphStyle("TH3", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
            Paragraph("<b>Descrição do Problema e Causa Raiz</b>", ParagraphStyle("TH4", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white)),
        ],
        # Finding 1
        [
            Paragraph(f"{badge_alta}", body_compact),
            Paragraph("<b>SEC-01</b><br/>1. Banco / Isolamento", body_compact),
            Paragraph("<code>src/central_nvr/core/config.py</code><br/>L193-207", body_compact),
            Paragraph("<b>Armazenamento de Senhas em Texto Puro em devices.json:</b> Mesmo com tentativa de sincronismo com o keyring, o campo <code>password</code> permanece no dicionário salvo em disco.", body_compact),
        ],
        # Finding 2
        [
            Paragraph(f"{badge_alta}", body_compact),
            Paragraph("<b>SEC-02</b><br/>2. Privilégios / Root", body_compact),
            Paragraph("<code>src/central_nvr/ui/update_dialog.py</code><br/>L268-271<br/><code>src/central_nvr/core/updater.py</code><br/>L521-532", body_compact),
            Paragraph("<b>Execução Privilegiada em Diretório Compartilhado Inseguro:</b> Atualizador baixa pacotes para <code>/tmp/central_nvr_updates</code> e executa <code>pkexec apt/dnf</code> sem validação de integridade (risco de LPE/TOCTOU).", body_compact),
        ],
        # Finding 3
        [
            Paragraph(f"{badge_alta}", body_compact),
            Paragraph("<b>SEC-03</b><br/>3. IDOR / Arquivos", body_compact),
            Paragraph("<code>src/central_nvr/core/updater.py</code><br/>L394-416", body_compact),
            Paragraph("<b>Tar Slip / Path Traversal na Extração de Atualização:</b> <code>_extract_tar_members_to_local</code> extrai membros com caminhos relativos (ex: <code>usr/../../.bashrc</code>) sem validar se destino está contido em <code>target_base</code>.", body_compact),
        ],
        # Finding 4
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-04</b><br/>4. Chaves / Plaintext", body_compact),
            Paragraph("<code>src/central_nvr/core/config.py</code><br/>L160<br/><code>src/central_nvr/ui/settings_dialog.py</code><br/>L126, L203", body_compact),
            Paragraph("<b>Exposição de GitHub Personal Access Token (PAT):</b> O token pessoal de acesso ao GitHub é persistido em texto puro em <code>settings.json</code> sem utilizar cofre de senhas do sistema operacional.", body_compact),
        ],
        # Finding 5
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-05</b><br/>4. Chaves / Dependência", body_compact),
            Paragraph("<code>requirements.txt</code><br/>L1-15<br/><code>pyproject.toml</code><br/>L34-42<br/><code>config.py</code> L13-17", body_compact),
            Paragraph("<b>Dependência Keyring Ausente com Fallback Silencioso:</b> A biblioteca <code>keyring</code> não está declarada nas dependências do projeto, fazendo a proteção criptográfica falhar silenciosamente para texto puro em novas instalações.", body_compact),
        ],
        # Finding 6
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-06</b><br/>5. Inputs / DoS XML", body_compact),
            Paragraph("<code>src/central_nvr/scanner/parser.py</code><br/>L79", body_compact),
            Paragraph("<b>Parsing de XML de Rede via ElementTree sem Limites:</b> <code>ET.fromstring()</code> processa datagramas UDP multicast não autenticados na porta 3702 sem proteção contra XML Entity Expansion (Billion Laughs / DoS).", body_compact),
        ],
        # Finding 7
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-07</b><br/>5. Integridade / Binários", body_compact),
            Paragraph("<code>src/central_nvr/core/updater.py</code><br/>L339-382", body_compact),
            Paragraph("<b>Ausência de Verificação Criptográfica de Integridade (SHA-256):</b> O download de releases do GitHub aceita qualquer payload retornado sem calcular nem conferir checksum SHA-256.", body_compact),
        ],
        # Finding 8
        [
            Paragraph(f"{badge_baixa}", body_compact),
            Paragraph("<b>SEC-08</b><br/>5. Inputs / XSS RichText", body_compact),
            Paragraph("<code>src/central_nvr/ui/camera_view.py</code><br/>L100, L298<br/><code>src/central_nvr/ui/timeline_bar.py</code><br/>L65", body_compact),
            Paragraph("<b>Injeção de RichText/HTML em QLabels:</b> Concatenação de nomes de câmeras recebidos da rede em strings com tags HTML (<code>&lt;b&gt;{name}&lt;/b&gt;</code>) sem <code>html.escape()</code>, permitindo UI Redressing e spoofing de status.", body_compact),
        ],
    ]

    t_findings = Table(findings_table_data, colWidths=[18 * mm, 34 * mm, 46 * mm, 76 * mm])
    t_findings.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(t_findings)
    story.append(Spacer(1, 4 * mm))

    # Seção 7: Recomendações Priorizadas
    story.append(Paragraph("7. Recomendações Priorizadas de Remediação", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=5))

    recs_data = [
        [
            Paragraph("<b>P1<br/>Imediato</b>", ParagraphStyle("RP1", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#B91C1C"), alignment=1)),
            Paragraph(
                "<b>1. Sanitizar extração de pacotes contra Tar Slip (SEC-03):</b> Validar se <code>dest_path.resolve()</code> inicia com <code>target_base.resolve()</code> antes de abrir arquivos.<br/>"
                "<b>2. Eliminar diretório /tmp compartilhado e validar integridade (SEC-02 / SEC-07):</b> Usar <code>tempfile.mkdtemp(prefix='nvr_upd_', dir=Path.home() / '.cache')</code> e verificar SHA-256 do pacote baixado.<br/>"
                "<b>3. Mascarar senhas em devices.json e adicionar keyring às dependências (SEC-01 / SEC-05):</b> Incluir <code>keyring&gt;=24.0.0</code> em <code>requirements.txt</code> e remover <code>d['password']</code> antes de salvar o JSON.",
                body_compact
            )
        ],
        [
            Paragraph("<b>P2<br/>Médio Prazo</b>", ParagraphStyle("RP2", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#EA580C"), alignment=1)),
            Paragraph(
                "<b>4. Armazenar GitHub PAT no Keyring (SEC-04):</b> Salvar o <code>github_token</code> no keyring do sistema (ex: <code>keyring.set_password('central-nvr', 'github_token', token)</code>) em vez de persistir em <code>settings.json</code>.<br/>"
                "<b>5. Proteger parser XML contra ataques de negação de serviço (SEC-06):</b> Substituir <code>xml.etree.ElementTree</code> por <code>defusedxml.ElementTree</code> em <code>parser.py</code>.",
                body_compact
            )
        ],
        [
            Paragraph("<b>P3<br/>Melhoria</b>", ParagraphStyle("RP3", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#2563EB"), alignment=1)),
            Paragraph(
                "<b>6. Escapar inputs em widgets RichText do Qt (SEC-08):</b> Aplicar <code>html.escape()</code> em <code>camera_view.py</code> e <code>timeline_bar.py</code>, ou definir explicitamente <code>setTextFormat(Qt.TextFormat.PlainText)</code>.",
                body_compact
            )
        ],
    ]
    t_recs = Table(recs_data, colWidths=[20 * mm, 154 * mm])
    t_recs.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#FEF2F2")),
        ("BACKGROUND", (0, 1), (0, 1), colors.HexColor("#FFF7ED")),
        ("BACKGROUND", (0, 2), (0, 2), colors.HexColor("#EFF6FF")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_recs)

    story.append(PageBreak())

    # =========================================================================
    # PÁGINAS SEGUINTES: SEÇÃO ISSUES PARA O GITHUB (FORMATO PRONTO PARA COPIAR)
    # =========================================================================
    story.append(Paragraph("8. Issues Prontas para o GitHub", h1_style))
    story.append(Paragraph(
        "Abaixo estão os modelos completos de Issues em formato Markdown, delimitados por blocos identificadores, "
        "prontos para serem copiados e colados diretamente no repositório GitHub do projeto:",
        body_style
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))

    issues_list = [
        # ISSUE 1
        {
            "num": 1,
            "title": "[Segurança] [Alta] Vulnerabilidade de Tar Slip / Path Traversal na extração de atualizações locais",
            "labels": "security, bug, priority-high",
            "desc": (
                "A rotina `_extract_tar_members_to_local()` em `src/central_nvr/core/updater.py` processa membros de arquivos "
                "`.tar.gz` ou arquivos de dados `data.tar` contidos em pacotes `.deb` baixados do GitHub Releases e os grava diretamente "
                "em `~/.local/`. Se um pacote de atualização malicioso ou adulterado contiver caminhos relativos com sequências `../` "
                "(ex: `usr/../../.bashrc` ou `../../.config/autostart/update.desktop`), o método `target_base.joinpath(*rel_parts)` resolve "
                "o arquivo para fora do diretório de destino pretendido, permitindo a sobrescrita arbitrária de arquivos na pasta home do usuário."
            ),
            "evidence": (
                "**Arquivo:** `src/central_nvr/core/updater.py` (Linhas 394-416)\n```python\n"
                "def _extract_tar_members_to_local(tar, target_base):\n"
                "    for member in tar.getmembers():\n"
                "        rel_parts = Path(member.name).parts\n"
                "        if len(rel_parts) > 1 and rel_parts[0] in ('.', '/'):\n"
                "            rel_parts = rel_parts[1:]\n"
                "        if len(rel_parts) > 0 and rel_parts[0] == 'usr':\n"
                "            rel_parts = rel_parts[1:]\n"
                "        dest_path = target_base.joinpath(*rel_parts)\n"
                "        # Falha: dest_path não é validado contra target_base!\n"
                "        with open(dest_path, 'wb') as out_f:\n"
                "            shutil.copyfileobj(tar.extractfile(member), out_f)\n```"
            ),
            "impact": "Execução remota de código (RCE) no contexto do usuário e destruição/sobrescrita de configurações críticas (`~/.bashrc`, `~/.profile`, chaves SSH).",
            "remediation": (
                "Validar o caminho canônico resolvido antes de extrair qualquer membro:\n```python\n"
                "resolved_target = target_base.resolve()\n"
                "dest_path = target_base.joinpath(*rel_parts).resolve()\n"
                "if not str(dest_path).startswith(str(resolved_target)):\n"
                "    logger.warning(f'Tentativa de Path Traversal bloqueada: {member.name}')\n"
                "    continue\n```"
            ),
            "checklist": [
                "[ ] Validar caminhos extraídos garantindo que estejam estritamente contidos em `target_base`.",
                "[ ] Rejeitar links simbólicos que apontem para fora de `target_base`.",
                "[ ] Criar teste de unidade reproduzindo pacote com caminho malicioso `usr/../../.test_slip`.",
            ],
        },
        # ISSUE 2
        {
            "num": 2,
            "title": "[Segurança] [Alta] Execução de instalador via pkexec sobre pacotes em diretório /tmp compartilhado sem validação de integridade",
            "labels": "security, bug, priority-high",
            "desc": (
                "Ao baixar pacotes de atualização do GitHub, `update_dialog.py` grava o arquivo `.deb` ou `.rpm` em um diretório "
                "previsível dentro de `/tmp` (`/tmp/central_nvr_updates/<arquivo>`), criado com permissões herdadas do umask. "
                "Em seguida, `updater.py` executa `pkexec apt install -y` ou `pkexec dnf install -y` sobre esse caminho como `root`, "
                "sem verificar hash SHA-256 ou assinatura criptográfica. Qualquer usuário local malicioso pode predeterminar ou "
                "substituir o arquivo em `/tmp` antes da execução administrativa (TOCTOU), obtendo escalonamento de privilégios para root."
            ),
            "evidence": (
                "**Arquivo:** `src/central_nvr/ui/update_dialog.py` (Linhas 268-271) e `src/central_nvr/core/updater.py` (Linhas 521-532)\n```python\n"
                "dest_dir = os.path.join(tempfile.gettempdir(), 'central_nvr_updates')\n"
                "dest_file = os.path.join(dest_dir, self.best_asset.name)\n"
                "...\n"
                "subprocess.Popen(['pkexec', 'apt', 'install', '-y', os.path.abspath(file_path)])\n```"
            ),
            "impact": "Escalonamento Local de Privilégios (Local Privilege Escalation - LPE) para `root` em sistemas Linux multiusuário.",
            "remediation": (
                "1. Armazenar arquivos de atualização em pasta privada do usuário (`~/.cache/central-nvr/updates/`) com permissão `0700`.\n"
                "2. Validar o hash criptográfico SHA-256 do arquivo baixado antes de acionar `pkexec`."
            ),
            "checklist": [
                "[ ] Alterar diretório de download de `/tmp` para `Path.home() / '.cache' / 'central-nvr' / 'updates'`.",
                "[ ] Aplicar permissões estritas `0700` no diretório e `0600` no arquivo baixado.",
                "[ ] Implementar verificação de hash SHA-256 obrigatória antes de invocar `pkexec`.",
            ],
        },
        # ISSUE 3
        {
            "num": 3,
            "title": "[Segurança] [Alta] Armazenamento de credenciais de câmeras em texto puro em devices.json e dependência keyring ausente",
            "labels": "security, bug, priority-high",
            "desc": (
                "O método `save_devices()` em `src/central_nvr/core/config.py` serializa a lista `self.devices` diretamente para o "
                "arquivo `~/.config/central-nvr/devices.json`. Embora tente salvar no keyring, o campo `password` não é purgado do dicionário "
                "antes da escrita. Além disso, o pacote `keyring` não está em `requirements.txt` nem em `pyproject.toml`, fazendo "
                "`HAS_KEYRING` ser sempre `False` em instalações padrão e persistindo todas as senhas de câmeras e NVRs em texto puro no disco."
            ),
            "evidence": (
                "**Arquivo:** `src/central_nvr/core/config.py` (Linhas 193-207)\n```python\n"
                "def save_devices(self):\n"
                "    for d in self.devices:\n"
                "        dev_id = d.get('id')\n"
                "        if dev_id and 'password' in d:\n"
                "            set_keyring_password(dev_id, d.get('password', ''))\n"
                "    # Falha: d['password'] permanece dentro de self.devices!\n"
                "    _secure_write_json(self.devices_path, self.devices)\n```"
            ),
            "impact": "Exposição de senhas de CFTV em caso de vazamento da pasta home, backups não criptografados ou acesso indevido.",
            "remediation": (
                "1. Adicionar `keyring>=24.0.0` ao `requirements.txt` e `pyproject.toml`.\n"
                "2. Ao salvar `devices.json`, criar cópia dos dicionários omitindo ou ofuscando o campo `password` quando o keyring estiver ativo."
            ),
            "checklist": [
                "[ ] Incluir `keyring>=24.0.0` em `requirements.txt` e `pyproject.toml`.",
                "[ ] Sanitizar `devices.json` para que senhas fiquem exclusivamente no cofre do sistema operacional.",
                "[ ] Implementar aviso na interface caso o serviço de keyring do sistema não esteja disponível.",
            ],
        },
        # ISSUE 4
        {
            "num": 4,
            "title": "[Segurança] [Média] Armazenamento de Personal Access Token (PAT) do GitHub em texto claro em settings.json",
            "labels": "security, priority-medium",
            "desc": (
                "O token de acesso pessoal do GitHub inserido nas Configurações da aplicação é salvo em texto puro dentro do "
                "arquivo de preferências `~/.config/central-nvr/settings.json` na chave `github_token`. Diferente das credenciais de câmeras, "
                "não há qualquer tentativa de integração do PAT com o cofre de credenciais do sistema."
            ),
            "evidence": (
                "**Arquivo:** `src/central_nvr/core/config.py` (L160) e `src/central_nvr/ui/settings_dialog.py` (L203)\n```python\n"
                "self.config_mgr.set('github_token', self.txt_github_token.text().strip())\n```"
            ),
            "impact": "Comprometimento de credenciais da conta do GitHub do usuário caso o arquivo de configuração seja acessado ou compartilhado em relatórios de suporte.",
            "remediation": (
                "Utilizar o serviço de keyring seguro para o PAT:\n```python\n"
                "keyring.set_password('central-nvr', 'github_token', token)\n```"
            ),
            "checklist": [
                "[ ] Migrar persistência do `github_token` para o `keyring`.",
                "[ ] Omitir o token em texto puro de `settings.json`.",
                "[ ] Adicionar teste de migração de tokens legados.",
            ],
        },
        # ISSUE 5
        {
            "num": 5,
            "title": "[Segurança] [Média] Risco de Negação de Serviço (DoS) via XML Entity Expansion no parser WS-Discovery UDP",
            "labels": "security, priority-medium",
            "desc": (
                "A função `parse_ws_discovery_response()` em `src/central_nvr/scanner/parser.py` invoca `xml.etree.ElementTree.fromstring()` "
                "sobre dados brutos recebidos via socket UDP multicast (porta 3702). O parser padrão do Python não limita expansão de entidades "
                "XML e é suscetível a ataques de negação de serviço (Billion Laughs / quadratic blowup) disparados por qualquer dispositivo na rede local."
            ),
            "evidence": (
                "**Arquivo:** `src/central_nvr/scanner/parser.py` (Linha 79)\n```python\n"
                "root = ET.fromstring(xml_data)\n```"
            ),
            "impact": "Congelamento da interface e esgotamento de memória/CPU por envio de datagramas UDP multicast na rede local.",
            "remediation": (
                "Adicionar `defusedxml` às dependências e utilizá-lo para processar payloads XML externos:\n```python\n"
                "import defusedxml.ElementTree as SafeET\n"
                "root = SafeET.fromstring(xml_data)\n```"
            ),
            "checklist": [
                "[ ] Adicionar `defusedxml>=0.7.1` ao `requirements.txt`.",
                "[ ] Substituir chamadas de `xml.etree.ElementTree` por `defusedxml` no módulo scanner.",
                "[ ] Adicionar teste com XML contendo entidades expansivas garantindo rejeição segura.",
            ],
        },
        # ISSUE 6
        {
            "num": 6,
            "title": "[Segurança] [Baixa] Injeção de marcação HTML/RichText em QLabels através de nomes de câmeras não sanitizados",
            "labels": "security, priority-low",
            "desc": (
                "Widgets `QLabel` no PySide6 interpretam texto como RichText (subconjunto HTML) automaticamente quando encontram "
                "tags formatadas. Os métodos em `camera_view.py` e `timeline_bar.py` interpolam nomes de câmeras obtidos via rede "
                "diretamente em `<b>{name}</b>`. Um dispositivo com nome manipulado na rede local pode injetar tags de formatação, "
                "hiperlinks e quebras de layout na interface."
            ),
            "evidence": (
                "**Arquivo:** `src/central_nvr/ui/camera_view.py` (L100, L298) e `src/central_nvr/ui/timeline_bar.py` (L65)\n```python\n"
                "self.lbl_title.setText(f'<b>{new_name}</b>')\n"
                "self.lbl_cam.setText(f'<b>Timeline:</b> {name}')\n```"
            ),
            "impact": "Adulteração visual da interface (UI Spoofing / Redressing) e indução de cliques em links maliciosos externos.",
            "remediation": (
                "Utilizar `html.escape()` ao interpolar em HTML ou forçar modo de texto puro:\n```python\n"
                "import html\n"
                "self.lbl_title.setText(f'<b>{html.escape(new_name)}</b>')\n"
                "self.lbl_cam.setText(f'<b>Timeline:</b> {html.escape(name)}')\n```"
            ),
            "checklist": [
                "[ ] Aplicar `html.escape()` em todos os QLabels com interpolação de variáveis dinâmicas.",
                "[ ] Configurar `setTextFormat(Qt.TextFormat.PlainText)` em campos onde marcação não é requerida.",
            ],
        },
    ]

    for issue in issues_list:
        issue_block = []
        issue_block.append(Paragraph(f"<b>--- ISSUE {issue['num']} ---</b>", ParagraphStyle("IssueDelim", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#2563EB"))))
        issue_block.append(Paragraph(f"<b>Título:</b> <code>{issue['title']}</code>", h2_style))
        issue_block.append(Paragraph(f"<b>Labels sugeridas:</b> <code>{issue['labels']}</code>", body_style))
        issue_block.append(Spacer(1, 1 * mm))

        issue_block.append(Paragraph("<b>Descrição do Problema:</b>", h2_style))
        issue_block.append(Paragraph(issue["desc"], body_style))
        issue_block.append(Spacer(1, 1 * mm))

        issue_block.append(Paragraph("<b>Evidência no Código:</b>", h2_style))
        issue_block.append(Paragraph(issue["evidence"].replace("\n", "<br/>"), code_style))
        issue_block.append(Spacer(1, 1 * mm))

        issue_block.append(Paragraph("<b>Impacto:</b>", h2_style))
        issue_block.append(Paragraph(issue["impact"], body_style))
        issue_block.append(Spacer(1, 1 * mm))

        issue_block.append(Paragraph("<b>Sugestão de Correção:</b>", h2_style))
        issue_block.append(Paragraph(issue["remediation"].replace("\n", "<br/>"), code_style))
        issue_block.append(Spacer(1, 1 * mm))

        issue_block.append(Paragraph("<b>Critérios de Aceite (Checklist):</b>", h2_style))
        for item in issue["checklist"]:
            issue_block.append(Paragraph(f"&nbsp;&nbsp;{item}", body_style))

        issue_block.append(Spacer(1, 1 * mm))
        issue_block.append(Paragraph(f"<b>--- FIM ISSUE {issue['num']} ---</b>", ParagraphStyle("IssueEnd", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#64748B"))))
        issue_block.append(Spacer(1, 4 * mm))
        issue_block.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=5))

        story.append(KeepTogether(issue_block))

    # Construir o documento PDF com canvas numerado
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"[OK] Relatório de Auditoria de Segurança gerado com sucesso em:")
    print(f"     {pdf_target_path}")


if __name__ == "__main__":
    target_pdf = Path("/home/Othay/Documentos/security-audit/relatorio-auditoria-seguranca.pdf")
    script_directory = Path(__file__).resolve().parent
    build_pdf_report(target_pdf, script_directory)
