// имя('scout') — разведчик: поиск и чтение уходят из основной сессии (GLM-5.3) в
// spawn-ребёнка на glm-5.3-flash. Бриф ≤ briefTokens от родителя, назад — только итог
// ≤ resultTokens.
//
// Устройство DSH 0.2.2-rc.5 и замер — vault/решения «Разведчик — spawn через
// ctx.subagents с caller-доставкой, flash в Go». Коротко:
// - встроенный `subagent` (provider spawn) асинхронен: результат приходит родителю
//   отдельным user/message и будит лишний ход родителя; поэтому свой инструмент;
// - `scout` в execute зовёт `ctx.subagents.startActivation({ provider: 'spawn',
//   delivery: 'caller', agentOptions: { provider, model }, prompt: [бриф], ... })`
//   и ждёт `activation.result` — итог ребёнка ложится обычным tool/result того же шага;
// - ребёнок живёт в своей сессии (~/.dsh/sessions/<cwd>/<childId>/), его чтения в
//   журнал родителя не попадают; родитель видит tool/call (бриф) и tool/result (итог).
//
// Ребёнок только читает. `agentPreset` в headless недоступен (нет agent-preset-registry),
// а `ctx.tools.restrict` действует только в scope самого ребёнка — поэтому глобальный
// `ctx.tools.guard`: для сессий разведчика (childId резервируем сами до старта) всё,
// кроме READ_TOOLS, отклоняется до исполнения. Схемы закрытых инструментов ребёнок
// видит (наследует пресет родителя), бриф говорит ему ими не пользоваться.
//
// $ ребёнка — из его `session/event` (assistant/message с usage) по таблице цен;
// строка `kind: scout` в ~/.<каталог данных>/usage.jsonl. В журнал сессии DSH пакет своего не
// пишет (vault/решения «События плагинов продукта ломают возобновление сессии DSH»).
// Токены — оценка DSH: символы / 4.
//
// Из @deepseek-ai/* — только `import type`, кроме schemastery: он даёт `Config`.
import z from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { ContentBlock, TokenUsage } from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-session'
import type { SessionId } from '@deepseek-ai/dsh-session'
import type { SubagentResult } from '@deepseek-ai/dsh-subagent'
import type { ToolDefinition, ToolExecution, ToolRunContext } from '@deepseek-ai/dsh-tools'
import { randomUUID } from 'node:crypto'
import { appendFileSync, mkdirSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { имя, журнал } from './brand.js'

export const name = имя('scout')
/** `tools` — `scout` и guard; `subagents` — spawn-ребёнок; `systemPrompt` — подсказка родителю. */
export const inject = ['tools', 'subagents', 'systemPrompt']

const VERSION = '0.2.2'

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

/** Справочник цен проекта: packages/prices.json — копия tools/prices.json, копий в коде нет. */
const ПРАЙС = fileURLToPath(new URL('prices.json', import.meta.url))

function справочник(): { модели: Record<string, Price>; 'по префиксу': Record<string, Price> } {
  return JSON.parse(readFileSync(ПРАЙС, 'utf8'))
}

export const PRICES: Record<string, Price> = справочник().модели
export const PREFIX_PRICES: Record<string, Price> = справочник()['по префиксу']

/** Цена по id модели: точное совпадение, иначе (как в tools/digest.py) по префиксу. */
export function ценаМодели(model: string, prices: Record<string, Price> = PRICES): Price | undefined {
  if (prices[model]) return prices[model]
  for (const префикс of Object.keys(PREFIX_PRICES).sort((a, b) => b.length - a.length)) {
    if (model.startsWith(префикс)) return PREFIX_PRICES[префикс]
  }
  return undefined
}

/** Схема настроек: поля формы Settings DSH и значения по умолчанию — отсюда. */
/** Тип настроек — выход схемы: одно объявление на форму и на валидацию. */
export type Config = ReturnType<typeof Config>
export const Config = z.object({
  provider: z.string().default('opencode-go').description("Провайдер ребёнка. Пусто — модель родителя. Нет такого маршрута в DSH — тоже модель родителя."),
  model: z.string().default('glm-5.3-flash').description("Модель ребёнка. Пусто — модель родителя."),
  subagentProvider: z.string().description("Провайдер ctx.subagents"),
  briefTokens: z.number().step(1).min(1).default(3000).description("Потолок брифа от родителя, токенов"),
  resultTokens: z.number().step(1).min(1).default(2000).description("Потолок итога для родителя, токенов"),
  childMaxTokens: z.number().step(1).min(1).description("Потолок вывода ребёнка за шаг, токенов"),
  profile: z.string().description("Метка профиля в строке журнала. По умолчанию — имя профиля DSH."),
  log: z.string().description("Журнал учёта. По умолчанию ~/.<каталог данных>/usage.jsonl."),
  prices: z.dict(z.array(z.number())).description("Дополнительные или переопределённые цены по id модели."),
})

export const DEFAULTS = {
  provider: 'opencode-go',
  model: 'glm-5.3-flash',
  subagentProvider: 'spawn',
  briefTokens: 3000,
  resultTokens: 2000,
  childMaxTokens: 8000,
}

/** Инструменты, которые ребёнку разрешены: только чтение и поиск. `run_code` — транспорт PTC, его вложенные вызовы проходят guard по одному. */
export const READ_TOOLS: readonly string[] = ['read', 'read_image', 'grep', 'glob', 'working_directory', 'run_code']

export const HINT =
  'Ищи и читай через `scout`. Нужно найти, где определено или вызывается X, или прочитать файл, который ты не будешь править, — зови `scout`. ' +
  'Два и больше grep/glob/`grep -rn`/`find`/`sed -n` подряд — это работа `scout`, не делай их сам. ' +
  'Файл, который правишь, читай сам. ' +
  'Бриф — одна самодостаточная строка: «найди, где вызывается parseConfig в src/, верни пути:строки и сигнатуру».'

const CHARS_PER_TOKEN = 4

/** Оценка токенов текста, как у DSH (символы / 4). */
export function tokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN)
}

/**
 * Обрезать текст до `maxTokens` по границе строки, с пометкой о вырезанном.
 * Возвращает текст как есть, если он влезает. Результат всегда ≤ maxTokens.
 */
export function clip(text: string, maxTokens: number, what: string): string {
  const budget = maxTokens * CHARS_PER_TOKEN
  if (text.length <= budget) return text
  const note = `\n[${имя('scout')}: ${what} ≈${tokens(text)} ток. обрезан до ${maxTokens}]`
  let cut = text.slice(0, Math.max(0, budget - note.length))
  const nl = cut.lastIndexOf('\n')
  if (nl > cut.length / 2) cut = cut.slice(0, nl)
  return cut + note
}

/** Бриф ребёнку: правила разведчика + задание родителя (уже обрезанное). */
export function childPrompt(brief: string, resultTokens: number): string {
  return [
    'Ты разведчик. Твоя работа — найти и прочитать нужное в файлах и коротко доложить.',
    `Разрешено только чтение: ${READ_TOOLS.filter(t => t !== 'run_code').join(', ')}. ` +
      'Ничего не создавай, не правь и не запускай — запись, bash и остальные инструменты закрыты и вернут отказ.',
    `Итог — одним сообщением, не длиннее ~${resultTokens} токенов (≈${resultTokens * CHARS_PER_TOKEN} символов): ` +
      'факты, пути с номерами строк, короткие точные цитаты. Без пересказа хода работы и без вступлений. ' +
      'Если не нашёл — так и скажи и перечисли, где искал.',
    '',
    'Задание:',
    brief,
  ].join('\n')
}

/** Текст итога ребёнка: текстовые блоки последнего сообщения. */
export function textOf(output: readonly ContentBlock[]): string {
  return output.map(b => (b.type === 'text' ? b.text : '')).filter(Boolean).join('\n').trim()
}

/** Решение guard: undefined — можно; строка — отказ. */
export function denyForScout(tool: string): string | undefined {
  if (READ_TOOLS.includes(tool)) return undefined
  return `${имя('scout')}: разведчик только читает — инструмент \`${tool}\` закрыт. Доступны: ${READ_TOOLS.join(', ')}.`
}

/** $ по usage и цене. */
export function usdOf(u: TokenUsage, p: Price): number {
  return ((u.inputTokens ?? 0) * p[0] + (u.cacheReadTokens ?? 0) * p[1] +
    (u.cacheWriteTokens ?? 0) * p[2] + (u.outputTokens ?? 0) * p[3]) / 1e6
}

/** Сервис LLM DSH — только список маршрутов. */
interface LlmService {
  listProviders(): ReadonlyArray<{ id: string }>
}

/**
 * Маршруты провайдеров, зарегистрированные в DSH, или undefined, если узнать нельзя
 * (тогда маршрут проверит сам запуск ребёнка и запасной путь).
 */
export function маршруты(ctx: Pick<Context, 'get'>): Set<string> | undefined {
  try {
    const llm = ctx.get('llm' as never) as LlmService | undefined
    const список = llm?.listProviders?.()
    return список ? new Set(список.map(p => p.id)) : undefined
  } catch {
    return undefined
  }
}

/**
 * Чем пускать ребёнка: своей дешёвой моделью или моделью родителя (undefined в agentOptions —
 * DSH берёт маршрут родителя). У человека может не быть подписки OpenCode Go — тогда маршрута
 * `opencode-go` в DSH нет, и ребёнок падал «no adapter registered»; разведчик не должен блокировать.
 */
export function выборМодели(
  cfg: { provider?: string; model?: string },
  есть: Set<string> | undefined,
): { provider: string; model: string } | { причина: string } {
  if (!cfg.provider || !cfg.model) return { причина: 'модель не задана' }
  if (есть && !есть.has(cfg.provider)) return { причина: `нет маршрута ${cfg.provider}` }
  return { provider: cfg.provider, model: cfg.model }
}

/** Глубина делегирования агента (как delegationDepthOf в dsh-subagent, без импорта значения). */
function depthOf(agent: Agent): number {
  const runtime = (agent.options as { subagentDepth?: number }).subagentDepth ?? 0
  return Math.max(agent.session.header.delegationDepth ?? 0, runtime)
}

/** Учёт одного ребёнка. */
interface ChildUsage {
  steps: number
  usd: number
  inputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
  outputTokens: number
}

interface PromptService {
  section(section: { name: string; order: number; text: (context: { agent?: Agent }) => string }): () => void
  getSectionOrder(name: string): number
}

export interface ScoutArgs {
  brief: string
  description?: string
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
  const cfg = { ...DEFAULTS, ...config }
  const log = ctx.logger(name)
  // Свой журнал — в новый каталог, читатели смотрят и в старый (до E16-S02)
  const usageLog = config.log ?? журнал(homedir())
  const prices = { ...PRICES, ...цены(config.prices) }

  /** Живые дети разведчика: childId → учёт. */
  const children = new Map<string, ChildUsage>()

  // Ребёнок только читает: глобальный guard, сессии разведчика — по childId.
  ctx.tools.guard((exec: Readonly<ToolExecution>) => {
    const sid = exec.agent?.session.id
    if (sid === undefined || !children.has(sid)) return undefined
    const reason = denyForScout(exec.name)
    if (reason) log.info('ребёнок %s: отказ в %s', sid, exec.name)
    return reason
  })

  // $ ребёнка: шаги из его журнала (только чтение события).
  ctx.on('session/event', (session, event) => {
    const c = children.get(session.id)
    if (!c || event.type !== 'assistant/message') return
    const data = (event as { data?: { usage?: TokenUsage; message?: { source?: { model?: string } } } }).data
    const u = data?.usage
    if (!u) return
    const model = data?.message?.source?.model ?? cfg.model
    const p = ценаМодели(model, prices) ?? [0, 0, 0, 0]
    c.steps++
    c.usd += usdOf(u, p)
    c.inputTokens += u.inputTokens ?? 0
    c.cacheReadTokens += u.cacheReadTokens ?? 0
    c.cacheWriteTokens += u.cacheWriteTokens ?? 0
    c.outputTokens += u.outputTokens ?? 0
  })

  function record(row: Record<string, unknown>) {
    try {
      mkdirSync(dirname(usageLog), { recursive: true })
      appendFileSync(usageLog, JSON.stringify(row) + '\n')
    } catch (error) {
      log.warn('не записал строку scout в %s: %s', usageLog, error)
    }
  }

  /** Причина, по которой своя модель ребёнка больше не пробуется в этом процессе. */
  let маршрутСломан: string | undefined

  /** Один запуск ребёнка и строка журнала. `модель` undefined — модель родителя. */
  async function запуск(
    parent: Agent, brief: string, raw: string, args: ScoutArgs, exec: ToolRunContext,
    модель: { provider: string; model: string } | undefined, запасной: string | undefined,
  ) {
    const started = Date.now()
    const childId = randomUUID()
    const usage: ChildUsage = { steps: 0, usd: 0, inputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, outputTokens: 0 }
    children.set(childId, usage)
    let result: SubagentResult | undefined
    let failure: unknown
    try {
      const activation = await ctx.subagents.startActivation({
        provider: cfg.subagentProvider,
        label: (args.description ?? 'scout').slice(0, 80),
        childId: childId as SessionId,
        parent,
        prompt: [{ type: 'text', text: childPrompt(brief, cfg.resultTokens) }],
        agentOptions: { ...(модель ?? {}), maxTokens: cfg.childMaxTokens },
        signal: exec.signal,
        delivery: 'caller',
      })
      try {
        result = await activation.result
      } finally {
        await activation.dispose().catch((error: unknown) => log.warn('dispose %s: %s', childId, error))
      }
    } catch (error) {
      failure = error
    } finally {
      children.delete(childId)
    }

    const родитель = модель ? undefined : текущаяМодель(parent)
    const full = result ? textOf(result.output) : ''
    const text = clip(full, cfg.resultTokens, 'итог')
    const stop = result?.stopReason ?? 'failed'
    record({
      ts: new Date().toISOString(),
      kind: 'scout',
      pkg: name, version: VERSION,
      profile: профиль(ctx, config.profile),
      session: parent.session.id,
      child: childId,
      provider: модель?.provider ?? родитель?.provider, model: модель?.model ?? родитель?.model,
      ...(запасной ? { fallback: запасной } : {}),
      stop,
      ms: Date.now() - started,
      steps: usage.steps,
      usd: round(usage.usd, 6),
      inputTokens: usage.inputTokens,
      cacheReadTokens: usage.cacheReadTokens,
      cacheWriteTokens: usage.cacheWriteTokens,
      outputTokens: usage.outputTokens,
      briefTokens: tokens(raw),
      briefClipped: brief !== raw,
      resultTokens: tokens(full),
      resultClipped: text !== full,
    })
    log.info('scout %s: %s, %d шагов, $%s, бриф %d → итог %d ток.', childId, stop,
      usage.steps, round(usage.usd, 6), tokens(raw), tokens(full))
    return { result, failure, text, stop, steps: usage.steps }
  }

  async function run(args: ScoutArgs, exec: ToolRunContext): Promise<string> {
    const parent = exec.agent
    if (!parent) throw new Error('scout: нет вызывающего агента')
    if (children.has(parent.session.id) || depthOf(parent) > 0) {
      throw new Error('scout: разведчик не запускает разведчиков — работай сам')
    }
    const raw = String(args.brief ?? '').trim()
    if (!raw) throw new Error('scout: пустой бриф')
    const brief = clip(raw, cfg.briefTokens, 'бриф')
    const выбор = маршрутСломан ? { причина: маршрутСломан } : выборМодели(cfg, маршруты(ctx))
    let попытка = await запуск(parent, brief, raw, args, exec, 'provider' in выбор ? выбор : undefined,
      'причина' in выбор && cfg.provider ? выбор.причина : undefined)
    // Своя модель упала, не сделав ни шага (нет ключа, нет маршрута, регион, нет модели) —
    // повтор на модели родителя: разведчик дешевле, но не обязателен.
    if ('provider' in выбор && попытка.stop !== 'completed' && попытка.steps === 0 && !exec.signal?.aborted) {
      маршрутСломан = `${выбор.provider}/${выбор.model} не ответила`
      log.warn('scout: %s — дальше модель родителя', маршрутСломан)
      попытка = await запуск(parent, brief, raw, args, exec, undefined, маршрутСломан)
    }
    const { result, failure, text } = попытка

    if (failure !== undefined) throw failure
    if (result!.stopReason !== 'completed') {
      const why = result!.diagnostic ? `: ${result!.diagnostic}` : ''
      const head = `[scout: ${result!.stopReason}${why}]`
      return text ? `${head}\n${text}` : head
    }
    return text || '[scout: пустой итог]'
  }

  const tool: ToolDefinition = {
    name: 'scout',
    description:
      'Разведчик: поиск и чтение файлов на дешёвой модели в отдельном контексте. Дай самодостаточный бриф ' +
      `(≤ ${cfg.briefTokens} ток.: что найти, где, что вернуть) — получишь сжатый итог ≤ ${cfg.resultTokens} ток. ` +
      'в этом же шаге. Разведчик не видит эту беседу и только читает (read/grep/glob) — правок не делает. ' +
      HINT,
    parameters: {
      type: 'object',
      properties: {
        brief: {
          type: 'string',
          description: 'Самодостаточное задание разведчику: что найти или прочитать, где искать, в каком виде вернуть. Длиннее ' +
            `${cfg.briefTokens} ток. — обрежется.`,
        },
        description: { type: 'string', description: 'Короткая подпись (3–5 слов) для показа.' },
      },
      required: ['brief'],
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: String(value) }],
    },
    isConcurrencySafe: () => true,
    execute(args, exec) {
      return run(args as ScoutArgs, exec)
    },
  }
  ctx.tools.register(tool)

  // Подсказка основной модели — только корневому агенту (у детей глубина > 0).
  const prompt = (ctx as unknown as { systemPrompt: PromptService }).systemPrompt
  prompt.section({
    name: 'tool:scout',
    order: prompt.getSectionOrder('TOOL_GREP') + 1,
    text: (context) => (context.agent && depthOf(context.agent) === 0 && !children.has(context.agent.session.id) ? HINT : ''),
  })

  log.info('scout: %s/%s (нет маршрута — модель родителя), бриф ≤ %d, итог ≤ %d ток.', cfg.provider, cfg.model, cfg.briefTokens, cfg.resultTokens)
}

/** Модель родителя для строки журнала — что DSH выберет для его следующего шага. */
function текущаяМодель(agent: Agent): { provider?: string; model?: string } | undefined {
  try {
    const m = (agent as { model?: { current?: () => { provider: string; model: string } | undefined } }).model?.current?.()
    if (m) return m
  } catch { /* нет выбора — смотрим options */ }
  const o = agent.options as { provider?: string; model?: string } | undefined
  return o ? { provider: o.provider, model: o.model } : undefined
}

function round(x: number, digits = 4) {
  const k = 10 ** digits
  return Math.round(x * k) / k
}
