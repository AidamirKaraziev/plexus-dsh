#!/usr/bin/env python3
"""Прогонщик эталона продукта: задача → worktree со снимка → DSH headless с паузами → проверка → строка в results.jsonl.

    python3 tools/bench.py run --tasks t1[,t2…] --profile base|warm|e04|e05|market [--transport acp|proc] [--pause 7m×2] [--handoff] [--dry-run] [--keep]
    python3 tools/bench.py compare --tasks t1[,t2…] --profiles base,warm

Ход 1 — задание из yml; дальше между ходами пауза (имитация человека, по умолчанию 2×7 мин), ход
продолжается в той же сессии (`--session-id`). У большой задачи на границе этапа проверяется этап
и открывается новая сессия (как `/end` → `/start`) с заданием следующего этапа в том же каталоге.
Итог задачи: `bench/check.sh <задача> --dir <worktree>` (все этапы); $ — из журналов сессий DSH
(~/.dsh/sessions/*/<id>/session*.jsonl.zstd), сумма по сессиям. Ключ OPENCODE_API_KEY читается
из ~/.dsh/.credentials.yaml и уходит только в env процесса DSH; нигде не печатается и не пишется.

--handoff — ходы 2–3 идут новой сессией того же профиля и транспорта: первая реплика = выжимка хода 1
(tools/handoff.py: из журнала, по шаблону итога /end Плексуса — что сделано, файлы, что проверить) + текст
хода; паузы те же. Только для задач без этапов. Строка результата: handoff: true, id всех сессий, $ и токены суммой.

--profile base — DSH без пакетов продукта (профиль bench-base, уже готовый не пересоздаётся).
--profile warm — профиль bench-warm: bench-base + meter + warm (tools/install.sh --from bench-base).
--profile e04 — профиль bench-e04: bench-base + meter + warm + read-cap + порог компакции
(COMPACTION["e04"] → блок `- id: compaction-basic` в cordis.patch.yml профиля).
--profile e05 — профиль bench-e05: e04 + scout (инструмент scout, ребёнок на glm-5.3-flash; E05-S03).

--profile market — профиль bench-market: пакеты продукта (@plexus-dsh/bundle, @plexus-dsh/plexus) установлены из индекса маркета, а не install.sh;
создаётся и пересоздаётся только tools/проверка_маркета.py (здесь профиль берётся готовый).

--transport acp (по умолчанию для base и warm, чтобы они шли одинаково): один процесс DSH на сессию
этапа, ходы и паузы внутри него (ACP, JSON-RPC по stdio; профиль <dsh>-acp = тот же профиль с бандлом
dsh-acp-app вместо dsh-headless). Нужен для warm: таймер пинга живёт в процессе DSH, а headless
между ходами не живёт. --transport proc — прежнее поведение (процесс на ход; так снята база t1).

compare — таблица по строкам results.jsonl: успех, $ (с пингами из журналов учёта: новый ~/.<имя>/usage.jsonl и старый ~/.<старый каталог>/usage.jsonl), промахи
после пауз (tools/digest.py --json), $ на успешный этап.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import queue
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
sys.path.insert(0, str(BENCH / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff as hof  # noqa: E402
import check as chk  # noqa: E402  (загрузить, git)
from dsh_digest import PRICES, NAME, пакет  # noqa: E402  $ за 1M; имя продукта и пакетов

DSH_HOME = Path(os.path.expanduser("~/.dsh/profiles"))
# профиль эталона → профиль DSH и пакеты продукта (None — чистый DSH, без пакетов)
PROFILES = {"base": {"dsh": "bench-base", "packages": None},
            "warm": {"dsh": "bench-warm", "packages": " ".join(map(пакет, ("meter", "warm")))},
            # E04 = warm + read-cap + compact: пакеты продукта и конфиг компакции DSH (E04-S02)
            "e04": {"dsh": "bench-e04", "packages": " ".join(map(пакет, ("meter", "warm", "read-cap", "budget"))),
                    "compaction": "e04", "budget": "e04"},
            # E05 = e04 + scout (разведчик на glm-5.3-flash)
            "e05": {"dsh": "bench-e05", "packages": " ".join(map(пакет, ("meter", "warm", "read-cap", "scout", "budget"))),
                    "compaction": "e04", "budget": "e04"},
            # market = пакеты из индекса маркета (tools/проверка_маркета.py создаёт профиль заново)
            "market": {"dsh": "bench-market", "packages": None, "готовый": True}}
# Порог компакции dsh-compaction-basic (DSH 0.2.2-rc.5). Порог задаётся только долей окна:
# trigger = floor(min(W × thresholdRatio, W − O − headroomTokens)); у glm-5.3 W = 1M, O = 131K, поэтому
# по умолчанию (0.8) порог 800K и база t1 до 171K не компактится. Доля считается от окна 1M (CTX_WINDOW):
# 0.09 → 90K (E04-S04: t1 дороже базы на 16 %, перечитывание файлов после 2 компакций), 0.2 → 200K (не сработала: ctx 158K, t1 $4,56, кэш-чтение растёт; оставлен 90K). retainTokens — хвост
# дословно, maxTokens — потолок выжимки (вместе с рассуждением). tool-result-pruner (8192/4096/1024
# символов) уже в dsh-base и режет выводы только при срабатывании порога — не трогаем.
COMPACTION = {"e04": {"thresholdRatio": 0.09, "retainTokens": 12000, "maxTokens": 16384}}
# Стоп-кран budget (E08-S04). budgetUsd ≈ 1,5 × $ базы задачи: база t1 = $4,68 → 7. Бюджет задачи —
# поле `бюджет_usd` в bench/задачи/<id>.yml, нет поля — BUDGET_ЗАПАС (для t2 базы ещё нет).
# noProgressSteps 0 (выкл.): с 8 стоп no_progress срезал t1 на шаге 10 — чтения прогрессом не считаются,
# а разведка t1 в базе длится десятки шагов; ложный стоп (E04-S04, $0,11, 3 хода впустую). Лимит держит budgetUsd.
# windowPct 200 (по сути выкл.): window_pct meter — окно Go целиком, с параллельными сессиями (прогон базы,
# работа руководителя; уже 75 %), а не расход замера; стоп по окну дал бы ложное срабатывание. Замер
# идёт одной задачей на окно, лимит держит budgetUsd; реальный предел Go даст ошибку провайдера сам.
BUDGET_ЗАПАС = 7.0
BUDGET = {"e04": {"warnPct": 80, "windowPct": 200, "noProgressSteps": 0}}
CTX_WINDOW = 1_000_000  # окно glm-5.3 (pi-ai opencode-go.json) — только для печати порога в токенах
COMPACT_MARK = ("# >>> bench compaction (tools/bench.py)", "# <<< bench compaction")
DSH_VER = "@deepseek-ai/dsh@0.2.2-rc.5"
# маркеры, с которых в cordis.patch.yml headless начинаются вставки продукта (пакеты, скиллы Плексуса)
PRODUCT_MARK = ("# Plexus DSH E06", f"# >>> {NAME} packages")
ACP_TAIL = ("- id: acp\n  config:\n    provider: opencode-go\n    model: glm-5.3\n")


def профиль_dsh(имя: str, бюджет: float | None = None) -> str:
    """Профиль DSH для эталона. base: `bench-base` — копия headless без вставок продукта (только провайдер
    и модель); уже есть — не трогаем. warm: `bench-warm` ставит tools/install.sh из bench-base."""
    dst = DSH_HOME / PROFILES[имя]["dsh"]
    if PROFILES[имя].get("готовый"):
        if not (dst / "cordis.patch.yml").exists():
            raise SystemExit(f"✘ профиля {dst.name} нет: создайте его python3 tools/проверка_маркета.py")
        return dst.name
    пакеты = PROFILES[имя]["packages"]
    if пакеты:
        профиль_dsh("base")
        r = subprocess.run([str(ROOT / "tools" / "install.sh"), "--profiles", dst.name, "--from", "bench-base",
                            "--packages", пакеты], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"✘ install.sh для {dst.name}: {(r.stderr or r.stdout)[-400:]}")
        if PROFILES[имя].get("compaction"):
            патч_компакции(dst / "cordis.patch.yml", COMPACTION[PROFILES[имя]["compaction"]])
        if PROFILES[имя].get("budget"):
            патч_бюджета(dst / "cordis.patch.yml", dst.name, BUDGET[PROFILES[имя]["budget"]],
                         BUDGET_ЗАПАС if бюджет is None else бюджет)
        return dst.name
    if dst.exists():
        return dst.name
    src = DSH_HOME / "headless"
    dst.mkdir(exist_ok=True)
    for f in ("package.json", "pnpm-workspace.yaml", "cordis.yml"):
        shutil.copy(src / f, dst / f)
    строки = []
    for line in (src / "cordis.patch.yml").read_text(encoding="utf-8").splitlines():
        if line.startswith(PRODUCT_MARK):
            break
        строки.append(line)
    (dst / "cordis.patch.yml").write_text("\n".join(строки) + "\n", encoding="utf-8")
    return dst.name


def патч_компакции(патч: Path, cfg: dict) -> None:
    """Блок `- id: compaction-basic` с config в конце патча профиля (install.sh только что скопировал патч
    из bench-base, так что блок пишется заново). Патч по id сливается с записью из бандла dsh-base."""
    текст = патч.read_text(encoding="utf-8")
    if COMPACT_MARK[0] in текст:
        текст = текст[:текст.index(COMPACT_MARK[0])]
    блок = f"{COMPACT_MARK[0]}\n- id: compaction-basic\n  config: {json.dumps(cfg)}\n{COMPACT_MARK[1]}\n"
    патч.write_text(текст.rstrip("\n") + "\n\n" + блок, encoding="utf-8")


def патч_бюджета(патч: Path, профиль: str, cfg: dict, бюджет: float) -> None:
    """config записи budget в блоке пакетов патча (install.sh поставил значения по умолчанию)."""
    конфиг = {"profile": профиль, "budgetUsd": бюджет, **cfg}
    строки = патч.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(строки):
        if l.strip() == f"- id: {пакет('budget')}":
            for j in range(i + 1, len(строки)):
                if строки[j].strip().startswith("config:"):
                    строки[j] = f"      config: {json.dumps(конфиг)}"
                    патч.write_text("\n".join(строки) + "\n", encoding="utf-8")
                    return
    raise SystemExit(f"✘ в {патч} нет записи {пакет('budget')}")


def порог_строка(cfg: dict) -> str:
    return (f"компакция: порог {int(CTX_WINDOW * cfg['thresholdRatio'])} ток. (thresholdRatio {cfg['thresholdRatio']}"
            f" × окно glm-5.3), хвост {cfg.get('retainTokens', '16 %')}, выжимка ≤ {cfg.get('maxTokens', 65536)}")


def профиль_acp(dsh_проф: str) -> str:
    """`<профиль>-acp`: копия профиля, где бандл dsh-headless заменён на dsh-acp-app, модель сессии — glm-5.3
    через OpenCode Go. Исходный профиль не меняется."""
    src, dst = DSH_HOME / dsh_проф, DSH_HOME / f"{dsh_проф}-acp"
    dst.mkdir(exist_ok=True)
    for f in ("pnpm-workspace.yaml", "cordis.yml"):
        shutil.copy(src / f, dst / f)
    pj = (src / "package.json").read_text(encoding="utf-8").replace("dsh-headless", "dsh-acp-app")
    (dst / "package.json").write_text(pj, encoding="utf-8")
    патч = (src / "cordis.patch.yml").read_text(encoding="utf-8")
    # пакеты продукта хранят в config имя профиля — переписать на ACP-профиль (журнал usage.jsonl)
    патч = patch_name(патч, dsh_проф, dst.name)
    (dst / "cordis.patch.yml").write_text(патч.rstrip("\n") + "\n" + ACP_TAIL, encoding="utf-8")
    # пакеты, поставленные через `dsh plugin add` (профиль market), живут в node_modules профиля
    if (src / "node_modules").is_dir():
        shutil.rmtree(dst / "node_modules", ignore_errors=True)
        shutil.copytree(src / "node_modules", dst / "node_modules", symlinks=True)
    return dst.name


def patch_name(текст: str, старое: str, новое: str) -> str:
    return текст.replace(f'"profile": "{старое}"', f'"profile": "{новое}"')


TURN_TIMEOUT = 40 * 60
CONTINUE = ("Продолжай. Проверь свою работу против задания: запусти указанную в нём проверку, "
            "исправь, что не сходится. Если всё готово — коротко скажи «готово».")
STAGE2 = ("Этап {n}. Предыдущий этап уже сделан в этом каталоге (новая сессия, контекста не помнишь — "
          "посмотри состояние репо и код).\n\n{текст}")


def ключ() -> str:
    p = Path(os.path.expanduser("~/.dsh/.credentials.yaml"))
    return yaml.safe_load(p.read_text())["refs"]["OPENCODE_API_KEY"]


def разобрать_паузу(s: str) -> list[float]:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*(?:[×xX*]\s*(\d+))?\s*", s)
    if not m:
        raise SystemExit(f"✘ пауза «{s}»: ждал вида 7m×2, 30s, 1m")
    сек = float(m[1]) * {"s": 1, "m": 60, "h": 3600, "": 60}[m[2]]
    return [сек] * int(m[3] or 1)


def журнал(sid: str) -> Path | None:
    fs = glob.glob(os.path.expanduser(f"~/.dsh/sessions/*/{sid}/session*.jsonl.zstd"))
    return Path(max(fs, key=os.path.getmtime)) if fs else None


def стоимость(sid: str) -> dict:
    """Токены и $ сессии из журнала DSH (assistant/message с usage)."""
    f = журнал(sid)
    if not f:
        return {"вход": 0, "кэш": 0, "выход": 0, "usd": 0.0, "журнал": False}
    raw = subprocess.run(["zstd", "-dc", str(f)], capture_output=True, check=True).stdout
    i = r = w = o = 0
    usd = 0.0
    for line in raw.decode().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("type") != "assistant/message":
            continue
        d = e["data"]
        u = d.get("usage") or {}
        model = ((d["message"].get("source") or {}).get("model")) or "glm-5.3"
        pi, pr, pw, po = PRICES.get(model, PRICES["glm-5.3"])
        a, b, c, x = (u.get(k, 0) for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens"))
        i, r, w, o = i + a, r + b, w + c, o + x
        usd += (a * pi + b * pr + c * pw + x * po) / 1e6
    return {"вход": i + w, "кэш": r, "выход": o, "usd": usd, "журнал": True}


class Acp:
    """Клиент ACP (JSON-RPC по stdio, строки NDJSON) к одному процессу DSH: сессия, ходы, закрытие.
    Процесс живёт всю сессию этапа — паузы идут внутри него, таймер пинга warm тикает."""

    def __init__(self, каталог: Path, env: dict, dsh_профиль: str):
        self.p = subprocess.Popen(["npx", "-y", DSH_VER, "--profile", dsh_профиль], cwd=каталог, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, bufsize=1)
        self.n = 0
        self.ответ: list[str] = []
        self.err: list[str] = []
        threading.Thread(target=lambda: self.err.extend(self.p.stderr), daemon=True).start()
        self.q: "queue.Queue[dict | None]" = queue.Queue()
        threading.Thread(target=self._читать, daemon=True).start()
        self.вызов("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, 120)
        self.sid = self.вызов("session/new", {"cwd": str(каталог), "mcpServers": []}, 120)["sessionId"]

    def _читать(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass
        self.q.put(None)

    def _слать(self, o: dict):
        self.p.stdin.write(json.dumps(o) + "\n")
        self.p.stdin.flush()

    def вызов(self, метод: str, params: dict, timeout: float) -> dict:
        self.n += 1
        mid = self.n
        self._слать({"jsonrpc": "2.0", "id": mid, "method": метод, "params": params})
        конец = time.time() + timeout
        while True:
            try:
                m = self.q.get(timeout=max(0.1, конец - time.time()))
            except queue.Empty:
                raise TimeoutError(f"ACP {метод}: нет ответа за {int(timeout)} с")
            if m is None:
                raise RuntimeError(f"ACP: процесс DSH закрылся ({''.join(self.err)[-300:].strip()})")
            if m.get("method") == "session/request_permission":   # авто-allow, как headless без вопросов
                оп = (m.get("params") or {}).get("options") or []
                выбор = next((o for o in оп if str(o.get("kind", "")).startswith("allow")), оп[0] if оп else {})
                self._слать({"jsonrpc": "2.0", "id": m["id"],
                             "result": {"outcome": {"outcome": "selected", "optionId": выбор.get("optionId")}}})
            elif m.get("method") == "session/update":
                u = (m.get("params") or {}).get("update") or {}
                if u.get("sessionUpdate") == "agent_message_chunk" and (u.get("content") or {}).get("type") == "text":
                    self.ответ.append(u["content"].get("text", ""))
            elif m.get("id") == mid and "method" not in m:
                if "error" in m:
                    raise RuntimeError(f"ACP {метод}: {m['error']}")
                return m.get("result") or {}
            elif "id" in m and "method" in m:   # чужой запрос агента — ответить «не поддерживается»
                self._слать({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "unsupported"}})

    def ход(self, текст: str) -> tuple[str, str]:
        self.ответ = []
        r = self.вызов("session/prompt", {"sessionId": self.sid, "prompt": [{"type": "text", "text": текст}]}, TURN_TIMEOUT)
        return r.get("stopReason", ""), "".join(self.ответ)

    def закрыть(self):
        try:
            self.вызов("session/close", {"sessionId": self.sid}, 60)
        except Exception:
            pass
        try:
            self.p.stdin.close()
            self.p.wait(timeout=30)
        except Exception:
            self.p.kill()


def ход(каталог: Path, текст: str, env: dict, sid: str | None, dsh_профиль: str, extra: list[str]) -> tuple[str | None, int, str]:
    cmd = ["npx", "-y", DSH_VER, "--profile", dsh_профиль, "--json"] + extra + (["--session-id", sid] if sid else []) + [текст]
    p = subprocess.run(cmd, cwd=каталог, env=env, capture_output=True, text=True, timeout=TURN_TIMEOUT)
    новый, ответ = sid, ""
    for line in p.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "session":
            новый = e["sessionId"]
        elif e.get("type") == "final":
            ответ = e.get("text") or ""
    if p.returncode:
        print(f"  ! DSH код {p.returncode}: {p.stderr.strip()[-300:]}", file=sys.stderr)
    return новый, p.returncode, ответ


def план(t: dict, пауза: list[float], handoff: bool = False) -> list[dict]:
    """Сессии прогона: у каждой — тексты ходов (первый — задание); паузы между ходами одни и те же.
    handoff: ход 1 — сессия 1; ходы 2–3 — сессия 2, где первый ход (None) заполняется выжимкой по журналу
    сессии 1 в момент старта; пауза перед ним — первая из `пауза`, у сессии 1 «перед» = 0."""
    if handoff:
        if t.get("этапы"):
            raise SystemExit("✘ --handoff только для задач без этапов (у этапных граница уже есть)")
        if not пауза:
            raise SystemExit("✘ --handoff: нужен хотя бы один ход после хода 1 (пауза)")
        return [{"имя": t["id"] + " · ход 1", "ходы": [t["задание"].strip()], "паузы": [0]},
                {"имя": t["id"] + " · ходы 2–" + str(len(пауза) + 1), "ходы": [None] + [CONTINUE] * (len(пауза) - 1),
                 "паузы": list(пауза), "выжимка": True}]
    if t.get("этапы"):
        res = []
        for n, э in enumerate(t["этапы"], 1):
            txt = э["задание"].strip()
            res.append({"имя": э.get("имя", f"этап {n}"),
                        "ходы": [txt if n == 1 else STAGE2.format(n=n, текст=txt)] + [CONTINUE] * len(пауза)})
        return res
    return [{"имя": t["id"], "ходы": [t["задание"].strip()] + [CONTINUE] * len(пауза)}]


def паузы_сессии(э: dict, пауза: list[float]) -> list[float]:
    """Пауза перед каждым ходом сессии (у первого хода без handoff её нет)."""
    return э.get("паузы") or [0] + пауза[:len(э["ходы"]) - 1]


def текст_хода(э: dict, k: int, sid_пред: str | None) -> str:
    """Текст хода k сессии; первый ход сессии с выжимкой = выжимка журнала предыдущей сессии + CONTINUE."""
    текст = э["ходы"][k]
    if текст is None:
        f = журнал(sid_пред) if sid_пред else None
        вж = hof.выжимка_сессии(f) if f else hof.ШАБЛОН.format(сделано="журнал сессии не найден", файлы="—", проверить="запусти проверку из задания")
        return hof.реплика_хода2(вж, CONTINUE)
    return текст


def собрать_строку(t: dict, профиль: str, сессии: list[str], ц: list[dict], ok: bool, этапы_ok: list[bool],
                   пауза: list[float], транспорт: str, handoff: bool, длит: int) -> dict:
    """Строка results.jsonl: $ и токены — суммой по сессиям; handoff: true — все сессии в `сессии`."""
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "задача": t["id"], "профиль": профиль, "сессии": сессии,
        "usd": round(sum(x["usd"] for x in ц), 4),
        "токены": {k: sum(x[k] for x in ц) for k in ("вход", "кэш", "выход")},
        "успех": ok, "этапы_успех": этапы_ok,
        "длительность_с": длит, "паузы": [int(p) for p in пауза], "транспорт": транспорт,
        **({"compact_k": int(CTX_WINDOW * COMPACTION[PROFILES[профиль]["compaction"]]["thresholdRatio"] / 1000)}
           if PROFILES[профиль].get("compaction") else {}),
        **({"handoff": True} if handoff else {}),
        **({} if all(x["журнал"] for x in ц) else {"предупреждение": "нет журнала сессии, $ неполный"}),
    }


def прогон(t: dict, профиль: str, пауза: list[float], keep: bool, dry: bool, транспорт: str = "proc",
           handoff: bool = False) -> dict:
    сессии_план = план(t, пауза, handoff)
    if dry:
        dp = PROFILES[профиль]["dsh"]
        print(f"[dry] {t['id']} · профиль {профиль} → DSH {dp}"
              + (f" (ACP: {dp}-acp)" if транспорт == "acp" else "")
              + f" · транспорт {транспорт} · пакеты {PROFILES[профиль]['packages'] or '—'} · сессий {len(сессии_план)} · паузы {[int(p) for p in пауза]} с")
        if PROFILES[профиль].get("budget"):
            print(f"  бюджет: budgetUsd {t.get('бюджет_usd', BUDGET_ЗАПАС)}"
                  + ("" if "бюджет_usd" in t else " (запасное)") + f", {BUDGET[PROFILES[профиль]['budget']]}")
        if PROFILES[профиль].get("compaction"):
            print("  " + порог_строка(COMPACTION[PROFILES[профиль]["compaction"]]))
        for n, э in enumerate(сессии_план, 1):
            print(f"  сессия {n}: {э['имя']} · ходов {len(э['ходы'])}, паузы перед ходами {[int(p) for p in паузы_сессии(э, пауза)]} с")
            if э.get("выжимка"):
                print("    первая реплика = выжимка хода 1 (из журнала сессии 1, шаблон итога /end) + текст хода:")
                print("      " + hof.реплика_хода2(hof.ШАБЛОН.format(сделано="<последний текст ассистента хода 1>", файлы="<write/edit из журнала>", проверить="<команды проверки из журнала>"), CONTINUE[:60] + "…").replace("\n", "\n      "))
            else:
                print(f"    задание: {э['ходы'][0][:90].replace(chr(10), ' ')}…")
            if n < len(сессии_план) and not handoff:
                print(f"  граница: check.sh {t['id']} --этап {n} --dir <worktree> → новая сессия в том же каталоге")
        print("  итог: check.sh <задача> --dir <worktree> (все этапы) → строка в bench/results.jsonl")
        return {}
    repo = Path(os.path.expanduser(t["репо_путь"]))
    tmp = Path(tempfile.mkdtemp(prefix="bench-run-"))
    wt = tmp / "wt"
    r = chk.git(repo, "worktree", "add", "--detach", str(wt), str(t["снимок"]))
    if r.returncode:
        raise SystemExit(f"✘ worktree на {t['снимок']}: {r.stderr}")
    env = dict(os.environ, OPENCODE_API_KEY=ключ(), GOPROXY="off", GOFLAGS="-mod=readonly",
               GOTOOLCHAIN="local", GOWORK="off")
    dsh_проф = профиль_dsh(профиль, t.get("бюджет_usd"))
    if транспорт == "acp":
        dsh_проф = профиль_acp(dsh_проф)
    сессии: list[str] = []
    этапы_ok: list[bool] = []
    t0 = time.time()
    ok = False
    try:
        for n, э in enumerate(сессии_план, 1):
            sid = None
            acp = Acp(wt, env, dsh_проф) if транспорт == "acp" else None
            if acp:
                sid = acp.sid
                сессии.append(sid)
            пз = паузы_сессии(э, пауза)
            for k in range(len(э["ходы"])):
                if пз[k]:
                    print(f"  пауза {int(пз[k])} с…", flush=True)
                    time.sleep(пз[k])
                текст = текст_хода(э, k, сессии[-1] if n > 1 and сессии else None)
                if acp:
                    try:
                        стоп, ответ = acp.ход(текст)
                        rc = 0 if стоп in ("end_turn", "") else 1
                    except Exception as e:  # noqa: BLE001
                        print(f"  ! ACP: {e}", file=sys.stderr)
                        rc, ответ = 1, ""
                else:
                    sid, rc, ответ = ход(wt, текст, env, sid, dsh_проф, [])
                if sid and sid not in сессии:
                    сессии.append(sid)
                print(f"  сессия {n} ход {k + 1}: код {rc} · {ответ[:80]!r}", flush=True)
                if rc or not sid:
                    break
            if acp:
                acp.закрыть()
            if n < len(сессии_план) and not handoff:
                c = subprocess.run([str(BENCH / "check.sh"), t["id"], "--этап", str(n), "--dir", str(wt)],
                                   capture_output=True, text=True)
                этапы_ok.append(c.returncode == 0)
                print(f"  граница: этап {n} {'зелёный' if c.returncode == 0 else 'красный'}", flush=True)
        c = subprocess.run([str(BENCH / "check.sh"), t["id"], "--dir", str(wt)], capture_output=True, text=True)
        ok = c.returncode == 0
        этапы_ok.append(ok)
        print(c.stdout[-600:] + (c.stderr[-400:] if not ok else ""), flush=True)
    finally:
        if keep:
            print(f"  worktree оставлен: {wt}")
        else:
            chk.git(repo, "worktree", "remove", "--force", str(wt))
            shutil.rmtree(tmp, ignore_errors=True)
    ц = [стоимость(s) for s in сессии]
    return собрать_строку(t, профиль, сессии, ц, ok, этапы_ok, пауза, транспорт, handoff, round(time.time() - t0))


# Журнал учёта продукта.
USAGE_FILES = [Path(os.path.expanduser(f"~/.{NAME}/usage.jsonl"))]


def пинги(сессии: list[str]) -> tuple[float, int]:
    """$ и число пингов warm по id сессий: строки `kind: ping` в журнале продукта.
    Повторённая строка (одна и та же запись дважды) считается один раз."""
    usd, n = 0.0, 0
    seen: set[str] = set()
    for path in USAGE_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") != "ping" or e.get("session") not in сессии:
                continue
            key = json.dumps(e, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            n += 1
            usd += next((float(e[k]) for k in ("usd", "cost_usd", "ping_usd", "session_usd") if isinstance(e.get(k), (int, float))), 0.0)
    return usd, n


def промахи(сессии: list[str]) -> int | None:
    """Промахи кэша после пауз по сессиям строки (tools/digest.py --json → misses.after_pause)."""
    всего = 0
    for sid in сессии:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "digest.py"), sid, "--json"], capture_output=True, text=True)
        if r.returncode:
            return None
        всего += json.loads(r.stdout)["misses"]["after_pause"]
    return всего


def сравнить(задачи: list[str], профили: list[str], файл: str) -> None:
    rows = []
    if Path(файл).exists():
        for line in Path(файл).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    # отладочные строки в сравнение не берём; из прогонов задачи/профиля — последний на каждый транспорт
    рабочие = [r for r in rows if not str(r.get("примечание", "")).startswith("отладочный")]
    шапка = ["задача", "профиль", "транспорт", "порог", "успех", "$ сессий", "$ пинги", "$ всего", "промахи после пауз", "$ на успешный этап"]
    таблица = [шапка]
    for имя in задачи:
        for проф in профили:
            найд = [r for r in рабочие if r.get("профиль") == проф
                    and (r["задача"] == имя or r["задача"].startswith(имя + "-"))]
            if not найд:
                таблица.append([имя, проф] + ["—"] * 8)
                continue
            # по одному последнему прогону на транспорт и порог компакции (compact_k, тыс. ток.;
            # у старых строк e04 поля нет — тогда порог был 90K): proc/acp и 90K/200K идут отдельными строками
            послед = {}
            for r in найд:
                ck = r.get("compact_k", 90 if PROFILES.get(проф, {}).get("compaction") else None)
                послед[(r.get("транспорт", "proc"), ck)] = r
            for (_, ck), r in послед.items():
                пд, пн = пинги(r["сессии"])
                всего = r["usd"] + пд
                м = промахи(r["сессии"])
                ок = sum(1 for x in r.get("этапы_успех", []) if x)
                таблица.append([имя, проф, r.get("транспорт", "proc"),
                                f"{ck}K" if ck else "—", "да" if r["успех"] else "нет", f"{r['usd']:.2f}",
                                f"{пд:.3f} ({пн})" if пн else "—", f"{всего:.2f}",
                                "—" if м is None else str(м), f"{всего / ок:.2f}" if ок else "—"])
    w = [max(len(str(x[i])) for x in таблица) for i in range(len(шапка))]
    for k, x in enumerate(таблица):
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(x)))
        if k == 0:
            print("  ".join("-" * n for n in w))


def main() -> int:
    ap = argparse.ArgumentParser(description="прогонщик эталона продукта")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--tasks", required=True, help="t1,t2")
    c.add_argument("--profiles", default="base,warm")
    c.add_argument("--file", default=str(BENCH / "results.jsonl"))
    r = sub.add_parser("run")
    r.add_argument("--tasks", required=True, help="t1,t2 или полные id")
    r.add_argument("--profile", default="base", choices=sorted(PROFILES))
    r.add_argument("--transport", choices=["acp", "proc"], default=None,
                   help="acp — один процесс DSH на сессию этапа (по умолчанию); proc — процесс на ход")
    r.add_argument("--pause", default="7m×2", help="пауза и их число, напр. 7m×2")
    r.add_argument("--handoff", action="store_true",
                   help="ходы 2–3 — новая сессия с выжимкой хода 1 (шаблон итога /end Плексуса)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--keep", action="store_true", help="не убирать worktree")
    r.add_argument("--out", default=str(BENCH / "results.jsonl"))
    a = ap.parse_args()
    if a.cmd == "compare":
        сравнить([x.strip() for x in a.tasks.split(",")], [x.strip() for x in a.profiles.split(",")], a.file)
        return 0
    транспорт = a.transport or "acp"
    пауза = разобрать_паузу(a.pause)
    файлы = sorted((BENCH / "задачи").glob("*.yml"))
    for имя in a.tasks.split(","):
        имя = имя.strip()
        найдено = [f for f in файлы if f.stem == имя or f.stem.startswith(имя + "-")]
        if len(найдено) != 1:
            raise SystemExit(f"✘ задача «{имя}»: найдено {len(найдено)}")
        t = chk.загрузить(найдено[0].stem)
        строка = прогон(t, a.profile, пауза, a.keep, a.dry_run, транспорт, a.handoff)
        if строка:
            with open(a.out, "a", encoding="utf-8") as f:
                f.write(json.dumps(строка, ensure_ascii=False) + "\n")
            print(json.dumps(строка, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
