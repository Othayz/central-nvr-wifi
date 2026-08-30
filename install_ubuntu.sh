#!/bin/bash
# ==============================================================================
# Script de Instalação Automática da Central NVR WiFi no Ubuntu / Debian / Mint
# Compatível com Ubuntu 20.04, 22.04 LTS, 24.04 LTS e derivados
# ==============================================================================
set -e

# Cores para o terminal
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}     Instalador Automático - Central NVR WiFi (Ubuntu/Debian)        ${NC}"
echo -e "${BLUE}======================================================================${NC}"

# Verificar permissão de sudo
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Este script precisa de permissões de administrador (sudo) para instalar dependências.${NC}"
    echo -e "Reexecutando com sudo...\n"
    exec sudo bash "$0" "$@"
fi

CURRENT_USER="${SUDO_USER:-$USER}"
USER_HOME=$(eval echo "~${CURRENT_USER}")
INSTALL_DIR="/opt/central-nvr"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\n${GREEN}[1/5] Atualizando repositórios e instalando dependências do sistema...${NC}"
# Habilitar repositório universe (necessário para alguns pacotes multimídia no Ubuntu)
if command -v add-apt-repository >/dev/null 2>&1; then
    add-apt-repository -y universe || true
fi

apt update -y
apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    va-driver-all \
    mesa-va-drivers \
    libva-drm2 \
    libgl1 \
    libglx-mesa0 \
    libegl1 \
    libxkbcommon-x11-0 \
    libxcb-cursor0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-xinerama0

echo -e "\n${GREEN}[2/5] Configurando diretório da aplicação em ${INSTALL_DIR}...${NC}"
rm -rf "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
cp -r "${SOURCE_DIR}/src" "${INSTALL_DIR}/"
cp -r "${SOURCE_DIR}/packaging" "${INSTALL_DIR}/"
cp "${SOURCE_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp "${SOURCE_DIR}/README.md" "${INSTALL_DIR}/" 2>/dev/null || true

echo -e "\n${GREEN}[3/5] Criando ambiente virtual Python isolado (PEP 668 seguro)...${NC}"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# Ajustar permissões seguras (propriedade do root com permissão 755, evitando vetor de LPE)
chown -R root:root "${INSTALL_DIR}"
chmod -R 755 "${INSTALL_DIR}"

echo -e "\n${GREEN}[4/5] Criando comando executável global /usr/local/bin/central-nvr...${NC}"
cat << 'EOF' > /usr/local/bin/central-nvr
#!/bin/bash
export PYTHONPATH="/opt/central-nvr/src:${PYTHONPATH}"
export QT_QPA_PLATFORM="xcb"
exec /opt/central-nvr/venv/bin/python3 -m central_nvr.app "$@"
EOF
chmod 755 /usr/local/bin/central-nvr

echo -e "\n${GREEN}[5/5] Integrando ao menu de aplicativos e lançador do Ubuntu...${NC}"
mkdir -p /usr/share/applications
mkdir -p /usr/share/pixmaps
cp "${SOURCE_DIR}/packaging/icons/central-nvr.png" /usr/share/pixmaps/central-nvr.png

for size in 16 24 32 48 64 128 256 512; do
    mkdir -p "/usr/share/icons/hicolor/${size}x${size}/apps"
    if [ -f "${SOURCE_DIR}/packaging/icons/central-nvr-${size}x${size}.png" ]; then
        cp "${SOURCE_DIR}/packaging/icons/central-nvr-${size}x${size}.png" "/usr/share/icons/hicolor/${size}x${size}/apps/central-nvr.png"
    fi
done

# Copiar desktop entry
cp "${SOURCE_DIR}/packaging/central-nvr.desktop" /usr/share/applications/central-nvr.desktop
sed -i 's|Exec=central-nvr|Exec=/usr/local/bin/central-nvr|g' /usr/share/applications/central-nvr.desktop

# Atualizar caches do sistema
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}       Central NVR WiFi instalada com sucesso no Ubuntu!              ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo -e "Você pode iniciar a aplicação de duas formas:"
echo -e " 1. Procurando por ${BLUE}'Central NVR WiFi'${NC} no menu de aplicativos (Super / Dash)"
echo -e " 2. Executando no terminal: ${BLUE}central-nvr${NC}\n"
