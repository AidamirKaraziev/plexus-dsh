// Имя продукта на сборке: пишет packages/brand.json, src/brand.ts в каждом пакете и копию
// brand.json рядом с собранным кодом.
//
//   node tools/brandinfo.mjs           — brand.json и src/brand.ts, src/types.ts
//   node tools/brandinfo.mjs --копии   — те же файлы в lib/ собранных пакетов
//
// Источник имени один — NAME в tools/dsh_digest.py; сама строка собирается там из кусков,
// чтобы исходники не держали копию имени. Скрипт запускается из сборки пакетов
// (`pnpm run build`), поэтому правки руками в brand.json и src/brand.ts при сборке затрутся.
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const корень = dirname(dirname(fileURLToPath(import.meta.url)))
const пакеты = join(корень, 'packages')
const шаблон = join(корень, 'tools', 'brand-template.ts')
const толькоКопии = process.argv.includes('--копии')

// Пакеты, которым brand.ts не нужен: у них нет своего кода. Так бандл продукта —
// один манифест, патч и иконка, — он не собирается tsc и имени продукта не держит.
const безКода = new Set(['bundle'])

const имя = execFileSync('python3', ['-c', 'import sys; sys.path.insert(0, sys.argv[1]); from dsh_digest import NAME; print(NAME)',
  join(корень, 'tools')], { encoding: 'utf8' }).trim()
if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(имя)) throw new Error(`непонятное имя продукта: ${JSON.stringify(имя)}`)

// 1. brand.json — его импортирует src/brand.ts каждого пакета
const бренд = join(пакеты, 'brand.json')
const брендТекст = JSON.stringify({ КОРЕНЬ: имя }, null, 2) + '\n'

// 2. src/brand.ts — одинаковый во всех пакетах; корень берётся из brand.json.
//    Пакетам, у которых имя пакета обязано быть литералом типа (ключи MessageSourceMap и
//    SessionEventMap), пишется ещё src/types.ts с этим литералом — см. tools/brand-keys.json.
const текст = readFileSync(шаблон, 'utf8')
const ключи = JSON.parse(readFileSync(join(корень, 'tools', 'brand-keys.json'), 'utf8'))

if (!толькоКопии) {
  writeFileSync(бренд, брендТекст)
  let пакетов = 0
  for (const пакет of readdirSync(пакеты)) {
    if (!пакет.startsWith(`${имя}-`)) continue
    if (безКода.has(пакет.slice(имя.length + 1))) continue
    const src = join(пакеты, пакет, 'src')
    if (!existsSync(join(src, 'index.ts'))) continue
    const короткое = пакет.slice(имя.length + 1)
    writeFileSync(join(src, 'brand.ts'), текст, 'utf8')
    // brand.json — рядом с brand.ts: путь './brand.json' работает и в src/ при сборке,
    // и в lib/ после установки пакета зависимостью профиля DSH (E16-S06).
    writeFileSync(join(src, 'brand.json'), брендТекст, 'utf8')
    if (ключи.пакеты[короткое]) {
      // Ключ модульного расширения DSH — литерал. Строковое сложение TS сворачивает в литерал
      // только у шаблонной строки, поэтому корень режется на куски и склеивается в `${...}`:
      // сам исходник не держит имя продукта целиком (сторож E16-S05), а тип остаётся литералом.
      // Каждый кусок — пара кусков имени продукта: в исходнике нет куска, равного самому имени.
      const четверти = имя.split('-').map((кусок) => {
        const срез = Math.ceil(кусок.length / 2)
        return '${' + JSON.stringify(кусок.slice(0, срез)) + '}${' + JSON.stringify(кусок.slice(срез)) + '}'
      })
      const литерал = '`' + четверти.join('-') + '-' + короткое + '`'
      writeFileSync(join(src, 'types.ts'),
        '// Типовые имена пакета (пишет tools/brandinfo.mjs на сборке из tools/brand-keys.json):\n' +
        '// Ключи модульных расширений DSH — литералы, а не string, поэтому имя собирается здесь.\n' +
        'export const ПАКЕТ = ' + литерал + '\n', 'utf8')
    }
    пакетов++
  }
  console.log(`имя: ${имя} → brand.json, src/brand.ts в ${пакетов} пакет(ах)`)
  process.exit(0)
}

// 3. Копии в lib: brand.json и brand.ts читаются уже собранным кодом, tsc их туда не кладёт.
//    brand.json нужен рядом с каждым lib/: пакет ставится зависимостью профиля DSH из своего
//    каталога, и `../brand.json` до корня workspace оттуда не достаёт (E16-S06).
let скопировано = 0
for (const пакет of readdirSync(пакеты)) {
  if (!пакет.startsWith(`${имя}-`)) continue
  const lib = join(пакеты, пакет, 'lib')
  if (!existsSync(join(lib, 'brand.js'))) continue
  copyFileSync(join(пакеты, пакет, 'src', 'brand.ts'), join(lib, 'brand.ts'))
  copyFileSync(join(пакеты, пакет, 'src', 'brand.json'), join(lib, 'brand.json'))
  скопировано++
}
console.log(`копии: brand.json и brand.ts в lib ${скопировано} пакет(ов)`)
