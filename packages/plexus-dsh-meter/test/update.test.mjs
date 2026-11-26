// Офлайн-тест проверки новой версии: поддельный fetch, поддельный бандл во временной папке.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { проверитьОбновление } from '../lib/update.js'
import { apply } from '../lib/index.js'

const NOW = Date.parse('2026-01-10T12:00:00Z')
const СТРОКА = 'Вышел Plexus DSH %s (у вас %s) — обновите: Плагины → Установлено → Plexus DSH → Версии'

function стенд(версия = '1.0.0') {
  const dir = mkdtempSync(join(tmpdir(), 'upd-'))
  const бандл = join(dir, 'node_modules', '@plexus-dsh', 'bundle')
  const корень = join(бандл, 'node_modules', '@plexus-dsh', 'meter')
  mkdirSync(корень, { recursive: true })
  if (версия) writeFileSync(join(бандл, 'package.json'), JSON.stringify({ name: '@plexus-dsh/bundle', version: версия }))
  const файл = join(dir, 'data', 'update.json')
  const логи = []
  const log = { info: (...a) => логи.push(['info', ...a]), debug: (...a) => логи.push(['debug', ...a]) }
  return { dir, корень, файл, log, логи }
}
const ответ = (version) => async () => ({ ok: true, json: async () => ({ version }) })
function шпион(по) {
  const urls = []
  const f = async (url) => { urls.push(url); return по(url) }
  return { f, urls }
}
const упал = async () => { throw new Error('сеть') }
const прочесть = (файл) => JSON.parse(readFileSync(файл, 'utf8'))

test('нет файла + npm отвечает 9.9.9 → info и update.json', async () => {
  const s = стенд()
  const { f, urls } = шпион(ответ('9.9.9'))
  await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: f, now: NOW })
  assert.deepEqual(s.логи, [['info', СТРОКА, '9.9.9', '1.0.0']])
  assert.equal(urls.length, 1)
  const j = прочесть(s.файл)
  assert.equal(j.latest, '9.9.9'); assert.equal(j.installed, '1.0.0'); assert.equal(j.source, 'npm')
  assert.equal(j.checked_at, new Date(NOW).toISOString())
})

test('update.json старше 24 ч → проверка идёт', async () => {
  const s = стенд()
  mkdirSync(join(s.dir, 'data'))
  writeFileSync(s.файл, JSON.stringify({ checked_at: new Date(NOW - 25 * 3600_000).toISOString() }))
  const { f, urls } = шпион(ответ('9.9.9'))
  await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: f, now: NOW })
  assert.equal(urls.length, 1)
  assert.equal(прочесть(s.файл).latest, '9.9.9')
})

test('npm упал → спрашивается зеркало', async () => {
  const s = стенд()
  const { f, urls } = шпион((u) => (u.includes('npmjs') ? упал() : ответ('9.9.9')()))
  await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: f, now: NOW })
  assert.equal(urls.length, 2)
  assert.ok(urls[1].startsWith('https://registry.npmmirror.com/'))
  assert.equal(прочесть(s.файл).source, 'npmmirror')
  assert.equal(s.логи[0][0], 'info')
})

test('оба упали → нет info, checked_at записан', async () => {
  const s = стенд()
  const { f, urls } = шпион(упал)
  await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: f, now: NOW })
  assert.equal(urls.length, 2)
  assert.ok(!s.логи.some((l) => l[0] === 'info'))
  assert.equal(прочесть(s.файл).checked_at, new Date(NOW).toISOString())
})

test('свежий checked_at → ни одного запроса', async () => {
  const s = стенд()
  mkdirSync(join(s.dir, 'data'))
  writeFileSync(s.файл, JSON.stringify({ checked_at: new Date(NOW - 3600_000).toISOString() }))
  const { f, urls } = шпион(ответ('9.9.9'))
  await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: f, now: NOW })
  assert.equal(urls.length, 0)
  assert.deepEqual(s.логи, [])
})

test('версия не новее или пред-релиз → строки нет', async () => {
  for (const v of ['1.0.0', '0.9.0', '9.9.9-rc.1']) {
    const s = стенд()
    await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: шпион(ответ(v)).f, now: NOW })
    assert.ok(!s.логи.some((l) => l[0] === 'info'), v)
  }
})

test('бандл не найден → ни одного запроса, одна debug', async () => {
  const s = стенд(null)
  const { f, urls } = шпион(ответ('9.9.9'))
  await проверитьОбновление({ корень: s.корень, файл: s.файл, log: s.log, fetch: f, now: NOW })
  assert.equal(urls.length, 0)
  assert.equal(s.логи.length, 1); assert.equal(s.логи[0][0], 'debug')
  assert.ok(!existsSync(s.файл))
})

test('apply: updateCheck:false → ни одного запроса; по умолчанию — проверка в фоне', async () => {
  const quiet = { info() {}, warn() {}, debug() {} }
  for (const [updateCheck, ждём] of [[false, 0], [undefined, 1]]) {
    const s = стенд()
    const { f, urls } = шпион(ответ('9.9.9'))
    let готово
    const p = new Promise((r) => { готово = r })
    apply({ logger: () => quiet, on() {}, get: () => undefined },
      { log: join(s.dir, 'data', 'usage.jsonl'), updateCheck }, { корень: s.корень, fetch: f, готово })
    if (ждём) await p; else await new Promise((r) => setImmediate(r))
    assert.equal(urls.length, ждём)
    assert.equal(existsSync(s.файл), Boolean(ждём))
  }
})
