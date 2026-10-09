#!/usr/bin/env python3
"""Тесты агента Mind без модели и сети: разбор действий, выполнение, подтверждения, сообщения во время работы.

    python3 tools/test-mind-agent.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="mind-agent-test-"))
os.environ.update({"XDG_CONFIG_HOME": str(TMP / "conf"), "APPDATA": str(TMP / "conf"), "OLLAMA_HOST": "127.0.0.1:9"})
sys.path.insert(0, str(ROOT / "overlay/usr/lib/aisktagos"))

import aisktag_ai as ai  # noqa: E402
from mind_studio import mind_agent as ma, store  # noqa: E402

ma.desktop = lambda: TMP / "Desktop"
GAME = ("<!DOCTYPE html><html><body><p id='m'>0</p><button id='b'>+</button>"
        "<script>let n=0;document.getElementById('b').onclick=()=>{n++;document.getElementById('m').textContent=n}"
        "</script></body></html>")


def scripted(*replies):
    """Подменяет модель: каждый шаг цикла получает следующий заготовленный ответ (по кусочкам, как поток)."""
    queue = list(replies)
    seen = []

    def fake(messages, cfg=None, **kw):
        seen.append(messages)
        text = queue.pop(0) if queue else "Готово."
        for i in range(0, len(text), 7):
            yield text[i:i + 7]
    ai.stream_chat = fake
    return seen


def run(text, chat=None, inbox=None):
    chat = chat if chat is not None else {"id": "t", "title": "тест"}
    events = list(ma.run(chat, [], text, {}, lambda: False, inbox))
    return chat, "".join(str(d) for k, d in events if k == "text"), events


class Parser(unittest.TestCase):
    def test_stream_split_anywhere(self):
        src = 'Делаю.<think>секрет</think><write path="a/b.txt">```\nhi < there\n```</write> a<b <open target="x"/>'
        for step in (1, 2, 5, 50):
            p = ma.ActionStream()
            ev = []
            for i in range(0, len(src), step):
                ev += p.feed(src[i:i + step])
            ev += p.flush()
            text = "".join(e[1] for e in ev if e[0] == "text")
            acts = [(e[1], e[2]) for e in ev if e[0] == "action"]
            self.assertEqual(text, "Делаю. a<b ")
            self.assertEqual(acts, [("write", {"path": "a/b.txt"}), ("open", {"target": "x"})])
            self.assertNotIn("секрет", text)

    def test_unclosed_write_still_saved(self):
        p = ma.ActionStream()
        ev = p.feed('<write path="x.html"><p>hi</p>') + p.flush()
        self.assertEqual(ev[-1][:3], ("action", "write", {"path": "x.html"}))

    def test_code_blocks_fallback(self):
        files = ma.code_blocks_as_files("Файл style.css:\n```css\nbody{color:red;margin:0;padding:0;font:14px a}\n```", "p")
        self.assertEqual(files[0][0], "p/style.css")


class Agent(unittest.TestCase):
    def setUp(self):
        store.save_settings({"agent_workspace": str(TMP / "ws"), "agent_bypass": True, "agent_confirm_danger": True})

    def test_builds_site_and_shortcut_itself(self):
        scripted(f'Создаю игру.\n<write path="game/index.html">{GAME}</write>\n<shortcut name="Игра" '
                 'target="game/index.html"/>', "Готово: игра и ярлык.")
        _, text, _ = run("Создай сайт игры и ярлык на рабочем столе")
        self.assertTrue((TMP / "ws/game/index.html").read_text(encoding="utf-8").startswith("<!DOCTYPE"))
        self.assertTrue(any((TMP / "Desktop").glob("Игра.*")))
        self.assertIn("Создал файл `game/index.html`", text)
        self.assertIn("ярлык «Игра»", text)
        self.assertIn("опубликовать сайт", text)        # предлагает бесплатный хостинг
        self.assertNotIn("<write", text)

    def test_code_dump_is_turned_into_files(self):
        dump = f"Вот код, сохраните его в index.html:\n```html\n{GAME}\n```"
        scripted(dump, dump, "Готово.")
        _, text, _ = run("сделай сайт-счётчик")
        self.assertTrue((TMP / "ws").glob("*/index.html"))
        self.assertIn("Создал файл", text)

    def test_runs_commands(self):
        scripted("<run>echo mind-ok</run>", "Готово.")
        _, text, _ = run("проверь консоль")
        self.assertIn("mind-ok", text)

    def test_destructive_command_waits_for_yes(self):
        victim = TMP / "keep"
        victim.mkdir(exist_ok=True)
        cmd = f'Remove-Item -Recurse -Force "{victim}"' if ma.IS_WINDOWS else f'rm -rf "{victim}"'
        scripted(f"<run>{cmd}</run>")
        chat, text, _ = run("удали папку keep")
        self.assertIn("Нужно ваше подтверждение", text)
        self.assertTrue(victim.exists())
        scripted("Удалил.")
        _, text, _ = run("да", chat)
        self.assertFalse(victim.exists())

    def test_messages_during_work_are_injected(self):
        box = ma.Inbox()
        seen = scripted('<write path="n.txt">1</write>', "Ок, учёл.")
        box.put("и добавь README")
        _, _, events = run("сделай файл", inbox=box)
        self.assertIn(("inject", "и добавь README"), events)
        self.assertIn("и добавь README", seen[1][-1]["content"])
        self.assertFalse(box.put("поздно"))         # после конца работы ящик закрыт — клиент отправит сам

    def test_smoke_test_catches_dead_clicks(self):
        page = TMP / "dead.html"
        page.write_text("<html><body><button onclick=\"x()\">a</button><script>function x(){}</script></body></html>",
                        encoding="utf-8")
        if not ma.shutil.which("node"):
            self.skipTest("нет Node.js")
        self.assertIn("никак не изменилась", ma.smoke_test(page, page.read_text(encoding="utf-8")))
        page.write_text(GAME, encoding="utf-8")
        self.assertEqual(ma.smoke_test(page, GAME), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
