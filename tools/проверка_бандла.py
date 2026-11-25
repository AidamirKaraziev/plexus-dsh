#!/usr/bin/env python3
"""Проверка пакетов продукта как бандла DSH: одна команда на задачу круга.

    python3 tools/проверка_бандла.py манифесты    — поля бандла и локали в манифестах пакетов
    python3 tools/проверка_бандла.py составе      — патч бандла из манифестов, иконка, локали, detail
    python3 tools/проверка_бандла.py установка    — tarball ставится в чистый профиль, строки видны

Установка идёт в отдельный DSH_HOME (`--дом`, по умолчанию временный каталог): настоящий профиль
не трогается, `~/.plexus-dsh` тоже. Рантайм берётся из приложения DSH (`--dsh`) или из PATH.

Код 0 — всё сошлось, 1 — есть расхождение (каждая строка `✗` объясняет, что не так).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsh_digest import NAME  # noqa: E402  имя продукта — единственное место

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПАКЕТЫ = os.path.join(КОРЕНЬ, 'packages')
БАНДЛ = 'bundle'
# Пакеты, которые бандл несёт в профиль; остальные ставятся поштучно.
В_БАНДЛЕ = ('meter', 'warm', 'read-cap', 'scout')
# Поля контракта бандла: у всех шести пакетов одинаковы.
ПОЛЯ = ('dsh', 'engines', 'exports', 'files', 'plexus', 'locale')
РАНТАЙМ_ФАЙЛ = os.path.join(КОРЕНЬ, 'tools', 'runtime.json')
ТЕКСТ_ФАЙЛ = os.path.join(КОРЕНЬ, 'tools', 'bundle-text.json')
ПАТЧ_ФАЙЛ = 'cordis.patch.yml'

CEIL = 0
for _путь in ('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh', shutil.which('dsh')):
    if _путь and os.path.exists(_путь):
        CEIL = _путь
        break


def ошибки() -> None:
    raise SystemExit(1)


def читать(путь: str) -> dict:
    with open(путь, encoding='utf-8') as f:
        return json.load(f)


def пакеты() -> list:
    """Короткие имена всех пакетов продукта (без самого бандла)."""
    return sorted(
        п[len(NAME) + 1:].replace('/', '')
        for п in os.listdir(ПАКЕТЫ)
        if п.startswith(NAME + '-') and п != f'{NAME}-{БАНДЛ}'
        and os.path.isfile(os.path.join(ПАКЕТЫ, п, 'package.json'))
    )


def манифест(короткое: str) -> dict:
    return читать(os.path.join(ПАКЕТЫ, f'{NAME}-{короткое}', 'package.json'))


def манифест_бандла() -> dict:
    return читать(os.path.join(ПАКЕТЫ, f'{NAME}-{БАНДЛ}', 'package.json'))


def проверки_манифестов() -> list:
    """Задача 1: у каждого пакета поля контракта бандла и обе локали."""
    бандл = манифест_бандла()
    беды = []
    for короткое in пакеты():
        каталог = os.path.join(ПАКЕТЫ, f'{NAME}-{короткое}')
        м = манифест(короткое)
        имя = м.get('name', '?')
        if м.get('name') != f'@{NAME}/{короткое}':
            беды.append(f'{имя}: name должно быть @{NAME}/{короткое}')
        if 'cortex' in м:
            беды.append(f'{имя}: остался ключ cortex (его место занял plexus)')
        if 'plexus' not in м:
            беды.append(f'{имя}: нет ключа plexus')
        elif not isinstance(м['plexus'].get('id'), str) or not м['plexus']['id'].startswith(NAME + '-'):
            беды.append(f'{имя}: plexus.id должен начинаться с {NAME}-')
        if м.get('engines', {}).get('dsh') != бандл.get('engines', {}).get('dsh'):
            беды.append(f'{имя}: engines.dsh не совпадает с бандлом')
        peer = м.get('peerDependencies', {})
        if not peer:
            беды.append(f'{имя}: нет peerDependencies')
        for пакет, диапазон in peer.items():
            if str(диапазон).startswith('workspace:'):
                беды.append(f'{имя}: peer {пакет} задан через workspace: — установка не пройдёт')
        if м.get('exports', {}).get('./locale/*.json') != './locale/*.json':
            беды.append(f'{имя}: exports без ./locale/*.json')
        if м.get('exports', {}).get('.') is None:
            беды.append(f'{имя}: exports без точки входа')
        файлы = м.get('files', [])
        if not any(ф.startswith('lib/') for ф in файлы):
            беды.append(f'{имя}: files без lib/')
        if 'locale/*.json' not in файлы:
            беды.append(f'{имя}: files без locale/*.json')
        for язык in ('en', 'ru'):
            путь = os.path.join(каталог, 'locale', f'{язык}.json')
            if not os.path.isfile(путь):
                беды.append(f'{имя}: нет locale/{язык}.json')
                continue
            лок = читать(путь)
            мета = лок.get('meta', {})
            for поле in ('title', 'description'):
                if not str(мета.get(поле, '')).strip():
                    беды.append(f'{имя}: locale/{язык}.json без meta.{поле}')
        # Конфиг формы собирается из схемы, а не из манифеста: подпись поля живёт в локали.
        лок_ru = читать(os.path.join(каталог, 'locale', 'ru.json'))
        подписи = json.loads(json.dumps(лок_ru.get('config', {}), ensure_ascii=False))
        if not isinstance(подписи, dict):
            беды.append(f'{имя}: locale/ru.json: config должен быть словарём')
        исходник = os.path.join(каталог, 'src', 'index.ts')
        if os.path.isfile(исходник):
            текст = open(исходник, encoding='utf-8').read()
            if 'export const Config = z.object(' not in текст:
                беды.append(f'{имя}: src/index.ts без схемы export const Config = z.object')
            if re.search(r'^export interface Config\b', текст, re.M):
                беды.append(f'{имя}: src/index.ts: остался export interface Config (нужна схема)')
            поля = set(re.findall(r'^  (\w+): z\.', текст, re.M))
            if поля - set(подписи):
                беды.append(f'{имя}: в схеме поля без подписи: ' + ', '.join(sorted(поля - set(подписи))))
    return беды


def проверки_состава() -> list:
    """Задача 2: патч бандла соответствует манифестам, у бандла есть лицо и описание."""
    беды = []
    каталог = os.path.join(ПАКЕТЫ, f'{NAME}-{БАНДЛ}')
    м = манифест_бандла()
    патч = м.get('dsh', {}).get('bundle', {}).get('patch')
    if патч != './' + ПАТЧ_ФАЙЛ:
        беды.append(f'бандл: dsh.bundle.patch должен быть ./{ПАТЧ_ФАЙЛ}, а не {патч!r}')
    if м.get('private') is not True:
        беды.append('бандл: пока не опубликован, private должен быть true')
    if 'main' in м:
        беды.append('бандл: у бандла нет своего кода — main не нужен')
    файл_патча = os.path.join(каталог, ПАТЧ_ФАЙЛ)
    if not os.path.isfile(файл_патча):
        return беды + [f'бандл: нет {ПАТЧ_ФАЙЛ} (сгенерируйте: node tools/bundle.mjs патч)']

    # Патч — ровно строки пакетов, у которых plexus.bundle.build не false, в заданном порядке.
    строки = []
    текущая = None
    for строка in open(файл_патча, encoding='utf-8'):
        m_id = re.match(r'^\s*- id:\s*(\S+)\s*$', строка)
        if m_id:
            текущая = {'id': m_id.group(1)}
            строки.append(текущая)
            continue
        m_имя = re.match(r'^\s+name:\s*[\'"]?([^\'"]+)[\'"]?\s*$', строка)
        if m_имя and текущая is not None:
            текущая['name'] = m_имя.group(1)
    ожидаемые = [к for к in В_БАНДЛЕ]
    фактические = [с['name'] for с in строки if 'name' in с]
    for короткое in пакеты():
        м_пакета = манифест(короткое)
        входит = м_пакета.get('plexus', {}).get('bundle', {}).get('build') is not False
        if входит != (короткое in В_БАНДЛЕ):
            беды.append(f'{короткое}: plexus.bundle.build={входит}, а бандл его '
                        + ('ждёт' if короткое in В_БАНДЛЕ else 'не ждёт'))
    if фактические != [f'@{NAME}/{к}' for к in ожидаемые]:
        беды.append('бандл: строки патча не совпадают с составом: ' + ', '.join(фактические))
    for строка in строки:
        if 'id' not in строка or 'name' not in строка:
            беды.append(f'бандл: строка патча без id или name: {строка}')
    if len({с.get('id') for с in строки}) != len(строки):
        беды.append('бандл: в патче повторяются id')

    # Лицо бандла: иконка, локали, подробное описание — по пути, который объявлен.
    if not os.path.isfile(os.path.join(каталог, 'icon.svg')):
        беды.append('бандл: нет icon.svg')
    if м.get('exports', {}).get('./icon') != './icon.svg':
        беды.append('бандл: exports без ./icon')
    for язык in ('en', 'ru'):
        путь = os.path.join(каталог, 'locale', f'{язык}.json')
        if not os.path.isfile(путь):
            беды.append(f'бандл: нет locale/{язык}.json')
            continue
        мета = читать(путь).get('meta', {})
        for поле in ('title', 'description'):
            if not str(мета.get(поле, '')).strip():
                беды.append(f'бандл: locale/{язык}.json без meta.{поле}')
        detail = мета.get('detail', '')
        if detail and not os.path.isfile(os.path.join(каталог, detail)):
            беды.append(f'бандл: meta.detail указывает на {detail}, а файла нет')
        if detail and f'./{os.path.dirname(detail)}/*' not in м.get('exports', {}):
            беды.append(f'бандл: exports не открывает {os.path.dirname(detail)}/*')
    # Пакеты бандла — его зависимости: без них строки патча не найдут модуль.
    зав = set(м.get('dependencies', {}))
    for короткое in В_БАНДЛЕ:
        if f'@{NAME}/{короткое}' not in зав:
            беды.append(f'бандл: {короткое} не в dependencies')
    return беды


def паковать(где: str) -> str:
    """Tarball бандла — так его и получит человек (или маркет) в руки."""
    pnpm = shutil.which('pnpm') or 'pnpm'  # на Windows это pnpm.cmd
    subprocess.run([pnpm, '--filter', f'@{NAME}/{БАНДЛ}', 'exec', pnpm, 'pack', '--pack-destination', где],
                   cwd=ПАКЕТЫ, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
    for имя in os.listdir(где):
        if имя.endswith('.tgz'):
            return os.path.join(где, имя)
    raise RuntimeError(f'pnpm pack не оставил tarball в {где}')


def проверки_установки(дом: str, dsh: str) -> list:
    """Задача 3: бандл ставится в чистый профиль, строки и схемы видны, лишнее не тащится."""
    беды = []
    если_нет = [с for с in (dsh,) if not с or not os.path.exists(с)]
    if если_нет:
        return ['не найден запускатор dsh (--dsh или PATH)']
    временное = tempfile.mkdtemp(prefix='проверка-бандла-')
    try:
        архив = паковать(временное)
        with tarfile.open(архив) as тар:
            внутри = тар.getnames()
        # pnpm кладёт bundled-зависимости с путём `package/../node_modules/…`: на распаковке
        # это совпадает с `package/node_modules/…`, поэтому `..` в пути — норма, проверяем итог.
        плохие = [и for и in внутри if '..' in и.split('/')[:-1] and not и.startswith('package/../node_modules/')]
        if плохие:
            беды.append('tarball: файлы за пределами пакета: ' + ', '.join(плохие[:3]))
        for обязателен in (ПАТЧ_ФАЙЛ, 'icon.svg', 'locale/ru.json', 'detail/README.md'):
            if not не_в(внутри, обязателен):
                беды.append(f'tarball: нет {обязателен}')
        for короткое in В_БАНДЛЕ:
            if not не_в(внутри, f'node_modules/@{NAME}/{короткое}/lib/index.js'):
                беды.append(f'tarball: нет собранного пакета {короткое}')
            if not не_в(внутри, f'node_modules/@{NAME}/{короткое}/lib/brand.json'):
                беды.append(f'tarball: у {короткое} нет lib/brand.json — плагин не загрузится')

        профиль = os.path.join(дом, 'profiles', 'проверка')
        окружение = dict(os.environ, DSH_HOME=дом)
        установка = subprocess.run([dsh, 'plugin', '--profile', 'проверка', 'add', архив],
                                   env=окружение, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if установка.returncode != 0:
            хвост = (установка.stderr or установка.stdout).strip().splitlines()[-3:]
            return беды + ['установка не прошла: ' + ' / '.join(хвост)]
        манифест_профиля = читать(os.path.join(профиль, 'package.json'))
        бандлы = манифест_профиля.get('dsh', {}).get('profile', {}).get('bundles', [])
        if f'@{NAME}/{БАНДЛ}' not in бандлы:
            беды.append('профиль: бандл не выбран в dsh.profile.bundles')

        конфиг = subprocess.run([dsh, '--profile', 'проверка', '--dump-config'],
                                env=окружение, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if конфиг.returncode != 0:
            return беды + ['--dump-config не прошёл: ' + конфиг.stderr.strip().splitlines()[-1]]
        текст = конфиг.stdout
        for короткое in В_БАНДЛЕ:
            if f'- id: {NAME}-{короткое}' not in текст:
                беды.append(f'профиль: в конфиге нет строки {NAME}-{короткое}')
        for короткое in ('hello', 'budget'):
            if f'- id: {NAME}-{короткое}' in текст:
                беды.append(f'профиль: строка {NAME}-{короткое} попала в бандл, а не должна')

        схема = subprocess.run([dsh, '--profile', 'проверка', '--dump-config-schema'],
                               env=окружение, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if схема.returncode != 0:
            # Схему печатают только пакеты с Config; загрузка без ошибок уже проверена выше.
            беды.append('--dump-config-schema не прошёл: ' + схема.stderr.strip().splitlines()[-1])
        else:
            for короткое in В_БАНДЛЕ:
                if f'{NAME}-{короткое}' not in схема.stdout:
                    беды.append(f'настройки: {NAME}-{короткое} нет в схеме (нет Config или пакет не загрузился)')
    finally:
        shutil.rmtree(временное, ignore_errors=True)
    return беды


def не_в(имена: list, что: str) -> bool:
    """Есть ли файл в tarball: записи начинаются с `package/`, bundled — с `package/../`."""
    return any(имя == 'package/' + что or имя.endswith('/' + что) for имя in имена)


def main() -> int:
    разбор = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    разбор.add_argument('что', choices=('манифесты', 'составе', 'установка', 'всё'))
    разбор.add_argument('--дом', default=None, help='каталог DSH_HOME для установки (по умолчанию временный)')
    разбор.add_argument('--dsh', default=CEIL, help='запускатор dsh (по умолчанию приложение DSH или PATH)')
    args = разбор.parse_args()

    шаги = []
    if args.что in ('манифесты', 'всё'):
        шаги.append(('манифесты', lambda: проверки_манифестов()))
    if args.что in ('составе', 'всё'):
        шаги.append(('составе', lambda: проверки_состава()))
    if args.что in ('установка', 'всё'):
        дом = args.дом or tempfile.mkdtemp(prefix='ds-home-')
        свой = args.дом is None
        try:
            шаги.append(('установка', lambda: проверки_установки(дом, args.dsh)))
            всего = []
            for название, проверка in шаги:
                беды = проверка()
                всего.append((название, беды))
        finally:
            if свой:
                shutil.rmtree(дом, ignore_errors=True)
    else:
        всего = [(название, проверка()) for название, проверка in шаги]

    провал = False
    for название, беды in всего:
        if беды:
            провал = True
            print(f'✗ {название}: {len(беды)} расхождени(й)')
            for беда in беды:
                print(f'  - {беда}')
        else:
            print(f'✓ {название}')
    return 1 if провал else 0


if __name__ == '__main__':
    sys.exit(main())
