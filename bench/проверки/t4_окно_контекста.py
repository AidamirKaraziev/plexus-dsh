#!/usr/bin/env python3
"""Скрытый тест t4: окно контекста по id модели и правило «занято больше окна → 1M».

Гоняет `lib/контекст.py --repo <пустой> --сессия <журнал>` кандидата ($CAND — каталог plexus).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

скрипт = Path(os.environ["CAND"]) / "lib" / "контекст.py"


def журнал(path: Path, used: int, model: str) -> None:
    rows = [{"message": {"role": "user", "content": "hi"}},
            {"message": {"role": "assistant", "model": model, "usage": {
                "input_tokens": 1, "cache_creation_input_tokens": used - 101,
                "cache_read_input_tokens": 100, "output_tokens": 5}}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


СЛУЧАИ = [  # (занято, модель, код, подстрока вывода)
    (109_000, "claude-opus-5-5", 0, "109k из 1M · 11 %"),
    (109_000, "claude-opus-4-8", 0, "из 1M"),
    (10_000, "claude-opus-4-7", 0, "из 1M "),
    (10_000, "claude-opus-4-6", 0, "из 1M "),
    (10_000, "claude-sonnet-4-6", 0, "из 1M "),
    (10_000, "claude-sonnet-5-5", 0, "из 1M "),
    (10_000, "claude-haiku-5-5", 0, "из 1M "),
    (10_000, "claude-fable-5-2", 0, "из 1M "),
    (10_000, "claude-mythos-5", 0, "из 1M "),
    (10_000, "claude-opus-4-5-20251101", 0, "из 200k "),
    (10_000, "claude-sonnet-4-5", 0, "из 200k "),
    (10_000, "claude-haiku-4-5-20251001", 0, "из 200k "),
    (10_000, "claude-3-7-sonnet", 0, "из 200k "),
    (50_000, "gpt-unknown", 1, "из 200k"),
    (50_000, "claude-sonnet-4-5[1m]", 0, "из 1M"),
    (250_000, "claude-next-9", 1, "250k из 1M · 25 %"),
]

ошибки = []
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    j = Path(tmp) / "s.jsonl"
    for used, model, код, ждём in СЛУЧАИ:
        журнал(j, used, model)
        r = subprocess.run([sys.executable, str(скрипт), "--repo", str(repo), "--сессия", str(j)],
                           capture_output=True, text=True)
        if r.returncode != код or ждём not in r.stdout:
            ошибки.append(f"{model} {used}: код {r.returncode} (ждал {код}), вывод {r.stdout.strip()!r}{r.stderr.strip()[-200:]}")
if ошибки:
    print("✘", *ошибки, sep="\n  ")
    sys.exit(1)
print(f"ок: окна контекста, случаев {len(СЛУЧАИ)}")
