#!/usr/bin/env python3
"""Скрытый тест для скриптов с самопроверкой «чёрным ящиком» (subprocess на свой __file__).

    python3 самопроверка_эталона.py <путь скрипта в репо> [флаг самопроверки]

Берёт скрипт из эталонного коммита ($REPO, $REF), подменяет в нём `__file__` на путь
скрипта в проверяемом каталоге ($CAND) и запускает самопроверку эталона: она гоняет
скрипт кандидата. Код — код самопроверки.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    rel = sys.argv[1]
    флаг = sys.argv[2] if len(sys.argv) > 2 else "--самопроверка"
    repo, ref, cand = os.environ["REPO"], os.environ["REF"], Path(os.environ["CAND"])
    текст = subprocess.run(["git", "-C", repo, "show", f"{ref}:{rel}"], capture_output=True,
                           text=True, check=True).stdout
    кандидат = cand / rel
    if not кандидат.is_file():
        print(f"✘ нет {кандидат}")
        return 1
    текст = текст.replace("__file__", repr(str(кандидат)))
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "харнесс.py"
        h.write_text(текст, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(кандидат.parent))
        return subprocess.run([sys.executable, str(h), флаг], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
