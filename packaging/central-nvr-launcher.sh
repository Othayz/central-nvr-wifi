#!/bin/sh
# Launcher instalado em usr/bin/central-nvr dentro do AppDir (executado pelo AppRun e pelo .desktop).
APPDIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
SITE_PACKAGES="${APPDIR}/usr/lib/python3.12/site-packages"
[ ! -d "$SITE_PACKAGES" ] && SITE_PACKAGES="${APPDIR}/usr/lib/python3/site-packages"

export PYTHONHOME="${APPDIR}/usr"
# Apenas caminhos do AppDir: o PYTHONPATH do host não é herdado e não sobra entrada vazia,
# que o Python interpretaria como o diretório atual.
export PYTHONPATH="${APPDIR}/usr/src:${SITE_PACKAGES}"
# -P: não colocar o diretório atual em sys.path[0] (padrão de "python -m"), para que um arquivo
# como argparse.py na pasta de onde o AppImage é executado nunca seja importado.
exec "${APPDIR}/usr/bin/python3" -P -m central_nvr "$@"
