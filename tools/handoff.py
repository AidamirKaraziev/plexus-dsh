"""Выжимка хода 1 для новой сессии (bench.py --handoff, E04-S03): из журнала DSH, без вызова модели.

Шаблон — итог скилла `end` Плексуса: что сделано / файлы / что проверить. Источник: правки (`write`, `edit`),
команды `bash` с проверками и последние тексты ассистента из журнала сессии хода 1. Выжимка — данные
журнала, не новые токены: рабочая сессия не раздувается лишней репликой, $ = 0.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsh_digest import load  # noqa: E402

ШАБЛОН = ("## Итог предыдущей сессии (этап уже начат, контекста ты не помнишь)\n"
          "**Что сделано:** {сделано}\n"
          "**Файлы:** {файлы}\n"
          "**Что проверить:** {проверить}\n")
ПРОВЕРКА = ("test", "check", "pytest", "go build", "go vet", "make", "lint", "tsc", "pnpm", "npm")
МАКС_ТЕКСТ, МАКС_КОМАНД = 1500, 5


def _args(d):
    try:
        return json.loads(d.get("arguments") or "{}")
    except ValueError:
        return {}


def выжимка(события: list[dict]) -> dict:
    """{'сделано', 'файлы', 'проверить'} из событий журнала (весь журнал = ход 1 новой сессии)."""
    файлы, команды, тексты = [], [], []
    for e in события:
        d = e.get("data", {})
        if e["type"] == "tool/call" and d.get("name") in ("write", "edit"):
            p = _args(d).get("file_path") or _args(d).get("path")
            if p and p not in файлы:
                файлы.append(p)
        elif e["type"] == "tool/call" and d.get("name") == "bash":
            c = (_args(d).get("command") or "").strip()
            if c and any(k in c for k in ПРОВЕРКА):
                команды.append(c.splitlines()[0][:200])
        elif e["type"] == "assistant/message":
            t = "".join(b.get("text", "") for b in d["message"].get("content", []) if b.get("type") == "text").strip()
            if t:
                тексты.append(t)
    return {"сделано": (тексты[-1][:МАКС_ТЕКСТ] if тексты else "нет итогового текста"),
            "файлы": ", ".join(файлы) or "правок файлов нет",
            "проверить": "; ".join(dict.fromkeys(команды[-МАКС_КОМАНД:])) or "команд проверки в журнале нет — запусти проверку из задания"}


def выжимка_сессии(путь_журнала) -> str:
    return ШАБЛОН.format(**выжимка(load(str(путь_журнала))))


def реплика_хода2(текст_выжимки: str, текст_хода: str) -> str:
    """Первая реплика новой сессии: выжимка + текст хода."""
    return f"{текст_выжимки}\n{текст_хода}"
