#!/usr/bin/env python3
"""
Gerador do Relatório de Auditoria de Segurança em PDF para a Central NVR WiFi.
Utiliza ReportLab e Matplotlib em ambiente virtual isolado.
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
            self.drawRightString(190 * mm, 283 * mm, "AGOSTO / 2026")
            
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(20 * mm, 280 * mm, 190 * mm, 280 * mm)

            # Rodapé
            self.line(20 * mm, 16 * mm, 190 * mm, 16 * mm)
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawString(20 * mm, 11 * mm, "Central NVR WiFi — Documento de Segurança e Conformidade Técnica")
            page_text = f"Página {self._pageNumber} de {page_count}"
            self.drawRightString(190 * mm, 11 * mm, page_text)

        self.restoreState()


def generate_charts(output_dir: Path) -> tuple[Path, Path]:
    """Gera gráficos de rosca e barras com a paleta oficial da auditoria."""
    output_dir.mkdir(parents=True, exist_ok=True)
    donut_path = output_dir / "chart_donut_severity.png"
    bar_path = output_dir / "chart_bar_categories.png"

    # Paleta oficial:
    # Crítica #B91C1C, Alta #EA580C, Média #D97706, Baixa #2563EB, Ponto Forte #059669
    
    # 1. Gráfico de Rosca por Severidade
    labels = ["Alta (3)", "Média (3)", "Baixa (2)"]
    sizes = [3, 3, 2]
    colors_list = ["#EA580C", "#D97706", "#2563EB"]

    fig, ax = plt.subplots(figsize=(4.2, 2.7), dpi=300)
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=labels,
        colors=colors_list,
        autopct="%1.0f%%",
        startangle=140,
        pctdistance=0.72,
        wedgeprops=dict(width=0.45, edgecolor="#FFFFFF", linewidth=2),
        textprops=dict(color="#1E293B", fontsize=8.5, fontweight="bold")
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontsize(8.5)
        at.set_weight("bold")

    ax.set_title("Achados por Severidade (Total: 8)", fontsize=10, fontweight="bold", pad=10, color="#0F172A")
    plt.tight_layout()
    plt.savefig(donut_path, transparent=False, facecolor="#F8FAFC", bbox_inches="tight")
    plt.close()

    # 2. Gráfico de Barras por Categoria
    categories = [
        "1. Banco / Permissões",
        "2. Privilégios / Root",
        "3. IDOR / Arquivos",
        "4. Senhas / Plaintext",
        "5. Injeção / SOAP / SSRF",
    ]
    counts = [1, 2, 2, 2, 3]
    bar_colors = ["#EA580C", "#EA580C", "#D97706", "#EA580C", "#D97706"]

    fig, ax = plt.subplots(figsize=(5.0, 2.7), dpi=300)
    y_pos = np.arange(len(categories))
    bars = ax.barh(y_pos, counts, align="center", color=bar_colors, height=0.52, edgecolor="#334155", linewidth=0.5)
    
    ax.set_yticks(y_pos)
    ax.set_yticklabels(categories, fontsize=8, fontweight="bold", color="#1E293B")
    ax.invert_yaxis()
    ax.set_xlabel("Número de Achados", fontsize=8.5, fontweight="bold", color="#334155")
    ax.set_xlim(0, 4)
    ax.set_xticks(range(0, 5))
    ax.grid(axis="x", linestyle="--", alpha=0.5, color="#CBD5E1")
    ax.set_axisbelow(True)
    ax.set_title("Achados por Categoria de Segurança", fontsize=10, fontweight="bold", pad=10, color="#0F172A")

    for bar in bars:
        width = bar.get_width()
        ax.text(
            width + 0.08,
            bar.get_y() + bar.get_height() / 2,
            f"{int(width)}",
            ha="left",
            va="center",
            fontsize=8.5,
            fontweight="bold",
            color="#0F172A",
        )

    plt.tight_layout()
    plt.savefig(bar_path, transparent=False, facecolor="#F8FAFC", bbox_inches="tight")
    plt.close()

    return donut_path, bar_path


def build_pdf(pdf_path: Path):
    """Constrói o relatório formal em PDF com paginação perfeita de 5 páginas."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    donut_img, bar_img = generate_charts(pdf_path.parent)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    # Estilos customizados
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=27,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12,
    )

    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=6,
        spaceAfter=5,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=4,
        spaceAfter=3,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.8,
        textColor=colors.HexColor("#334155"),
        spaceAfter=3,
    )

    body_compact = ParagraphStyle(
        "BodyCompact",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.8,
        leading=10.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=2,
    )

    issue_block_style = ParagraphStyle(
        "IssueBlock",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=6.1,
        leading=7.7,
        textColor=colors.HexColor("#0F172A"),
        backColor=colors.HexColor("#F8FAFC"),
        borderColor=colors.HexColor("#94A3B8"),
        borderWidth=0.5,
        borderPadding=5,
        spaceBefore=2,
        spaceAfter=5,
        wordWrap="CJK",
    )

    badge_alta = '<font color="#FFFFFF" bgcolor="#EA580C"><b>&nbsp;ALTA&nbsp;</b></font>'
    badge_media = '<font color="#FFFFFF" bgcolor="#D97706"><b>&nbsp;MÉDIA&nbsp;</b></font>'
    badge_baixa = '<font color="#FFFFFF" bgcolor="#2563EB"><b>&nbsp;BAIXA&nbsp;</b></font>'
    badge_forte = '<font color="#FFFFFF" bgcolor="#059669"><b>&nbsp;SEGURO&nbsp;</b></font>'

    story = []

    # =========================================================================
    # PÁGINA 1: CAPA + ESCOPO + NOTA METODOLÓGICA
    # =========================================================================
    story.append(Paragraph("<font color='#2563EB'><b>[ AUDITORIA DE CÓDIGO E SEGURANÇA ]</b></font>", ParagraphStyle("CoverTag", fontName="Helvetica-Bold", fontSize=9.5, leading=12, spaceAfter=4)))
    story.append(Paragraph("Relatório de Auditoria de Segurança — Central NVR WiFi", title_style))
    story.append(Paragraph("Auditoria Estática de Vulnerabilidades, Controle de Acesso, Isolamento e Riscos de Rede", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2.5, color=colors.HexColor("#2563EB"), spaceAfter=10))

    meta_data = [
        [Paragraph("<b>Projeto Auditado:</b>", body_style), Paragraph("Central NVR WiFi para Linux (Desktop Client & NVR)", body_style)],
        [Paragraph("<b>Versão / Release:</b>", body_style), Paragraph("v1.0.0 (Python 3.10+ / PySide6 / PyAV / OpenCV / XDG)", body_style)],
        [Paragraph("<b>Data da Auditoria:</b>", body_style), Paragraph("29 de Agosto de 2026", body_style)],
        [Paragraph("<b>Escopo Auditado:</b>", body_style), Paragraph("<code>src/central_nvr/*</code>, <code>packaging/*</code>, <code>scripts/*</code>, <code>install_ubuntu.sh</code>", body_style)],
        [Paragraph("<b>Classificação:</b>", body_style), Paragraph("<font color='#B91C1C'><b>DOCUMENTO CONFIDENCIAL / SEGURANÇA TÉCNICA</b></font>", body_style)],
    ]
    t_meta = Table(meta_data, colWidths=[42 * mm, 132 * mm])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 5 * mm))

    # Nota Metodológica e Mapeamento da Stack
    story.append(Paragraph("Nota Metodológica e Mapeamento da Stack", h2_style))
    methodology_p1 = (
        "Esta auditoria analisou minuciosamente o código-fonte da aplicação desktop <b>Central NVR WiFi</b>. "
        "Como trata-se de um software nativo Linux cliente/NVR em Python com interface PySide6 e integração a protocolos "
        "de CFTV (ONVIF WS-Discovery, RTSP, SOAP), cada uma das cinco categorias clássicas de segurança foi adaptada ao ecossistema real:"
    )
    story.append(Paragraph(methodology_p1, body_style))
    story.append(Spacer(1, 2 * mm))

    stack_map_data = [
        [Paragraph("<b>Categoria Padrão</b>", body_style), Paragraph("<b>Equivalente Mapeado na Stack Central NVR WiFi</b>", body_style)],
        [Paragraph("<b>1. Banco sem Tranca</b>", body_style), Paragraph("Permissões de arquivos e diretórios de persistência XDG (<code>settings.json</code>, <code>devices.json</code>) e isolamento multiusuário Linux.", body_style)],
        [Paragraph("<b>2. Permissão no Navegador</b>", body_style), Paragraph("Privilégios de execução no sistema operacional (scripts <code>install_ubuntu.sh</code>, pacotes <code>postinst</code> executados como root, permissão de arquivos em <code>/opt</code>).", body_style)],
        [Paragraph("<b>3. IDOR / Acesso a Objetos</b>", body_style), Paragraph("Manipulação de caminhos em nomes de arquivos, snapshots, gravações e identificadores de câmera (Path Traversal em salvamento e execução de handlers de arquivo).", body_style)],
        [Paragraph("<b>4. Chaves Expostas (Hardcode)</b>", body_style), Paragraph("Armazenamento em texto claro de senhas de câmeras IP, credenciais de conexão NVR, tokens de autenticação e falta de codificação em URIs.", body_style)],
        [Paragraph("<b>5. Inputs / Injeção (XSS)</b>", body_style), Paragraph("Injeção de XML/SOAP em chamadas ONVIF PTZ/Media, blind SSRF em endpoints <code>XAddr</code> e parsing inseguro de XML multicast com <code>xml.etree</code>.", body_style)],
    ]
    t_stack = Table(stack_map_data, colWidths=[45 * mm, 129 * mm])
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
    # PÁGINA 2: RESUMO EXECUTIVO + GRÁFICOS + POSTURA (FORTES & FRACOS)
    # =========================================================================
    story.append(Paragraph("1. Resumo Executivo", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=5))

    exec_summary = (
        "A auditoria identificou um total de <b>8 achados de segurança verificados no código real</b> "
        "(0 Críticos, 3 Altos, 3 Médios e 2 Baixos). Não foram identificadas vulnerabilidades de injeção de comandos de shell direta, "
        "graças ao uso consistente de <code>subprocess.Popen</code> e <code>subprocess.run</code> sem <code>shell=True</code>. "
        "No entanto, destacam-se riscos centrais associados ao <b>armazenamento de senhas em texto puro</b> em <code>devices.json</code>, "
        "<b>permissões permissivas no diretório de configuração do usuário</b>, <b>atribuição de propriedade insegura (chown) no instalador root</b> "
        "e <b>interpolação não sanitizada de tokens em envelopes SOAP ONVIF</b>."
    )
    story.append(Paragraph(exec_summary, body_style))
    story.append(Spacer(1, 2 * mm))

    # Tabela de Métricas Rápidas
    metrics_table_data = [
        [
            Paragraph("<font color='#B91C1C'><b>CRÍTICA</b></font><br/><b>0</b> achados", ParagraphStyle("M1", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#EA580C'><b>ALTA</b></font><br/><b>3</b> achados", ParagraphStyle("M2", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#D97706'><b>MÉDIA</b></font><br/><b>3</b> achados", ParagraphStyle("M3", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#2563EB'><b>BAIXA</b></font><br/><b>2</b> achados", ParagraphStyle("M4", alignment=1, fontSize=8.5, leading=11)),
            Paragraph("<font color='#059669'><b>PONTOS FORTES</b></font><br/><b>4</b> verificados", ParagraphStyle("M5", alignment=1, fontSize=8.5, leading=11)),
        ]
    ]
    t_metrics = Table(metrics_table_data, colWidths=[34.8 * mm, 34.8 * mm, 34.8 * mm, 34.8 * mm, 34.8 * mm])
    t_metrics.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_metrics)
    story.append(Spacer(1, 3 * mm))

    # Gráficos Lado a Lado
    charts_table_data = [
        [
            Image(str(donut_img), width=78 * mm, height=50 * mm),
            Image(str(bar_img), width=92 * mm, height=50 * mm),
        ]
    ]
    t_charts = Table(charts_table_data, colWidths=[80 * mm, 94 * mm])
    t_charts.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t_charts)
    story.append(Spacer(1, 3 * mm))

    # Seção 2: Avaliação de Postura
    story.append(Paragraph("2. Avaliação de Postura: Pontos Fortes e Riscos Centrais", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=4))

    story.append(Paragraph("<b>Pontos Fortes Verificados no Código:</b>", h2_style))
    strengths_data = [
        [
            Paragraph(f"{badge_forte}", body_compact),
            Paragraph("<b>Ofuscação de Credenciais em Logs:</b> <code>stream_worker.py:69-74</code> mascara senhas RTSP com regex antes de emitir logs.", body_compact),
        ],
        [
            Paragraph(f"{badge_forte}", body_compact),
            Paragraph("<b>Execução Segura de Subprocessos:</b> <code>playback_view.py:224</code> e <code>diagnose_camera.py:85</code> usam listas sem <code>shell=True</code>.", body_compact),
        ],
        [
            Paragraph(f"{badge_forte}", body_compact),
            Paragraph("<b>Digest WS-Security OASIS:</b> <code>onvif_client.py:54-91</code> gera PasswordDigest SHA-1 com Nonce sem enviar senha em claro no SOAP.", body_compact),
        ],
        [
            Paragraph(f"{badge_forte}", body_compact),
            Paragraph("<b>Isolamento de Threads:</b> Rede e decodificação rodam em QThreads dedicadas, evitando travamentos da GUI.", body_compact),
        ],
    ]
    t_strengths = Table(strengths_data, colWidths=[20 * mm, 154 * mm])
    t_strengths.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_strengths)
    story.append(Spacer(1, 2 * mm))

    story.append(Paragraph("<b>Riscos Centrais Identificados:</b>", h2_style))
    weaknesses = (
        "• <b>Exposição de Credenciais Locais:</b> Senhas gravadas em texto puro em <code>devices.json</code> com permissões padrão do SO (0644).<br/>"
        "• <b>Privilégios de Sistema em Instalação:</b> <code>install_ubuntu.sh</code> entrega <code>/opt/central-nvr</code> a usuário comum enquanto wrapper roda como root.<br/>"
        "• <b>Manipulação SOAP/ONVIF:</b> Interpolação direta de tokens não sanitizados em envelopes SOAP e aceitação de <code>XAddr</code> arbitrários sem validação."
    )
    story.append(Paragraph(weaknesses, body_compact))

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 3: TABELA DETALHADA DE ACHADOS + PLANO DE AÇÃO (P1, P2, P3)
    # =========================================================================
    story.append(Paragraph("3. Tabela Detalhada de Achados de Segurança", h1_style))
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
            Paragraph("<code>src/central_nvr/core/config.py</code><br/>L23, L107-108, L116-117", body_compact),
            Paragraph("<b>Permissões Inseguras em Arquivos de Credenciais:</b> <code>devices.json</code> criado com umask padrão (0644), permitindo leitura de senhas por outros usuários locais.", body_compact),
        ],
        # Finding 2
        [
            Paragraph(f"{badge_alta}", body_compact),
            Paragraph("<b>SEC-02</b><br/>4. Chaves / Plaintext", body_compact),
            Paragraph("<code>src/central_nvr/core/camera.py</code><br/>L138, L159", body_compact),
            Paragraph("<b>Armazenamento de Senha em Texto Puro:</b> O campo <code>password</code> é serializado diretamente sem criptografia em repouso ou integração com SecretService/Keyring.", body_compact),
        ],
        # Finding 3
        [
            Paragraph(f"{badge_alta}", body_compact),
            Paragraph("<b>SEC-03</b><br/>2. Privilégios / Root", body_compact),
            Paragraph("<code>install_ubuntu.sh</code><br/>L74, L81", body_compact),
            Paragraph("<b>chown Inseguro em Diretório de Sistema:</b> <code>/opt/central-nvr</code> é entregue ao <code>CURRENT_USER</code> enquanto <code>/usr/local/bin/central-nvr</code> executa seu Python, abrindo vetor de LPE.", body_compact),
        ],
        # Finding 4
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-04</b><br/>2. Privilégios / Root", body_compact),
            Paragraph("<code>packaging/debian/postinst</code><br/>L8-11", body_compact),
            Paragraph("<b>pip install como Root no postinst:</b> Script deb instala pacotes PyPI via rede como root sem pinning ou hash verification, suscetível a MITM/supply-chain.", body_compact),
        ],
        # Finding 5
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-05</b><br/>5. Injeção / SOAP", body_compact),
            Paragraph("<code>src/central_nvr/core/onvif_client.py</code><br/>L246, L277, L343", body_compact),
            Paragraph("<b>Injeção de XML/SOAP em Tokens ONVIF:</b> <code>ProfileToken</code> e <code>PresetToken</code> interpolados sem escape XML em envelopes SOAP, permitindo adulteração de estrutura.", body_compact),
        ],
        # Finding 6
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-06</b><br/>5. Injeção / SSRF", body_compact),
            Paragraph("<code>src/central_nvr/core/onvif_client.py</code><br/>L189-199<br/><code>src/central_nvr/scanner/parser.py</code><br/>L55-64", body_compact),
            Paragraph("<b>SSRF em XAddr de Capacidades ONVIF:</b> Cliente redireciona requisições SOAP autenticadas para URLs arbitrárias em <code>&lt;tt:XAddr&gt;</code> sem validar se o host corresponde ao IP da câmera.", body_compact),
        ],
        # Finding 7
        [
            Paragraph(f"{badge_media}", body_compact),
            Paragraph("<b>SEC-07</b><br/>3. IDOR / Arquivos", body_compact),
            Paragraph("<code>src/central_nvr/ui/camera_view.py</code><br/>L384-386<br/><code>src/central_nvr/ui/fullscreen_view.py</code><br/>L262-264", body_compact),
            Paragraph("<b>Path Traversal Potencial em Snapshots:</b> O nome do snapshot concatena <code>self.camera.id</code>. Se o ID contiver <code>../</code>, pode gravar fora da pasta <code>snapshots/</code>.", body_compact),
        ],
        # Finding 8
        [
            Paragraph(f"{badge_baixa}", body_compact),
            Paragraph("<b>SEC-08</b><br/>4. Credenciais / Scripts", body_compact),
            Paragraph("<code>scripts/diagnose_camera.py</code><br/>L63-64", body_compact),
            Paragraph("<b>Falta de URL-Encoding em Credenciais no Diagnóstico:</b> Concatenação de senha na URL RTSP sem <code>urllib.parse.quote</code>, corrompendo conexão com caracteres especiais.", body_compact),
        ],
    ]

    t_findings = Table(findings_table_data, colWidths=[16 * mm, 32 * mm, 46 * mm, 80 * mm])
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

    # Seção 4: Recomendações Priorizadas
    story.append(Paragraph("4. Plano de Ação e Recomendações Priorizadas", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=4))

    recs_data = [
        [
            Paragraph("<b>Prioridade P1<br/>(Imediata)</b>", ParagraphStyle("P1", fontName="Helvetica-Bold", fontSize=8.0, textColor=colors.HexColor("#B91C1C"))),
            Paragraph(
                "1. <b>Restringir Permissões de Arquivo:</b> Ajustar <code>save_devices()</code> para gravar com <code>0600</code> (<code>os.open</code> com <code>O_CREAT | O_WRONLY</code> e <code>0o600</code>) e diretório com <code>0700</code>.<br/>"
                "2. <b>Corrigir Permissões do Instalador:</b> Em <code>install_ubuntu.sh</code>, manter <code>/opt/central-nvr</code> como <code>root:root 755</code>, instalando pacotes no build e evitando que usuários alterem os binários executados pelo wrapper global.",
                body_compact
            ),
        ],
        [
            Paragraph("<b>Prioridade P2<br/>(Curto Prazo)</b>", ParagraphStyle("P2", fontName="Helvetica-Bold", fontSize=8.0, textColor=colors.HexColor("#EA580C"))),
            Paragraph(
                "3. <b>Escape de XML em Tokens ONVIF:</b> Aplicar <code>html.escape()</code> em todos os parâmetros interpolados nos métodos de <code>OnvifClient</code>.<br/>"
                "4. <b>Sanitização de IDs de Câmeras:</b> Aplicar <code>re.sub(r'[^a-zA-Z0-9_-]', '', camera.id)</code> antes de usá-lo em nomes de arquivos de snapshot.<br/>"
                "5. <b>Validação de Host em XAddr (Anti-SSRF):</b> Verificar se o host retornado em <code>XAddr</code> corresponde ao IP configurado da câmera.",
                body_compact
            ),
        ],
        [
            Paragraph("<b>Prioridade P3<br/>(Médio Prazo)</b>", ParagraphStyle("P3", fontName="Helvetica-Bold", fontSize=8.0, textColor=colors.HexColor("#2563EB"))),
            Paragraph(
                "6. <b>Criptografia em Repouso / Keyring:</b> Integrar com a biblioteca <code>keyring</code> do sistema operacional para armazenar senhas no cofre nativo (GNOME Keyring / KWallet).<br/>"
                "7. <b>Refatoração do Pacote Debian:</b> Remover <code>pip install</code> do <code>postinst</code>; declarar dependências nativas no <code>debian/control</code>.",
                body_compact
            ),
        ],
    ]
    t_recs = Table(recs_data, colWidths=[30 * mm, 144 * mm])
    t_recs.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_recs)

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 4: ISSUES PARA O GITHUB (ISSUES 1 E 2)
    # =========================================================================
    story.append(Paragraph("5. Issues Acionáveis para o GitHub (Parte 1)", h1_style))
    story.append(Paragraph("Templates completos em Markdown prontos para abertura de issues no repositório:", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=5))

    # Issue 1
    issue_1_text = """--- ISSUE 1 ---
**Título:** [Segurança] Permissões inseguras e armazenamento em texto claro de senhas em devices.json
**Labels:** security, severity:high, bug, storage

### Descrição do Problema
O arquivo `~/.config/central-nvr/devices.json` armazena senhas de câmeras IP e credenciais NVR em texto puro (plaintext) e é salvo com a máscara de permissões padrão do sistema (0644). Em estações de trabalho ou servidores Linux multiusuário, qualquer usuário local pode ler o arquivo e obter credenciais de toda a rede de monitoramento.

### Evidência
- `src/central_nvr/core/config.py:116-117`:
```python
with open(self.devices_path, "w", encoding="utf-8") as f:
    json.dump(self.devices, f, indent=2, ensure_ascii=False)
```
- `src/central_nvr/core/camera.py:138`:
```python
"password": self.password,
```

### Impacto
Exposição total de senhas de CFTV para processos e usuários não privilegiados na mesma máquina.

### Sugestão de Correção
1. Criar o diretório com `0700` e arquivos com `0600` utilizando `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)`.
2. Integrar suporte opcional ao `keyring` do sistema para proteção de senhas.

### Critérios de Aceite
- [ ] `devices.json` e `settings.json` são criados com permissão estrita `0600`.
- [ ] O diretório `~/.config/central-nvr` possui permissão `0700`.
- [ ] Testes automatizados confirmam permissão octal em sistemas POSIX.
--- FIM ISSUE 1 ---"""

    story.append(Paragraph("<b>Issue 1: Permissões Inseguras e Senhas em devices.json</b>", h2_style))
    story.append(Paragraph(issue_1_text.replace("\n", "<br/>").replace(" ", "&nbsp;"), issue_block_style))
    story.append(Spacer(1, 2 * mm))

    # Issue 2
    issue_2_text = """--- ISSUE 2 ---
**Título:** [Segurança] Vetor de elevação de privilégios por chown inseguro em install_ubuntu.sh e pip no postinst
**Labels:** security, severity:high, packaging, installer

### Descrição do Problema
O instalador `install_ubuntu.sh` executa como root e transfere a posse de `/opt/central-nvr` para o `CURRENT_USER` desprivilegiado, enquanto `/usr/local/bin/central-nvr` executa o interpretador desse diretório. Se o administrador (root) ou outro usuário executar o binário global, código injetado por processos do usuário comum será executado com privilégios elevados. Além disso, `packaging/debian/postinst` executa `pip install` arbitrário pela rede como root.

### Evidência
- `install_ubuntu.sh:74, 81`:
```bash
chown -R "${CURRENT_USER}:${CURRENT_USER}" "${INSTALL_DIR}"
...
exec /opt/central-nvr/venv/bin/python3 -m central_nvr.app "$@"
```
- `packaging/debian/postinst:10`:
```bash
/opt/central-nvr/venv/bin/pip install -q PySide6 opencv-python-headless av requests pillow numpy
```

### Impacto
Potencial elevação de privilégios local para root e risco de supply-chain durante a instalação do pacote deb.

### Sugestão de Correção
1. Manter `/opt/central-nvr` pertencente a `root:root` com permissão `755` (somente leitura para usuários normais).
2. Remover execução do `pip` no `postinst` e declarar dependências nativas no `debian/control`.

### Critérios de Aceite
- [ ] `/opt/central-nvr` não pertence a usuário desprivilegiado.
- [ ] O script `postinst` não executa download de pacotes via pip em tempo de instalação.
--- FIM ISSUE 2 ---"""

    story.append(Paragraph("<b>Issue 2: Elevação de Privilégios no Instalador e Pacote Debian</b>", h2_style))
    story.append(Paragraph(issue_2_text.replace("\n", "<br/>").replace(" ", "&nbsp;"), issue_block_style))

    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 5: ISSUES PARA O GITHUB (ISSUES 3 E 4)
    # =========================================================================
    story.append(Paragraph("5. Issues Acionáveis para o GitHub (Parte 2)", h1_style))
    story.append(Paragraph("Templates completos em Markdown prontos para abertura de issues no repositório:", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=5))

    # Issue 3
    issue_3_text = """--- ISSUE 3 ---
**Título:** [Segurança] Interpolação de XML não escapado em chamadas ONVIF PTZ/Media e risco de SSRF em XAddr
**Labels:** security, severity:medium, onvif, injection

### Descrição do Problema
O cliente `OnvifClient` interpola variáveis como `profile_token` e `preset_token` diretamente em envelopes SOAP sem sanitização XML, abrindo vetor para injeção de tags XML SOAP. Adicionalmente, o cliente aceita URLs `XAddr` arbitrárias retornadas por respostas ONVIF e envia requisições autenticadas com WS-Security para esses destinos sem validação de host.

### Evidência
- `src/central_nvr/core/onvif_client.py:246`:
```python
<trt:ProfileToken>{token}</trt:ProfileToken>
```
- `src/central_nvr/core/onvif_client.py:191`:
```python
self.media_service_url = media_match.group(1).strip()
```

### Impacto
Adulteração de mensagens SOAP e envio de requisições autenticadas para servidores internos arbitrários (SSRF).

### Sugestão de Correção
1. Aplicar `html.escape(token)` em todos os parâmetros inseridos em templates XML.
2. Validar que o host de qualquer `XAddr` seja idêntico ao IP configurado da câmera.

### Critérios de Aceite
- [ ] Todos os tokens em `onvif_client.py` passam por escape XML.
- [ ] Validação de host rejeita `XAddr` divergente do IP da câmera.
--- FIM ISSUE 3 ---"""

    story.append(Paragraph("<b>Issue 3: Injeção de XML/SOAP e Blind SSRF em Endpoints ONVIF</b>", h2_style))
    story.append(Paragraph(issue_3_text.replace("\n", "<br/>").replace(" ", "&nbsp;"), issue_block_style))
    story.append(Spacer(1, 2 * mm))

    # Issue 4
    issue_4_text = """--- ISSUE 4 ---
**Título:** [Segurança] Path Traversal potencial na nomenclatura de snapshots e falta de encoding de URL no script de diagnóstico
**Labels:** security, severity:low, ui, sanitization

### Descrição do Problema
O método de captura de snapshot concatena diretamente `self.camera.id` no caminho do arquivo sem sanitização de caracteres especiais ou travessia de diretório (`../`). No script `scripts/diagnose_camera.py`, as credenciais são concatenadas sem `urllib.parse.quote`.

### Evidência
- `src/central_nvr/ui/camera_view.py:385-386`:
```python
filename = f"snap_{self.camera.id}_{ts}.jpg"
filepath = str(snap_dir / filename)
```
- `scripts/diagnose_camera.py:63-64`:
```python
auth = f"{user}:{password}@" if password else ""
url = f"rtsp://{auth}{ip}:554/onvif1"
```

### Impacto
Possibilidade de gravação de arquivos fora do diretório pretendido caso o ID da câmera seja manipulado e falha de autenticação com senhas complexas.

### Sugestão de Correção
1. Sanitizar `camera.id` com `re.sub(r'[^a-zA-Z0-9_-]', '_', self.camera.id)`.
2. Aplicar `urllib.parse.quote(password)` na construção de URIs RTSP no script de diagnóstico.

### Critérios de Aceite
- [ ] Snapshots gravados contêm apenas caracteres seguros no nome do arquivo.
- [ ] Script de diagnóstico codifica corretamente caracteres especiais em senhas.
--- FIM ISSUE 4 ---"""

    story.append(Paragraph("<b>Issue 4: Sanitização de Nomes de Snapshots e Encoding de URL</b>", h2_style))
    story.append(Paragraph(issue_4_text.replace("\n", "<br/>").replace(" ", "&nbsp;"), issue_block_style))

    # Gerar documento PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"[Sucesso] Relatório PDF gerado em: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")


if __name__ == "__main__":
    report_pdf = Path(__file__).parent / "relatorio-auditoria-seguranca.pdf"
    build_pdf(report_pdf)
