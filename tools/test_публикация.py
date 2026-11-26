"""Тесты tools/опубликовать.sh: режим проверки, сторож по tarball'ам, порядок публикации.

Настоящий pnpm и npm registry не нужны: в PATH подложен поддельный pnpm,
который умеет pack (tar с заменой workspace:*) и пишет вызовы publish в журнал.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parent.parent
USERS = "/Use" + "rs/x"
ВЕРСИИ = {"meter": "0.1.1", "warm": "0.1.1", "read-cap": "0.1.1", "scout": "0.2.2", "bundle": "0.1.2"}
РЕПО = {"type": "git", "url": "git+https://github.com/AidamirKaraziev/plexus-dsh.git"}

ПОДДЕЛЬНЫЙ_PNPM = r'''#!/usr/bin/env python3
import json, os, sys, tarfile, tempfile
a = sys.argv[1:]
if a[:1] == ["-C"]:
    a = a[2:]
if a[:1] != ["pack"] and a[:1] != ["publish"]:
    sys.exit(0)
if a[0] == "publish":
    with open(os.environ["ФЕЙК_ЖУРНАЛ"], "a") as f:
        f.write(os.path.basename(os.getcwd()) + "\n")
    sys.exit(0)
куда = a[a.index("--pack-destination") + 1]
m = json.load(open("package.json"))
версии = json.load(open(os.path.join(os.path.dirname(os.getcwd()), "версии.json")))
for секция in ("dependencies",):
    for д, в in m.get(секция, {}).items():
        if в.startswith("workspace:"):
            m[секция][д] = версии[д]
имя = m["name"].lstrip("@").replace("/", "-")
with tempfile.TemporaryDirectory() as t:
    json.dump(m, open(os.path.join(t, "package.json"), "w"))
    with tarfile.open(os.path.join(куда, f"{имя}-{m['version']}.tgz"), "w:gz") as tar:
        tar.add(os.path.join(t, "package.json"), "package/package.json")
        for корень, _, файлы in os.walk("."):
            for ф in файлы:
                п = os.path.join(корень, ф)
                if os.path.normpath(п) != "package.json":
                    tar.add(п, "package/" + os.path.normpath(п))
'''


ПОДДЕЛЬНЫЙ_NPM = r'''#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
if a[:1] == ["view"] and a[1] in os.environ.get("ФЕЙК_В_NPM", "").split():
    print(a[1].rsplit("@", 1)[1])
'''


class Мир:
    def __init__(self, tmp_path):
        self.корень = tmp_path / "мастерская"
        (self.корень / "tools").mkdir(parents=True)
        shutil.copy(КОРЕНЬ / "tools" / "опубликовать.sh", self.корень / "tools" / "опубликовать.sh")
        shutil.copy(КОРЕНЬ / "tools" / "сторож.py", self.корень / "tools" / "сторож.py")
        self.пакеты = self.корень / "packages"
        версии = {"@plexus-dsh/" + п: в for п, в in ВЕРСИИ.items()}
        self.пакеты.mkdir()
        (self.пакеты / "версии.json").write_text(json.dumps(версии))
        for п, в in ВЕРСИИ.items():
            d = self.пакеты / f"plexus-dsh-{п}"
            d.mkdir()
            m = {"name": "@plexus-dsh/" + п, "version": в, "license": "MIT",
                 "publishConfig": {"access": "public"},
                 "repository": dict(РЕПО, directory=f"packages/plexus-dsh-{п}")}
            if п == "bundle":
                внутр = ["@plexus-dsh/" + x for x in ВЕРСИИ if x != "bundle"]
                m["dependencies"] = {x: "workspace:*" for x in внутр}
                m["bundledDependencies"] = внутр
                for x in внутр:
                    nm = d / "node_modules" / x
                    nm.mkdir(parents=True)
                    (nm / "package.json").write_text(json.dumps({"name": x, "repository": РЕПО}))
            (d / "package.json").write_text(json.dumps(m))
            (d / "README.md").write_text("# " + п + "\n")
        (self.пакеты / "node_modules").mkdir()
        бин = tmp_path / "бин"
        бин.mkdir()
        pnpm = бин / "pnpm"
        pnpm.write_text(ПОДДЕЛЬНЫЙ_PNPM)
        pnpm.chmod(pnpm.stat().st_mode | stat.S_IEXEC)
        npm = бин / "npm"
        npm.write_text(ПОДДЕЛЬНЫЙ_NPM)
        npm.chmod(npm.stat().st_mode | stat.S_IEXEC)
        self.журнал = tmp_path / "журнал.txt"
        self.env = dict(os.environ, PATH=f"{бин}:{os.environ['PATH']}", ФЕЙК_ЖУРНАЛ=str(self.журнал),
                        PACKAGES_DIR=str(self.пакеты))

    def запуск(self, *флаги):
        return subprocess.run([str(self.корень / "tools" / "опубликовать.sh"), *флаги],
                              env=self.env, capture_output=True, text=True)

    def вызовы_publish(self):
        return self.журнал.read_text().split() if self.журнал.exists() else []


@pytest.fixture
def мир(tmp_path):
    return Мир(tmp_path)


def test_проверка_чистых_пакетов_ничего_не_публикует(мир):
    r = мир.запуск()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pnpm publish" in r.stdout
    assert "--публиковать" in r.stdout
    assert мир.вызовы_publish() == []


def test_утечка_в_readme_пакета_даёт_код_1(мир):
    (мир.пакеты / "plexus-dsh-warm" / "README.md").write_text("# warm\nлежит в %s/проект\n" % USERS)
    r = мир.запуск()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "README.md" in r.stdout
    assert мир.вызовы_publish() == []


def test_workspace_в_tarball_бандла_даёт_код_1(мир):
    # подмена: поддельный pack не заменяет workspace:* у бандла
    m = json.loads((мир.пакеты / "версии.json").read_text())
    (мир.пакеты / "версии.json").write_text(json.dumps({k: "workspace:*" for k in m}))
    r = мир.запуск()
    assert r.returncode == 1
    assert "workspace:" in r.stdout


def test_публикация_в_порядке_зависимостей(мир):
    r = мир.запуск("--публиковать")
    assert r.returncode == 0, r.stdout + r.stderr
    assert мир.вызовы_publish() == ["plexus-dsh-meter", "plexus-dsh-warm", "plexus-dsh-read-cap",
                                    "plexus-dsh-scout", "plexus-dsh-bundle"]


def test_уже_опубликованные_версии_пропускаются(мир):
    мир.env["ФЕЙК_В_NPM"] = "@plexus-dsh/meter@0.1.1 @plexus-dsh/warm@0.1.1 @plexus-dsh/read-cap@0.1.1"
    r = мир.запуск("--публиковать")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "@plexus-dsh/meter@0.1.1 уже в npm" in r.stdout
    assert мир.вызовы_publish() == ["plexus-dsh-scout", "plexus-dsh-bundle"]


def test_всё_уже_в_npm_публиковать_нечего(мир):
    мир.env["ФЕЙК_В_NPM"] = " ".join(f"@plexus-dsh/{п}@{в}" for п, в in ВЕРСИИ.items())
    r = мир.запуск("--публиковать")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "публиковать нечего" in r.stdout
    assert мир.вызовы_publish() == []


def test_публикация_не_идёт_при_утечке(мир):
    (мир.пакеты / "plexus-dsh-scout" / "README.md").write_text(USERS + "\n")
    r = мир.запуск("--публиковать")
    assert r.returncode == 1
    assert мир.вызовы_publish() == []


def test_пакеты_готовы_к_npm():
    for п, в in ВЕРСИИ.items():
        m = json.loads((КОРЕНЬ / "packages" / f"plexus-dsh-{п}" / "package.json").read_text())
        assert "private" not in m
        assert m["version"] == в
        assert m["license"] == "MIT"
        assert m["publishConfig"] == {"access": "public"}
        assert m["repository"]["url"] == РЕПО["url"]
        assert m["repository"]["directory"] == f"packages/plexus-dsh-{п}"
