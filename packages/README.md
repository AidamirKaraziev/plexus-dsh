# Пакеты Plexus DSH

Плагины Cordis для DeepSeek Harness (DSH `@deepseek-ai/dsh@0.2.2-rc.5`): плагины, а не форк DSH. Каждый пакет — отдельный каталог `plexus-dsh-*/` со своим `package.json`, `src/` (TypeScript), `test/` и `locale/`.

```
packages/
  package.json, pnpm-workspace.yaml   монорепо (pnpm), общие devDependencies: typescript + типы DSH
  tsconfig.base.json                  общие настройки TS (ESM, NodeNext, strict)
  _template/                          шаблон пакета (не собирается)
  plexus-dsh-budget/                  стоп-кран: предупреждение у порога бюджета, стоп хода при бюджете, окне и шагах без прогресса (в бандл не входит)
  plexus-dsh-bundle/                  бандл: патч для `dsh plugin add`, состав meter, warm, read-cap, scout (кода нет)
  plexus-dsh-hello/                   проверка каркаса: событие plexus-dsh-hello в журнал сессии (в бандл не входит)
  plexus-dsh-meter/                   счётчик окна: после хода $ и % окна за 5 ч по всем сессиям в usage.jsonl
  plexus-dsh-read-cap/                большие результаты read, grep и bash режутся: голова и хвост до порога токенов
  plexus-dsh-scout/                   инструмент scout: поиск и чтение в дочерней сессии на дешёвой модели
  plexus-dsh-warm/                    кэш тёплый в паузе: пинг повтором последнего запроса модели
```

В бандл входят пакеты, у которых в `package.json` нет `plexus.bundle.build: false`: сейчас meter, warm, read-cap и scout. `budget` и `hello` ставятся поштучно.

## plexus-dsh-meter

На `agent/turn-stopping` считает $ всех шагов (`assistant/message` с usage) за скользящие часы окна по всем сессиям DSH: живые сессии процесса берёт из `session/event` (только чтение), остальные читает с диска `~/.dsh/sessions/*/*/session*.jsonl.zstd` (изменённые за окно, кэш по mtime и размеру). Цены берёт из `prices.json` (копия справочника цен проекта, кладётся при сборке). Префикс форка до `session/end-seed` не считается. В журнал сессии не пишет ничего; результат дописывается строкой в `~/.plexus-dsh/usage.jsonl`:

`{ts, session, turn, profile, session_usd, session_steps, window_usd, window_pct, window_steps, window_sessions, window_frees_at, by_model}`

Config: `profile`, `windowHours` (5), `budgetUsd` (12), `log`, `sessionsDir`, `prices` (дополнительные цены по id модели).

## plexus-dsh-warm

Держит кэш провайдера тёплым в паузе человека. Перехват: waterfall `llm/stream` — запрос хода (`sessionId` есть, `purpose` нет) запоминается по ссылке, из `usage` ответа берётся размер контекста. На `agent/turn-stopping` взводится таймер в процессе DSH (`unref`); раз в `pingEveryMin` пакет повторяет запрос через `ctx.llm.stream` с `maxTokens: 1`. Стоп: новый шаг (`agent/pre-step`) или запрос, `maxPauseMin`, контекст меньше `minContextTokens`, `session/disposed`. Параметры по модели (`ttlMin`, `pingEveryMin`, `maxPauseMin`, `price`) берутся из `providers.json` рядом с `package.json`; явные значения в Config их перекрывают.

Пинг выключен по умолчанию: в схеме `live: false` — только запись в лог. Чтобы слать пинг на самом деле, нужен Config `live: true`. Каждый пинг пишет строку `kind: ping` в `usage.jsonl`; в журнал сессии пакет не пишет. Headless-процесс между ходами не живёт — там таймеру некому сработать.

Config: `profile`, `pingEveryMin`, `maxPauseMin`, `minContextTokens` (30000), `live` (false), `log`, `providers`.

## plexus-dsh-read-cap

Waterfall `tools/post-execute`: результат `read`, `grep` или `bash` (корневой вызов, только текстовые блоки, без `value`) больше `maxTokens` заменяется головой и хвостом по границам строк. В разрыве стоит пометка `[plexus-dsh-read-cap: … вырезаны строки a–b из N. …]` с подсказкой, как дочитать (read с offset/limit, точнее grep, прицельный вывод bash). Токены считаются оценкой DSH: символы / 4 + 4 на блок. Правка идёт до записи результата в историю, префикс кэша не ломается; своих событий в журнал не пишет — в журнале обычный, уже обрезанный `tool/result`.

Config: `maxTokens` (6000), `tools` (`read`, `grep`, `bash`), `headShare` (доля бюджета на голову, в коде 0.6).

## plexus-dsh-scout

Инструмент `scout`: поиск и чтение уходят из основной сессии в дочернюю (spawn через `ctx.subagents`, `delivery: 'caller'`) на дешёвой модели; родителю возвращается только итог. Ребёнку разрешены только инструменты чтения (`read`, `read_image`, `grep`, `glob`, `working_directory`, `run_code`) — остальные отклоняет `ctx.tools.guard`. Расход ребёнка пишется строкой `kind: scout` в `usage.jsonl`. В системный промпт добавляется подсказка, когда звать `scout`.

Config: `provider` (`opencode-go`), `model` (`glm-5.3-flash`), `subagentProvider` (`spawn`), `briefTokens` (3000, потолок брифа), `resultTokens` (2000, потолок итога), `childMaxTokens` (8000), `profile`, `log`, `prices`.

## plexus-dsh-budget

Стоп-кран. Перед шагом (`agent/pre-step`) решает по порогам: у `warnPct` бюджета добавляет одно уведомление модели (обычное `user/message` в конце истории); при исчерпании бюджета сессии `budgetUsd`, при заполнении окна `windowPct`, после `noProgressSteps` шагов без прогресса или `repeatErrors` повторов одной ошибки отменяет ход штатно (`turn/end` с причиной `hook`, очередь человека сохраняется). Шаг с сообщением человека не стопорится. Базу $ берёт из `usage.jsonl` (его пишет meter), внутри хода досчитывает по `session/event`. Стоп пишется строкой `kind: stop` в `usage.jsonl`; в журнал сессии пакет не пишет. Не входит в бандл: `plexus.bundle.build: false`.

Config: `profile`, `budgetUsd` (1; 0 — без бюджета), `warnPct`, `windowPct` (90), `noProgressSteps` (8; 0 — выкл.), `repeatErrors`, `log`, `prices`.

## plexus-dsh-bundle

Пакет без кода: `cordis.patch.yml` (генерируется `tools/bundle.mjs`, вручную не править), иконка, локали и описание. Патч вставляет в профиль DSH строку на каждый пакет с `plexus.bundle.build !== false` вместе с его Config по умолчанию. Зависимости (`dependencies`, `bundledDependencies`) — те же пакеты. Тест сверяет состав патча и зависимостей с каталогами пакетов.

## plexus-dsh-hello

Проверка каркаса. На `agent/turn-stopping` пишет в журнал сессии событие с ключом `plexus-dsh-hello` (константа `ПАКЕТ` из `src/types.ts`) и данными `{pkg, version, turn, label?}`. Config: `label` (метка, чтобы отличать профили). Не входит в бандл.

## Установка

Основной способ: бандлом через DSH.

```sh
dsh plugin --profile <профиль> add <путь-к-tarball-или-каталогу-бандла>
```

Одна строка в Plugins ставит все пакеты бандла (meter, warm, read-cap, scout), настройки каждого — в Settings DSH. `budget` и `hello` в бандл не входят.

Для профилей `bench-*` и поштучной установки — `tools/install.sh`:

```sh
tools/install.sh               # собрать, скопировать в ~/.plexus-dsh/packages, вписать в профили desktop и headless
tools/install.sh --uninstall   # снять блок Plexus DSH из патчей профилей
tools/install.sh --no-build    # без сборки;  --profiles "web" — другие профили
tools/install.sh --profiles bench-warm --from bench-base --packages "plexus-dsh-meter plexus-dsh-warm"
                               # профиль-копия bench-base + названные пакеты; bench-base не меняется
```

Пакет с `plexus.bundle.build: false` собирается, но в профили без явного `--packages` не вписывается.

Что делает `tools/install.sh`:
1. `pnpm install` и `tsc -b` для каждого `plexus-dsh-*/`.
2. Копирует `lib/` и `package.json` каждого пакета в `~/.plexus-dsh/packages/<id>/` — профили не зависят от того, где лежит клон.
3. В `~/.dsh/profiles/<профиль>/cordis.patch.yml` вписывает один блок между маркерами `# >>> plexus-dsh packages` и `# <<< plexus-dsh packages` (строка на пакет: `id`, абсолютный `name`, `config`). Остальной файл не трогается; повторный запуск заменяет блок. Перед первой правкой делается копия `cordis.patch.yml.plexus-dsh.bak`.

Записи Plexus DSH необязательны (не `required`): если пакет не загрузится, DSH предупредит и продолжит.

## Тесты

```sh
pnpm -C packages test
```

Запускает `test` каждого пакета (`pnpm run -r test`): `tsc -b`, затем `node --test` — встроенный раннер Node, без сети и без DSH, `ctx` подделан. Тесты пакетов лежат в `test/*.test.mjs` (budget — `src/*.test.ts`, собираются в `lib/`). Образец — `plexus-dsh-hello/test/hello.test.mjs`:

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { apply } from '../lib/index.js'

test('apply: сбой append не валит ход — предупреждение в лог', () => {
  const ctx = fakeCtx()            // поддельный ctx: handlers, warns
  apply(ctx, {})
  const agent = { session: { append: () => { throw new Error('диск') } } }
  assert.doesNotThrow(() => ctx.handlers['agent/turn-stopping'][0]({ agent, turn: 1 }))
  assert.equal(ctx.warns.length, 1)
})
```

## Новый пакет

```sh
cp -R packages/_template packages/plexus-dsh-имя
# заменить NAME/ОПИСАНИЕ в package.json и src/index.ts
tools/install.sh
```

- `package.json` → `plexus.id` — id записи в патче; `plexus.config` — её `config`, строка `{profile}` заменяется именем профиля; `plexus.bundle.build: false` оставляет пакет вне бандла.
- Из `@deepseek-ai/*` — только `import type` (кроме `schemastery` для Config): пакет грузится по абсолютному пути из `~/.plexus-dsh/packages`, своих `node_modules` там нет, а второй экземпляр модулей DSH сломал бы общие классы. Встроенные модули Node (`node:fs`, `node:path`) можно.
- Нужен ещё тип DSH — добавить его пакет в `devDependencies` корневого `packages/package.json` той же версии `0.2.2-rc.5`.
- Только документированные точки расширения (`docs/subsystems/*` в пакете `@deepseek-ai/dsh`): события `agent/*`, `session/*`, `tools/*`, сервисы через `inject`.

## Своё событие в журнале сессии

Тип объявляется слиянием деклараций `SessionEventMap` (`declare module '@deepseek-ai/dsh-session'`) и пишется `agent.session.append(ключ, data)` — см. `plexus-dsh-hello/src/index.ts`. Ключ события hello — `plexus-dsh-hello`. Событие log-only: в историю модели не попадает, видно в `~/.dsh/sessions/<cwd>/<session>/session.v4.jsonl.zstd` (`zstd -dc … | grep plexus-dsh`).

Писать в открытом ходе — из `agent/turn-stopping`, `agent/pre-step`, `tools/*`; из слушателя `session/event` писать нельзя (повторная публикация запрещена).

Риск: такое событие не помечено `ignorable` (у внешних плагинов нет документированного способа). DSH 0.2.2-rc.5 при чтении неизвестные типы не отвергает, но по контракту читатель вправе отказаться восстанавливать сессию без плагина. Пишите в журнал только то, что нужно в нём; объёмные данные — в свои файлы (`~/.plexus-dsh/…`), как meter. Остальные пакеты в журнал сессии ничего не пишут.
