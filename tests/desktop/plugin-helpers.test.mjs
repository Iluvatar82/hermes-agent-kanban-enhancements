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
import { cpSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
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
for (const name of ['Badge','Button','Codicon','ConfirmDialog','DropdownMenu','DropdownMenuContent',
  'DropdownMenuItem','DropdownMenuSeparator','DropdownMenuTrigger','EmptyState','ErrorState','Input',
  'Loader','Popover','PopoverContent','PopoverTrigger','ScrollArea','SearchField','StatusDot','Tip']) {
  Object.defineProperty(globalThis, name, { value: stub(name), configurable: true })
}
export const Badge = globalThis.Badge, Button = globalThis.Button, Codicon = globalThis.Codicon
export const ConfirmDialog = globalThis.ConfirmDialog, DropdownMenu = globalThis.DropdownMenu
export const DropdownMenuContent = globalThis.DropdownMenuContent, DropdownMenuItem = globalThis.DropdownMenuItem
export const DropdownMenuSeparator = globalThis.DropdownMenuSeparator
export const DropdownMenuTrigger = globalThis.DropdownMenuTrigger, EmptyState = globalThis.EmptyState
export const ErrorState = globalThis.ErrorState, Input = globalThis.Input, Loader = globalThis.Loader
export const Popover = globalThis.Popover, PopoverContent = globalThis.PopoverContent
export const PopoverTrigger = globalThis.PopoverTrigger, ScrollArea = globalThis.ScrollArea
export const SearchField = globalThis.SearchField, StatusDot = globalThis.StatusDot, Tip = globalThis.Tip
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
