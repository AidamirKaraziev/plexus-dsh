// Офлайн-тест plexus-dsh-scout: обрезка брифа до 3K и итога до 2K, guard «только чтение»,
// строка kind: scout в журнале. Без DSH и без сети — ctx подделан.
//   cd packages/plexus-dsh-scout && pnpm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, clip, tokens, denyForScout, childPrompt, профиль, выборМодели, разрешённые, очередьМоделей, ключ, ценаМодели } from '../lib/index.js'

const big = (n) => Array.from({ length: n }, (_, i) => `строка ${i} ` + 'x'.repeat(60)).join('\n')

test('clip: короткий текст не трогает, длинный режет до потолка с пометкой', () => {
  assert.equal(clip('коротко', 3000, 'бриф'), 'коротко')
  const out = clip(big(1000), 3000, 'бриф')
  assert.ok(tokens(out) <= 3000, `≤ 3000, а ${tokens(out)}`)
  assert.match(out, /plexus-dsh-scout: бриф ≈\d+ ток\. обрезан до 3000/)
})

test('guard: чтение можно, запись и прочее — отказ', () => {
  for (const t of ['read', 'grep', 'glob']) assert.equal(denyForScout(t), undefined)
  for (const t of ['write', 'edit', 'bash', 'str_replace_editor', 'subagent', 'scout']) assert.match(denyForScout(t), /закрыт/)
})

test('профиль: явная метка главнее, иначе имя профиля DSH; {profile} — заглушка', () => {
  const ctx = { get: (n) => (n === 'profileContext' ? { name: 'проба' } : undefined) }
  assert.equal(профиль(ctx, 'e05'), 'e05')
  assert.equal(профиль(ctx, undefined), 'проба')
  assert.equal(профиль(ctx, '{profile}'), 'проба')
  assert.equal(профиль({ get: () => undefined }, undefined), undefined)
})

function fakeCtx(output, { providers, падает } = {}) {
  const h = { tools: [], guards: [], on: {}, starts: [], sections: [] }
  const ctx = {
    get: (n) => (n === 'llm' && providers ? { listProviders: () => providers.map(id => ({ id })) } : undefined),
    logger: () => ({ info() {}, warn() {}, debug() {} }),
    on: (ev, fn) => { h.on[ev] = fn },
    tools: { register: (t) => { h.tools.push(t); return () => {} }, guard: (g) => { h.guards.push(g); return () => {} } },
    systemPrompt: { section: (s) => { h.sections.push(s); return () => {} }, getSectionOrder: () => 1500 },
    subagents: {
      async startActivation(spec) {
        h.starts.push(spec)
        const session = { id: spec.childId }
        if (h.приСтарте) await h.приСтарте(spec)
        // своя модель недоступна: ребёнок падает до первого шага
        if (падает && spec.agentOptions.provider === падает) {
          return { childId: spec.childId, name: 'agent-x', result: Promise.resolve({ output: [], stopReason: 'error' }), dispose: async () => {} }
        }
        // ребёнок пытается писать — guard должен отказать
        h.denied = h.guards[0]({ name: 'write', agent: { session } })
        h.allowed = h.guards[0]({ name: 'read', agent: { session } })
        // два шага ребёнка с usage
        for (const usage of [{ inputTokens: 6000, outputTokens: 100 }, { inputTokens: 500, cacheReadTokens: 6000, outputTokens: 900 }]) {
          h.on['session/event'](session, { type: 'assistant/message', data: { usage, message: { source: { model: 'glm-5.3-flash' } } } })
        }
        return { childId: spec.childId, name: 'agent-1', result: Promise.resolve({ output: [{ type: 'text', text: output }], stopReason: 'completed' }), dispose: async () => {} }
      },
    },
  }
  return { ctx, h }
}

const parent = { session: { id: 'session-parent', header: {} }, options: {} }

test('scout: бриф ≤ 3K, итог ≤ 2K, ребёнок только читает, строка kind: scout', async () => {
  const log = join(mkdtempSync(join(tmpdir(), 'scout-')), 'usage.jsonl')
  const { ctx, h } = fakeCtx(big(500))
  apply(ctx, { log, profile: 'test' })
  const tool = h.tools.find(t => t.name === 'scout')
  assert.ok(tool, 'scout зарегистрирован')
  assert.match(h.sections[0].text({ agent: parent }), /Ищи и читай через `scout`/)
  assert.match(h.sections[0].text({ agent: parent }), /grep -rn.*sed -n.*Файл, который правишь, читай сам/)

  const brief = big(1000)
  const out = await tool.execute({ brief }, { agent: parent, signal: new AbortController().signal })

  const spec = h.starts[0]
  assert.equal(spec.delivery, 'caller')
  assert.equal(spec.provider, 'spawn')
  assert.deepEqual([spec.agentOptions.provider, spec.agentOptions.model], ['opencode-go', 'glm-5.3-flash'])
  const sent = spec.prompt[0].text
  const overhead = tokens(childPrompt('', 2000))
  assert.ok(tokens(sent) <= 3000 + overhead, `бриф ребёнку ${tokens(sent)} > 3000 + ${overhead}`)
  assert.match(sent, /бриф ≈\d+ ток\. обрезан до 3000/)

  assert.ok(tokens(out) <= 2000, `итог ${tokens(out)} > 2000`)
  assert.match(out, /итог ≈\d+ ток\. обрезан до 2000/)

  assert.match(h.denied, /закрыт/)
  assert.equal(h.allowed, undefined)
  // после ребёнка guard его больше не держит
  assert.equal(h.guards[0]({ name: 'write', agent: { session: { id: spec.childId } } }), undefined)

  const rows = readFileSync(log, 'utf8').trim().split('\n').map(l => JSON.parse(l))
  assert.equal(rows.length, 1)
  const r = rows[0]
  assert.equal(r.kind, 'scout')
  assert.equal(r.session, 'session-parent')
  assert.equal(r.child, spec.childId)
  assert.equal(r.steps, 2)
  assert.equal(r.briefClipped, true)
  assert.equal(r.resultClipped, true)
  // 6500 вход × 0,15 + 6000 кэш × 0,03 + 1000 выход × 0,5 за 1M
  assert.equal(r.usd, Math.round((6500 * 0.15 + 6000 * 0.03 + 1000 * 0.5) / 1e6 * 1e6) / 1e6)
  assert.match(readFileSync(log, 'utf8'), /"kind":"scout"/)
})

test('scout: субагент (глубина 1) зовёт разведчика и видит подсказку', async () => {
  const { ctx, h } = fakeCtx('ok')
  apply(ctx, { log: join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl') })
  const sub = { session: { id: 'c', header: { delegationDepth: 1 } }, options: {} }
  assert.match(h.sections[0].text({ agent: sub }), /Ищи и читай через `scout`/)
  assert.equal(await h.tools[0].execute({ brief: 'x' }, { agent: sub, signal: new AbortController().signal }), 'ok')
})

test('scout: callerMaxDepth 0 — субагенту отказ и без подсказки', async () => {
  const { ctx, h } = fakeCtx('ok')
  apply(ctx, { log: join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl'), callerMaxDepth: 0 })
  const sub = { session: { id: 'c', header: { delegationDepth: 1 } }, options: {} }
  assert.equal(h.sections[0].text({ agent: sub }), '')
  await assert.rejects(h.tools[0].execute({ brief: 'x' }, { agent: sub, signal: new AbortController().signal }), /до глубины 0, у тебя 1/)
  assert.equal(h.starts.length, 0)
})

test('scout: сам разведчик разведчика не зовёт и подсказки не видит', async () => {
  const { ctx, h } = fakeCtx('ok')
  let изнутри
  h.приСтарте = async (spec) => {
    const сам = { session: { id: spec.childId, header: { delegationDepth: 1 } }, options: {} }
    изнутри = {
      подсказка: h.sections[0].text({ agent: сам }),
      вызов: await h.tools[0].execute({ brief: 'y' }, { agent: сам, signal: new AbortController().signal }).catch(e => e.message),
    }
  }
  apply(ctx, { log: join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl') })
  await h.tools[0].execute({ brief: 'x' }, { agent: parent, signal: new AbortController().signal })
  assert.equal(изнутри.подсказка, '')
  assert.match(изнутри.вызов, /не запускает разведчиков/)
  assert.equal(h.starts.length, 1)
})

test('список моделей: основная, затем models без пустых и повторов', () => {
  const r = разрешённые({ provider: 'opencode-go', model: 'glm-5.3-flash', models: [
    { provider: 'openai-codex', model: 'gpt-6-luna' }, { provider: 'opencode-go', model: 'glm-5.3-flash' }, { provider: '', model: 'x' },
  ] })
  assert.deepEqual(r.map(ключ), ['opencode-go/glm-5.3-flash', 'openai-codex/gpt-6-luna'])
  assert.deepEqual(разрешённые({ provider: '', model: '', models: [] }), [])
})

test('очередь: выбранная первой, без маршрута и сломанные — в причины', () => {
  const r = [{ provider: 'opencode-go', model: 'glm-5.3-flash' }, { provider: 'openai-codex', model: 'gpt-6-luna' }, { provider: 'openai-codex', model: 'gpt-6-sol' }]
  const есть = new Set(['openai-codex'])
  const q1 = очередьМоделей(r, есть, new Map())
  assert.deepEqual(q1.очередь.map(ключ), ['openai-codex/gpt-6-luna', 'openai-codex/gpt-6-sol'])
  assert.deepEqual(q1.причины, ['нет маршрута opencode-go'])
  const q2 = очередьМоделей(r, undefined, new Map([['openai-codex/gpt-6-luna', 'openai-codex/gpt-6-luna не ответила']]), 'openai-codex/gpt-6-sol')
  assert.deepEqual(q2.очередь.map(ключ), ['openai-codex/gpt-6-sol', 'opencode-go/glm-5.3-flash'])
  assert.deepEqual(q2.причины, ['openai-codex/gpt-6-luna не ответила'])
  assert.deepEqual(очередьМоделей(r, undefined, new Map(), 'x/y').причины, ['модель x/y не из списка'])
})

test('только Codex: Go нет — разведчик на модели из списка, не на модели родителя', async () => {
  const log = join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl')
  const { ctx, h } = fakeCtx('нашёл', { providers: ['openai-codex'] })
  apply(ctx, { log, profile: 'test', models: [{ provider: 'openai-codex', model: 'gpt-6-luna' }] })
  assert.equal(await h.tools[0].execute({ brief: 'найди X' }, { agent: родитель, signal: new AbortController().signal }), 'нашёл')
  assert.deepEqual([h.starts[0].agentOptions.provider, h.starts[0].agentOptions.model], ['openai-codex', 'gpt-6-luna'])
  const r = JSON.parse(readFileSync(log, 'utf8').trim())
  assert.deepEqual([r.provider, r.model, r.fallback], ['openai-codex', 'gpt-6-luna', 'нет маршрута opencode-go'])
})

test('выбор агентом: выключен — поля model нет и аргумент не действует', async () => {
  const { ctx, h } = fakeCtx('ok')
  apply(ctx, { log: join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl'), models: [{ provider: 'openai-codex', model: 'gpt-6-sol' }] })
  assert.equal(h.tools[0].parameters.properties.model, undefined)
  await h.tools[0].execute({ brief: 'x', model: 'openai-codex/gpt-6-sol' }, { agent: parent, signal: new AbortController().signal })
  assert.equal(h.starts[0].agentOptions.provider, 'opencode-go')
})

test('выбор агентом: включён — enum из разрешённых, выбранная идёт первой, chosen в журнале', async () => {
  const log = join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl')
  const { ctx, h } = fakeCtx('ok')
  apply(ctx, { log, modelSelection: true, models: [{ provider: 'openai-codex', model: 'gpt-6-sol' }] })
  const поле = h.tools[0].parameters.properties.model
  assert.deepEqual(поле.enum, ['opencode-go/glm-5.3-flash', 'openai-codex/gpt-6-sol'])
  assert.match(поле.description, /Не указывай — возьмётся opencode-go\/glm-5.3-flash/)
  await h.tools[0].execute({ brief: 'x', model: 'openai-codex/gpt-6-sol' }, { agent: parent, signal: new AbortController().signal })
  assert.deepEqual([h.starts[0].agentOptions.provider, h.starts[0].agentOptions.model], ['openai-codex', 'gpt-6-sol'])
  const r = JSON.parse(readFileSync(log, 'utf8').trim())
  assert.equal(r.chosen, 'openai-codex/gpt-6-sol')
})

test('цены: модели Codex в справочнике', () => {
  assert.deepEqual(ценаМодели('gpt-6-luna'), [0.1, 0.01, 0.125, 0.5])
  assert.ok(ценаМодели('gpt-6-sol'))
})

test('выбор модели: нет маршрута или модель пустая — модель родителя', () => {
  const cfg = { provider: 'opencode-go', model: 'glm-5.3-flash' }
  assert.deepEqual(выборМодели(cfg, undefined), cfg)
  assert.deepEqual(выборМодели(cfg, new Set(['opencode-go', 'deepseek'])), cfg)
  assert.match(выборМодели(cfg, new Set(['deepseek'])).причина, /нет маршрута opencode-go/)
  assert.ok('причина' in выборМодели({ provider: '', model: '' }, undefined))
})

const родитель = { session: { id: 'session-parent', header: {} }, options: { provider: 'deepseek', model: 'deepseek-v4' } }

test('scout без подписки Go: маршрута нет — ребёнок на модели родителя, без ошибки', async () => {
  const log = join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl')
  const { ctx, h } = fakeCtx('нашёл', { providers: ['deepseek'] })
  apply(ctx, { log, profile: 'test' })
  const out = await h.tools[0].execute({ brief: 'найди X' }, { agent: родитель, signal: new AbortController().signal })
  assert.equal(out, 'нашёл')
  assert.equal(h.starts.length, 1)
  assert.equal(h.starts[0].agentOptions.provider, undefined)
  assert.equal(h.starts[0].agentOptions.model, undefined)
  const r = JSON.parse(readFileSync(log, 'utf8').trim())
  assert.deepEqual([r.provider, r.model, r.fallback], ['deepseek', 'deepseek-v4', 'нет маршрута opencode-go'])
})

test('scout: своя модель упала до первого шага — повтор на модели родителя, дальше сразу родитель', async () => {
  const log = join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl')
  const { ctx, h } = fakeCtx('нашёл', { падает: 'opencode-go' })
  apply(ctx, { log, profile: 'test' })
  const exec = { agent: родитель, signal: new AbortController().signal }
  assert.equal(await h.tools[0].execute({ brief: 'найди X' }, exec), 'нашёл')
  assert.deepEqual(h.starts.map(s => s.agentOptions.provider), ['opencode-go', undefined])
  assert.equal(await h.tools[0].execute({ brief: 'найди Y' }, exec), 'нашёл')
  assert.equal(h.starts.length, 3)
  assert.equal(h.starts[2].agentOptions.provider, undefined)
  const rows = readFileSync(log, 'utf8').trim().split('\n').map(l => JSON.parse(l))
  assert.deepEqual(rows.map(r => r.stop), ['error', 'completed', 'completed'])
  assert.match(rows[1].fallback, /opencode-go\/glm-5.3-flash не ответила/)
})
