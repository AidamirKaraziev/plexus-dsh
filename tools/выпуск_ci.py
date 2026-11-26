#!/usr/bin/env python3
"""Выпуск из CI: публикация tarball'ов, ожидание архива в npm, зеркало npmmirror, сводка.

Читает <папка>/порядок.txt (его пишет опубликовать.sh --tgz): строки
`@plexus-dsh/<п> <версия> <файл.tgz>`. Только stdlib. Сеть, npm и часы вынесены
в функции верхнего уровня (http_статус, http_json, http_put, запустить_npm,
часы, спать) - тесты их подменяют.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ОБЛАСТЬ = "@plexus-dsh/"
РЕЕСТР = "https://registry.npmjs.org"
ЗЕРКАЛО_ЗАПИСЬ = "https://registry-direct.npmmirror.com"
ЗЕРКАЛО_ЧТЕНИЕ = "https://registry.npmmirror.com"
РЕПО_ВИТРИНЫ = "https://github.com/AidamirKaraziev/plexus-dsh"
ФАЙЛ_ЗЕРКАЛА = "зеркало.json"


# --- подменяемые внешние функции -------------------------------------------

def часы():
    return time.monotonic()


def спать(секунд):
    time.sleep(секунд)


def http_статус(url, метод="HEAD"):
    """HTTP-код ответа; 0 - сеть недоступна."""
    запрос = urllib.request.Request(url, method=метод)
    try:
        with urllib.request.urlopen(запрос, timeout=30) as ответ:
            return ответ.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def http_json(url):
    """Тело ответа как текст (разбор - в версия_в_ответе); None - сбой сети."""
    try:
        with urllib.request.urlopen(url, timeout=30) as ответ:
            return ответ.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None


def http_put(url):
    """HTTP-код PUT с пустым телом; 0 - сеть недоступна."""
    запрос = urllib.request.Request(url, data=b"{}", method="PUT",
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(запрос, timeout=30) as ответ:
            return ответ.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def запустить_npm(аргументы):
    return subprocess.run(["npm", *аргументы]).returncode


# --- разбор и адреса ---------------------------------------------------------

class Ошибка(Exception):
    pass


def разобрать_порядок(папка):
    """[(имя, версия, файл)] из <папка>/порядок.txt; пустой файл - пустой список."""
    путь = os.path.join(папка, "порядок.txt")
    try:
        with open(путь, encoding="utf-8") as f:
            текст = f.read()
    except OSError as e:
        raise Ошибка(f"нет файла {путь}: {e}")
    результат = []
    for н, строка in enumerate(текст.splitlines(), 1):
        if not строка.strip():
            continue
        части = строка.split()
        if (len(части) != 3 or not части[0].startswith(ОБЛАСТЬ) or len(части[0]) == len(ОБЛАСТЬ)
                or not части[2].endswith(".tgz") or части[2] != os.path.basename(части[2])
                or части[2].startswith(".")):
            raise Ошибка(f"{путь}:{н}: ожидается «@plexus-dsh/<п> <версия> <файл.tgz>», а не «{строка}»")
        результат.append((части[0], части[1], части[2]))
    return результат


def короткое(имя):
    return имя[len(ОБЛАСТЬ):]


def адрес_архива(имя, версия):
    п = короткое(имя)
    return f"{РЕЕСТР}/{ОБЛАСТЬ}{п}/-/{п}-{версия}.tgz"


def адрес_синхронизации(имя):
    return f"{ЗЕРКАЛО_ЗАПИСЬ}/-/package/{ОБЛАСТЬ}{короткое(имя)}/syncs"


def адрес_опроса_зеркала(имя):
    return f"{ЗЕРКАЛО_ЧТЕНИЕ}/{ОБЛАСТЬ}{короткое(имя)}?t={int(time.time() * 1000)}"


def версия_в_ответе(тело, версия):
    """True, если версия есть в versions; False - нет, битый JSON или не тот вид."""
    try:
        данные = json.loads(тело)
    except (TypeError, ValueError):
        return False
    версии = данные.get("versions") if isinstance(данные, dict) else None
    return isinstance(версии, dict) and версия in версии


# --- подкоманды --------------------------------------------------------------

def команда_опубликовать(а):
    пакеты = разобрать_порядок(а.папка)
    for имя, версия, файл in пакеты:
        if а.сухой:
            print(f"было бы опубликовано: {имя}@{версия} ({файл})")
            continue
        print(f"публикую {имя}@{версия}")
        if запустить_npm(["publish", os.path.join(а.папка, файл), "--access", "public"]) != 0:
            print(f"ОШИБКА: не опубликован {имя}@{версия}; дальше не идём", file=sys.stderr)
            return 1
    return 0


def команда_ждать(а):
    пакеты = разобрать_порядок(а.папка)
    for имя, версия, _ in пакеты:
        url = адрес_архива(имя, версия)
        if а.сухой:
            print(f"было бы ждали: {имя}@{версия} - {url}")
            continue
        старт = часы()
        while True:
            if http_статус(url) == 200:
                print(f"в npm: {имя}@{версия}")
                break
            if часы() - старт >= а.таймаут:
                print(f"ОШИБКА: {имя}@{версия} не появился в npm за {а.таймаут} с: {url}", file=sys.stderr)
                return 1
            спать(а.шаг)
    return 0


def _зеркало_одного(имя, версия, таймаут, шаг):
    """(состояние, пояснение)."""
    код = http_put(адрес_синхронизации(имя))
    if not 200 <= код < 300:
        return "ошибка", f"PUT syncs вернул {код}"
    старт = часы()
    while True:
        тело = http_json(адрес_опроса_зеркала(имя))
        if тело is not None and версия_в_ответе(тело, версия):
            return "есть", ""
        if часы() - старт >= таймаут:
            return "таймаут", f"версии нет на зеркале за {таймаут} с"
        спать(шаг)


def команда_зеркало(а):
    пакеты = разобрать_порядок(а.папка)
    итог = {}
    for имя, версия, _ in пакеты:
        if а.сухой:
            print(f"было бы синхронизировано с зеркалом: {имя}@{версия}")
            continue
        состояние, пояснение = _зеркало_одного(имя, версия, а.таймаут, а.шаг)
        итог[имя] = {"версия": версия, "состояние": состояние, "пояснение": пояснение}
        if состояние == "есть":
            print(f"на зеркале: {имя}@{версия}")
        else:
            print(f"ПРЕДУПРЕЖДЕНИЕ: зеркало {имя}@{версия}: {пояснение}", file=sys.stderr)
    if not а.сухой:
        with open(os.path.join(а.папка, ФАЙЛ_ЗЕРКАЛА), "w", encoding="utf-8") as f:
            json.dump(итог, f, ensure_ascii=False, indent=2)
    return 0


def _состояние_зеркала(папка, имя, версия):
    try:
        with open(os.path.join(папка, ФАЙЛ_ЗЕРКАЛА), encoding="utf-8") as f:
            данные = json.load(f)
    except (OSError, ValueError):
        return "не проверялось"
    запись = данные.get(имя) if isinstance(данные, dict) else None
    if not isinstance(запись, dict) or запись.get("версия") != версия:
        return "не проверялось"
    if запись.get("состояние") == "есть":
        return "синхронизировано"
    return "не синхронизировано: " + (запись.get("пояснение") or запись.get("состояние") or "?")


def команда_сводка(а):
    пакеты = разобрать_порядок(а.папка)
    строки = ["## Сухой прогон: было бы опубликовано" if а.сухой else "## Выпуск"]
    if not пакеты:
        строки += ["", "Новых версий нет."]
    else:
        строки += ["", "| Пакет | Версия | Зеркало npmmirror |", "|---|---|---|"]
        for имя, версия, _ in пакеты:
            ссылка = f"https://www.npmjs.com/package/{имя}/v/{версия}"
            строки.append(f"| [{имя}]({ссылка}) | {версия} | {_состояние_зеркала(а.папка, имя, версия)} |")
    тег = f"v{а.витрина}"
    строки += ["", f"Тег витрины: [{тег}]({РЕПО_ВИТРИНЫ}/releases/tag/{тег})"]
    print("\n".join(строки))
    return 0


def построить_разбор():
    р = argparse.ArgumentParser(description=__doc__)
    п = р.add_subparsers(dest="команда", required=True)

    def общие(x, ожидание=False):
        x.add_argument("папка")
        x.add_argument("--сухой", action="store_true")
        if ожидание:
            x.add_argument("--таймаут", type=float, default=900)
            x.add_argument("--шаг", type=float, default=15)

    общие(п.add_parser("опубликовать"))
    общие(п.add_parser("ждать"), True)
    общие(п.add_parser("зеркало"), True)
    с = п.add_parser("сводка")
    общие(с)
    с.add_argument("--витрина", required=True)
    return р


КОМАНДЫ = {"опубликовать": команда_опубликовать, "ждать": команда_ждать,
           "зеркало": команда_зеркало, "сводка": команда_сводка}


def main(argv=None):
    а = построить_разбор().parse_args(argv)
    try:
        return КОМАНДЫ[а.команда](а)
    except Ошибка as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
