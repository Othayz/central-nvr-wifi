# Central NVR WiFi

Sistema desktop profissional para monitoramento, controle motorizado PTZ de câmeras IP ONVIF e streaming RTSP de baixa latência em distribuições Linux.

## Language

**AppImage**:
Formato único e universal de distribuição executável para Linux que encapsula a aplicação e dependências de usuário em uma imagem SquashFS montável sem instalação prévia.
_Avoid_: Pacote nativo, instalador deb, pacote rpm

**AppDir**:
Estrutura padronizada de diretórios que espelha o sistema de arquivos Linux contendo os binários, recursos de interface, bibliotecas do usuário e o inicializador AppRun.
_Avoid_: Build dir, chroot

**Host Delegation**:
Padrão arquitetural de integração no qual drivers de GPU e aceleradores de hardware (libva, drivers Mesa/DRI em `/usr/lib64/dri`) são consumidos diretamente do sistema operacional hospedeiro, viabilizando aceleração por hardware VA-API sem conflito de ABI de kernel.
_Avoid_: Bundling gráfico completo, driver embutido

**Atomic Replacement**:
Mecanismo de atualização no qual o novo binário é baixado, tem sua integridade criptográfica SHA-256 validada e substitui o arquivo original em uma única chamada atômica de sistema (`os.replace`), prevenindo corrupção em caso de encerramento abrupto.
_Avoid_: Atualização in-place via apt/dnf, extração com pkexec
