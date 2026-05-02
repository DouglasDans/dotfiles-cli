# dotfiles-cli

CLI em Python para gerenciar dotfiles via symlinks + Git, substituindo o chezmoi.

## Contexto e motivação

O chezmoi impõe um workflow onde os arquivos só podem ser editados por ele. Este projeto resolve isso com symlinks: o arquivo original vira um link para dentro do repo, então editar no lugar certo sincroniza automaticamente com o Git via watcher.

Dois repositórios separados por design:
- **dotfiles-cli** (este repo): ferramenta pública, compartilhável, sem configuração pessoal
- **dotfiles** (repo pessoal do usuário): contém os configs reais + o manifesto `links.toml`

## Stack

- **Python 3.11+** (sem dependências de sistema além do Python)
- **watchdog** — watcher de filesystem
- **tomllib** (stdlib 3.11+) para leitura de TOML; **tomli-w** para escrita
- **argparse** — CLI
- **subprocess** — chamadas git
- **threading.Timer** — debounce do watcher
- **systemd journal** via `logger` (subprocesso) — sem dependência de lib externa

## Estrutura de pastas

```
dotfiles-cli/
├── dotfiles/
│   ├── __init__.py
│   ├── cli.py           ← entry point, define os comandos via argparse
│   ├── config.py        ← lê/escreve ~/.config/dotfiles-cli/config.toml
│   ├── manifest.py      ← lê/escreve links.toml dentro do repo de dotfiles
│   ├── linker.py        ← lógica de add, unlink, restore
│   ├── git.py           ← commit, push, pull via subprocess
│   └── watcher.py       ← watchdog + debounce + log no journald
├── systemd/
│   └── dotfiles-watch.service.template   ← template do unit file
├── install.sh           ← clona e cria symlink do binário em ~/.local/bin
├── CLAUDE.md
└── README.md
```

## Formato dos arquivos de configuração

### `~/.config/dotfiles-cli/config.toml` (config do CLI, local por máquina)

```toml
repo = "/home/user/dotfiles"
debounce_seconds = 30
```

### `~/.config/dotfiles-cli/state.toml` (estado do watcher, não versionado)

```toml
last_commit = "abc1234"
last_commit_at = "2024-01-15T10:30:00"
last_error = "push failed: connection refused"
last_error_at = "2024-01-15T10:25:00"
```

Escrito pelo watcher após cada operação. Lido pelo `status`. Nunca versionado.

### `~/.config/dotfiles-cli/watcher.pid` (lock de instância)

Contém o PID do processo watcher em execução. Criado na inicialização, removido no encerramento. Se já existir com PID ativo ao iniciar, o novo processo aborta com erro.

### `<repo>/links.toml` (manifesto, versionado no repo pessoal)

```toml
[[links]]
source = "~/.zshrc"
target = "zsh/.zshrc"
tags = ["shell"]

[[links]]
source = "~/.config/nvim"
target = "nvim/"
tags = ["editor"]
```

- `source`: caminho original no sistema (onde o symlink fica)
- `target`: caminho relativo dentro do repo de dotfiles (onde o arquivo real fica)
- `tags`: categorias para `restore --tag <tag>` (útil em máquinas que não precisam de tudo)

## Comandos

| Comando | Descrição |
|---|---|
| `dotfiles init --repo <path>` | Configura CLI com repo local já existente, instala e inicia o serviço systemd |
| `dotfiles init --clone <url>` | Clona o repo, configura, inicia o serviço systemd e executa `restore` automaticamente |
| `dotfiles add <path>` | Move o arquivo/pasta pro repo, cria symlink na origem, registra no manifesto, commita |
| `dotfiles unlink <path>` | Remove o symlink, devolve o arquivo para a origem, remove do manifesto |
| `dotfiles restore [--tag <tag>]` | Recria todos os symlinks a partir do manifesto (idempotente) |
| `dotfiles status` | Mostra estado de cada link (OK, quebrado, drift), status do watcher (running/stopped), último commit e último erro |

O comando `watch` (chamado pelo systemd, não pelo usuário diretamente) é o daemon do watcher.

## Fluxo do comando `add`

1. Verifica se o caminho existe
2. Verifica se o caminho já é um symlink — erro claro se sim (`"already a symlink — use 'dotfiles restore' if it's broken"`)
3. Verifica se já está no manifesto (erro se sim)
4. Sugere destino no repo baseado no `basename` do caminho
5. Pede confirmação interativa (usuário pode sobrescrever o destino sugerido)
6. Move o arquivo/pasta para o repo
7. Cria symlink: `source → <repo>/<target>` — se falhar, desfaz o move (rollback) e aborta com erro
8. Registra no `links.toml`
9. `git add <target> links.toml && git commit -m "add: <target>" && git push`

## Fluxo do `restore`

1. Lê `links.toml` — se não existir, trata como lista vazia e encerra sem erro
2. Para cada entrada (filtrada por tag, se passada):
   - Se symlink já existe e aponta pro lugar certo: loga `[OK]` e pula
   - Se o `target` no repo não existe: loga `[MISSING]` e pula (não cria link quebrado)
   - Cria o diretório pai do `source` se não existir (`os.makedirs(..., exist_ok=True)`)
   - Se existe algo no lugar (arquivo real, symlink errado): pergunta se sobrescreve (exceto em modo `--force`, usado pelo `init --clone`)
   - Se não existe: cria o symlink
3. Totalmente idempotente — rodar duas vezes não quebra nada

Casos de uso:
- **Máquina nova**: chamado automaticamente pelo `init --clone` em modo `--force`
- **Nova entrada no manifesto**: quando outra máquina adiciona um link e o pull traz o `links.toml` atualizado
- **Recuperação**: symlink quebrou por movimentação de arquivo ou re-clone do repo

## Fluxo do comando `unlink`

1. Verifica se o `source` é um symlink gerenciado (está no manifesto) — erro claro se não estiver
2. Remove o symlink
3. Move o arquivo do repo de volta para o `source` original
4. Remove a entrada do `links.toml`
5. Remove o arquivo do repo: `git rm <target>`
6. `git add links.toml && git commit -m "unlink: <target>" && git push`

O `unlink` propaga para todas as máquinas: na próxima execução do watcher delas, o `git pull` remove o arquivo do repo local. Os symlinks das outras máquinas ficam quebrados até que o usuário rode `dotfiles restore` nelas — o `status` deve exibir isso como `[BROKEN]`.

## Fluxo do watcher (daemon)

1. Verifica `watcher.pid` — se existir com PID ativo, aborta com erro (`"watcher already running (PID X)"`)
2. Grava o próprio PID em `watcher.pid`; remove o arquivo ao encerrar (inclusive em SIGTERM/SIGINT)
3. Lê o manifesto e monta a lista de caminhos a observar (os targets dentro do repo)
4. Inicia o `watchdog` observando o diretório do repo
5. Em cada evento de mudança:
   - Se o arquivo alterado for `links.toml`: agenda chamada ao `restore` (sem `--force`) após o debounce, além do commit normal
   - Acumula o caminho do arquivo alterado
   - Reseta o timer de debounce (padrão: 30s, configurável)
6. Quando o timer expira (sem novas mudanças):
   - Verifica se há rebase em andamento (`.git/rebase-merge/` ou `.git/rebase-apply/`) — se sim, loga erro e aborta o ciclo sem tentar pull
   - `git pull --rebase` — sincroniza mudanças de outras máquinas antes de commitar
   - `git add <arquivos acumulados>` — somente os arquivos que geraram eventos, nunca `git add .`
   - `git commit -m "auto: <lista de arquivos alterados>"`
   - `git push`
   - Se `links.toml` estava entre os arquivos do pull: executa `restore` (sem `--force`) para criar symlinks novos
   - Loga no journald via `logger -t dotfiles-cli "pushed N changes"`
   - Grava `last_commit` e `last_commit_at` em `state.toml`
7. Se qualquer operação git falhar (sem rede, conflito, etc.):
   - Loga o erro no journald, não trava
   - Grava `last_error` e `last_error_at` em `state.toml`
   - Tenta novamente no próximo ciclo de debounce

## Template do systemd user service

```ini
[Unit]
Description=dotfiles-cli watcher
After=network.target

[Service]
Type=simple
ExecStart=/home/{user}/.local/bin/dotfiles watch
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

Instalado em `~/.config/systemd/user/dotfiles-watch.service` pelo comando `init`.

## Instalação (install.sh)

```bash
#!/usr/bin/env bash
# Cria symlink do binário principal em ~/.local/bin/dotfiles
# Não requer pip, não requer root
```

O binário principal é `dotfiles/cli.py` com shebang `#!/usr/bin/env python3`.

## Decisões de design

- **Symlink direction**: `~/.zshrc` (symlink) → `<repo>/zsh/.zshrc` (arquivo real). O repo tem o arquivo, não o link.
- **Debounce obrigatório**: autosave de editores pode gerar dezenas de eventos por minuto. O commit só acontece após X segundos de inatividade.
- **Repo separado do CLI**: o CLI não tem opinião sobre o conteúdo dos dotfiles. Qualquer pessoa pode usar o CLI com seu próprio repo.
- **Sem PyPI**: instalação via clone + symlink, sem `pip install`. Requisito de portabilidade.
- **Journald via `logger`**: sem dependência de `systemd-python` ou similar. `subprocess` chamando `logger` é suficiente.
- **`restore` idempotente**: pode ser rodado quantas vezes quiser sem efeito colateral. Essencial para bootstrap de máquina nova.
- **Falha silenciosa no push**: watcher não pode travar por falta de rede. Loga o erro, grava em `state.toml` e tenta no próximo ciclo.
- **`git add` cirúrgico**: watcher acumula os caminhos dos eventos watchdog e faz `git add <arquivos específicos>`. O `.gitignore` do repo do usuário é segunda linha de defesa, não o filtro primário — o CLI não o cria nem o gerencia.
- **Pull antes do push**: watcher faz `git pull --rebase` antes de commitar para incorporar mudanças de outras máquinas. Conflito de rebase é tratado como erro: loga, não commita, tenta no próximo ciclo.
- **`init --clone` emenda `restore`**: após clonar e configurar o serviço, executa `restore --force` automaticamente para criar todos os symlinks sem interação.
- **`add` é atômico com rollback**: se o symlink falhar após o move, o arquivo é devolvido ao `source` original. O estado do sistema nunca fica parcialmente modificado.
- **`add` e `unlink` fazem push**: operações estruturais (adicionar/remover links do manifesto) propagam imediatamente para o repo remoto. O watcher cuida apenas de mudanças de conteúdo.
- **`unlink` remove do repo**: o arquivo é deletado do repo e commitado. Outras máquinas perdem o symlink no próximo pull e devem rodar `restore` para limpar o estado — o `status` exibe como `[BROKEN]`.
- **Watcher detecta `links.toml`**: mudança no `links.toml` via pull dispara `restore` automático para criar os novos symlinks sem intervenção do usuário.
- **Watcher instância única**: `watcher.pid` previne duas instâncias simultâneas. PID file é removido no encerramento normal e em SIGTERM/SIGINT.
- **Rebase em andamento**: watcher detecta estado de rebase no `.git/` e pula o ciclo em vez de acumular falhas em loop.

## Regras de implementação

- Módulos não se importam circularmente: `cli.py` chama tudo; módulos internos não chamam `cli.py`
- `config.py` e `manifest.py` são os únicos que tocam em arquivos TOML
- `git.py` é o único que chama `subprocess` com comandos git
- Sem variáveis globais de estado — configuração é passada como argumento
- Todos os caminhos passam por `os.path.expanduser()` e `os.path.abspath()` antes de qualquer operação
- Se `config.toml` não existir ao executar qualquer comando, o erro deve ser orientador: `"config not found — run 'dotfiles init' first"`
- Testes ficam em `tests/` espelhando a estrutura de `dotfiles/`
- Comandos de teste: `python -m pytest tests/`
