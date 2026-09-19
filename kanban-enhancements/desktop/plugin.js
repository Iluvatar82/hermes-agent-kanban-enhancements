/**
 * Kanban+ — the desktop half of the `kanban-enhancements` agent plugin.
 *
 * Loaded UNCOMPILED from disk, so this file is plain ESM: no JSX, no TypeScript,
 * and only the three specifiers the runtime loader rewrites
 * ('@hermes/plugin-sdk', 'react', 'react/jsx-runtime'). Elements are built with
 * jsx()/jsxs()/Fragment, exactly what a compiler would have emitted.
 *
 * What it adds on top of core Kanban:
 *   · a `/kanban-plus` page — board stop/start, a parallel-run cap, a board-wide
 *     model, the worker log with a time gutter, and a per-task context meter
 *   · a statusbar pill with a small stop/start menu
 *   · three command-palette rows
 *   · a 3px context-fill strip above the composer
 *
 * Everything talks to this plugin's OWN backend namespace through `ctx.rest`
 * (`/api/plugins/kanban-enhancements`); the context breakdown for a chat session
 * and the model catalog come from the gateway via `host.request`.
 */

import {
  Badge,
  Button,
  Codicon,
  COMPOSER_AREAS,
  ConfirmDialog,
  Contribute,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  ErrorState,
  Input,
  Loader,
  PALETTE_AREA,
  Popover,
  PopoverContent,
  PopoverTrigger,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  ScrollArea,
  SearchField,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  StatusDot,
  Tip,
  WORKSPACE_PAGE_HEADER_AREA,
  atom,
  cn,
  compactNumber,
  host,
  isSubmitEnter,
  queryClient,
  useMutation,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Fragment, jsx, jsxs } from 'react/jsx-runtime'

// ── backend door ─────────────────────────────────────────────────────────────
// `ctx` only exists inside register(), but components mount long after it
// returned — so the scoped `rest` is parked here and released on dispose. Every
// call goes through `api()`, which fails loudly rather than silently no-opping
// if a hot reload ever leaves a stale component behind.

let restCall = null
let osDoor = null

function api(path, opts) {
  if (!restCall) {
    return Promise.reject(new Error('Kanban+ ist nicht geladen.'))
  }

  return restCall(path, opts)
}

/** The plugin's OS door (native save/open dialogs), for components too deep to
 *  be handed `ctx`. Null before register() and after dispose. */
function pluginOs() {
  if (!osDoor) {
    host.notify({ kind: 'error', message: 'Kanban+ ist nicht geladen.' })
  }

  return osDoor
}

// ── the selected board ───────────────────────────────────────────────────────
// Separate boards are separate databases, so every call carries the slug the
// user picked and every cache key is scoped by it: switching boards is a clean
// cache miss, never a stale render of the other board. '' means "whatever the
// server calls current" — what a fresh install and the CLI agree on.

/** Selected board slug ('' = the server's current board). Persisted. */
export const $boardSlug = atom('')

const BOARD_SLUG_KEY = 'boardSlug'

/** Append the selected board (and any other params) to a path. */
export function withBoard(path, params = {}) {
  const search = new URLSearchParams(params)
  const slug = $boardSlug.get()

  if (slug) {
    search.set('board', slug)
  }

  const qs = search.toString()

  return qs ? `${path}?${qs}` : path
}

// Board-scoped query keys; the bare prefixes stay usable for invalidation.
const STATE_KEY = ['kanban-plus', 'state']
const TASKS_KEY = ['kanban-plus', 'tasks']
const BOARD_KEY = ['kanban-plus', 'board']
const BOARDS_KEY = ['kanban-plus', 'boards']
const PROJECTS_KEY = ['kanban-plus', 'projects']
const stateKey = slug => ['kanban-plus', 'state', slug]
const tasksKey = slug => ['kanban-plus', 'tasks', slug]
const boardKey = slug => ['kanban-plus', 'board', slug]
const logKey = (slug, id) => ['kanban-plus', 'log', slug, id]
const contextKey = (slug, id) => ['kanban-plus', 'context', slug, id]

const fetchState = () => api(withBoard('/state'))
const fetchTasks = () => api(withBoard('/tasks'))
const fetchBoard = () => api(withBoard('/board'))
const stopBoard = reason => api(withBoard('/stop'), { method: 'POST', body: reason ? { reason } : {} })
const startBoard = () => api(withBoard('/start'), { method: 'POST' })
const putMaxParallel = value => api(withBoard('/max-parallel'), { method: 'PUT', body: { value } })
const putBoardModel = (model, provider) => api(withBoard('/model'), { method: 'PUT', body: { model, provider } })
const fetchTaskLog = id =>
  api(withBoard(`/tasks/${encodeURIComponent(id)}/log`, { tail: '1048576', timestamps: 'true' }))
const fetchTaskContext = id => api(withBoard(`/tasks/${encodeURIComponent(id)}/context`))

// The board directory itself. `ctx.rest` cannot leave this plugin's namespace,
// so these mirror core's own `/boards` endpoints rather than borrowing them.
const fetchBoards = () => api('/boards')
const fetchProjects = () => api('/projects')
const createBoard = (slug, name, projectId) =>
  api('/boards', { method: 'POST', body: { slug, name, ...(projectId ? { project_id: projectId } : {}) } })
const updateBoard = (slug, patch) => api(`/boards/${encodeURIComponent(slug)}`, { method: 'PATCH', body: patch })
const deleteBoard = slug => api(`/boards/${encodeURIComponent(slug)}`, { method: 'DELETE' })
const exportBoard = (slug, output) =>
  api(`/boards/${encodeURIComponent(slug)}/export`, { method: 'POST', body: { output } })
const importBoard = archive => api('/boards/import', { method: 'POST', body: { archive } })

/** Invalidate everything a stop/start can have moved — usable outside React. */
function refreshAll(qc) {
  const client = qc ?? queryClient

  void client.invalidateQueries({ queryKey: STATE_KEY })
  void client.invalidateQueries({ queryKey: TASKS_KEY })
  // A stop reclaims running tasks back into Ready, so the lanes moved too.
  void client.invalidateQueries({ queryKey: BOARD_KEY })
  // A stop/start also flips the switcher's per-board marker.
  void client.invalidateQueries({ queryKey: BOARDS_KEY })
}

// ── pure helpers (also named exports, so they can be unit-tested) ─────────────

// The worker prefixes each line with its write time (`[2026-09-17T10:23:45+02:00] `).
const STAMP_RE = /^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\] /

// CSI (colors, cursor moves) and OSC (titles, hyperlinks) escape sequences.
const ANSI_RE = /\u001b\[[0-?]*[ -/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)|\u001b[@-Z\\-_]/g

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const fullFmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
const dayFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', weekday: 'short', year: 'numeric' })

/** Drop ANSI styling. The `includes` guard keeps the common (clean) line free. */
export function stripAnsi(text) {
  return typeof text === 'string' && text.includes('\u001b') ? text.replace(ANSI_RE, '') : (text ?? '')
}

function localDayKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
}

/**
 * Split a stamped log into display rows: one `{kind:'line'}` per line, plus a
 * `{kind:'day'}` divider whenever the LOCAL date changes. A line's carriage
 * returns collapse to what a terminal would show last (progress bars), and
 * unstamped lines keep an empty gutter instead of borrowing the previous time.
 */
export function buildLogRows(content) {
  const lines = String(content ?? '').split('\n')

  // A trailing newline is a terminator, not an empty last line.
  if (lines.at(-1) === '') {
    lines.pop()
  }

  const rows = []
  // One formatted label per distinct ISO stamp: a 1 MiB tail is tens of
  // thousands of lines but only a few thousand distinct seconds.
  const labels = new Map()
  let day = ''
  let lastIso = ''

  for (const raw of lines) {
    let line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    const match = STAMP_RE.exec(line)
    let iso = null
    let at = null

    if (match) {
      line = line.slice(match[0].length)
      const parsed = new Date(match[1])

      if (!Number.isNaN(parsed.getTime())) {
        iso = match[1]
        at = parsed
      }
    }

    const cr = line.lastIndexOf('\r')
    const text = stripAnsi(cr >= 0 ? line.slice(cr + 1) : line)

    if (!iso || !at) {
      rows.push({ iso: null, kind: 'line', repeat: false, text, time: '', title: '' })

      continue
    }

    const key = localDayKey(at)

    if (key !== day) {
      day = key
      rows.push({ kind: 'day', label: dayFmt.format(at) })
    }

    let label = labels.get(iso)

    if (!label) {
      label = { time: timeFmt.format(at), title: fullFmt.format(at) }
      labels.set(iso, label)
    }

    rows.push({ iso, kind: 'line', repeat: iso === lastIso, text, time: label.time, title: label.title })
    lastIso = iso
  }

  return rows
}

const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

/** Binary sizes, because the backend's `tail` is a byte budget (1 MiB). */
export function formatBytes(bytes) {
  const num = Number(bytes)

  if (!Number.isFinite(num) || num <= 0) {
    return '0 B'
  }

  let value = num
  let unit = 0

  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }

  // Whole bytes never need a decimal; everything else gets one significant one.
  const shown = unit === 0 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, '')

  return `${shown} ${BYTE_UNITS[unit]}`
}

/**
 * Occupancy tone for a context meter: calm until the window gets tight, amber
 * past 75%, red past 90% (compaction territory). Returns a NAME rather than a
 * color so it stays trivially assertable in a unit test; `toneColor` maps it.
 */
export function meterTone(percent) {
  const num = Number(percent)

  if (!Number.isFinite(num)) {
    return 'normal'
  }

  if (num >= 90) {
    return 'critical'
  }

  if (num >= 75) {
    return 'warn'
  }

  return 'normal'
}

/**
 * The board model trigger's label, mirroring the per-task override's
 * `overrideLabel`: `provider: model`, the bare model when the backend stored no
 * provider, and the inherit copy when nothing is pinned. There is no effort
 * field here — the board model is a spawn/auto-composer patch, not a live
 * session's request parameters.
 */
export function modelLabel(value, inheritCopy) {
  const model = String(value?.model ?? '').trim()

  if (!model) {
    return inheritCopy
  }

  const provider = String(value?.provider ?? '').trim()

  return provider ? `${provider}: ${model}` : model
}

const TONE_COLOR = {
  critical: 'var(--color-destructive, #e5484d)',
  normal: 'var(--color-primary)',
  warn: '#fbbf24'
}

const toneColor = tone => TONE_COLOR[tone] ?? TONE_COLOR.normal

/** Neutral fill for a category whose `color` the backend left blank or bogus. */
const NEUTRAL_SEGMENT = 'var(--ui-text-quaternary, #8a8a8a)'

/** Only paint what the browser agrees is a color — a stray label would
 *  otherwise land in `background` and silently render as nothing. */
function safeColor(value) {
  if (typeof value !== 'string' || !value.trim()) {
    return NEUTRAL_SEGMENT
  }

  try {
    return CSS.supports('color', value) ? value : NEUTRAL_SEGMENT
  } catch {
    return NEUTRAL_SEGMENT
  }
}

// ── small time / text helpers ────────────────────────────────────────────────

function parseMs(value) {
  if (!value) {
    return null
  }

  const ms = typeof value === 'number' ? value * (value < 1e12 ? 1000 : 1) : Date.parse(value)

  return Number.isFinite(ms) ? ms : null
}

/** Run time as a clock (`4:12`, `1:23:45`) — reads better than "vor 4 Min". */
function formatElapsed(fromMs, nowMs) {
  const total = Math.max(0, Math.floor((nowMs - fromMs) / 1000))
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = n => String(n).padStart(2, '0')

  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

function formatStamp(value) {
  const ms = parseMs(value)

  return ms === null ? '' : fullFmt.format(new Date(ms))
}

function errText(err) {
  if (err instanceof Error) {
    return err.message
  }

  return typeof err === 'string' && err ? err : 'Unbekannter Fehler'
}

/** Re-render on a tick so elapsed clocks move without a store behind them. */
function useTicker(active, intervalMs) {
  const [, setTick] = useState(0)

  useEffect(() => {
    if (!active) {
      return undefined
    }

    const timer = setInterval(() => setTick(n => n + 1), intervalMs)

    return () => clearInterval(timer)
  }, [active, intervalMs])
}

const SELECTED_TASK_KEY = 'hermes.plugin.kanban-enhancements.selectedTask'

/** Per board: a task id from one board means nothing on the next one. */
export function selectedTaskKey(slug) {
  return slug ? `${SELECTED_TASK_KEY}.${slug}` : SELECTED_TASK_KEY
}

// localStorage throws in a locked-down renderer; a remembered selection is a
// convenience, never a correctness requirement.
function readSelectedTask(slug) {
  try {
    return localStorage.getItem(selectedTaskKey(slug))
  } catch {
    return null
  }
}

function writeSelectedTask(slug, id) {
  try {
    if (id) {
      localStorage.setItem(selectedTaskKey(slug), id)
    } else {
      localStorage.removeItem(selectedTaskKey(slug))
    }
  } catch {
    // ignored — see readSelectedTask
  }
}

// ── the context meter ────────────────────────────────────────────────────────

/** Hover-open delay: a cursor travelling past the strip must not pop the card. */
const OPEN_DELAY_MS = 180
/** Grace period so the pointer can travel from the strip into the popover. */
const CLOSE_DELAY_MS = 160

/** The hover card: one row per category, label left, token count right. */
function BreakdownList({ categories, header }) {
  return jsxs('div', {
    className: 'flex w-56 flex-col gap-1',
    children: [
      header ? jsx('div', { className: 'pb-1 text-[0.6875rem] text-(--ui-text-tertiary)', children: header }) : null,
      categories.length === 0
        ? jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: 'Keine Aufschlüsselung' })
        : jsx('div', {
            className: 'flex flex-col gap-0.5',
            children: categories.map(category =>
              jsxs(
                'div',
                {
                  className: 'flex items-center gap-1.5 text-[0.6875rem]',
                  children: [
                    jsx('span', {
                      className: 'size-1.5 shrink-0 rounded-full',
                      style: { background: safeColor(category.color) }
                    }),
                    jsx('span', {
                      className: 'min-w-0 flex-1 truncate text-(--ui-text-secondary)',
                      children: category.label
                    }),
                    jsx('span', {
                      className: 'shrink-0 tabular-nums text-(--ui-text-quaternary)',
                      children: compactNumber(category.tokens)
                    })
                  ]
                },
                category.id ?? category.label
              )
            )
          })
    ]
  })
}

/**
 * A slim horizontal bar split into the snapshot's categories, with the
 * breakdown on hover. Deliberately hand-rolled instead of the SDK's
 * `ContextMeter`: that export does not exist in stock upstream Hermes, which is
 * what this plugin must keep running against.
 *
 * Renders nothing without a known window size — a meter with no maximum is a
 * decoration, not a readout.
 */
function ContextBar({ align = 'end', className, header, showLabel = true, side = 'top', snapshot }) {
  const [open, setOpen] = useState(false)
  const timer = useRef(null)

  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current)
      }
    },
    []
  )

  const max = Number(snapshot?.context_max) || 0
  const used = Number(snapshot?.context_used) || 0
  const percent = Math.max(0, Math.min(100, Math.round(Number(snapshot?.context_percent) || 0)))
  const categories = Array.isArray(snapshot?.categories) ? snapshot.categories : []

  // Category tokens are estimates while the headline may be measured — scale
  // the segments so together they fill exactly the headline percentage.
  const segments = useMemo(() => {
    const total = categories.reduce((sum, category) => sum + (Number(category.tokens) || 0), 0)

    if (!total || !percent) {
      return []
    }

    return categories.map((category, index) => ({
      color: safeColor(category.color),
      key: category.id ?? `${category.label}-${index}`,
      width: ((Number(category.tokens) || 0) / total) * percent
    }))
  }, [categories, percent])

  if (!max) {
    return null
  }

  const schedule = (next, delay) => {
    if (timer.current) {
      clearTimeout(timer.current)
    }

    timer.current = setTimeout(() => setOpen(next), delay)
  }

  const tone = meterTone(percent)
  const color = toneColor(tone)
  const label = `${compactNumber(used)}/${compactNumber(max)} (${percent}%)`

  const bar = jsxs('div', {
    className: 'relative flex h-[3px] min-w-8 flex-1 overflow-hidden rounded-full bg-(--ui-stroke-tertiary)',
    children: [
      segments.length > 0
        ? segments.map(segment =>
            jsx(
              'span',
              { className: 'h-full min-w-px', style: { background: segment.color, width: `${segment.width}%` } },
              segment.key
            )
          )
        : jsx('span', { className: 'h-full', style: { background: color, width: `${percent}%` } }),
      // A tone cap on the fill's leading edge flags a tight window even when
      // the category colors themselves are muted.
      tone === 'normal'
        ? null
        : jsx('span', {
            className: 'absolute inset-y-0 w-[3px] rounded-full',
            style: { background: color, left: `calc(${percent}% - 3px)` }
          })
    ]
  })

  return jsxs(Popover, {
    onOpenChange: setOpen,
    open,
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsxs('div', {
          'aria-label': label,
          'aria-valuemax': 100,
          'aria-valuemin': 0,
          'aria-valuenow': percent,
          className: cn('flex min-w-0 cursor-default items-center gap-1.5', className),
          'data-slot': 'kanban-plus-context-bar',
          onPointerEnter: () => schedule(true, OPEN_DELAY_MS),
          onPointerLeave: () => schedule(false, CLOSE_DELAY_MS),
          role: 'meter',
          children: [
            bar,
            showLabel
              ? jsx('span', {
                  className: 'shrink-0 text-[0.625rem] tabular-nums text-(--ui-text-quaternary)',
                  style: tone === 'normal' ? undefined : { color },
                  children: label
                })
              : null
          ]
        })
      }),
      jsx(PopoverContent, {
        align,
        className: 'w-auto',
        onCloseAutoFocus: event => event.preventDefault(),
        onOpenAutoFocus: event => event.preventDefault(),
        onPointerEnter: () => schedule(true, 0),
        onPointerLeave: () => schedule(false, CLOSE_DELAY_MS),
        side,
        children: jsx(BreakdownList, { categories, header })
      })
    ]
  })
}

/** The selected task's worker context — fast poll while it runs, slow after. */
function TaskContextMeter({ running, taskId }) {
  const slug = useValue($boardSlug)

  const { data } = useQuery({
    enabled: Boolean(taskId),
    queryFn: () => fetchTaskContext(taskId),
    queryKey: contextKey(slug, taskId ?? ''),
    refetchInterval: running ? 10_000 : 60_000
  })

  if (!data || data.available === false || !data.context_max) {
    return null
  }

  const header = [
    data.model,
    data.profile,
    data.live ? 'live' : data.updated_at ? `Stand ${formatStamp(data.updated_at)}` : null
  ]
    .filter(Boolean)
    .join(' · ')

  return jsxs('div', {
    className: 'flex items-center gap-2',
    children: [
      jsx('span', { className: 'shrink-0 text-[0.625rem] text-(--ui-text-quaternary)', children: 'Kontext' }),
      jsx(ContextBar, {
        align: 'start',
        className: 'max-w-md flex-1',
        header: header || 'Worker-Kontext',
        side: 'bottom',
        snapshot: data
      })
    ]
  })
}

// ── worker log ───────────────────────────────────────────────────────────────

const LogLine = memo(function LogLine({ row }) {
  return jsxs(Fragment, {
    children: [
      row.iso
        ? jsx('time', {
            // `select-none` keeps the gutter out of a copy, so the clipboard
            // gets the plain log and not a column of timestamps.
            className: cn(
              'pr-3 text-right text-[0.6875rem] tabular-nums text-(--ui-text-quaternary) select-none',
              row.repeat && 'opacity-45'
            ),
            dateTime: row.iso,
            title: row.title,
            children: row.time
          })
        : jsx('span', { 'aria-hidden': 'true', className: 'select-none' }),
      jsx('span', { className: 'min-w-0 break-words whitespace-pre-wrap', children: row.text || ' ' })
    ]
  })
})

/** The log as a two-column timeline: write time left, text right, day dividers. */
function TimestampedLog({ content }) {
  const rows = useMemo(() => buildLogRows(content), [content])

  if (!rows.length) {
    return jsx('div', { className: 'font-mono text-[0.75rem] text-(--ui-text-quaternary)', children: '—' })
  }

  return jsx('div', {
    className: 'grid grid-cols-[auto_minmax(0,1fr)] font-mono text-[0.75rem] leading-[1.6] text-(--ui-text-tertiary)',
    'data-selectable-text': 'true',
    children: rows.map((row, index) =>
      row.kind === 'day'
        ? jsxs(
            'div',
            {
              className:
                'col-span-2 my-1.5 flex items-center gap-2 text-[0.625rem] font-medium text-(--ui-text-quaternary) select-none first:mt-0',
              role: 'separator',
              children: [
                jsx('span', { className: 'h-px flex-1 bg-(--ui-stroke-tertiary)' }),
                row.label,
                jsx('span', { className: 'h-px flex-1 bg-(--ui-stroke-tertiary)' })
              ]
            },
            `day-${index}`
          )
        : jsx(LogLine, { row }, index)
    )
  })
}

/** Pixels from the bottom that still count as "following" the tail. */
const FOLLOW_SLACK_PX = 48

/**
 * The selected task's worker log: fills the remaining height, follows the tail
 * unless the reader scrolled up, and refetches every 3s while the task runs.
 */
function WorkerLogPanel({ task }) {
  const running = task.status === 'running'
  const slug = useValue($boardSlug)
  const scrollRef = useRef(null)
  const followRef = useRef(true)

  const {
    data: log,
    error,
    isLoading
  } = useQuery({
    queryFn: () => fetchTaskLog(task.id),
    queryKey: logKey(slug, task.id),
    refetchInterval: running ? 3_000 : 15_000
  })

  // No "re-arm on task switch" effect: the page keys this panel by task id, so
  // a different task remounts it and `followRef` starts at true again.

  // Layout effect, not effect: scroll before paint so the tail never flickers.
  useLayoutEffect(() => {
    const el = scrollRef.current

    if (el && followRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [log?.content])

  const onScroll = () => {
    const el = scrollRef.current

    if (el) {
      followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLACK_PX
    }
  }

  const meta = []

  if (log?.size_bytes != null) {
    meta.push(formatBytes(log.size_bytes))
  }

  if (log?.truncated) {
    meta.push('gekürzt')
  }

  return jsxs('section', {
    className: 'flex min-h-0 min-w-0 flex-1 flex-col',
    children: [
      jsxs('header', {
        className: 'flex shrink-0 flex-col gap-2 border-b border-(--ui-stroke-tertiary) px-4 pt-3 pb-2.5',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsxs('span', {
                className:
                  'flex items-center gap-1.5 rounded-full bg-(--ui-bg-quaternary) px-2 py-0.5 text-[0.625rem] font-semibold tracking-wide text-(--ui-text-secondary) uppercase',
                children: [jsx(Codicon, { name: 'tasklist', size: '0.75rem' }), 'Worker-Log']
              }),
              jsx('span', {
                className: 'font-mono text-[0.6875rem] text-(--ui-text-quaternary)',
                'data-selectable-text': 'true',
                children: task.id
              }),
              jsxs(Badge, {
                size: 'xs',
                variant: running ? 'default' : 'muted',
                children: [
                  running
                    ? jsx('span', { className: 'mr-1 inline-block size-1.5 animate-pulse rounded-full bg-current' })
                    : null,
                  task.status
                ]
              }),
              meta.length > 0
                ? jsx('span', {
                    className: 'ml-auto text-[0.625rem] text-(--ui-text-quaternary)',
                    children: meta.join(' · ')
                  })
                : null
            ]
          }),
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx('h2', {
                className: 'min-w-0 truncate text-sm font-semibold text-foreground',
                title: task.title,
                children: task.title
              }),
              task.assignee
                ? jsx('span', {
                    className: 'shrink-0 text-[0.75rem] text-(--ui-text-tertiary)',
                    children: task.assignee
                  })
                : null
            ]
          }),
          jsx(TaskContextMeter, { running, taskId: task.id })
        ]
      }),
      jsx('div', {
        className: 'min-h-0 flex-1 overflow-auto px-4 py-3',
        onScroll,
        ref: scrollRef,
        children:
          isLoading && !log
            ? jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader, {}) })
            : error
              ? jsx(ErrorState, { description: errText(error), title: 'Log konnte nicht geladen werden' })
              : log && log.exists === false
                ? jsx(EmptyState, { description: 'Dieser Task hat (noch) keine Log-Datei.', title: 'Kein Log' })
                : jsx(TimestampedLog, { content: log?.content ?? '' })
      })
    ]
  })
}

// ── board controls ───────────────────────────────────────────────────────────

/** The cap field: local text, validated before it ever becomes a request. */
function MaxParallelField({ state }) {
  const qc = useQueryClient()
  const configured = state?.max_in_progress
  const serverText = configured == null ? '' : String(configured)
  const [text, setText] = useState(serverText)
  const [invalid, setInvalid] = useState(null)
  // What the last PUT carried. Enter and the blur that usually follows it are
  // two commits of the same value, and the server hasn't answered in between —
  // without this the second one fires a redundant request.
  const sentRef = useRef(null)

  // Follow the server whenever it moves (another window, a palette command) —
  // but never while the user is mid-edit, hence the `serverText` dependency
  // only: a re-render with an unchanged server value leaves typing alone.
  useEffect(() => {
    setText(serverText)
    setInvalid(null)
    sentRef.current = null
  }, [serverText])

  const save = useMutation({
    mutationFn: value => putMaxParallel(value),
    onError: err => host.notifyError(err, 'Limit konnte nicht gespeichert werden'),
    onSuccess: () => host.notify({ kind: 'info', message: 'Limit gespeichert' }),
    onSettled: () => refreshAll(qc)
  })

  const commit = () => {
    const raw = text.trim()

    // Unchanged (including "still unset") is not an edit — no request, no error.
    if (raw === serverText || raw === sentRef.current) {
      setInvalid(null)

      return
    }

    // Refuse locally: negative, fractional, empty or non-numeric never reaches
    // the backend, so a typo costs no round trip and no error toast.
    if (!/^\d+$/.test(raw)) {
      setInvalid('Bitte eine ganze Zahl ≥ 0 eingeben (0 = unbegrenzt).')

      return
    }

    setInvalid(null)
    sentRef.current = raw
    save.mutate(Number(raw))
  }

  const effective = state?.effective_max_in_progress
  const hint = effective == null ? 'wirksam: unbegrenzt' : `wirksam: max. ${effective} parallel`

  // No own padding: the settings row below lays this out beside the model field.
  return jsxs('div', {
    className: 'flex flex-wrap items-center gap-2 text-[0.75rem]',
    children: [
      jsx('label', {
        className: 'text-(--ui-text-secondary)',
        htmlFor: 'kanban-plus-max',
        children: 'Max. parallele Runs'
      }),
      jsx(Input, {
        'aria-invalid': invalid ? 'true' : undefined,
        className: 'w-20 tabular-nums',
        disabled: save.isPending,
        id: 'kanban-plus-max',
        inputMode: 'numeric',
        onBlur: commit,
        onChange: event => setText(event.target.value),
        onKeyDown: event => {
          if (event.key === 'Enter') {
            event.preventDefault()
            commit()
          }
        },
        placeholder: '0',
        value: text
      }),
      jsx('span', { className: 'text-(--ui-text-quaternary)', children: '0 = unbegrenzt' }),
      jsx('span', { className: 'text-(--ui-text-tertiary)', children: `· ${hint}` }),
      state?.max_in_progress_set === false
        ? jsx('span', { className: 'text-(--ui-text-quaternary)', children: '· nicht gesetzt' })
        : null,
      invalid ? jsx('span', { className: 'text-destructive', role: 'alert', children: invalid }) : null
    ]
  })
}

/** The one amber notice style the page uses, so every "this is not being
 *  enforced" row reads the same wherever it comes from. */
function WarningRow({ children }) {
  return jsxs('div', {
    className:
      'mx-4 mb-1 flex items-start gap-2 rounded-md border-l-2 border-amber-500 bg-amber-500/10 px-3 py-1.5 text-[0.75rem] text-(--ui-text-secondary)',
    role: 'status',
    children: [
      jsx(Codicon, { className: 'mt-px shrink-0 text-amber-500', name: 'warning', size: '0.85rem' }),
      jsx('span', { className: 'min-w-0', children })
    ]
  })
}

/** The guard is what actually enforces stop/start and the cap — say so loudly. */
function GuardWarning({ state }) {
  if (!state || state.guard_active !== false) {
    return null
  }

  return jsx(WarningRow, {
    // English on purpose: this names a Hermes-internal breakage an operator
    // will paste into an issue.
    children:
      'Dispatcher guard inactive — stop/start and the parallel cap are not enforced. A Hermes update changed the dispatcher; update this plugin to restore the guard.'
  })
}

// ── board model ──────────────────────────────────────────────────────────────

const MODEL_INHERIT = 'Modell des Profils'

/** The catalog RPC rides a gateway socket that can wedge (cold pool spawn,
 *  dropped remote hop). Past this budget the query REJECTS, so the picker shows
 *  its retry row instead of a spinner that never ends. */
const MODEL_OPTIONS_SETTLE_MS = 20_000

function boundedFetch(promise, settleMs = MODEL_OPTIONS_SETTLE_MS) {
  // `window.setTimeout`, not the bare global: a bare test harness exposes timers
  // only through the window shim. With no scheduler at all, degrade to the
  // unbounded promise rather than not fetching.
  const scope = typeof window === 'undefined' ? null : window

  if (!scope || typeof scope.setTimeout !== 'function') {
    return promise
  }

  let timerId = null

  const deadline = new Promise((_resolve, reject) => {
    timerId = scope.setTimeout(
      () => reject(new Error(`model.options hat nicht innerhalb von ${Math.round(settleMs / 1000)}s geantwortet`)),
      settleMs
    )
  })

  return Promise.race([promise, deadline]).finally(() => scope.clearTimeout(timerId))
}

/** True when the stored provider names this catalog row. A pinned provider can
 *  legitimately be the slug, the display name, or a custom-provider alias. */
function providerMatches(provider, current) {
  if (!current) {
    return false
  }

  return provider.slug === current || provider.name === current || provider.aliases.includes(current)
}

/**
 * Flatten `model.options` into `[{ slug, name, aliases, models: string[] }]`.
 * Current gateways send `models` as bare slugs, older ones as `{id, name}`
 * objects, so both are normalized here instead of at every read site. Providers
 * with nothing selectable are dropped — an empty group is a dead header.
 */
function normalizeProviders(data) {
  const rows = Array.isArray(data?.providers) ? data.providers : []

  return rows
    .filter(row => row && row.slug)
    .map(row => {
      const models = (Array.isArray(row.models) ? row.models : [])
        .map(entry => (typeof entry === 'string' ? entry : (entry?.id ?? entry?.name ?? '')))
        .filter(Boolean)
      const featured = (Array.isArray(row.featured_models) ? row.featured_models : []).filter(model =>
        models.includes(model)
      )

      return {
        aliases: Array.isArray(row.aliases) ? row.aliases : [],
        // Featured first, the rest in catalog order — the same priority the
        // app's own menu gives them, without reshuffling anything else.
        models: [...featured, ...models.filter(model => !featured.includes(model))],
        name: row.name || row.slug,
        slug: row.slug
      }
    })
    .filter(row => row.models.length > 0)
}

/** The catalog, fetched only once the user reaches for the picker. */
function useModelOptions(enabled) {
  return useQuery({
    enabled,
    queryFn: () => boundedFetch(host.request('model.options', { include_unconfigured: true, explicit_only: false })),
    queryKey: ['kanban-plus', 'model-options'],
    // A wedged catalog must not be retried three times behind the settle guard.
    retry: false,
    staleTime: 120_000
  })
}

/** Compact "the catalog didn't come back" row with a retry — never a spinner. */
function CatalogError({ message, onRetry, pending }) {
  return jsxs('div', {
    className: 'flex items-start gap-2 px-2 py-2 text-[0.6875rem] text-(--ui-text-tertiary)',
    children: [
      jsx(Codicon, { className: 'mt-px shrink-0 text-amber-500', name: 'warning', size: '0.8rem' }),
      jsxs('div', {
        className: 'flex min-w-0 flex-col items-start gap-1',
        children: [
          jsx('span', { className: 'min-w-0', children: message }),
          jsx(Button, {
            disabled: pending,
            onClick: onRetry,
            size: 'xs',
            variant: 'outline',
            children: 'Erneut versuchen'
          })
        ]
      })
    ]
  })
}

/**
 * The board's model: what every dispatched task worker AND the auto-composer's
 * decompose/specify calls run on. Unset means inherit, exactly like the
 * per-task override — so the trigger reads `provider: model` or the inherit
 * copy, and the clear button beside it puts it back.
 */
function BoardModelField({ state }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  // Sticky: once the catalog is asked for it stays cached for this page.
  const [opened, setOpened] = useState(false)
  const [query, setQuery] = useState('')
  const searchRef = useRef(null)

  const { data, error, isFetching, refetch } = useModelOptions(opened)
  const providers = useMemo(() => normalizeProviders(data), [data])

  const current = { model: state?.model ?? '', provider: state?.provider ?? '' }
  const isSet = Boolean(String(current.model).trim())

  const save = useMutation({
    mutationFn: ({ model, provider }) => putBoardModel(model, provider),
    onError: err => host.notifyError(err, 'Board-Modell konnte nicht gesetzt werden'),
    onSuccess: (_result, variables) =>
      host.notify({
        kind: 'info',
        message: variables.model
          ? `Board-Modell: ${modelLabel(variables, MODEL_INHERIT)}`
          : 'Board-Modell zurückgesetzt'
      }),
    onSettled: () => void qc.invalidateQueries({ queryKey: STATE_KEY })
  })

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()

    if (!needle) {
      return providers
    }

    return providers
      .map(provider => {
        // A hit on the provider itself keeps its whole group: typing "openai"
        // should list that provider, not filter its models down to nothing.
        const wholeGroup = provider.slug.toLowerCase().includes(needle) || provider.name.toLowerCase().includes(needle)

        return {
          ...provider,
          models: wholeGroup ? provider.models : provider.models.filter(model => model.toLowerCase().includes(needle))
        }
      })
      .filter(provider => provider.models.length > 0)
  }, [providers, query])

  const pick = (model, provider) => {
    setOpen(false)
    save.mutate({ model, provider })
  }

  const body = error
    ? jsx(CatalogError, {
        message: `Modellkatalog nicht verfügbar: ${errText(error)}`,
        onRetry: () => void refetch(),
        pending: isFetching
      })
    : isFetching && !data
      ? jsx('div', {
          className: 'px-2 py-3 text-[0.6875rem] text-(--ui-text-quaternary)',
          children: 'Modelle werden geladen…'
        })
      : providers.length === 0
        ? jsx(CatalogError, {
            message: 'Der Gateway meldet keine auswählbaren Modelle.',
            onRetry: () => void refetch(),
            pending: isFetching
          })
        : groups.length === 0
          ? jsx('div', {
              className: 'px-2 py-3 text-[0.6875rem] text-(--ui-text-quaternary)',
              children: 'Keine Treffer'
            })
          : jsx(ScrollArea, {
              className: 'max-h-64',
              children: jsx('div', {
                className: 'py-1',
                children: groups.map(group =>
                  jsxs(
                    'div',
                    {
                      children: [
                        jsx('div', {
                          className:
                            'px-2 pt-2 pb-1 text-[0.625rem] font-medium tracking-wide text-(--ui-text-quaternary) uppercase',
                          children: group.name
                        }),
                        ...group.models.map(model => {
                          // A stored provider that is blank still counts as a
                          // match: the backend accepts a bare model id.
                          const selected =
                            model === current.model && (!current.provider || providerMatches(group, current.provider))

                          return jsxs(
                            'button',
                            {
                              className: cn(
                                'flex w-full items-center gap-2 px-2 py-1 text-left text-[0.75rem] transition-colors hover:bg-(--ui-bg-quaternary)',
                                selected ? 'text-primary' : 'text-(--ui-text-secondary)'
                              ),
                              onClick: () => pick(model, group.slug),
                              type: 'button',
                              children: [
                                jsx('span', { className: 'min-w-0 flex-1 truncate', children: model }),
                                selected
                                  ? jsx(Codicon, { className: 'shrink-0', name: 'check', size: '0.75rem' })
                                  : null
                              ]
                            },
                            model
                          )
                        })
                      ]
                    },
                    group.slug
                  )
                )
              })
            })

  return jsxs('div', {
    className: 'flex min-w-0 flex-col gap-1 text-[0.75rem]',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('span', {
            className: 'shrink-0 text-(--ui-text-secondary)',
            id: 'kanban-plus-model-label',
            children: 'Board-Modell'
          }),
          jsxs(Popover, {
            onOpenChange: next => {
              setOpen(next)

              if (next) {
                setOpened(true)
                setQuery('')
              }
            },
            open,
            children: [
              jsx(PopoverTrigger, {
                asChild: true,
                children: jsxs(Button, {
                  'aria-labelledby': 'kanban-plus-model-label',
                  className: cn(
                    'h-7 max-w-64 justify-between gap-2 px-2 font-normal',
                    !isSet && 'text-(--ui-text-tertiary)'
                  ),
                  disabled: save.isPending,
                  type: 'button',
                  variant: 'outline',
                  children: [
                    jsx('span', { className: 'min-w-0 truncate', children: modelLabel(current, MODEL_INHERIT) }),
                    jsx(Codicon, { className: 'shrink-0 opacity-50', name: 'chevron-down', size: '0.7rem' })
                  ]
                })
              }),
              jsx(PopoverContent, {
                align: 'start',
                className: 'w-72 p-0',
                // Radix focuses the content itself by default; take the focus to
                // the search field instead so typing filters straight away.
                onOpenAutoFocus: event => {
                  event.preventDefault()
                  searchRef.current?.focus()
                },
                side: 'bottom',
                children: jsxs('div', {
                  className: 'flex flex-col',
                  children: [
                    jsx('div', {
                      className: 'border-b border-(--ui-stroke-tertiary) px-2 py-1',
                      children: jsx(SearchField, {
                        'aria-label': 'Modelle durchsuchen',
                        containerClassName: 'w-full',
                        inputClassName: 'w-full',
                        inputRef: searchRef,
                        loading: isFetching && !data,
                        onChange: setQuery,
                        placeholder: 'Modell suchen…',
                        value: query
                      })
                    }),
                    body
                  ]
                })
              })
            ]
          }),
          isSet
            ? jsx(Tip, {
                label: 'Board-Modell zurücksetzen',
                children: jsx(Button, {
                  'aria-label': 'Board-Modell zurücksetzen',
                  disabled: save.isPending,
                  onClick: () => save.mutate({ model: '', provider: '' }),
                  size: 'icon-xs',
                  variant: 'ghost',
                  children: jsx(Codicon, { name: 'close', size: '0.7rem' })
                })
              })
            : null
        ]
      }),
      jsx('span', {
        className: 'text-[0.6875rem] text-(--ui-text-quaternary)',
        children:
          'Gilt für Task-Runs und den Auto-Composer (Decompose/Specify). Nicht gesetzt = Modell des jeweiligen Profils.'
      })
    ]
  })
}

/** A pinned model that the gateway does not actually patch in is a lie the page
 *  must not tell. Only an EXPLICIT `false` warns — an older backend omits the
 *  field entirely, and that is not evidence of breakage. */
function ModelPatchWarning({ state }) {
  const patches = state?.model_patches

  if (!patches || !String(state?.model ?? '').trim()) {
    return null
  }

  const spawnOff = patches.spawn === false
  const composerOff = patches.auto_composer === false

  if (!spawnOff && !composerOff) {
    return null
  }

  const message =
    spawnOff && composerOff
      ? 'Board-Modell ist gesetzt, greift aber nirgends: weder Task-Runs noch der Auto-Composer werden gepatcht.'
      : spawnOff
        ? 'Board-Modell greift nicht für Task-Runs (Spawn-Patch inaktiv) — der Auto-Composer nutzt es.'
        : 'Board-Modell greift nicht für den Auto-Composer (Patch inaktiv) — Task-Runs nutzen es.'

  return jsx(WarningRow, { children: message })
}

/** Stop/Start with the confirmation the destructive direction deserves. */
function BoardControls({ state }) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const running = Number(state?.running) || 0

  const stop = useMutation({
    mutationFn: () => stopBoard(),
    onError: err => host.notifyError(err, 'Board konnte nicht gestoppt werden'),
    onSuccess: result => {
      const reclaimed = result?.reclaimed?.length ?? 0

      host.notify({
        kind: 'info',
        message: `Board gestoppt · ${reclaimed} ${reclaimed === 1 ? 'Task' : 'Tasks'} zurück in Ready`
      })

      if (result?.failed?.length) {
        host.notify({ kind: 'warning', message: `Nicht zurückgeholt: ${result.failed.join(', ')}` })
      }
    },
    onSettled: () => refreshAll(qc)
  })

  const start = useMutation({
    mutationFn: () => startBoard(),
    onError: err => host.notifyError(err, 'Board konnte nicht gestartet werden'),
    onSuccess: result => {
      const spawned = result?.spawned?.length ?? 0

      host.notify({ kind: 'info', message: `Board gestartet · ${spawned} Worker gestartet` })
    },
    onSettled: () => refreshAll(qc)
  })

  if (!state) {
    return null
  }

  const pending = stop.isPending || start.isPending

  if (state.stopped) {
    return jsx(Tip, {
      label: 'Dispatch wieder aufnehmen',
      children: jsxs(Button, {
        disabled: pending,
        onClick: () => start.mutate(),
        size: 'sm',
        variant: 'outline',
        children: [
          jsx(Codicon, { name: start.isPending ? 'sync' : 'debug-start', size: '0.8rem', spinning: start.isPending }),
          'Board starten'
        ]
      })
    })
  }

  return jsxs(Fragment, {
    children: [
      jsx(Tip, {
        label: 'Dispatch anhalten und laufende Worker beenden',
        children: jsxs(Button, {
          disabled: pending,
          // Running workers die on a stop, so that direction asks first; an
          // idle board has nothing to lose and stops immediately.
          onClick: () => (running > 0 ? setConfirming(true) : stop.mutate()),
          size: 'sm',
          variant: 'ghost',
          children: [
            jsx(Codicon, { name: stop.isPending ? 'sync' : 'debug-stop', size: '0.8rem', spinning: stop.isPending }),
            'Board stoppen',
            running > 0
              ? jsx('span', {
                  className:
                    'rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] tabular-nums text-(--ui-text-tertiary)',
                  children: running
                })
              : null
          ]
        })
      }),
      jsx(ConfirmDialog, {
        confirmLabel: 'Board stoppen',
        description: `${running} ${running === 1 ? 'laufender Worker wird' : 'laufende Worker werden'} beendet; ${running === 1 ? 'der zugehörige Task geht' : 'die zugehörigen Tasks gehen'} zurück in Ready.`,
        destructive: true,
        onClose: () => setConfirming(false),
        onConfirm: () => {
          setConfirming(false)
          stop.mutate()
        },
        open: confirming,
        title: 'Board wirklich stoppen?'
      })
    ]
  })
}

// ── task list ────────────────────────────────────────────────────────────────

function TaskRow({ now, onSelect, selected, task }) {
  const running = task.status === 'running'
  const startedMs = parseMs(task.started_at)
  const stamp = task.completed_at ?? task.started_at ?? task.created_at

  return jsxs('button', {
    className: cn(
      'flex w-full flex-col gap-1 border-l-2 px-3 py-2 text-left transition-colors',
      selected ? 'border-primary bg-(--ui-bg-quaternary)' : 'border-transparent hover:bg-(--ui-bg-quaternary)/60'
    ),
    onClick: () => onSelect(task.id),
    type: 'button',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-1.5',
        children: [
          running
            ? jsx('span', { className: 'size-1.5 shrink-0 animate-pulse rounded-full bg-primary' })
            : jsx(StatusDot, { className: 'shrink-0', tone: task.status === 'done' ? 'good' : 'muted' }),
          jsx('span', {
            className: 'min-w-0 flex-1 truncate text-[0.75rem] text-foreground',
            title: task.title,
            children: task.title
          }),
          running && startedMs !== null
            ? jsx('span', {
                className: 'shrink-0 text-[0.625rem] tabular-nums text-(--ui-text-tertiary)',
                children: formatElapsed(startedMs, now)
              })
            : null
        ]
      }),
      jsx('div', {
        className: 'flex items-center gap-1.5 pl-3 text-[0.625rem] text-(--ui-text-quaternary)',
        children: [task.status, task.assignee, formatStamp(stamp)].filter(Boolean).join(' · ')
      })
    ]
  })
}

function TaskGroup({ label, now, onSelect, selectedId, tasks }) {
  if (!tasks.length) {
    return null
  }

  return jsxs('div', {
    children: [
      jsx('div', {
        className: 'px-3 pt-3 pb-1 text-[0.625rem] font-medium tracking-wide text-(--ui-text-quaternary) uppercase',
        children: label
      }),
      jsx('div', {
        children: tasks.map(task => jsx(TaskRow, { now, onSelect, selected: task.id === selectedId, task }, task.id))
      })
    ]
  })
}

// ── the board switcher ───────────────────────────────────────────────────────
// The core Kanban page's board switcher, the same dropdown with the same
// actions, projected into the workspace page header while Kanban+ is mounted:
// separate boards are the point, so switching, creating, scoping, transferring
// and archiving one must work from here exactly as it does from the core page.

/** Mirrors `kanban_db.DEFAULT_BOARD` — the board that always exists. */
const DEFAULT_BOARD = 'default'
const NO_PROJECT = '__none__'
const FIELD_LABEL = 'text-[0.62rem] font-semibold uppercase tracking-[0.14em] text-(--ui-text-quaternary)'
const ARCHIVE_FILTERS = [{ extensions: ['tar.gz', 'tgz'], name: 'Hermes-Board' }]

/** The slug `boards create` makes of a display name. */
export function boardSlugFromName(name) {
  return String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/** What the switcher's trigger shows: display name, else slug, else the word. */
export function boardLabel(boards, slug) {
  const current = boards?.boards?.find(meta => meta.slug === (slug || boards.current))

  return current?.name || current?.slug || 'Board'
}

/** Board scope = a first-class Hermes project. Its primary repo becomes the
 *  board's default workspace root; new tasks inherit it as a worktree with a
 *  deterministic branch. "No project" falls back to scratch sandboxes. */
function ProjectPicker({ onChange, value }) {
  const { data } = useQuery({ queryFn: fetchProjects, queryKey: PROJECTS_KEY, staleTime: 30_000 })
  const projects = data?.projects ?? []

  return jsxs('label', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('span', { className: FIELD_LABEL, children: 'Projekt' }),
      jsxs(Select, {
        onValueChange: id => onChange(id === NO_PROJECT ? '' : id),
        value: value || NO_PROJECT,
        children: [
          jsx(SelectTrigger, { children: jsx(SelectValue, {}) }),
          jsxs(SelectContent, {
            children: [
              jsx(SelectItem, { value: NO_PROJECT, children: 'Kein Projekt (Scratch-Sandboxes)' }),
              projects.map(project => jsx(SelectItem, { value: project.id, children: project.name }, project.id))
            ]
          })
        ]
      }),
      jsxs('span', {
        className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
        children: [
          'Das primäre Repo des Projekts wird zum Arbeitsverzeichnis des Boards. Projekte verwaltest du mit ',
          jsx('span', { className: 'font-mono', children: 'hermes project' }),
          '.'
        ]
      })
    ]
  })
}

/** Every board write ends the same way: refresh the switcher's list and let
 *  the caller finish, or surface the error and leave the dialog open. */
function useBoardWrite(mutationFn, onDone) {
  const qc = useQueryClient()

  return useMutation({
    mutationFn,
    onError: err => host.notify({ kind: 'error', message: errText(err) }),
    onSuccess: result => {
      void qc.invalidateQueries({ queryKey: BOARDS_KEY })
      onDone(result)
    }
  })
}

/** Shared chrome for the board dialogs — same width, same Abbrechen/confirm pair. */
function BoardDialog({ children, confirmLabel, disabled, onClose, onConfirm, open, title }) {
  return jsx(Dialog, {
    onOpenChange: next => !next && onClose(),
    open,
    children: jsxs(DialogContent, {
      className: 'max-w-md',
      children: [
        jsx(DialogHeader, { children: jsx(DialogTitle, { children: title }) }),
        jsx('div', { className: 'flex flex-col gap-3', children }),
        jsxs(DialogFooter, {
          children: [
            jsx(Button, { onClick: onClose, variant: 'text', children: 'Abbrechen' }),
            jsx(Button, { disabled, onClick: onConfirm, children: confirmLabel })
          ]
        })
      ]
    })
  })
}

/** Display name, with the slug it maps to shown underneath. */
function BoardNameField({ onChange, onEnter, slug, value }) {
  return jsxs('label', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('span', { className: FIELD_LABEL, children: 'Name' }),
      jsx(Input, {
        autoFocus: true,
        onChange: event => onChange(event.target.value),
        onKeyDown: event => isSubmitEnter(event) && onEnter(),
        placeholder: 'Board-Name',
        value
      }),
      slug ? jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: `Slug: ${slug}` }) : null
    ]
  })
}

function NewBoardDialog({ onClose, open }) {
  const [name, setName] = useState('')
  const [project, setProject] = useState('')
  const slug = boardSlugFromName(name)

  useEffect(() => {
    if (open) {
      setName('')
      setProject('')
    }
  }, [open])

  const create = useBoardWrite(
    () => createBoard(slug, name.trim(), project || undefined),
    result => {
      $boardSlug.set(result.board.slug)
      onClose()
    }
  )

  return jsxs(BoardDialog, {
    confirmLabel: 'Board erstellen',
    disabled: !slug || create.isPending,
    onClose,
    onConfirm: () => create.mutate(),
    open,
    title: 'Neues Board',
    children: [
      // Enter submits only while the scope is untouched — once a project is
      // picked the choice is worth a deliberate click.
      jsx(BoardNameField, {
        onChange: setName,
        onEnter: () => slug && !project && create.mutate(),
        slug,
        value: name
      }),
      jsx(ProjectPicker, { onChange: setProject, value: project })
    ]
  })
}

/** Name-only edit, the way projects rename. The slug is immutable — it is the
 *  board's directory name — so this touches the display name alone. */
function RenameBoardDialog({ board, onClose }) {
  const [name, setName] = useState('')
  // The dialog stays mounted while closed, so `board` is null most of the
  // time; resolve the slug here rather than inside the mutation callback.
  const slug = board?.slug ?? ''

  useEffect(() => {
    if (board) {
      setName(board.name || board.slug)
    }
  }, [board])

  const save = useBoardWrite(() => updateBoard(slug, { name: name.trim() }), onClose)
  const disabled = !name.trim() || save.isPending

  return jsx(BoardDialog, {
    confirmLabel: 'Speichern',
    disabled,
    onClose,
    onConfirm: () => save.mutate(),
    open: Boolean(board),
    title: 'Board umbenennen',
    children: jsx(BoardNameField, {
      onChange: setName,
      onEnter: () => !disabled && save.mutate(),
      slug,
      value: name
    })
  })
}

function BoardSettingsDialog({ board, onClose }) {
  const [project, setProject] = useState('')
  // Null while closed — see RenameBoardDialog.
  const slug = board?.slug ?? ''

  useEffect(() => {
    if (board) {
      setProject(board.project_id || '')
    }
  }, [board])

  // The name lives in the rename dialog; '' clears the scope, which also drops
  // the mirrored default_workdir on the backend.
  const save = useBoardWrite(() => updateBoard(slug, { project_id: project }), onClose)

  return jsx(BoardDialog, {
    confirmLabel: 'Speichern',
    disabled: save.isPending,
    onClose,
    onConfirm: () => save.mutate(),
    open: Boolean(board),
    title: board ? `Board-Einstellungen — ${board.name || board.slug}` : 'Einstellungen…',
    children: jsx(ProjectPicker, { onChange: setProject, value: project })
  })
}

// Board transfer exchanges filesystem paths, not bytes: the picker runs on the
// machine hosting the backend, so the backend reads and writes the archive.

async function runExportBoardFlow(os, slug) {
  const output = await os.pickSavePath({
    defaultPath: `${slug}.tar.gz`,
    filters: ARCHIVE_FILTERS,
    title: 'Board exportieren…'
  })

  if (!output) {
    return null
  }

  try {
    const result = await exportBoard(slug, output)

    host.notify({ kind: 'success', message: `Board nach ${result.archive} exportiert` })

    return result.archive
  } catch (error) {
    host.notify({ kind: 'error', message: errText(error) })

    return null
  }
}

async function runImportBoardFlow(os) {
  const archive = await os.pickOpenPath({ filters: ARCHIVE_FILTERS, title: 'Board importieren…' })

  if (!archive) {
    return null
  }

  try {
    const result = await importBoard(archive)

    host.notify({ kind: 'success', message: `${result.name} importiert` })

    // The slug auto-suffixes on collision, and warnings cover tasks parked for
    // an unresolvable workspace — both change what the user sees on the board
    // they just opened, so neither is allowed to pass silently.
    if (result.renamed) {
      host.notify({ kind: 'info', message: `Name war vergeben — als ${result.board} importiert` })
    }

    for (const warning of result.warnings ?? []) {
      host.notify({ kind: 'warning', message: warning })
    }

    return result.board
  } catch (error) {
    host.notify({ kind: 'error', message: errText(error) })

    return null
  }
}

function BoardSwitcher() {
  const qc = useQueryClient()
  const slug = useValue($boardSlug)
  const { data: boards } = useQuery({ queryFn: fetchBoards, queryKey: BOARDS_KEY, staleTime: 30_000 })
  const [adding, setAdding] = useState(false)
  const [settingsFor, setSettingsFor] = useState(null)
  const [renameFor, setRenameFor] = useState(null)
  const [deleteFor, setDeleteFor] = useState(null)

  // Archive rather than erase, so a mis-click stays recoverable. The backend
  // reverts the active board to default; drop our override to follow it.
  const confirmDelete = async target => {
    const { result } = await deleteBoard(target.slug)

    $boardSlug.set('')
    void qc.invalidateQueries({ queryKey: BOARDS_KEY })
    host.notify({ kind: 'success', message: `Board nach ${result.new_path} archiviert` })
  }

  const runExport = async target => {
    const os = pluginOs()

    if (os) {
      await runExportBoardFlow(os, target)
    }
  }

  const runImport = async () => {
    const os = pluginOs()

    if (!os) {
      return
    }

    const imported = await runImportBoardFlow(os)

    if (imported) {
      $boardSlug.set(imported)
      void qc.invalidateQueries({ queryKey: BOARDS_KEY })
    }
  }

  if (!boards) {
    return null
  }

  const currentSlug = slug || boards.current
  const current = boards.boards.find(meta => meta.slug === currentSlug)
  const label = boardLabel(boards, slug)

  return jsxs(Fragment, {
    children: [
      jsxs(DropdownMenu, {
        children: [
          jsx(Tip, {
            label: 'Board wechseln',
            children: jsx(DropdownMenuTrigger, {
              asChild: true,
              children: jsxs(Button, {
                'aria-label': `Board: ${label}`,
                className: 'h-full min-w-0 max-w-full gap-1.5 px-2',
                size: 'sm',
                variant: 'ghost',
                children: [
                  jsx(Codicon, { className: 'shrink-0 text-(--ui-text-tertiary)', name: 'project', size: '0.8125rem' }),
                  jsx('span', {
                    className: 'shrink-0 text-[0.6875rem] font-medium text-(--ui-text-tertiary)',
                    children: 'Board'
                  }),
                  jsx('span', { className: 'min-w-0 flex-1 truncate text-[0.75rem] font-medium leading-none', children: label }),
                  typeof current?.total === 'number'
                    ? jsx('span', {
                        className: 'text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)',
                        children: current.total
                      })
                    : null,
                  jsx(Codicon, { className: 'shrink-0 text-(--ui-text-tertiary)', name: 'chevron-down', size: '0.8125rem' })
                ]
              })
            })
          }),
          jsxs(DropdownMenuContent, {
            align: 'center',
            children: [
              boards.boards.map(meta =>
                jsxs(
                  DropdownMenuItem,
                  {
                    onSelect: () => $boardSlug.set(meta.slug === boards.current ? '' : meta.slug),
                    children: [
                      // This plugin's own addition to the core switcher: a
                      // board whose dispatch is stopped says so in the list.
                      meta.stopped ? jsx(StatusDot, { tone: 'bad' }) : null,
                      meta.name || meta.slug,
                      typeof meta.total === 'number'
                        ? jsx('span', {
                            className: 'text-[0.625rem] tabular-nums text-(--ui-text-quaternary)',
                            children: meta.total
                          })
                        : null,
                      meta.slug === currentSlug ? jsx(Codicon, { className: 'ml-auto', name: 'check', size: '0.8rem' }) : null
                    ]
                  },
                  meta.slug
                )
              ),
              jsx(DropdownMenuSeparator, {}),
              current
                ? jsxs(Fragment, {
                    children: [
                      jsxs(DropdownMenuItem, {
                        onSelect: () => setRenameFor(current),
                        children: [jsx(Codicon, { name: 'edit', size: '0.8rem' }), 'Umbenennen…']
                      }),
                      jsxs(DropdownMenuItem, {
                        onSelect: () => setSettingsFor(current),
                        children: [jsx(Codicon, { name: 'settings-gear', size: '0.8rem' }), 'Einstellungen…']
                      })
                    ]
                  })
                : null,
              jsxs(DropdownMenuItem, {
                onSelect: () => setAdding(true),
                children: [jsx(Codicon, { name: 'add', size: '0.8rem' }), 'Neues Board…']
              }),
              jsx(DropdownMenuSeparator, {}),
              current
                ? jsxs(DropdownMenuItem, {
                    onSelect: () => void runExport(current.slug),
                    children: [jsx(Codicon, { name: 'package', size: '0.8rem' }), 'Exportieren…']
                  })
                : null,
              jsxs(DropdownMenuItem, {
                onSelect: () => void runImport(),
                children: [jsx(Codicon, { name: 'cloud-download', size: '0.8rem' }), 'Importieren…']
              }),
              // `default` is the fallback every board reverts to — the backend
              // refuses to remove it, so it never offers the action.
              current && current.slug !== DEFAULT_BOARD
                ? jsxs(Fragment, {
                    children: [
                      jsx(DropdownMenuSeparator, {}),
                      jsxs(DropdownMenuItem, {
                        onSelect: () => setDeleteFor(current),
                        variant: 'destructive',
                        children: [jsx(Codicon, { name: 'trash', size: '0.8rem' }), 'Löschen']
                      })
                    ]
                  })
                : null
            ]
          })
        ]
      }),
      jsx(NewBoardDialog, { onClose: () => setAdding(false), open: adding }),
      jsx(RenameBoardDialog, { board: renameFor, onClose: () => setRenameFor(null) }),
      jsx(BoardSettingsDialog, { board: settingsFor, onClose: () => setSettingsFor(null) }),
      jsx(ConfirmDialog, {
        confirmLabel: 'Löschen',
        description:
          'Das Board wird archiviert, nicht gelöscht — Tasks und Anhänge bleiben auf der Platte und lassen sich wiederherstellen.',
        destructive: true,
        onClose: () => setDeleteFor(null),
        onConfirm: () => confirmDelete(deleteFor),
        open: Boolean(deleteFor),
        title: deleteFor ? `„${deleteFor.name || deleteFor.slug}" löschen?` : 'Löschen'
      })
    ]
  })
}

// ── the board columns ────────────────────────────────────────────────────────
// The lanes core's Kanban page draws, in core's order and with its icons and
// tones (`COLUMN_META` in the core plugin's types.ts). Kanban+ is a board page:
// a card has to be visible HERE, not only in the running/recent list beside the
// log. Cards select the task the log and the context meter below them show.

const COLUMN_META = {
  triage: { codicon: 'inbox', label: 'Triage', tone: 'var(--ui-text-tertiary)' },
  todo: { codicon: 'circle-outline', label: 'Todo', tone: 'var(--ui-text-secondary)' },
  scheduled: { codicon: 'watch', label: 'Geplant', tone: '#a78bfa' },
  ready: { codicon: 'play-circle', label: 'Ready', tone: '#60a5fa' },
  running: { codicon: 'sync', label: 'Läuft', tone: '#34d399' },
  blocked: { codicon: 'error', label: 'Blockiert', tone: '#f87171' },
  review: { codicon: 'eye', label: 'Review', tone: '#fbbf24' },
  done: { codicon: 'pass', label: 'Fertig', tone: 'var(--ui-text-tertiary)' },
  archived: { codicon: 'archive', label: 'Archiviert', tone: 'var(--ui-text-quaternary)' }
}

/** Icon, label and tone for a lane; an unknown status keeps its own id. */
export function columnMeta(name) {
  return COLUMN_META[name] ?? { codicon: 'circle-outline', label: String(name ?? ''), tone: 'var(--ui-text-secondary)' }
}

function BoardCard({ now, onSelect, selected, task }) {
  const running = task.status === 'running'
  const startedMs = parseMs(task.started_at)
  const meta = []

  if (task.assignee) {
    meta.push(task.assignee)
  }

  if (running && startedMs !== null) {
    meta.push(formatElapsed(startedMs, now))
  }

  if (task.progress?.total) {
    meta.push(`${task.progress.done}/${task.progress.total}`)
  }

  if (task.comment_count > 0) {
    meta.push(`${task.comment_count} ${task.comment_count === 1 ? 'Kommentar' : 'Kommentare'}`)
  }

  return jsxs('button', {
    className: cn(
      'flex w-full flex-col gap-1 rounded-md border px-2 py-1.5 text-left transition-colors',
      selected
        ? 'border-(--color-primary) bg-(--ui-bg-tertiary)'
        : 'border-(--ui-stroke-tertiary) hover:bg-(--ui-bg-tertiary)'
    ),
    onClick: () => onSelect(task.id),
    title: task.title,
    type: 'button',
    children: [
      jsx('span', { className: 'line-clamp-2 text-[0.75rem] leading-snug text-foreground', children: task.title }),
      meta.length > 0
        ? jsx('span', {
            className: 'truncate text-[0.625rem] tabular-nums text-(--ui-text-quaternary)',
            children: meta.join(' · ')
          })
        : null
    ]
  })
}

function BoardColumn({ column, now, onSelect, selectedId }) {
  const meta = columnMeta(column.name)
  const tasks = column.tasks ?? []

  return jsxs('section', {
    className: 'flex h-full w-56 shrink-0 flex-col gap-1.5',
    children: [
      jsxs('div', {
        className: 'flex shrink-0 items-center gap-1.5 px-0.5',
        children: [
          // The tone rides on the wrapper: codicons are glyphs and take
          // `currentColor`, so this colors the icon without a style prop on it.
          jsx('span', {
            className: 'inline-flex',
            style: { color: meta.tone },
            children: jsx(Codicon, { name: meta.codicon, size: '0.8rem' })
          }),
          jsx('span', {
            className: 'text-[0.6875rem] font-semibold tracking-wide text-(--ui-text-secondary) uppercase',
            children: meta.label
          }),
          jsx('span', {
            className:
              'rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] tabular-nums text-(--ui-text-tertiary)',
            children: tasks.length
          })
        ]
      }),
      jsx('div', {
        className: 'flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto pb-2',
        children:
          tasks.length === 0
            ? jsx('span', { className: 'px-0.5 text-[0.6875rem] text-(--ui-text-quaternary)', children: '—' })
            : tasks.map(task =>
                jsx(BoardCard, { now, onSelect, selected: task.id === selectedId, task }, task.id)
              )
      })
    ]
  })
}

/** The whole board, one lane per status, scrolling sideways when it is wider
 *  than the page. The page owns the query — it needs the same cards to resolve
 *  a selection the running/recent list never carried. */
function BoardColumns({ board, error, isLoading, now, onSelect, selectedId }) {
  if (error) {
    return jsx('div', {
      className: 'grid h-full place-items-center p-4',
      children: jsx(ErrorState, { description: errText(error), title: 'Board konnte nicht geladen werden' })
    })
  }

  if (isLoading && !board) {
    return jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader, {}) })
  }

  const columns = board?.columns ?? []

  if (columns.every(column => (column.tasks?.length ?? 0) === 0)) {
    return jsx('div', {
      className: 'grid h-full place-items-center p-4',
      children: jsx(EmptyState, {
        description: 'Dieses Board hat noch keine Tasks. Lege einen an — auf der Kanban-Seite oder mit `hermes kanban add`.',
        title: 'Leeres Board'
      })
    })
  }

  return jsx('div', {
    className: 'flex h-full min-h-0 gap-3 overflow-x-auto px-4 py-3',
    'data-slot': 'kanban-plus-columns',
    children: columns.map(column =>
      jsx(BoardColumn, { column, now, onSelect, selectedId }, column.name)
    )
  })
}

// ── the page ─────────────────────────────────────────────────────────────────

function KanbanPlusPage() {
  const slug = useValue($boardSlug)
  const [selectedId, setSelectedId] = useState(() => readSelectedTask($boardSlug.get()))
  // Which board already got its one automatic "land on a running task".
  const autoLanded = useRef('')

  // A task id belongs to the board it came from, so a switch re-reads THAT
  // board's remembered selection instead of pointing at a task it never had.
  useEffect(() => setSelectedId(readSelectedTask(slug)), [slug])

  const {
    data: state,
    error: stateError,
    isLoading: stateLoading
  } = useQuery({
    queryFn: fetchState,
    queryKey: stateKey(slug),
    refetchInterval: 10_000
  })

  const { data: tasks } = useQuery({
    queryFn: fetchTasks,
    queryKey: tasksKey(slug),
    refetchInterval: 5_000
  })

  const {
    data: board,
    error: boardError,
    isLoading: boardLoading
  } = useQuery({
    queryFn: fetchBoard,
    queryKey: boardKey(slug),
    refetchInterval: 5_000
  })

  const running = useMemo(() => tasks?.running ?? [], [tasks])
  const recent = useMemo(() => tasks?.recent ?? [], [tasks])

  // One ticker for the whole list: N running rows must not mean N intervals.
  useTicker(running.length > 0, 1_000)

  const now = Date.now()

  // A card can sit in a lane the running/recent list does not reach (an old
  // Todo, say), and picking one still has to open its log.
  const selected = useMemo(() => {
    const cards = (board?.columns ?? []).flatMap(column => column.tasks ?? [])

    return [...running, ...recent, ...cards].find(task => task.id === selectedId) ?? null
  }, [board, recent, running, selectedId])

  const select = id => {
    setSelectedId(id)
    writeSelectedTask(slug, id)
  }

  // Nothing resolved (first open, or a remembered id whose task was pruned) and
  // something IS running: land on it rather than leaving the panel on a dead
  // selection. Only runs once the first task payload arrived — before that an
  // empty `running` says nothing about whether the id is still valid. Once per
  // board, so closing the log keeps the lanes full height instead of having the
  // next poll re-open it.
  useEffect(() => {
    if (tasks && !selected && running.length > 0 && autoLanded.current !== slug) {
      autoLanded.current = slug
      select(running[0].id)
    }
    // `select` is stable enough (it only wraps two setters) to leave out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, selected, slug, tasks])

  if (stateError) {
    return jsx('div', {
      className: 'grid h-full place-items-center p-6',
      children: jsx(ErrorState, { description: errText(stateError), title: 'Kanban+ Backend nicht erreichbar' })
    })
  }

  if (stateLoading && !state) {
    return jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader, {}) })
  }

  const stopped = Boolean(state?.stopped)
  const workers = Number(state?.running) || 0
  const since = formatStamp(state?.stopped_at)

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col bg-(--ui-surface-background)',
    'data-slot': 'kanban-plus-page',
    children: [
      // Page-owned header chrome: exists exactly while this page is mounted.
      jsx(Contribute, {
        area: WORKSPACE_PAGE_HEADER_AREA,
        id: 'kanban-plus:board-switcher',
        children: jsx(BoardSwitcher, {})
      }),
      jsxs('header', {
        className: 'flex shrink-0 flex-wrap items-center gap-2 px-4 pt-3 pb-2',
        children: [
          jsx('h1', { className: 'text-sm font-semibold text-foreground', children: 'Kanban+' }),
          jsxs(Badge, {
            size: 'xs',
            variant: stopped ? 'destructive' : 'success',
            children: [
              jsx(StatusDot, { className: 'mr-1', tone: stopped ? 'bad' : 'good' }),
              stopped ? 'gestoppt' : 'läuft'
            ]
          }),
          stopped && state?.reason
            ? jsx('span', {
                className: 'max-w-[18rem] truncate text-[0.6875rem] text-(--ui-text-tertiary)',
                title: state.reason,
                children: state.reason
              })
            : null,
          stopped && since
            ? jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-quaternary)', children: `seit ${since}` })
            : null,
          jsx('span', {
            className: 'text-[0.6875rem] tabular-nums text-(--ui-text-tertiary)',
            children: `${workers} Worker aktiv`
          }),
          jsx('div', { className: 'ml-auto flex items-center gap-1', children: jsx(BoardControls, { state }) })
        ]
      }),
      jsx(GuardWarning, { state }),
      jsx(ModelPatchWarning, { state }),
      // Both board settings share one row: side by side when there is room,
      // stacked when the window is narrow.
      jsxs('div', {
        className: 'flex shrink-0 flex-wrap items-start gap-x-8 gap-y-2 px-4 py-2',
        children: [jsx(MaxParallelField, { state }), jsx(BoardModelField, { state })]
      }),
      jsxs('div', {
        className: 'flex min-h-0 flex-1 border-t border-(--ui-stroke-tertiary)',
        children: [
          jsx('aside', {
            className: 'w-72 shrink-0 overflow-y-auto border-r border-(--ui-stroke-tertiary)',
            children:
              running.length === 0 && recent.length === 0
                ? jsx(EmptyState, { description: 'Weder laufende noch kürzlich beendete Tasks.', title: 'Keine Tasks' })
                : jsxs(Fragment, {
                    children: [
                      jsx(TaskGroup, { label: 'Laufend', now, onSelect: select, selectedId, tasks: running }),
                      jsx(TaskGroup, { label: 'Zuletzt', now, onSelect: select, selectedId, tasks: recent })
                    ]
                  })
          }),
          // The lanes own the page; the log opens underneath them for the card
          // (or list row) the reader picked, and gives the space back on close.
          jsxs('div', {
            className: 'flex min-h-0 min-w-0 flex-1 flex-col',
            children: [
              jsx('div', {
                className: 'min-h-0 flex-1',
                children: jsx(BoardColumns, {
                  board,
                  error: boardError,
                  isLoading: boardLoading,
                  now,
                  onSelect: select,
                  selectedId
                })
              }),
              selected
                ? jsxs('div', {
                    className: 'flex h-2/5 min-h-0 shrink-0 flex-col border-t border-(--ui-stroke-tertiary)',
                    children: [
                      jsx(WorkerLogPanel, { task: selected }, selected.id),
                      jsx('div', {
                        className: 'shrink-0 border-t border-(--ui-stroke-tertiary) px-4 py-1 text-right',
                        children: jsx(Button, {
                          onClick: () => select(null),
                          size: 'sm',
                          variant: 'text',
                          children: 'Log schließen'
                        })
                      })
                    ]
                  })
                : null
            ]
          })
        ]
      })
    ]
  })
}

// ── statusbar ────────────────────────────────────────────────────────────────

function StatusbarPill() {
  const qc = useQueryClient()
  const slug = useValue($boardSlug)

  const { data: state } = useQuery({
    queryFn: fetchState,
    queryKey: stateKey(slug),
    refetchInterval: 15_000
  })

  if (!state) {
    return null
  }

  const stopped = Boolean(state.stopped)
  const workers = Number(state.running) || 0

  // The menu is the compact twin of the page's controls: no confirmation here,
  // because the pill's own label already says how many workers a stop ends.
  const run = (action, done, fallback) =>
    action()
      .then(result => host.notify({ kind: 'info', message: done(result) }))
      .catch(err => host.notifyError(err, fallback))
      .finally(() => refreshAll(qc))

  const onStop = () =>
    void run(
      () => stopBoard(),
      result => {
        const reclaimed = result?.reclaimed?.length ?? 0

        return `Board gestoppt · ${reclaimed} ${reclaimed === 1 ? 'Task' : 'Tasks'} zurück in Ready`
      },
      'Board konnte nicht gestoppt werden'
    )

  const onStart = () =>
    void run(
      startBoard,
      result => `Board gestartet · ${result?.spawned?.length ?? 0} Worker gestartet`,
      'Board konnte nicht gestartet werden'
    )

  return jsxs(DropdownMenu, {
    children: [
      jsx(DropdownMenuTrigger, {
        asChild: true,
        children: jsx(Tip, {
          label: stopped ? 'Kanban-Board ist gestoppt' : `Kanban-Board läuft · ${workers} Worker`,
          children: jsxs('button', {
            className: cn(
              'inline-flex h-full items-center gap-1 rounded-none px-1.5 text-[0.6875rem] tabular-nums transition-colors',
              'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
            ),
            type: 'button',
            children: [
              jsx(StatusDot, { tone: stopped ? 'bad' : 'good' }),
              jsx('span', { children: `Kanban: ${stopped ? 'gestoppt' : 'läuft'} · ${workers}` })
            ]
          })
        })
      }),
      jsxs(DropdownMenuContent, {
        align: 'end',
        children: [
          stopped
            ? jsx(DropdownMenuItem, { onSelect: onStart, children: 'Board starten' })
            : jsx(DropdownMenuItem, { onSelect: onStop, children: 'Board stoppen' }),
          jsx(DropdownMenuSeparator, {}),
          jsx(DropdownMenuItem, { onSelect: () => host.navigate('/kanban-plus'), children: 'Kanban+ öffnen' })
        ]
      })
    ]
  })
}

// ── composer strip ───────────────────────────────────────────────────────────

/**
 * A 3px context-fill bar above the composer for the FOCUSED session.
 *
 * `focusedUsage` is streamed by the backend and needs no RPC, so it owns the
 * headline numbers whenever it carries them. It has no categories, though —
 * those only exist in the breakdown — so the RPC runs when there are no usage
 * numbers at all, or once the reader hovers and actually wants the split.
 */
function ComposerContextStrip() {
  const sessionId = useValue(host.state.focusedSessionId)
  const usage = useValue(host.state.focusedUsage)
  const [hovered, setHovered] = useState(false)

  const hasUsage = Boolean(usage?.context_used && usage?.context_max)

  // Sticky within a session (one hover keeps the split fresh while the reader
  // works), but a session switch drops back to the no-RPC path.
  useEffect(() => setHovered(false), [sessionId])

  const { data: breakdown } = useQuery({
    enabled: Boolean(sessionId) && (!hasUsage || hovered),
    queryFn: () => host.request('session.context_breakdown', { session_id: sessionId }),
    queryKey: ['kanban-plus', 'session-context', sessionId ?? ''],
    refetchInterval: 30_000
  })

  const snapshot = useMemo(() => {
    if (hasUsage) {
      return {
        categories: breakdown?.categories ?? [],
        context_max: usage.context_max,
        context_percent: usage.context_percent ?? (usage.context_used / usage.context_max) * 100,
        context_used: usage.context_used
      }
    }

    if (breakdown?.context_max) {
      return breakdown
    }

    return null
  }, [breakdown, hasUsage, usage])

  // No session, no numbers — the strip is invisible rather than empty chrome.
  if (!sessionId || !snapshot) {
    return null
  }

  return jsx('div', {
    className: 'px-3 pb-1',
    onPointerEnter: () => setHovered(true),
    children: jsx(ContextBar, {
      align: 'end',
      header: breakdown?.model ?? 'Session-Kontext',
      showLabel: false,
      side: 'top',
      snapshot
    })
  })
}

// ── registration ─────────────────────────────────────────────────────────────

/** Palette rows run outside React, so they invalidate the shared client. */
function paletteStop() {
  stopBoard()
    .then(result => {
      const reclaimed = result?.reclaimed?.length ?? 0

      host.notify({
        kind: 'info',
        message: `Board gestoppt · ${reclaimed} ${reclaimed === 1 ? 'Task' : 'Tasks'} zurück in Ready`
      })
    })
    .catch(err => host.notifyError(err, 'Board konnte nicht gestoppt werden'))
    .finally(() => refreshAll())
}

function paletteStart() {
  startBoard()
    .then(result =>
      host.notify({ kind: 'info', message: `Board gestartet · ${result?.spawned?.length ?? 0} Worker gestartet` })
    )
    .catch(err => host.notifyError(err, 'Board konnte nicht gestartet werden'))
    .finally(() => refreshAll())
}

const plugin = {
  id: 'kanban-enhancements',
  name: 'Kanban+',
  description:
    'Board-Kontrolle für Kanban: Stop/Start, Limit für parallele Runs, Worker-Log mit Zeitspalte und Kontext-Anzeige.',
  register(ctx) {
    restCall = ctx.rest
    osDoor = ctx.os

    // The picked board is a setting, not a session detail: hydrate it from the
    // plugin's own storage and keep that storage in sync with the atom.
    const storage = ctx.storage

    $boardSlug.set(storage?.get(BOARD_SLUG_KEY, '') ?? '')

    const unlisten = storage ? $boardSlug.listen(value => storage.set(BOARD_SLUG_KEY, value)) : () => {}

    // Disk plugins hot-reload on every save; drop the doors so a stale closure
    // can never keep calling through a context that was torn down.
    ctx.onDispose(() => {
      unlisten()
      restCall = null
      osDoor = null
    })

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        title: 'Kanban+',
        data: { path: '/kanban-plus' },
        render: () => jsx(KanbanPlusPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'tasklist', label: 'Kanban+', path: '/kanban-plus' }
      },
      {
        id: 'statusbar',
        area: STATUSBAR_AREAS.right,
        order: 78,
        render: () => jsx(StatusbarPill, {})
      },
      {
        id: 'composer-context',
        area: COMPOSER_AREAS.top,
        order: 20,
        render: () => jsx(ComposerContextStrip, {})
      },
      {
        id: 'palette-stop',
        area: PALETTE_AREA,
        data: {
          id: 'kanban-plus.stop',
          label: 'Kanban: Board stoppen',
          keywords: ['kanban', 'board', 'stop', 'stoppen', 'anhalten', 'worker'],
          run: paletteStop
        }
      },
      {
        id: 'palette-start',
        area: PALETTE_AREA,
        data: {
          id: 'kanban-plus.start',
          label: 'Kanban: Board starten',
          keywords: ['kanban', 'board', 'start', 'starten', 'dispatch'],
          run: paletteStart
        }
      },
      {
        id: 'palette-open',
        area: PALETTE_AREA,
        data: {
          id: 'kanban-plus.open',
          label: 'Kanban: Board-Seite öffnen',
          keywords: ['kanban', 'board', 'seite', 'open', 'öffnen', 'log'],
          run: () => host.navigate('/kanban-plus')
        }
      }
    ])
  }
}

export default plugin
