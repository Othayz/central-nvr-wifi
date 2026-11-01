# 0001. Adoção do AppImage como Formato Único de Distribuição

## Contexto

Anteriormente, o projeto mantinha matrizes e scripts de empacotamento separados para `.deb` (Debian/Ubuntu) e `.rpm` (Fedora/RHEL). Esse modelo gerava complexidade desproporcional de manutenção, dependia de chamadas a gerenciadores de pacotes de sistema com elevação de privilégios (`pkexec`, `apt`, `dnf`) ou de extração manual propensa a falhas em sistemas operacionais modernos imutáveis (como SteamOS, Bazzite e Fedora Silverblue).

## Decisão

Decidimos descontinuar integralmente os formatos `.deb` e `.rpm` e consolidar a distribuição do Central NVR WiFi exclusivamente no formato portátil e autocontido **AppImage**. O AppImage adota o padrão de *Host Delegation* para bibliotecas gráficas de baixo nível (`libva`, drivers Mesa/DRI), garantindo aceleração por hardware VA-API sem quebra de ABI de GPU, e o módulo de atualização contínua opera por *Atomic Replacement* diretamente no espaço de usuário (`os.replace`), dispensando permissões de root.

## Consequências

- **Positivas**: Eliminação da duplicação de pipelines de empacotamento; distribuição universal garantida para qualquer distribuição Linux; atualizações automáticas silenciosas, atômicas e seguras sem intervenção de `pkexec`; funcionamento nativo em sistemas imutáveis.
- **Negativas/Compromissos**: O binário final do AppImage agrupa o runtime do interpretador Python e bibliotecas Qt/PyAV, resultando em um arquivo único maior que os pacotes nativos enxutos dependentes de bibliotecas do sistema.
