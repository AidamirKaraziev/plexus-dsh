// Офлайн-тест plexus-dsh-hello: на конец хода в журнал сессии пишется ровно одно событие.
// Без DSH и без сети — ctx подделан.
//   cd packages/plexus-dsh-hello && pnpm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { apply } from '../lib/index.js'
import { ПАКЕТ } from '../lib/types.js'

function fakeCtx() {
  const handlers = {}
  const warns = []
  return {
    handlers,
    warns,
    on: (event, fn) => { (handlers[event] ??= []).push(fn) },
    logger: () => ({ warn: (...a) => warns.push(a) }),
  }
}

test('apply: подписка на agent/turn-stopping, на ход ровно один append', () => {
  const ctx = fakeCtx()
  apply(ctx, { label: 'проба' })
  assert.deepEqual(Object.keys(ctx.handlers), ['agent/turn-stopping'])
  assert.equal(ctx.handlers['agent/turn-stopping'].length, 1)

  const calls = []
  const agent = { session: { append: (...a) => calls.push(a) } }
  ctx.handlers['agent/turn-stopping'][0]({ agent, turn: 7 })

  assert.equal(calls.length, 1)
  const [key, data] = calls[0]
  assert.equal(key, ПАКЕТ)
  assert.match(key, /^plexus-dsh.hello$/)
  assert.deepEqual(data, { pkg: 'plexus-dsh-hello', version: '0.1.0', turn: 7, label: 'проба' })
})

test('apply: без метки поле label не пишется', () => {
  const ctx = fakeCtx()
  apply(ctx, {})
  const calls = []
  ctx.handlers['agent/turn-stopping'][0]({ agent: { session: { append: (...a) => calls.push(a) } }, turn: 1 })
  assert.equal(calls.length, 1)
  assert.equal('label' in calls[0][1], false)
})

test('apply: сбой append не валит ход — предупреждение в лог', () => {
  const ctx = fakeCtx()
  apply(ctx, {})
  const agent = { session: { append: () => { throw new Error('диск') } } }
  assert.doesNotThrow(() => ctx.handlers['agent/turn-stopping'][0]({ agent, turn: 1 }))
  assert.equal(ctx.warns.length, 1)
})
