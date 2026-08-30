Atue como um Engenheiro de Software Sênior especialista em desenvolvimento Linux, redes e streaming de vídeo (RTSP/ONVIF). 

Desenvolva a arquitetura completa e o código-fonte funcional de um aplicativo desktop para Linux com interface gráfica moderna, responsiva e suporte a empacotamento nativo (.deb e .rpm).

### 1. Escopo e Funcionalidades Principais
* **Descoberta de Rede (Network Discovery):**
  * Implementação de varredura local via protocolo **ONVIF (WS-Discovery via UDP multicast 239.255.255.250:3702)** e sondagem rápida de portas padrão (RTSP 554, HTTP 80/8080).
  * Listagem automática de dispositivos encontrados (Câmeras IP, NVRs) com IP, porta, modelo e status de conexão.
* **Visualização e Streaming:**
  * Player de vídeo integrado de baixa latência com decodificação por hardware (VA-API), consumindo fluxos **RTSP / H.264 / H.265**.
  * Suporte a grid multi-câmeras responsivo (1x1, 2x2, 3x3) e modo tela cheia.
* **Controle de Câmeras (PTZ & Configuração):**
  * Controles direcionais PTZ (Pan, Tilt, Zoom) via chamadas de API ONVIF SOAP.
  * Gerenciamento seguro de credenciais (usuário e senha com suporte a digest/WS-Security).
* **Interface Gráfica (GUI):**
  * Escolha uma stack moderna e de alto desempenho (sugestão: Python com PyQt6/PySide6, ou Rust/Go com Webview/Tauri).
  * Design limpo em Dark Mode, com feedback visual de conexões ativas e latência.

---

### 2. Estrutura de Resposta Esperada
1. **Definição da Stack:** Justificativa técnica das bibliotecas escolhidas para rede, reprodução de vídeo e GUI.
2. **Código-Fonte Modular:**
   * Módulo de descoberta (`scanner/discovery`).
   * Módulo de conexão e controle ONVIF/RTSP (`core/camera`).
   * Interface gráfica e player (`ui/view`).
3. **Pipeline de Empacotamento (.deb e .rpm):**
   * Estrutura de diretórios padrão Linux (`/usr/bin`, `/usr/share/applications`, `/usr/share/icons`).
   * Arquivo de configuração de controle (`debian/control`) e script de geração do pacote `.deb`.
   * Arquivo de especificação (`app.spec`) e comandos para geração do pacote `.rpm`.
4. **Instruções de Compilação e Execução:** Comandos diretos de terminal para instalar dependências no Debian/Ubuntu/Mint e Fedora/RHEL.
5. **Referencias** Utilize as imagens da pasta de 'imagens de Referencias' para Referencias sobre a interface.
