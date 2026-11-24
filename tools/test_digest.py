#!/usr/bin/env python3
"""Проверки tools/digest.py: python3 tools/test_digest.py.

Один и тот же шаблон шагов (первый шаг-запись, промах без паузы, промах после
паузы >= 5 мин) записывается во всех трёх форматах — меры должны совпасть.
Плюс: дедуп повторных строк ответа Claude, цена 1ч-кэша, автоопределение
источника, стабильные ключи --json. Если на машине есть реальные данные —
сверка DSH dabb3295 с заметкой E34 и экспорта OpenCode с его info.tokens.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import digest  # noqa: E402

T0 = 1_000_000  # мс, эпоха условной сессии
FAILS = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else f'  [{detail}]'))
    if not ok:
        FAILS.append(name)


def near(got, want, rel=0.01):
    return abs(got - want) <= max(abs(want) * rel, 1e-9)


def run_cli(args):
    out = subprocess.run([sys.executable, os.path.join(HERE, 'digest.py')] + args,
                         capture_output=True, text=True)
    return out


# ---------- фикстуры: шаг 1 — старт (30000), шаг 2 — промах без паузы (40000),
# шаг 3 — промах после паузы 6 мин (50000 + чтение 1000) ----------

COMMON = dict(steps=3, input=120000, cache_read=1000, cache_write=0, output=150,
              hit_pct=0.8, ctx_first=30000, ctx_max=51000)
MISSES = dict(total=2, after_pause=1, after_pause_tokens=50000,
              no_pause=1, no_pause_tokens=40000)


def assistant_usage(inp, read=0, out=50):
    return dict(inputTokens=inp, cacheReadTokens=read, cacheWriteTokens=0, outputTokens=out)


def dsh_msg(model, inp, read=0, out=50):
    return {'message': {'role': 'assistant', 'source': {'model': model, 'provider': 'opencode-go'},
                        'content': [{'type': 'text', 'text': 'ok'}]},
            'usage': assistant_usage(inp, read, out)}


def fixture_dsh(d):
    ev = [
        {'type': 'session', 'version': 4, 'id': 'session-fixt-dsh', 'createdAt': T0,
         'cwd': '/tmp/fixt', 'delegationDepth': 0},
        {'type': 'step/start', 'seq': 1, 'time': T0, 'data': {'turn': 1, 'step': 1}},
        {'type': 'assistant/message', 'seq': 2, 'time': T0 + 1000, 'data': dsh_msg('glm-5.3', 30000)},
        {'type': 'tool/call', 'seq': 3, 'time': T0 + 1500, 'data': {'callId': 'c1', 'name': 'read'}},
        {'type': 'tool/result', 'seq': 3, 'time': T0 + 1600, 'data': {'message': {
            'role': 'tool', 'toolCallId': 'c1', 'content': [{'type': 'text', 'text': 'x' * 4000}]}}},
        {'type': 'tool/call', 'seq': 3, 'time': T0 + 1700, 'data': {'callId': 'c2', 'name': 'read'}},
        {'type': 'tool/result', 'seq': 3, 'time': T0 + 1800, 'data': {'message': {
            'role': 'tool', 'toolCallId': 'c2', 'content': [{'type': 'text', 'text': 'y' * 401}]}}},
        {'type': 'step/end', 'seq': 3, 'time': T0 + 2000, 'data': {}},
        {'type': 'step/start', 'seq': 4, 'time': T0 + 2000 + 120000, 'data': {'turn': 1, 'step': 2}},
        {'type': 'assistant/message', 'seq': 5, 'time': T0 + 122000, 'data': dsh_msg('glm-5.3', 40000)},
        {'type': 'step/end', 'seq': 6, 'time': T0 + 123000, 'data': {}},
        {'type': 'step/start', 'seq': 7, 'time': T0 + 123000 + 360000, 'data': {'turn': 2, 'step': 1}},
        {'type': 'assistant/message', 'seq': 8, 'time': T0 + 484000, 'data': dsh_msg('glm-5.3', 50000, 1000)},
        {'type': 'step/end', 'seq': 9, 'time': T0 + 485000, 'data': {}},
    ]
    raw = '\n'.join(json.dumps(e, ensure_ascii=False) for e in ev).encode()
    path = os.path.join(d, 'session-fixt-dsh-0000-0000-000000000000', 'session.v4.jsonl.zstd')
    os.makedirs(os.path.dirname(path))
    subprocess.run(['zstd', '-q', '-f', '-o', path], input=raw, check=True)
    return path


def fixture_opencode(d):
    doc = {'info': {'id': 'sess-oc-fixt', 'title': 'фикстура',
                    'location': {'directory': '/tmp/fixt'}, 'model': {'id': 'glm-5.3'}},
           'messages': [
               {'id': 'u1', 'type': 'user', 'time': {'created': T0}, 'text': 'hi'},
               {'id': 'a1', 'type': 'assistant', 'agent': 'build',
                'model': {'id': 'glm-5.3', 'providerID': 'opencode-go'},
                'time': {'created': T0, 'completed': T0 + 1000},
                'tokens': {'input': 30000, 'output': 50, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}},
                'content': []},
               {'id': 'a2', 'type': 'assistant', 'agent': 'build',
                'model': {'id': 'glm-5.3', 'providerID': 'opencode-go'},
                'time': {'created': T0 + 1000 + 120000, 'completed': T0 + 122000},
                'tokens': {'input': 40000, 'output': 50, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}},
                'content': []},
               {'id': 'a3', 'type': 'assistant', 'agent': 'build',
                'model': {'id': 'glm-5.3', 'providerID': 'opencode-go'},
                'time': {'created': T0 + 122000 + 360000, 'completed': T0 + 484000},
                'tokens': {'input': 50000, 'output': 50, 'reasoning': 0, 'cache': {'read': 1000, 'write': 0}},
                'content': []}]}
    path = os.path.join(d, 'фикстура-opencode.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False)
    return path


def cc_line(ts, role, msg_id=None, model='claude-opus-5-5', inp=0, read=0, write=0, out=0):
    d = {'type': role, 'timestamp': ts, 'sessionId': 'fixt-cc', 'cwd': '/tmp/fixt',
         'message': {'role': role}}
    if role == 'user':
        d['message']['content'] = 'hi'
    else:
        d['message'].update(model=model, id=msg_id, usage={
            'input_tokens': inp, 'cache_read_input_tokens': read,
            'cache_creation_input_tokens': write, 'output_tokens': out})
    return d


def fixture_claude(d):
    lines = [
        cc_line('2026-01-01T00:00:00.000Z', 'user'),
        cc_line('2026-01-01T00:00:02.000Z', 'assistant', 'm1', inp=30000, out=50),
        cc_line('2026-01-01T00:00:03.000Z', 'assistant', 'm1', inp=30000, out=50),  # дубль ответа
        cc_line('2026-01-01T00:02:04.000Z', 'user'),          # пауза до шага 2 мин
        cc_line('2026-01-01T00:02:06.000Z', 'assistant', 'm2', inp=40000, out=50),
        cc_line('2026-01-01T00:08:07.000Z', 'user'),          # пауза до шага 6 мин
        cc_line('2026-01-01T00:08:09.000Z', 'assistant', 'm3', inp=50000, read=1000, out=50),
    ]
    path = os.path.join(d, 'fixt-cc-0000-0000-000000000000.jsonl')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(json.dumps(l, ensure_ascii=False) for l in lines) + '\n')
    return path


KEYS = {'session', 'source', 'cwd', 'title', 'model', 'models', 'steps', 'sidechain_steps',
        'input', 'cache_read', 'cache_write', 'output', 'hit_pct', 'ctx_first', 'ctx_max',
        'started', 'ended', 'wall_min', 'busy_min', 'misses', 'cost_usd', 'unpriced_models', 'tools'}
MISS_KEYS = {'total', 'after_pause', 'after_pause_tokens', 'after_pause_usd',
             'no_pause', 'no_pause_tokens', 'no_pause_usd'}
COST_KEYS = {'total', 'input', 'read', 'write', 'output'}


def common_checks(tag, path, d, cost_want, miss_usd):
    for k, want in COMMON.items():
        check(f'{tag}: {k} == {want}', d[k] == want, f'получили {d[k]}')
    for k, want in MISSES.items():
        check(f'{tag}: промахи {k} == {want}', d['misses'][k] == want, f'получили {d["misses"][k]}')
    check(f'{tag}: промахи после пауз $ ≈ {miss_usd[0]}', near(d['misses']['after_pause_usd'], miss_usd[0]),
          f'получили {d["misses"]["after_pause_usd"]}')
    check(f'{tag}: промахи без паузы $ ≈ {miss_usd[1]}', near(d['misses']['no_pause_usd'], miss_usd[1]),
          f'получили {d["misses"]["no_pause_usd"]}')
    check(f'{tag}: COST $ ≈ {cost_want}', near(d['cost_usd']['total'], cost_want),
          f'получили {d["cost_usd"]}')
    check(f'{tag}: wall/busy > 0', d['wall_min'] > 0 and d['busy_min'] > 0,
          f'wall={d["wall_min"]} busy={d["busy_min"]}')


def main():
    zstd = shutil.which('zstd')
    tmp = tempfile.mkdtemp(prefix='digest-test-')
    try:
        paths = {}
        if zstd:
            paths['dsh'] = fixture_dsh(tmp)
        else:
            print('skip dsh: нет zstd')
        paths['opencode'] = fixture_opencode(tmp)
        paths['claude'] = fixture_claude(tmp)

        # одинаковые меры на трёх источниках, автоопределение и стабильный --json
        costs = {'dsh': 0.1689, 'opencode': 0.1689, 'claude': 0.608}   # glm-5.3 против opus
        miss_usd = {'dsh': (0.057, 0.0456), 'opencode': (0.057, 0.0456), 'claude': (0.225, 0.18)}
        for src, path in paths.items():
            out = run_cli([path, '--json'])
            check(f'{src}: --json запускается', out.returncode == 0, out.stderr[-300:])
            d = json.loads(out.stdout)
            check(f'{src}: источник определён сам', d['source'] == src, f'получили {d["source"]}')
            check(f'{src}: ключи --json стабильны',
                  set(d) == KEYS and set(d['misses']) == MISS_KEYS and set(d['cost_usd']) == COST_KEYS,
                  f'{sorted(set(d) ^ KEYS)}')
            common_checks(src, path, d, costs[src], miss_usd[src])
            if src == 'dsh':
                want = {'read': {'count': 2, 'max_tokens': 1004, 'tokens': 1004 + 105}}
                check('dsh: tools — число и max токенов по инструменту', d['tools'] == want, f'получили {d["tools"]}')
            again = run_cli([path, '--json'])
            check(f'{src}: --json воспроизводим', again.stdout == out.stdout)

        if 'dsh' in paths:
            check('find: файл dsh по формату', digest.find(paths['dsh']) == ('dsh', paths['dsh']))
        out = run_cli([paths['claude'], '--source', 'opencode'])
        check('find: --source против формата — отказ', out.returncode != 0 and 'формат' in out.stderr,
              out.stderr[-200:])
        try:
            digest.find('нет-такой-сессии-zzzq-12345')
            check('find: ничего не нашлось — отказ', False, 'не завершился')
        except SystemExit:
            check('find: ничего не нашлось — отказ', True)

        check('price: glm-5.3 из dsh_digest.PRICES', digest.price('glm-5.3') == digest.PRICES['glm-5.3'])
        check('price: claude-opus-5-5', digest.price('claude-opus-5-5') == (5, 0.5, 6.25, 50))
        check('price: claude-sonnet-4-5-20250929', digest.price('claude-sonnet-4-5-20250929') == (3, 0.3, 3.75, 15))
        check('price: claude-haiku-4-5-20251001', digest.price('claude-haiku-4-5-20251001') == (1, 0.1, 1.25, 5))
        check('price: неизвестной модели нет', digest.price('claude-fable-5-2') is None)

        # 1ч-кэш Claude: запись вдвое дороже 5м
        p = os.path.join(tmp, 'cc-1h.jsonl')
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(cc_line('2026-01-01T00:00:00.000Z', 'user')) + '\n')
            l = cc_line('2026-01-01T00:00:02.000Z', 'assistant', 'm1', inp=2, write=1_000_000, out=10)
            l['message']['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': 0,
                                                       'ephemeral_1h_input_tokens': 1_000_000}
            fh.write(json.dumps(l) + '\n')
        d = json.loads(run_cli([p, '--json']).stdout)
        check('claude: 1ч-запись $12.50 за 1M', near(d['cost_usd']['write'], 12.5),
              f'получили {d["cost_usd"]["write"]}')

        # ---- реальные данные, если есть на машине ----
        real_dsh = glob.glob(os.path.expanduser('~/.dsh/sessions/*/*dabb3295*/session*.jsonl.zstd'))
        if real_dsh:
            d = json.loads(run_cli([max(real_dsh, key=os.path.getmtime), '--json']).stdout)
            note = {'steps': 84, 'input': 251_000, 'cache_read': 6_750_000, 'output': 82_000,
                    'ctx_max': 151_000, 'hit_pct': 96.4}          # заметка E34
            for k, want in note.items():
                got = d[k]
                check(f'dabb3295: {k} ≈ {want} (±1 %)', near(got, want) if k != 'hit_pct' else abs(got - want) < 0.2,
                      f'получили {got}')
            check('dabb3295: COST ≈ $2.47 (±1 %)', near(d['cost_usd']['total'], 2.47),
                  f'получили {d["cost_usd"]["total"]}')
            check('dabb3295: промах после паузы 1, без паузы 0',
                  d['misses']['after_pause'] == 1 and d['misses']['no_pause'] == 0, str(d['misses']))
            check('dabb3295: busy ≈ 0.35 ч', near(d['busy_min'], 21, rel=0.03), f'получили {d["busy_min"]} мин')
        else:
            print('skip dabb3295: нет журнала')

        oc = os.path.expanduser('~/Downloads/e34-s03-start-command.json')
        if os.path.isfile(oc):
            info = json.load(open(oc, encoding='utf-8'))['info']
            t = info['tokens']
            d = json.loads(run_cli([oc, '--json']).stdout)
            check('opencode e34-s03: input совпадает с info.tokens',
                  near(d['input'], t['input']), f'{d["input"]} против {t["input"]}')
            check('opencode e34-s03: cache_read совпадает с info.tokens',
                  near(d['cache_read'], t['cache']['read']), f'{d["cache_read"]} против {t["cache"]["read"]}')
            check('opencode e34-s03: output+reasoning совпадает с info.tokens',
                  near(d['output'], t['output'] + t['reasoning']),
                  f'{d["output"]} против {t["output"] + t["reasoning"]}')
            hand = (t['input'] * 1.4 + t['cache']['read'] * 0.26 + (t['output'] + t['reasoning']) * 4.4) / 1e6
            check('opencode e34-s03: COST совпадает с ручным расчётом (±1 %)',
                  near(d['cost_usd']['total'], hand), f'{d["cost_usd"]["total"]} против {hand:.4f}')
        else:
            print('skip opencode: нет экспорта')

        cc = [f for f in glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl'))
              if os.path.getsize(f) > 100_000]
        if cc:
            d = json.loads(run_cli([max(cc, key=os.path.getmtime), '--json']).stdout)
            check('claude: реальная сессия разбирается',
                  d['source'] == 'claude' and d['steps'] >= 5 and d['cache_read'] > 0 and d['ctx_max'] > 0,
                  f'steps={d["steps"]} read={d["cache_read"]} ctx={d["ctx_max"]}')
        else:
            print('skip claude: нет транскриптов')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'\n{"FAIL: " + str(len(FAILS)) if FAILS else "все проверки зелёные"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
