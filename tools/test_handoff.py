"""Офлайн-проверка E04-S03: python3 tools/test_handoff.py"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "lib"))
import bench
import handoff as hof


def ev(тип, **d):
    return {"type": тип, "data": d}


ЖУРНАЛ = [
    ev("tool/call", name="read", arguments=json.dumps({"file_path": "a.py"})),
    ev("tool/call", name="edit", arguments=json.dumps({"file_path": "lib/x.py"})),
    ev("tool/call", name="write", arguments=json.dumps({"file_path": "lib/y.py"})),
    ev("tool/call", name="edit", arguments=json.dumps({"file_path": "lib/x.py"})),
    ev("tool/call", name="bash", arguments=json.dumps({"command": "ls"})),
    ev("tool/call", name="bash", arguments=json.dumps({"command": "python3 -m pytest tests\necho ok"})),
    ev("assistant/message", message={"content": [{"type": "text", "text": "черновик"}]}),
    ev("assistant/message", message={"content": [{"type": "reasoning", "text": "x"}, {"type": "text", "text": "Готово: поправил x и y."}]}),
]


class Тест(unittest.TestCase):
    def test_выжимка(self):
        в = hof.выжимка(ЖУРНАЛ)
        self.assertEqual(в["сделано"], "Готово: поправил x и y.")
        self.assertEqual(в["файлы"], "lib/x.py, lib/y.py")
        self.assertEqual(в["проверить"], "python3 -m pytest tests")

    def test_реплика_хода2(self):
        текст = hof.реплика_хода2(hof.ШАБЛОН.format(**hof.выжимка(ЖУРНАЛ)), bench.CONTINUE)
        self.assertIn("**Что сделано:**", текст)
        self.assertTrue(текст.endswith(bench.CONTINUE))

    def test_план(self):
        t = {"id": "tX", "задание": "сделай"}
        п = bench.план(t, [420, 420], handoff=True)
        self.assertEqual(len(п), 2)
        self.assertEqual(п[0]["ходы"], ["сделай"])
        self.assertEqual(п[1]["ходы"][1:], [bench.CONTINUE])
        self.assertIsNone(п[1]["ходы"][0])
        self.assertEqual(bench.паузы_сессии(п[1], [420, 420]), [420, 420])
        self.assertEqual(bench.паузы_сессии(bench.план(t, [420, 420])[0], [420, 420]), [0, 420, 420])

    def test_строка_суммой(self):
        ц = [{"вход": 10, "кэш": 100, "выход": 5, "usd": 1.5, "журнал": True},
             {"вход": 1, "кэш": 20, "выход": 2, "usd": 0.5, "журнал": True}]
        r = bench.собрать_строку({"id": "tX"}, "e04", ["s1", "s2"], ц, True, [True], [420.0, 420.0], "acp", True, 9)
        self.assertTrue(r["handoff"])
        self.assertEqual(r["сессии"], ["s1", "s2"])
        self.assertEqual(r["usd"], 2.0)
        self.assertEqual(r["токены"], {"вход": 11, "кэш": 120, "выход": 7})
        self.assertNotIn("handoff", bench.собрать_строку({"id": "tX"}, "e04", ["s1"], ц[:1], True, [True], [], "acp", False, 1))


if __name__ == "__main__":
    unittest.main()
