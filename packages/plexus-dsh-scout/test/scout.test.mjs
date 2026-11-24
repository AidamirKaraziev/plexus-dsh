// Офлайн-тест plexus-dsh-scout: обрезка брифа до 3K и итога до 2K, guard «только чтение»,
// строка kind: scout в журнале. Без DSH и без сети — ctx подделан.
//   cd packages/plexus-dsh-scout && pnpm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, clip, tokens, denyForScout, childPrompt, профиль } from '../lib/index.js'

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

function fakeCtx(output) {
  const h = { tools: [], guards: [], on: {}, starts: [], sections: [] }
  const ctx = {
    logger: () => ({ info() {}, warn() {}, debug() {} }),
    on: (ev, fn) => { h.on[ev] = fn },
    tools: { register: (t) => { h.tools.push(t); return () => {} }, guard: (g) => { h.guards.push(g); return () => {} } },
    systemPrompt: { section: (s) => { h.sections.push(s); return () => {} }, getSectionOrder: () => 1500 },
    subagents: {
      async startActivation(spec) {
        h.starts.push(spec)
        const session = { id: spec.childId }
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

test('scout: ребёнок (глубина > 0) разведчика не зовёт и подсказки не видит', async () => {
  const { ctx, h } = fakeCtx('ok')
  apply(ctx, { log: join(mkdtempSync(join(tmpdir(), 'scout-')), 'u.jsonl') })
  const child = { session: { id: 'c', header: { delegationDepth: 1 } }, options: {} }
  assert.equal(h.sections[0].text({ agent: child }), '')
  await assert.rejects(h.tools[0].execute({ brief: 'x' }, { agent: child, signal: new AbortController().signal }), /не запускает/)
})
