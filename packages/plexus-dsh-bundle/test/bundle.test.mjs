// Офлайн-тест plexus-dsh-bundle: состав бандла (cordis.patch.yml и dependencies) совпадает
// с каталогами packages/plexus-dsh-*, у которых plexus.bundle.build !== false.
//   cd packages/plexus-dsh-bundle && pnpm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const bundleDir = join(root, 'plexus-dsh-bundle')
const sorted = (a) => [...a].sort()

// id и имя npm каждого пакета, который должен входить в бандл
const ожидаемые = readdirSync(root, { withFileTypes: true })
  .filter((d) => d.isDirectory() && d.name.startsWith('plexus-dsh-') && d.name !== 'plexus-dsh-bundle')
  .filter((d) => existsSync(join(root, d.name, 'package.json')))
  .map((d) => JSON.parse(readFileSync(join(root, d.name, 'package.json'), 'utf8')))
  .filter((p) => p.plexus?.bundle?.build !== false)
  .map((p) => ({ id: p.plexus?.id ?? p.name, name: p.name }))

test('каталоги: в бандл входят meter, warm, read-cap, scout', () => {
  assert.deepEqual(sorted(ожидаемые.map((p) => p.id)), [
    'plexus-dsh-meter', 'plexus-dsh-read-cap', 'plexus-dsh-scout', 'plexus-dsh-warm',
  ])
})

test('cordis.patch.yml: id в патче = ровно пакеты с build !== false', () => {
  const yml = readFileSync(join(bundleDir, 'cordis.patch.yml'), 'utf8')
  const ids = [...yml.matchAll(/^\s*-\s*id:\s*(\S+)\s*$/gm)].map((m) => m[1])
  assert.deepEqual(sorted(ids), sorted(ожидаемые.map((p) => p.id)))
})

test('package.json: dependencies = ровно пакеты с build !== false', () => {
  const pkg = JSON.parse(readFileSync(join(bundleDir, 'package.json'), 'utf8'))
  assert.deepEqual(sorted(Object.keys(pkg.dependencies)), sorted(ожидаемые.map((p) => p.name)))
})
