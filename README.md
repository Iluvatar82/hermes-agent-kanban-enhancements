# Kanban Enhancements for Hermes Agent

Operator controls for the [Hermes Agent](https://github.com/NousResearch/hermes-agent) kanban
board, packaged as a plugin: **install it once and it survives every `hermes update`** — no
patched core files, no desktop rebuild.

| | |
|---|---|
| **Separate boards** | The board switcher core's Kanban page has, in the Kanban+ page header: switch between boards, create one (scoped to a Hermes project), rename it, export and import it, archive it. Every control here — the stop switch, the board model, the logs — then applies to the board you picked, and the pick survives a restart. |
| **The board's columns** | The lanes themselves — *Triage, Todo, Geplant, Ready, Läuft, Blockiert, Review, Fertig* — with their cards. Like core's board, a lane folds to a rail: empty ones fold on their own, occupied ones open on their own, the chevron in the header (or a click on the rail) overrides that, and a folded rail still takes a drop. |
| **Adding a task to a lane** | Core's per-lane add, ported: the dashed **+ Aufgabe** at the foot of a lane opens the same dialog with that lane as its target — title, description, priority, workspace, worker, skills, model and goal mode. Creating goes through core's own kanban API, so the defaults, the validation and the "this ready task will sit idle" warning are the ones every other surface gets; this page only decides where the card lands. The lanes the dispatcher hands out (*Läuft, Review, Geplant*) get no **+** — an add that cannot land is worse than no button. |
| **Drag & drop, and a card menu** | Drag a card into another lane, or right-click it and pick one. The lanes the dispatcher owns (*Läuft, Review, Geplant*) refuse the drop instead of failing after it, and the move itself goes through core's own transition rules — a running worker is reclaimed, a *Ready* that a parent still blocks is refused with the reason. |
| **A full task view** | Core's task drawer, over the lanes on the right — a third of the board wide, never narrower than 22rem: status menu, worker, per-task model override, description (editable), result and last summary, dependencies, comments, activity, run history — plus a 200px worker log and the context meter. |
| **A log that takes over the page** | The small log in the task view is the last 16 KiB, plain and wrapped — the same glance core's drawer gives, cheap enough to poll every three seconds. The four-arrow button above it blows the log up over the whole page: the last megabyte (up to 5.000 lines), each with its write time, and a divider whenever the day turns over. `Esc` (or the button again) gives the task details back. |
| **Stop the whole board** | One switch that persists. Running workers are reclaimed (tasks go back to *ready*), and nothing dispatches again — not the gateway tick, not the CLI, not the desktop nudge — until you start the board. It survives gateway and app restarts, so a restart never silently resumes work. |
| **A live cap on parallel runs** | `kanban.max_in_progress` is re-read on every tick, so changing it needs no restart, and `0` finally means *explicitly unbounded* instead of falling back to the memory-derived default. |
| **A model for the whole board** | Pick one model and every task run *and* the auto-composer (decompose / specify) use it. A task that pins its own model keeps it. Unset = every profile uses its own model, exactly like the per-task override. The seams it needs are re-attached on every dispatcher tick, so a gateway that could not patch them at boot repairs itself instead of running the wrong model until the next restart. |
| **Timestamped worker logs** | Workers prefix every log line with the time they wrote it, and write LF line endings whatever the platform thinks a line ends with. Every existing reader keeps the classic format — the stamps are stripped unless a caller asks for them. |
| **Updates from the page** | Kanban+ compares what is running, what is on disk and what its source repository has, and updates itself with one button. It knows the difference between *there is a new version* and *the new version is already installed and the gateway has not loaded it yet*. |
| **Worker context snapshots** | Each worker writes how full its context window is next to its log, so you can see it from outside the process. |
| **A desktop page** | `Kanban+` in the sidebar: the board switcher, the board's columns, board state, the controls above and the task drawer. Works the same opened as a split tile (right-click the sidebar row ▸ *Open in split*). Plus a status-bar pill, three command-palette entries and a context-fill strip above the chat composer. |

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

### From the page

The **Kanban+** page shows its own version beside the board settings, checks its source repository
for a newer one, and installs it with one button. It goes through Hermes' own installer — the same
entry point the desktop's *Install from Git* modal uses — so the result is identical to installing
by hand, and it can only ever reinstall *this* plugin from the source Hermes recorded for it (a fork
updates from the fork).

Two things it deliberately does not hide:

- **It updates the profile the gateway runs under, and only that one.** For every profile, use the
  script below.
- **The gateway keeps serving the old code until it restarts.** Installing replaces the directory
  the backend was imported from, so the row switches to *installiert — Gateway neu starten* until
  you do. That is also why the version line can read `Kanban+ 0.4.0 → 0.5.0`: running, then
  installed.

The update *check* is one HTTPS request to `raw.githubusercontent.com` for the manifest. Set
`update_check: false` (see [Settings](#settings)) to stop the page making it; the button still
works.

### Every profile at once

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

Clicking a card opens the **task drawer** over the right of the board — a third of the width, never
below 22rem, so the lanes keep theirs: the status menu (the third way to move a task), worker, the
per-task model override, the description (editable), result, last summary, dependencies, comments,
activity, run history, the worker context meter and the worker log. The four-arrow button above that
log blows it up over the Kanban+ page — every line with its timestamp, wrapped, scrolling only
vertically — and `Esc` brings the details back. `Esc` again closes the drawer.

A lane with no cards shows as a **rail** you can still drop onto; one with cards is open. The
chevron in a lane header folds it by hand, a click on a rail unfolds it, and that choice lasts until
the lane fills or empties — then the automatic rule takes over again. The choices are remembered per
install.

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
| `GET /state` | Board switch, running workers, cap, board model, and whether the core hooks are active (re-attaching any that are not) |
| `POST /stop` `{reason?}` | Stop the board, reclaim running workers |
| `POST /start` | Start the board and dispatch once |
| `PUT /max-parallel` `{value}` | Write `kanban.max_in_progress` (`0` = unbounded) |
| `PUT /model` `{model, provider}` | Set the board model; empty `model` clears it |
| `GET /version` `?check=` | Running / installed / latest version, the recorded source, and whether a restart is pending |
| `POST /update` `{ref?}` | Reinstall this plugin from its recorded source (`ref` pins one 40-character SHA) |
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
| `update_check` | `true` | The page asks the source repository whether a newer version exists. `false` stops that request; the update button still works. |

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

The composer wrapper takes no board argument and needs none: core's auto-decomposer pins
`HERMES_KANBAN_BOARD` around the call and `get_current_board()` reads it, so the model resolves to
the board being decomposed rather than whichever one was switched to last.

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

### Why the patches are retried, and where the flag lives

All three seams used to be attached exactly once, from `register()`, and the fact that they were
attached was a module global. Both were wrong:

- **Once is too early.** `register()` runs during gateway boot, which is the one moment
  `hermes_cli.kanban_db_dispatch` and (especially) `agent.auxiliary_client` may not import yet. A
  "not yet" was recorded as a "never", and the board model stayed inert for the life of the process.
- **A module global is not the process.** `plugins_loader` gives every Hermes home its own module
  name, so a gateway serving several profiles imports this package several times. Each copy had its
  own flags, and `GET /state` read whichever copy the dashboard API found — reporting
  `guard_active: false` and *"board model set but patches nowhere"* while the real patches were live
  in another copy.

So the flag is now an attribute on the patched function (`core.PATCH_MARKER`), which every copy can
read, and installing is idempotent and retried: at load, once per `on_kanban_dispatch_tick`, on
every `GET /state`, and on `hermes kanban-plus status`. A copy that finds a seam already patched
leaves it alone, and an uninstall only ever unwraps its own wrapper.

## Development

```bash
git clone https://github.com/Iluvatar82/hermes-agent-kanban-enhancements
cd hermes-agent-kanban-enhancements
HERMES_AGENT_REPO=/path/to/hermes-agent pytest    # 98 tests against a real checkout
node --test tests/desktop/*.test.mjs              # the desktop helpers, with stubbed SDK
ruff check .
```

The Python tests import the plugin exactly the way Hermes does and exercise the seams against a
real Hermes checkout rather than a stub; without `HERMES_AGENT_REPO` (or a checkout in the default
location) they skip. `tests/test_manifest.py` checks `plugin.yaml` against that checkout's own
installer and loader — a manifest the installed Hermes refuses is a plugin nobody can install.

`tests/test_desktop_classes.py` is the same idea for CSS. **Tailwind v4 generates the app's
stylesheet by scanning source at build time, and a plugin loaded from disk at runtime is not in
that scan** — so a class works here only if Hermes' own source contains the same string. An
invented arbitrary value (`w-96`, `max-w-[55%]`, `h-[3px]`) produces no rule at all and fails
silently; the test checks every arbitrary class in `plugin.js` against the checkout. Anything
load-bearing goes in an inline `style` instead, where nothing has to be generated for it to apply.

```
kanban-enhancements/      the installable package (this is what lands in $HERMES_HOME/plugins/)
  plugin.yaml             manifest + settings schema
  __init__.py             register(ctx): what is installed where
  board_control.py        the stop switch
  board_model.py          the board model (task runs + auto-composer)
  dispatch_guard.py       the dispatch_once wrapper and the signature guard
  log_stamps.py           per-line log timestamps + the reader patch
  worker_context.py       the context snapshot
  self_update.py          version arithmetic + the install this plugin runs on itself
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
