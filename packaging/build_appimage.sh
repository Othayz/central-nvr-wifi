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

# Downloads com versão e SHA-256 fixos (Issue #20): o build falha se o arquivo baixado divergir.
# CPython: hash publicado no SHA256SUMS da release do python-build-standalone.
PYTHON_STANDALONE_TAG="20261026"
PYTHON_TARBALL="cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_TAG}/${PYTHON_TARBALL}"
PYTHON_SHA256="6a79e922e50006b601f29e688407659da1c2b86ebcd4e9c7254cf234be8b89de"
# appimagetool: release com tag fixa (o runtime dela é embutido em todo AppImage gerado).
# Hash calculado do asset da release 1.9.1 (a release não publica checksum próprio).
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"

# Confere o SHA-256 de um arquivo baixado; remove o arquivo e aborta se divergir
verify_sha256() {
    local file="$1" expected="$2"
    if ! echo "${expected}  ${file}" | sha256sum -c --status -; then
        echo "===> [ERRO] SHA-256 divergente para $(basename "${file}"). Esperado: ${expected}"
        echo "            Obtido:   $(sha256sum "${file}" | cut -d ' ' -f 1)"
        rm -f "${file}"
        exit 1
    fi
    echo "     [OK] SHA-256 conferido: $(basename "${file}")"
}

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
    verify_sha256 "${CACHE_DIR}/${PYTHON_TARBALL}" "${PYTHON_SHA256}"
    echo "     Descompactando CPython em ${APPDIR}/usr..."
    tar -xzf "${CACHE_DIR}/${PYTHON_TARBALL}" --strip-components=1 -C "${APPDIR}/usr"
fi

if [ -d "${APPDIR}/usr/lib/python3.12" ] && [ ! -e "${APPDIR}/usr/lib/python3" ]; then
    ln -s python3.12 "${APPDIR}/usr/lib/python3"
fi

# 2. Sincronizar dependências pré-instaladas (PySide6, av, cv2, etc.)
SITE_PACKAGES="${APPDIR}/usr/lib/python3.12/site-packages"
mkdir -p "${SITE_PACKAGES}"

if [ -d "${PROJECT_ROOT}/venv/lib/python3.12/site-packages/PySide6" ]; then
    echo "===> Sincronizando dependências compiladas de venv/lib/python3.12/site-packages..."
    rsync -a --exclude='pip*' --exclude='_distutils*' "${PROJECT_ROOT}/venv/lib/python3.12/site-packages/" "${SITE_PACKAGES}/"
fi

# Garantir presença de todas as dependências, com versões e hashes do lockfile (Issue #20)
echo "===> Verificando dependências completas (PySide6, av, cv2, numpy, keyring, defusedxml)..."
if ! "${APPDIR}/usr/bin/python3" -c "import PySide6, av, cv2, numpy, keyring, defusedxml" 2>/dev/null; then
    echo "     Instalando dependências do requirements.lock (--require-hashes) no ambiente embutido..."
    "${APPDIR}/usr/bin/python3" -m ensurepip --default-pip 2>/dev/null || true
    "${APPDIR}/usr/bin/python3" -m pip install --no-warn-script-location --require-hashes --no-deps \
        --target "${SITE_PACKAGES}" -r "${PROJECT_ROOT}/requirements.lock"
fi

# Sanity check estrito: se os imports críticos falharem, o build DEVE falhar aqui
echo "===> Validando sanidade das dependências essenciais no runtime do AppImage..."
"${APPDIR}/usr/bin/python3" -c "import PySide6, av, cv2, numpy, PIL, keyring, defusedxml; print('     [OK] Todas as dependências críticas estão presentes.')" || {
    echo "===> [ERRO CRÍTICO] Falha ao importar dependências no ambiente embutido! Interrompendo build."
    exit 1
}

# 3. Copiar código-fonte da aplicação
echo "===> Sincronizando código-fonte da Central NVR..."
mkdir -p "${APPDIR}/usr/src"
rm -rf "${APPDIR}/usr/src/central_nvr"
cp -r "${PROJECT_ROOT}/src/central_nvr" "${APPDIR}/usr/src/"

# Instalar o launcher usr/bin/central-nvr
cp "${PROJECT_ROOT}/packaging/central-nvr-launcher.sh" "${APPDIR}/usr/bin/central-nvr"
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
rm -rf "${APPDIR}/usr/share/metainfo"
mkdir -p "${APPDIR}/usr/share/metainfo"
TODAY=$(date +%Y-%m-%d)
sed -e "s/@VERSION@/${VERSION}/g" -e "s/@DATE@/${TODAY}/g" \
    "${PROJECT_ROOT}/packaging/io.github.othayz.central_nvr.appdata.xml" > "${APPDIR}/usr/share/metainfo/io.github.othayz.central_nvr.appdata.xml"

# 5. Copiar launcher AppRun
echo "===> Instalando launcher portátil AppRun (Host Delegation para VA-API)..."
cp "${PROJECT_ROOT}/packaging/AppRun" "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"

# 6. Limpeza de caches e arquivos temporários no AppDir
echo "===> Removendo caches __pycache__ e arquivos .pyc do AppDir..."
find "${APPDIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${APPDIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

# 7. Obter o appimagetool fixado (sempre o mesmo binário, conferido por SHA-256; sem fallback
#    para outro repositório nem uso de um appimagetool qualquer instalado no sistema)
APPIMAGETOOL="${CACHE_DIR}/appimagetool-${APPIMAGETOOL_VERSION}-x86_64.AppImage"
if [ ! -s "${APPIMAGETOOL}" ]; then
    echo "===> Baixando appimagetool ${APPIMAGETOOL_VERSION}..."
    curl -fsSL -o "${APPIMAGETOOL}" "${APPIMAGETOOL_URL}"
fi
verify_sha256 "${APPIMAGETOOL}" "${APPIMAGETOOL_SHA256}"
chmod +x "${APPIMAGETOOL}"
TOOL_CMD="${APPIMAGETOOL}"

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
