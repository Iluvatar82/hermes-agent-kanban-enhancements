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

  it('keeps the line a Windows log ends with \\r\\r\\n', () => {
    // The bug this test exists for: a worker's text stream rewrites its `\n`
    // as `\r\n`, so a line that was ALREADY CRLF reaches the file doubled.
    // Read as "everything after the last \r", every row came out blank — a
    // 26 KiB log rendered as an empty box.
    const rows = lines('[2026-09-17T08:00:00Z] built in 3.4s\r\r\n[2026-09-17T08:00:01Z] done\r\r\n')

    assert.deepEqual(rows.map(row => row.text), ['built in 3.4s', 'done'])
  })

  it('keeps a line whose only carriage return is the one at its end', () => {
    assert.deepEqual(lines('plain\r\nlines\r\n').map(row => row.text), ['plain', 'lines'])
    assert.equal(lines('cursor parked\r')[0].text, 'cursor parked')
  })

  it('survives empty and missing input', () => {
    assert.deepEqual(plugin.buildLogRows(''), [])
    assert.deepEqual(plugin.buildLogRows(null), [])
  })
})

describe('collapseCarriageReturns', () => {
  const collapse = plugin.collapseCarriageReturns

  it('keeps the last frame that actually wrote something', () => {
    assert.equal(collapse('10%\r50%\r100%'), '100%')
    assert.equal(collapse('done\r\r'), 'done')
    assert.equal(collapse('no returns here'), 'no returns here')
    assert.equal(collapse('\r\r'), '')
  })
})

describe('run openings borrow the next stamp', () => {
  const lines = content => plugin.buildLogRows(content).filter(row => row.kind === 'line')
  const run = [
    'Query: work kanban task t_1',
    'Initializing agent...',
    '[2026-09-17T08:00:05Z] preparing terminal',
    '[2026-09-17T08:00:06Z] done'
  ].join('\n')

  it('gives the lines before the first stamp that stamp, marked as an estimate', () => {
    const rows = lines(run)

    assert.deepEqual(
      rows.map(row => [row.text, row.iso, Boolean(row.approx)]),
      [
        ['Query: work kanban task t_1', '2026-09-17T08:00:05Z', true],
        ['Initializing agent...', '2026-09-17T08:00:05Z', true],
        ['preparing terminal', '2026-09-17T08:00:05Z', false],
        ['done', '2026-09-17T08:00:06Z', false]
      ]
    )
    assert.ok(rows[0].title.startsWith('≈'))
    assert.equal(rows[0].time, rows[2].time)
  })

  it('never dims the real line as a repeat of its own estimate', () => {
    assert.equal(lines(run)[2].repeat, false)
  })

  it('does not reach into the previous run', () => {
    const log = ['old run line', '[kanban-worker-exit] rc=0', 'Query: work kanban task t_1', run.split('\n').slice(1).join('\n')].join('\n')
    const rows = lines(log)

    assert.equal(rows[0].iso, null)
    assert.equal(rows[1].iso, null)
    assert.equal(rows[2].approx, true)
  })

  it('stops at the run start even without an exit marker above it', () => {
    const log = ['unstamped tail of an older run', 'Query: work kanban task t_2', '[2026-09-17T09:00:00Z] go'].join('\n')

    assert.deepEqual(lines(log).map(row => row.iso), [null, '2026-09-17T09:00:00Z', '2026-09-17T09:00:00Z'])
  })

  it('borrows at most five lines back', () => {
    const log = [...Array.from({ length: 8 }, (_, i) => `line ${i}`), '[2026-09-17T09:00:00Z] first stamp'].join('\n')
    const borrowed = lines(log).filter(row => row.approx).length

    assert.equal(borrowed, 5)
  })

  it('leaves an entirely unstamped (old) log blank', () => {
    assert.ok(lines('Query: work kanban task t_3\nInitializing agent...\nsomething').every(row => row.iso === null))
  })
})

describe('plainLogText', () => {
  it('drops the stamps, the ANSI and the carriage returns, keeps the lines', () => {
    const log = [
      '[2026-09-17T08:00:00Z] \u001b[32mstarting\u001b[0m\r',
      '[2026-09-17T08:00:01Z] 10%\r100%\r',
      'no stamp at all\r',
      ''
    ].join('\n')

    assert.equal(plugin.plainLogText(log), 'starting\n100%\nno stamp at all\n')
  })

  it('leaves a bracket that is not a stamp alone', () => {
    assert.equal(plugin.plainLogText('[exit 128] see above'), '[exit 128] see above')
  })

  it('survives empty and missing input', () => {
    assert.equal(plugin.plainLogText(''), '')
    assert.equal(plugin.plainLogText(null), '')
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

  it('reads a 405 as the gateway serving an older backend than this page', () => {
    // Both halves ship together, so the page cannot ask its own backend for a
    // method that backend lacks — unless an update replaced the files under a
    // gateway that had already imported the previous module. `Method Not
    // Allowed` alone sends the reader looking for a bug that is not there.
    for (const raw of ['405: {"detail":"Method Not Allowed"}', 'Method Not Allowed']) {
      assert.match(plugin.errText(new Error(raw)), /Gateway serviert noch die vorherige Version/)
      assert.match(plugin.errText(new Error(raw)), /hermes gateway restart/)
    }
  })

  it('does not read a 405 into a status that merely contains one', () => {
    assert.equal(plugin.errText(new Error('500: {"detail":"worker 405 exited"}')), 'worker 405 exited')
  })
})

describe('shortId', () => {
  it('drops the prefix and keeps six characters', () => {
    assert.equal(plugin.shortId('t_ab12cd34ef'), 'ab12cd')
    assert.equal(plugin.shortId('short'), 'short')
    assert.equal(plugin.shortId(null), '')
  })
})

describe('parseSkills', () => {
  it('splits on commas and drops the empties', () => {
    assert.deepEqual(plugin.parseSkills(' python , , review '), ['python', 'review'])
    assert.deepEqual(plugin.parseSkills(''), [])
    assert.deepEqual(plugin.parseSkills(null), [])
  })
})

describe('newTaskPayload', () => {
  const form = {
    assignee: '__inherit__',
    body: '',
    goalMode: false,
    model: { model: '', provider: '' },
    parent: '',
    priority: '0',
    skills: '',
    title: '  Ship it  ',
    workspaceKind: '__inherit__',
    workspacePath: ''
  }

  it('sends the lane, the trimmed title and nothing it was not given', () => {
    // An empty field sent as '' is not the same as an omitted one: core reads
    // the first as "no, really, nothing" where it would otherwise inherit the
    // board's default.
    assert.deepEqual(plugin.newTaskPayload(form, 'todo'), { priority: 0, status: 'todo', title: 'Ship it' })
  })

  it('carries every field the dialog did get', () => {
    assert.deepEqual(
      plugin.newTaskPayload(
        {
          ...form,
          assignee: 'dev_developer',
          body: '  context  ',
          goalMode: true,
          model: { model: 'qwen3', provider: 'lmstudio' },
          parent: 't_parent',
          priority: '7',
          skills: 'python, review',
          workspaceKind: 'worktree',
          workspacePath: ' /repo '
        },
        'ready'
      ),
      {
        assignee: 'dev_developer',
        body: 'context',
        goal_mode: true,
        model_override: 'qwen3',
        parents: ['t_parent'],
        priority: 7,
        provider_override: 'lmstudio',
        skills: ['python', 'review'],
        status: 'ready',
        title: 'Ship it',
        workspace_kind: 'worktree',
        workspace_path: '/repo'
      }
    )
  })

  it('leaves the assignee out entirely when none was picked', () => {
    // Omitted, not empty: the dispatcher fills an unassigned ready task from
    // `kanban.default_assignee` on its next tick, and an empty string would
    // read as an answer where none was given.
    assert.ok(!('assignee' in plugin.newTaskPayload(form, 'ready')))
  })

  it('drops a workspace path that scratch would ignore', () => {
    const payload = plugin.newTaskPayload({ ...form, workspaceKind: 'scratch', workspacePath: '/repo' }, 'ready')

    assert.equal(payload.workspace_kind, 'scratch')
    assert.ok(!('workspace_path' in payload))
  })

  it('never sends a provider without the model it belongs to', () => {
    const payload = plugin.newTaskPayload({ ...form, model: { model: '', provider: 'lmstudio' } }, 'ready')

    assert.ok(!('provider_override' in payload) && !('model_override' in payload))
  })

  it('survives a priority that is not a number', () => {
    assert.equal(plugin.newTaskPayload({ ...form, priority: 'abc' }, 'ready').priority, 0)
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

describe('logPlaceholder', () => {
  // The bug this guards: as a COMPONENT, `jsx(LogPlaceholder, …)` handed both
  // log views an element object that is truthy even in the "there is a log"
  // case, so `placeholder ?? theLog` never reached the log. Small view and
  // overlay were empty boxes under a header that correctly reported the size.
  it('is null once a log arrived, so the views can draw it', () => {
    const log = { content: 'worker starting\n', exists: true, size_bytes: 26_731 }

    assert.equal(plugin.logPlaceholder({ error: null, isLoading: false, log }), null)
    // Still null while a poll refreshes a log that is already on screen.
    assert.equal(plugin.logPlaceholder({ error: null, isLoading: true, log }), null)
  })

  it('shows the loader only before the first answer', () => {
    const element = plugin.logPlaceholder({ error: null, isLoading: true, log: undefined })

    assert.equal(element.type, 'div')
    assert.equal(element.props.children.type.__stub, 'Loader')
  })

  it('shows the error and the missing-file state', () => {
    const failed = plugin.logPlaceholder({ error: new Error('boom'), isLoading: false, log: undefined })
    const missing = plugin.logPlaceholder({ error: null, isLoading: false, log: { exists: false } })

    assert.equal(failed.type.__stub, 'ErrorState')
    assert.equal(failed.props.description, 'boom')
    assert.equal(missing.type.__stub, 'EmptyState')
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
  // These guards read the SHIPPED code: a comment that talks about a mistake —
  // and the ones below are documented where they were made — is prose, not a
  // second helping of the bug.
  const code = readFileSync(pluginFile, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')

  // `jsx(Component)` without a props object throws "Cannot read properties of
  // undefined (reading 'key')" inside React's real runtime and takes the whole
  // page down — the stub above is too forgiving to catch it, so read the source.
  it('never calls jsx() without a props object', () => {
    const offenders = [...code.matchAll(/\bjsxs?\(\s*[A-Za-z_$][\w$.]*\s*\)/g)].map(match => match[0])

    assert.deepEqual(offenders, [])
  })

  // `jsx(Component, …)` builds an element OBJECT — truthy even when that
  // component goes on to render nothing. `const placeholder = jsx(LogPlaceholder,
  // …)` followed by `placeholder ?? theLog` is what left BOTH worker-log views
  // empty: the branch that draws the log was unreachable. An element is
  // something to render, never something to ask whether it exists.
  it('never decides a branch on an element object', () => {
    const tested = text =>
      [...text.matchAll(/\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*jsxs?\(/g)]
        .map(match => match[1])
        .filter(name =>
          new RegExp(`!\\s*${name}\\b|\\b${name}\\s*(?:\\?\\?|\\?(?![.?])|&&|\\|\\|)`).test(text))

    // The detector, against the bug it exists for and against a legitimate use.
    assert.deepEqual(tested('const placeholder = jsx(P, {})\nreturn placeholder ?? log'), ['placeholder'])
    assert.deepEqual(tested("const bar = jsxs('div', {})\nreturn jsx('p', { children: bar })"), [])

    assert.deepEqual(tested(code), [])
  })

  // `jsx(Typo, …)` is a ReferenceError the moment that branch renders, which
  // can be a task view nobody opened during a smoke test. The stub SDK is far
  // too forgiving to catch it, so the names are checked against the source.
  it('only builds elements from components this file actually has', () => {
    const names = pattern => new Set([...code.matchAll(pattern)].map(match => match[1]))

    const declared = new Set([
      // `function X(` / `export function X(`
      ...names(/^(?:export )?function ([A-Za-z_$][\w$]*)/gm),
      // `const X = …` at module scope (memo() components, atoms, constants)
      ...names(/^(?:export )?const ([A-Za-z_$][\w$]*) =/gm),
      // Everything imported — the SDK block, react, react/jsx-runtime.
      ...[...code.matchAll(/import\s*\{([^}]*)\}\s*from/g)].flatMap(match =>
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

// ── what the full log's lines are ───────────────────────────────────────────

describe('classifyLogRule', () => {
  it('reads the frame the CLI draws a response in', () => {
    // What a worker log actually carries: the box padded to the width of the
    // terminal the worker ran in, title and all.
    assert.deepEqual(plugin.classifyLogRule('╭─ ☤ Hermes  15:28────────────────────────╮'), {
      edge: 'top',
      label: '☤ Hermes  15:28'
    })
    assert.deepEqual(plugin.classifyLogRule('╰─────────────────────────────────────────╯'), {
      edge: 'bottom',
      label: ''
    })
    assert.deepEqual(plugin.classifyLogRule('┌─ Reasoning ─────────────────────────────┐'), {
      edge: 'top',
      label: 'Reasoning'
    })
  })

  it('reads a bare rule, and only when it is long enough to be one', () => {
    assert.deepEqual(plugin.classifyLogRule('────────────────────────'), { edge: 'plain', label: '' })
    assert.equal(plugin.classifyLogRule('───'), null)
  })

  it('leaves ordinary lines — and prose that merely contains a dash — alone', () => {
    assert.equal(plugin.classifyLogRule('All automated checks pass (96/96 files)'), null)
    assert.equal(plugin.classifyLogRule('  ┊ $ python review.py   0.5s'), null)
    assert.equal(plugin.classifyLogRule('the run — which took 4m — is done'), null)
    assert.equal(plugin.classifyLogRule(''), null)
    assert.equal(plugin.classifyLogRule(undefined), null)
  })
})

describe('diffTone', () => {
  it('colours a unified diff the way a terminal would have', () => {
    assert.equal(plugin.diffTone('+    return True, im2.format'), 'plus')
    assert.equal(plugin.diffTone('-    return True, im2.size'), 'minus')
    assert.equal(plugin.diffTone('@@ -40,10 +40,20 @@'), 'hunk')
    assert.equal(plugin.diffTone('a/scripts/restore.py → b/scripts/restore.py'), 'file')
    assert.equal(plugin.diffTone('     try:'), 'context')
    assert.equal(plugin.diffTone('… omitted 103 diff line(s) across 1 additional file(s)'), 'hunk')
  })

  it('ends at the tool gutter, which is what follows a diff', () => {
    // It starts with a space like a context line does — reading it as one
    // swallowed every tool line after a diff into the diff.
    assert.equal(plugin.diffTone('  ┊ ✓ patch applied'), null)
    assert.equal(plugin.diffTone('Angewendet.'), null)
    assert.equal(plugin.diffTone(''), null)
  })
})

describe('workerExitCode', () => {
  it('reads the last line a kanban worker writes', () => {
    assert.equal(plugin.workerExitCode('[kanban-worker-exit] rc=0'), 0)
    assert.equal(plugin.workerExitCode('[kanban-worker-exit] rc=75'), 75)
    assert.equal(plugin.workerExitCode('  [kanban-worker-exit] rc=-1'), -1)
  })

  it('is null for everything else, including a line that talks about it', () => {
    assert.equal(plugin.workerExitCode('waiting for [kanban-worker-exit] rc=0'), null)
    assert.equal(plugin.workerExitCode('rc=0'), null)
    assert.equal(plugin.workerExitCode(undefined), null)
  })
})

describe('decorateLogRows', () => {
  const stamp = '[2026-09-20T15:28:04+02:00] '
  const log = [
    `${stamp}╭─ ☤ Hermes  15:28──────────────────────╮`,
    `${stamp}All automated checks pass`,
    `${stamp}`,
    `${stamp}╰───────────────────────────────────────╯`,
    `${stamp}  ┊ $ python review.py   0.5s`,
    `${stamp}  ┊ review diff`,
    `${stamp}a/scripts/restore.py → b/scripts/restore.py`,
    `${stamp}@@ -40,10 +40,20 @@`,
    `${stamp}-    return True, im2.size`,
    `${stamp}+    return True, im2.format`,
    `${stamp}  ┊ ✓ patch applied`,
    `${stamp}[kanban-worker-exit] rc=0`,
    ''
  ].join('\n')

  const rows = plugin.decorateLogRows(plugin.buildLogRows(log))

  it('reads a transcript into the kinds the view draws', () => {
    assert.deepEqual(rows.map(row => row.kind), [
      'day', 'rule', 'line', 'line', 'rule', 'line', 'diff', 'diff', 'diff', 'diff', 'diff', 'line', 'exit'
    ])
  })

  it('marks the lines a frame encloses, and only those', () => {
    assert.deepEqual(rows.filter(row => row.framed).map(row => row.text), [
      'All automated checks pass', ''
    ])
  })

  it('carries the rule’s title so the view can redraw the frame at its own width', () => {
    const [top, bottom] = rows.filter(row => row.kind === 'rule')

    assert.deepEqual([top.edge, top.label], ['top', '☤ Hermes  15:28'])
    assert.deepEqual([bottom.edge, bottom.label], ['bottom', ''])
  })

  it('tones a diff run and stops at the next tool line', () => {
    assert.deepEqual(rows.filter(row => row.kind === 'diff').map(row => row.tone), [
      'marker', 'file', 'hunk', 'minus', 'plus'
    ])
    assert.equal(rows.at(-2).kind, 'line')
  })

  it('keeps the exit code, which is what makes the end of a run visible', () => {
    assert.equal(rows.at(-1).code, 0)
  })

  it('survives a tail that starts mid-frame, and empty input', () => {
    const cut = plugin.decorateLogRows(plugin.buildLogRows('still inside the box\n╰──────────╯\nafter\n'))

    // No opening rule in this tail, so nothing claims to be framed.
    assert.deepEqual(cut.map(row => Boolean(row.framed)), [false, false, false])
    assert.deepEqual(plugin.decorateLogRows(undefined), [])
  })
})

describe('anchorOffset', () => {
  it('centres the breakdown card on the pointer', () => {
    // A meter that spans a window is a long way from a card pinned to its end.
    assert.equal(plugin.anchorOffset({ left: 100, pointerX: 500, width: 240 }), 280)
    assert.equal(plugin.anchorOffset({ left: 100, pointerX: 100, width: 240 }), -120)
  })

  it('falls back to the caller’s alignment when no pointer was seen', () => {
    assert.equal(plugin.anchorOffset({ left: 100, pointerX: null }), null)
    assert.equal(plugin.anchorOffset({ left: undefined, pointerX: 500 }), null)
  })
})

describe('skillsText', () => {
  it('round-trips through what the create dialog parses', () => {
    assert.equal(plugin.skillsText(['python', 'review']), 'python, review')
    assert.deepEqual(plugin.parseSkills(plugin.skillsText(['python', 'review'])), ['python', 'review'])
    assert.equal(plugin.skillsText(null), '')
    assert.equal(plugin.skillsText([]), '')
  })
})

describe('profileUpdateSummary', () => {
  it('counts what landed and names what did not', () => {
    const summary = plugin.profileUpdateSummary({
      profiles: [{ name: 'default', ok: true }, { name: 'dev', ok: false }, { name: 'review', ok: true }]
    })

    assert.deepEqual(summary.failed, ['dev'])
    assert.equal(summary.kind, 'warning')
    assert.equal(summary.message, '2 von 3 Profilen aktualisiert · fehlgeschlagen: dev')
  })

  it('says a full success is one, and asks for the restart it needs', () => {
    const summary = plugin.profileUpdateSummary({
      profiles: [{ name: 'default', ok: true }],
      restart_required: true
    })

    assert.equal(summary.kind, 'success')
    assert.equal(summary.message, '1 von 1 Profil aktualisiert · Gateway neu starten, um die neue Version zu laden')
  })

  it('never invents profiles it was not told about', () => {
    assert.equal(plugin.profileUpdateSummary(undefined).message, '0 von 0 Profilen aktualisiert')
  })
})

describe('the folded lane’s label', () => {
  it('starts at the top of the rail, not at half its height', () => {
    // `writing-mode: vertical-rl` turns the inline axis vertical, so a
    // <button>'s UA `text-align: center` centres the label DOWN the rail.
    assert.deepEqual(plugin.RAIL_LABEL_STYLE, { textAlign: 'start', writingMode: 'vertical-rl' })
  })
})
