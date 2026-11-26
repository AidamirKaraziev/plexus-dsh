"""Тесты tools/выпуск_ci.py: порядок.txt, ожидание, зеркало, сухой режим, сводка. Без сети и без npm."""
import importlib.util
import json
from pathlib import Path

import pytest

ПУТЬ = Path(__file__).resolve().parent / "выпуск_ci.py"
_спек = importlib.util.spec_from_file_location("выпуск_ci", ПУТЬ)
в = importlib.util.module_from_spec(_спек)
_спек.loader.exec_module(в)

СТРОКИ = ("@plexus-dsh/meter 0.1.1 plexus-dsh-meter-0.1.1.tgz\n"
          "@plexus-dsh/bundle 0.1.2 plexus-dsh-bundle-0.1.2.tgz\n")


class Часы:
    """Поддельные часы: спать сдвигает время."""
    def __init__(self):
        self.т = 0.0

    def часы(self):
        return self.т

    def спать(self, с):
        self.т += с


@pytest.fixture
def мир(tmp_path, monkeypatch):
    ч = Часы()
    монитор = {"сеть": [], "npm": []}
    monkeypatch.setattr(в, "часы", ч.часы)
    monkeypatch.setattr(в, "спать", ч.спать)
    monkeypatch.setattr(в, "http_статус", lambda u, м="HEAD": монитор["сеть"].append(u) or 404)
    monkeypatch.setattr(в, "http_json", lambda u: монитор["сеть"].append(u) or None)
    monkeypatch.setattr(в, "http_put", lambda u: монитор["сеть"].append(u) or 500)
    monkeypatch.setattr(в, "запустить_npm", lambda a: монитор["npm"].append(a) or 0)
    папка = tmp_path / "tgz"
    папка.mkdir()
    (папка / "порядок.txt").write_text(СТРОКИ)
    монитор["папка"] = папка
    return монитор


def test_порядок_пустой_файл(tmp_path):
    (tmp_path / "порядок.txt").write_text("")
    assert в.разобрать_порядок(str(tmp_path)) == []


def test_порядок_сохраняется(мир):
    assert в.разобрать_порядок(str(мир["папка"])) == [
        ("@plexus-dsh/meter", "0.1.1", "plexus-dsh-meter-0.1.1.tgz"),
        ("@plexus-dsh/bundle", "0.1.2", "plexus-dsh-bundle-0.1.2.tgz")]


@pytest.mark.parametrize("строка", ["@plexus-dsh/meter 0.1.1", "left-pad 1.0.0 a.tgz",
                                    "@plexus-dsh/m 0.1.1 ../a.tgz", "@plexus-dsh/m 0.1.1 a.txt"])
def test_порядок_кривая_строка_ошибка(tmp_path, строка):
    (tmp_path / "порядок.txt").write_text(строка + "\n")
    with pytest.raises(в.Ошибка):
        в.разобрать_порядок(str(tmp_path))
    assert в.main(["опубликовать", str(tmp_path)]) == 1


def test_пустой_порядок_ничего_не_делает(мир):
    (мир["папка"] / "порядок.txt").write_text("")
    for к in ("опубликовать", "ждать", "зеркало"):
        assert в.main([к, str(мир["папка"])]) == 0
    assert мир["npm"] == [] and мир["сеть"] == []


def test_опубликовать_по_порядку(мир):
    assert в.main(["опубликовать", str(мир["папка"])]) == 0
    assert [a[1] for a in мир["npm"]] == [str(мир["папка"] / "plexus-dsh-meter-0.1.1.tgz"),
                                          str(мир["папка"] / "plexus-dsh-bundle-0.1.2.tgz")]
    assert all(a[0] == "publish" and a[2:] == ["--access", "public"] for a in мир["npm"])


def test_опубликовать_первый_сбой_останавливает(мир, monkeypatch, capsys):
    monkeypatch.setattr(в, "запустить_npm", lambda a: мир["npm"].append(a) or 1)
    assert в.main(["опубликовать", str(мир["папка"])]) == 1
    assert len(мир["npm"]) == 1
    assert "@plexus-dsh/meter" in capsys.readouterr().err


def test_ожидание_404_404_200_успех(мир, monkeypatch):
    коды = iter([404, 404, 200, 200])
    запросы = []
    monkeypatch.setattr(в, "http_статус", lambda u, м="HEAD": запросы.append(u) or next(коды))
    assert в.main(["ждать", str(мир["папка"]), "--шаг", "1"]) == 0
    assert запросы[0] == "https://registry.npmjs.org/@plexus-dsh/meter/-/meter-0.1.1.tgz"
    assert запросы[3] == "https://registry.npmjs.org/@plexus-dsh/bundle/-/bundle-0.1.2.tgz"


def test_ожидание_таймаут_код_1_с_адресом(мир, capsys):
    assert в.main(["ждать", str(мир["папка"]), "--таймаут", "10", "--шаг", "3"]) == 1
    err = capsys.readouterr().err
    assert "@plexus-dsh/meter" in err
    assert "https://registry.npmjs.org/@plexus-dsh/meter/-/meter-0.1.1.tgz" in err


def test_разбор_ответа_зеркала():
    assert в.версия_в_ответе('{"versions": {"0.1.1": {}}}', "0.1.1")
    assert not в.версия_в_ответе('{"versions": {"0.1.0": {}}}', "0.1.1")
    assert not в.версия_в_ответе("{не json", "0.1.1")
    assert not в.версия_в_ответе('{"name": "x"}', "0.1.1")
    assert not в.версия_в_ответе("[1]", "0.1.1")


def test_url_опроса_зеркала_содержит_t():
    assert "?t=" in в.адрес_опроса_зеркала("@plexus-dsh/meter")
    assert в.адрес_синхронизации("@plexus-dsh/meter") == \
        "https://registry-direct.npmmirror.com/-/package/@plexus-dsh/meter/syncs"


def test_зеркало_успех_пишет_файл(мир, monkeypatch):
    опросы = []

    def json_ответ(u):
        опросы.append(u)
        return '{"versions": {}}' if len(опросы) == 1 else '{"versions": {"0.1.1": {}, "0.1.2": {}}}'

    monkeypatch.setattr(в, "http_put", lambda u: 201)
    monkeypatch.setattr(в, "http_json", json_ответ)
    assert в.main(["зеркало", str(мир["папка"]), "--шаг", "1"]) == 0
    assert all("?t=" in u for u in опросы)
    данные = json.loads((мир["папка"] / "зеркало.json").read_text())
    assert данные["@plexus-dsh/meter"]["состояние"] == "есть"
    assert данные["@plexus-dsh/bundle"]["состояние"] == "есть"


def test_зеркало_сбой_и_таймаут_не_валят(мир, monkeypatch, capsys):
    коды = iter([500, 200])
    monkeypatch.setattr(в, "http_put", lambda u: next(коды))
    monkeypatch.setattr(в, "http_json", lambda u: '{"versions": {}}')
    assert в.main(["зеркало", str(мир["папка"]), "--таймаут", "5", "--шаг", "2"]) == 0
    assert "ПРЕДУПРЕЖДЕНИЕ" in capsys.readouterr().err
    данные = json.loads((мир["папка"] / "зеркало.json").read_text())
    assert данные["@plexus-dsh/meter"]["состояние"] == "ошибка"
    assert данные["@plexus-dsh/bundle"]["состояние"] == "таймаут"


def test_сухой_режим_не_зовёт_сеть_и_npm(мир, capsys):
    for к in ("опубликовать", "ждать", "зеркало"):
        assert в.main([к, str(мир["папка"]), "--сухой"]) == 0
    вывод = capsys.readouterr().out
    assert "было бы опубликовано: @plexus-dsh/meter@0.1.1" in вывод
    assert "было бы ждали" in вывод
    assert мир["npm"] == [] and мир["сеть"] == []
    assert not (мир["папка"] / "зеркало.json").exists()


def test_сводка(мир, capsys):
    (мир["папка"] / "зеркало.json").write_text(json.dumps(
        {"@plexus-dsh/meter": {"версия": "0.1.1", "состояние": "есть", "пояснение": ""}}))
    assert в.main(["сводка", str(мир["папка"]), "--витрина", "0.1.5"]) == 0
    вывод = capsys.readouterr().out
    assert "https://www.npmjs.com/package/@plexus-dsh/meter/v/0.1.1" in вывод
    assert "https://www.npmjs.com/package/@plexus-dsh/bundle/v/0.1.2" in вывод
    assert "https://github.com/AidamirKaraziev/plexus-dsh/releases/tag/v0.1.5" in вывод
    assert "синхронизировано" in вывод and "не проверялось" in вывод
    assert "Сухой прогон" not in вывод


def test_сводка_сухая_и_пустая(мир, capsys):
    в.main(["сводка", str(мир["папка"]), "--витрина", "0.1.5", "--сухой"])
    assert "Сухой прогон: было бы опубликовано" in capsys.readouterr().out
    (мир["папка"] / "порядок.txt").write_text("")
    в.main(["сводка", str(мир["папка"]), "--витрина", "0.1.5"])
    assert "Новых версий нет" in capsys.readouterr().out
