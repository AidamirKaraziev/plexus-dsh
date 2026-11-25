#!/usr/bin/env python3
"""Сторож утечек: ищет личное в папке выпуска.

  сторож.py <папка> [--личный <файл>] [--почта <адрес>]

Ищет без учёта регистра: пути домашних папок, логин и фамилию (фамилия законна
в LICENSE и внутри ссылок на GitHub-аккаунт в любом файле: github.com/<аккаунт>/...,
raw.githubusercontent.com/<аккаунт>/..., git+https://github.com/<аккаунт>/...), почту автора
(кроме noreply-адресов GitHub) и каждую непустую строку личного списка.
Вывод: «что искали → файл:строка:текст». Код 1 при находке, 0 — чисто.
Образцы собраны по частям, чтобы сторож не находил сам себя в белом списке.
"""
import os
import re
import sys

USERS = "/Use" + "rs/"
HOME = "/ho" + "me/"
LOGIN = "ajda" + "mir"
SURNAME = "kara" + "ziev"
NOREPLY = "@users.noreply." + "github.com"
URL = re.compile(r"(?:git\+)?https?://(?:www\.)?(?:github\.com|raw\.githubusercontent\.com)/[^\s\"'<>)\]]*|(?:github\.com|raw\.githubusercontent\.com)/[^\s\"'<>)\]]*", re.I)


def разбор(argv):
    папка, личный, почта = None, None, ""
    it = iter(argv)
    for a in it:
        if a == "--личный":
            личный = next(it, None)
        elif a == "--почта":
            почта = next(it, "")
        elif папка is None:
            папка = a
        else:
            return None
    return (папка, личный, почта) if папка else None


def искомое(личный, почта):
    термы = [(USERS, USERS), (HOME, HOME), (LOGIN, LOGIN)]
    почта = (почта or "").strip()
    if почта and not почта.lower().endswith(NOREPLY):
        термы.append(("почта автора", почта))
    if личный and os.path.isfile(личный):
        with open(личный, encoding="utf-8") as f:
            for s in f:
                s = s.strip()
                if s:
                    термы.append(("личный список: " + s, s))
    return термы


def текст(путь):
    try:
        with open(путь, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main(argv):
    p = разбор(argv)
    if not p:
        print("сторож: нужно <папка> [--личный <файл>] [--почта <адрес>]")
        return 2
    папка, личный, почта = p
    термы = искомое(личный, почта)
    находки = []
    for корень, dirs, files in os.walk(папка):
        dirs[:] = [d for d in dirs if d != ".git"]
        for имя in sorted(files):
            путь = os.path.join(корень, имя)
            rel = os.path.relpath(путь, папка)
            data = текст(путь)
            if data is None:
                continue
            for n, строка in enumerate(data.split("\n"), 1):
                low = строка.lower()
                for метка, t in термы:
                    if t.lower() in low:
                        находки.append((метка, rel, n, строка))
                if SURNAME in low and rel != "LICENSE":
                    проверяемое = URL.sub("", low)
                    if SURNAME in проверяемое:
                        находки.append((SURNAME, rel, n, строка))
    for метка, rel, n, строка in находки:
        print("%s → %s:%d:%s" % (метка, rel, n, строка.strip()[:100]))
    return 1 if находки else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
