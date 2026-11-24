#!/usr/bin/env python3
"""Сводный разбор сессии для трёх источников: DSH, OpenCode, Claude Code.

Источник определяется сам: путь к файлу или папке — по формату, id или подстрока —
поиском по ~/.dsh/sessions (DSH), ~/.claude/projects (Claude Code), ~/Downloads
(экспорты OpenCode); --source задаёт источник явно. Меры общие, те же, что в
dsh_digest.py / oc_digest.py, чтобы сессии разных harness'ов сравнивать напрямую:

  шаги — запросы к модели; вход без кэша / чтение кэша / запись кэша / выход;
  % попадания в кэш = чтение / весь вход; контекст первый и максимальный;
  длительность по часам (wall) и в работе модели (busy);
  промахи — полная перезапись префикса (запись или некэшированный вход > MISS,
  первый шаг не в счёт), отдельно после пауз >= PAUSE мин и без паузы;
  $ по таблице цен из tools/prices.json (один справочник на проект);
  tools (только DSH) — по инструменту число результатов и max/сумма их токенов
  (оценка DSH: символы / 4 + 4 на блок, как у пакета read-cap).

Запуск:
  python3 tools/digest.py <сессия> [--source dsh|opencode|claude] [--json]

--json печатает стабильный словарь (ключи не меняются) — его читает tools/bench.py.
"""
import collections
import glob
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsh_digest import PRICES, CLAUDE_PRICES as _claude_prices, MISS, PAUSE, load  # noqa: E402  пороги мер, цены и чтение .zstd

# Цены — из tools/prices.json (один справочник на проект). Claude подбирается по префиксу id
# (claude-opus-5-5, claude-sonnet-4-5-20250929, …); запись 1ч-кэша вдвое дороже.
CLAUDE_PRICES = _claude_prices

HOME = os.path.expanduser('~')
SOURCES = ('dsh', 'opencode', 'claude')


def price(model):
    """(вход, чтение, запись, выход) за 1M по id модели или None, если цены нет."""
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    for k in sorted(CLAUDE_PRICES, key=len, reverse=True):
        if model.startswith(k):
            return CLAUDE_PRICES[k]
    return None


def _step(model, provider, inp, read, write, out, gap, busy, write1h=0, side=False):
    return dict(model=model, provider=provider, inp=inp or 0, read=read or 0,
                write=write or 0, write1h=write1h or 0, out=out or 0,
                ctx=(inp or 0) + (read or 0) + (write or 0), gap=gap or 0.0,
                busy=busy or 0.0, side=side)


def tokens_est(content):
    """Токены блоков результата по оценке DSH (dsh-token-meter/estimate): символы / 4 + 4 на блок."""
    n = 0
    for b in content or []:
        txt = b.get('text') if isinstance(b, dict) and b.get('type') == 'text' else json.dumps(b, ensure_ascii=False)
        n += -(-len(txt or '') // 4) + 4
    return n


def steps_dsh(path):
    """Журнал DSH session*.jsonl.zstd: шаг = assistant/message с usage."""
    ev = load(path)
    head = ev[0]
    steps, last_end, cur = [], None, {}
    calls, tools = {}, {}
    for e in ev:
        t, d = e['type'], e.get('data', {})
        if t == 'tool/call':
            calls[d.get('callId')] = d.get('name') or '?'
        elif t == 'tool/result':
            m = d.get('message') or {}
            name = calls.get(m.get('toolCallId'), '?')
            k = tokens_est(m.get('content'))
            s = tools.setdefault(name, {'count': 0, 'max_tokens': 0, 'tokens': 0})
            s['count'] += 1
            s['tokens'] += k
            s['max_tokens'] = max(s['max_tokens'], k)
        elif t == 'step/start':
            gap = (e['time'] - last_end) / 60000 if last_end else 0.0
            cur = {'start': e['time'], 'gap': gap}
        elif t == 'assistant/message':
            m, u = d['message'], d.get('usage') or {}
            src = m.get('source') or {}
            steps.append(_step(src.get('model'), src.get('provider'),
                               u.get('inputTokens', 0), u.get('cacheReadTokens', 0),
                               u.get('cacheWriteTokens', 0), u.get('outputTokens', 0),
                               cur.get('gap', 0.0),
                               (e['time'] - cur['start']) / 60000 if cur.get('start') else 0.0))
        elif t == 'step/end':
            last_end = e['time']
    title = next((e.get('data', {}).get('title') for e in ev
                  if e['type'] == 'session/title' and e.get('data', {}).get('title')), None)
    meta = dict(id=head.get('id'), cwd=head.get('cwd'), title=title,
                started=head.get('createdAt'), ended=max(e.get('time', 0) for e in ev),
                tools=dict(sorted(tools.items())))
    return meta, steps


def steps_opencode(path):
    """Экспорт сессии OpenCode: шаг = assistant с tokens (выход включает reasoning)."""
    d = json.load(open(path, encoding='utf-8'))
    info, ms = d['info'], d['messages']
    steps, last_end = [], None
    for m in ms:
        tk = m.get('tokens') or {}
        if m.get('type') != 'assistant' or not tk:
            continue
        c = tk.get('cache') or {}
        created = m['time']['created']
        done = m['time'].get('completed') or created
        gap = (created - last_end) / 60000 if last_end else 0.0
        last_end = done
        model = m.get('model') or {}
        steps.append(_step(model.get('id'), model.get('providerID'),
                           tk.get('input', 0), c.get('read', 0), c.get('write', 0),
                           (tk.get('output', 0) or 0) + (tk.get('reasoning', 0) or 0),
                           gap, (done - created) / 60000))
    times = [m['time'].get('completed') or m['time']['created'] for m in ms]
    meta = dict(id=info.get('id'), cwd=(info.get('location') or {}).get('directory'),
                title=info.get('title'), started=ms[0]['time']['created'] if ms else None,
                ended=max(times) if times else None)
    return meta, steps


def iso_ms(s):
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() * 1000
    except (AttributeError, ValueError):
        return None


def steps_claude(path):
    """Транскрипт Claude Code ~/.claude/projects/<проект>/<id>.jsonl.

    Один ответ API пишется несколькими строками с одним message.id — шаг это
    первый экземпляр id. Пауза до шага и работа = от прошлого ответа до
    предыдущей user-строки (≈ старт запроса) и от неё до ответа; это оценка,
    точных времен запроса в транскрипте нет.
    """
    steps, meta = [], {'id': None, 'cwd': None, 'title': None}
    started = ended = last_user = last_end = None
    seen = set()
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = iso_ms(d.get('timestamp'))
            if ts is None:
                continue
            started = ts if started is None else started
            ended = ts
            if meta['id'] is None and d.get('sessionId'):
                meta['id'] = d['sessionId']
            if meta['cwd'] is None and d.get('cwd'):
                meta['cwd'] = d['cwd']
            m = d.get('message') or {}
            role = m.get('role')
            if role == 'user':
                last_user = ts
            elif role == 'assistant':
                u = m.get('usage') or {}
                if not u or m.get('model') == '<synthetic>':
                    continue
                key = m.get('id') or d.get('requestId') or d.get('uuid')
                if key in seen:
                    continue
                seen.add(key)
                cc = u.get('cache_creation') or {}
                gap = (last_user - last_end) / 60000 if (last_user is not None and last_end is not None) else 0.0
                busy = max(0.0, (ts - last_user) / 60000) if last_user is not None else 0.0
                steps.append(_step(m.get('model'), None,
                                   u.get('input_tokens', 0), u.get('cache_read_input_tokens', 0),
                                   u.get('cache_creation_input_tokens', 0), u.get('output_tokens', 0),
                                   gap, busy, cc.get('ephemeral_1h_input_tokens', 0),
                                   bool(d.get('isSidechain'))))
                last_end = ts
    meta.update(started=started, ended=ended)
    return meta, steps


LOADERS = {'dsh': steps_dsh, 'opencode': steps_opencode, 'claude': steps_claude}


def sniff(path):
    """Источник по формату файла."""
    if path.endswith('.zstd'):
        return 'dsh'
    if path.endswith('.jsonl'):
        return 'claude'
    if path.endswith('.json'):
        with open(path, encoding='utf-8', errors='ignore') as fh:
            head = fh.read(2048)
        return 'opencode' if '"messages"' in head and '"info"' in head else None
    return None


def find(arg, source=None):
    """(источник, путь): файл/папка — по формату, иначе поиск id/подстроки по трём хранилищам."""
    arg = os.path.expanduser(arg)
    if os.path.isdir(arg):
        fs = glob.glob(os.path.join(arg, 'session*.jsonl.zstd'))
        if not fs:
            sys.exit(f'в {arg} нет журнала DSH (session*.jsonl.zstd)')
        return 'dsh', max(fs, key=os.path.getmtime)
    if os.path.isfile(arg):
        s = sniff(arg)
        if source and s and s != source:
            sys.exit(f'{arg} — формат {s}, не {source}')
        s = source or s
        if not s:
            sys.exit(f'не узнал формат {arg} — задай --source dsh|opencode|claude')
        return s, arg
    hits = {}
    if source in (None, 'dsh'):
        fs = glob.glob(f'{HOME}/.dsh/sessions/*/*{arg}*/session*.jsonl.zstd')
        if fs:
            hits['dsh'] = max(fs, key=os.path.getmtime)
    if source in (None, 'claude'):
        fs = glob.glob(f'{HOME}/.claude/projects/*/*{arg}*.jsonl')
        if fs:
            hits['claude'] = max(fs, key=os.path.getmtime)
    if source in (None, 'opencode'):
        fs = [f for f in glob.glob(f'{HOME}/Downloads/*{arg}*.json') if sniff(f) == 'opencode']
        if fs:
            hits['opencode'] = max(fs, key=os.path.getmtime)
    if len(hits) > 1:
        sys.exit('сессия нашлась в нескольких источниках: ' +
                 '; '.join(f'{k}={v}' for k, v in hits.items()) + ' — задай --source')
    if not hits:
        sys.exit(f'нет сессии для {arg!r}' + (f' в источнике {source}' if source else ''))
    return next(iter(hits.items()))


def summarize(source, meta, steps):
    """Стабильный словарь мер — то, что печатает --json и читает tools/bench.py."""
    I = sum(s['inp'] for s in steps); R = sum(s['read'] for s in steps)
    W = sum(s['write'] for s in steps); O = sum(s['out'] for s in steps)
    models = collections.Counter(s['model'] or '?' for s in steps)
    cost, unpriced = collections.Counter(), set()
    for s in steps:
        p = price(s['model'])
        if not p:
            unpriced.add(s['model'])
            continue
        ci, cr, cw, co = p
        cost['input'] += s['inp'] * ci / 1e6
        cost['read'] += s['read'] * cr / 1e6
        cost['write'] += (s['write'] + s['write1h']) * cw / 1e6  # 1ч-кэш дороже вдвое
        cost['output'] += s['out'] * co / 1e6
    # промах: полная перезапись префикса, первый шаг не в счёт
    miss = [s for s in steps[1:] if max(s['inp'], s['write']) > MISS]
    after = [s for s in miss if s['gap'] >= PAUSE]
    nopause = [s for s in miss if s['gap'] < PAUSE]
    mcost = {'after_pause': 0.0, 'no_pause': 0.0}
    for s in after:
        mcost['after_pause'] += _miss_cost(s)
    for s in nopause:
        mcost['no_pause'] += _miss_cost(s)
    started, ended = meta.get('started'), meta.get('ended')
    return {
        'session': meta.get('id'), 'source': source, 'cwd': meta.get('cwd'), 'title': meta.get('title'),
        'model': models.most_common(1)[0][0] if models else None, 'models': dict(models),
        'steps': len(steps), 'sidechain_steps': sum(1 for s in steps if s['side']),
        'input': I, 'cache_read': R, 'cache_write': W, 'output': O,
        'hit_pct': round(100.0 * R / max(R + I + W, 1), 1),
        'ctx_first': steps[0]['ctx'] if steps else 0,
        'ctx_max': max((s['ctx'] for s in steps), default=0),
        'started': started, 'ended': ended,
        'wall_min': round((ended - started) / 60000, 1) if started and ended else 0.0,
        'busy_min': round(sum(s['busy'] for s in steps), 2),
        'misses': {
            'total': len(miss),
            'after_pause': len(after),
            'after_pause_tokens': sum(max(s['inp'], s['write']) for s in after),
            'after_pause_usd': round(mcost['after_pause'], 4),
            'no_pause': len(nopause),
            'no_pause_tokens': sum(max(s['inp'], s['write']) for s in nopause),
            'no_pause_usd': round(mcost['no_pause'], 4),
        },
        'cost_usd': {
            'total': round(sum(cost.values()), 4),
            'input': round(cost['input'], 4), 'read': round(cost['read'], 4),
            'write': round(cost['write'], 4), 'output': round(cost['output'], 4),
        },
        'unpriced_models': sorted(m for m in unpriced if m),
        'tools': meta.get('tools') or {},
    }


def _miss_cost(s):
    """Потеря на промахе: что ушло по ценам входа/записи против полного чтения кэша."""
    p = price(s['model'])
    if not p:
        return 0.0
    ci, cr, cw, _ = p
    return (s['inp'] * ci + (s['write'] + s['write1h']) * cw - max(s['inp'], s['write']) * cr) / 1e6


def tok(n):
    if n >= 1e6:
        return f'{n / 1e6:.2f}M'
    if n >= 1e3:
        return f'{n / 1e3:.1f}K' if n % 1000 else f'{n // 1000}K'
    return str(n)


def render(d):
    """Человекочитаемые строки по стабильному словарю."""
    m, c = d['misses'], d['cost_usd']
    out = [f"SESSION {d['session']} source={d['source']} model={d['model']} cwd={d['cwd']}"
           + (f" title={d['title']}" if d['title'] else ''),
           f"STEPS {d['steps']} input {tok(d['input'])} read {tok(d['cache_read'])} "
           f"write {tok(d['cache_write'])} out {tok(d['output'])} hit {d['hit_pct']}%",
           f"CTX first {tok(d['ctx_first'])} max {tok(d['ctx_max'])}",
           f"WALL {d['wall_min'] / 60:.2f}h BUSY {d['busy_min'] / 60:.2f}h",
           f"MISSES {m['total']}: после пауз >= {PAUSE:g} мин — {m['after_pause']} шт "
           f"({tok(m['after_pause_tokens'])}, ${m['after_pause_usd']}); "
           f"без паузы — {m['no_pause']} шт ({tok(m['no_pause_tokens'])}, ${m['no_pause_usd']})",
           f"COST ${c['total']} = input ${c['input']} + read ${c['read']} "
           f"+ write ${c['write']} + out ${c['output']}"]
    if d['tools']:
        out.append('TOOLS ' + ', '.join(f"{k} {v['count']}× max {tok(v['max_tokens'])} всего {tok(v['tokens'])}"
                                        for k, v in d['tools'].items()))
    if d['unpriced_models']:
        out.append('  нет цен для: ' + ', '.join(d['unpriced_models']))
    return out


def main():
    argv = sys.argv[1:]
    as_json = '--json' in argv
    if as_json:
        argv.remove('--json')
    source = None
    if '--source' in argv:
        i = argv.index('--source')
        if i + 1 >= len(argv) or argv[i + 1] not in SOURCES:
            sys.exit(f'--source требует одно из: {", ".join(SOURCES)}')
        source = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 1:
        sys.exit('запуск: python3 tools/digest.py <сессия> [--source dsh|opencode|claude] [--json]')
    src, path = find(argv[0], source)
    meta, steps = LOADERS[src](path)
    d = summarize(src, meta, steps)
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print('\n'.join(render(d)))


if __name__ == '__main__':
    main()
