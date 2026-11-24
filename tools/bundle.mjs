#!/usr/bin/env node
// Пакеты продукта как бандл DSH: манифесты пакетов и патч бандла — из одного места.
//
//   node tools/bundle.mjs локали      — locale/en.json и locale/ru.json в каждом пакете
//   node tools/bundle.mjs манифесты   — dsh, engines, peerDependencies, exports, files в package.json
//   node tools/bundle.mjs патч        — packages/plexus-dsh-bundle/cordis.patch.yml из манифестов
//   node tools/bundle.mjs             — всё три шага по порядку
//
// Откуда что берётся:
//  - имя продукта — tools/dsh_digest.py (NAME), версия рантайма DSH — tools/runtime.json;
//  - текст для человека (title, description, detail, поля формы) — tools/bundle-text.json;
//  - плюмбинг (dsh, engines, exports, files, plexus) — здесь, один на все пакеты.
//
// Почему генератор, а не руки: у шести манифестов одни и те же `dsh`/`engines`/`exports`/`files`;
// расхождение «в пяти есть, в шестом забыли» видно только проверкой, а не на глаз.
//
// Манифест правится точечно: порядок чужих полей сохраняется, новые встают рядом со `description`.
// `cortex` в манифестах больше не пишется — его место занял `plexus`.
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const корень = dirname(dirname(fileURLToPath(import.meta.url)))
const пакеты = join(корень, 'packages')
const рантайм = JSON.parse(readFileSync(join(корень, 'tools', 'runtime.json'), 'utf8'))
const текст = JSON.parse(readFileSync(join(корень, 'tools', 'bundle-text.json'), 'utf8'))

/** Имя продукта: единственный источник — tools/dsh_digest.py. */
const имя = execFileSync('python3', ['-c',
  'import sys; sys.path.insert(0, sys.argv[1]); from dsh_digest import NAME; print(NAME)',
  join(корень, 'tools')], { encoding: 'utf8' }).trim()
if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(имя)) throw new Error(`непонятное имя продукта: ${JSON.stringify(имя)}`)

/** Пакет, который несёт патч: сам манифест бандла, членом бандла не является. */
const бандл = 'bundle'

// Порядок строк в профиле: сначала учёт (meter), потом то, что меняет поведение модели.
// Так в журнале видно окно до того, как кэш и обрезка начнут его менять.
const ПОРЯДОК = ['meter', 'warm', 'read-cap', 'scout']

/** Короткие имена пакетов продукта: `plexus-dsh-meter` → `meter`. */
const короткие = readdirSync(пакеты)
  .filter((п) => п.startsWith(`${имя}-`) && existsSync(join(пакеты, п, 'package.json')))
  .map((п) => п.slice(имя.length + 1))
  .filter((п) => п !== бандл)
  .sort((а, б) => {
    const я = ПОРЯДОК.indexOf(а)
    const бя = ПОРЯДОК.indexOf(б)
    return (я < 0 ? ПОРЯДОК.length : я) - (бя < 0 ? ПОРЯДОК.length : бя) || а.localeCompare(б)
  })

/** Каталог пакета по короткому имени. */
function каталог(короткое) {
  return join(пакеты, `${имя}-${короткое}`)
}

/** Манифест пакета. */
function манифест(короткое) {
  return JSON.parse(readFileSync(join(каталог(короткое), 'package.json'), 'utf8'))
}

/** Вставить поля бандла сразу за `main`, сохранив порядок остальных ключей.
 *  Поля бандла главнее уже записанных: `files` и `exports` в старом манифесте тоже есть,
 *  и старая копия иначе встанет после новой и её перебьёт. */
function сПолями(манифест, поля) {
  const новый = {}
  for (const [k, v] of Object.entries(манифест)) {
    if (k in поля) continue
    if (k === 'cortex' || k === 'plexus') continue
    новый[k] = v
    if (k === 'main') Object.assign(новый, поля)
  }
  if (!('main' in манифест)) Object.assign(новый, поля)
  return новый
}

/** Локаль: `meta` для страницы Plugins и `config` для формы настроек. */
function локаль(короткое, язык) {
  const т = текст[короткое]?.[язык]
  if (!т?.title || !т?.description) throw new Error(`tools/bundle-text.json: у «${короткое}» нет ${язык}.title/description`)
  const поля = {}
  for (const [поле, подпись] of Object.entries(т.config ?? {})) {
    if (typeof подпись !== 'string' || подпись.trim() === '') throw new Error(`bundle: пустая подпись «${короткое}.${поле}.${язык}»`)
    поля[поле] = подпись
  }
  return { meta: { title: т.title, description: т.description, detail: т.detail ?? 'README.md' }, config: поля }
}

/** 1. Локали в каждом пакете. */
function локали() {
  for (const короткое of короткие) {
    const dir = join(каталог(короткое), 'locale')
    mkdirSync(dir, { recursive: true })
    for (const язык of ['en', 'ru']) {
      writeFileSync(join(dir, `${язык}.json`), JSON.stringify(локаль(короткое, язык), null, 2) + '\n', 'utf8')
    }
  }
  console.log(`локали: en и ru в ${короткие.length} пакет(ах)`)
}

/** 2. Поля бандла в манифестах пакетов. */
function манифесты() {
  for (const короткое of короткие) {
    const старый = манифест(короткое)
    const описание = текст[короткое]?.ru?.description
    if (!описание) throw new Error(`tools/bundle-text.json: у «${короткое}» нет ru.description`)
    // lib/*.json — brand.json и prices.json: собранный код читает их рядом с собой, а не из корня workspace.
    const файлы = [`lib/**/*.js`, `lib/**/*.d.ts`, `lib/**/*.ts`, `lib/*.json`, `src/**/*.ts`, `locale/*.json`, 'README.md']
    if (existsSync(join(каталог(короткое), 'providers.json'))) файлы.push('providers.json')
    if (existsSync(join(каталог(короткое), 'prices.json'))) файлы.push('prices.json')
    const новый = сПолями(старый, {
      description: описание,
      exports: {
        '.': { types: './lib/index.d.ts', default: './lib/index.js' },
        './locale/*.json': './locale/*.json',
        './package.json': './package.json',
      },
      files: файлы,
      engines: { dsh: рантайм.dsh },
      peerDependencies: { '@deepseek-ai/cordis': рантайм.cordis },
      // `plexus` уже собран (id, config, bundle.build) — при генерации он не переписывается:
      // build решает, попадает ли пакет в бандл, и от текста для человека не зависит.
      plexus: старый.plexus ?? { id: `${имя}-${короткое}`, config: {}, bundle: { build: true } },
    })
    writeFileSync(join(каталог(короткое), 'package.json'), JSON.stringify(новый, null, 2) + '\n', 'utf8')
  }
  console.log(`манифесты: поля бандла в ${короткие.length} пакет(ах)`)
}

/** 3. Патч бандла: строки `insert` для пакетов, у которых `plexus.bundle.build` не false. */
function патч() {
  const dir = join(пакеты, `${имя}-${бандл}`)
  const строки = []
  for (const короткое of короткие) {
    const м = манифест(короткое)
    if (м.plexus?.bundle?.build === false) continue
    строки.push({ id: м.plexus.id, name: м.name, config: м.plexus.config ?? {} })
  }
  const шапка = [
    '# Сгенерировано tools/bundle.mjs — правки затрутся на сборке.',
    '# Строка на каждый пакет, у которого plexus.bundle.build не false; config — значения по умолчанию',
    '# из plexus.config пакета: форма настроек показывает их как default и без них валидация не пройдёт.',
  ]
  const yaml = []
  for (const с of строки) {
    // config пишется построчно: значения по умолчанию читаемее, чем одна длинная строка JSON.
    yaml.push(`    - id: ${с.id}`)
    yaml.push(`      name: ${JSON.stringify(с.name)}`)
    const поля = Object.entries(с.config)
    if (поля.length === 0) continue
    yaml.push('      config:')
    for (const [k, v] of поля) yaml.push(`        ${k}: ${JSON.stringify(v)}`)
  }
  const файл = [...шапка, '- insert:', ...yaml].join('\n') + '\n'
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'cordis.patch.yml'), файл, 'utf8')
  console.log(`патч: ${строки.length} строк(и) → ${join(каталог(бандл), 'cordis.patch.yml')}`)
}

const шаг = process.argv[2] ?? 'всё'
if (шаг === 'локали' || шаг === 'всё') локали()
if (шаг === 'манифесты' || шаг === 'всё') манифесты()
if (шаг === 'патч' || шаг === 'всё') патч()
if (!['всё', 'локали', 'манифесты', 'патч'].includes(шаг)) {
  console.error(`bundle: неизвестный шаг ${JSON.stringify(шаг)}; есть локали, манифесты, патч, всё`)
  process.exit(2)
}
