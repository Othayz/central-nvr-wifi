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
# Versão no formato X.Y.Z (com sufixo opcional de pré-release, ex.: 1.3.0-rc1)
if ! [[ "${NEW_VER}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
    echo "Versão inválida: '${NEW_VER}'. Use o formato X.Y.Z (ex.: 1.3.0 ou 1.3.0-rc1)."
    exit 1
fi
TAG_NAME="v${NEW_VER}"
if git rev-parse -q --verify "refs/tags/${TAG_NAME}" >/dev/null; then
    echo "A tag ${TAG_NAME} já existe."
    exit 1
fi
MSG="${2:-Lançamento da versão ${TAG_NAME}}"

# Issue #76: abortar se já houver arquivos no stage ou alterações nos arquivos de versão antes do sed
STAGED_FILES="$(git diff --cached --name-only)"
if [ -n "${STAGED_FILES}" ]; then
    echo "Erro: há alterações já no stage (git index) que não devem entrar no commit de release:" >&2
    echo "${STAGED_FILES}" >&2
    echo "Faça commit ou retire do stage (git restore --staged) antes de criar a release." >&2
    exit 1
fi

DIRTY_VERSION_FILES="$(git diff --name-only -- src/central_nvr/__init__.py pyproject.toml)"
if [ -n "${DIRTY_VERSION_FILES}" ]; then
    echo "Erro: os arquivos de versão já possuem alterações não commitadas:" >&2
    echo "${DIRTY_VERSION_FILES}" >&2
    echo "Faça commit ou descarte essas alterações antes de criar a release." >&2
    exit 1
fi

echo "======================================================================"
echo " Preparando Lançamento: ${TAG_NAME}"
echo "======================================================================"

# 1. Atualizar src/central_nvr/__init__.py
sed -i "s/__version__ = .*/__version__ = \"${NEW_VER}\"/g" src/central_nvr/__init__.py

# 2. Atualizar pyproject.toml
sed -i "s/version = \".*\"/version = \"${NEW_VER}\"/g" pyproject.toml

echo "[1/3] Versão atualizada para ${NEW_VER} nos arquivos do projeto."

# Git commit e tag (restritos exclusivamente aos arquivos de versão — Issue #76)
git add -- src/central_nvr/__init__.py pyproject.toml
# Sem "|| true": se o commit falhar, a tag não é criada (set -e interrompe o script)
if git diff --cached --quiet -- src/central_nvr/__init__.py pyproject.toml; then
    echo "Nada a commitar: a versão já é ${NEW_VER}?"
    exit 1
fi
git commit -m "chore(release): bump version to ${TAG_NAME}" -- src/central_nvr/__init__.py pyproject.toml
git tag -a "${TAG_NAME}" -m "${MSG}"

echo "[2/3] Tag git ${TAG_NAME} criada localmente."
echo "======================================================================"
echo " Para publicar a release no GitHub e acionar a geração de pacotes:"
echo "   git push origin main"
echo "   git push origin ${TAG_NAME}"
echo "======================================================================"
