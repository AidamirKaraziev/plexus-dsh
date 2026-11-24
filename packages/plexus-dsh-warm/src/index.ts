// имя('warm') — держит кэш провайдера тёплым в паузе человека: после хода раз в
// `pingEveryMin` повторяет последний запрос модели (тот же объект, ответ в 1 токен),
// пока не начнётся новый ход, не выйдет `maxPauseMin` или контекст мал (< `minContextTokens`).
//
// Параметры по модели — providers.json рядом с package.json (ttlMin, pingEveryMin,
// maxPauseMin, price); явные значения в config их перекрывают.
//
// Точка перехвата — waterfall `llm/stream` (@deepseek-ai/dsh-llm): через него идёт
// каждый вызов модели. Запрос цикла (dsh-agent-loop) приходит глубоко замороженным
// и «чистой функцией журнала» — его можно хранить по ссылке и отдать
// `ctx.llm.stream` повторно: тот же адаптер pi-ai сериализует его в те же байты.
//
// В журнал сессии DSH пакет НИЧЕГО не пишет (vault/решения «События плагинов продукта
// ломают возобновление сессии DSH»): пинг идёт мимо агента, прямо в ctx.llm.
// Учёт — строкой `kind: ping` в ~/.<каталог данных>/usage.jsonl (поля `session`, `usd` читает
// tools/bench.py compare).
//
// Процесс: таймер живёт в процессе DSH. В headless процесс выходит после хода —
// пинговать некому; эталон идёт через ACP (vault/решения «Эталон через ACP …»).
//
// Из @deepseek-ai/* — только `import type`, кроме schemastery: он даёт `Config`.
import z from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-session'
import type { GenerateOptions, StreamChunk, TokenUsage } from '@deepseek-ai/dsh-llm'
import { appendFileSync, mkdirSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { имя, журнал } from './brand.js'

export const name = имя('warm')
/** `ctx.llm` — сервис dsh-llm; без него пакет не стартует. */
export const inject = ['llm']

const VERSION = '0.2.0'

/**
 * Метка профиля для строки журнала. Явное `config.profile` главнее (tools/bench.py пишет туда свои
 * метки); иначе — имя профиля, с которым запущен DSH (`ctx.profileContext`, им же DSH заполняет
 * `DSH_PROFILE` в shell-вызовах). Буквальная `{profile}` — заглушка из старых патчей, а не имя.
 */
export function профиль(ctx: Pick<Context, 'get'>, задано?: string): string | undefined {
  if (задано && задано !== '{profile}') return задано
  return ctx.get('profileContext')?.name
}

/** $ за 1M токенов: [вход, чтение кэша, запись кэша, выход]. */
export type Price = [number, number, number, number]

/** Строка providers.json. */
export interface ProviderParams {
  provider?: string
  /** Нижняя граница жизни кэша без запросов, мин. */
  ttlMin: number
  pingEveryMin: number
  maxPauseMin: number
  price: Price
}

/** Схема настроек: поля формы Settings DSH и значения по умолчанию — отсюда. */
/** Тип настроек — выход схемы: одно объявление на форму и на валидацию. */
export type Config = ReturnType<typeof Config>
export const Config = z.object({
  profile: z.string().description("Метка профиля в строке журнала. По умолчанию — имя профиля DSH."),
  pingEveryMin: z.number().min(0).description("Интервал пинга, мин (по умолчанию — по модели)"),
  maxPauseMin: z.number().min(0).description("Сколько паузы держать кэш, мин (по умолчанию — по модели)"),
  minContextTokens: z.number().step(1).min(0).default(30_000).description("Не пинговать, если контекст меньше, токенов"),
  live: z.boolean().default(false).description("Слать пинг на самом деле (false — только запись в лог)"),
  log: z.string().description("Журнал учёта. По умолчанию ~/.<каталог данных>/usage.jsonl."),
  providers: z.dict(z.object({
    provider: z.string(),
    ttlMin: z.number(),
    pingEveryMin: z.number(),
    maxPauseMin: z.number(),
    price: z.array(z.number()),
  })).description("Параметры провайдеров, перекрывают providers.json"),
})

/** Если модели нет в providers.json: как GLM-5.3 (TTL 6–10 мин), цена неизвестна. */
const FALLBACK: ProviderParams = { ttlMin: 6, pingEveryMin: 4.5, maxPauseMin: 25, price: [0, 0, 0, 0] }

/** Состояние паузы одной сессии. */
interface Warm {
  /** Последний запрос цикла — по ссылке, замороженный. */
  request: GenerateOptions
  /** Контекст последнего запроса: вход + чтение + запись кэша, токенов. */
  contextTokens: number
  /** Начало паузы (конец хода), мс; 0 — пауза не идёт. */
  pausedAt: number
  /** Параметры модели на эту паузу. */
  everyMs: number
  maxPauseMs: number
  timer?: ReturnType<typeof setTimeout>
  pings: number
  /** Отмена пинга в полёте при новом ходе. */
  abort?: AbortController
}

export type StopReason = 'new-turn' | 'max-pause' | 'small-context' | 'disposed'

/** Контекст запроса по usage ответа. */
export function contextOf(u: TokenUsage): number {
  return (u.inputTokens ?? 0) + (u.cacheReadTokens ?? 0) + (u.cacheWriteTokens ?? 0)
}

/** $ по usage и цене. */
export function usdOf(u: TokenUsage, p: Price): number {
  return ((u.inputTokens ?? 0) * p[0] + (u.cacheReadTokens ?? 0) * p[1] +
    (u.cacheWriteTokens ?? 0) * p[2] + (u.outputTokens ?? 0) * p[3]) / 1e6
}

/**
 * Запрос обычного хода цикла. `isAgentLoopRequest` — значение из dsh-llm, импортировать
 * его нельзя (только типы), поэтому признак косвенный: у запроса цикла есть
 * `sessionId` и нет `purpose` (вспомогательные вызовы — заголовок, сжатие — его ставят).
 */
export function isTurnRequest(o: GenerateOptions): boolean {
  return o.sessionId !== undefined && o.purpose === undefined
}

/** Пинг — тот же запрос, ответ в 1 токен, своя отмена. Новый объект, не помечен как запрос цикла. */
export function pingRequest(o: GenerateOptions, signal: AbortSignal): GenerateOptions {
  return { ...o, maxTokens: 1, signal }
}

/** providers.json пакета (lib/../providers.json); нет файла — пустая таблица. */
export function loadProviders(): Record<string, ProviderParams> {
  try {
    const path = join(dirname(fileURLToPath(import.meta.url)), '..', 'providers.json')
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    return {}
  }
}

export function apply(ctx: Context, config: Config = Config({})) {
  const log = ctx.logger(name)
  const minContext = config.minContextTokens ?? 30_000
  const live = config.live ?? true
  // Свой журнал — в новый каталог, читатели смотрят и в старый (до E16-S02)
  const usageLog = config.log ?? журнал(homedir())
  const providers = (config.providers as Record<string, ProviderParams>) ?? loadProviders()

  function params(model: string): ProviderParams {
    const p = providers[model] ?? FALLBACK
    return {
      ...p,
      pingEveryMin: config.pingEveryMin ?? p.pingEveryMin,
      maxPauseMin: config.maxPauseMin ?? p.maxPauseMin,
    }
  }

  /** sessionId → состояние. */
  const warm = new Map<string, Warm>()
  /** Свои пинги: их waterfall пропускает, не считая новым ходом. */
  const ours = new WeakSet<GenerateOptions>()

  function stop(sessionId: string, reason: StopReason) {
    const w = warm.get(sessionId)
    if (!w) return
    if (w.timer) clearTimeout(w.timer)
    w.abort?.abort()
    if (w.pausedAt) log.debug('сессия %s: стоп (%s) после %d пингов', sessionId, reason, w.pings)
    w.timer = undefined
    w.abort = undefined
    w.pausedAt = 0
    w.pings = 0
  }

  /** Конец хода: взвести таймер, если контекст стоит того. */
  function arm(sessionId: string) {
    const w = warm.get(sessionId)
    if (!w) return
    stop(sessionId, 'new-turn')
    if (w.contextTokens < minContext) {
      log.debug('сессия %s: контекст %d < %d — не пингую', sessionId, w.contextTokens, minContext)
      return
    }
    const p = params(w.request.model)
    w.everyMs = p.pingEveryMin * 60_000
    w.maxPauseMs = p.maxPauseMin * 60_000
    w.pausedAt = Date.now()
    schedule(sessionId, w)
  }

  function schedule(sessionId: string, w: Warm) {
    w.timer = setTimeout(() => { void tick(sessionId) }, w.everyMs)
    w.timer.unref?.() // таймер не держит процесс: выход DSH не ждёт паузы
  }

  async function tick(sessionId: string) {
    const w = warm.get(sessionId)
    if (!w || !w.pausedAt) return
    w.timer = undefined
    if (Date.now() - w.pausedAt + w.everyMs > w.maxPauseMs + w.everyMs / 2) {
      // следующий пинг вышел бы за лимит паузы — кэш дальше не держим
      return stop(sessionId, 'max-pause')
    }
    if (w.contextTokens < minContext) return stop(sessionId, 'small-context')
    const abort = (w.abort = new AbortController())
    const pausedAt = w.pausedAt
    try {
      const usage = await sendPing(w.request, abort.signal)
      if (abort.signal.aborted || w.pausedAt !== pausedAt) return // новый ход перебил пинг
      w.pings++
      if (usage) record(sessionId, w, usage)
    } catch (error) {
      if (!abort.signal.aborted) log.warn('сессия %s: пинг не прошёл: %s', sessionId, error)
    } finally {
      if (w.abort === abort) w.abort = undefined
    }
    if (w.pausedAt === pausedAt) schedule(sessionId, w)
  }

  /** Строка `kind: ping` в журнал учёта. */
  function record(sessionId: string, w: Warm, u: TokenUsage) {
    const p = params(w.request.model)
    const context = contextOf(u)
    const row = {
      ts: new Date().toISOString(),
      kind: 'ping',
      pkg: name, version: VERSION,
      profile: профиль(ctx, config.profile),
      session: sessionId,
      provider: w.request.provider, model: w.request.model,
      n: w.pings,
      pause_min: round((Date.now() - w.pausedAt) / 60_000, 2),
      usd: round(usdOf(u, p.price), 6),
      inputTokens: u.inputTokens ?? 0,
      cacheReadTokens: u.cacheReadTokens ?? 0,
      cacheWriteTokens: u.cacheWriteTokens ?? 0,
      outputTokens: u.outputTokens ?? 0,
      contextTokens: context,
      turnContextTokens: w.contextTokens,
      // попадание: почти весь контекст прочитан из кэша (как MISS в tools/dsh_digest.py — промах > 20K некэшированного)
      hit: context > 0 && context - (u.cacheReadTokens ?? 0) <= 20_000,
    }
    try {
      mkdirSync(dirname(usageLog), { recursive: true })
      appendFileSync(usageLog, JSON.stringify(row) + '\n')
    } catch (error) {
      log.warn('не записал пинг в %s: %s', usageLog, error)
    }
    log.info('сессия %s: пинг %d (%s мин паузы) — чтение %d из %d, $%s%s', sessionId, row.n,
      row.pause_min, row.cacheReadTokens, context, row.usd, row.hit ? '' : ' — ПРОМАХ')
  }

  /**
   * Пинг: тот же запрос через `ctx.llm.stream` (waterfall `llm/stream` → адаптер),
   * ответ в 1 токен; поток дочитывается до конца, usage — последний чанк `usage`.
   */
  async function sendPing(request: GenerateOptions, signal: AbortSignal): Promise<TokenUsage | undefined> {
    if (!live) {
      log.info('пинг (live: false): %s/%s, сообщений %d', request.provider, request.model, request.messages.length)
      return undefined
    }
    const req = pingRequest(request, signal)
    ours.add(req)
    let usage: TokenUsage | undefined
    const stream: AsyncIterable<StreamChunk> = ctx.llm.stream(req)
    for await (const chunk of stream) {
      if (chunk.type === 'usage') usage = chunk.usage
    }
    return usage
  }

  // Перехват: каждый вызов модели. Запрос хода — запомнить (по ссылке) и снять паузу;
  // usage ответа — размер контекста. Поток отдаётся дальше без изменений.
  ctx.on('llm/stream', function (options, next) {
    if (ours.has(options) || !isTurnRequest(options)) return next()
    const sessionId = options.sessionId as string
    stop(sessionId, 'new-turn')
    const w: Warm = warm.get(sessionId) ??
      { request: options, contextTokens: 0, pausedAt: 0, pings: 0, everyMs: 0, maxPauseMs: 0 }
    w.request = options
    warm.set(sessionId, w)
    return (async function* () {
      for await (const chunk of next()) {
        if (chunk.type === 'usage') w.contextTokens = contextOf(chunk.usage)
        yield chunk
      }
    })()
  })

  // Новый ход: любой новый шаг снимает паузу раньше, чем уйдёт запрос.
  ctx.on('agent/pre-step', ({ agent }, next) => {
    stop(agent.session.id, 'new-turn')
    return next()
  })

  ctx.on('agent/turn-stopping', ({ agent }) => {
    arm(agent.session.id)
  })

  ctx.on('session/disposed', (session) => {
    stop(session.id, 'disposed')
    warm.delete(session.id)
  })

  ctx.effect(() => () => {
    for (const id of [...warm.keys()]) stop(id, 'disposed')
    warm.clear()
  }, `${имя('warm')} timers`)
}

function round(x: number, digits = 4) {
  const k = 10 ** digits
  return Math.round(x * k) / k
}
