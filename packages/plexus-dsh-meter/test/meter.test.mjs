// Офлайн-тест plexus-dsh-meter: цена модели, шаги журнала, профиль и перехват agent/turn-stopping.
// Без DSH и без сети — ctx подделан; всё, что пишется на диск, — во временной папке.
//   cd packages/plexus-dsh-meter && pnpm test
import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { zstdCompressSync } from 'node:zlib'
import { apply, ценаМодели, stepOf, stepsOfJournal, профиль, PRICES } from '../lib/index.js'

const usage = { inputTokens: 1000, cacheReadTokens: 2000, cacheWriteTokens: 500, outputTokens: 300 }
const msg = (model, u = usage, extra = {}) => ({ type: 'assistant/message', data: { usage: u, message: { source: { model } } }, ...extra })
// ручной расчёт для glm-5.3: [1.4, 0.26, 0.0, 4.4] за 1M
const GLM = (1000 * 1.4 + 2000 * 0.26 + 500 * 0 + 300 * 4.4) / 1e6
const round = (x) => Math.round(x * 1e4) / 1e4
const quiet = { info() {}, warn() {}, debug() {} }

test('ценаМодели: точное имя, префикс, неизвестная модель', () => {
  assert.deepEqual(ценаМодели('glm-5.3', PRICES), PRICES['glm-5.3'])
  assert.deepEqual(ценаМодели('claude-sonnet-5-5', PRICES), [3, 0.3, 3.75, 15])
  assert.deepEqual(ценаМодели('claude-opus-9', PRICES), [5, 0.5, 6.25, 50])
  assert.equal(ценаМодели('нет-такой-модели', PRICES), undefined)
  // точное совпадение в переданных ценах главнее префикса
  assert.deepEqual(ценаМодели('claude-sonnet-x', { 'claude-sonnet-x': [1, 2, 3, 4] }), [1, 2, 3, 4])
})

test('stepOf: шаг с usage → $, без usage и чужой тип → null', () => {
  const s = stepOf(msg('glm-5.3', usage, { time: 42 }), PRICES)
  assert.equal(s.model, 'glm-5.3')
  assert.equal(s.time, 42)
  assert.ok(Math.abs(s.usd - GLM) < 1e-12)
  assert.equal(stepOf({ type: 'assistant/message', data: {} }, PRICES), null)
  assert.equal(stepOf({ type: 'user/message', data: { usage } }, PRICES), null)
  assert.equal(stepOf(msg('нет-такой-модели'), PRICES).usd, 0)
})

test('stepsOfJournal: префикс до session/end-seed не считается', () => {
  const events = [
    { ...msg('glm-5.3'), seq: 1 },
    { type: 'session/end-seed', seq: 2, data: { inherited: true } },
    { ...msg('glm-5.3'), seq: 3 },
    { ...msg('glm-5.3'), seq: 4 },
    { type: 'other', seq: 5 },
  ]
  assert.deepEqual([...stepsOfJournal(events, PRICES).keys()], [3, 4])
  assert.equal(stepsOfJournal(events.slice(0, 1), PRICES).size, 1)
})

test('профиль: явная метка главнее, иначе имя профиля DSH; {profile} — заглушка', () => {
  const ctx = { get: (n) => (n === 'profileContext' ? { name: 'проба' } : undefined) }
  assert.equal(профиль(ctx, 'e05'), 'e05')
  assert.equal(профиль(ctx, undefined), 'проба')
  assert.equal(профиль(ctx, '{profile}'), 'проба')
  assert.equal(профиль({ get: () => undefined }, undefined), undefined)
})

test('apply: на agent/turn-stopping в usage.jsonl одна строка, session_usd = ручной расчёт', (t) => {
  t.after(() => mock.timers.reset())
  const NOW = Date.parse('2026-01-01T12:00:00Z')
  mock.timers.enable({ apis: ['Date'], now: NOW })
  const dir = mkdtempSync(join(tmpdir(), 'meter-'))
  const sessionsDir = join(dir, 'sessions')
  mkdirSync(sessionsDir)
  const log = join(dir, 'out', 'usage.jsonl')

  const h = {}
  const ctx = {
    logger: () => quiet,
    on: (ev, fn) => { h[ev] = fn },
    get: (n) => (n === 'profileContext' ? { name: 'проба' } : undefined),
  }
  apply(ctx, { log, sessionsDir, windowHours: 5, budgetUsd: 12 })
  assert.equal(typeof h['agent/turn-stopping'], 'function')

  const session = { id: 'session-1' }
  const at = NOW - 3600_000
  h['session/event'](session, { ...msg('glm-5.3', usage, { time: at }), seq: 1 })
  h['session/event'](session, { ...msg('glm-5.3', usage, { time: at }), seq: 2 })
  h['session/event'](session, { ...msg('glm-5.3', usage, { time: NOW - 6 * 3600_000 }), seq: 3 }) // вне окна
  h['agent/turn-stopping']({ agent: { session }, turn: 7 })

  const rows = readFileSync(log, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
  assert.equal(rows.length, 1)
  const r = rows[0]
  for (const k of ['ts', 'session', 'turn', 'profile', 'session_usd', 'window_usd', 'window_pct', 'by_model']) assert.ok(k in r, `поле ${k}`)
  assert.equal(r.session, 'session-1')
  assert.equal(r.turn, 7)
  assert.equal(r.profile, 'проба')
  assert.equal(r.ts, new Date(NOW).toISOString())
  assert.equal(r.session_usd, round(3 * GLM)) // сессия считает все свои шаги
  assert.equal(r.window_usd, round(2 * GLM)) // окно — только два шага за 5 ч
  assert.equal(r.window_pct, Math.round(1000 * 2 * GLM / 12) / 10)
  assert.deepEqual(r.by_model, { 'glm-5.3': round(2 * GLM) })
})

test('apply: шаги с диска (sessionsDir) учитываются', (t) => {
  t.after(() => mock.timers.reset())
  const NOW = Date.parse('2026-01-01T12:00:00Z')
  mock.timers.enable({ apis: ['Date'], now: NOW })
  const dir = mkdtempSync(join(tmpdir(), 'meter-'))
  const jd = join(dir, 'sessions', 'cwd', 'sid')
  mkdirSync(jd, { recursive: true })
  const evs = [{ type: 'session/start', id: 'disk-1', seq: 0 }, { ...msg('glm-5.3', usage, { time: NOW - 1000 }), seq: 1 }]
  writeFileSync(join(jd, 'session.jsonl.zstd'), zstdCompressSync(Buffer.from(evs.map((e) => JSON.stringify(e)).join('\n') + '\n')))
  const log = join(dir, 'usage.jsonl')
  const h = {}
  apply({ logger: () => quiet, on: (ev, fn) => { h[ev] = fn }, get: () => undefined }, { log, sessionsDir: join(dir, 'sessions') })
  h['agent/turn-stopping']({ agent: { session: { id: 'disk-1' } }, turn: 1 })
  const r = JSON.parse(readFileSync(log, 'utf8').trim())
  assert.equal(r.session_usd, round(GLM))
  assert.equal(r.window_sessions, 1)
})
