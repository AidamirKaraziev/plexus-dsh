// Офлайн-тест порогов имя('budget')}: `cd packages/имя('budget')} && pnpm test`.
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { apply, decide, readMeter } from './index.js'
import { имя } from './brand.js'

const cfg = { budgetUsd: 1, warnPct: 80, windowPct: 90, noProgressSteps: 0 }

test('decide: ниже 80 % — go', () => {
  assert.equal(decide({ sessionUsd: 0.5, idleSteps: 0 }, cfg).kind, 'go')
})
test('decide: 80 % бюджета — warn с «закругляйся»', () => {
  const d = decide({ sessionUsd: 0.8, idleSteps: 0 }, cfg)
  assert.equal(d.kind, 'warn')
  assert.match((d as any).text, /Закругляйся/)
})
test('decide: 100 % бюджета — stop budget', () => {
  const d = decide({ sessionUsd: 1, idleSteps: 0 }, cfg)
  assert.deepEqual([d.kind, (d as any).reason], ['stop', 'budget'])
})
test('decide: окно Go выше windowPct — stop window; ниже — нет', () => {
  const d = decide({ sessionUsd: 0.1, windowPct: 90, idleSteps: 0 }, cfg)
  assert.deepEqual([d.kind, (d as any).reason], ['stop', 'window'])
  assert.equal(decide({ sessionUsd: 0.1, windowPct: 89.9, idleSteps: 0 }, cfg).kind, 'go')
})
test('decide: budgetUsd 0 — бюджета нет', () => {
  assert.equal(decide({ sessionUsd: 99, idleSteps: 0 }, { ...cfg, budgetUsd: 0 }).kind, 'go')
})

// Мини-контекст: ловим обработчики, которые пакет вешает на ctx.
function harness(config: any) {
  const handlers: Record<string, Function> = {}
  const logger = { warn() {}, info() {}, debug() {}, error() {} }
  const ctx: any = { logger: () => logger, on: (n: string, f: Function) => { handlers[n] = f } }
  apply(ctx, config)
  const cancels: any[] = []
  const agent = { session: { id: 'S1' }, cancel: (...a: any[]) => cancels.push(a) }
  const session = { id: 'S1' }
  const spend = (usd: number) => handlers['session/event'](session, {
    type: 'assistant/message',
    data: { usage: { outputTokens: Math.round(usd / 4.4 * 1e6) }, message: { source: { model: 'glm-5.3' } } },
  })
  const step = (kind: 'user' | 'tool') => handlers['agent/pre-step'](
    { agent, turn: 1, step: 2, messages: [{ source: { kind } }] },
    async () => ({ kind: 'enter', messages: [{ source: { kind } }] }),
  )
  return { cancels, spend, step }
}

test('apply: warn один раз, стоп при 100 % со строкой kind:stop, человек проходит', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const log = join(dir, 'usage.jsonl')
  const h = harness({ budgetUsd: 0.05, log, profile: 't' })

  h.spend(0.045) // 90 %
  const r1: any = await h.step('tool')
  assert.equal(r1.kind, 'enter')
  assert.equal(r1.messages.at(-1).source.kind, имя('budget'))
  const r1b: any = await h.step('tool')
  assert.equal(r1b.messages.length, 1, 'предупреждение — один раз')

  h.spend(0.01) // 110 %
  const r2: any = await h.step('tool')
  assert.equal(r2.kind, 'reject')
  assert.equal(h.cancels.length, 1)
  assert.equal(h.cancels[0][0].kind, 'hook')
  assert.deepEqual(h.cancels[0][1], { keepInbox: true })
  const row = JSON.parse(readFileSync(log, 'utf8').trim().split('\n').at(-1)!)
  assert.equal(row.kind, 'stop')
  assert.equal(row.reason, 'budget')

  const r3: any = await h.step('user') // сообщение человека — не стопится
  assert.equal(r3.kind, 'enter')
  assert.equal(h.cancels.length, 1)
  assert.match(r3.messages.at(-1).content[0].text, /человек продолжил/)
})

test(`apply: окно Go по ${имя('meter')} стопит при windowPct`, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const log = join(dir, 'usage.jsonl')
  writeFileSync(log, JSON.stringify({ pkg: имя('meter'), session: 'other', session_usd: 1, window_usd: 9.5, budget_usd: 10, window_pct: 95 }) + '\n')
  assert.equal(readMeter(log, 'S1').last?.window_pct, 95)
  const h = harness({ log, windowPct: 90 })
  const r: any = await h.step('tool')
  assert.equal(r.kind, 'reject')
  assert.equal(JSON.parse(readFileSync(log, 'utf8').trim().split('\n').at(-1)!).reason, 'window')
})

// ── S03: стоп без прогресса. Записанный (синтетический) журнал шагов в форме событий DSH. ──
type Ev = { type: string; data: any }
const call = (step: number, id: string, name: string): Ev => ({ type: 'tool/call', data: { turn: 1, step, callId: id, name, arguments: '{}' } })
const result = (step: number, id: string, text: string, isError = false): Ev => ({
  type: 'tool/result',
  data: { turn: 1, step, message: { role: 'tool', source: { kind: 'tool', callId: id }, toolCallId: id, isError, content: [{ type: 'text', text }] } },
})
const end = (step: number): Ev => ({ type: 'step/end', data: { turn: 1, step } })

/** Проигрывает журнал; перед каждым шагом спрашивает pre-step. Возвращает номер шага, на котором стоп (или 0). */
async function replay(config: any, steps: Ev[][]) {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const log = join(dir, 'usage.jsonl')
  const handlers: Record<string, Function> = {}
  const logger = { warn() {}, info() {}, debug() {}, error() {} }
  apply({ logger: () => logger, on: (n: string, f: Function) => { handlers[n] = f } } as any, { log, ...config })
  const agent = { session: { id: 'S1' }, cancel() {} }
  for (const [i, evs] of steps.entries()) {
    const r: any = await handlers['agent/pre-step'](
      { agent, turn: 1, step: i + 1, messages: [{ source: { kind: 'tool' } }] },
      async () => ({ kind: 'enter', messages: [{ source: { kind: 'tool' } }] }),
    )
    if (r.kind === 'reject') return { stopAt: i + 1, row: JSON.parse(readFileSync(log, 'utf8').trim().split('\n').at(-1)!) }
    for (const e of evs) handlers['session/event']({ id: 'S1' }, e)
  }
  return { stopAt: 0, row: undefined as any }
}

test('S03: петля чтений без прогресса — стоп перед шагом noProgressSteps+2', async () => {
  const steps = [
    [call(1, 'a', 'write'), result(1, 'a', 'ok'), end(1)], // правка — прогресс
    ...[2, 3, 4].map(n => [call(n, `r${n}`, 'read'), result(n, `r${n}`, `файл ${n}`), end(n)]), // 3 шага без прогресса
    [call(5, 'x', 'read'), result(5, 'x', 'ещё'), end(5)],
  ]
  const r = await replay({ noProgressSteps: 3 }, steps)
  assert.equal(r.stopAt, 5, 'шаги 2–4 пустые → перед шагом 5 стоп')
  assert.equal(r.row.reason, 'no_progress')
  assert.equal(r.row.kind, 'stop')
  assert.equal(r.row.idleSteps, 3)
})

test('S03: петля одной ошибки bash — стоп после 3-го повтора', async () => {
  const err = (n: number, line: number) => [call(n, `c${n}`, 'bash'), result(n, `c${n}`, `Error: cannot find module 'x' at line ${line}`, true), end(n)]
  const r = await replay({ noProgressSteps: 8 }, [err(1, 10), err(2, 11), err(3, 12), err(4, 13)])
  assert.equal(r.stopAt, 4, 'после третьего повтора — перед шагом 4')
  assert.match(r.row.detail, /одна ошибка 3 раз/)
})

test('S03: правки файлов и новый вывод — прогресс; тот же вывод без правок — нет', async () => {
  const steps: Ev[][] = []
  for (let n = 1; n <= 12; n++) {
    steps.push(n % 2
      ? [call(n, `b${n}`, 'bash'), result(n, `b${n}`, `тесты: упало ${'x'.repeat(n)}`, true), end(n)]
      : [call(n, `e${n}`, 'edit'), result(n, `e${n}`, 'ok'), end(n)])
  }
  assert.equal((await replay({ noProgressSteps: 2 }, steps)).stopAt, 0)
  const same = [1, 2, 3].map(n => [call(n, `s${n}`, 'bash'), result(n, `s${n}`, 'всё зелёно'), end(n)])
  const r = await replay({ noProgressSteps: 2, repeatErrors: 0 }, [...same, []])
  assert.equal(r.stopAt, 4, 'шаг 1 — новый вывод, шаги 2–3 — повтор → стоп перед шагом 4')
})

test('S03: noProgressSteps 0 и repeatErrors 0 — выключено', async () => {
  const steps = [1, 2, 3, 4, 5].map(n => [call(n, `r${n}`, 'bash'), result(n, `r${n}`, 'Error: тот же', true), end(n)])
  assert.equal((await replay({ noProgressSteps: 0, repeatErrors: 0 }, steps)).stopAt, 0)
})
