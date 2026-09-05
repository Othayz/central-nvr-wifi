#!/bin/bash
# ==============================================================================
# Script de Construção do Pacote Nativo Debian/Ubuntu (.deb)
# Central NVR WiFi
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PACKAGE_NAME="central-nvr"
VERSION="${1:-1.1.0}"
ARCH="all"
PKG_DIR="${ROOT_DIR}/build/deb/${PACKAGE_NAME}_${VERSION}_${ARCH}"
DIST_DIR="${ROOT_DIR}/dist"

echo "======================================================================"
echo "Iniciando geração do pacote .deb para ${PACKAGE_NAME} v${VERSION}..."
echo "======================================================================"

# Limpar builds anteriores
rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/usr/lib/${PACKAGE_NAME}"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${DIST_DIR}"

# 1. Copiar metadados DEBIAN
cp "${SCRIPT_DIR}/debian/control" "${PKG_DIR}/DEBIAN/control"
cp "${SCRIPT_DIR}/debian/postinst" "${PKG_DIR}/DEBIAN/postinst"
cp "${SCRIPT_DIR}/debian/prerm" "${PKG_DIR}/DEBIAN/prerm"
chmod 755 "${PKG_DIR}/DEBIAN/postinst" "${PKG_DIR}/DEBIAN/prerm"

# 2. Copiar Código-Fonte Python para /usr/lib/central-nvr
find "${ROOT_DIR}/src" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${ROOT_DIR}/src" -type f -name "*.pyc" -delete 2>/dev/null || true
cp -r "${ROOT_DIR}/src/central_nvr" "${PKG_DIR}/usr/lib/${PACKAGE_NAME}/"
find "${PKG_DIR}/usr/lib/${PACKAGE_NAME}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 3. Criar Executável /usr/bin/central-nvr
cat << 'EOF_INNER' > "${PKG_DIR}/usr/bin/central-nvr"
#!/bin/sh
export PYTHONPATH="/usr/lib/central-nvr:${PYTHONPATH}"
export QT_QPA_PLATFORM="xcb"

if [ -f /opt/central-nvr/venv/bin/python3 ]; then
    exec /opt/central-nvr/venv/bin/python3 -m central_nvr.app "$@"
else
    exec /usr/bin/python3 -m central_nvr.app "$@"
fi
EOF_INNER
chmod 755 "${PKG_DIR}/usr/bin/central-nvr"

# 4. Copiar Desktop Entry e Ícones PNG e SVG
cp "${SCRIPT_DIR}/central-nvr.desktop" "${PKG_DIR}/usr/share/applications/central-nvr.desktop"
mkdir -p "${PKG_DIR}/usr/share/pixmaps"
if [ -f "${SCRIPT_DIR}/icons/central-nvr.png" ]; then
    cp "${SCRIPT_DIR}/icons/central-nvr.png" "${PKG_DIR}/usr/share/pixmaps/central-nvr.png"
fi
if [ -f "${SCRIPT_DIR}/icons/central-nvr.svg" ]; then
    cp "${SCRIPT_DIR}/icons/central-nvr.svg" "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/central-nvr.svg"
fi

for size in 16 24 32 48 64 128 256 512; do
    mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/${size}x${size}/apps"
    if [ -f "${SCRIPT_DIR}/icons/central-nvr-${size}x${size}.png" ]; then
        cp "${SCRIPT_DIR}/icons/central-nvr-${size}x${size}.png" "${PKG_DIR}/usr/share/icons/hicolor/${size}x${size}/apps/central-nvr.png"
    fi
done

# 5. Gerar o pacote .deb com dpkg-deb ou deb_packer.py
if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb --build --root-owner-group "${PKG_DIR}" "${DIST_DIR}/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo "======================================================================"
    echo " Pacote Debian gerado com sucesso via dpkg-deb em:"
    echo " ${DIST_DIR}/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo "======================================================================"
else
    echo "dpkg-deb não encontrado. Utilizando gerador nativo deb_packer.py..."
    python3 "${SCRIPT_DIR}/deb_packer.py" "${PKG_DIR}" "${DIST_DIR}/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo "======================================================================"
    echo " Pacote Debian gerado com sucesso em:"
    echo " ${DIST_DIR}/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
    echo "======================================================================"
fi
