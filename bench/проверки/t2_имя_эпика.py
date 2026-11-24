#!/usr/bin/env python3
"""Скрытый тест t2: имя эпика на экране старта для трёх видов строки roadmap ($CAND — каталог plexus)."""
import os
import sys
import tempfile
from pathlib import Path

lib = Path(os.environ["CAND"]) / "lib"
sys.path.insert(0, str(lib))
import экран_старта as э  # noqa: E402

ROAD = """# План

## Сейчас

- **E19 · Проводки** — журнал проводок · back
- **E14** Логи для человека — описание · 11/16 · back

## Дальше

- **E13** · docs
- **E12**
"""

with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    plan = repo / ".claude" / "plan"
    plan.mkdir(parents=True)
    (plan / "roadmap.md").write_text(ROAD, encoding="utf-8")
    (plan / "E13-мелочи аудита.md").write_text("## E13 · мелочи аудита\n", encoding="utf-8")
    имена = {e["id"]: e["name"] for e in э.эпики(repo)}

ждём = {"E19": "Проводки", "E14": "Логи для человека", "E13": "мелочи аудита", "E12": "E12"}
ошибки = [f"{k}: {имена.get(k)!r}, ждал {v!r}" for k, v in ждём.items() if имена.get(k) != v]
if ошибки:
    print("✘", *ошибки, sep="\n  ")
    sys.exit(1)
print("ок: имена эпиков", имена)
