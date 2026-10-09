"""Агенты Mind Studio. Каждый отдаёт поток событий (вид, данные):

    ("route", {...})      кто отвечает (агент, модель, уровень задачи)
    ("text", "...")       кусок ответа
    ("activity", "...")   что агент делает (вызов инструмента и т. п.)
    ("meta", {...})       служебное: id сессии Claude, id разговора Antigravity — сохраняется в чат
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterator

import aisktag_ai as ai

from . import antigravity

Event = tuple[str, object]

AGENTS = {
    "mind": {"title": "Mind", "subtitle": "ваши модели, бесплатные первыми"},
    "claude": {"title": "Claude", "subtitle": "Claude Code на вашем аккаунте"},
    "antigravity": {"title": "Antigravity", "subtitle": "агент Antigravity через мост"},
    "all": {"title": "Все сразу", "subtitle": "агенты отвечают вместе, Mind сводит"},
}


def claude_exe() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    return None


def status() -> dict:
    cfg = ai.load_config()
    have = ai.available_providers(cfg)
    return {
        "mind": {"ready": bool(have), "detail": ", ".join(ai.PROVIDERS[p]["title"] for p in have)
                 or "нет ключей и локальной модели"},
        "claude": {"ready": bool(claude_exe()), "detail": "Claude Code найден" if claude_exe()
                   else "Claude Code не установлен"},
        "antigravity": {"ready": antigravity.linked(), "installed": antigravity.installed(),
                        "autostart": antigravity.autostart_installed(),
                        "detail": "мост подключён" if antigravity.linked() else
                        (antigravity.last_error() or "попросите агента Antigravity запустить мост")
                        if antigravity.installed() else "Antigravity не найден"},
    }


# ---------------------------------------------------------------------------
# Mind — маршрутизатор моделей
# ---------------------------------------------------------------------------

def run_mind(messages: list[dict], opts: dict, should_stop: Callable[[], bool]) -> Iterator[Event]:
    routes: list[dict] = []
    mode = "council" if opts.get("mode") == "council" else "auto"
    cfg = ai.load_config()
    if opts.get("provider") in ai.PROVIDERS:
        name = opts["provider"]
        cfg.update(provider=name, base_url=ai.PROVIDERS[name]["base_url"],
                   model=opts.get("model") or ai.provider_model(name, "code"), api_key=ai.provider_key(name, cfg))
    gen = ai.stream_chat(messages, cfg, should_stop=should_stop, tier=opts.get("tier"),
                         persona=opts.get("persona", "general"), mode=mode, on_route=routes.append)
    for piece in gen:
        while routes:
            r = routes.pop(0)
            yield "route", {"agent": "mind", **r}
        yield "text", piece
    while routes:
        yield "route", {"agent": "mind", **routes.pop(0)}


# ---------------------------------------------------------------------------
# Claude — Claude Code CLI (подписка пользователя), сессия продолжается в рамках чата
# ---------------------------------------------------------------------------

def _tool_line(block: dict) -> str:
    name = block.get("name", "инструмент")
    inp = block.get("input") or {}
    hint = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or ""
    return f"{name} {str(hint)[:80]}".strip()


def run_claude(prompt: str, opts: dict, should_stop: Callable[[], bool]) -> Iterator[Event]:
    exe = claude_exe()
    if not exe:
        raise ai.AIError("Claude Code не установлен. Установите: npm install -g @anthropic-ai/claude-code")
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if opts.get("claude_session"):
        cmd += ["--resume", opts["claude_session"]]
    if opts.get("claude_model"):
        cmd += ["--model", opts["claude_model"]]
    if opts.get("system"):
        cmd += ["--append-system-prompt", opts["system"]]
    # «Агент» может править файлы проекта; по умолчанию — только чтение и ответы
    if opts.get("claude_edit"):
        cmd += ["--permission-mode", "acceptEdits"]
    cwd = opts.get("cwd") if opts.get("cwd") and Path(opts["cwd"]).is_dir() else str(Path.home())
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)     # работать по подписке, а не списывать деньги с API-ключа
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    err_lines: list[str] = []
    threading.Thread(target=lambda: err_lines.extend(proc.stderr.read().decode(errors="replace").splitlines()),
                     daemon=True).start()
    routed = False
    got_text = False
    try:
        for raw in proc.stdout:
            if should_stop():
                proc.kill()
                return
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                yield "meta", {"claude_session": ev.get("session_id", "")}
            elif t == "stream_event" and not ev.get("parent_tool_use_id"):
                e = ev.get("event", {})
                if e.get("type") == "message_start" and not routed:
                    routed = True
                    yield "route", {"agent": "claude", "provider": "claude",
                                    "model": e.get("message", {}).get("model", ""), "tier": ""}
                elif e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                    got_text = True
                    yield "text", e["delta"]["text"]
                elif e.get("type") == "content_block_start" and e.get("content_block", {}).get("type") == "tool_use":
                    yield "activity", "Claude: " + e["content_block"].get("name", "инструмент")
            elif t == "assistant" and not ev.get("parent_tool_use_id"):
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        yield "activity", "Claude: " + _tool_line(block)
                if got_text:
                    yield "text", "\n\n"
                    got_text = False
            elif t == "result":
                if ev.get("is_error") or ev.get("subtype", "success") != "success":
                    raise ai.AIError("Claude: " + str(ev.get("result") or ev.get("subtype"))[:300])
    finally:
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc.stdout.close()
    if proc.returncode not in (0, None) and not routed:
        raise ai.AIError("Claude Code завершился с ошибкой: " + " ".join(err_lines[-3:])[:300])


# ---------------------------------------------------------------------------
# Antigravity — через мост
# ---------------------------------------------------------------------------

def run_antigravity(prompt: str, opts: dict, should_stop: Callable[[], bool]) -> Iterator[Event]:
    acts: list[str] = []
    model = opts.get("agy_model") or "flash"
    yield "route", {"agent": "antigravity", "provider": "antigravity",
                    "model": antigravity.MODELS.get(model, model), "tier": ""}
    try:
        for kind, data in antigravity.ask(prompt, conversation_id=opts.get("agy_conversation", ""), model=model,
                                          title=opts.get("title", ""), should_stop=should_stop,
                                          on_activity=acts.append):
            while acts:
                yield "activity", acts.pop(0)
            if kind == "conversation":
                yield "meta", {"agy_conversation": data}
            else:
                yield "text", data
    except antigravity.AntigravityError as e:
        raise ai.AIError(f"Antigravity: {e}") from e


# ---------------------------------------------------------------------------
# Все сразу: агенты параллельно, Mind сводит
# ---------------------------------------------------------------------------

def _collect(gen: Iterator[Event]) -> tuple[str, list[Event]]:
    text, meta = [], []
    for kind, data in gen:
        if kind == "text":
            text.append(str(data))
        elif kind in ("meta", "route"):
            meta.append((kind, data))
    return "".join(text), meta


def run_all(messages: list[dict], prompt: str, opts: dict, should_stop: Callable[[], bool]) -> Iterator[Event]:
    st = status()
    jobs: dict[str, Callable[[], Iterator[Event]]] = {}
    if st["mind"]["ready"]:
        jobs["Mind"] = lambda: run_mind(messages, {**opts, "mode": "auto", "tier": "code"}, should_stop)
    if st["claude"]["ready"]:
        jobs["Claude"] = lambda: run_claude(prompt, {**opts, "claude_model": opts.get("claude_model") or "sonnet"},
                                            should_stop)
    if st["antigravity"]["ready"]:
        jobs["Antigravity"] = lambda: run_antigravity(prompt, opts, should_stop)
    if not jobs:
        raise ai.AIError("Ни один агент не готов: добавьте ключ в настройках или подключите Claude/Antigravity.")
    results: dict[str, str] = {}
    metas: list[Event] = []
    errors: dict[str, str] = {}

    def work(name: str) -> None:
        try:
            text, meta = _collect(jobs[name]())
            results[name] = text
            metas.extend(m for m in meta if m[0] == "meta")
        except ai.AIError as e:
            errors[name] = str(e)

    threads = [threading.Thread(target=work, args=(n,), daemon=True) for n in jobs]
    for t in threads:
        t.start()
    for name in jobs:
        yield "activity", f"{name} думает…"
    for t in threads:
        t.join()
    yield from metas
    for name, err in errors.items():
        yield "activity", f"{name}: {err[:120]}"
    answers = {n: t for n, t in results.items() if t.strip()}
    if should_stop() or not answers:
        if not answers:
            raise ai.AIError("Никто из агентов не ответил: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
        return
    if len(answers) == 1:
        name, text = next(iter(answers.items()))
        yield "route", {"agent": name.lower(), "provider": name.lower(), "model": "", "tier": "all"}
        yield "text", text
        return
    bundle = "\n\n".join(f"### Ответ агента {n}\n{t}" for n, t in answers.items())
    user = prompt
    judge = [{"role": "system", "content": ai.JUDGE_PROMPT},
             {"role": "user", "content": f"Вопрос пользователя:\n{user}\n\n{bundle}"}]
    routes: list[dict] = []
    voters = ", ".join(answers)
    for piece in ai.stream_chat(judge, ai.load_config(), tier="deep", should_stop=should_stop, use_cache=False,
                                on_route=routes.append):
        while routes:
            yield "route", {"agent": "all", "voters": voters, **routes.pop(0), "tier": "all"}
        yield "text", piece
