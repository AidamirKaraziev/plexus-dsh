// имя('meter') — счётчик окна OpenCode Go: после каждого хода считает $ и % окна
// ($12 за скользящие 5 ч) по всем сессиям DSH и дописывает строку в
// ~/.<каталог данных>/usage.jsonl.
//
// В журнал сессии DSH пакет НИЧЕГО не пишет (см. vault/решения «События плагинов
// продукта ломают возобновление сессии DSH»): только читает.
//
// Откуда берутся шаги:
//  - живые сессии этого процесса — из `session/event` (assistant/message с usage),
//    слушатель только читает: на диск журнал уходит с задержкой пакетного окна;
//  - все остальные сессии — с диска ~/.dsh/sessions/*/*/session*.jsonl.zstd,
//    изменённые за окно. Дубликаты (та же сессия и seq) схлопываются.
// Цена шага — как в tools/digest.py: вход, чтение кэша, запись кэша, выход по
// справочнику packages/prices.json. Унаследованный префикс форка (до
// `session/end-seed`) не считается — он уже посчитан в родителе.
//
// Из @deepseek-ai/* — только `import type`, кроме schemastery: он даёт `Config`.
import z from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import { spawnSync } from 'node:child_process'
import { appendFileSync, mkdirSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { zstdDecompressSync } from 'node:zlib'
import { имя, журнал } from './brand.js'
import { проверитьОбновление, type ОпцииПроверки } from './update.js'

export const name = имя('meter')

/** $ за 1M токенов: [вход, чтение кэша, запись кэша, выход]. */
export type Price = [number, number, number, number]

/** Справочник цен проекта: packages/prices.json — копия tools/prices.json, копий в коде нет. */
const ПРАЙС = join(dirname(fileURLToPath(import.meta.url)), 'prices.json')

function справочник(): { модели: Record<string, Price>; 'по префиксу': Record<string, Price> } {
  return JSON.parse(readFileSync(ПРАЙС, 'utf8'))
}

/** Схема настроек: поля формы Settings DSH и значения по умолчанию — отсюда. */
/** Тип настроек — выход схемы: одно объявление на форму и на валидацию. */
export type Config = ReturnType<typeof Config>
export const Config = z.object({
  profile: z.string().description("Метка профиля в строке журнала. По умолчанию — имя профиля DSH."),
  windowHours: z.number().step(1).min(1).default(5).description("Окно лимита, часы"),
  budgetUsd: z.number().min(0).default(12).description("Бюджет окна, $"),
  log: z.string().description("Журнал учёта. По умолчанию ~/.<каталог данных>/usage.jsonl."),
  sessionsDir: z.string().description("Корень сессий DSH. По умолчанию ~/.dsh/sessions."),
  updateCheck: z.boolean().default(true).description("Раз в сутки проверять, вышла ли новая версия Plexus DSH."),
  prices: z.dict(z.array(z.number())).description("Дополнительные или переопределённые цены по id модели."),
})

// Цены — из packages/prices.json (копия tools/prices.json на сборке, один справочник на проект).
export const PRICES: Record<string, Price> = справочник().модели
export const PREFIX_PRICES: Record<string, Price> = справочник()['по префиксу']

interface Step {
  time: number
  model: string
  usd: number
}

interface RawEvent {
  type: string
  seq?: number
  time?: number
  id?: string
  data?: any
}

const VERSION = '0.1.0'

/**
 * Метка профиля для строки журнала. Явное `config.profile` главнее (tools/bench.py пишет туда свои
 * метки); иначе — имя профиля, с которым запущен DSH (`ctx.profileContext`, им же DSH заполняет
 * `DSH_PROFILE` в shell-вызовах). Буквальная `{profile}` — заглушка из старых патчей, а не имя.
 */
export function профиль(ctx: Pick<Context, 'get'>, задано?: string): string | undefined {
  if (задано && задано !== '{profile}') return задано
  return ctx.get('profileContext')?.name
}

/** Цена по id модели: точное совпадение, иначе (как в tools/digest.py) по префиксу. */
export function ценаМодели(model: string, prices: Record<string, Price>): Price | undefined {
  if (prices[model]) return prices[model]
  for (const префикс of Object.keys(PREFIX_PRICES).sort((a, b) => b.length - a.length)) {
    if (model.startsWith(префикс)) return PREFIX_PRICES[префикс]
  }
  return undefined
}


/** Цена одного assistant/message или null, если в нём нет usage. */
export function stepOf(ev: RawEvent, prices: Record<string, Price>): Step | null {
  if (ev.type !== 'assistant/message') return null
  const u = ev.data?.usage
  if (!u) return null
  const model: string = ev.data?.message?.source?.model ?? '?'
  const p = ценаМодели(model, prices)
  const usd = p
    ? ((u.inputTokens ?? 0) * p[0] + (u.cacheReadTokens ?? 0) * p[1] +
       (u.cacheWriteTokens ?? 0) * p[2] + (u.outputTokens ?? 0) * p[3]) / 1e6
    : 0
  return { time: ev.time ?? 0, model, usd }
}

/** Разжать журнал: сначала zlib (Node ≥ 23.8), при рваном хвосте — `zstd -dc` с частичным выводом. */
function readJournal(path: string): RawEvent[] {
  let text: string
  try {
    text = zstdDecompressSync(readFileSync(path)).toString('utf8')
  } catch {
    const r = spawnSync('zstd', ['-dcq', path], { maxBuffer: 1 << 30 })
    text = r.stdout ? r.stdout.toString('utf8') : ''
  }
  const out: RawEvent[] = []
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    try { out.push(JSON.parse(line)) } catch { /* рваный хвост */ }
  }
  return out
}

/** Шаги журнала по seq; префикс форка до session/end-seed пропускается. */
export function stepsOfJournal(events: RawEvent[], prices: Record<string, Price>): Map<number, Step> {
  const seedEnd = events.findIndex(e => e.type === 'session/end-seed' && e.data?.inherited === true)
  const out = new Map<number, Step>()
  events.forEach((e, i) => {
    if (i <= seedEnd || e.seq === undefined) return
    const s = stepOf(e, prices)
    if (s) out.set(e.seq, s)
  })
  return out
}

interface CacheEntry { mtimeMs: number; size: number; id: string; steps: Map<number, Step> }

/** Цены из config — в кортежи: схема отдаёт `number[]`, учёт ждёт четвёрку. */
function цены(из: Record<string, number[]> | undefined): Record<string, Price> {
  const итог: Record<string, Price> = {}
  for (const [модель, п] of Object.entries(из ?? {})) {
    const [вход, чтение, запись, выход] = п
    if (п.length === 4 && [вход, чтение, запись, выход].every((ч) => typeof ч === 'number')) {
      итог[модель] = [вход!, чтение!, запись!, выход!]
    }
  }
  return итог
}

/** Подмена для тестов: откуда искать бандл, сеть и вызов по окончании фоновой проверки. */
export interface ОпцииUpdate { корень?: string; fetch?: ОпцииПроверки['fetch']; готово?: () => void }

export function apply(ctx: Context, config: Config = Config({}), опции: ОпцииUpdate = {}) {
  const log = ctx.logger(name)
  const windowMs = (config.windowHours ?? 5) * 3600_000
  const budget = config.budgetUsd ?? 12
  const usageLog = config.log ?? журнал(homedir())
  const root = config.sessionsDir ?? join(homedir(), '.dsh', 'sessions')
  const prices = { ...PRICES, ...цены(config.prices) }

  /** Живые сессии процесса: id → seq → шаг. */
  const live = new Map<string, Map<number, Step>>()
  /** Журналы на диске: путь → разобранные шаги (по mtime/size). */
  const disk = new Map<string, CacheEntry>()

  ctx.on('session/event', (session, event) => {
    const s = stepOf(event as RawEvent, prices)
    if (!s) return
    let m = live.get(session.id)
    if (!m) live.set(session.id, (m = new Map()))
    m.set((event as RawEvent).seq ?? m.size, s)
  })

  function scanDisk(since: number): Map<string, Map<number, Step>> {
    const out = new Map<string, Map<number, Step>>()
    let cwds: string[] = []
    try { cwds = readdirSync(root) } catch { return out }
    for (const cwd of cwds) {
      let dirs: string[] = []
      try { dirs = readdirSync(join(root, cwd)) } catch { continue }
      for (const dir of dirs) {
        let files: string[] = []
        try { files = readdirSync(join(root, cwd, dir)).filter(f => /^session.*\.jsonl\.zstd$/.test(f)) } catch { continue }
        if (!files.length) continue
        // как digest: самый свежий журнал папки (после миграции формата их бывает два)
        const paths = files.map(f => join(root, cwd, dir, f))
        const stats = paths.map(p => { try { return statSync(p) } catch { return null } })
        let best = -1
        stats.forEach((st, i) => { if (st && (best < 0 || st.mtimeMs > stats[best]!.mtimeMs)) best = i })
        const st = stats[best]
        if (!st || st.mtimeMs < since) continue
        const path = paths[best]!
        let entry = disk.get(path)
        if (!entry || entry.mtimeMs !== st.mtimeMs || entry.size !== st.size) {
          try {
            const evs = readJournal(path)
            entry = { mtimeMs: st.mtimeMs, size: st.size, id: evs[0]?.id ?? dir, steps: stepsOfJournal(evs, prices) }
            disk.set(path, entry)
          } catch (error) {
            log.warn('не прочитал %s: %s', path, error)
            continue
          }
        }
        out.set(entry.id, entry.steps)
      }
    }
    return out
  }

  function measure(sessionId: string, turn: number) {
    const now = Date.now()
    const since = now - windowMs
    const all = scanDisk(since)
    for (const [id, m] of live) {
      const merged = new Map(all.get(id) ?? [])
      for (const [seq, s] of m) merged.set(seq, s)
      all.set(id, merged)
    }
    let windowUsd = 0, windowSteps = 0, sessions = 0
    let oldest = now
    const byModel: Record<string, number> = {}
    for (const steps of all.values()) {
      let used = false
      for (const s of steps.values()) {
        if (s.time < since) continue
        windowUsd += s.usd; windowSteps++; used = true
        byModel[s.model] = (byModel[s.model] ?? 0) + s.usd
        if (s.time < oldest) oldest = s.time
      }
      if (used) sessions++
    }
    let sessionUsd = 0, sessionSteps = 0
    for (const s of (all.get(sessionId) ?? new Map<number, Step>()).values()) { sessionUsd += s.usd; sessionSteps++ }
    const row = {
      ts: new Date(now).toISOString(),
      pkg: name, version: VERSION,
      profile: профиль(ctx, config.profile),
      session: sessionId, turn,
      session_usd: round(sessionUsd), session_steps: sessionSteps,
      window_h: windowMs / 3600_000, budget_usd: budget,
      window_usd: round(windowUsd),
      window_pct: Math.round(1000 * windowUsd / budget) / 10,
      window_steps: windowSteps, window_sessions: sessions,
      // когда старейший шаг окна выпадет из него — тогда % начнёт падать
      window_frees_at: windowSteps ? new Date(oldest + windowMs).toISOString() : null,
      by_model: Object.fromEntries(Object.entries(byModel).map(([k, v]) => [k, round(v)])),
    }
    mkdirSync(dirname(usageLog), { recursive: true })
    appendFileSync(usageLog, JSON.stringify(row) + '\n')
    log.info('окно Go: $%s = %s%% из $%s за %d ч (%d сессий); сессия $%s',
      row.window_usd, row.window_pct, budget, row.window_h, sessions, row.session_usd)
  }

  if (config.updateCheck ?? true) {
    // в фоне: загрузку плагина и ход не задерживает; сама проверка не бросает
    void проверитьОбновление({
      корень: опции.корень ?? dirname(fileURLToPath(import.meta.url)),
      файл: join(dirname(usageLog), 'update.json'),
      log, fetch: опции.fetch,
    }).then(опции.готово)
  }

  ctx.on('agent/turn-stopping', ({ agent, turn }) => {
    try {
      measure(agent.session.id, turn)
    } catch (error) {
      log.warn(`${имя('meter')}: не посчитал окно: %s`, error)
    }
  })
}

function round(x: number) {
  return Math.round(x * 1e4) / 1e4
}

export { readJournal }
