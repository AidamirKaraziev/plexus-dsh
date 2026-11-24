#!/usr/bin/env python3
"""Сверка сессий DSH с их папками.

DSH кладёт сессию в ~/.dsh/sessions/<ключ cwd>/<id>/session.v4.jsonl.zstd, где
ключ собирается из cwd в заголовке сессии. При запуске он читает все сессии и
проверяет, что каждая лежит ровно там, куда указывает её заголовок. Хоть одна не
на месте — реестр рабочих пространств не поднимается, а с ним и workspaceController:
«workspace create failed: … "workspaceController" is unavailable», проекты пропадают.

Так случилось 24.11.2026: при переименовании cortex_harness → plexus-dsh восемь
сессий перенесли в папку нового пути, а в заголовках остался старый cwd.

    python3 tools/dsh_sessions_check.py          # проверить; код 1, если есть сессии не на месте
    python3 tools/dsh_sessions_check.py --fix    # вернуть их в папки по заголовку
    DSH_SESSIONS=… — другой корень сессий (по умолчанию ~/.dsh/sessions)

Заголовки не переписываются: cwd сессии — её история, DSH сверяет по нему.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ЛОГ = re.compile(r'^session\.v(\d+)\.jsonl(\.zstd)?$')


def ключ_проекта(cwd: str) -> str:
    """Повтор projectKey из @deepseek-ai/dsh-session-persistence-jsonl (DSH 0.2.2-rc.5)."""
    if not cwd:
        raise ValueError('пустой cwd')
    out, run = [], False
    for ch in cwd:
        if ch in '/\\:':
            if not run:
                out.append('-')
            run = True
        elif ch != '~' and re.fullmatch(r'[A-Za-z0-9._-]', ch):
            out.append(ch)
            run = False
        else:
            out.append('~%04X' % ord(ch))
            run = False
    readable = ''.join(out).lstrip('-') or 'root'
    return f'--{readable[:251]}--'


def заголовок(лог: Path) -> dict | None:
    if лог.suffix == '.zstd':
        if not shutil.which('zstd'):
            sys.exit('нужен zstd: brew install zstd')
        r = subprocess.run(['zstd', '-dc', str(лог)], capture_output=True)
        текст = r.stdout
    else:
        текст = лог.read_bytes()
    первая = текст.split(b'\n', 1)[0]
    try:
        return json.loads(первая)
    except ValueError:
        return None


def обход(корень: Path):
    for проект in sorted(p for p in корень.iterdir() if p.is_dir()):
        for сессия in sorted(p for p in проект.iterdir() if p.is_dir()):
            логи = sorted((f for f in сессия.iterdir() if ЛОГ.match(f.name)),
                          key=lambda f: int(ЛОГ.match(f.name).group(1)), reverse=True)
            if логи:
                yield проект, сессия, логи[0]


def main() -> int:
    чинить = '--fix' in sys.argv[1:]
    корень = Path(os.environ.get('DSH_SESSIONS', '~/.dsh/sessions')).expanduser()
    if not корень.is_dir():
        print(f'нет папки сессий: {корень}')
        return 0
    беды, всего = [], 0
    for проект, сессия, лог in обход(корень):
        всего += 1
        h = заголовок(лог)
        if not h or 'cwd' not in h:
            if проект.name != '_no-cwd':
                print(f'? {сессия}: заголовок без cwd — не трогаю')
            continue
        нужно = ключ_проекта(h['cwd'])
        if проект.name != нужно:
            беды.append((сессия, корень / нужно / сессия.name, h['cwd']))
    for откуда, куда, cwd in беды:
        print(f'не на месте: {откуда.relative_to(корень)}\n   cwd {cwd} → {куда.relative_to(корень)}')
    if not беды:
        print(f'сессий DSH: {всего}, все на своих местах')
        return 0
    if not чинить:
        print(f'\nне на месте {len(беды)} из {всего}. DSH с ними не откроет ни одну папку. Починка: --fix')
        return 1
    if subprocess.run(['pgrep', '-f', 'MacOS/DeepSeek Harness'], capture_output=True).returncode == 0:
        print('\nвнимание: DeepSeek Harness запущен — после переноса перезапустите его (⌘Q)')
    for откуда, куда, _ in беды:
        if куда.exists():
            print(f'пропуск: {куда} уже есть — разберите вручную')
            continue
        куда.parent.mkdir(mode=0o700, exist_ok=True)
        shutil.move(str(откуда), str(куда))
        print(f'перенесено: {куда.relative_to(корень)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
