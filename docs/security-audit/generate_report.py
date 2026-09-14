import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
import matplotlib.pyplot as plt

def create_charts(temp_dir):
    # Data for Doughnut Chart
    labels = ['Informativa']
    sizes = [1]
    colors_pie = ['#2563EB']
    
    fig1, ax1 = plt.subplots(figsize=(4, 4))
    wedges, texts, autotexts = ax1.pie(
        sizes, labels=labels, colors=colors_pie, autopct='%1.1f%%',
        startangle=90, pctdistance=0.85, textprops={'color': "black"}
    )
    centre_circle = plt.Circle((0,0), 0.70, fc='white')
    fig1.gca().add_artist(centre_circle)
    ax1.axis('equal')  
    plt.title("Achados por Severidade")
    pie_path = os.path.join(temp_dir, 'severity_pie.png')
    plt.savefig(pie_path, bbox_inches='tight')
    plt.close(fig1)

    # Data for Bar Chart
    categories = ['Banco sem tranca', 'Permissões Frontend', 'IDOR', 'Chaves Expostas', 'XSS/Injeção']
    counts = [0, 0, 0, 1, 0]
    colors_bar = ['#059669', '#059669', '#059669', '#2563EB', '#059669']
    
    fig2, ax2 = plt.subplots(figsize=(6, 4))
    ax2.barh(categories, counts, color=colors_bar)
    ax2.set_xlabel('Quantidade de Achados')
    ax2.set_title('Achados por Categoria (0 = OK/Não Aplicável)')
    ax2.set_xlim(0, max(counts) + 1 if max(counts) > 0 else 1)
    ax2.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    plt.tight_layout()
    bar_path = os.path.join(temp_dir, 'category_bar.png')
    plt.savefig(bar_path, bbox_inches='tight')
    plt.close(fig2)
    
    return pie_path, bar_path

def add_page_number(canvas, doc):
    page_num = canvas.getPageNumber()
    text = "Relatório de Auditoria de Segurança - Central NVR WiFi | Página %s" % page_num
    canvas.saveState()
    canvas.setFont('Helvetica', 9)
    canvas.drawString(20, 15, text)
    canvas.restoreState()

def generate_pdf(output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temp_dir = os.path.dirname(output_path)
    
    pie_path, bar_path = create_charts(temp_dir)
    
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=40, leftMargin=40,
        topMargin=40, bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    h1_style = styles['Heading1']
    h2_style = styles['Heading2']
    
    custom_normal = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=11,
        leading=14,
        spaceAfter=10
    )
    
    story = []
    
    # Capa
    story.append(Spacer(1, 100))
    story.append(Paragraph("Relatório de Auditoria de Segurança", title_style))
    story.append(Spacer(1, 20))
    story.append(Paragraph("Projeto: Central NVR WiFi", h2_style))
    story.append(Spacer(1, 10))
    from datetime import date
    story.append(Paragraph(f"Data: {date.today().strftime('%d/%m/%Y')}", h2_style))
    story.append(Spacer(1, 50))
    
    nota = """<b>Escopo Auditado e Nota Metodológica:</b><br/>
    Auditoria realizada sob as cinco categorias pré-definidas. O projeto <b>Central NVR WiFi</b> é um aplicativo desktop local construído com Python e PySide6 (Qt). Como não há banco de dados remoto (multi-tenant), não há APIs REST, e não existe separação entre frontend e backend no navegador, as categorias de isolamento de tenant, IDOR e checagem dupla de permissões não se aplicam diretamente e foram verificadas para confirmar essa ausência. As categorias de Hardcode de Segredos e XSS/Injection foram rigorosamente mapeadas e verificadas no código.
    """
    story.append(Paragraph(nota, custom_normal))
    story.append(PageBreak())
    
    # Resumo Executivo
    story.append(Paragraph("Resumo Executivo", h1_style))
    story.append(Paragraph("O código auditado apresentou uma postura de segurança excepcional para um app desktop. Nenhuma vulnerabilidade crítica, alta ou média foi encontrada. Foi identificado apenas 1 achado de severidade Informativa em relação a valores padrão.", custom_normal))
    
    story.append(Image(pie_path, width=250, height=250))
    story.append(Image(bar_path, width=350, height=230))
    
    story.append(PageBreak())
    
    # Pontos Fortes e Fracos
    story.append(Paragraph("Pontos Fortes (Proteções Evidenciadas)", h1_style))
    story.append(Paragraph("✅ <b>Cofre de Senhas (Keyring):</b> A aplicação usa de forma exemplar a biblioteca keyring (<i>src/central_nvr/core/config.py</i>) para armazenar senhas de câmeras e tokens do GitHub no cofre de senhas do sistema operacional (ex: KWallet/GNOME Keyring), evitando salvar credenciais em texto puro no disco.", custom_normal))
    story.append(Paragraph("✅ <b>Prevenção de XSS/XML Injection:</b> O renderizador de Markdown e o cliente SOAP aplicam <i>html.escape</i> corretamente antes de utilizar entradas para UI rica e requisições ONVIF (ex: <i>src/central_nvr/core/onvif_client.py</i>, <i>src/central_nvr/ui/update_dialog.py</i>).", custom_normal))
    
    story.append(Paragraph("Pontos Fracos", h1_style))
    story.append(Paragraph("Nenhuma falha de segurança explorável foi identificada. O único apontamento refere-se a valores padrão (placeholders) que podem facilitar preenchimento, mas não representam uma brecha do sistema em si.", custom_normal))
    
    # Tabela de Achados
    story.append(Paragraph("Tabela de Achados Detalhados", h1_style))
    
    data = [
        ["Categoria", "Severidade", "Arquivo:linha", "Descrição"]
    ]
    data.append([
        "Chaves Expostas", 
        "Informativa", 
        "src/central_nvr/core/config.py:61", 
        "Presença de placeholder 'admin' para username padrão."
    ])
    
    table = Table(data, colWidths=[90, 70, 160, 190])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('TEXTCOLOR', (1,1), (1,1), colors.blue),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('WORDWRAP',(0,0),(-1,-1), True)
    ]))
    story.append(table)
    
    story.append(Spacer(1, 20))
    story.append(Paragraph("Categorias Não Aplicáveis:", h2_style))
    na_text = """
    • <b>Banco sem Tranca:</b> N/A. Sem BD ou isolamento multi-tenant.<br/>
    • <b>Permissão Definida no Navegador:</b> N/A. Sem rotas de API/gateways.<br/>
    • <b>IDOR:</b> N/A. Sem manipulação de IDs de objetos remotos.
    """
    story.append(Paragraph(na_text, custom_normal))
    
    story.append(PageBreak())
    
    # Recomendações Priorizadas
    story.append(Paragraph("Recomendações Priorizadas", h1_style))
    story.append(Paragraph("<b>P4 (Informativo):</b> Avaliar a real necessidade de carregar 'admin' como nome de usuário padrão. Pode incentivar o uso do usuário default em vez de encorajar a criação de usuários específicos na câmera.", custom_normal))
    
    # Issues
    story.append(PageBreak())
    story.append(Paragraph("ISSUES PARA O GITHUB", h1_style))
    
    issue1 = """
<font name="Courier">
--- ISSUE 1 ---<br/><br/>
**[Segurança] Remover credenciais padronizadas do código (default_username)**<br/><br/>
**Labels:** `security`, `informativo`<br/><br/>
**Descrição do problema e por que é explorável:**<br/>
Foram encontrados defaults de credenciais preenchidos automaticamente na configuração global e no construtor da UI de descoberta de câmeras. Embora facilite a busca de câmeras novas, o hardcode de credenciais padrão desencoraja práticas fortes de segurança nas câmeras. Não é uma vulnerabilidade explorável, mas é um bad-practice.<br/><br/>
**Evidência:**<br/>
`src/central_nvr/core/config.py:61`<br/>
`"default_username": "admin",`<br/>
`src/central_nvr/ui/settings_dialog.py:123`<br/>
`self.txt_default_user = QLineEdit("admin")`<br/>
`src/central_nvr/ui/discovery_dialog.py:344`<br/>
`self.txt_man_user.setText("admin")`<br/><br/>
**Impacto:**<br/>
Informativo.<br/><br/>
**Sugestão de correção:**<br/>
Remover a inicialização automática com "admin" e deixar os campos vazios.<br/><br/>
**Critérios de aceite:**<br/>
- [ ] Campos de usuário na UI de descoberta estão vazios ao iniciar.<br/>
- [ ] `default_username` em `config.py` é uma string vazia.<br/>
--- FIM ISSUE 1 ---
</font>
    """
    story.append(Paragraph(issue1, custom_normal))
    
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(f"PDF gerado em {output_path}")

if __name__ == "__main__":
    output_path = "/home/Othay/Documentos/security-audit/relatorio-auditoria-seguranca.pdf"
    generate_pdf(output_path)
