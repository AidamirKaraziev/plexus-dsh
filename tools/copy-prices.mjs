// Копия справочника цен к собранным пакетам: tools/prices.json — единственный источник,
// packages/prices.json — копия для репозитория, lib/prices.json — то, что читает пакет.
import { copyFileSync, existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const пакеты = join(dirname(dirname(fileURLToPath(import.meta.url))), 'packages')
const { КОРЕНЬ } = JSON.parse(readFileSync(join(пакеты, 'brand.json'), 'utf8'))
const источник = join(пакеты, 'prices.json')
if (!existsSync(источник)) throw new Error('tools/copy-prices: нет packages/prices.json')

let скопировано = 0
for (const имя of readdirSync(пакеты)) {
  if (!имя.startsWith(`${КОРЕНЬ}-`)) continue
  const lib = join(пакеты, имя, 'lib')
  if (!existsSync(join(lib, 'index.js'))) continue
  copyFileSync(источник, join(lib, 'prices.json'))
  скопировано++
}
console.log(`цены: ${скопировано} пакет(ов) получили lib/prices.json`)
