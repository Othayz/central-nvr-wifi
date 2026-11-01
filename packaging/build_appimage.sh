#!/usr/bin/env bash
set -e

# =============================================================================
# Script de Construção do Pacote Universal AppImage (Central NVR WiFi)
# =============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build/appimage"
APPDIR="${BUILD_DIR}/Central_NVR_WiFi.AppDir"
DIST_DIR="${PROJECT_ROOT}/dist"
VERSION=$(grep -m1 'version = ' "${PROJECT_ROOT}/pyproject.toml" | cut -d '"' -f 2)

echo "===> Iniciando build do AppImage Central NVR WiFi v${VERSION}..."

mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/512x512/apps"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${DIST_DIR}"

# 1. Copiar metadados de Desktop e Ícones
echo "===> Instalando ícones e arquivo de menu .desktop..."
cp "${PROJECT_ROOT}/packaging/central-nvr.desktop" "${APPDIR}/central-nvr.desktop"
cp "${PROJECT_ROOT}/packaging/central-nvr.desktop" "${APPDIR}/usr/share/applications/central-nvr.desktop"

cp "${PROJECT_ROOT}/packaging/icons/central-nvr.png" "${APPDIR}/central-nvr.png"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr.png" "${APPDIR}/.DirIcon"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr-512x512.png" "${APPDIR}/usr/share/icons/hicolor/512x512/apps/central-nvr.png"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr-256x256.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/central-nvr.png"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/central-nvr.svg"

# 2. Copiar script inicializador AppRun
echo "===> Instalando launcher portátil AppRun (Host Delegation para VA-API)..."
cp "${PROJECT_ROOT}/packaging/AppRun" "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"

# 3. Empacotar código da aplicação e dependências
echo "===> Sincronizando código-fonte da aplicação..."
mkdir -p "${APPDIR}/usr/src"
cp -r "${PROJECT_ROOT}/src/central_nvr" "${APPDIR}/usr/src/"

# Criar wrapper executável central-nvr se não gerado por PyInstaller
cat <<'EOF' > "${APPDIR}/usr/bin/central-nvr"
#!/bin/sh
APPDIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
export PYTHONPATH="${APPDIR}/usr/src:${APPDIR}/usr/lib/python3/site-packages:${PYTHONPATH}"
exec python3 -m central_nvr "$@"
EOF
chmod +x "${APPDIR}/usr/bin/central-nvr"

# 4. Obter appimagetool se não estiver presente no sistema
APPIMAGETOOL="${PROJECT_ROOT}/build/appimagetool-x86_64.AppImage"
if ! command -v appimagetool &> /dev/null; then
    if [ ! -s "${APPIMAGETOOL}" ]; then
        echo "===> Baixando appimagetool oficial..."
        curl -fsSL -o "${APPIMAGETOOL}" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
            || curl -fsSL -o "${APPIMAGETOOL}" "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
            || rm -f "${APPIMAGETOOL}"
        [ -s "${APPIMAGETOOL}" ] && chmod +x "${APPIMAGETOOL}" || true
    fi
    TOOL_CMD="${APPIMAGETOOL}"
else
    TOOL_CMD="appimagetool"
fi

OUTPUT_APPIMAGE="${DIST_DIR}/Central_NVR_WiFi-${VERSION}-x86_64.AppImage"

# 5. Gerar o AppImage final
if [ -x "${TOOL_CMD}" ] || command -v "${TOOL_CMD}" &> /dev/null; then
    echo "===> Empacotando AppImage com ${TOOL_CMD}..."
    RUN_FLAGS=""
    if [ "${TOOL_CMD}" = "${APPIMAGETOOL}" ]; then
        RUN_FLAGS="--appimage-extract-and-run"
    fi
    ARCH=x86_64 "${TOOL_CMD}" ${RUN_FLAGS} "${APPDIR}" "${OUTPUT_APPIMAGE}"
    chmod +x "${OUTPUT_APPIMAGE}"
    
    # Gerar Checksums SHA-256
    cd "${DIST_DIR}"
    sha256sum "$(basename "${OUTPUT_APPIMAGE}")" > "${OUTPUT_APPIMAGE}.sha256"
    sha256sum Central_NVR_WiFi-*.AppImage > sha256sums.txt 2>/dev/null || true
    
    echo "===> AppImage gerado com sucesso: ${OUTPUT_APPIMAGE}"
    echo "===> Checksum SHA-256:"
    cat "${OUTPUT_APPIMAGE}.sha256"
else
    echo "===> [AVISO] appimagetool não disponível no ambiente atual."
    echo "===> Estrutura AppDir pronta em: ${APPDIR}"
fi
