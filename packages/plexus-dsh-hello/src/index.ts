// имя('hello') — минимальный плагин Cordis для DSH: проверяет, что
// пакет загружен в профиль и умеет писать в журнал сессии.
//
// На каждый `agent/turn-stopping` (ход вот-вот закроется, ход ещё открыт)
// дописывает в сессию событие `<корень>/hello` — его видно в session.v4.jsonl.zstd.
//
// Только `import type`: в рантайме пакет ничего не импортирует, поэтому грузится
// по абсолютному пути из ~/.<каталог данных>/packages без своих node_modules.
import z from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import { имя } from './brand.js'
import { ПАКЕТ } from './types.js'

export const name = имя('hello')

/** Схема настроек: поля формы Settings DSH и значения по умолчанию — отсюда. */
/** Тип настроек — выход схемы: одно объявление на форму и на валидацию. */
export type Config = ReturnType<typeof Config>
export const Config = z.object({
  label: z.string().description("Метка в событии, чтобы отличать профили"),
})

export interface HelloEvent {
  pkg: string
  version: string
  turn: number
  label?: string
}

// Свой тип события в журнале сессии (документированная точка расширения:
// declaration merge SessionEventMap). Тип log-only, в историю модели не попадает.
declare module '@deepseek-ai/dsh-session' {
  interface SessionEventMap {
    [ПАКЕТ]: HelloEvent
  }
}

const VERSION = '0.1.0'

export function apply(ctx: Context, config: Config = Config({})) {
  ctx.on('agent/turn-stopping', ({ agent, turn }) => {
    try {
      agent.session.append(ПАКЕТ, {
        pkg: имя('hello'),
        version: VERSION,
        turn,
        ...(config.label ? { label: config.label } : {}),
      })
    } catch (error) {
      ctx.logger(имя('hello')).warn(`не удалось записать ${имя('hello')}: %s`, error)
    }
  })
}
