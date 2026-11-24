// имя('budget') — стоп-кран этапа (E08). Каркас S01: конфиг, решение порогов, ручки DSH.
// Пороги и строка `kind: stop` доводятся в S02, стоп без прогресса — в S03.
//
// Ручки DSH 0.2.2-rc.5 (проверены по исходникам dsh-agent-loop, vault/решения
// «Стоп-кран имя('budget') — отказ шага в agent-pre-step с причиной hook»):
//  - ПРЕДУПРЕЖДЕНИЕ — в waterfall `agent/pre-step`: к сообщениям, которые входят
//    в шаг, дописывается одно user-сообщение с source `{ kind: имя('budget'),
//    form: 'notice' }`. Это обычное событие `user/message` в конце истории:
//    префикс не меняется (кэш цел), тип события известен DSH (возобновление цело).
//  - СТОП — там же, до вызова модели: `agent.cancel({ kind: 'hook', reason },
//    { keepInbox: true })` и `{ kind: 'reject' }`. Ход закрывается штатным
//    `turn/end` с `reason: { kind: 'aborted', reason: { kind: 'hook', reason } }`;
//    шаг не открывается, запроса к модели нет, очередь человека не теряется.
//  - Шаг, в который входит сообщение человека (source.kind === 'user'), не
//    стопорится: человек явно продолжил после стопа — ход проходит, модель
//    получает предупреждение; следующий шаг цикла инструментов снова стопорится.
//
// В журнал сессии пакет своих типов событий НЕ пишет (vault/решения «События
// плагинов продукта ломают возобновление сессии DSH»): данные — в ~/.<каталог данных>/usage.jsonl.
//
// Источник $: имя('meter')} (~/.<каталог данных>/usage.jsonl) даёт базу на конец прошлого хода —
// $ сессии и окна Go; внутри хода (headless — один ход на десятки шагов) пакет сам
// досчитывает $ шагов по `session/event` теми же ценами, что имя('meter')}.
//
// Из @deepseek-ai/* — только `import type`, кроме schemastery: он даёт `Config`.
import z from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import type { ContextFormed, MessageId, UserMessage } from '@deepseek-ai/dsh-llm'
import { randomUUID } from 'node:crypto'
import { appendFileSync, mkdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'
import { defaultUsageLog, readMeter, usageLogs } from './usage.js'
import { КАТАЛОГ, имя, журнал, каталоги } from './brand.js'
import { ПАКЕТ } from './types.js'
export { readMeter, usageLogs, pluginDirs } from './usage.js'

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    [ПАКЕТ]: { kind: typeof ПАКЕТ } & ContextFormed
  }
}

export const name = имя('budget')
const VERSION = '0.1.0'

/** Схема настроек: поля формы Settings DSH и значения по умолчанию — отсюда. */
/** Тип настроек — выход схемы: одно объявление на форму и на валидацию. */
export type Config = ReturnType<typeof Config>
export const Config = z.object({
  profile: z.string().description("Метка профиля в строке журнала."),
  budgetUsd: z.number().min(0).default(1).description("Бюджет сессии, $ (0 — без бюджета)"),
  warnPct: z.number().min(0).max(100).description("Доля бюджета для предупреждения, %"),
  windowPct: z.number().min(0).max(100).default(90).description("Порог окна лимита, %"),
  noProgressSteps: z.number().step(1).min(0).default(8).description("Шагов подряд без прогресса до стопа (0 — выкл.)"),
  repeatErrors: z.number().step(1).min(0).description("Повторов одной и той же ошибки до стопа (0 — выкл.)"),
  log: z.string().description("Журнал учёта. По умолчанию ~/.<каталог данных>/usage.jsonl."),
  prices: z.dict(z.array(z.number())).description("Дополнительные или переопределённые цены по id модели."),
})

/** $ за 1M токенов: [вход, чтение кэша, запись кэша, выход]. */
export type Price = [number, number, number, number]

/** Справочник цен проекта: packages/prices.json — копия tools/prices.json, копий в коде нет. */
const ПРАЙС = fileURLToPath(new URL('prices.json', import.meta.url))

function справочник(): { модели: Record<string, Price>; 'по префиксу': Record<string, Price> } {
  return JSON.parse(readFileSync(ПРАЙС, 'utf8'))
}

/** Цена по id модели: точное совпадение, иначе (как в tools/digest.py) по префиксу. */
export function ценаМодели(model: string, prices: Record<string, Price>): Price | undefined {
  if (prices[model]) return prices[model]
  for (const префикс of Object.keys(справочник()['по префиксу']).sort((a, b) => b.length - a.length)) {
    if (model.startsWith(префикс)) return справочник()['по префиксу'][префикс]
  }
  return undefined
}

// Цены — из packages/prices.json (копия tools/prices.json на сборке, один справочник на проект).
export const PRICES: Record<string, Price> = справочник().модели

export type StopReason = 'budget' | 'window' | 'no_progress'

/** Что видит пакет перед шагом. */
export interface Snapshot {
  sessionUsd: number
  /** % окна Go (имя('meter')}) с учётом шагов этого хода; undefined — meter молчит. */
  windowPct?: number
  /** Шагов подряд без прогресса (S03). */
  idleSteps: number
  /** Самая частая повторяющаяся ошибка инструмента (S03). */
  repeat?: { count: number; sig: string }
}

export type Decision =
  | { kind: 'go' }
  | { kind: 'warn'; text: string }
  | { kind: 'stop'; reason: StopReason; detail: string }

/** Чистое решение порогов — офлайн-тесты S02/S03 бьют сюда. */
export function decide(s: Snapshot, cfg: Required<Pick<Config, 'budgetUsd' | 'warnPct' | 'windowPct' | 'noProgressSteps'>> & { repeatErrors?: number }): Decision {
  const b = cfg.budgetUsd
  if (b > 0 && s.sessionUsd >= b) {
    return { kind: 'stop', reason: 'budget', detail: `$${fmt(s.sessionUsd)} ≥ бюджета $${fmt(b)}` }
  }
  if (s.windowPct !== undefined && s.windowPct >= cfg.windowPct) {
    return { kind: 'stop', reason: 'window', detail: `окно Go ${s.windowPct}% ≥ ${cfg.windowPct}%` }
  }
  if (cfg.noProgressSteps > 0 && s.idleSteps >= cfg.noProgressSteps) {
    return { kind: 'stop', reason: 'no_progress', detail: `${s.idleSteps} шагов без прогресса` }
  }
  const rep = cfg.repeatErrors ?? 3
  if (rep > 0 && s.repeat && s.repeat.count >= rep) {
    return { kind: 'stop', reason: 'no_progress', detail: `одна ошибка ${s.repeat.count} раз: ${s.repeat.sig}` }
  }
  if (b > 0 && s.sessionUsd >= b * cfg.warnPct / 100) {
    return {
      kind: 'warn',
      text: `[${имя('budget')}: потрачено $${fmt(s.sessionUsd)} из $${fmt(b)} бюджета этапа. `
        + 'Закругляйся: доведи текущий шаг, проверь результат и дай итог — что сделано, что осталось. '
        + 'Новых направлений не начинай.]',
    }
  }
  return { kind: 'go' }
}

interface SessionState {
  /** $ шагов, увиденных живьём после базы meter. */
  liveUsd: number
  /** База meter на старт процесса/хода. */
  baseUsd: number
  baseWindowUsd?: number
  windowBudgetUsd?: number
  idleSteps: number
  warned: boolean
  /** Шаг открыт и в нём ещё не было прогресса (S03). */
  stepProgress: boolean
  /** Вызовы инструментов шага: callId → имя. */
  calls: Map<string, string>
  /** Подписи уже виденных выводов bash. */
  seenOutputs: Set<string>
  /** Подпись ошибки → сколько раз. */
  errors: Map<string, number>
}

/** Инструменты, чей успешный вызов меняет файлы (имена из dsh-tool-fs / dsh-tool-str-replace-editor). */
export const MUTATING_TOOLS = new Set(['write', 'edit', 'apply_patch', 'str_replace_editor'])
/** Инструменты, чей новый вывод — вывод проверки (тесты, сборка, команды). */
export const CHECK_TOOLS = new Set(['bash', 'pwsh'])

/** Подпись текста: без чисел/путей-шума и пробелов, чтобы «та же ошибка» с другим временем/номером строки совпадала. */
export function signature(text: string): string {
  return text.replace(/0x[0-9a-f]+|\d+/gi, '#').replace(/\s+/g, ' ').trim().slice(0, 160)
}

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

export function apply(ctx: Context, config: Config = Config({})) {
  const log = ctx.logger(name)
  const cfg = {
    budgetUsd: config.budgetUsd ?? 0,
    warnPct: config.warnPct ?? 80,
    windowPct: config.windowPct ?? 90,
    noProgressSteps: config.noProgressSteps ?? 0,
    repeatErrors: config.repeatErrors ?? 3,
  }
  const usageLog = config.log ?? defaultUsageLog()
  const prices = { ...PRICES, ...цены(config.prices) }
  const sessions = new Map<string, SessionState>()

  function state(sessionId: string): SessionState {
    let s = sessions.get(sessionId)
    if (!s) {
      const { last, mine } = readMeter(usageLogs(config.log), sessionId)
      s = {
        liveUsd: 0, baseUsd: mine?.session_usd ?? 0,
        baseWindowUsd: last?.window_usd, windowBudgetUsd: last?.budget_usd,
        idleSteps: 0, warned: false,
        stepProgress: false, calls: new Map(), seenOutputs: new Set(), errors: new Map(),
      }
      sessions.set(sessionId, s)
    }
    return s
  }

  function snapshot(s: SessionState): Snapshot {
    const sessionUsd = s.baseUsd + s.liveUsd
    const windowPct = s.baseWindowUsd !== undefined && s.windowBudgetUsd
      ? Math.round(1000 * (s.baseWindowUsd + s.liveUsd) / s.windowBudgetUsd) / 10
      : undefined
    let repeat: Snapshot['repeat']
    for (const [sig, count] of s.errors) if (!repeat || count > repeat.count) repeat = { count, sig }
    return { sessionUsd, windowPct, idleSteps: s.idleSteps, repeat }
  }

  // $ шагов внутри хода: тот же расчёт, что имя('meter')}.stepOf.
  ctx.on('session/event', (session, event) => {
    const ev = event as { type: string; data?: any }
    if (ev.type === 'tool/call' || ev.type === 'tool/result' || ev.type === 'step/end') { progress(session.id, ev); return }
    if (ev.type !== 'assistant/message') return
    const u = ev.data?.usage
    if (!u) return
    const p = ценаМодели(ev.data?.message?.source?.model ?? '?', prices)
    if (!p) return
    state(session.id).liveUsd += ((u.inputTokens ?? 0) * p[0] + (u.cacheReadTokens ?? 0) * p[1]
      + (u.cacheWriteTokens ?? 0) * p[2] + (u.outputTokens ?? 0) * p[3]) / 1e6
  })

  // S03: прогресс шага по событиям журнала DSH (см. vault/решения/Стоп-кран имя('budget')}…).
  //  - tool/call {callId,name} + tool/result {message:{toolCallId,content,isError}};
  //  - прогресс = успешный write/edit/apply_patch/str_replace_editor (файлы изменены)
  //    ИЛИ вывод bash, которого в сессии ещё не было (новый вывод проверки);
  //  - одна ошибка (isError) с той же подписью — счётчик; успешная правка файла его обнуляет;
  //  - step/end: шаг без прогресса — idleSteps+1, с прогрессом — 0.
  function progress(id: string, ev: { type: string; data?: any }) {
    const s = state(id)
    if (ev.type === 'tool/call') {
      s.calls.set(ev.data.callId, ev.data.name)
    } else if (ev.type === 'tool/result') {
      const m = ev.data?.message
      const tool = s.calls.get(m?.toolCallId) ?? ''
      s.calls.delete(m?.toolCallId)
      const text = (m?.content ?? []).map((b: any) => b?.text ?? '').join('\n')
      const sig = tool + ':' + signature(text)
      if (m?.isError) s.errors.set(sig, (s.errors.get(sig) ?? 0) + 1)
      if (!m?.isError && MUTATING_TOOLS.has(tool)) {
        s.stepProgress = true
        s.errors.clear()
      } else if (CHECK_TOOLS.has(tool) && !s.seenOutputs.has(sig)) {
        s.stepProgress = true
      }
      if (CHECK_TOOLS.has(tool)) s.seenOutputs.add(sig)
    } else {
      s.idleSteps = s.stepProgress ? 0 : s.idleSteps + 1
      s.stepProgress = false
    }
  }

  ctx.on('agent/pre-step', async ({ agent, messages, turn, step }, next) => {
    const s = state(agent.session.id)
    const snap = snapshot(s)
    const d = decide(snap, cfg)
    const human = messages.some(m => m.source.kind === 'user')

    if (d.kind === 'stop' && !human) {
      const reason = `${имя('budget')}/${d.reason}: ${d.detail}`
      writeRow({ kind: 'stop', reason: d.reason, detail: d.detail, session: agent.session.id, turn, step, ...snap })
      log.warn('стоп хода %d: %s', turn, d.detail)
      // Штатная отмена: turn/end aborted с причиной hook; очередь человека сохраняется.
      agent.cancel({ kind: 'hook', reason }, { keepInbox: true })
      return { kind: 'reject' }
    }

    const decision = await next()
    if (decision.kind !== 'enter') return decision
    let text: string | undefined
    if (d.kind === 'stop') text = `[${имя('budget')}: стоп-кран сработал (${d.detail}); человек продолжил ход явно. Дай итог одним ответом.]`
    else if (d.kind === 'warn' && !s.warned) { s.warned = true; text = d.text }
    if (!text) return decision
    return { ...decision, messages: [...decision.messages, notice(text, d.kind === 'stop' ? d.reason : 'warn')] }
  })

  ctx.on('agent/turn-stopping', ({ agent }) => {
    // Новый ход начнёт с базы meter (он пишет строку в этом же событии) — живой счёт сбрасывается.
    sessions.delete(agent.session.id)
  })

  function writeRow(row: Record<string, unknown>) {
    try {
      mkdirSync(dirname(usageLog), { recursive: true })
      appendFileSync(usageLog, JSON.stringify({ ts: new Date().toISOString(), pkg: name, version: VERSION, profile: config.profile, ...row }) + '\n')
    } catch (error) {
      log.warn('не записал %s: %s', usageLog, error)
    }
  }
}

/** Уведомление модели: обычное user-сообщение с source имя('budget')} (известный тип события). */
function notice(text: string, summary: string): UserMessage {
  return {
    id: randomUUID() as MessageId,
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: ПАКЕТ, form: 'notice', summary: `${имя('budget')}: ${summary}` },
  }
}

function fmt(x: number) {
  return x.toFixed(x < 1 ? 3 : 2)
}
