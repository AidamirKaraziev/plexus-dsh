// Имя продукта и его каталог данных — одно место на весь репозиторий.
//
// Своих копий имени в пакетах нет: тексты пишутся как `имя('meter')` и `каталог(homedir())`, а сам
// корень имени приходит из tools/dsh_digest.py — на сборке `node ../tools/brandinfo.mjs` пишет
// packages/brand.json (его импортируют пакеты) и lib/brand.json рядом с собранным кодом
// (его читает поле `name` плагина Cordis, у которого нет сборки).
//
// Утилиты (usage.ts, список каталогов) от этого модуля не зависят и берут каталог параметром:
// тесты не дёргают сборку.

// brand.json пишет tools/brandinfo.mjs на сборке; его копия лежит рядом с собранным lib/brand.js.
// Путь относительный к этому файлу, а не к src: в пакет уезжает собранный lib/brand.js.
import brand from './brand.json' with { type: 'json' }

/** Корень имени продукта: его пишет tools/brandinfo.mjs в brand.json на сборке. */
export const КОРЕНЬ: string = brand.КОРЕНЬ

/** Имя пакета продукта: имя('meter') → `<корень>-meter`. */
export function имя(пакет: string): string {
  return `${КОРЕНЬ}-${пакет}`
}

/** Каталог данных продукта в домашней папке: `.<корень>`. */
export const КАТАЛОГ = `.${КОРЕНЬ}`

/** Журнал учёта по умолчанию: `<home>/.<корень>/usage.jsonl`. */
export function журнал(home: string): string {
  return `${home}/${КАТАЛОГ}/usage.jsonl`
}

/** Каталоги данных плагинов (один — каталог продукта). */
export function каталоги(home: string): string[] {
  return [`${home}/${КАТАЛОГ}`]
}
