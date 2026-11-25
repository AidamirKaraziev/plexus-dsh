// Офлайн-тест plexus-dsh-read-cap: estimate, cut, hint и перехват tools/post-execute.
// Без DSH и без сети — ctx подделан.
//   cd packages/plexus-dsh-read-cap && pnpm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { estimate, cut, hint, apply } from '../lib/index.js'

const big = (n) => Array.from({ length: n }, (_, i) => `строка ${i} ` + 'x'.repeat(60)).join('\n')
const note = (c, n) => `<<${c[0]}-${c[1]}/${n}>>`
const text = (t) => ({ type: 'text', text: t })

test('estimate: символы/4 + 4 на блок, не-текст по JSON', () => {
  assert.equal(estimate([]), 0)
  assert.equal(estimate([text('x'.repeat(40))]), 14)
  assert.equal(estimate([text('abc'), text('abcde')]), (1 + 4) + (2 + 4))
  const img = { type: 'image', data: 'zz' }
  assert.equal(estimate([img]), Math.ceil(JSON.stringify(img).length / 4) + 4)
})

test('cut: короткий текст не трогается', () => {
  assert.equal(cut('коротко', 100, 0.6, note), undefined)
  assert.equal(cut('x'.repeat(100), 100, 0.6, note), undefined)
})

test('cut: голова ≈ headShare, хвост остальное, границы по строкам', () => {
  const src = big(200)
  const lines = src.split('\n')
  const out = cut(src, 1000, 0.6, note)
  assert.ok(out)
  const m = out.match(/^([\s\S]*)<<(\d+)-(\d+)\/200>>([\s\S]*)$/)
  assert.ok(m, 'пометка в разрыве')
  const [, head, a, b, tail] = m
  const headLines = head.split('\n')
  assert.ok(head.length <= 600 && head.length > 600 - 80, `голова ≈ 600, а ${head.length}`)
  assert.ok(tail.length <= 401 && tail.length > 400 - 80, `хвост ≈ 400, а ${tail.length}`)
  // целые строки: голова — начало оригинала, хвост — конец
  assert.deepEqual(headLines.slice(0, -1), lines.slice(0, headLines.length - 1))
  const tailLines = tail.split('\n').filter(Boolean)
  assert.equal(tailLines.at(-1), lines.at(-1))
  assert.equal(Number(b), 200 - tailLines.length)
  assert.ok(Number(a) <= headLines.length + 1)
})

test('cut: одна гигантская строка режется посимвольно', () => {
  const src = 'a'.repeat(5000)
  const out = cut(src, 1000, 0.5, note)
  assert.ok(out)
  assert.ok(out.length < 1100)
  assert.ok(out.startsWith('a'.repeat(500)))
  assert.match(out, /<<1-1\/1>>/)
})

test('hint: строка с инструментом, порогом, вырезанными строками и советом', () => {
  const h = hint('read', 9000, 6000, [12, 340], 400)
  assert.match(h, /\[plexus-dsh-read-cap: результат read ≈9000 ток\. больше порога 6000; вырезаны строки 12–340 из 400\. /)
  assert.match(h, /offset\/limit/)
  assert.match(hint('grep', 1, 1, [1, 2], 3), /Сузь поиск/)
  assert.match(hint('bash', 1, 1, [1, 2], 3), /sed -n/)
})

function fakeCtx() {
  const h = {}
  const ctx = {
    logger: () => ({ info() {}, warn() {}, debug() {} }),
    on: (ev, fn) => { h[ev] = fn },
  }
  return { ctx, h }
}
const run = (h, name, content, extra = {}) =>
  h['tools/post-execute']({ name, callId: 'c1', parent: undefined, ...extra }, { content }, async () => ({ kind: 'accept' }))

test('apply: большой результат read режется до ≤ maxTokens', async () => {
  const { ctx, h } = fakeCtx()
  apply(ctx, { maxTokens: 1000, tools: ['read', 'grep', 'bash'], headShare: 0.6 })
  const content = [text(big(2000))]
  const d = await run(h, 'read', content)
  assert.equal(d.kind, 'accept')
  assert.ok(estimate(d.content) <= 1000, `≤ 1000, а ${estimate(d.content)}`)
  assert.ok(estimate(d.content) < estimate(content))
  assert.match(d.content[0].text, /plexus-dsh-read-cap: результат read ≈\d+ ток\. больше порога 1000; вырезаны строки \d+–\d+ из 2000/)
})

test('apply: маленький результат, чужой инструмент и дочерний вызов проходят без изменений', async () => {
  const { ctx, h } = fakeCtx()
  apply(ctx, { maxTokens: 1000, tools: ['read'], headShare: 0.6 })
  assert.deepEqual(await run(h, 'read', [text('мало')]), { kind: 'accept' })
  assert.deepEqual(await run(h, 'write', [text(big(2000))]), { kind: 'accept' })
  assert.deepEqual(await run(h, 'read', [text(big(2000))], { parent: 'p' }), { kind: 'accept' })
})
