#!/usr/bin/env python3
"""Тесты маршрутизатора Mind (aisktag_ai) на поддельных API — без сети и без настоящих ключей.

    python3 tools/test-mind-router.py

Поднимает несколько локальных HTTP-серверов, каждый изображает провайдера с заданным поведением:
нормальный ответ, лимит 429, неверный ключ 401, ошибка 500, пустой ответ, обрыв посреди ответа,
Anthropic SSE. Проверяет выбор модели под задачу, резерв, паузы, кэш, консилиум и каталог моделей.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="mind-test-")
# Изоляция: настройки и кэш только во временной папке, никакие реальные ключи не подхватываются
os.environ["XDG_CONFIG_HOME"] = str(Path(TMP) / "conf")
os.environ["XDG_CACHE_HOME"] = str(Path(TMP) / "cache")
os.environ["AISKTAG_CATALOG"] = str(ROOT / "overlay/usr/share/aisktagos/ai/models.json")
for k in list(os.environ):
    if k.endswith(("_API_KEY",)) or k in ("AI_PROVIDER", "AI_BASE_URL", "AI_MODEL", "AI_API_KEY"):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "overlay/usr/lib/aisktagos"))
import aisktag_ai as ai  # noqa: E402

CALLS: list[tuple[str, dict]] = []   # (имя сервера, тело запроса)


def make_server(name: str, behaviour: str):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            CALLS.append((name, body))
            b = SERVERS[name]["behaviour"]
            if b == "gone1":   # первая модель из списка снята с поддержки (410), остальные работают
                b = "410" if body.get("model") == ai.PROVIDERS[name]["models"]["fast"][0] else "ok"
            if b == "overload_deep":   # «Service temporarily overloaded» событием в потоке у главной deep-модели
                b = "overload" if body.get("model") == ai.PROVIDERS[name]["models"]["deep"][0] else "ok"
            if b == "overload":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"error":{"message":"Service temporarily overloaded"}}\n\n')
                return
            if b in ("429", "401", "500", "404", "410"):
                self.send_response(int(b))
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"nope"}}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if b == "empty":
                self.wfile.write(b"data: [DONE]\n\n")
                return
            if b == "claude":
                for part in (f"{name}:", body.get("system", "")[:10]):
                    ev = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": part}}
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.write(b'data: {"type":"message_stop"}\n\n')
                return
            text = f"{name}|{body.get('model')}|{len(body.get('messages', []))}"
            if b == "judge":
                user = body["messages"][-1]["content"]
                text = "JUDGED:" + ",".join(s for s in ("ok1", "ok2", "ok3") if s in user)
            for i in range(0, len(text), 4):
                ev = {"choices": [{"delta": {"content": text[i:i + 4]}}]}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                if b == "break" and i >= 4:
                    self.wfile.write(b'data: {"error":{"message":"oops"}}\n\n')
                    return
            self.wfile.write(b"data: [DONE]\n\n")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


SERVERS: dict[str, dict] = {}


def setup_providers(spec: dict[str, str]) -> None:
    """spec: провайдер → поведение. Ключ выдаётся только перечисленным провайдерам."""
    for name in ai.PROVIDERS:
        for env in ai.PROVIDERS[name]["env"]:
            os.environ.pop(env, None)
    for name, behaviour in spec.items():
        if name not in SERVERS:
            srv = make_server(name, behaviour)
            SERVERS[name] = {"srv": srv, "behaviour": behaviour}
            port = srv.server_address[1]
            base = f"http://127.0.0.1:{port}" if ai.PROVIDERS[name]["kind"] == "claude" else f"http://127.0.0.1:{port}/v1"
            ai.PROVIDERS[name]["base_url"] = base
        SERVERS[name]["behaviour"] = behaviour
        if name != "local":
            os.environ[ai.PROVIDERS[name]["env"][0]] = "test-key"
    ai.backend_state = lambda: "ready" if "local" in spec else "missing"


def chat(text: str, **kw) -> tuple[str, dict]:
    route: dict = {}
    out = ai.complete([{"role": "system", "content": "sys"}, {"role": "user", "content": text}],
                      on_route=route.update, **kw)
    return out, route


class RouterTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        ai.reset_cooldowns()
        ai.clear_cache()
        try:
            ai.USER_CONF.unlink()
        except OSError:
            pass

    def test_classify(self):
        u = lambda t: [{"role": "user", "content": t}]  # noqa: E731
        self.assertEqual(ai.classify(u("привет, как дела?")), "fast")
        self.assertEqual(ai.classify(u("напиши функцию на python для сортировки")), "code")
        self.assertEqual(ai.classify(u("спроектируй архитектуру сервиса заказов с очередями и кэшем")), "deep")
        self.assertEqual(ai.classify(u("глянь"), persona="review"), "deep")
        self.assertEqual(ai.classify(u("x" * 25000)), "deep")

    def test_default_is_auto(self):
        self.assertEqual(ai.load_config()["provider"], "auto")

    def test_fast_question_goes_to_free_model_first(self):
        setup_providers({"groq": "ok", "claude": "claude"})
        out, route = chat("привет")
        self.assertEqual(route["provider"], "groq")
        self.assertTrue(out.startswith("groq|llama"))
        self.assertEqual([c[0] for c in CALLS], ["groq"])   # платный Claude не тронут

    def test_local_model_preferred_for_simple_questions(self):
        setup_providers({"local": "ok", "groq": "ok"})
        out, route = chat("как дела")
        self.assertEqual(route["provider"], "local")

    def test_fallback_on_rate_limit_and_cooldown(self):
        setup_providers({"groq": "429", "nvidia": "ok"})
        out, route = chat("напиши код на python")
        self.assertEqual(route["provider"], "nvidia")
        self.assertEqual(len(route["tried"]), 1)
        self.assertIn("groq", ai._cooldowns())
        CALLS.clear()
        ai.clear_cache()
        out, route = chat("напиши ещё код на python")
        self.assertEqual([c[0] for c in CALLS], ["nvidia"])   # groq на паузе — не дёргаем

    def test_retired_model_switches_to_next_model_of_same_provider(self):
        setup_providers({"nvidia": "gone1", "groq": "ok"})
        os.environ.pop("GROQ_API_KEY")          # только NVIDIA: резерв обязан найтись внутри неё
        out, route = chat("привет")
        self.assertEqual(route["provider"], "nvidia")
        self.assertEqual(route["model"], ai.PROVIDERS["nvidia"]["models"]["fast"][1])
        paused = ai._cooldowns()
        self.assertIn("nvidia|" + ai.PROVIDERS["nvidia"]["models"]["fast"][0], paused)
        self.assertNotIn("nvidia", paused)     # сама служба не на паузе
        CALLS.clear()
        ai.clear_cache()
        chat("привет ещё раз")
        self.assertEqual(len(CALLS), 1)        # снятую модель больше не дёргаем

    def test_overloaded_model_switches_model(self):
        setup_providers({"nvidia": "overload_deep"})
        _, route = chat("спроектируй архитектуру очереди задач с ретраями")
        self.assertEqual(route["tier"], "deep")
        self.assertEqual(route["model"], ai.PROVIDERS["nvidia"]["models"]["deep"][1])

    def test_deep_falls_back_to_simpler_tier(self):
        setup_providers({"nvidia": "overload"})
        orig = ai.PROVIDERS["nvidia"]["models"]
        ai.PROVIDERS["nvidia"]["models"] = {"fast": ["f1"], "code": ["c1"], "deep": ["d1"]}
        SERVERS["nvidia"]["behaviour"] = "ok"
        ai._cool("nvidia|d1", 600)          # главная модель уровня занята
        try:
            _, route = chat("спроектируй архитектуру очереди задач с ретраями", use_cache=False)
        finally:
            ai.PROVIDERS["nvidia"]["models"] = orig
        self.assertIn(route["model"], ("d1", "c1"))

    def test_bad_key_skipped(self):
        setup_providers({"groq": "401", "gemini": "ok"})
        _, route = chat("привет")
        self.assertEqual(route["provider"], "gemini")
        self.assertGreater(ai._cooldowns()["groq"], 3600)

    def test_server_error_and_empty_answer(self):
        setup_providers({"groq": "500", "gemini": "empty", "nvidia": "ok"})
        _, route = chat("привет")
        self.assertEqual(route["provider"], "nvidia")
        self.assertGreaterEqual(len(route["tried"]), 2)

    def test_all_fail(self):
        setup_providers({"groq": "429", "nvidia": "500"})
        with self.assertRaises(ai.AIError) as cm:
            chat("привет")
        self.assertIn("Ни одна модель", str(cm.exception))

    def test_nothing_configured(self):
        setup_providers({})
        with self.assertRaises(ai.AIError) as cm:
            chat("привет")
        self.assertIn("Нет ни одного доступного ИИ", str(cm.exception))

    def test_all_paused_still_tries(self):
        setup_providers({"groq": "ok"})
        ai._cool("groq", 600)
        _, route = chat("привет")
        self.assertEqual(route["provider"], "groq")

    def test_midstream_error_is_not_silently_switched(self):
        setup_providers({"groq": "break", "nvidia": "ok"})
        with self.assertRaises(ai.AIError):
            chat("привет")
        self.assertEqual([c[0] for c in CALLS], ["groq"])

    def test_cache_saves_quota(self):
        setup_providers({"groq": "ok"})
        a, r1 = chat("что такое git rebase?")
        self.assertFalse(r1["cached"])
        b, r2 = chat("что такое git rebase?")
        self.assertEqual(a, b)
        self.assertTrue(r2["cached"])
        self.assertEqual(len(CALLS), 1)
        c, r3 = chat("что такое git rebase?", use_cache=False)
        self.assertEqual(len(CALLS), 2)

    def test_history_trimmed_for_fast(self):
        setup_providers({"groq": "ok"})
        msgs = [{"role": "system", "content": "s"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(30)]
        msgs.append({"role": "user", "content": "ок"})
        ai.complete(msgs)
        self.assertEqual(len(CALLS[0][1]["messages"]), 7)   # system + 6 последних

    def test_tier_override_picks_strong_model(self):
        setup_providers({"nvidia": "ok"})
        out, route = chat("привет", tier="deep")
        self.assertEqual(route["model"], ai.PROVIDERS["nvidia"]["models"]["deep"][0])

    def test_paid_models_last(self):
        for tier, order in ai.DEFAULT_ROUTE.items():
            self.assertGreater(order.index("claude"), order.index("nvidia"), tier)
            self.assertGreater(order.index("claude"), order.index("groq"), tier)

    def test_claude_stream_and_system(self):
        setup_providers({"claude": "claude"})
        out, route = chat("привет")
        self.assertEqual(route["provider"], "claude")
        self.assertEqual(out, "claude:sys")
        self.assertNotIn("system", [m["role"] for m in CALLS[0][1]["messages"]])

    def test_council(self):
        setup_providers({"groq": "ok", "gemini": "ok", "nvidia": "judge"})
        # nvidia первым в очереди deep — он судья; ответы участников подменим маркерами
        orig = ai._stream_direct

        def fake(msgs, cfg, *a, **kw):
            if msgs[0]["content"] == ai.JUDGE_PROMPT:
                return orig(msgs, cfg, *a, **kw)
            return iter([{"groq": "ok1", "gemini": "ok2", "nvidia": "ok3"}[cfg["provider"]]])
        ai._stream_direct = fake
        try:
            out, route = chat("сравни подходы", mode="council")
        finally:
            ai._stream_direct = orig
        self.assertEqual(route["tier"], "council")
        self.assertEqual(route["provider"], "nvidia")
        self.assertEqual(out, "JUDGED:ok1,ok2,ok3")

    def test_council_with_single_key_uses_several_models(self):
        setup_providers({"nvidia": "ok"})
        voters = ai.council_voters(ai.load_config())
        self.assertEqual(len(voters), 3)
        self.assertEqual(len({m for _, m in voters}), 3)
        self.assertTrue(all(p == "nvidia" for p, _ in voters))

    def test_explicit_provider_bypasses_router(self):
        setup_providers({"groq": "ok", "nvidia": "ok"})
        ai.save_config({"provider": "nvidia", "keys": {"nvidia": "k"}})
        cfg = ai.load_config()
        self.assertEqual(cfg["base_url"], ai.PROVIDERS["nvidia"]["base_url"])
        out, route = chat("привет", cfg=cfg)
        self.assertEqual([c[0] for c in CALLS], ["nvidia"])

    def test_keys_from_settings_file(self):
        setup_providers({})
        os.environ.pop("GROQ_API_KEY", None)
        ai.save_config({"provider": "auto", "keys": {"groq": "from-file"}})
        self.assertIn("groq", ai.available_providers())
        self.assertEqual(ai.provider_key("groq"), "from-file")

    def test_catalog_local_vs_cloud(self):
        cat = ai.load_catalog()
        self.assertTrue(cat["models"])
        for m in cat["models"]:
            for f in ("id", "file", "url", "sha256", "size_mb", "ram_mb", "ctx"):
                self.assertIn(f, m, m.get("id"))
        self.assertIn(ai.recommend_model(64000), {m["id"] for m in cat["models"]})
        self.assertEqual(ai.recommend_model(1000), cat["default"])

    def test_ram_detected(self):
        self.assertGreater(ai.ram_mb(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
