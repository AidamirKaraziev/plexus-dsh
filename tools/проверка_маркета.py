#!/usr/bin/env python3
"""Установка plexus-dsh из индекса маркета в чистый профиль и один прогон эталона t2.

    python3 tools/проверка_маркета.py [--индекс <файл|URL>]

1. Профиль `bench-market` создаётся заново: провайдер и модель как у bench-base, без пакетов продукта,
   включён экспериментальный бандл маркета (`dsh-experimental-plugin-market-bundle`), в настройках
   маркета записан адрес индекса.
2. Индекс читается по адресу витрины (или `--индекс <файл|URL>`: так проверяют локальный market.json до
   выпуска витрины), проверяется формат; для каждого плагина берётся последняя версия. Ожидаемые пакеты —
   плагины индекса; @plexus-dsh/plexus, которого нет в индексе или в npm, пропускается без падения.
3. Пакеты ставятся теми же аргументами, что кнопка Install (`marketInstallTarget` из
   `@deepseek-ai/dsh-plugin-market-index`): `dsh plugin --profile bench-market add <spec>`.
4. `dsh plugin --profile bench-market list` обязан показать все ожидаемые пакеты.
4а. Если @plexus-dsh/plexus стоит: дымовая проверка `/plexus-help`. В headless нет разбора слэш-команд
   (он в клиентском UI), строка уходит модели профиля как задание, а скилл `plexus-help` подгружает
   инструмент `skill`. Один дешёвый вызов `dsh --profile bench-market --json "/plexus-help"`: ответ
   непустой и без «unknown command» и подобного; вызов инструмента skill печатается справкой.
5. `python3 tools/bench.py run --tasks t2 --profile market` — одна платная прогонка (≈ $0,5–0,8),
   успех задачи — условие кода 0.

Код 0 — всё сошлось, 1 — расхождение (строка `✗` объясняет). `DSH_HOME` (по умолчанию ~/.dsh) и `DSH`
(путь к рантайму) можно переопределить.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПРОФИЛЬ = 'bench-market'
ИСТОЧНИК_ПРОФИЛЯ = 'bench-base'
АДРЕС_ИНДЕКСА = 'https://raw.githubusercontent.com/AidamirKaraziev/plexus-dsh/main/market.json'
БАНДЛ_МАРКЕТА = 'dsh-experimental-plugin-market-bundle'
ПЛЕКСУС = '@plexus-dsh/plexus'
КОМАНДА_СПРАВКИ = '/plexus-help'
ТАЙМАУТ_СПРАВКИ = 300
# ответ-отказ вместо справки: слэш-команду не распознали
ОТКАЗ = re.compile(r'unknown (slash )?command|unknown skill|command not found|no such (skill|command)|'
                   r'unrecognized command|неизвестн\w+ (команд|скилл)', re.IGNORECASE)
МАРКЕР_ПРОДУКТА = '# >>> plexus-dsh packages'
ТАЙМАУТ_УСТАНОВКИ = 900


def найти_dsh() -> str:
    for путь in (os.environ.get('DSH'),
                 '/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh',
                 shutil.which('dsh')):
        if путь and os.path.exists(путь):
            return путь
    raise SystemExit('✗ рантайм dsh не найден (задайте DSH=<путь>)')


def версия_рантайма() -> str:
    with open(os.path.join(КОРЕНЬ, 'tools', 'runtime.json'), encoding='utf-8') as f:
        return json.load(f)['dsh']


def дом_профилей() -> str:
    return os.path.join(os.environ.get('DSH_HOME') or os.path.expanduser('~/.dsh'), 'profiles')


def _ключ_версии(версия: str) -> tuple:
    основа = версия.split('-')[0].split('+')[0]
    return tuple(int(x) for x in основа.split('.')), '-' not in версия


def последняя(плагин: dict) -> dict:
    """Версия плагина с наибольшим номером (релиз старше пререлиза того же номера)."""
    return max(плагин['versions'], key=lambda в: _ключ_версии(в['version']))


def цель_установки(имя: str, версия: dict) -> dict:
    """Порт `marketInstallTarget`: spec и необязательный registry."""
    ист = версия['source']
    if ист['type'] == 'npm':
        r = {'spec': f"{имя}@{версия['version']}"}
        if ист.get('registry'):
            r['registry'] = ист['registry']
        return r
    url = ист['url'] if ист['url'].endswith('.git') else ист['url'] + '.git'
    путь = f"&path:/{ист['path']}" if ист.get('path') else ''
    return {'spec': f"git+{url}#{ист['commit']}{путь}"}


def цели_индекса(индекс: dict) -> list:
    return [цель_установки(п['name'], последняя(п)) for п in индекс['plugins']]


def ожидаемые(индекс: dict, недоступные: tuple = ()) -> list:
    """Имена плагинов индекса, которые обязаны стоять в профиле; недоступные в npm пропускаются."""
    return [п['name'] for п in индекс['plugins'] if п['name'] not in недоступные]


def в_npm(имя: str) -> bool:
    """npm отдаёт пакет (`npm view`); 404 — нет, любая иная ошибка тоже «нет»: проверка идёт без него."""
    try:
        p = subprocess.run(['npm', 'view', имя, 'version'], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and bool(p.stdout.strip())


def команда_добавления(dsh: str, профиль: str, цель: dict) -> list:
    extra = [f"--registry={цель['registry']}"] if цель.get('registry') else []
    return [dsh, 'plugin', '--profile', профиль, 'add', *extra, цель['spec']]


def команда_списка(dsh: str, профиль: str) -> list:
    return [dsh, 'plugin', '--profile', профиль, 'list']


def патч_профиля(исходный: str, адрес: str) -> str:
    """cordis.patch.yml нового профиля: провайдер и модель из исходного, без блока пакетов продукта,
    плюс адрес индекса в настройках маркета."""
    i = исходный.find(МАРКЕР_ПРОДУКТА)
    база = (исходный if i < 0 else исходный[:i]).rstrip('\n')
    return (f"{база}\n\n# bench-market (tools/проверка_маркета.py): адрес индекса в настройках маркета\n"
            f"- id: plugin-market\n  config:\n    indexes:\n      - {json.dumps(адрес)}\n")


def создать_профиль(dsh: str, адрес: str = АДРЕС_ИНДЕКСА) -> str:
    дом = дом_профилей()
    src, dst = os.path.join(дом, ИСТОЧНИК_ПРОФИЛЯ), os.path.join(дом, ПРОФИЛЬ)
    if not os.path.isfile(os.path.join(src, 'cordis.patch.yml')):
        raise SystemExit(f'✗ нет профиля-источника {src}')
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for f in ('package.json', 'pnpm-workspace.yaml', 'cordis.yml'):
        shutil.copy(os.path.join(src, f), dst)
    with open(os.path.join(src, 'cordis.patch.yml'), encoding='utf-8') as f:
        патч = патч_профиля(f.read(), адрес if re.match(r'https?://', адрес) else АДРЕС_ИНДЕКСА)
    with open(os.path.join(dst, 'cordis.patch.yml'), 'w', encoding='utf-8') as f:
        f.write(патч)
    пакет = f"@deepseek-ai/{БАНДЛ_МАРКЕТА}@{версия_рантайма()}"
    выполнить(команда_добавления(dsh, ПРОФИЛЬ, {'spec': пакет}))
    return dst


def выполнить(cmd: list) -> str:
    print('$ ' + ' '.join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=ТАЙМАУТ_УСТАНОВКИ)
    if p.returncode:
        raise SystemExit(f"✗ код {p.returncode}: {(p.stderr or p.stdout)[-600:]}")
    return p.stdout


def прочитать_индекс(адрес: str) -> dict:
    if re.match(r'https?://', адрес):
        with urllib.request.urlopen(адрес, timeout=60) as r:
            индекс = json.load(r)
    else:
        путь = адрес[len('file://'):] if адрес.startswith('file://') else адрес
        with open(путь, encoding='utf-8') as f:
            индекс = json.load(f)
    if индекс.get('format') != 1 or not isinstance(индекс.get('plugins'), list):
        raise SystemExit('✗ индекс не формата 1')
    return индекс


def адрес_url(адрес: str) -> str:
    """Абсолютный URL индекса для parseMarketIndex (локальный файл → file://)."""
    return адрес if re.match(r'[a-z]+://', адрес) else 'file://' + os.path.abspath(адрес)


def сверка_с_node(индекс: dict, цели: list, адрес: str = АДРЕС_ИНДЕКСА) -> None:
    """Если рядом есть `@deepseek-ai/dsh-plugin-market-index`, цели обязаны совпасть с его marketInstallTarget."""
    модуль = os.path.join(КОРЕНЬ, 'packages', 'node_modules', '@deepseek-ai', 'dsh-plugin-market-index', 'lib', 'index.js')
    if not os.path.isfile(модуль) or not shutil.which('node'):
        print('  (marketInstallTarget не найден — сверка пропущена)')
        return
    if any(len(x['versions']) != 1 for x in индекс['plugins']):
        print('  (у плагина несколько версий — сверка с node пропущена)')
        return
    js = ("import(process.argv[1]).then(m=>{const i=JSON.parse(require('fs').readFileSync(0,'utf8'));"
          "const p=m.parseMarketIndex(i,process.argv[2]);console.log(JSON.stringify(p.plugins.map(x=>"
          "m.marketInstallTarget(x.name,x.versions[0]))))})")
    p = subprocess.run(['node', '-e', js, 'file://' + модуль, адрес_url(адрес)], input=json.dumps(индекс), capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f'✗ parseMarketIndex отказал индексу: {p.stderr[-400:]}')
    эталон = json.loads(p.stdout)
    if эталон != цели:
        raise SystemExit(f'✗ цели расходятся с marketInstallTarget: {эталон} ≠ {цели}')
    print('✓ аргументы совпали с marketInstallTarget')


def не_хватает_в_списке(вывод: str, ожидаемые_имена) -> list:
    return [имя for имя in ожидаемые_имена if имя not in вывод]


def разобрать_поток(stdout: str) -> dict:
    """Строки `--json` headless: id сессии, финальный текст, вызовы инструментов."""
    r = {'sid': None, 'текст': '', 'вызовы': []}
    for строка in stdout.splitlines():
        try:
            e = json.loads(строка)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get('type') == 'session':
            r['sid'] = e.get('sessionId')
        elif e.get('type') == 'final':
            r['текст'] = e.get('text') or ''
        elif e.get('type') == 'tool_call':
            r['вызовы'].append((e.get('tool'), e.get('input')))
    return r


def оценить_справку(поток: dict, код: int) -> str | None:
    """None — справка ответила; иначе причина отказа."""
    if код:
        return f'dsh завершился с кодом {код}'
    текст = поток['текст'].strip()
    if not текст:
        return 'пустой ответ'
    m = ОТКАЗ.search(текст)
    if m:
        return f'в ответе «{m.group(0)}»'
    return None


def ключ_провайдера() -> str | None:
    if os.environ.get('OPENCODE_API_KEY'):
        return os.environ['OPENCODE_API_KEY']
    try:
        import yaml
        with open(os.path.expanduser('~/.dsh/.credentials.yaml'), encoding='utf-8') as f:
            return yaml.safe_load(f)['refs']['OPENCODE_API_KEY']
    except Exception:
        return None


def команда_справки(dsh: str, профиль: str) -> list:
    return [dsh, '--profile', профиль, '--json', КОМАНДА_СПРАВКИ]


def дымовая_справка(dsh: str) -> bool:
    ключ = ключ_провайдера()
    if not ключ:
        print('✗ нет OPENCODE_API_KEY (env или ~/.dsh/.credentials.yaml)')
        return False
    cmd = команда_справки(dsh, ПРОФИЛЬ)
    print('$ ' + ' '.join(cmd), flush=True)
    with tempfile.TemporaryDirectory(prefix='market-help-') as каталог:
        p = subprocess.run(cmd, cwd=каталог, env=dict(os.environ, OPENCODE_API_KEY=ключ),
                           capture_output=True, text=True, timeout=ТАЙМАУТ_СПРАВКИ)
    поток = разобрать_поток(p.stdout)
    причина = оценить_справку(поток, p.returncode)
    print(f"  сессия {поток['sid']}; вызовы инструментов: {[в[0] for в in поток['вызовы']] or 'нет'}")
    if причина:
        print(f'✗ {КОМАНДА_СПРАВКИ}: {причина}\n{(p.stderr or p.stdout)[-600:]}')
        return False
    print(f"✓ {КОМАНДА_СПРАВКИ} ответил ({len(поток['текст'])} симв.)")
    return True


def прогон_t2() -> bool:
    cmd = [sys.executable, os.path.join(КОРЕНЬ, 'tools', 'bench.py'), 'run', '--tasks', 't2', '--profile', 'market']
    print('$ python3 tools/bench.py run --tasks t2 --profile market', flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=КОРЕНЬ)
    sys.stdout.write(p.stdout[-3000:])
    if p.returncode:
        sys.stderr.write(p.stderr[-1500:])
        return False
    строки = [l for l in p.stdout.splitlines() if l.startswith('{')]
    return bool(строки) and json.loads(строки[-1]).get('успех') is True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Установка из индекса маркета и прогон t2')
    ap.add_argument('--индекс', default=АДРЕС_ИНДЕКСА, help='файл или URL индекса (по умолчанию удалённая витрина)')
    адрес = ap.parse_args(argv).индекс
    dsh = найти_dsh()
    print(f'• профиль {ПРОФИЛЬ} заново, маркет включён')
    создать_профиль(dsh, адрес)
    print(f'• индекс {адрес}')
    индекс = прочитать_индекс(адрес)
    недоступные = ()
    if any(п['name'] == ПЛЕКСУС for п in индекс['plugins']) and not в_npm(ПЛЕКСУС):
        print(f'  ! {ПЛЕКСУС} не отдаётся npm — проверка идёт без него')
        недоступные = (ПЛЕКСУС,)
    плагины = [п for п in индекс['plugins'] if п['name'] not in недоступные]
    цели = [цель_установки(п['name'], последняя(п)) for п in плагины]
    сверка_с_node({**индекс, 'plugins': плагины}, цели, адрес)
    for цель in цели:
        выполнить(команда_добавления(dsh, ПРОФИЛЬ, цель))
    вывод = выполнить(команда_списка(dsh, ПРОФИЛЬ))
    нет = не_хватает_в_списке(вывод, ожидаемые(индекс, недоступные))
    if нет:
        print(f"✗ в списке плагинов профиля нет: {', '.join(нет)}\n{вывод}")
        return 1
    print(f"✓ в списке профиля: {', '.join(ожидаемые(индекс, недоступные))}")
    if ПЛЕКСУС in ожидаемые(индекс, недоступные):
        if not дымовая_справка(dsh):
            return 1
    else:
        print(f'• {ПЛЕКСУС} нет — проверка {КОМАНДА_СПРАВКИ} пропущена')
    if not прогон_t2():
        print('✗ t2 не прошла')
        return 1
    print('✓ t2 успех')
    return 0


if __name__ == '__main__':
    sys.exit(main())
