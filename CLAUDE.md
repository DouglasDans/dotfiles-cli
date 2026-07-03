# dotfiles-cli

CLI em Python para gerenciar dotfiles via symlinks + Git, substituindo o chezmoi.

## Contexto e motivação

O chezmoi impõe um workflow onde os arquivos só podem ser editados por ele. Este projeto resolve isso com symlinks: o arquivo original vira um link para dentro do repo, então editar no lugar certo sincroniza automaticamente com o Git via watcher.

Dois repositórios separados por design:
- **dotfiles-cli** (este repo): ferramenta pública, compartilhável, sem configuração pessoal
- **dotfiles** (repo pessoal do usuário): contém os configs reais + o manifesto `links.toml`

## Stack

- **Python 3.11+** (sem dependências de sistema além do Python)
- **watchdog >=6.0,<7** — watcher de filesystem. O pin de major é intencional: o filtro de tipos de evento do watcher depende de quais classes de evento o watchdog emite (ver Decisões de design)
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
sync_interval_seconds = 300
max_batch_seconds = 300
```

`max_batch_seconds` é o teto do debounce: mesmo sob fluxo contínuo de eventos, um flush acontece no máximo a cada `max_batch_seconds`. Deve ser >= `debounce_seconds` (validado no load, erro orientador). Todos os campos exceto `repo` têm default e são opcionais — configs antigos sem as chaves novas continuam válidos.

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
   - Se symlink já existe e aponta pro lugar certo: registra `[OK]` e pula
   - Se o `target` no repo não existe: registra `[MISSING]` e pula (não cria link quebrado)
   - Cria o diretório pai do `source` se não existir (`os.makedirs(..., exist_ok=True)`)
   - Se existe um **diretório real** no lugar: registra em `dir_conflicts` e pula — **sempre**, independente de `--force`. `cli.py` prompta o usuário (`delete this folder? [y/N]`); `watcher.py` loga e ignora
   - Se existe um **arquivo ou symlink** no lugar:
     - Sem `--force`: registra em `file_conflicts` e pula
     - Com `--force`: remove e cria o symlink
   - Se não existe nada: cria o symlink
3. Retorna `RestoreResult(ok, missing, created, file_conflicts, dir_conflicts)`
4. Totalmente idempotente — rodar duas vezes não quebra nada

Casos de uso:
- **Máquina nova**: chamado automaticamente pelo `init --clone` em modo `--force`
- **Nova entrada no manifesto**: quando outra máquina adiciona um link e o pull traz o `links.toml` atualizado
- **Recuperação**: symlink quebrou por movimentação de arquivo ou re-clone do repo

## Fluxo do comando `unlink`

1. Verifica se o `source` está no manifesto — erro claro se não estiver
2. Verifica se existe algo no `source` que não seja symlink — erro claro se sim (intervenção manual necessária)
3. Remove o symlink (se existir)
4. Move o arquivo do repo de volta para o `source` original
   - Se o `target` não existir no repo: limpa o manifesto e avisa o usuário, sem erro. `cli.py` não deve executar `git rm` neste caso (`remove_link` retorna `(target, existed=False)`)
5. Remove a entrada do `links.toml`
6. Se `existed=True`: `git rm <target> && git add links.toml && git commit -m "unlink: <target>" && git push`
7. Se `existed=False`: `git add links.toml && git commit -m "unlink: <target>" && git push`

O `unlink` propaga para todas as máquinas: na próxima execução do watcher delas, o `git pull` remove o arquivo do repo local. Os symlinks das outras máquinas ficam quebrados até que o usuário rode `dotfiles restore` nelas — o `status` deve exibir isso como `[BROKEN]`.

## Fluxo do watcher (daemon)

1. Verifica `watcher.pid` — se existir com PID ativo, aborta com erro (`"watcher already running (PID X)"`)
2. Grava o próprio PID em `watcher.pid`; remove o arquivo ao encerrar (inclusive em SIGTERM/SIGINT)
3. Inicia o `watchdog` observando o diretório do repo recursivamente, com `event_filter` — uma **whitelist** de tipos de evento que mutam conteúdo (`File{Created,Modified,Deleted,Moved}`, `Dir{Created,Deleted,Moved}`). Eventos de leitura (`opened`, `closed_no_write`) e redundantes (`closed`, `DirModified`) nunca entram na fila do observer
4. Em cada evento aceito pelo filtro:
   - Coleta `src_path` **e** `dest_path` (eventos `moved` têm os dois)
   - Se todos os paths do evento estão dentro de `.git/`: descarta
   - Senão: marca a flag de sujeira e (re)agenda o flush. O intervalo é `min(debounce_seconds, teto restante do lote)` — o primeiro evento do lote inicia o relógio de `max_batch_seconds`
   - **Nenhum subprocesso é executado no caminho do evento** — custo por evento é de microssegundos
5. Quando o timer expira:
   - Verifica se há rebase em andamento (`.git/rebase-merge/` ou `.git/rebase-apply/`) — se sim, loga e aborta o ciclo sem tentar nada
   - `git status --porcelain -z` decide **o que** mudou — os eventos são só o gatilho, o git é a fonte da verdade. Se o working tree está limpo, encerra silenciosamente sem log de erro
   - `git add -A` — o `.gitignore` do repo do usuário é o filtro primário, aplicado pelo próprio git
   - `git commit -m "auto: <paths>"` (até 5 paths listados; acima disso, `auto: N files changed`). "Nothing to commit" loga e encerra sem gravar erro (defesa contra corrida entre o status e o add)
   - `git pull --rebase` — com a mudança local já commitada
   - `git push`
   - Se `links.toml` estava entre os arquivos alterados (local) ou foi trazido pelo pull: executa `restore` (sem `--force`)
   - Loga no journald via `logger -t dotfiles-cli "pushed N changes"` e grava `last_commit`/`last_commit_at` em `state.toml`
6. A cada `sync_interval_seconds`, o `_sync` roda `git pull --rebase` + `git push` — o push aqui é o retry de commits que ficaram sem push por falha de rede em ciclos anteriores. Se o pull trouxe `links.toml` novo, executa `restore`
7. Se qualquer operação git falhar (sem rede, conflito, etc.):
   - Loga o erro no journald, não trava
   - Grava `last_error` e `last_error_at` em `state.toml`
   - Tenta novamente no próximo ciclo de debounce ou no próximo `_sync`

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
MemoryMax=256M

[Install]
WantedBy=default.target
```

`MemoryMax=256M` é contenção deliberada: se qualquer regressão futura voltar a vazar memória, o systemd mata e reinicia o serviço em vez de deixar o vazamento derrubar a máquina (já aconteceu: 9.6GB de RSS e OOM kill do sistema inteiro).

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
- **Falha silenciosa no push**: watcher não pode travar por falta de rede. Loga o erro, grava em `state.toml` e tenta no próximo ciclo. O `_sync` periódico também faz `git push` — é ele que garante que um commit local que ficou sem push (rede fora no momento do flush) seja empurrado em no máximo `sync_interval_seconds`, mesmo sem nenhum evento novo.
- **`git status` como fonte da verdade, não os paths dos eventos**: o watcher trata eventos apenas como gatilho ("algo mudou"); no flush, `git status --porcelain -z` decide o que commitar e `git add -A` stageia. A abordagem anterior ("git add cirúrgico": acumular paths de eventos e adicionar só eles) causou três bugs de produção distintos — pathspec de submodule órfão abortando o lote, paths de `.git/` entrando no add, e arquivos temporários (`mimeapps.list.new`) que geravam evento e sumiam antes do flush, fazendo `git add` falhar com "did not match any files" e abortar o commit dos demais arquivos. Paths crus de inotify não são uma declaração confiável do que commitar; o git é. Consequência aceita: qualquer arquivo não-ignorado sujo no repo entra no commit do ciclo, mesmo que a sujeira venha de outra origem — comportamento desejável para uma ferramenta de backup. O `.gitignore` do repo do usuário é o filtro primário, aplicado pelo próprio git; o CLI não o cria nem o gerencia.
- **Commit antes do pull**: `_flush` faz `git add` → `git commit` → `git pull --rebase` → `git push`, nessa ordem. A ordem inversa (pull antes de commit) foi a original do projeto e nunca funcionou de fato: o motivo de existir um evento pendente é sempre "um arquivo mudou no disco", então o working tree já está sujo no início de todo ciclo, e `git pull --rebase` recusa rodar com qualquer coisa não commitada — mesmo que a mudança pendente não tenha nada a ver com o que vem do remoto. Isso travou o repo pessoal do usuário por 2 meses sem nenhum commit automático bem-sucedido. Com commit primeiro, o rebase sempre tem o que fazer (replay do commit local em cima do remoto) em vez de recusar rodar; e se pull ou push falharem depois, o commit já existe localmente — nada se perde, só falta empurrar no próximo ciclo. Conflito real de conteúdo (mesma linha alterada em duas máquinas) ainda pausa o rebase e exige resolução manual — isso não fica visível em `dotfiles status` hoje (débito técnico registrado, fora de escopo).
- **"Nothing to commit" não é erro**: o caso comum (working tree limpo no flush, ex: pull anterior já trouxe exatamente essa mudança de outra máquina) é resolvido pelo próprio `git status` — flush encerra silenciosamente antes de qualquer add/commit. A tolerância a "nothing to commit" no `git commit` permanece como defesa contra corrida entre o status e o add. Nenhum dos dois casos grava `last_error`.
- **`init --clone` emenda `restore`**: após clonar e configurar o serviço, executa `restore --force` automaticamente para criar todos os symlinks sem interação.
- **`add` é atômico com rollback**: se o symlink falhar após o move, o arquivo é devolvido ao `source` original. O estado do sistema nunca fica parcialmente modificado.
- **`add` e `unlink` fazem push**: operações estruturais (adicionar/remover links do manifesto) propagam imediatamente para o repo remoto. O watcher cuida apenas de mudanças de conteúdo.
- **`unlink` remove do repo**: o arquivo é deletado do repo e commitado. Outras máquinas perdem o symlink no próximo pull e devem rodar `restore` para limpar o estado — o `status` exibe como `[BROKEN]`.
- **Watcher detecta `links.toml`**: mudança no `links.toml` via pull dispara `restore` automático para criar os novos symlinks sem intervenção do usuário.
- **Watcher instância única**: `watcher.pid` previne duas instâncias simultâneas. PID file é removido no encerramento normal e em SIGTERM/SIGINT.
- **Rebase em andamento**: watcher detecta estado de rebase no `.git/` e pula o ciclo em vez de acumular falhas em loop.
- **Whitelist de tipos de evento (`event_filter`) — a lição mais cara do projeto**: o watchdog >= 2.3 emite eventos `opened` e `closed_no_write` para **meras leituras** de arquivo. Como `~/.gitconfig` é symlink para dentro do repo observado, todo comando git de qualquer processo do sistema (starship, IDE, o próprio CLI) gera eventos no repo ao ler a config global — medido: 3 aberturas por invocação de git = 6 eventos. Na arquitetura antiga, cada evento disparava um `git check-ignore` (subprocesso), que por sua vez lia o gitconfig e gerava 6 novos eventos: um loop de realimentação exponencial que pinava um core inteiro (milhares de forks/segundo), crescia a fila **ilimitada** do observer até 9.6GB de RSS e terminava em OOM kill — silenciosamente, porque cada evento também resetava o debounce e o flush nunca rodava. Duas defesas estruturais: (1) `event_filter` no `observer.schedule` com whitelist de eventos que mutam conteúdo — eventos de leitura nunca entram na fila; (2) **zero subprocessos no caminho do evento** — o handler só marca uma flag e agenda timer. Qualquer mudança futura no `on_any_event` deve preservar essas duas propriedades. O pin `watchdog>=6,<7` existe porque a whitelist depende das classes de evento do major instalado.
- **Eventos dentro de `.git/` são descartados no handler**: escritas do próprio git (locks, refs, objects durante `pull`/`commit`/`push`) passam pelo `event_filter` (são `created`/`modified` legítimos), então `on_any_event` descarta eventos cujos paths (src **e** dest) estão todos sob `.git/`. Sem isso, cada flush geraria eventos que agendam o próximo flush em loop (bug histórico: ~10GB de RAM antes do primeiro diagnóstico).
- **Teto de debounce (`max_batch_seconds`)**: o debounce clássico reseta a cada evento — sob fluxo contínuo, o flush nunca rodaria (foi exatamente o modo de falha do loop do gitconfig: horas sem commit e sem log). O intervalo de cada timer é `min(debounce_seconds, tempo restante do lote)`, garantindo um flush no máximo a cada `max_batch_seconds` mesmo sob eventos ininterruptos.

## Regras de implementação

- Módulos não se importam circularmente: `cli.py` chama tudo; módulos internos não chamam `cli.py`
- `config.py` e `manifest.py` são os únicos que tocam em arquivos TOML
- `git.py` é o único que chama `subprocess` com comandos git
- Sem variáveis globais de estado — configuração é passada como argumento
- Paths de `source` usam apenas `Path(path).expanduser()` — **sem** `.resolve()`. `.resolve()` segue symlinks e retorna o arquivo dentro do repo em vez do path lógico do symlink, quebrando toda comparação posterior. O manifesto armazena sempre o path do symlink, não do destino.
- Se `config.toml` não existir ao executar qualquer comando, o erro deve ser orientador: `"config not found — run 'dotfiles init' first"`
- Testes ficam em `tests/` espelhando a estrutura de `dotfiles/`
- Comandos de teste: `python -m pytest tests/`
