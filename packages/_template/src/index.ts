// <корень>-NAME — ОПИСАНИЕ.
//
// Правила пакета:
// - только `import type` из @deepseek-ai/*: пакет грузится по абсолютному пути
//   из ~/.<каталог данных>/packages/<id>/ и своих node_modules там не имеет;
// - Node-встроенные модули (node:fs, node:path…) импортировать можно;
// - события и сервисы — только документированные (docs/subsystems/* в пакете dsh).
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'

import { имя } from './brand.js'

export const name = имя('NAME')

export interface Config {}

export function apply(ctx: Context, _config: Config = {}) {
  ctx.on('agent/turn-stopping', ({ agent, turn }) => {
    ctx.logger(name).debug('turn %d of %s', turn, agent.id)
  })
}
