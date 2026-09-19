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
  StatusDot,
  Tip,
  cn,
  compactNumber,
  host,
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

function api(path, opts) {
  if (!restCall) {
    return Promise.reject(new Error('Kanban+ ist nicht geladen.'))
  }

  return restCall(path, opts)
}

const STATE_KEY = ['kanban-plus', 'state']
const TASKS_KEY = ['kanban-plus', 'tasks']
const logKey = id => ['kanban-plus', 'log', id]
const contextKey = id => ['kanban-plus', 'context', id]

const fetchState = () => api('/state')
const fetchTasks = () => api('/tasks')
const stopBoard = reason => api('/stop', { method: 'POST', body: reason ? { reason } : {} })
const startBoard = () => api('/start', { method: 'POST' })
const putMaxParallel = value => api('/max-parallel', { method: 'PUT', body: { value } })
const putBoardModel = (model, provider) => api('/model', { method: 'PUT', body: { model, provider } })
const fetchTaskLog = id => api(`/tasks/${encodeURIComponent(id)}/log?tail=1048576&timestamps=true`)
const fetchTaskContext = id => api(`/tasks/${encodeURIComponent(id)}/context`)

/** Invalidate everything a stop/start can have moved — usable outside React. */
function refreshAll(qc) {
  const client = qc ?? queryClient

  void client.invalidateQueries({ queryKey: STATE_KEY })
  void client.invalidateQueries({ queryKey: TASKS_KEY })
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

// localStorage throws in a locked-down renderer; a remembered selection is a
// convenience, never a correctness requirement.
function readSelectedTask() {
  try {
    return localStorage.getItem(SELECTED_TASK_KEY)
  } catch {
    return null
  }
}

function writeSelectedTask(id) {
  try {
    if (id) {
      localStorage.setItem(SELECTED_TASK_KEY, id)
    } else {
      localStorage.removeItem(SELECTED_TASK_KEY)
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
  const { data } = useQuery({
    enabled: Boolean(taskId),
    queryFn: () => fetchTaskContext(taskId),
    queryKey: contextKey(taskId ?? ''),
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
  const scrollRef = useRef(null)
  const followRef = useRef(true)

  const {
    data: log,
    error,
    isLoading
  } = useQuery({
    queryFn: () => fetchTaskLog(task.id),
    queryKey: logKey(task.id),
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
            ? jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader) })
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

// ── the page ─────────────────────────────────────────────────────────────────

function KanbanPlusPage() {
  const [selectedId, setSelectedId] = useState(() => readSelectedTask())

  const {
    data: state,
    error: stateError,
    isLoading: stateLoading
  } = useQuery({
    queryFn: fetchState,
    queryKey: STATE_KEY,
    refetchInterval: 10_000
  })

  const { data: tasks } = useQuery({
    queryFn: fetchTasks,
    queryKey: TASKS_KEY,
    refetchInterval: 5_000
  })

  const running = useMemo(() => tasks?.running ?? [], [tasks])
  const recent = useMemo(() => tasks?.recent ?? [], [tasks])

  // One ticker for the whole list: N running rows must not mean N intervals.
  useTicker(running.length > 0, 1_000)

  const now = Date.now()

  const selected = useMemo(
    () => [...running, ...recent].find(task => task.id === selectedId) ?? null,
    [recent, running, selectedId]
  )

  const select = id => {
    setSelectedId(id)
    writeSelectedTask(id)
  }

  // Nothing resolved (first open, or a remembered id whose task was pruned) and
  // something IS running: land on it rather than leaving the panel on a dead
  // selection. Only runs once the first task payload arrived — before that an
  // empty `running` says nothing about whether the id is still valid.
  useEffect(() => {
    if (tasks && !selected && running.length > 0) {
      select(running[0].id)
    }
    // `select` is stable enough (it only wraps two setters) to leave out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, selected, tasks])

  if (stateError) {
    return jsx('div', {
      className: 'grid h-full place-items-center p-6',
      children: jsx(ErrorState, { description: errText(stateError), title: 'Kanban+ Backend nicht erreichbar' })
    })
  }

  if (stateLoading && !state) {
    return jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader) })
  }

  const stopped = Boolean(state?.stopped)
  const workers = Number(state?.running) || 0
  const since = formatStamp(state?.stopped_at)

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col bg-(--ui-surface-background)',
    'data-slot': 'kanban-plus-page',
    children: [
      jsxs('header', {
        className: 'flex shrink-0 flex-wrap items-center gap-2 px-4 pt-3 pb-2',
        children: [
          jsx('h1', { className: 'text-sm font-semibold text-foreground', children: state?.board ?? 'Kanban+' }),
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
          selected
            ? jsx(WorkerLogPanel, { task: selected }, selected.id)
            : jsx('div', {
                className: 'grid min-h-0 flex-1 place-items-center',
                children: jsx(EmptyState, {
                  description: 'Task links auswählen, um sein Worker-Log zu sehen.',
                  title: 'Kein Task ausgewählt'
                })
              })
        ]
      })
    ]
  })
}

// ── statusbar ────────────────────────────────────────────────────────────────

function StatusbarPill() {
  const qc = useQueryClient()

  const { data: state } = useQuery({
    queryFn: fetchState,
    queryKey: STATE_KEY,
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
    // Disk plugins hot-reload on every save; drop the door so a stale closure
    // can never keep calling through a context that was torn down.
    ctx.onDispose(() => {
      restCall = null
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
