// Офлайн-тест plexus-dsh-warm: чистые функции и apply на поддельном ctx, таймеры — mock.timers.
// Без DSH и без сети. Журнал пинга — во временную папку.
//   cd packages/plexus-dsh-warm && pnpm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, Config, contextOf, usdOf, isTurnRequest, pingRequest, loadProviders } from '../lib/index.js'

test('contextOf: вход + чтение + запись кэша, пропуски — нули', () => {
  assert.equal(contextOf({ inputTokens: 10, cacheReadTokens: 200, cacheWriteTokens: 30, outputTokens: 99 }), 240)
  assert.equal(contextOf({}), 0)
  assert.equal(contextOf({ cacheReadTokens: 5 }), 5)
})

test('usdOf: цена за 1M по четырём полям', () => {
  const u = { inputTokens: 1_000_000, cacheReadTokens: 2_000_000, cacheWriteTokens: 1_000_000, outputTokens: 500_000 }
  assert.equal(usdOf(u, [1, 0.1, 2, 4]), 1 + 0.2 + 2 + 2)
  assert.equal(usdOf({}, [1, 1, 1, 1]), 0)
})

test('isTurnRequest: ход — есть sessionId и нет purpose', () => {
  assert.equal(isTurnRequest({ sessionId: 's', messages: [] }), true)
  assert.equal(isTurnRequest({ sessionId: 's', purpose: 'title', messages: [] }), false)
  assert.equal(isTurnRequest({ messages: [] }), false)
})

test('pingRequest: тот же запрос, maxTokens 1, свой signal, новый объект', () => {
  const req = { sessionId: 's', model: 'm', messages: [1, 2], maxTokens: 4096, signal: new AbortController().signal }
  const own = new AbortController().signal
  const ping = pingRequest(req, own)
  assert.notEqual(ping, req)
  assert.equal(ping.maxTokens, 1)
  assert.equal(ping.signal, own)
  assert.equal(ping.messages, req.messages)
  assert.equal(ping.model, 'm')
  assert.equal(req.maxTokens, 4096)
})

test('loadProviders: providers.json пакета — таблица с параметрами моделей', () => {
  const p = loadProviders()
  const keys = Object.keys(p)
  assert.ok(keys.length > 0)
  for (const k of keys) {
    assert.equal(typeof p[k].ttlMin, 'number')
    assert.equal(typeof p[k].pingEveryMin, 'number')
    assert.equal(p[k].price.length, 4)
  }
})

const MIN = 60_000

function fakeCtx() {
  const h = { on: {}, streams: [], disposers: [] }
  const ctx = {
    logger: () => ({ info() {}, warn() {}, debug() {} }),
    get: () => undefined,
    on: (ev, fn) => { h.on[ev] = fn },
    effect: (fn) => { h.disposers.push(fn()) },
    llm: {
      stream(req) {
        h.streams.push(req)
        return (async function* () {
          yield { type: 'usage', usage: { inputTokens: 100, cacheReadTokens: 40_000, cacheWriteTokens: 0, outputTokens: 1 } }
        })()
      },
    },
  }
  return { ctx, h }
}

/** Ход: llm/stream с usage на `context` токенов; поток дочитывается. */
async function turn(h, request, context) {
  const out = h.on['llm/stream'](request, () => (async function* () {
    yield { type: 'usage', usage: { inputTokens: context } }
  })())
  for await (const _ of out) { /* дочитать */ }
}
const agent = (id) => ({ agent: { session: { id } } })
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)) }
const request = { sessionId: 's1', provider: 'p', model: 'неизвестная', messages: [{ role: 'user' }], maxTokens: 4096 }

function setup(extra = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'warm-'))
  const log = join(dir, 'usage.jsonl')
  const { ctx, h } = fakeCtx()
  apply(ctx, Config({ live: true, pingEveryMin: 1, maxPauseMin: 10, log, ...extra }))
  return { h, log }
}

test('turn-stopping: таймер взведён, по паузе ctx.llm.stream с maxTokens 1, строка kind: ping', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_700_000_000_000 })
  const { h, log } = setup()
  await turn(h, request, 50_000)
  h.on['agent/turn-stopping'](agent('s1'))
  assert.equal(h.streams.length, 0)
  t.mock.timers.tick(MIN - 1)
  await flush()
  assert.equal(h.streams.length, 0, 'до паузы пинга нет')
  t.mock.timers.tick(1)
  await flush()
  assert.equal(h.streams.length, 1)
  assert.equal(h.streams[0].maxTokens, 1)
  assert.equal(h.streams[0].messages, request.messages)
  assert.notEqual(h.streams[0].signal, undefined)
  const row = JSON.parse(readFileSync(log, 'utf8').trim().split('\n')[0])
  assert.equal(row.kind, 'ping')
  assert.equal(row.session, 's1')
  assert.equal(row.contextTokens, 40_100)
})

test('pre-step гасит таймер: stream не зовётся', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_700_000_000_000 })
  const { h } = setup()
  await turn(h, request, 50_000)
  h.on['agent/turn-stopping'](agent('s1'))
  await h.on['agent/pre-step'](agent('s1'), () => {})
  t.mock.timers.tick(5 * MIN)
  await flush()
  assert.equal(h.streams.length, 0)
})

test('контекст < minContextTokens: таймер не взводится', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_700_000_000_000 })
  const { h } = setup()
  await turn(h, request, 1_000)
  h.on['agent/turn-stopping'](agent('s1'))
  t.mock.timers.tick(5 * MIN)
  await flush()
  assert.equal(h.streams.length, 0)
})

test('session/disposed гасит таймер', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_700_000_000_000 })
  const { h } = setup()
  await turn(h, request, 50_000)
  h.on['agent/turn-stopping'](agent('s1'))
  h.on['session/disposed']({ id: 's1' })
  t.mock.timers.tick(5 * MIN)
  await flush()
  assert.equal(h.streams.length, 0)
})

test('live: false — stream не зовётся, журнал не пишется', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1_700_000_000_000 })
  const { h, log } = setup({ live: false })
  await turn(h, request, 50_000)
  h.on['agent/turn-stopping'](agent('s1'))
  t.mock.timers.tick(MIN)
  await flush()
  assert.equal(h.streams.length, 0)
  assert.equal(existsSync(log), false)
})
