// имя('budget') — журнал учёта (~/.<каталог данных>/usage.jsonl) и путь к нему.

import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { каталоги, журнал, имя } from './brand.js'

/** Каталоги данных плагинов (один — каталог продукта). */
export function pluginDirs(home = homedir()): string[] {
  return каталоги(home)
}

/** Журнал учёта по умолчанию: ~/.<каталог данных>/usage.jsonl. */
export function defaultUsageLog(home = homedir()): string {
  return журнал(home)
}

/** Существующие журналы: свой `config.log` (один файл), иначе журнал в каталоге продукта. */
export function usageLogs(own?: string, home = homedir()): string[] {
  if (own) return [own]
  return pluginDirs(home).map(d => join(d, 'usage.jsonl')).filter(p => existsSync(p))
}

interface MeterRow { ts?: string; session?: string; turn?: number; session_usd?: number; window_pct?: number; window_usd?: number; budget_usd?: number }
type MeterLine = MeterRow & { pkg?: string }

const key = (row: MeterLine) => `${row.ts ?? ''}|${row.session ?? ''}|${row.turn ?? ''}`

/** Последняя строка имя('meter')} (окно) и последняя по сессии. Читается хвост журналов. */
export function readMeter(path: string | string[], sessionId: string): { last?: MeterRow; mine?: MeterRow } {
  const files = (Array.isArray(path) ? path : [path]).filter(p => existsSync(p))
  let last: MeterRow | undefined, mine: MeterRow | undefined
  let lastKey = '', mineKey = ''
  // Строка с большим (ts, session, turn) выигрывает, при равенстве — из файла, что раньше в списке.
  for (const file of files) {
    const lines = readFileSync(file, 'utf8').trimEnd().split('\n').slice(-2000)
    for (let i = lines.length - 1; i >= 0; i--) {
      let row: MeterLine
      try { row = JSON.parse(lines[i]!) } catch { continue }
      if (row.pkg !== имя('meter')) continue
      const k = key(row)
      if (!last || k > lastKey) { last = row; lastKey = k }
      if (row.session === sessionId && (!mine || k > mineKey)) { mine = row; mineKey = k }
    }
  }
  return { last, mine }
}
