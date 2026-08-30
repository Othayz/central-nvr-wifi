# Central NVR WiFi - Sistema Desktop Linux para Monitoramento e Controle ONVIF / RTSP

Aplicativo desktop profissional para Linux projetado para descoberta de rede, streaming de vídeo de baixa latência com aceleração por hardware (VA-API), controle direcional PTZ e gerenciamento em mosaico (Grid) de Câmeras IP e NVRs.

---

## 1. Definição da Stack Tecnológica

| Camada | Tecnologia | Justificativa Técnica |
| :--- | :--- | :--- |
| **Interface Gráfica (GUI)** | **PySide6 (Qt 6)** | Renderização acelerada por GPU, layouts responsivos dinâmicos (1x1, 2x2, 3x3), suporte nativo a monitores High-DPI e estilização limpa em Dark Mode. |
| **Decodificação de Vídeo** | **PyAV / FFmpeg (VA-API)** | Decodificação direta de streams RTSP H.264 / H.265 via hardware Linux (Intel QuickSync, AMD Radeon via `/dev/dri/renderD128`) com buffer ultrabaixo (<150ms). |
| **Descoberta de Rede** | **WS-Discovery Nativo (UDP Multicast)** | Envio RFC-compliant de sondas SOAP para `239.255.255.250:3702` (escopos ONVIF `NetworkVideoTransmitter`), combinado com scanner assíncrono de portas TCP (554, 80, 8080, 8899). |
| **Controle ONVIF / PTZ** | **Cliente SOAP com WS-Security** | Implementação com autenticação `UsernameToken` (SHA-1 Password Digest e Nonce), resolução dinâmica de URLs RTSP e controle de Pan, Tilt, Zoom e Presets. |
| **Empacotamento Nativo** | **`.deb` (Debian/Ubuntu) e `.rpm` (Fedora/RHEL)** | Estrutura padrão FHS (`/usr/bin`, `/usr/lib`, `/usr/share/applications`, `/usr/share/icons`). |

---

## 2. Estrutura Modular do Código-Fonte

```
Central NVR Wifi/
├── src/
│   └── central_nvr/
│       ├── __init__.py
│       ├── __main__.py
│       ├── app.py                      # Ponto de entrada e ciclo de vida da aplicação Qt
│       ├── scanner/
│       │   ├── __init__.py
│       │   ├── discovery.py            # Descoberta WS-Discovery Multicast & Port Scanner
│       │   └── parser.py               # Parser de envelopes XML SOAP / ProbeMatches
│       ├── core/
│       │   ├── __init__.py
│       │   ├── camera.py               # Modelo de dados da Câmera, status e métricas
│       │   ├── onvif_client.py         # Cliente ONVIF SOAP (Digest Auth, Media, PTZ)
│       │   ├── stream_worker.py        # Thread de streaming de baixa latência (VA-API)
│       │   └── config.py               # Persistência de configurações XDG (~/.config/central-nvr)
│       └── ui/
│           ├── __init__.py
│           ├── main_window.py          # Janela Principal (Topbar, Sidebar, Grid, Timeline)
│           ├── camera_grid.py          # Grid multi-câmeras responsivo (1x1, 2x2, 3x3)
│           ├── camera_view.py          # Viewport individual com OSD (FPS, Latência, Status)
│           ├── ptz_controller.py       # Painel de controle PTZ (D-Pad, Zoom, Presets)
│           ├── discovery_dialog.py     # Diálogo de varredura e adição de câmeras
│           ├── settings_dialog.py      # Diálogo de preferências de hardware e rede
│           ├── timeline_bar.py         # Linha do tempo de gravações e eventos
│           └── styles.py               # Folha de estilos QSS Dark Mode moderna
├── packaging/
│   ├── debian/                         # Arquivos de controle do pacote Debian
│   │   ├── control
│   │   ├── postinst
│   │   └── prerm
│   ├── rpm/                            # Arquivo de especificação RPM
│   │   └── central-nvr.spec
│   ├── icons/                          # Ícone SVG do aplicativo
│   │   └── central-nvr.svg
│   ├── central-nvr.desktop             # Entrada do lançador do sistema Linux
│   ├── build_deb.sh                    # Script de geração do pacote .deb
│   └── build_rpm.sh                    # Script de geração do pacote .rpm
├── requirements.txt                    # Dependências do Python
├── pyproject.toml                      # Metadados de empacotamento Python
├── setup.py                            # Script setup.py
└── README.md                           # Documentação do projeto
```

---

## 3. Instruções de Instalação e Execução

### A. Em Distribuições Debian / Ubuntu / Linux Mint

1. **Instalar dependências de sistema:**
```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv ffmpeg va-driver-all libva-drm2 mesa-va-drivers
```

2. **Criar ambiente virtual e instalar dependências Python:**
```bash
cd "Central NVR Wifi"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Executar a aplicação:**
```bash
python3 -m central_nvr
```

---

### B. Em Distribuições Fedora / RHEL / CentOS / Bazzite

1. **Instalar dependências de sistema:**
```bash
sudo dnf install -y python3 python3-pip ffmpeg libva-utils mesa-va-drivers
```

2. **Criar ambiente virtual e instalar dependências Python:**
```bash
cd "Central NVR Wifi"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Executar a aplicação:**
```bash
python3 -m central_nvr
```

---

## 4. Pipeline de Empacotamento Nativo Linux

### Gerando o Pacote Debian (`.deb`)
Para compilar e gerar o instalador `.deb` compatível com Debian, Ubuntu, Mint e derivados:
```bash
chmod +x packaging/build_deb.sh
./packaging/build_deb.sh
```
O pacote será gerado em `dist/central-nvr_1.0.0_all.deb`. Para instalar no sistema:
```bash
sudo dpkg -i dist/central-nvr_1.0.0_all.deb
sudo apt install -f  # Para resolver dependências faltantes caso necessário
```

---

### Gerando o Pacote Red Hat (`.rpm`)
Para compilar e gerar o pacote `.rpm` compatível com Fedora, RHEL, CentOS e Bazzite:
```bash
chmod +x packaging/build_rpm.sh
./packaging/build_rpm.sh
```
O pacote será gerado em `dist/central-nvr-1.0.0-1.noarch.rpm`. Para instalar no sistema:
```bash
sudo dnf install dist/central-nvr-1.0.0-1.noarch.rpm
```

---

## 5. Principais Recursos e Funcionalidades

- **Varredura Automática ONVIF (WS-Discovery):** Encontra instantaneamente câmeras Intelbras, Hikvision, Dahua, Reolink e marcas compatíveis na rede local via UDP Multicast (`239.255.255.250:3702`).
- **Player de Baixa Latência com VA-API:** Decodificação direta por hardware acelerado pela GPU (Intel/AMD) com tempo de resposta < 150ms.
- **Visualização em Mosaico (Grid 1x1, 2x2, 3x3):** Permite alternar rapidamente entre visualização de 1, 4 ou 9 câmeras simultâneas. Clique duplo em qualquer câmera para modo foco/maximizado.
- **Controle PTZ Direcional:** D-Pad de 8 direções, Zoom In/Out, controle de velocidade e gerenciamento de posições salvas (Presets).
- **Captura de Snapshots e Gravações:** Botão dedicado no OSD de cada câmera para salvar fotos e registrar eventos.
- **Linha do Tempo (Timeline):** Exibição das últimas 24 horas com diferenciação visual entre gravações contínuas e eventos de detecção de movimento.
- **Tema Dark Mode Profissional:** Interface moderna em tons escuros projetada para operações contínuas de segurança e monitoramento sem cansaço visual.
