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
    const [бандл, плексус] = индекс.plugins
    assert.equal(индекс.plugins.length, 2)
    assert.equal(бандл.name, '@plexus-dsh/bundle')
    assert.deepEqual(бандл.versions[0].source, { type: 'npm' })
    assert.equal(бандл.versions[0].version, '0.1.1')
    assert.equal(бандл.versions[0].publishTimestamp, Math.floor(Date.parse('2026-11-24T12:00:00.000Z') / 1000))
    assert.equal(бандл.versions[0].engines.dsh, '0.2.2-rc.5')
    assert.equal(бандл.title.en, 'Plexus DSH')
    assert.ok(бандл.description.ru)
    assert.match(бандл.icon, /^data:image\/svg\+xml;base64,/)
    assert.equal(плексус.name, '@plexus-dsh/plexus')
    assert.equal(плексус.versions[0].version, '2.11.3')
    assert.deepEqual(плексус.versions[0].source, { type: 'npm' })
    assert.equal(плексус.versions[0].engines.dsh, '0.2.2-rc.5')
    assert.equal(плексус.versions[0].publishTimestamp, Math.floor(Date.parse('2026-11-25T18:41:24.892Z') / 1000))
    assert.ok(!JSON.stringify(индекс).includes('plexus-workers'))
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

test('npm 404 на @plexus-dsh/plexus — плагин пропущен, предупреждение в stderr, код 0', () => {
  const к = копия()
  try {
    rmSync(join(к.сеть, 'npm-view-plexus.json'))
    const р = собрать(к)
    assert.equal(р.status, 0, р.stderr)
    assert.match(р.stderr, /@plexus-dsh\/plexus.*404/)
    const индекс = JSON.parse(readFileSync(к.выход, 'utf8'))
    assert.deepEqual(индекс.plugins.map((п) => п.name), ['@plexus-dsh/bundle'])
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

test('без engines.dsh у Плексуса — код 1', () => {
  const к = копия()
  try {
    writeFileSync(join(к.сеть, 'npm-view-plexus.json'), JSON.stringify({
      name: '@plexus-dsh/plexus', version: '2.11.3', time: { '2.11.3': '2026-11-25T18:41:24.892Z' },
    }))
    const р = собрать(к)
    assert.equal(р.status, 1)
    assert.match(р.stderr, /engines\.dsh/)
  } finally { rmSync(к.каталог, { recursive: true, force: true }) }
})

test('--проверка: источник с коротким SHA — код 1', () => {
  const к = копия()
  try {
    assert.equal(собрать(к).status, 0)
    const индекс = JSON.parse(readFileSync(к.выход, 'utf8'))
    индекс.plugins[1].versions[0].source = { type: 'git', url: 'https://github.com/x/y', commit: 'abc1234' }
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
