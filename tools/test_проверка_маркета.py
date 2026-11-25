"""Тест установки из индекса: без сети и без DSH — сборка команд и отказ install.sh."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import проверка_маркета as м  # noqa: E402

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA = '054480694c81cc4c68b1c241b470cd74059cb08a'

ИНДЕКС = {
    'format': 1,
    'plugins': [
        {'name': '@plexus-dsh/bundle',
         'versions': [{'version': '0.1.0', 'source': {'type': 'npm'}},
                      {'version': '0.1.1', 'source': {'type': 'npm'}}]},
        {'name': '@plexus-dsh/plexus',
         'versions': [{'version': '2.11.3', 'source': {'type': 'npm'}}]},
    ],
}


def test_цели_npm():
    assert м.цели_индекса(ИНДЕКС) == [
        {'spec': '@plexus-dsh/bundle@0.1.1'},
        {'spec': '@plexus-dsh/plexus@2.11.3'},
    ]


def test_источник_без_path_и_с_суффиксом():
    в = {'version': '1.0.0', 'source': {'type': 'git', 'url': 'https://x.test/a/b.git', 'commit': SHA}}
    assert м.цель_установки('a', в) == {'spec': f'git+https://x.test/a/b.git#{SHA}'}


def test_источник_с_path():
    в = {'version': '1.0.0', 'source': {'type': 'git', 'url': 'https://x.test/a/b', 'commit': SHA, 'path': 'p/q'}}
    assert м.цель_установки('a', в) == {'spec': f'git+https://x.test/a/b.git#{SHA}&path:/p/q'}


def test_registry_уходит_флагом():
    ц = м.цель_установки('p', {'version': '1.0.0', 'source': {'type': 'npm', 'registry': 'https://r.test/'}})
    assert м.команда_добавления('/dsh', 'bench-market', ц) == [
        '/dsh', 'plugin', '--profile', 'bench-market', 'add', '--registry=https://r.test/', 'p@1.0.0']


def test_команда_добавления_и_списка():
    assert м.команда_добавления('/dsh', 'bench-market', {'spec': '@plexus-dsh/bundle@0.1.1'}) == [
        '/dsh', 'plugin', '--profile', 'bench-market', 'add', '@plexus-dsh/bundle@0.1.1']
    assert м.команда_списка('/dsh', 'bench-market') == ['/dsh', 'plugin', '--profile', 'bench-market', 'list']


def test_последняя_версия_релиз_старше_пререлиза():
    п = {'versions': [{'version': '1.0.0-rc.1'}, {'version': '1.0.0'}, {'version': '0.9.9'}]}
    assert м.последняя(п)['version'] == '1.0.0'


def test_патч_профиля_без_блока_продукта_с_адресом():
    исх = ('- id: llm-pi-ai\n  config: {}\n\n# >>> plexus-dsh packages (tools/install.sh)\n'
           '- insert:\n    - id: plexus-dsh-meter\n# <<< plexus-dsh packages\n')
    п = м.патч_профиля(исх, м.АДРЕС_ИНДЕКСА)
    assert 'plexus-dsh-meter' not in п and 'llm-pi-ai' in п
    assert f'- "{м.АДРЕС_ИНДЕКСА}"' in п and '- id: plugin-market' in п


def test_ожидаемые_берутся_из_индекса():
    assert м.ожидаемые(ИНДЕКС) == ['@plexus-dsh/bundle', '@plexus-dsh/plexus']
    assert м.ожидаемые(ИНДЕКС, ('@plexus-dsh/plexus',)) == ['@plexus-dsh/bundle']
    assert м.ожидаемые({'plugins': ИНДЕКС['plugins'][:1]}) == ['@plexus-dsh/bundle']


def test_список_профиля():
    ож = м.ожидаемые(ИНДЕКС)
    assert м.не_хватает_в_списке('@plexus-dsh/bundle 0.1.1\n@plexus-dsh/plexus 2.11.3', ож) == []
    assert м.не_хватает_в_списке('@plexus-dsh/bundle 0.1.1', ож) == ['@plexus-dsh/plexus']


def test_индекс_из_файла(tmp_path):
    f = tmp_path / 'market.json'
    f.write_text(json.dumps(ИНДЕКС), encoding='utf-8')
    assert м.прочитать_индекс(str(f)) == ИНДЕКС
    assert м.прочитать_индекс('file://' + str(f)) == ИНДЕКС


def test_индекс_не_формата_1(tmp_path):
    f = tmp_path / 'market.json'
    f.write_text('{"format": 2, "plugins": []}', encoding='utf-8')
    with pytest.raises(SystemExit):
        м.прочитать_индекс(str(f))


def test_флаг_индекс_по_умолчанию_удалённый(monkeypatch):
    видел = {}
    monkeypatch.setattr(м, 'найти_dsh', lambda: '/dsh')

    def стоп(dsh, адрес):
        видел['адрес'] = адрес
        raise SystemExit('стоп')
    monkeypatch.setattr(м, 'создать_профиль', стоп)
    for арг, ожид in (([], м.АДРЕС_ИНДЕКСА), (['--индекс', 'публичное/market.json'], 'публичное/market.json')):
        with pytest.raises(SystemExit):
            м.main(арг)
        assert видел['адрес'] == ожид


def test_поток_и_справка_ответила():
    out = '\n'.join(json.dumps(e) for e in [
        {'type': 'session', 'sessionId': 'session-1', 'cwd': '/x'},
        {'type': 'tool_call', 'tool': 'skill', 'input': {'name': 'plexus-help'}},
        {'type': 'final', 'sessionId': 'session-1', 'reason': None, 'text': 'Команды Плексуса: /plexus-start …'}]) + '\nмусор\n'
    п = м.разобрать_поток(out)
    assert п['sid'] == 'session-1' and п['вызовы'] == [('skill', {'name': 'plexus-help'})]
    assert м.оценить_справку(п, 0) is None


@pytest.mark.parametrize('текст,код,часть', [
    ('', 0, 'пуст'),
    ('   \n', 0, 'пуст'),
    ('Error: Unknown command /plexus-help', 0, 'nknown command'),
    ('такого скилла нет: no such skill', 0, 'no such skill'),
    ('текст', 1, 'кодом 1'),
])
def test_справка_отказ(текст, код, часть):
    причина = м.оценить_справку({'текст': текст, 'вызовы': [], 'sid': None}, код)
    assert причина and часть in причина


def test_команда_справки():
    assert м.команда_справки('/dsh', 'bench-market') == ['/dsh', '--profile', 'bench-market', '--json', '/plexus-help']


def test_пакет_без_npm_пропускается(monkeypatch):
    class Р:
        returncode, stdout = 1, ''
    monkeypatch.setattr(м.subprocess, 'run', lambda *a, **k: Р())
    assert м.в_npm('@plexus-dsh/plexus') is False
    Р.returncode, Р.stdout = 0, '2.11.3\n'
    assert м.в_npm('@plexus-dsh/plexus') is True


def test_main_без_плексуса_в_индексе_справка_не_идёт(monkeypatch, tmp_path, capsys):
    f = tmp_path / 'market.json'
    f.write_text(json.dumps({'format': 1, 'plugins': ИНДЕКС['plugins'][:1]}), encoding='utf-8')
    вызовы = []
    monkeypatch.setattr(м, 'найти_dsh', lambda: '/dsh')
    monkeypatch.setattr(м, 'создать_профиль', lambda dsh, адрес: None)
    monkeypatch.setattr(м, 'сверка_с_node', lambda *a: None)
    monkeypatch.setattr(м, 'выполнить', lambda cmd: вызовы.append(cmd) or '@plexus-dsh/bundle 0.1.1')
    monkeypatch.setattr(м, 'дымовая_справка', lambda dsh: вызовы.append('справка') or True)
    monkeypatch.setattr(м, 'прогон_t2', lambda: True)
    assert м.main(['--индекс', str(f)]) == 0
    assert 'справка' not in вызовы
    assert 'пропущена' in capsys.readouterr().out


def test_main_плексус_не_в_npm_идёт_без_него(monkeypatch, tmp_path):
    f = tmp_path / 'market.json'
    f.write_text(json.dumps(ИНДЕКС), encoding='utf-8')
    добавлено = []
    monkeypatch.setattr(м, 'найти_dsh', lambda: '/dsh')
    monkeypatch.setattr(м, 'создать_профиль', lambda dsh, адрес: None)
    monkeypatch.setattr(м, 'сверка_с_node', lambda *a: None)
    monkeypatch.setattr(м, 'в_npm', lambda имя: False)
    monkeypatch.setattr(м, 'выполнить', lambda cmd: добавлено.append(cmd[-1]) or '@plexus-dsh/bundle 0.1.1')
    monkeypatch.setattr(м, 'дымовая_справка', lambda dsh: pytest.fail('справка не должна идти'))
    monkeypatch.setattr(м, 'прогон_t2', lambda: True)
    assert м.main(['--индекс', str(f)]) == 0
    assert '@plexus-dsh/bundle@0.1.1' in добавлено and '@plexus-dsh/plexus@2.11.3' not in добавлено


@pytest.mark.parametrize('профиль', ['desktop', 'headless', 'web'])
def test_install_sh_отказывает_не_bench(профиль):
    p = subprocess.run([os.path.join(КОРЕНЬ, 'tools', 'install.sh'), '--profiles', профиль],
                       capture_output=True, text=True)
    assert p.returncode == 1
    assert 'Discover' in p.stderr


def test_install_sh_bench_проходит_проверку_имени(tmp_path):
    # профиль bench-* имя принимает: дальше скрипт идёт к разбору аргументов и падает позже (--from без источника),
    # но не из-за имени профиля
    env = dict(os.environ, DSH_HOME=str(tmp_path), PLUGIN_HOME=str(tmp_path / 'p'))
    p = subprocess.run([os.path.join(КОРЕНЬ, 'tools', 'install.sh'), '--no-build', '--profiles', 'bench-x',
                        '--from', 'bench-none', '--packages', 'zzz'], capture_output=True, text=True, env=env)
    assert 'Discover' not in p.stderr


def test_адрес_url_локального_файла():
    assert м.адрес_url('https://x.test/m.json') == 'https://x.test/m.json'
    assert м.адрес_url('публичное/market.json').startswith('file:///')
