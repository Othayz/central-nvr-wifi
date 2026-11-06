#!/usr/bin/env bash
set -e

# =============================================================================
# Script de Construção do Pacote Universal AppImage (Central NVR WiFi)
# =============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build/appimage"
CACHE_DIR="${PROJECT_ROOT}/build/cache"
APPDIR="${BUILD_DIR}/Central_NVR_WiFi.AppDir"
DIST_DIR="${PROJECT_ROOT}/dist"
VERSION=$(grep -m1 'version = ' "${PROJECT_ROOT}/pyproject.toml" | cut -d '"' -f 2)

PYTHON_STANDALONE_TAG="20261026"
PYTHON_TARBALL="cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_TAG}/${PYTHON_TARBALL}"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

export DESKTOPINTEGRATION=no
export APPIMAGELAUNCHER_DISABLE=1

echo "============================================================================="
echo "  Construção do AppImage Universal: Central NVR WiFi v${VERSION} (x86_64)"
echo "============================================================================="

mkdir -p "${CACHE_DIR}"
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/lib"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/512x512/apps"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${DIST_DIR}"

# 1. Obter e descompactar runtime Python 3.12 relocável independente
if [ ! -x "${APPDIR}/usr/bin/python3" ]; then
    echo "===> Obtendo runtime CPython 3.12 Standalone..."
    if [ ! -s "${CACHE_DIR}/${PYTHON_TARBALL}" ]; then
        echo "     Baixando ${PYTHON_TARBALL}..."
        curl -fsSL -o "${CACHE_DIR}/${PYTHON_TARBALL}" "${PYTHON_URL}"
    fi
    echo "     Descompactando CPython em ${APPDIR}/usr..."
    tar -xzf "${CACHE_DIR}/${PYTHON_TARBALL}" --strip-components=1 -C "${APPDIR}/usr"
fi

if [ -d "${APPDIR}/usr/lib/python3.12" ] && [ ! -e "${APPDIR}/usr/lib/python3" ]; then
    ln -s python3.12 "${APPDIR}/usr/lib/python3"
fi

# 2. Sincronizar dependências pré-instaladas (PySide6, av, cv2, etc.)
SITE_PACKAGES="${APPDIR}/usr/lib/python3.12/site-packages"
mkdir -p "${SITE_PACKAGES}"

if [ -d "${PROJECT_ROOT}/venv/lib/python3.12/site-packages" ]; then
    echo "===> Sincronizando dependências compiladas de venv/lib/python3.12/site-packages..."
    rsync -a --exclude='pip*' --exclude='_distutils*' "${PROJECT_ROOT}/venv/lib/python3.12/site-packages/" "${SITE_PACKAGES}/"
fi

# Garantir presença de keyring e defusedxml
echo "===> Verificando dependências essenciais (keyring, defusedxml)..."
if ! "${APPDIR}/usr/bin/python3" -c "import keyring, defusedxml" 2>/dev/null; then
    echo "     Instalando keyring e defusedxml no ambiente embutido..."
    rm -rf "${SITE_PACKAGES}/pip"* 2>/dev/null || true
    "${APPDIR}/usr/bin/python3" -m ensurepip --default-pip 2>/dev/null || true
    "${APPDIR}/usr/bin/python3" -m pip install --no-warn-script-location --target "${SITE_PACKAGES}" keyring defusedxml
fi

# 3. Copiar código-fonte da aplicação
echo "===> Sincronizando código-fonte da Central NVR..."
mkdir -p "${APPDIR}/usr/src"
rm -rf "${APPDIR}/usr/src/central_nvr"
cp -r "${PROJECT_ROOT}/src/central_nvr" "${APPDIR}/usr/src/"

# Criar wrapper executável central-nvr
cat <<'EOF' > "${APPDIR}/usr/bin/central-nvr"
#!/bin/sh
APPDIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
SITE_PACKAGES="${APPDIR}/usr/lib/python3.12/site-packages"
[ ! -d "$SITE_PACKAGES" ] && SITE_PACKAGES="${APPDIR}/usr/lib/python3/site-packages"

export PYTHONHOME="${APPDIR}/usr"
export PYTHONPATH="${APPDIR}/usr/src:${SITE_PACKAGES}:${PYTHONPATH}"
exec "${APPDIR}/usr/bin/python3" -m central_nvr "$@"
EOF
chmod +x "${APPDIR}/usr/bin/central-nvr"

# 4. Copiar metadados de Desktop, Ícones e AppStream
echo "===> Instalando ícones e arquivo de menu .desktop..."
cp "${PROJECT_ROOT}/packaging/central-nvr.desktop" "${APPDIR}/io.github.othayz.central_nvr.desktop"
cp "${PROJECT_ROOT}/packaging/central-nvr.desktop" "${APPDIR}/usr/share/applications/io.github.othayz.central_nvr.desktop"
ln -sf "io.github.othayz.central_nvr.desktop" "${APPDIR}/usr/share/applications/central-nvr.desktop"

cp "${PROJECT_ROOT}/packaging/icons/central-nvr.png" "${APPDIR}/central-nvr.png"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr.png" "${APPDIR}/.DirIcon"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr-512x512.png" "${APPDIR}/usr/share/icons/hicolor/512x512/apps/central-nvr.png"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr-256x256.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/central-nvr.png"
cp "${PROJECT_ROOT}/packaging/icons/central-nvr.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/central-nvr.svg"

echo "===> Instalando metadados AppStream..."
mkdir -p "${APPDIR}/usr/share/metainfo"
TODAY=$(date +%Y-%m-%d)
sed -e "s/@VERSION@/${VERSION}/g" -e "s/@DATE@/${TODAY}/g" \
    "${PROJECT_ROOT}/packaging/central-nvr.appdata.xml" > "${APPDIR}/usr/share/metainfo/io.github.othayz.central_nvr.appdata.xml"
cp "${APPDIR}/usr/share/metainfo/io.github.othayz.central_nvr.appdata.xml" "${APPDIR}/usr/share/metainfo/central-nvr.appdata.xml"

# 5. Copiar launcher AppRun
echo "===> Instalando launcher portátil AppRun (Host Delegation para VA-API)..."
cp "${PROJECT_ROOT}/packaging/AppRun" "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"

# 6. Limpeza de caches e arquivos temporários no AppDir
echo "===> Removendo caches __pycache__ e arquivos .pyc do AppDir..."
find "${APPDIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${APPDIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

# 7. Obter appimagetool se não estiver instalado no sistema
APPIMAGETOOL="${CACHE_DIR}/appimagetool-x86_64.AppImage"
if ! command -v appimagetool &> /dev/null; then
    if [ ! -s "${APPIMAGETOOL}" ]; then
        echo "===> Baixando appimagetool oficial..."
        curl -fsSL -o "${APPIMAGETOOL}" "${APPIMAGETOOL_URL}" \
            || curl -fsSL -o "${APPIMAGETOOL}" "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
            || rm -f "${APPIMAGETOOL}"
        [ -s "${APPIMAGETOOL}" ] && chmod +x "${APPIMAGETOOL}" || true
    fi
    TOOL_CMD="${APPIMAGETOOL}"
else
    TOOL_CMD="appimagetool"
fi

OUTPUT_APPIMAGE="${DIST_DIR}/Central_NVR_WiFi-${VERSION}-x86_64.AppImage"

# 8. Empacotar AppImage com appimagetool
if [ -x "${TOOL_CMD}" ] || command -v "${TOOL_CMD}" &> /dev/null; then
    echo "===> Empacotando AppImage com ${TOOL_CMD}..."
    RUN_FLAGS=""
    if [ "${TOOL_CMD}" = "${APPIMAGETOOL}" ]; then
        RUN_FLAGS="--appimage-extract-and-run"
    fi
    ARCH=x86_64 "${TOOL_CMD}" ${RUN_FLAGS} "${APPDIR}" "${OUTPUT_APPIMAGE}"
    chmod +x "${OUTPUT_APPIMAGE}"
    
    # 9. Gerar Checksums SHA-256
    cd "${DIST_DIR}"
    sha256sum "$(basename "${OUTPUT_APPIMAGE}")" > "${OUTPUT_APPIMAGE}.sha256"
    sha256sum Central_NVR_WiFi-*.AppImage > sha256sums.txt
    [ -s sha256sums.txt ] || { echo "===> [ERRO] sha256sums.txt vazio."; exit 1; }
    
    echo "============================================================================="
    echo "===> AppImage gerado com sucesso: ${OUTPUT_APPIMAGE}"
    echo "===> Tamanho: $(du -h "${OUTPUT_APPIMAGE}" | cut -f1)"
    echo "===> Checksum SHA-256:"
    cat "${OUTPUT_APPIMAGE}.sha256"
    echo "============================================================================="
else
    echo "===> [ERRO] appimagetool não disponível para gerar o arquivo .AppImage."
    exit 1
fi
