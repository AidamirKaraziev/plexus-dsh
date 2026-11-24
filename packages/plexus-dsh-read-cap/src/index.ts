// <корень>-read-cap — большие результаты `read`/`grep`/`bash` режутся в момент результата:
// голова и хвост до `maxTokens` плюс подсказка «читай с offset/limit или grep».
//
// Основание — vault/решения «Большие чтения режем в момент чтения»: `spill-policy`
// пропускает `read`, и файл на 10–30K символов живёт в контексте до конца сессии.
// Правка идёт в waterfall `tools/post-execute` (@deepseek-ai/dsh-tools) — до того,
// как результат ляжет в историю, т. е. в конец префикса: кэш провайдера не ломается.
//
// В журнал сессии DSH пакет НИЧЕГО своего не пишет (vault/решения «События плагинов
// продукта ломают возобновление сессии DSH»): в журнал попадает обычный `tool/result`,
// просто уже обрезанный.
//
// Токены — та же оценка, что у DSH (dsh-token-meter/estimate): символы / 4 + 4 на блок.
//
// Из @deepseek-ai/* — только `import type`, кроме schemastery: он даёт `Config`.
import z from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import type { PostToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'
import { имя } from './brand.js'

export const name = имя('read-cap')
/** `tools` — реестр инструментов dsh-tools: без него событий post-execute нет. */
export const inject = ['tools']

/** Схема настроек: поля формы Settings DSH и значения по умолчанию — отсюда. */
/** Тип настроек — выход схемы: одно объявление на форму и на валидацию. */
export type Config = ReturnType<typeof Config>
export const Config = z.object({
  maxTokens: z.number().step(1).min(1).default(6000).description("Порог результата, токенов"),
  tools: z.array(z.string()).default(['read', 'grep', 'bash']).description("Какие инструменты резать"),
  headShare: z.number().min(0).max(1).description("Доля бюджета на голову, остальное — хвост"),
})

const CHARS_PER_TOKEN = 4
const BLOCK_OVERHEAD = 4

/** Оценка токенов блоков, как у DSH; не-текст — по JSON. */
export function estimate(blocks: readonly ContentBlock[]): number {
  let t = 0
  for (const b of blocks) {
    const n = b.type === 'text' ? b.text.length : JSON.stringify(b).length
    t += Math.ceil(n / CHARS_PER_TOKEN) + BLOCK_OVERHEAD
  }
  return t
}

/** Подсказка в разрыве: что вырезано и как дочитать. */
export function hint(tool: string, totalTokens: number, cap: number, cutLines: [number, number], totalLines: number): string {
  const how = tool === 'read'
    ? 'Нужный кусок читай read с offset/limit (номера строк слева) или найди grep.'
    : tool === 'grep'
      ? 'Сузь поиск: точнее pattern, path/glob или меньше контекста.'
      : 'Нужное выведи прицельно: grep, head/tail, sed -n "a,bp" или в файл и read с offset/limit.'
  return `\n[${имя('read-cap')}: результат ${tool} ≈${totalTokens} ток. больше порога ${cap}; `
    + `вырезаны строки ${cutLines[0]}–${cutLines[1]} из ${totalLines}. ${how}]\n`
}

/**
 * Обрезать текст до `budgetChars`: голова и хвост по границам строк, подсказка в разрыве.
 * Возвращает undefined, если резать нечего.
 */
export function cut(text: string, budgetChars: number, headShare: number,
  note: (cutLines: [number, number], totalLines: number) => string): string | undefined {
  if (text.length <= budgetChars) return undefined
  const lines = text.split('\n')
  const headBudget = Math.floor(budgetChars * headShare)
  const tailBudget = budgetChars - headBudget
  let h = 0, used = 0
  while (h < lines.length && used + lines[h]!.length + 1 <= headBudget) used += lines[h++]!.length + 1
  let t = lines.length, usedT = 0
  while (t > h && usedT + lines[t - 1]!.length + 1 <= tailBudget) usedT += lines[--t]!.length + 1
  if (t <= h) return undefined
  let head = lines.slice(0, h).join('\n')
  // одна строка длиннее бюджета (минифицированный вывод) — режем посимвольно
  if (h === 0) head = text.slice(0, headBudget)
  let tail = lines.slice(t).join('\n')
  if (t === lines.length) tail = text.slice(-tailBudget)
  return head + note([h + 1, t], lines.length) + tail
}

export function apply(ctx: Context, config: Config = Config({})) {
  const log = ctx.logger(name)
  const cap = config.maxTokens ?? 6000
  const tools = new Set(config.tools ?? ['read', 'grep', 'bash'])
  const headShare = config.headShare ?? 0.6

  /** Обрезанный контент или undefined, если влезает. */
  function capContent(tool: string, content: readonly ContentBlock[]): ContentBlock[] | undefined {
    if (!content.every(b => b.type === 'text')) return undefined
    const total = estimate(content)
    if (total <= cap) return undefined
    const text = content.map(b => (b as Extract<ContentBlock, { type: 'text' }>).text).join('')
    // запас на подсказку (~70 ток.) и рамку блока
    const budgetChars = Math.max(0, (cap - 100) * CHARS_PER_TOKEN)
    const out = cut(text, budgetChars, headShare, (c, n) => hint(tool, total, cap, c, n))
    if (out === undefined) return undefined
    return [{ type: 'text', text: out }]
  }

  ctx.on('tools/post-execute', async (exec: ToolExecution, result, next): Promise<PostToolDecision> => {
    const decision = await next()
    if (!tools.has(exec.name) || exec.parent !== undefined) return decision
    if (decision.kind !== 'accept' || Object.hasOwn(decision, 'value')) return decision
    const content = decision.content ?? result.content
    const capped = capContent(exec.name, content)
    if (capped === undefined) return decision
    log.debug('%s %s: %d → %d ток.', exec.name, exec.callId, estimate(content), estimate(capped))
    const extra = decision.additionalContexts
    return { kind: 'accept', content: capped, ...extra ? { additionalContexts: extra } : {} }
  })
}
