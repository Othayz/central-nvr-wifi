#!/bin/bash
# ==============================================================================
# Script de Construção do Pacote Nativo Fedora/RHEL/CentOS (.rpm)
# Central NVR WiFi
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PACKAGE_NAME="central-nvr"
VERSION="${1:-1.1.0}"
RELEASE="${2:-1}"
RPMBUILD_DIR="${ROOT_DIR}/build/rpmbuild"
DIST_DIR="${ROOT_DIR}/dist"

echo "======================================================================"
echo "Iniciando geração do pacote .rpm para ${PACKAGE_NAME} v${VERSION}..."
echo "======================================================================"

mkdir -p "${RPMBUILD_DIR}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
mkdir -p "${DIST_DIR}"

# 1. Copiar arquivo .spec
cp "${SCRIPT_DIR}/rpm/central-nvr.spec" "${RPMBUILD_DIR}/SPECS/"

# 2. Criar tarball fonte
TARBALL_NAME="${PACKAGE_NAME}-${VERSION}"
TMP_TAR_DIR="${ROOT_DIR}/build/${TARBALL_NAME}"

rm -rf "${TMP_TAR_DIR}"
mkdir -p "${TMP_TAR_DIR}"
find "${ROOT_DIR}/src" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${ROOT_DIR}/src" -type f -name "*.pyc" -delete 2>/dev/null || true
cp -r "${ROOT_DIR}/src" "${TMP_TAR_DIR}/"
cp -r "${ROOT_DIR}/packaging" "${TMP_TAR_DIR}/"
cp "${ROOT_DIR}/README.md" "${TMP_TAR_DIR}/" 2>/dev/null || true

tar --exclude='__pycache__' --exclude='*.pyc' -czf "${RPMBUILD_DIR}/SOURCES/${PACKAGE_NAME}-${VERSION}.tar.gz" -C "${ROOT_DIR}/build" "${TARBALL_NAME}"
rm -rf "${TMP_TAR_DIR}"

# 3. Executar rpmbuild ou fallback para gerador nativo rpm_packer.py
if command -v rpmbuild >/dev/null 2>&1; then
    rpmbuild --define "_topdir ${RPMBUILD_DIR}" -ba "${RPMBUILD_DIR}/SPECS/central-nvr.spec"
    find "${RPMBUILD_DIR}/RPMS" -name "*.rpm" -exec cp {} "${DIST_DIR}/" \;
    echo "======================================================================"
    echo " Pacote RPM gerado com sucesso via rpmbuild em:"
    ls -l "${DIST_DIR}"/*.rpm 2>/dev/null || true
    echo "======================================================================"
else
    echo "rpmbuild não encontrado no sistema. Utilizando gerador nativo rpm_packer.py..."
    OUTPUT_RPM="${DIST_DIR}/${PACKAGE_NAME}-${VERSION}-${RELEASE}.noarch.rpm"
    python3 "${SCRIPT_DIR}/rpm_packer.py" "${ROOT_DIR}" "${OUTPUT_RPM}" "${VERSION}" "${RELEASE}"
    echo "======================================================================"
    echo " Pacote RPM gerado com sucesso em:"
    echo " ${OUTPUT_RPM}"
    echo "======================================================================"
fi
