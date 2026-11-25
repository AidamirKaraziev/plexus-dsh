// Тест генератора индекса маркета: без сети, на фикстурах.
// Запуск: node --test tools/test_индекс_маркета.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ТУТ = dirname(fileURLToPath(import.meta.url))
const СКРИПТ = join(ТУТ, 'индекс_маркета.mjs')
const ФИКСТУРЫ = join(ТУТ, 'фикстуры', 'маркет')
const SHA = '0123456789abcdef0123456789abcdef01234567'

// Свежая копия фикстур, чтобы каждый тест ломал своё.
function копия() {
  const каталог = mkdtempSync(join(tmpdir(), 'индекс-'))
  cpSync(ФИКСТУРЫ, каталог, { recursive: true })
  return { каталог, сеть: join(каталог, 'сеть'), бандл: join(каталог, 'бандл'), выход: join(каталог, 'market.json') }
}

function запуск(...арг) {
  return spawnSync(process.execPath, [СКРИПТ, ...арг], { encoding: 'utf8' })
}

function собрать(к) {
  return запуск('--выход', к.выход, '--сеть-из', к.сеть, '--бандл', к.бандл)
}

test('валидный индекс собирается и проходит --проверка', () => {
  const к = копия()
  try {
    const р = собрать(к)
    assert.equal(р.status, 0, р.stderr)
    const индекс = JSON.parse(readFileSync(к.выход, 'utf8'))
    assert.equal(indexFormat(индекс), 1)
    const [бандл, адаптер] = индекс.plugins
    assert.equal(бандл.name, '@plexus-dsh/bundle')
    assert.deepEqual(бандл.versions[0].source, { type: 'npm' })
    assert.equal(бандл.versions[0].version, '0.1.1')
    assert.equal(бандл.versions[0].publishTimestamp, Math.floor(Date.parse('2026-11-24T12:00:00.000Z') / 1000))
    assert.equal(бандл.versions[0].engines.dsh, '0.2.2-rc.5')
    assert.equal(бандл.title.en, 'Plexus DSH')
    assert.ok(бандл.description.ru)
    assert.match(бандл.icon, /^data:image\/svg\+xml;base64,/)
    assert.deepEqual(адаптер.versions[0].source, {
      type: 'git', url: 'https://github.com/AidamirKaraziev/plexus', commit: SHA, path: 'bundles/plexus-workers',
    })
    const п = запуск('--проверка', к.выход)
    assert.equal(п.status, 0, п.stderr)
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

const indexFormat = (и) => и.format

test('без engines.dsh у бандла — код 1', () => {
  const к = копия()
  try {
    writeFileSync(join(к.сеть, 'npm-view.json'), JSON.stringify({
      name: '@plexus-dsh/bundle', version: '0.1.1', time: { '0.1.1': '2026-11-24T12:00:00.000Z' },
    }))
    const р = собрать(к)
    assert.equal(р.status, 1)
    assert.match(р.stderr, /engines\.dsh/)
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

test('короткий SHA от ls-remote — код 1', () => {
  const к = копия()
  try {
    writeFileSync(join(к.сеть, 'ls-remote.txt'), '0123456\trefs/heads/main\n')
    const р = собрать(к)
    assert.equal(р.status, 1)
    assert.match(р.stderr, /SHA/)
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

test('нет bundles/plexus-workers/package.json по SHA — код 1', () => {
  const к = копия()
  try {
    writeFileSync(join(к.сеть, 'ls-remote.txt'), `${'f'.repeat(40)}\trefs/heads/main\n`)
    const р = собрать(к)
    assert.equal(р.status, 1)
    assert.match(р.stderr, /package\.json/)
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

test('--проверка: индекс с коротким SHA — код 1', () => {
  const к = копия()
  try {
    assert.equal(собрать(к).status, 0)
    const индекс = JSON.parse(readFileSync(к.выход, 'utf8'))
    индекс.plugins[1].versions[0].source.commit = 'abc1234'
    const файл = join(к.каталог, 'плохой.json')
    writeFileSync(файл, JSON.stringify(индекс))
    const р = запуск('--проверка', файл)
    assert.equal(р.status, 1)
    assert.match(р.stderr, /parseMarketIndex/)
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

test('без аргументов — код 1', () => {
  assert.equal(запуск().status, 1)
})
