#!/bin/bash
# ==============================================================================
# Script de Construção do Pacote Nativo Fedora/RHEL/CentOS (.rpm)
# Central NVR WiFi
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PACKAGE_NAME="central-nvr"
VERSION="1.0.0"
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
cp -r "${ROOT_DIR}/src" "${TMP_TAR_DIR}/"
cp -r "${ROOT_DIR}/packaging" "${TMP_TAR_DIR}/"
cp "${ROOT_DIR}/README.md" "${TMP_TAR_DIR}/" 2>/dev/null || true

tar -czf "${RPMBUILD_DIR}/SOURCES/${PACKAGE_NAME}-${VERSION}.tar.gz" -C "${ROOT_DIR}/build" "${TARBALL_NAME}"
rm -rf "${TMP_TAR_DIR}"

# 3. Executar rpmbuild
if command -v rpmbuild >/dev/null 2>&1; then
    rpmbuild --define "_topdir ${RPMBUILD_DIR}" -ba "${RPMBUILD_DIR}/SPECS/central-nvr.spec"
    
    # Copiar RPMs gerados para dist/
    find "${RPMBUILD_DIR}/RPMS" -name "*.rpm" -exec cp {} "${DIST_DIR}/" \;
    echo "======================================================================"
    echo " Pacote RPM gerado com sucesso em:"
    ls -l "${DIST_DIR}"/*.rpm 2>/dev/null || true
    echo "======================================================================"
else
    echo "AVISO: 'rpmbuild' não encontrado no sistema."
    echo "Para compilar em uma máquina Fedora/RHEL instale 'rpm-build' e execute:"
    echo "  rpmbuild --define '_topdir ${RPMBUILD_DIR}' -ba ${RPMBUILD_DIR}/SPECS/central-nvr.spec"
fi
