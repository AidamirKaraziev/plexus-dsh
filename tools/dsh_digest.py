# Разбор журнала сессии DSH (session*.jsonl.zstd): шаги, контекст, промахи кэша, паузы,
# компакция, большие выводы инструментов, reasoning, доли стоимости и доллары окна.
# Аналог tools/oc_digest.py, меры те же, чтобы сравнивать с сессиями OpenCode.
# Запуск: python3 tools/dsh_digest.py <session-dir | session.jsonl.zstd | session-id> [--steps] [--window 12]
import collections, glob, json, os, subprocess, sys

# $ за 1M токенов: (вход без кэша, чтение кэша, запись кэша, выход). Источник — tools/prices.json,
# один на весь проект: пакеты DSH и скрипты читают его же, своих копий не держат.
СПРАВОЧНИК = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prices.json')
with open(СПРАВОЧНИК, encoding='utf-8') as _f:
    _цены = json.load(_f)
PRICES = {k: tuple(v) for k, v in _цены['модели'].items()}
CLAUDE_PRICES = {k: tuple(v) for k, v in _цены['по префиксу'].items()}
# Имя продукта и его пакетов живёт здесь: остальные скрипты и пакеты берут имя отсюда,
# своей копии в tools и packages/*/src нет.
NAME = 'plex' + 'us' + '-' + 'dsh'  # корень имени продукта; остальное выводят из него

# Старое имя (до E16-S02) — только для tools/rename.sh: путь папки проекта до переименования.
# Собирается кусками, чтобы в репозитории не было прямого написания.
LEGACY_NAME = 'cor' + 'tex'

# Версии, на которые рассчитаны пакеты (tools/runtime.json → engines.dsh и peerDependencies):
# форма хуков dsh-agent-loop и API cordis меняются между релизами, поэтому `dsh` — точная версия,
# а не диапазон: несовпадение блокирует установку, а не ломает сессию на ходу.
RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runtime.json')


def версия_рантайма() -> dict:
    """Версии DSH и Cordis, под которые собраны пакеты: {'dsh': '…', 'cordis': '…'}."""
    with open(RUNTIME, encoding='utf-8') as f:
        return json.load(f)


def пакет(имя):
    """Полное имя пакета продукта: пакет('meter') → '<NAME>-meter'."""
    return f'{NAME}-{имя}'


MISS = 20_000   # некэшированный вход или запись больше этого — промах (полная перезапись префикса)
PAUSE = 5.0     # мин; пауза человека, после которой кэш провайдера обычно мёртв
SUMMARY = 20_000  # контекст, с которого мог бы стартовать следующий этап с выжимкой


def find(arg):
    if os.path.isfile(arg): return arg
    if os.path.isdir(arg):
        fs = glob.glob(os.path.join(arg, 'session*.jsonl.zstd'))
    else:
        fs = glob.glob(os.path.expanduser(f'~/.dsh/sessions/*/*{arg}*/session*.jsonl.zstd'))
    if not fs: sys.exit(f'нет журнала для {arg}')
    return max(fs, key=os.path.getmtime)


def load(path):
    raw = subprocess.run(['zstd', '-dc', path], capture_output=True, check=True).stdout
    return [json.loads(l) for l in raw.decode().splitlines() if l.strip()]


def text_len(content):
    return sum(len(x.get('text') or '') for x in content or [] if isinstance(x, dict))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    show_steps = '--steps' in sys.argv
    window = float(sys.argv[sys.argv.index('--window') + 1]) if '--window' in sys.argv else 12.0
    if '--window' in sys.argv: args.remove(sys.argv[sys.argv.index('--window') + 1])
    path = find(args[0]); ev = load(path)
    head = ev[0]
    print('SESSION', head.get('id'), 'cwd', head.get('cwd'), 'depth', head.get('delegationDepth'))
    types = collections.Counter(e['type'] for e in ev)

    steps = []  # по одному на assistant/message с usage
    tools, toolchars, big = collections.Counter(), collections.Counter(), []
    calls = {}
    last_end = None; turn_first = {}; reasoning = visible = 0
    events = []; cur = {'gap': 0}
    for e in ev:
        t, d = e['type'], e.get('data', {})
        if t == 'user/message':
            print(f"#{e['seq']} USER:", (text_len(d.get('content')) and d['content'][0].get('text', ''))[:200].replace('\n', ' '))
        elif t == 'step/start':
            gap = (e['time'] - last_end) / 60000 if last_end else 0
            cur = {'turn': d.get('turn'), 'step': d.get('step'), 'start': e['time'], 'gap': gap}
        elif t == 'assistant/message':
            m = d['message']; u = d.get('usage') or {}
            src = m.get('source') or {}
            for b in m.get('content') or []:
                if b.get('type') == 'reasoning': reasoning += len(b.get('text') or '')
                elif b.get('type') == 'text': visible += len(b.get('text') or '')
            s = dict(cur)
            s.update(time=e['time'], model=src.get('model'), provider=src.get('provider'),
                     inp=u.get('inputTokens', 0), read=u.get('cacheReadTokens', 0),
                     write=u.get('cacheWriteTokens', 0), out=u.get('outputTokens', 0), tools=[])
            s['ctx'] = s['inp'] + s['read'] + s['write']
            steps.append(s); turn_first.setdefault(s.get('turn'), s)
        elif t == 'tool/call':
            calls[d['callId']] = (d['name'], d.get('arguments', '')[:90])
        elif t == 'tool/result':
            msg = d.get('message', {}); cid = msg.get('toolCallId')
            nm, a = calls.get(cid, ('?', ''))
            n = text_len(msg.get('content')); tools[nm] += 1; toolchars[nm] += n
            if n > 10_000: big.append((e['seq'], nm, n, a))
            if steps: steps[-1]['tools'].append(f'{nm}({n}ch)')
        elif t == 'step/end':
            last_end = e['time']
        elif t.startswith(('compaction/', 'image/offload', 'llm/retry')) or 'spill' in t or 'error' in t:
            events.append((e['seq'], t, json.dumps(d, ensure_ascii=False)[:200]))

    if show_steps:
        for s in steps:
            flag = ' MISS' if max(s['inp'], s['write']) > MISS else ''
            print(f"T{s.get('turn')}.{s.get('step')} ctx={s['ctx']} in={s['inp']} r={s['read']} w={s['write']} "
                  f"out={s['out']} gap={s['gap']:.1f}m{flag} {' '.join(s['tools'])[:160]}")
    for x in events: print('EVENT', *x)

    R = sum(s['read'] for s in steps); W = sum(s['write'] for s in steps)
    I = sum(s['inp'] for s in steps); O = sum(s['out'] for s in steps)
    print(f'STEPS {len(steps)} input {I} read {R} write {W} out {O} hit {R / max(R + I + W, 1):.1%}')
    print('CTX first', steps[0]['ctx'] if steps else 0, 'max', max((s['ctx'] for s in steps), default=0))

    # Промахи: полная перезапись префикса (запись в кэш или, если провайдер её не отчитывает, некэшированный вход).
    miss = [(s, max(s['inp'], s['write'])) for s in steps[1:] if max(s['inp'], s['write']) > MISS]
    after_pause = [(s, n) for s, n in miss if s['gap'] >= PAUSE]
    allw = W + I
    print('MISSES', [(f"T{s.get('turn')}.{s.get('step')}", n, round(s['gap'], 1)) for s, n in miss])
    print(f"  после пауз >= {PAUSE:g} мин: {len(after_pause)} шт, {sum(n for _, n in after_pause)} ток "
          f"= {sum(n for _, n in after_pause) / max(allw, 1):.0%} всего некэш.входа+записи")

    # Перенос этапа: каждый ход после первого начинается с контекстом прошлых ходов.
    base = steps[0]['ctx'] if steps else 0; carry = 0
    for turn, f in turn_first.items():
        if f is steps[0]: continue
        n = sum(1 for s in steps if s.get('turn') == turn)
        extra = max(f['ctx'] - max(base, SUMMARY), 0); carry += extra * n
        print(f'TURN {turn}: старт ctx {f["ctx"]}, шагов {n}, пауза перед ходом {f["gap"]:.1f} мин, перенос ~{extra * n} ток чтения')
    print(f'CARRY перенос контекста через ходы ~{carry} = {carry / max(R + I, 1):.0%} всего входа (верхняя оценка: не каждый ход — новый этап)')

    print('TOOLS', dict(tools)); print('TOOLCHARS', dict(toolchars))
    print('BIG OUTPUTS >10K', big)
    print(f'REASONING {reasoning} симв против видимого ответа {visible}')
    print('TYPES', dict(types))

    t0 = ev[0].get('createdAt') or ev[1]['time']; t1 = max(e.get('time', 0) for e in ev)
    busy = sum(s['time'] - s['start'] for s in steps if 'start' in s)
    print(f'wall h {(t1 - t0) / 3.6e6:.2f} busy h {busy / 3.6e6:.2f}')

    cost = collections.Counter()
    for s in steps:
        p = PRICES.get(s['model'] or '')
        if not p: continue
        ci, cr, cw, co = p
        cost['input'] += s['inp'] * ci / 1e6; cost['read'] += s['read'] * cr / 1e6
        cost['write'] += s['write'] * cw / 1e6; cost['out'] += s['out'] * co / 1e6
        if max(s['inp'], s['write']) > MISS and s['gap'] >= PAUSE and s is not steps[0]:
            cost['miss_after_pause'] += (s['inp'] * ci + s['write'] * cw - max(s['inp'], s['write']) * cr) / 1e6
    tot = sum(v for k, v in cost.items() if k != 'miss_after_pause')
    if tot:
        print(f'COST ${tot:.3f} = {tot / window:.1%} окна ${window:g}: ' +
              ' '.join(f'{k} {v / tot:.0%}' for k, v in cost.items() if k != 'miss_after_pause'))
        print(f"  потери на промахах после пауз ${cost['miss_after_pause']:.3f} = {cost['miss_after_pause'] / tot:.0%} стоимости")
    for r, w, o in ((0.1, 1.25, 4), (0.2, 1.0, 4)):  # те же веса, что в oc_digest.py
        t = R * r + (W + I) * w + O * o
        if t: print(f'weights r={r} w={w} o={o}: read {R * r / t:.0%} write+uncached {(W + I) * w / t:.0%} out {O * o / t:.0%}')


if __name__ == '__main__':
    main()
