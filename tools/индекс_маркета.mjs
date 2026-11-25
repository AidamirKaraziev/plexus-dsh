#!/usr/bin/env node
// Генератор индекса маркета DSH (формат 1).
//   node tools/индекс_маркета.mjs --выход <файл>          собрать и записать market.json
//   node tools/индекс_маркета.mjs --проверка <файл|URL>   только провалидировать
// Сеть подменяема: --сеть-из <каталог> читает ответы из фикстур
// (npm-view.json — бандл, npm-view-plexus.json — @plexus-dsh/plexus; файла нет = npm ответил 404);
// --бандл <каталог> — где лежат package.json/иконка/locale бандла.
// Коды выхода: 0 — готово, 1 — ошибка (причина в stderr).
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const КОРЕНЬ = resolve(dirname(fileURLToPath(import.meta.url)), '..')
export const БАНДЛ_ИМЯ = '@plexus-dsh/bundle'
export const ПЛЕКСУС_ИМЯ = '@plexus-dsh/plexus'
const ИНДЕКС_URL = 'https://raw.githubusercontent.com/AidamirKaraziev/plexus-dsh/main/market.json'

export class Ошибка extends Error {}
const упасть = (текст) => { throw new Ошибка(текст) }

export async function загрузитьВалидатор() {
  const req = createRequire(join(КОРЕНЬ, 'packages', 'package.json'))
  try {
    return await import(pathToFileURL(req.resolve('@deepseek-ai/dsh-plugin-market-index')).href)
  } catch {
    return упасть('нет @deepseek-ai/dsh-plugin-market-index: выполни pnpm -C packages install')
  }
}

// ---- сеть: реальная и из фикстур ----
// npmView возвращает объект или null, если npm ответил 404 (пакета нет).
export function сетьРеальная() {
  return {
    npmView(имя) {
      try {
        return JSON.parse(execFileSync('npm', ['view', имя, '--json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
      } catch (e) {
        if (/E404|404 Not Found/.test(`${e.stderr ?? ''}${e.stdout ?? ''}`)) return null
        throw e
      }
    },
  }
}

export function сетьИзФикстур(каталог) {
  const файл = (имя) => join(каталог, имя === БАНДЛ_ИМЯ ? 'npm-view.json' : 'npm-view-plexus.json')
  return {
    npmView: (имя) => existsSync(файл(имя)) ? JSON.parse(readFileSync(файл(имя), 'utf8')) : null,
  }
}

// ---- метаданные пакета по exports: ./icon, ./locale/*.json, ./detail/* ----
function экспорт(pkg, ключ, подстановка) {
  const e = pkg.exports?.[ключ]
  if (typeof e !== 'string') return null
  return e.replace('*', подстановка ?? '').replace(/^\.\//, '')
}

async function метаданные(pkg, читать) {
  const вывод = {}
  const иконка = экспорт(pkg, './icon')
  if (иконка) {
    const текст = await читать(иконка)
    if (текст === null) упасть(`иконка ${иконка} не найдена`)
    if (!иконка.endsWith('.svg')) упасть(`тип иконки ${иконка} не поддержан (нужен .svg)`)
    вывод.icon = `data:image/svg+xml;base64,${Buffer.from(текст, 'utf8').toString('base64')}`
  }
  const title = {}
  const description = {}
  let detail
  for (const язык of ['en', 'ru']) {
    const путь = экспорт(pkg, './locale/*.json', язык)
    if (!путь) continue
    const текст = await читать(путь)
    if (текст === null) continue
    const meta = JSON.parse(текст).meta ?? {}
    if (meta.title) title[язык] = meta.title
    if (meta.description) description[язык] = meta.description
    if (meta.detail && язык === 'en') detail = meta.detail
  }
  if (detail && (await читать(detail)) === null) упасть(`meta.detail ${detail} не найден`)
  if (title.en) вывод.title = title
  if (description.en) вывод.description = description
  return вывод
}

function нужныеEngines(pkg, кто) {
  const dsh = pkg.engines?.dsh
  if (typeof dsh !== 'string' || dsh.trim() === '') упасть(`${кто}: нет engines.dsh`)
  return { dsh }
}

function версияNpm(имя, npm) {
  const версия = npm.version ?? упасть(`npm view ${имя}: нет version`)
  const время = npm.time?.[версия] ?? упасть(`npm view ${имя}: нет time[${версия}]`)
  const ts = Math.floor(Date.parse(время) / 1000)
  if (!Number.isFinite(ts)) упасть(`npm view ${имя}: время «${время}» не разобрать`)
  return { version: версия, publishTimestamp: ts, source: { type: 'npm' }, engines: нужныеEngines(npm, имя) }
}

export async function собрать({ сеть, бандлКаталог }) {
  // 1. бандл: npm view + локальные файлы для отображения
  const npm = сеть.npmView(БАНДЛ_ИМЯ) ?? упасть(`npm view ${БАНДЛ_ИМЯ}: пакета нет (404)`)
  const бандл = {
    name: БАНДЛ_ИМЯ,
    ...await метаданные(
      JSON.parse(readFileSync(join(бандлКаталог, 'package.json'), 'utf8')),
      async (п) => { const ф = join(бандлКаталог, п); return existsSync(ф) ? readFileSync(ф, 'utf8') : null }),
    versions: [версияNpm(БАНДЛ_ИМЯ, npm)],
  }
  const плагины = [бандл]

  // 2. Плексус: npm, последняя версия; 404 — плагин пропускается с предупреждением
  const плексус = сеть.npmView(ПЛЕКСУС_ИМЯ)
  if (плексус === null) {
    console.error(`индекс_маркета: предупреждение: ${ПЛЕКСУС_ИМЯ} нет в npm (404), плагин пропущен`)
  } else {
    плагины.push({ name: ПЛЕКСУС_ИМЯ, versions: [версияNpm(ПЛЕКСУС_ИМЯ, плексус)] })
  }

  return {
    format: 1,
    title: { en: 'Plexus DSH', ru: 'Plexus DSH' },
    description: { en: 'Plugins for DeepSeek Harness by Plexus.', ru: 'Плагины Plexus для DeepSeek Harness.' },
    plugins: плагины,
  }
}

export async function проверить(документ, url) {
  const { parseMarketIndex } = await загрузитьВалидатор()
  try {
    return parseMarketIndex(документ, url)
  } catch (e) {
    return упасть(`индекс не прошёл parseMarketIndex: ${e.message}`)
  }
}

async function главная(argv) {
  const арг = (имя) => { const i = argv.indexOf(имя); return i >= 0 ? argv[i + 1] : undefined }
  if (argv.includes('--проверка')) {
    const цель = арг('--проверка') ?? упасть('--проверка требует файл или URL')
    const url = /^https?:\/\//.test(цель)
    const текст = url ? await (await fetch(цель)).text() : readFileSync(цель, 'utf8')
    await проверить(JSON.parse(текст), url ? цель : pathToFileURL(resolve(цель)).href)
    console.log(`ok: ${цель}`)
    return
  }
  const выход = арг('--выход') ?? упасть('нужен --выход <файл> или --проверка <файл|URL>')
  const фикстуры = арг('--сеть-из')
  const индекс = await собрать({
    сеть: фикстуры ? сетьИзФикстур(фикстуры) : сетьРеальная(),
    бандлКаталог: арг('--бандл') ?? join(КОРЕНЬ, 'packages', 'plexus-dsh-bundle'),
  })
  await проверить(индекс, ИНДЕКС_URL)
  writeFileSync(выход, JSON.stringify(индекс, null, 2) + '\n')
  console.log(`записан ${выход}`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  главная(process.argv.slice(2)).catch((e) => {
    console.error(`индекс_маркета: ${e instanceof Ошибка ? e.message : e.stack ?? e}`)
    process.exit(1)
  })
}
