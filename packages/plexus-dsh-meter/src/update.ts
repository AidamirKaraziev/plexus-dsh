// Проверка новой версии @plexus-dsh/bundle: раз в сутки, в фоне, результат — в лог плагина
// и в файл update.json рядом с usage.jsonl. В журнал сессии и в usage.jsonl ничего не пишется.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

export const ПАКЕТ = '@plexus-dsh/bundle'
export const ИСТОЧНИКИ: [string, string][] = [
  ['npm', `https://registry.npmjs.org/${ПАКЕТ}/latest`],
  ['npmmirror', `https://registry.npmmirror.com/${ПАКЕТ}/latest`],
]
const СУТКИ = 24 * 3600_000
const ТАЙМАУТ = 3000

interface Лог { info(...a: any[]): void; debug(...a: any[]): void }
type Fetch = (url: string, init?: any) => Promise<{ ok: boolean; json(): Promise<any> }>

export interface ОпцииПроверки {
  /** Откуда подниматься в поиске бандла. */
  корень: string
  /** Путь к update.json. */
  файл: string
  log: Лог
  fetch?: Fetch
  /** Текущее время, мс. */
  now?: number
}

/** `X.Y.Z` без пред-релиза → числа; иначе null. */
export function разобрать(v: unknown): [number, number, number] | null {
  const m = typeof v === 'string' ? /^(\d+)\.(\d+)\.(\d+)$/.exec(v.trim()) : null
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null
}

/** a новее b (оба — чистые X.Y.Z). */
export function новее(a: string, b: string): boolean {
  const x = разобрать(a), y = разобрать(b)
  if (!x || !y) return false
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i]! > y[i]!
  return false
}

/** Установленная версия бандла: package.json бандла выше `от`; null — бандла нет. */
export function версияБандла(от: string): string | null {
  let dir = от
  for (;;) {
    const p = join(dir, 'package.json')
    if (existsSync(p)) {
      try {
        const j = JSON.parse(readFileSync(p, 'utf8'))
        if (j.name === ПАКЕТ && typeof j.version === 'string') return j.version
      } catch { /* битый package.json — ищем выше */ }
    }
    const up = dirname(dir)
    if (up === dir) return null
    dir = up
  }
}

function читать(файл: string): any {
  try { return JSON.parse(readFileSync(файл, 'utf8')) } catch { return null }
}

async function спросить(f: Fetch, url: string): Promise<string | null> {
  try {
    const r = await f(url, { signal: AbortSignal.timeout(ТАЙМАУТ) })
    if (!r.ok) return null
    const v = (await r.json())?.version
    return typeof v === 'string' ? v : null
  } catch {
    return null
  }
}

/** Одна проверка. Не бросает: ошибки — только в лог. */
export async function проверитьОбновление(o: ОпцииПроверки): Promise<void> {
  try {
    const now = o.now ?? Date.now()
    const установлена = версияБандла(o.корень)
    if (!установлена) { o.log.debug('Plexus DSH: бандл не найден, проверка обновлений пропущена'); return }
    const было = читать(o.файл)
    const прошлая = Date.parse(typeof было?.checked_at === 'string' ? было.checked_at : '')
    if (Number.isFinite(прошлая) && now - прошлая < СУТКИ) return
    const f: Fetch = o.fetch ?? (globalThis.fetch as any)
    let latest: string | null = null
    let source: string | null = null
    for (const [имя, url] of ИСТОЧНИКИ) {
      const v = await спросить(f, url)
      if (v) { latest = v; source = имя; break }
    }
    if (!latest) o.log.debug('Plexus DSH: npm и зеркало не ответили, повтор через сутки')
    const запись = {
      checked_at: new Date(now).toISOString(),
      installed: установлена,
      latest: latest ?? было?.latest ?? null,
      source: source ?? было?.source ?? null,
    }
    mkdirSync(dirname(o.файл), { recursive: true })
    writeFileSync(o.файл, JSON.stringify(запись, null, 2) + '\n')
    if (latest && новее(latest, установлена)) {
      o.log.info('Вышел Plexus DSH %s (у вас %s) — обновите: Плагины → Установлено → Plexus DSH → Версии', latest, установлена)
    }
  } catch (error) {
    o.log.debug('Plexus DSH: проверка обновлений не удалась: %s', error)
  }
}
