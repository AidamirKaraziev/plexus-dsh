// Офлайн-тест чтения журнала учёта: каталог данных продукта и строки имя('meter').
//   cd packages/имя('budget')} && pnpm test

import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { pluginDirs, readMeter, usageLogs } from './usage.js'
import { КАТАЛОГ, имя } from './brand.js'

const row = (o: object) => JSON.stringify(o) + '\n'

test('pluginDirs: один каталог продукта', () => {
  assert.deepEqual(pluginDirs('/tmp/me'), [join('/tmp/me', КАТАЛОГ)])
})

test('usageLogs: свой config.log один; иначе существующий журнал каталога продукта', () => {
  const home = mkdtempSync(join(tmpdir(), 'usage-'))
  assert.deepEqual(usageLogs(undefined, home), [])
  mkdirSync(join(home, КАТАЛОГ))
  writeFileSync(join(home, КАТАЛОГ, 'usage.jsonl'), '')
  assert.deepEqual(usageLogs(undefined, home), [join(home, КАТАЛОГ, 'usage.jsonl')])
  assert.deepEqual(usageLogs('/tmp/own.jsonl', home), ['/tmp/own.jsonl'])
})

test('readMeter: последняя по (ts, session, turn) из всех файлов; чужие строки мимо', () => {
  const home = mkdtempSync(join(tmpdir(), 'usage-'))
  mkdirSync(join(home, КАТАЛОГ))
  const первый = join(home, КАТАЛОГ, 'usage.jsonl')
  const второй = join(home, 'own.jsonl')
  const t1 = '2026-11-20T10:00:00.000Z', t2 = '2026-11-20T11:00:00.000Z'
  writeFileSync(второй, row({ ts: t1, pkg: имя('meter'), session: 's1', turn: 1, session_usd: 1, window_pct: 10 })
    + row({ ts: t1, pkg: имя('meter'), session: 's1', turn: 2, session_usd: 2, window_pct: 20 }))
  writeFileSync(первый, row({ ts: t2, pkg: имя('meter'), session: 's1', turn: 1, session_usd: 3, window_pct: 30 }))

  const both = readMeter([первый, второй], 's1')
  assert.equal(both.last?.ts, t2, 'новейшая строка')
  assert.equal(both.last?.window_pct, 30)
  assert.equal(both.mine?.ts, t2, 'по сессии тоже новейшая')

  // одинаковый (ts, session, turn) — выигрывает файл, что раньше в списке
  writeFileSync(второй, row({ ts: t2, pkg: имя('meter'), session: 's1', turn: 1, window_pct: 99 }))
  writeFileSync(первый, row({ ts: t2, pkg: имя('meter'), session: 's1', turn: 1, window_pct: 30 }))
  assert.equal(readMeter([первый, второй], 's1').last?.window_pct, 30)

  // метка закрытия (kind: stop), строки других имён и чужие пакеты — мимо; нет файлов — пусто
  writeFileSync(второй, row({ ts: t1, pkg: имя('meter'), session: 's1', turn: 1, window_pct: 10 })
    + row({ ts: t2, pkg: 'другой-meter', session: 's1', turn: 9, window_pct: 77 }))
  writeFileSync(первый, row({ ts: t2, pkg: имя('budget'), session: 's1', turn: 1, kind: 'stop' }))
  assert.equal(readMeter([первый, второй], 's1').last?.window_pct, 10, 'строка бюджета и чужого пакета — не окно')
  assert.deepEqual(readMeter([join(home, 'нет.jsonl')], 's1'), { last: undefined, mine: undefined })
})
