"""Тесты выпустить.sh и сторожа на временных git-репозиториях."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parent.parent
USERS = "/Use" + "rs/x"
ФАМИЛИЯ = "Kara" + "ziev"
ПОЧТА = "dev.person@example.org"


def run(cmd, cwd, env, check=False):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=check)


class Мир:
    def __init__(self, tmp_path):
        cfg = tmp_path / "gitconfig"
        cfg.write_text("[user]\n\tname = Тест\n\temail = %s\n[init]\n\tdefaultBranch = main\n" % ПОЧТА)
        self.env = dict(os.environ, HOME=str(tmp_path), GIT_CONFIG_GLOBAL=str(cfg), GIT_CONFIG_NOSYSTEM="1")
        м = self.м = tmp_path / "мастерская"
        (м / "tools").mkdir(parents=True)
        shutil.copy(КОРЕНЬ / "выпустить.sh", м / "выпустить.sh")
        shutil.copy(КОРЕНЬ / "tools" / "сторож.py", м / "tools" / "сторож.py")
        (м / "packages/plexus-dsh-bundle").mkdir(parents=True)
        (м / "packages/plexus-dsh-bundle/package.json").write_text('{"version": "0.1.0"}')
        (м / "packages/plexus-dsh-bundle/index.js").write_text("console.log(1)\n")
        (м / "bench").mkdir()
        (м / "bench/a.txt").write_text("a\n")
        (м / "CHANGELOG.md").write_text("## 0.1.0\n")
        (м / "публичное").mkdir()
        (м / "публичное/README.md").write_text("# p\nhttps://github.com/Aidamir%s/p\n" % ФАМИЛИЯ)
        (м / "публичное/LICENSE").write_text("MIT License\nCopyright (c) Aidamir %s\n" % ФАМИЛИЯ)
        for d in ("vault", ".claude"):
            (м / d).mkdir()
            (м / d / "n.md").write_text("секрет\n")
        (м / "instructions.md").write_text("секрет\n")
        run(["git", "init", "-q"], м, self.env, True)
        self.клон = tmp_path / "клон"
        self.клон.mkdir()
        run(["git", "init", "-q"], self.клон, self.env, True)
        self.удалённый = tmp_path / "удалённый.git"
        run(["git", "init", "-q", "--bare", str(self.удалённый)], tmp_path, self.env, True)
        run(["git", "remote", "add", "origin", str(self.удалённый)], self.клон, self.env, True)

    def коммит(self):
        run(["git", "add", "-A"], self.м, self.env, True)
        run(["git", "commit", "-q", "-m", "m"], self.м, self.env, True)

    def выпуск(self, *a):
        return run(["bash", str(self.м / "выпустить.sh"), *a], self.м, self.env)

    def git(self, *a, d=None):
        return run(["git", *a], d or self.клон, self.env)


@pytest.fixture
def мир(tmp_path):
    return Мир(tmp_path)


def test_чистый_выпуск(мир):
    мир.коммит()
    r = мир.выпуск("--в", str(мир.клон))
    assert r.returncode == 0, r.stdout + r.stderr
    assert мир.git("log", "-1", "--format=%s").stdout.strip() == "выпуск 0.1.0"
    assert мир.git("log", "-1", "--format=%ae").stdout.strip() == ПОЧТА
    assert мир.git("tag").stdout.split() == ["v0.1.0"]
    файлы = мир.git("ls-files").stdout.split()
    assert "README.md" in файлы and "LICENSE" in файлы
    assert "packages/plexus-dsh-bundle/index.js" in файлы
    assert not any(f.startswith(("vault", ".claude")) or f == "instructions.md" for f in файлы)
    assert мир.git("for-each-ref", d=мир.удалённый).stdout.strip() == ""


def test_push(мир):
    мир.коммит()
    assert мир.выпуск("--в", str(мир.клон), "--push").returncode == 0
    assert "v0.1.0" in мир.git("tag", d=мир.удалённый).stdout


def test_занятый_тег(мир):
    мир.коммит()
    assert мир.выпуск("--в", str(мир.клон)).returncode == 0
    (мир.м / "bench/a.txt").write_text("b\n")
    мир.коммит()
    r = мир.выпуск("--в", str(мир.клон))
    assert r.returncode == 1 and "v0.1.0" in r.stdout


@pytest.mark.parametrize("текст", [
    "p = '%s/f'\n" % USERS,
    "// %s\n" % ФАМИЛИЯ,
    "// " + ПОЧТА + "\n",
])
def test_утечки(мир, текст):
    (мир.м / "packages/plexus-dsh-bundle/leak.js").write_text(текст)
    мир.коммит()
    r = мир.выпуск("--в", str(мир.клон))
    assert r.returncode == 1
    assert "leak.js:1" in r.stdout and "→" in r.stdout
    assert мир.git("tag").stdout.strip() == ""


def test_личный_список(мир):
    (мир.м / ".claude/сторож.txt").write_text("\nсекретное-слово\n")
    (мир.м / "bench/a.txt").write_text("тут Секретное-Слово\n")
    мир.коммит()
    r = мир.выпуск("--в", str(мир.клон))
    assert r.returncode == 1 and "секретное-слово" in r.stdout and "bench/a.txt:1" in r.stdout


def test_проверка_без_клона_и_грязная_мастерская(мир):
    мир.коммит()
    (мир.м / "tools/dirty.py").write_text("x\n")
    assert мир.выпуск("--проверка").returncode == 0
    assert мир.выпуск("--в", str(мир.клон)).returncode == 1


def test_проверка_тег_занят(мир):
    мир.коммит()
    assert мир.выпуск("--в", str(мир.клон)).returncode == 0
    assert мир.выпуск("--проверка", "--в", str(мир.клон)).returncode == 1


def test_pre_push(мир):
    shutil.copy(КОРЕНЬ / "tools" / "pre-push", мир.м / "tools" / "pre-push")
    (мир.м / "bench/a.txt").write_text(USERS + "\n")
    мир.коммит()
    assert run(["bash", str(мир.м / "tools/pre-push")], мир.м, мир.env).returncode == 1
    (мир.м / "bench/a.txt").write_text("ок\n")
    мир.коммит()
    assert run(["bash", str(мир.м / "tools/pre-push")], мир.м, мир.env).returncode == 0
