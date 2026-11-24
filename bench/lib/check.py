#!/usr/bin/env python3
"""Проверка задачи эталона на коммите (временный worktree) или в готовом каталоге.

    bench/check.sh <задача> [--этап N] [--ref эталон|снимок | --dir <каталог>]

<задача> — id (имя файла без .yml в bench/задачи/) или путь к .yml.
--ref эталон (по умолчанию) — решение; --ref снимок — состояние до решения;
--dir — каталог с решением агента (не трогается, кроме подмены скрытых тестов на время прогона).
--этап N — для задачи из этапов: проверить этапы 1..N (эталон — коммит этапа N); без него — все.

Скрытые тесты (`скрытые_тесты`) берутся из эталонного коммита этапа и кладутся в каталог
на время проверки; исходные файлы потом возвращаются. Сеть не нужна: GOPROXY=off, GOFLAGS=-mod=readonly.
Код 0 — пройдено, 1 — не пройдено, 2 — неверный вызов или сбой подготовки.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parent.parent


def загрузить(задача: str) -> dict:
    путь = Path(задача)
    if not путь.suffix:
        путь = BENCH / "задачи" / f"{задача}.yml"
    if not путь.is_file():
        raise SystemExit(f"✘ нет задачи: {путь}")
    t = yaml.safe_load(путь.read_text(encoding="utf-8"))
    t["_файл"] = путь
    return t


def этапы(t: dict) -> list[dict]:
    if t.get("этапы"):
        return t["этапы"]
    return [{"эталон": t["эталон"], "проверка": t["проверка"], "скрытые_тесты": t.get("скрытые_тесты") or []}]


def git(repo: Path, *args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, **kw)


def прогнать(repo: Path, каталог: Path, этап: dict, номер: int) -> int:
    ref = str(этап["эталон"])
    сохранено: dict[Path, bytes | None] = {}
    try:
        for rel in этап.get("скрытые_тесты") or []:
            r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{rel}"], capture_output=True)
            if r.returncode:
                print(f"✘ нет скрытого теста {rel} в {ref}", file=sys.stderr)
                return 2
            файл = каталог / rel
            сохранено[файл] = файл.read_bytes() if файл.exists() else None
            файл.parent.mkdir(parents=True, exist_ok=True)
            файл.write_bytes(r.stdout)
        env = dict(os.environ, BENCH=str(BENCH), CAND=str(каталог), REPO=str(repo), REF=ref,
                   GOPROXY="off", GOFLAGS="-mod=readonly", GOTOOLCHAIN="local", GOWORK="off",
                   PYTHONDONTWRITEBYTECODE="1")
        print(f"— этап {номер}: {этап['проверка'].strip()}", flush=True)
        r = subprocess.run(["bash", "-euo", "pipefail", "-c", этап["проверка"]], cwd=каталог, env=env)
        return 0 if r.returncode == 0 else 1
    finally:
        for файл, было in сохранено.items():
            if было is None:
                файл.unlink(missing_ok=True)
                # пустые каталоги, созданные под скрытый тест
                p = файл.parent
                while p != каталог and p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
                    p = p.parent
            else:
                файл.write_bytes(было)


def main() -> int:
    ap = argparse.ArgumentParser(description="проверка задачи эталона")
    ap.add_argument("задача")
    ap.add_argument("--этап", type=int)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--ref", choices=["эталон", "снимок"], default="эталон")
    g.add_argument("--dir")
    a = ap.parse_args()

    t = загрузить(a.задача)
    все = этапы(t)
    n = a.этап or len(все)
    if not 1 <= n <= len(все):
        print(f"✘ этапов в задаче {len(все)}, а просили {n}", file=sys.stderr)
        return 2
    выбранные = все[:n]
    repo = Path(os.path.expanduser(t["репо_путь"]))

    tmp = None
    wt = None
    try:
        if a.dir:
            каталог = Path(a.dir).resolve()
            if not каталог.is_dir():
                print(f"✘ нет каталога {каталог}", file=sys.stderr)
                return 2
            что = str(каталог)
        else:
            commit = str(t["снимок"]) if a.ref == "снимок" else str(выбранные[-1]["эталон"])
            tmp = Path(tempfile.mkdtemp(prefix="bench-"))
            wt = tmp / "wt"
            r = git(repo, "worktree", "add", "--detach", str(wt), commit)
            if r.returncode:
                print(f"✘ worktree на {commit}: {r.stderr}", file=sys.stderr)
                return 2
            каталог = wt
            что = f"{a.ref} {commit}"
        print(f"задача {t['id']} · {t['размер']} · {что}", flush=True)
        итог = 0
        for i, этап in enumerate(выбранные, 1):
            код = прогнать(repo, каталог, этап, i)
            if код == 2:
                return 2
            итог = итог or код
        print(f"{'✔ зелёный' if итог == 0 else '✘ красный'}: {t['id']}")
        return итог
    finally:
        if wt is not None:
            git(repo, "worktree", "remove", "--force", str(wt))
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
