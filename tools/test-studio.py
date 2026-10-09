#!/usr/bin/env python3
"""Сквозной тест Mind Studio: сервер, API, SSE и интерфейс в настоящем браузере — без сети и ключей.

    python3 tools/test-studio.py [--shots ПАПКА]

Поддельные агенты: Mind — локальный OpenAI-совместимый сервер-заглушка; Claude — скрипт, печатающий поток
stream-json как настоящий `claude -p`; Antigravity — заглушка agentapi плюс транскрипт во временной папке.
Интерфейс проверяется через Playwright (Chromium/Edge), если он установлен; снимки экрана — в --shots.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="studio-test-"))
SHOTS = Path(sys.argv[sys.argv.index("--shots") + 1]) if "--shots" in sys.argv else None
if SHOTS:
    del sys.argv[sys.argv.index("--shots"):sys.argv.index("--shots") + 2]

os.environ.update({"XDG_CONFIG_HOME": str(TMP / "conf"), "XDG_CACHE_HOME": str(TMP / "cache"),
                   "AISKTAG_CATALOG": str(ROOT / "overlay/usr/share/aisktagos/ai/models.json"),
                   "ANTIGRAVITY_DATA": str(TMP / "agy"), "AISKTAG_SKILLS": str(TMP / "skills")})
for k in list(os.environ):
    if k.endswith("_API_KEY"):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "overlay/usr/lib/aisktagos"))
import aisktag_ai as ai  # noqa: E402

# --- Mind: заглушка модели ------------------------------------------------------------------------------
REPLY = "Привет! Вот **пример**:\n\n```python\nprint('hi')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(REPLY), 8):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': REPLY[i:i + 8]}}]})}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.01)
        self.wfile.write(b"data: [DONE]\n\n")


mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=mock.serve_forever, daemon=True).start()
ai.PROVIDERS["local"]["base_url"] = f"http://127.0.0.1:{mock.server_address[1]}/v1"
ai.backend_state = lambda: "ready"

# --- Claude: поддельный CLI -------------------------------------------------------------------------------
fake_claude = TMP / "fake_claude.py"
fake_claude.write_text('''import json, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
sid = args[args.index("--resume") + 1] if "--resume" in args else "11111111-2222-3333-4444-555555555555"
def out(d): print(json.dumps(d, ensure_ascii=False), flush=True)
out({"type": "system", "subtype": "init", "session_id": sid})
out({"type": "stream_event", "event": {"type": "message_start", "message": {"model": "claude-sonnet-test"}}})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}}]}})
for w in ("Claude ", "получил: ", prompt[:40], " (resume)" if "--resume" in args else ""):
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": w}}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
''', encoding="utf-8")
if sys.platform == "win32":
    shim = TMP / "claude.bat"
    shim.write_text(f'@"{sys.executable}" "{fake_claude}" %*\n', encoding="utf-8")
else:
    shim = TMP / "claude"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_claude}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)

# --- Antigravity: история и заглушка моста ---------------------------------------------------------------
AGY_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
logs = TMP / "agy" / "brain" / AGY_ID / ".system_generated" / "logs"
logs.mkdir(parents=True)
(logs / "transcript.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in [
    {"type": "USER_INPUT", "content": "<USER_REQUEST>\nсделай кнопку\n</USER_REQUEST>"},
    {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "Готово, кнопка сделана."}]) + "\n", encoding="utf-8")
skill = TMP / "skills" / "test-skill"
skill.mkdir(parents=True)
(skill / "SKILL.md").write_text("---\nname: test-skill\ndescription: Тестовый скилл\n---\nГовори кратко.\n", encoding="utf-8")

from mind_studio import agents, antigravity, server, store  # noqa: E402

agents.claude_exe = lambda: str(shim)


class AgyBridge(BaseHTTPRequestHandler):
    """Ведёт себя как мост: на /ask дописывает в транскрипт новый вопрос и ответ."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        cid = req.get("conversation_id") or str(__import__("uuid").uuid4())
        log = TMP / "agy" / "brain" / cid / ".system_generated" / "logs"
        log.mkdir(parents=True, exist_ok=True)

        def later():
            time.sleep(0.5)
            with open(log / "transcript.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "USER_INPUT", "content": req["prompt"]}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE",
                                    "tool_calls": [{"name": "view_file"}]}) + "\n")
                f.write(json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE",
                                    "content": "Antigravity ответил"}, ensure_ascii=False) + "\n")
        threading.Thread(target=later, daemon=True).start()
        body = json.dumps({"conversation_id": cid}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


bridge = ThreadingHTTPServer(("127.0.0.1", 0), AgyBridge)
threading.Thread(target=bridge.serve_forever, daemon=True).start()
store.DATA.mkdir(parents=True, exist_ok=True)
antigravity.LINK_FILE.write_text(json.dumps({"port": bridge.server_address[1], "secret": "s"}), encoding="utf-8")

SRV = server.start()
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"


def call(path, body=None, method=None, token=server.TOKEN):
    req = urllib.request.Request(BASE + "/api/" + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Studio-Token": token, "Content-Type": "application/json"},
                                 method=method or ("POST" if body is not None else "GET"))
    return urllib.request.urlopen(req, timeout=60)


def send(cid, **body) -> tuple[str, dict]:
    events, text, ev = {}, "", ""
    for raw in call(f"chats/{cid}/send", body):
        line = raw.decode().rstrip("\n")
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            events.setdefault(ev, []).append(data)
            if ev == "text":
                text += data
    return text, events


class StudioAPI(unittest.TestCase):
    def test_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            call("state", token="wrong")
        self.assertEqual(cm.exception.code, 403)

    def test_state(self):
        st = json.load(call("state"))
        self.assertTrue(st["status"]["mind"]["ready"])
        self.assertTrue(st["status"]["claude"]["ready"])
        self.assertTrue(st["status"]["antigravity"]["ready"])
        self.assertIn("test-skill", [s["id"] for s in st["skills"]])

    def test_mind_chat_saved(self):
        chat = json.load(call("chats", {"project": "P"}))
        text, ev = send(chat["id"], text="привет", agent="mind")
        self.assertEqual(text, REPLY)
        self.assertEqual(ev["route"][0]["provider"], "local")
        saved = json.load(call("chats/" + chat["id"]))
        self.assertEqual([m["role"] for m in saved["messages"]], ["user", "assistant"])
        self.assertEqual(saved["title"], "привет")

    def test_claude_resumes_session(self):
        chat = json.load(call("chats", {}))
        text, ev = send(chat["id"], text="первый", agent="claude")
        self.assertIn("Claude получил: первый", text)
        self.assertEqual(ev["activity"][0], "Claude: Read README.md")
        text, _ = send(chat["id"], text="второй", agent="claude")
        self.assertIn("(resume)", text)

    def test_antigravity_via_bridge(self):
        chat = json.load(call("chats", {}))
        text, ev = send(chat["id"], text="вопрос для agy", agent="antigravity")
        self.assertIn("Antigravity ответил", text)
        self.assertIn("Antigravity: view_file", ev.get("activity", []))

    def test_antigravity_history(self):
        hist = antigravity.conversation(AGY_ID)
        self.assertEqual(hist[0], {"role": "user", "content": "сделай кнопку"})

    def test_all_agents_combined(self):
        chat = json.load(call("chats", {}))
        text, ev = send(chat["id"], text="все вместе", agent="all")
        self.assertTrue(text)
        self.assertEqual(ev["route"][-1]["tier"], "all")
        self.assertIn("Claude", ev["route"][-1]["voters"])

    def test_skill_added_to_system_prompt(self):
        chat = store.get_chat(json.load(call("chats", {}))["id"])
        prompt = server._system_prompt(chat, {"skills": ["test-skill"]})
        self.assertIn("Говори кратко.", prompt)

    def test_settings_keys_never_returned(self):
        call("settings", {"keys": {"groq": "secret-value-123"}})
        raw = call("settings").read().decode()
        self.assertNotIn("secret-value-123", raw)
        self.assertTrue(json.loads(raw)["keys"]["groq"]["saved"])

    def test_bad_ids_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            call("chats/../../etc")
        with self.assertRaises(urllib.error.HTTPError):
            call("antigravity/conversation/..%2F..%2Fx")


class StudioUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("Playwright не установлен")
        cls.pw = sync_playwright().start()
        for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
            try:
                cls.browser = cls.pw.chromium.launch(**kw)
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            raise unittest.SkipTest("нет браузера для Playwright")
        cls.page = cls.browser.new_page(viewport={"width": 1320, "height": 860})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(server.url(SRV))
        cls.page.wait_for_selector(".agent-card")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def shot(self, name):
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            self.page.wait_for_timeout(700)       # дождаться конца анимаций
            self.page.screenshot(path=str(SHOTS / f"{name}.png"))

    def test_1_empty_state(self):
        self.assertEqual(self.page.locator(".agent-card").count(), 4)
        self.shot("1-start")

    def test_2_send_and_render(self):
        p = self.page
        p.fill("#input", "Покажи пример кода")
        p.keyboard.press("Enter")
        p.wait_for_selector(".msg.assistant .code", timeout=20000)
        p.wait_for_function("!document.querySelector('#btn-send').classList.contains('busy')")
        self.assertEqual(p.locator(".msg.assistant table").count(), 1)
        self.assertIn("Покажи пример кода", p.locator("#crumbs").inner_text())
        self.assertGreater(p.locator("#projects .item, #recent .item").count(), 0)
        self.shot("2-chat")

    def test_3_agent_menu_and_claude(self):
        p = self.page
        p.click("#pick-agent")
        p.click(".menu .mi[data-v=claude]")
        p.fill("#input", "Прочитай README")
        p.keyboard.press("Enter")
        p.wait_for_selector(".activity .act", timeout=20000)
        p.wait_for_function("!document.querySelector('#btn-send').classList.contains('busy')")
        self.assertIn("Claude получил", p.locator(".msg.assistant").last.inner_text())
        self.shot("3-claude")

    def test_4_skills_panel(self):
        p = self.page
        p.click("[data-view=skills]")
        p.wait_for_selector(".card")
        p.click("[data-sk=test-skill]")
        self.assertTrue(p.locator("#chips .chip").count() >= 1)
        self.shot("4-skills")

    def test_5_settings(self):
        p = self.page
        p.click("#btn-settings")
        p.wait_for_selector("#settings:not(.hidden)")
        self.shot("5-settings")
        p.click("[data-tab=keys]")
        p.wait_for_selector("[data-key=groq]")
        self.shot("6-keys")
        p.keyboard.press("Escape")

    def test_6_dark_theme(self):
        p = self.page
        p.click("#btn-new")
        p.click("#btn-theme")
        self.assertEqual(p.evaluate("document.documentElement.dataset.theme"), "dark")
        self.shot("7-dark")
        p.click("#btn-theme")

    def test_7_agents_panel(self):
        p = self.page
        p.click("[data-view=agents]")
        p.wait_for_selector(".card")
        self.assertIn("--link-antigravity", p.locator("pre.howto").inner_text())
        self.assertIn("фоновом", p.locator("pre.howto").inner_text())
        self.shot("8-agents")

    def test_9_no_js_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
