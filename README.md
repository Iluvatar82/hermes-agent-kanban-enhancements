# Kanban Enhancements for Hermes Agent

Operator controls for the [Hermes Agent](https://github.com/NousResearch/hermes-agent) kanban
board, packaged as a plugin: **install it once and it survives every `hermes update`** — no
patched core files, no desktop rebuild.

| | |
|---|---|
| **Separate boards** | The board switcher core's Kanban page has, in the Kanban+ page header: switch between boards, create one (scoped to a Hermes project), rename it, export and import it, archive it. Every control here — the stop switch, the board model, the logs — then applies to the board you picked, and the pick survives a restart. |
| **The board's columns** | The lanes themselves — *Triage, Todo, Geplant, Ready, Läuft, Blockiert, Review, Fertig* — with their cards. |
| **Drag & drop, and a card menu** | Drag a card into another lane, or right-click it and pick one. The lanes the dispatcher owns (*Läuft, Review, Geplant*) refuse the drop instead of failing after it, and the move itself goes through core's own transition rules — a running worker is reclaimed, a *Ready* that a parent still blocks is refused with the reason. |
| **A full task view** | Core's task drawer, beside the lanes: status menu, worker, per-task model override, description (editable), result and last summary, dependencies, comments, activity, run history — plus the worker log and the context meter. |
| **A log that takes over the page** | The four-arrow button above the small log blows it up over the whole page, with every line's timestamp and the day dividers. `Esc` (or the button again) gives the task details back. |
| **Stop the whole board** | One switch that persists. Running workers are reclaimed (tasks go back to *ready*), and nothing dispatches again — not the gateway tick, not the CLI, not the desktop nudge — until you start the board. It survives gateway and app restarts, so a restart never silently resumes work. |
| **A live cap on parallel runs** | `kanban.max_in_progress` is re-read on every tick, so changing it needs no restart, and `0` finally means *explicitly unbounded* instead of falling back to the memory-derived default. |
| **A model for the whole board** | Pick one model and every task run *and* the auto-composer (decompose / specify) use it. A task that pins its own model keeps it. Unset = every profile uses its own model, exactly like the per-task override. |
| **Timestamped worker logs** | Workers prefix every log line with the time they wrote it. Every existing reader keeps the classic format — the stamps are stripped unless a caller asks for them. |
| **Worker context snapshots** | Each worker writes how full its context window is next to its log, so you can see it from outside the process. |
| **A desktop page** | `Kanban+` in the sidebar: the board switcher, the board's columns, board state, the controls above and the task view. Works the same opened as a split tile (right-click the sidebar row ▸ *Open in split*). Plus a status-bar pill, three command-palette entries and a context-fill strip above the chat composer. |

The desktop UI is currently German-only. Everything else (CLI, API, logs) is English.

## Requirements

- Hermes Agent ≥ 0.21
- The desktop app for the UI half; the CLI and REST halves work without it.

## Install

```bash
hermes plugins install Iluvatar82/hermes-agent-kanban-enhancements/kanban-enhancements --enable
```

That installs the package into `$HERMES_HOME/plugins/kanban-enhancements/` and adds it to
`plugins.enabled`. Restart the gateway afterwards (`hermes gateway restart`); the desktop half
appears under **Capabilities → Plugins** and loads without a rebuild.

**Profiles:** plugins are per `HERMES_HOME`, so install it once for the profile whose gateway
runs the dispatcher, and once for every profile that runs kanban workers (that is what gives
those workers timestamped logs and context snapshots):

```bash
hermes -p dev_developer plugins install Iluvatar82/hermes-agent-kanban-enhancements/kanban-enhancements --enable
```

`scripts/install.ps1` (Windows) and `scripts/install.sh` (macOS/Linux) do that for every profile
they find, from a local clone — useful for offline installs and for testing a change.

## Update

Every profile at once:

```powershell
pwsh -File scripts\update.ps1 -RestartGateway
```

It finds the default profile plus every profile under `%LOCALAPPDATA%\hermes\profiles\` that has a
`config.yaml` and re-installs the plugin from GitHub into each (`--force --enable`, which also
updates a copy `hermes plugins update` refuses). `-FromClone` installs the working tree beside the
script instead — that is the one for testing a change. `-Ref <40-char SHA>` pins every profile to
one commit. One profile failing neither stops the others nor hides itself: it is named at the end
and the exit code is non-zero.

Without a clone, the same thing pasted into PowerShell:

```powershell
$spec = 'Iluvatar82/hermes-agent-kanban-enhancements/kanban-enhancements'
$names = @('') + (Get-ChildItem "$env:LOCALAPPDATA\hermes\profiles" -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'config.yaml') } | ForEach-Object Name)
foreach ($n in $names) {
    $p = if ($n) { @('-p', $n) } else { @() }
    hermes @p plugins install $spec --force --enable
}
hermes gateway restart
```

The gateway keeps the previous Python half in memory until it restarts; the desktop half reloads on
its own.

## Use

### Desktop

**Kanban+** in the sidebar. The page's own header carries the **board switcher** — the same one
core's Kanban page has: pick a board, or create, rename, scope, export, import and archive one. It
sits in the page rather than in the workspace's header band, so it is there in a split tile too. The
pick is remembered per install and scopes everything else on the page (a board whose dispatch is
stopped is marked in the list).

Below it the board's **columns** with their cards. **Drag** a card into another lane, or
**right-click** it for *Details öffnen · Verschieben nach … · Task-ID kopieren*. *Läuft*, *Review*
and *Geplant* are the dispatcher's and show a lock rather than accepting a drop.

Clicking a card opens the **task view** beside the lanes: the status menu (the third way to move a
task), worker, the per-task model override, the description (editable), result, last summary,
dependencies, comments, activity, run history, the worker context meter and the worker log. The
four-arrow button above that log blows it up over the whole page — every line with its timestamp,
scrollable — and `Esc` brings the details back. `Esc` again closes the task view.

The status-bar pill shows the picked board's state and opens stop/start; ⌘K/Ctrl+K has *Kanban:
Board stoppen / starten / Board-Seite öffnen*.

### CLI

```
hermes kanban-plus status                 # board state, worker count, cap, board model
hermes kanban-plus stop [reason]          # stop and reclaim running workers
hermes kanban-plus start                  # start and dispatch once
hermes kanban-plus max-parallel [N]       # show or set kanban.max_in_progress (0 = unbounded)
hermes kanban-plus model [MODEL]          # show or set the board model ('unset' clears it)
        [--provider NAME]
hermes kanban-plus log <task> [--tail N]  # worker log WITH its per-line timestamps
        [--no-timestamps]
hermes kanban-plus context <task> [--json]  # the worker's latest context snapshot
```

All of them take `--board <slug>` for a board other than the current one.

### REST

Mounted at `/api/plugins/kanban-enhancements`, behind the same dashboard auth as every other
plugin API:

All task endpoints take `?board=<slug>` and answer from **that** board's database; omitting it uses
the active board.

| Endpoint | What |
|---|---|
| `GET /boards` | Every board with its task counts, the active slug and this plugin's stop switch |
| `POST /boards` `{slug, name, project_id?, switch?}` | Create a board (idempotent) |
| `PATCH /boards/{slug}` `{name?, project_id?, default_workdir?}` | Display name, project scope, workdir (the slug is immutable) |
| `DELETE /boards/{slug}` `?delete=` | Archive (default) or hard-delete; `default` is refused |
| `POST /boards/{slug}/export` `{output?}` | Write the board to a `.tar.gz` on the backend's filesystem |
| `POST /boards/import` `{archive, slug?, switch?}` | Import an archive as a new board |
| `POST /boards/{slug}/switch` | Persist the board as active (CLI parity; the page picks client-side) |
| `GET /projects` | Live Hermes projects, for scoping a board |
| `GET /board` `?include_archived=` | The board grouped into its status columns — what the page draws |
| `GET /state` | Board switch, running workers, cap, board model, and whether the core hooks are active |
| `POST /stop` `{reason?}` | Stop the board, reclaim running workers |
| `POST /start` | Start the board and dispatch once |
| `PUT /max-parallel` `{value}` | Write `kanban.max_in_progress` (`0` = unbounded) |
| `PUT /model` `{model, provider}` | Set the board model; empty `model` clears it |
| `GET /tasks` | Running tasks first, then recently touched ones |
| `GET /tasks/{id}` | One task with its comments, activity, runs and dependency links — what the task view draws |
| `PATCH /tasks/{id}` | Move a task to another column, or edit title/body/assignee/priority/model override |
| `POST /tasks/{id}/comments` `{body, author?}` | Add a comment (a running worker reads it from its context) |
| `GET /tasks/{id}/log?tail=&timestamps=` | Worker log, with or without the line stamps |
| `GET /tasks/{id}/context` | The worker's latest context-window snapshot |

## Settings

Under `plugins.entries.kanban-enhancements.settings` in `config.yaml`:

| Setting | Default | What |
|---|---|---|
| `stop_terminates_workers` | `true` | Stopping also terminates running workers. `false` only blocks new spawns. |
| `log_timestamps` | `true` | Workers stamp every log line. |
| `worker_context_snapshots` | `true` | Workers write the context snapshot. |
| `context_snapshot_interval_seconds` | `15` | Minimum seconds between two snapshots. |

Board state (the stop switch and the board model) lives in `kanban-enhancements.json` beside
`kanban.db`, so it is per board and survives updates and reinstalls.

## How it works, and where it can break

Hermes fires `on_kanban_dispatch_tick` only *after* a tick, so an observer cannot keep a stopped
board from spawning. This plugin therefore wraps three core functions at load time:

| Wrapped | For |
|---|---|
| `kanban_db_dispatch.dispatch_once` | The stop switch and the live cap |
| `kanban_db_dispatch._default_spawn` | The board model for task runs |
| `agent.auxiliary_client.call_llm` | The board model for the auto-composer (only for the `kanban_decomposer` / `triage_specifier` tasks) |

`PATCH /tasks/{id}` is a fourth seam, and a gentler one: it **calls** core's own kanban plugin API
(`plugins/kanban/dashboard/plugin_api.py`, already mounted in the same process) rather than
re-implementing a status transition out of its private helpers. Reclaiming a running worker,
re-gating *Ready* on its parents and closing the open run are core's rules, and a drag on this page
gets exactly them. If a Hermes update moves that module, the endpoint answers `503` and the page
says the move is unavailable — nothing is written half-correctly. `tests/test_plugin_api.py` checks
the fields we send against that module's own model, so the drift shows up in CI rather than on a
board.

Wrapping code this plugin does not own is a real risk, so it is handled openly: the dispatcher
wrapper verifies the signature it expects and **refuses to install** when a Hermes update changed
it. In that case Hermes behaves exactly as shipped, a warning names what is off, `GET /state`
reports `guard_active: false`, and the desktop page shows a banner. Everything else — timestamps,
snapshots, the log view — keeps working, because those use documented plugin hooks.

If you hit that, please open an issue with your Hermes version.

## Development

```bash
git clone https://github.com/Iluvatar82/hermes-agent-kanban-enhancements
cd hermes-agent-kanban-enhancements
HERMES_AGENT_REPO=/path/to/hermes-agent pytest    # 65 tests against a real checkout
node --test tests/desktop/*.test.mjs              # the desktop helpers, with stubbed SDK
ruff check .
```

The Python tests import the plugin exactly the way Hermes does and exercise the seams against a
real Hermes checkout rather than a stub; without `HERMES_AGENT_REPO` (or a checkout in the default
location) they skip. `tests/test_manifest.py` checks `plugin.yaml` against that checkout's own
installer and loader — a manifest the installed Hermes refuses is a plugin nobody can install.

```
kanban-enhancements/      the installable package (this is what lands in $HERMES_HOME/plugins/)
  plugin.yaml             manifest + settings schema
  __init__.py             register(ctx): what is installed where
  board_control.py        the stop switch
  board_model.py          the board model (task runs + auto-composer)
  dispatch_guard.py       the dispatch_once wrapper and the signature guard
  log_stamps.py           per-line log timestamps + the reader patch
  worker_context.py       the context snapshot
  cli_commands.py         hermes kanban-plus …
  dashboard/plugin_api.py the REST namespace (board controls + the board directory)
  desktop/plugin.js       the desktop plugin (plain ESM, no build step): page, board
                          switcher, columns + drag & drop, task view, worker log,
                          status bar, palette
scripts/                  install and update helpers
tests/                    pytest + node --test
```

## License

MIT — see [LICENSE](LICENSE).
