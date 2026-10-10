#!/usr/bin/env python3
"""Дистилляция: учитель (сильная модель) решает задачи в формате агента Mind, решения проверяются и идут в датасет.

    python mindai/distill.py                                  # учитель — Claude Code (подписка, модель sonnet)
    python mindai/distill.py --teacher ollama:qwen3.5:9b      # любой учитель из Ollama
    python mindai/distill.py --limit 5 --prompts mindai/data/prompts.txt

Учитель видит тот же системный промпт, что и Mind, пишет те же теги действий; действия по-настоящему выполняются
во временной папке, результаты возвращаются учителю, пока он не скажет итог. В датасет (data/distilled.jsonl)
попадают только траектории, прошедшие проверку: задачи экзамена — его проверками, остальные — дымовым тестом
страниц, успешным запуском команд и отсутствием просьб «скопируйте код».
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "bench"))
import run_bench as rb  # noqa: E402  — та же песочница и те же проверки, что у экзамена

ma, ai = rb.ma, rb.ai
MAX_STEPS = 6


def teacher_reply(teacher: str, system: str, loop: list[dict]) -> str:
    if teacher.startswith("ollama:"):
        cfg = {**ai.load_config(), "provider": "ollama", "base_url": ai.PROVIDERS["ollama"]["base_url"],
               "model": teacher.split(":", 1)[1], "api_key": "", "ollama_options": ma.OLLAMA_OPTIONS}
        return "".join(ai.stream_chat([{"role": "system", "content": system}] + loop, cfg, max_tokens=8192,
                                      temperature=0.3, use_cache=False))
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("Claude Code не найден — укажите --teacher ollama:МОДЕЛЬ")
    convo = "\n\n".join(("ПОЛЬЗОВАТЕЛЬ:\n" if m["role"] == "user" else "ТЫ (Mind):\n") + m["content"] for m in loop)
    prompt = ("Ниже разговор. Напиши СЛЕДУЮЩИЙ ответ Mind строго в формате тегов действий из системного промпта: "
              "теги выполнит среда, результаты придут следующим сообщением. Только текст ответа.\n\n"
              f"{convo}\n\nТЫ (Mind):")
    # По подписке пользователя, не по API-ключу; переменные родительской сессии Claude Code тоже не передаём
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDE_AGENT_")) and k not in ("CLAUDECODE", "CLAUDE_PID")}
    model = teacher.split(":", 1)[1] if ":" in teacher else "sonnet"
    # Учитель отвечает с системным промптом Mind и без инструментов: только текст ответа. Хуки Claude Code
    # выключены (с разрешения владельца, 2026-10-10), вход и остальные настройки — ваши: хук проверки правок
    # перехватывал ответы учителя, хотя тот ничего не выполняет — действия выполняет песочница distill.py.
    r = subprocess.run([exe, "-p", "--model", model, "--output-format", "text", "--tools", "",
                        "--system-prompt", system, "--settings", '{"disableAllHooks": true}', "--strict-mcp-config"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=env,
                       input=prompt)      # разговор через stdin: в командной строке Windows он не помещается
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-400:])
    return r.stdout.strip()


def solve(task: dict, teacher: str) -> dict:
    ws = rb.TMP / "distill" / f"{task['id']}-{int(time.time() * 1000)}"
    desk = ws.parent / (ws.name + "-desktop")
    ws.mkdir(parents=True)
    desk.mkdir()
    for rel, content in (task.get("setup") or {}).items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(content, encoding="utf-8")
    ma.desktop = lambda: desk
    ma.open_path = lambda target: None
    ex = ma.Executor(ws.resolve(), {"bypass": True, "confirm_danger": True}, lambda: False)
    system = ma.system_prompt(ex.ws)
    loop = [{"role": "user", "content": task["prompt"]}]
    shown = []
    for _ in range(MAX_STEPS):
        reply = teacher_reply(teacher, system, loop)
        loop.append({"role": "assistant", "content": reply})
        p = ma.ActionStream()
        events = p.feed(reply) + p.flush()
        results = []
        for ev in events:
            if ev[0] == "text":
                shown.append(ev[1])
            elif ev[0] == "action":
                text, res = ex.do(ev[1], ev[2], ev[3])
                shown.append(text)
                results.append(res)
        if not results:
            break
        loop.append({"role": "user", "content": "Результаты:\n" + "\n".join(results) +
                     "\n\nПродолжай. Если всё готово — коротко скажи итог без действий."})
    trace = {"loop": loop, "problem": ""}
    checks = task.get("checks") or ["smoke", "no_missing_refs", "no_copy_paste"]
    fails = rb.check({**task, "checks": checks}, ws, desk, "".join(shown), trace)
    if any("ОШИБКА" in m["content"] for m in loop[-1:] if m["role"] == "user"):
        fails.append("последний шаг закончился ошибкой")
    return {"task": task["id"], "teacher": teacher, "prompt": task["prompt"], "system": system, "loop": loop,
            "fails": fails, "ok": not fails}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", default="claude:sonnet")
    ap.add_argument("--prompts", default=str(HERE / "data" / "prompts.txt"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    tasks = [json.loads(l) for l in (HERE / "bench" / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for i, line in enumerate(Path(args.prompts).read_text(encoding="utf-8").splitlines()):
        if line.strip() and not line.startswith("#"):
            tasks.append({"id": f"p{i:03d}", "prompt": line.strip()})
    if args.limit:
        tasks = tasks[:args.limit]
    out = HERE / "data" / "distilled.jsonl"
    done = set()
    if out.exists():      # уже принятые задачи повторно не гоняем: прерванный сбор продолжается с места
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ok") and r.get("teacher") == args.teacher:
                done.add(r["prompt"])
    tasks = [t for t in tasks if t["prompt"] not in done]
    ok = 0
    for t in tasks:
        try:
            r = solve(t, args.teacher)
        except Exception as e:  # noqa: BLE001
            print(f"! {t['id']}: {e}", flush=True)
            continue
        ok += r["ok"]
        print(f"{'✓' if r['ok'] else '✗'} {t['id']:10} {'; '.join(r['fails'])[:160]}", flush=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"== принято {ok} из {len(tasks)} → {out}")


if __name__ == "__main__":
    tempfile.tempdir = str(rb.TMP)
    main()
