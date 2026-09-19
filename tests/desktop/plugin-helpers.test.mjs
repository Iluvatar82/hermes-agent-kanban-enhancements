/**
 * Unit tests for the desktop plugin's pure helpers.
 *
 * The plugin imports '@hermes/plugin-sdk' and 'react', which only exist inside
 * the Hermes app. The harness builds a throwaway package tree with stubs for
 * both, copies plugin.js into it and imports that copy — so the file under test
 * is byte-identical to the one the app loads.
 *
 * Run: node --test tests/desktop/
 */

import assert from 'node:assert/strict'
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = fileURLToPath(new URL('.', import.meta.url))
const pluginFile = join(here, '..', '..', 'kanban-enhancements', 'desktop', 'plugin.js')

function stubTree() {
  const root = mkdtempSync(join(tmpdir(), 'kanban-plus-'))
  const sdkDir = join(root, 'node_modules', '@hermes', 'plugin-sdk')

  mkdirSync(sdkDir, { recursive: true })
  writeFileSync(join(root, 'package.json'), JSON.stringify({ name: 'harness', type: 'module' }))
  writeFileSync(join(sdkDir, 'package.json'), JSON.stringify({ name: '@hermes/plugin-sdk', main: 'index.js' }))
  // Every SDK export the plugin imports, stubbed: components are never rendered
  // here, so a marker object is enough.
  writeFileSync(
    join(sdkDir, 'index.js'),
    `const stub = name => Object.assign(() => null, { __stub: name })
export const host = { navigate() {}, notify() {}, notifyError() {}, request: async () => ({}),
  state: { focusedSessionId: { get: () => null, subscribe: () => () => {} },
           focusedUsage: { get: () => null, subscribe: () => () => {} } } }
export const queryClient = { invalidateQueries() {} }
export const cn = (...parts) => parts.filter(Boolean).join(' ')
export const compactNumber = n => String(n)
export const useValue = atom => atom.get()
export const useQuery = () => ({ data: undefined, isLoading: false })
export const useMutation = () => ({ mutate() {}, isPending: false })
export const useQueryClient = () => queryClient
export const COMPOSER_AREAS = { top: 'composer.top' }
export const PALETTE_AREA = 'palette'
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const WORKSPACE_PAGE_HEADER_AREA = 'workspace.pageHeader'
export const isSubmitEnter = () => false
// A nanostore the size of what the plugin uses: get / set / listen.
export const atom = initial => {
  let value = initial
  const listeners = new Set()

  return {
    get: () => value,
    set(next) { value = next; for (const fn of listeners) fn(next) },
    listen(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    subscribe(fn) { fn(value); listeners.add(fn); return () => listeners.delete(fn) }
  }
}
for (const name of ['Badge','Button','Codicon','ConfirmDialog','Contribute','Dialog','DialogContent',
  'DialogFooter','DialogHeader','DialogTitle','DropdownMenu','DropdownMenuContent',
  'DropdownMenuItem','DropdownMenuSeparator','DropdownMenuTrigger','EmptyState','ErrorState','Input',
  'Loader','Popover','PopoverContent','PopoverTrigger','ScrollArea','SearchField','Select','SelectContent',
  'SelectItem','SelectTrigger','SelectValue','StatusDot','Tip']) {
  Object.defineProperty(globalThis, name, { value: stub(name), configurable: true })
}
export const Badge = globalThis.Badge, Button = globalThis.Button, Codicon = globalThis.Codicon
export const ConfirmDialog = globalThis.ConfirmDialog, Contribute = globalThis.Contribute
export const Dialog = globalThis.Dialog, DialogContent = globalThis.DialogContent
export const DialogFooter = globalThis.DialogFooter, DialogHeader = globalThis.DialogHeader
export const DialogTitle = globalThis.DialogTitle, DropdownMenu = globalThis.DropdownMenu
export const DropdownMenuContent = globalThis.DropdownMenuContent, DropdownMenuItem = globalThis.DropdownMenuItem
export const DropdownMenuSeparator = globalThis.DropdownMenuSeparator
export const DropdownMenuTrigger = globalThis.DropdownMenuTrigger, EmptyState = globalThis.EmptyState
export const ErrorState = globalThis.ErrorState, Input = globalThis.Input, Loader = globalThis.Loader
export const Popover = globalThis.Popover, PopoverContent = globalThis.PopoverContent
export const PopoverTrigger = globalThis.PopoverTrigger, ScrollArea = globalThis.ScrollArea
export const SearchField = globalThis.SearchField, StatusDot = globalThis.StatusDot, Tip = globalThis.Tip
export const Select = globalThis.Select, SelectContent = globalThis.SelectContent
export const SelectItem = globalThis.SelectItem, SelectTrigger = globalThis.SelectTrigger
export const SelectValue = globalThis.SelectValue
`
  )
  // react + react/jsx-runtime: the helpers under test never render, so the
  // hooks only need to exist as functions.
  const reactDir = join(root, 'node_modules', 'react')

  mkdirSync(reactDir, { recursive: true })
  writeFileSync(
    join(reactDir, 'package.json'),
    JSON.stringify({
      name: 'react',
      exports: { '.': './index.js', './jsx-runtime': './jsx-runtime.js' }
    })
  )
  writeFileSync(
    join(reactDir, 'index.js'),
    `const noop = () => {}
export const memo = component => component
export const useEffect = noop
export const useLayoutEffect = noop
export const useMemo = factory => factory()
export const useRef = initial => ({ current: initial })
export const useState = initial => [initial, noop]
export default { memo, useEffect, useLayoutEffect, useMemo, useRef, useState }
`
  )
  writeFileSync(
    join(reactDir, 'jsx-runtime.js'),
    `export const Fragment = Symbol.for('react.fragment')
export const jsx = (type, props) => ({ type, props })
export const jsxs = jsx
`
  )
  cpSync(pluginFile, join(root, 'plugin.mjs'))

  return root
}

const root = stubTree()
const plugin = await import(pathToFileURL(join(root, 'plugin.mjs')).href)

after(() => {
  // The OS cleans the temp dir; nothing to do, but keep the hook explicit.
})

describe('plugin contract', () => {
  it('default-exports the shape the runtime loader validates', () => {
    assert.equal(plugin.default.id, 'kanban-enhancements')
    assert.equal(typeof plugin.default.register, 'function')
  })

  it('registers page, nav, statusbar, composer and palette rows', () => {
    const registered = []
    const ctx = { onDispose() {}, register: c => registered.push(c), registerMany: cs => registered.push(...cs) }

    plugin.default.register(ctx)
    const areas = registered.map(entry => entry.area)
    assert.ok(areas.includes('routes') && areas.includes('sidebar.nav'))
    assert.ok(areas.includes('statusBar.right') && areas.includes('composer.top'))
    assert.equal(areas.filter(area => area === 'palette').length, 3)
    const page = registered.find(entry => entry.area === 'routes')
    assert.equal(page.data.path, '/kanban-plus')
    assert.equal(typeof page.render, 'function')
  })
})

describe('buildLogRows', () => {
  const lines = content => plugin.buildLogRows(content).filter(row => row.kind === 'line')

  it('moves the write stamp into the gutter and drops ANSI styling', () => {
    const rows = lines('[2026-09-17T08:23:45Z] \u001b[2;3mInitializing agent...\u001b[0m\r\n')

    assert.equal(rows.length, 1)
    assert.equal(rows[0].text, 'Initializing agent...')
    assert.equal(rows[0].iso, '2026-09-17T08:23:45Z')
    assert.match(rows[0].time, /\d{2}.\d{2}.\d{2}/)
    assert.ok(rows[0].title.includes('2026'))
  })

  it('keeps unstamped lines without borrowing a time', () => {
    assert.deepEqual(
      lines('plain one\n[not a stamp] two').map(row => [row.iso, row.time, row.text]),
      [[null, '', 'plain one'], [null, '', '[not a stamp] two']]
    )
  })

  it('marks lines written in the same second as repeats', () => {
    const rows = lines(
      ['[2026-09-17T08:00:00Z] a', '[2026-09-17T08:00:00Z] b', '[2026-09-17T08:00:01Z] c', ''].join('\n')
    )

    assert.deepEqual(rows.map(row => row.repeat), [false, true, false])
  })

  it('inserts a divider whenever the local date changes', () => {
    const stamp = date => `[${date.toISOString().slice(0, 19)}Z]`
    const late = stamp(new Date(2026, 8, 16, 23, 59, 59))
    const early = stamp(new Date(2026, 8, 17, 0, 0, 1))

    assert.deepEqual(
      plugin.buildLogRows(`${late} late\n${early} early\n`).map(row => row.kind),
      ['day', 'line', 'day', 'line']
    )
  })

  it('shows what a terminal would keep after a carriage return', () => {
    assert.equal(lines('[2026-09-17T08:00:00Z] 10%\r50%\r100%')[0].text, '100%')
  })

  it('survives empty and missing input', () => {
    assert.deepEqual(plugin.buildLogRows(''), [])
    assert.deepEqual(plugin.buildLogRows(null), [])
  })
})

describe('stripAnsi', () => {
  it('removes CSI and OSC sequences only', () => {
    assert.equal(plugin.stripAnsi('\u001b[1;38;2;255;215;0mHermes\u001b[0m [exit 128]'), 'Hermes [exit 128]')
    assert.equal(plugin.stripAnsi('\u001b]8;;https://x\u0007link\u001b]8;;\u0007'), 'link')
  })
})

describe('meterTone', () => {
  it('escalates at 75 and 90 percent', () => {
    assert.equal(plugin.meterTone(10), 'normal')
    assert.equal(plugin.meterTone(80), 'warn')
    assert.equal(plugin.meterTone(95), 'critical')
    assert.equal(plugin.meterTone(Number.NaN), 'normal')
  })
})

describe('modelLabel', () => {
  it('reads like the app: provider: model, or the inherit copy', () => {
    assert.equal(plugin.modelLabel({ model: 'qwen', provider: 'lmstudio' }, 'inherit'), 'lmstudio: qwen')
    assert.equal(plugin.modelLabel({ model: 'qwen', provider: '' }, 'inherit'), 'qwen')
    assert.equal(plugin.modelLabel({ model: '   ', provider: 'x' }, 'inherit'), 'inherit')
    assert.equal(plugin.modelLabel(undefined, 'inherit'), 'inherit')
  })
})

describe('the picked board', () => {
  it('leaves a path alone while the server-side current board is picked', () => {
    plugin.$boardSlug.set('')

    assert.equal(plugin.withBoard('/state'), '/state')
    assert.equal(plugin.withBoard('/tasks/t_1/log', { tail: '100' }), '/tasks/t_1/log?tail=100')
  })

  it('carries the picked board on every call', () => {
    plugin.$boardSlug.set('shipping')

    assert.equal(plugin.withBoard('/state'), '/state?board=shipping')
    assert.equal(plugin.withBoard('/tasks/t_1/log', { tail: '100' }), '/tasks/t_1/log?tail=100&board=shipping')

    plugin.$boardSlug.set('')
  })

  it('remembers a selected task per board', () => {
    assert.equal(plugin.selectedTaskKey(''), 'hermes.plugin.kanban-enhancements.selectedTask')
    assert.equal(plugin.selectedTaskKey('shipping'), 'hermes.plugin.kanban-enhancements.selectedTask.shipping')
  })

  it('hydrates from plugin storage and writes every change back', () => {
    const stored = { boardSlug: 'shipping' }
    const disposers = []
    const ctx = {
      onDispose: fn => disposers.push(fn),
      register() {},
      registerMany() {},
      rest: async () => ({}),
      storage: { get: (key, fallback) => stored[key] ?? fallback, set: (key, value) => { stored[key] = value } }
    }

    plugin.default.register(ctx)
    assert.equal(plugin.$boardSlug.get(), 'shipping')

    plugin.$boardSlug.set('ops')
    assert.equal(stored.boardSlug, 'ops')

    disposers.forEach(dispose => dispose())
    plugin.$boardSlug.set('')
  })

  it('remembers the lanes the reader folded, and survives a junk value', () => {
    const stored = { collapsedLanes: { done: false } }
    const disposers = []
    const ctx = {
      onDispose: fn => disposers.push(fn),
      register() {},
      registerMany() {},
      rest: async () => ({}),
      storage: { get: (key, fallback) => stored[key] ?? fallback, set: (key, value) => { stored[key] = value } }
    }

    plugin.default.register(ctx)
    assert.deepEqual(plugin.$collapsedLanes.get(), { done: false })

    plugin.$collapsedLanes.set({ todo: true })
    assert.deepEqual(stored.collapsedLanes, { todo: true })

    disposers.forEach(dispose => dispose())

    // A storage that hands back something that is not a map must not become
    // the atom's value — every read of it would then throw on the board page.
    stored.collapsedLanes = 'corrupted'
    plugin.default.register(ctx)
    assert.deepEqual(plugin.$collapsedLanes.get(), {})

    disposers.forEach(dispose => dispose())
    plugin.$collapsedLanes.set({})
    plugin.$boardSlug.set('')
  })
})

describe('collapsed lanes', () => {
  const lane = (name, count) => ({ name, tasks: Array.from({ length: count }, (_, i) => ({ id: `${name}-${i}` })) })

  it('collapses an empty lane and opens an occupied one, with no state at all', () => {
    assert.equal(plugin.laneCollapsed({}, lane('done', 0)), true)
    assert.equal(plugin.laneCollapsed({}, lane('todo', 2)), false)
    assert.equal(plugin.laneCollapsed({}, { name: 'todo' }), true)
  })

  it('lets an override win over the rule, either way', () => {
    assert.equal(plugin.laneCollapsed({ done: false }, lane('done', 0)), false)
    assert.equal(plugin.laneCollapsed({ todo: true }, lane('todo', 2)), true)
  })

  it('records a click as a deviation, and a click back as no entry at all', () => {
    const opened = plugin.toggleLane({}, 'done', true)
    assert.deepEqual(opened, { done: false })
    assert.deepEqual(plugin.toggleLane(opened, 'done', true), {}, 'back to automatic leaves nothing behind')

    const closed = plugin.toggleLane({}, 'todo', false)
    assert.deepEqual(closed, { todo: true })
    assert.deepEqual(plugin.toggleLane(closed, 'todo', false), {})
  })

  it('signs the board by which lanes are empty', () => {
    assert.equal(plugin.lanePhase([lane('triage', 0), lane('todo', 1)]), 'triage:empty|todo:full')
    assert.equal(plugin.lanePhase(undefined), '')
  })

  it('drops an override only for the lane whose emptiness flipped', () => {
    const overrides = { todo: true, done: false }
    const before = 'todo:full|done:empty'
    const after = 'todo:full|done:full' // a card was dragged into Fertig

    assert.deepEqual(plugin.pruneStaleLanes(overrides, before, after), { todo: true })
  })

  it('keeps every override while nothing moved, and on the first render', () => {
    const overrides = { todo: true }

    assert.equal(plugin.pruneStaleLanes(overrides, 'todo:full', 'todo:full'), overrides)
    assert.equal(plugin.pruneStaleLanes(overrides, null, 'todo:empty'), overrides)
  })

  it('leaves a lane the board has never shown before alone', () => {
    const overrides = { todo: true }

    assert.deepEqual(plugin.pruneStaleLanes(overrides, 'done:empty', 'done:empty|todo:full'), { todo: true })
  })
})

describe('board names', () => {
  it('derives the slug `boards create` would make', () => {
    assert.equal(plugin.boardSlugFromName('  Ship it! 2.0 '), 'ship-it-2-0')
    assert.equal(plugin.boardSlugFromName('***'), '')
    assert.equal(plugin.boardSlugFromName(undefined), '')
  })

  it('labels the switcher with the display name, the slug, or the word', () => {
    const boards = {
      boards: [{ name: 'Default', slug: 'default' }, { name: '', slug: 'shipping' }],
      current: 'default'
    }

    assert.equal(plugin.boardLabel(boards, ''), 'Default')
    assert.equal(plugin.boardLabel(boards, 'shipping'), 'shipping')
    assert.equal(plugin.boardLabel(boards, 'archived-one'), 'Board')
    assert.equal(plugin.boardLabel(undefined, ''), 'Board')
  })
})

describe('columnMeta', () => {
  it('keeps core’s lanes, and falls back to the raw status', () => {
    assert.equal(plugin.columnMeta('running').codicon, 'sync')
    assert.equal(plugin.columnMeta('running').label, 'Läuft')
    assert.equal(plugin.columnMeta('triage').codicon, 'inbox')
    assert.equal(plugin.columnMeta('a-status-a-later-hermes-adds').label, 'a-status-a-later-hermes-adds')
  })
})

describe('errText', () => {
  it('surfaces the detail the REST bridge buried in a status line', () => {
    assert.equal(
      plugin.errText(new Error('409: {"detail":"Cannot move to \'ready\': blocked by parent(s)"}')),
      "Cannot move to 'ready': blocked by parent(s)"
    )
  })

  it('keeps a plain message, and never returns an empty string', () => {
    assert.equal(plugin.errText(new Error('boom')), 'boom')
    assert.equal(plugin.errText('boom'), 'boom')
    assert.equal(plugin.errText(new Error('500: not json {')), '500: not json {')
    assert.equal(plugin.errText(undefined), 'Unbekannter Fehler')
  })
})

describe('shortId', () => {
  it('drops the prefix and keeps six characters', () => {
    assert.equal(plugin.shortId('t_ab12cd34ef'), 'ab12cd')
    assert.equal(plugin.shortId('short'), 'short')
    assert.equal(plugin.shortId(null), '')
  })
})

describe('logTail', () => {
  const log = ['one', 'two', 'three', 'four'].join('\n')

  it('keeps the LAST lines, because that is where the worker is now', () => {
    assert.equal(plugin.logTail(log, 2), 'three\nfour')
  })

  it('treats a trailing newline as a terminator, not a line', () => {
    assert.equal(plugin.logTail(`${log}\n`, 2), 'three\nfour\n')
  })

  it('leaves a short log and an absent limit alone', () => {
    assert.equal(plugin.logTail(log, 10), log)
    assert.equal(plugin.logTail(log, 0), log)
    assert.equal(plugin.logTail(null, 5), '')
  })
})

describe('drop targets', () => {
  it('refuses the lanes the dispatcher owns', () => {
    assert.equal(plugin.isLockedTarget('running'), true)
    assert.equal(plugin.isLockedTarget('review'), true)
    assert.equal(plugin.isLockedTarget('scheduled'), true)
    assert.equal(plugin.isLockedTarget('todo'), false)
  })

  it('offers every open lane but the card’s own', () => {
    const columns = ['triage', 'todo', 'scheduled', 'ready', 'running', 'blocked', 'review', 'done']

    assert.deepEqual(plugin.moveTargets(columns, 'todo'), ['triage', 'ready', 'blocked', 'done'])
    assert.deepEqual(plugin.moveTargets(columns, 'running'), ['triage', 'todo', 'ready', 'blocked', 'done'])
    assert.deepEqual(plugin.moveTargets(undefined, 'todo'), [])
  })
})

describe('clampMenu', () => {
  const viewport = { viewportHeight: 800, viewportWidth: 1000 }

  it('opens where the pointer is when there is room', () => {
    assert.deepEqual(plugin.clampMenu({ ...viewport, height: 100, width: 200, x: 300, y: 400 }), {
      left: 300,
      top: 400
    })
  })

  it('pulls a menu back inside when it would overflow an edge', () => {
    assert.deepEqual(plugin.clampMenu({ ...viewport, height: 100, width: 200, x: 990, y: 790 }), {
      left: 794,
      top: 694
    })
  })

  it('never leaves the viewport on the other side either', () => {
    const { left, top } = plugin.clampMenu({ height: 900, viewportHeight: 200, viewportWidth: 100, width: 300, x: 0, y: 0 })

    assert.ok(left >= 0 && top >= 0)
  })
})

describe('eventLabel', () => {
  it('reads a machine payload back as a sentence', () => {
    assert.deepEqual(plugin.eventLabel({ kind: 'status', payload: { status: 'ready' } }), {
      detail: undefined,
      label: 'Verschoben nach Ready'
    })
    assert.equal(plugin.eventLabel({ kind: 'assigned', payload: { assignee: 'dev' } }).label, 'Zugewiesen an dev')
    assert.equal(plugin.eventLabel({ kind: 'assigned', payload: {} }).label, 'Zuweisung entfernt')
    assert.equal(plugin.eventLabel({ kind: 'spawned', payload: { pid: 42 } }).detail, 'PID 42')
  })

  it('parses a payload the backend stored as JSON text', () => {
    assert.equal(plugin.eventLabel({ kind: 'blocked', payload: '{"reason":"kein Netz"}' }).detail, 'kein Netz')
  })

  it('still says something for a kind it has never seen', () => {
    assert.deepEqual(plugin.eventLabel({ kind: 'some_new_kind', payload: { a: 1, b: null } }), {
      detail: 'a=1',
      label: 'some new kind'
    })
  })
})

describe('updateState', () => {
  it('puts "restart" ahead of "available"', () => {
    // The update already landed on disk; telling anyone to download it again
    // is the one genuinely confusing thing this row could say.
    assert.equal(
      plugin.updateState({ can_update: true, restart_required: true, update_available: true }),
      'restart'
    )
  })

  it('names the other three states', () => {
    assert.equal(plugin.updateState({ can_update: true, update_available: true }), 'available')
    assert.equal(plugin.updateState({ can_update: true, update_available: false }), 'current')
    assert.equal(plugin.updateState({ can_update: false, update_available: true }), 'unavailable')
    assert.equal(plugin.updateState(undefined), 'current')
  })
})

describe('versionLabel', () => {
  it('shows both versions only while they disagree', () => {
    assert.equal(plugin.versionLabel({ installed: '0.3.0', running: '0.3.0' }), 'Kanban+ 0.3.0')
    assert.equal(plugin.versionLabel({ installed: '0.4.0', running: '0.3.0' }), 'Kanban+ 0.3.0 → 0.4.0')
    assert.equal(plugin.versionLabel({ running: '0.3.0' }), 'Kanban+ 0.3.0')
    assert.equal(plugin.versionLabel(undefined), 'Kanban+')
  })
})

describe('element construction', () => {
  // `jsx(Component)` without a props object throws "Cannot read properties of
  // undefined (reading 'key')" inside React's real runtime and takes the whole
  // page down — the stub above is too forgiving to catch it, so read the source.
  it('never calls jsx() without a props object', () => {
    const source = readFileSync(pluginFile, 'utf8')
    const offenders = [...source.matchAll(/\bjsxs?\(\s*[A-Za-z_$][\w$.]*\s*\)/g)].map(match => match[0])

    assert.deepEqual(offenders, [])
  })

  // `jsx(Typo, …)` is a ReferenceError the moment that branch renders, which
  // can be a task view nobody opened during a smoke test. The stub SDK is far
  // too forgiving to catch it, so the names are checked against the source.
  it('only builds elements from components this file actually has', () => {
    const source = readFileSync(pluginFile, 'utf8')
    const names = pattern => new Set([...source.matchAll(pattern)].map(match => match[1]))

    const declared = new Set([
      // `function X(` / `export function X(`
      ...names(/^(?:export )?function ([A-Za-z_$][\w$]*)/gm),
      // `const X = …` at module scope (memo() components, atoms, constants)
      ...names(/^(?:export )?const ([A-Za-z_$][\w$]*) =/gm),
      // Everything imported — the SDK block, react, react/jsx-runtime.
      ...[...source.matchAll(/import\s*\{([^}]*)\}\s*from/g)].flatMap(match =>
        match[1].split(',').map(part => part.trim().split(/\s+as\s+/).pop())
      )
    ])

    const used = [...names(/\bjsxs?\(\s*([A-Z][\w$]*)/g)]
    const missing = used.filter(name => !declared.has(name))

    assert.deepEqual(missing, [])
    // A guard that finds nothing because its patterns broke is worse than none.
    assert.ok(used.length > 20 && declared.has('Codicon') && declared.has('TaskDetailPanel'))
  })
})

// The page's layout rules are load-bearing and invisible to a unit test that
// never renders: the drawer must float OVER the lanes rather than take a column
// from them, and nothing may scroll the page sideways. They are read off the
// source for the same reason the two guards above are.
describe('page layout', () => {
  const source = readFileSync(pluginFile, 'utf8')

  /** The element whose `data-slot` is `slot`, from its opening jsx call to it. */
  const slotElement = slot => {
    const at = source.indexOf(`'data-slot': '${slot}'`)

    assert.ok(at > 0, `no element carries data-slot="${slot}"`)

    return source.slice(Math.max(0, at - 900), at)
  }

  it('floats the task drawer over the board, a third wide with a floor', () => {
    const drawer = slotElement('kanban-plus-task')

    assert.match(drawer, /absolute inset-y-0 right-0/, 'the drawer is positioned, not a flex column')
    assert.match(source, /width: 'min\(100%, max\(22rem, 33\.3333%\)\)'/)
    // min-width would beat max-width in CSS and overflow a narrow pane — the
    // floor has to live inside the min() instead.
    assert.doesNotMatch(source, /minWidth:/, 'the floor belongs in the width expression')
  })

  // Tailwind scans source at BUILD time and never sees this file, so a class it
  // does not already generate for the app produces no rule at all. Sizes that
  // decide the layout are therefore inline, where nothing has to be generated.
  it('never bets the layout on an arbitrary class Hermes may not emit', () => {
    // Comments talk ABOUT these classes; only the ones that ship count.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const invented = [...code.matchAll(/\b(?:w|h|min-w|min-h|max-w|max-h)-[[(][^'"\s]*/g)].map(m => m[0])

    assert.deepEqual(invented, [], 'put load-bearing sizes in an inline style instead')
  })

  it('clips the board area so neither the drawer nor the lanes escape it', () => {
    const page = slotElement('kanban-plus-page')

    assert.match(page, /relative flex h-full min-h-0 flex-col overflow-hidden/)
    assert.match(source, /relative flex min-h-0 flex-1 overflow-hidden border-t/)
  })

  it('never scrolls the worker log sideways', () => {
    for (const slot of ['kanban-plus-log-overlay']) {
      assert.match(slotElement(slot), /overflow-hidden/)
    }

    // Both log scrollers: lines wrap, so the only scrollbar is the vertical one.
    assert.equal([...source.matchAll(/overflow-x-hidden overflow-y-auto/g)].length, 2)
    assert.doesNotMatch(source, /className: '[^']*\boverflow-auto\b/, 'no scroller may scroll both ways')
  })

  it('shows nothing to the left of the lanes', () => {
    const lanes = source.indexOf("'data-slot': 'kanban-plus-columns'")
    const page = source.indexOf("'data-slot': 'kanban-plus-page'")

    assert.ok(page > 0 && lanes > 0)
    // The running-worker rail is gone: the lanes already carry those cards, in
    // Läuft, and a second copy of them cost the board a column.
    assert.doesNotMatch(source, /TaskGroup|TaskRow/, 'the running list is gone, not just unmounted')
    assert.doesNotMatch(source, /border-r border-\(--ui-stroke-tertiary\)'/)
  })

  it('gives the drawer log a real height', () => {
    assert.match(source, /const SMALL_LOG_STYLE = \{ maxHeight: '\d+rem', minHeight: '200px' \}/)
  })
})
