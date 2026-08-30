#!/bin/bash
# ==============================================================================
# Script Auxiliar para Publicação de Novas Versões (Releases) no GitHub
# Central NVR WiFi
# ==============================================================================
set -e

if [ -z "$1" ]; then
    echo "Uso: ./scripts/create_release.sh <versao> [mensagem]"
    echo "Exemplo: ./scripts/create_release.sh 1.1.0 'Adicionado suporte a novo codec e melhorias no PTZ'"
    exit 1
fi

NEW_VER="$1"
# Remover prefixo 'v' se fornecido
NEW_VER="${NEW_VER#v}"
TAG_NAME="v${NEW_VER}"
MSG="${2:-Lançamento da versão ${TAG_NAME}}"

echo "======================================================================"
echo " Preparando Lançamento: ${TAG_NAME}"
echo "======================================================================"

# 1. Atualizar src/central_nvr/__init__.py
sed -i "s/__version__ = .*/__version__ = \"${NEW_VER}\"/g" src/central_nvr/__init__.py

# 2. Atualizar pyproject.toml
sed -i "s/version = \".*\"/version = \"${NEW_VER}\"/g" pyproject.toml

# 3. Atualizar setup.py
sed -i "s/version=\".*\"/version=\"${NEW_VER}\"/g" setup.py

# 4. Atualizar packaging/build_deb.sh e packaging/build_rpm.sh
sed -i "s/VERSION=\".*\"/VERSION=\"${NEW_VER}\"/g" packaging/build_deb.sh
sed -i "s/VERSION=\".*\"/VERSION=\"${NEW_VER}\"/g" packaging/build_rpm.sh

# 5. Atualizar packaging/debian/control
sed -i "s/Version: .*/Version: ${NEW_VER}/g" packaging/debian/control

# 6. Atualizar packaging/rpm/central-nvr.spec
sed -i "s/Version: .*/Version:        ${NEW_VER}/g" packaging/rpm/central-nvr.spec

echo "[1/3] Versão atualizada para ${NEW_VER} nos arquivos do projeto."

# Git commit e tag
git add src/central_nvr/__init__.py pyproject.toml setup.py packaging/
git commit -m "chore(release): bump version to ${TAG_NAME}" || true
git tag -a "${TAG_NAME}" -m "${MSG}"

echo "[2/3] Tag git ${TAG_NAME} criada localmente."
echo "======================================================================"
echo " Para publicar a release no GitHub e acionar a geração de pacotes:"
echo "   git push origin main"
echo "   git push origin ${TAG_NAME}"
echo "======================================================================"
