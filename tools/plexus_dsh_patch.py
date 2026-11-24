#!/usr/bin/env python3
"""Вписать или снять блок пакетов продукта в cordis.patch.yml профиля DSH.

Блок — текст между маркерами, остальной файл (с комментариями) не трогается.
Идемпотентно: повторная установка заменяет блок. Перед первой правкой —
копия cordis.patch.yml.<имя продукта>.bak (один раз, дальше не перезаписывается).

    plexus_dsh_patch.py install   <patch.yml> <entries.json>
    plexus_dsh_patch.py uninstall <patch.yml>

entries.json: [{"id": "<имя продукта>-hello", "name": "/abs/lib/index.js", "config": {...}}, ...]
"""
import json
import os
import shutil
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsh_digest import NAME  # noqa: E402  имя продукта — единственное место

class _Loader(yaml.SafeLoader):
    """SafeLoader, который пропускает теги DSH (`!!js` и др.) — нужна только форма."""


_Loader.add_multi_constructor("", lambda loader, suffix, node: None)
_Loader.add_multi_constructor("tag:yaml.org,2002:js", lambda loader, suffix, node: None)

BEGIN = f"# >>> {NAME} packages (tools/install.sh) — не править руками"
END = f"# <<< {NAME} packages"


def strip_block(text: str) -> str:
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == BEGIN:
            inside = True
            continue
        if inside and line.rstrip("\n") == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    if inside:
        sys.exit("plexus_dsh_patch: нет закрывающего маркера в блоке пакетов")
    return "".join(out).rstrip("\n") + "\n"


def drop_empty_list(text: str) -> str:
    """Убрать пустой список `[]`, оставленный DSH: он и есть «нет записей», а не шапка файла."""
    out = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "[]":
            continue
        out.append(line)
    return "".join(out).rstrip("\n") + "\n" if "".join(out).strip() else ""


def render_block(entries: list) -> str:
    lines = [BEGIN, "- insert:"]
    for e in entries:
        lines.append(f"    - id: {e['id']}")
        lines.append(f"      name: {json.dumps(e['name'], ensure_ascii=False)}")
        if e.get("config"):
            lines.append(f"      config: {json.dumps(e['config'], ensure_ascii=False)}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("install", "uninstall"):
        sys.exit(__doc__)
    action, path = sys.argv[1], sys.argv[2]
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    base = drop_empty_list(strip_block(text)) if text else ""
    if action == "install":
        entries = json.load(open(sys.argv[3], encoding="utf-8"))
        new = base + ("\n" if base else "") + render_block(entries) if entries else base
    else:
        new = base
        # Пустой файл из одних комментариев YAML читает как None, а не список — возвращаем `[]`.
        if new and not [л for л in new.splitlines() if л.strip() and not л.lstrip().startswith("#")]:
            new = new.rstrip("\n") + "\n[]\n"

    if new == text:
        print(f"  {path}: без изменений")
        return
    parsed = yaml.load(new, Loader=_Loader)
    if not isinstance(parsed, list):
        sys.exit(f"plexus_dsh_patch: {path} после правки — не YAML-список, отказ")
    bak = path + f".{NAME}.bak"
    if text and not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"  {bak}: копия до первой правки")
    tmp = path + f".{NAME}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    print(f"  {path}: {'вписан блок ' + NAME if action == 'install' else 'блок ' + NAME + ' снят'}")


if __name__ == "__main__":
    main()
