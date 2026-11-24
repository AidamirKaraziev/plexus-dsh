# Пакеты Plexus DSH

Плагины Cordis для DeepSeek Harness (DSH `@deepseek-ai/dsh@0.2.2-rc.5`). Почему плагины, а не форк — [[DSH — основа Plexus DSH, плагины, а не форк]].

```
packages/
  package.json, pnpm-workspace.yaml   монорепо (pnpm), общие devDependencies: typescript + типы DSH
  tsconfig.base.json                  общие настройки TS (ESM, NodeNext, strict)
  _template/                          шаблон пакета (не собирается)
  plexus-dsh-hello/                   проверка каркаса: событие plexus-dsh/hello в журнал сессии (в бандл не входит: plexus.bundle.build false)
  plexus-dsh-meter/                   счётчик окна Go: после хода $ и % окна за 5 ч по всем сессиям → ~/.plexus-dsh/usage.jsonl
  plexus-dsh-warm/                    кэш тёплый в паузе: повтор последнего запроса (каркас E03-S01, пинг — S03)
  plexus-dsh-read-cap/                большие результаты read/grep/bash — голова и хвост до 6K ток. (E04-S01)
```

## plexus-dsh-meter

На `agent/turn-stopping` считает $ всех шагов (`assistant/message` с usage) за скользящие 5 ч по всем сессиям DSH: живые сессии процесса — из `session/event` (только чтение), остальные — с диска `~/.dsh/sessions/*/*/session*.jsonl.zstd` (изменённые за окно, кэш по mtime/size). Цены — копия `PRICES` из `tools/dsh_digest.py` (менять вместе). Префикс форка до `session/end-seed` не считается. В журнал сессии DSH не пишет ничего. Строка `~/.plexus-dsh/usage.jsonl`:

`{ts, session, turn, profile, session_usd, session_steps, window_usd, window_pct, window_steps, window_sessions, window_frees_at, by_model}`

Config: `windowHours` (5), `budgetUsd` (12), `log`, `sessionsDir`, `prices` (доп. цены по id модели).

## plexus-dsh-warm

Каркас (E03-S01). Перехват — waterfall `llm/stream` (dsh-llm): запрос хода (`sessionId` есть, `purpose` нет) запоминается по ссылке, из `usage` ответа — размер контекста. На `agent/turn-stopping` взводится таймер (в процессе DSH, `unref`); стоп — новый шаг (`agent/pre-step`) или запрос, `maxPauseMin` (25), контекст < `minContextTokens` (30K), `session/disposed`. Пинг — `ctx.llm.stream({...запрос, maxTokens: 1})`, пока заглушка (`live: false` → только лог). В журнал сессии не пишет. Headless между ходами не живёт — способ для эталона в vault/решения «Пинг кэша — таймер в процессе DSH, не внешний процесс». Ставится только явно: профиль `bench-warm`.

## plexus-dsh-read-cap

E04-S01, vault/решения «Большие чтения режем в момент чтения». Waterfall `tools/post-execute` (dsh-tools): результат `read`/`grep`/`bash` (корневой вызов, только текст, без `value`) больше `maxTokens` (6000; оценка DSH — символы / 4 + 4 на блок) заменяется головой (60 %) и хвостом по границам строк, в разрыве — `[plexus-dsh-read-cap: … вырезаны строки a–b из N. …читай read с offset/limit…]`. Правка до записи результата в историю — префикс кэша не ломается; своих событий в журнал не пишет (в журнале обычный `tool/result`, уже обрезанный). Config: `maxTokens`, `tools`, `headShare`. Ставится только явно: профиль `bench-e04` (`tools/bench.py --profile e04`). Замер — `tools/digest.py <сессия> --json | jq .tools` (число и max токенов результатов по инструменту).

## Установить / снять

```sh
tools/install.sh               # собрать, скопировать в ~/.plexus-dsh/packages, вписать в профили desktop и headless
tools/install.sh --uninstall   # снять блок Plexus DSH из патчей профилей
tools/install.sh --no-build    # без сборки;  --profiles "web" — другие профили
tools/install.sh --profiles bench-warm --from bench-base --packages "plexus-dsh-meter plexus-dsh-warm"
                               # профиль-копия bench-base + названные пакеты; bench-base не меняется
```

Пакет с `plexus.bundle.build: false` в `package.json` собирается, но в бандл и профили не вписывается (кроме явного `--packages`).

Что делает установка:
1. `pnpm install` + `tsc -b` для каждого `plexus-dsh-*/`.
2. Копирует `lib/` и `package.json` каждого пакета в `~/.plexus-dsh/packages/<id>/` — профили не зависят от того, где лежит клон (worktree можно удалить).
3. В `~/.dsh/profiles/<профиль>/cordis.patch.yml` вписывает **один блок между маркерами** `# >>> plexus-dsh packages` … `# <<< plexus-dsh packages`: `- insert:` со строкой на пакет (`id`, абсолютный `name`, `config`). Остальной файл с комментариями не трогается; повторный запуск заменяет блок. Перед первой правкой — `cordis.patch.yml.plexus-dsh.bak`.

Записи Plexus DSH — необязательные (не `required`): если пакет не загрузится, DSH предупредит и продолжит.
Desktop подхватывает патч при следующем запуске (или по HMR, если включён).

## Новый пакет

```sh
cp -R packages/_template packages/plexus-dsh-meter
# заменить NAME/ОПИСАНИЕ в package.json и src/index.ts
tools/install.sh
```

- `package.json → plexus.id` — id записи в патче; `plexus.config` — её `config`, строка `{profile}` заменяется именем профиля.
- Из `@deepseek-ai/*` — **только `import type`**: пакет грузится по абсолютному пути из `~/.plexus-dsh/packages`, своих `node_modules` там нет, а второй экземпляр модулей DSH сломал бы общие классы. Встроенные модули Node (`node:fs`, `node:path`) — можно.
- Нужен ещё тип DSH — добавить его пакет в `devDependencies` корневого `packages/package.json` той же версии `0.2.2-rc.5`.
- Только документированные точки расширения (`docs/subsystems/*` в пакете `@deepseek-ai/dsh`): события `agent/*`, `session/*`, `tools/*`, сервисы через `inject`.

## Своё событие в журнале сессии

Тип объявляется слиянием деклараций `SessionEventMap` (`declare module '@deepseek-ai/dsh-session'`) и пишется `agent.session.append('plexus-dsh/<имя>', data)` — см. `plexus-dsh-hello/src/index.ts`. Событие log-only: в историю модели не попадает, видно в `~/.dsh/sessions/<cwd>/<session>/session.v4.jsonl.zstd` (`zstd -dc … | grep plexus-dsh/`) и в `TYPES` у `tools/dsh_digest.py`.

Писать в открытом ходе — из `agent/turn-stopping`, `agent/pre-step`, `tools/*`; из слушателя `session/event` писать нельзя (повторная публикация запрещена).

Риск: такое событие не помечено `ignorable` (у внешних плагинов нет документированного способа; `appendPluginRecord` — только для пакетов внутри репозитория DSH). DSH 0.2.2-rc.5 при чтении неизвестные типы не отвергает, но по контракту читатель вправе отказаться восстанавливать сессию без плагина. Пишите в журнал только то, что нужно в нём; объёмные данные — в свои файлы (`~/.plexus-dsh/…`), как plexus-dsh-meter.
