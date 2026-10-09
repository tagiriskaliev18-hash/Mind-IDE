#!/usr/bin/env python3
"""Экзамен MindAI: агент Mind решает настоящие задачи разработчика, результат проверяется автоматически.

    python mindai/bench/run_bench.py                         # лучшая локальная модель
    python mindai/bench/run_bench.py --model qwen3.5:9b --model qwen3.5:4b
    python mindai/bench/run_bench.py --only puzzle,todo --repeat 3

Каждая задача идёт в чистой временной папке (и с поддельным рабочим столом), проверки — по файлам, дымовому тесту
страниц, запуску команд и тексту ответа. Итоги — mindai/bench/results/*.md и *.json. Каждый провал со списком
причин («разбор ошибок») дописывается в mindai/data/critiques.jsonl: из них потом строятся пары «плохо/хорошо»
для обучения предпочтениям (DPO/KTO), а удачные решения попадают в датасет SFT через make_dataset.py.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = ROOT / "mindai" / "data"
TMP = Path(tempfile.mkdtemp(prefix="mindai-bench-"))
os.environ.update({"XDG_CONFIG_HOME": str(TMP / "conf"), "APPDATA": str(TMP / "conf"), "MINDAI_TRACE": "0"})
sys.path.insert(0, str(ROOT / "overlay/usr/lib/aisktagos"))

import aisktag_ai as ai  # noqa: E402
from mind_studio import mind_agent as ma, store  # noqa: E402

COPY_PASTE = re.compile(r"скопируй|скопируйте|вставь(те)? (этот|код|его)|сохрани(те)? (код|его|этот)|создай(те)? файл .{0,40}и встав|"
                        r"не могу (физически )?созда|cannot create files|copy (this|the) code", re.I)


def files_in(ws: Path) -> list[str]:
    return sorted(str(p.relative_to(ws)).replace("\\", "/") for p in ws.rglob("*") if p.is_file() and ".git" not in p.parts)


def check(task: dict, ws: Path, desk: Path, text: str, trace: dict) -> list[str]:
    """Список проваленных проверок с объяснением (пусто — задача решена)."""
    fails = []
    files = files_in(ws)
    setup = set((task.get("setup") or {}).keys())
    made = [f for f in files if f not in setup]
    loop_text = "\n".join(m["content"] for m in trace.get("loop", []) if m["role"] == "user")
    for c in task["checks"]:
        kind, _, arg = c.partition(":")
        if kind == "file":
            if not any(fnmatch.fnmatch(f, arg) for f in files):
                fails.append(f"нет файла {arg} (созданы: {', '.join(made[:6]) or 'ничего'})")
        elif kind == "min_kb":
            big = max((ws / f).stat().st_size for f in made) if made else 0
            if big < float(arg) * 1024:
                fails.append(f"слишком маленький результат: {big} байт — это заготовка, а не программа")
        elif kind == "contains":
            if not any(arg in (ws / f).read_text(encoding="utf-8", errors="ignore") for f in made):
                fails.append(f"в коде нет «{arg}»")
        elif kind == "contains_file":
            path, _, needle = arg.partition(":")
            p = ws / path
            if not p.exists() or needle not in p.read_text(encoding="utf-8", errors="ignore"):
                fails.append(f"в {path} нет «{needle}» — ошибка не исправлена")
        elif kind in ("smoke", "smoke_file"):
            pages = [arg] if kind == "smoke_file" else [f for f in made if f.endswith(".html")]
            for f in pages:
                p = ws / f
                if p.exists():
                    prob = ma.smoke_test(p, p.read_text(encoding="utf-8", errors="ignore"))
                    if prob:
                        fails.append(f"{f}: {prob}")
        elif kind == "no_missing_refs":
            for f in made:
                if f.endswith(".html"):
                    p = ws / f
                    prob = ma.missing_refs(p, p.read_text(encoding="utf-8", errors="ignore"))
                    if prob:
                        fails.append(f"{f}: {prob}")
        elif kind == "shortcut":
            if not any(desk.glob("*")):
                fails.append("ярлык на рабочем столе не создан")
        elif kind == "ran_ok":
            if not re.search(r"Код выхода: 0", loop_text):
                fails.append("программа так и не была успешно запущена (<run> с кодом 0)")
        elif kind == "git_commit":
            r = subprocess.run(["git", "-C", str(ws / arg), "log", "--oneline"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0 or not (r.stdout or "").strip():
                fails.append(f"в {arg} нет git-коммита")
        elif kind == "no_files":
            if made:
                fails.append(f"на простой вопрос создал файлы: {', '.join(made[:5])}")
        elif kind == "answer_len":
            lo, hi = map(int, arg.split(":"))
            if not lo <= len(text.strip()) <= hi:
                fails.append(f"длина ответа {len(text.strip())} вне {lo}–{hi}")
        elif kind == "no_copy_paste":
            m = COPY_PASTE.search(text)
            if m:
                fails.append(f"просит пользователя сделать работу руками: «{m.group(0)}»")
    if trace.get("problem"):
        fails.append("сам признал, что не смог исправить: " + trace["problem"].splitlines()[0][:200])
    return fails


def run_task(task: dict, model: str | None) -> dict:
    ws = TMP / "ws" / f"{task['id']}-{int(time.time() * 1000)}"
    desk = ws.parent / (ws.name + "-desktop")
    ws.mkdir(parents=True)
    desk.mkdir()
    for rel, content in (task.get("setup") or {}).items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(content, encoding="utf-8")
    store.save_settings({"agent_workspace": str(ws), "agent_bypass": True, "agent_confirm_danger": True})
    ma.desktop = lambda: desk
    ma.open_path = lambda target: None          # на экзамене окна не открываем
    opts = {"mode": "auto"} if not model else {"mode": "auto", "provider": "ollama", "model": model}
    trace: dict = {}
    text, t0, error = [], time.time(), ""
    try:
        for kind, data in ma.run({"id": "bench", "title": task["id"]}, [], task["prompt"], opts, lambda: False,
                                 ma.Inbox(), trace):
            if kind == "text":
                text.append(str(data))
    except Exception as e:  # noqa: BLE001 — сбой модели тоже результат экзамена
        error = f"{type(e).__name__}: {e}"
    answer = "".join(text)
    fails = ([f"сбой: {error}"] if error else []) + check(task, ws, desk, answer, trace)
    return {"task": task["id"], "model": trace.get("model") or model or "?", "ok": not fails, "fails": fails,
            "seconds": round(time.time() - t0), "steps": trace.get("steps", 0), "answer": answer[-1500:],
            "loop": trace.get("loop", []), "prompt": task["prompt"], "system": trace.get("system", "")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", help="модель Ollama (можно несколько); по умолчанию — выбор Mind")
    ap.add_argument("--only", default="", help="id задач через запятую")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    tasks = [json.loads(l) for l in (HERE / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only.split(",")]
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    for model in args.model or [None]:
        results = []
        for _ in range(args.repeat):
            for t in tasks:
                r = run_task(t, model)
                results.append(r)
                print(f"{'✓' if r['ok'] else '✗'} {r['model']:28} {t['id']:12} {r['seconds']:4}s  "
                      + ("; ".join(r["fails"])[:200] if r["fails"] else ""), flush=True)
                with (DATA / ("solutions.jsonl" if r["ok"] else "critiques.jsonl")).open("a", encoding="utf-8") as f:
                    f.write(json.dumps({k: r[k] for k in ("task", "model", "prompt", "system", "loop", "fails")},
                                       ensure_ascii=False) + "\n")
        name = (results[0]["model"] if results else "none").replace("/", "_").replace(":", "-")
        score = sum(r["ok"] for r in results)
        lines = [f"# Экзамен MindAI — {results[0]['model'] if results else '?'}", "",
                 f"{time.strftime('%Y-%m-%d %H:%M')} · решено **{score} из {len(results)}**", "",
                 "| Задача | Итог | Время | Шагов | Что не так |", "|---|---|---|---|---|"]
        lines += [f"| {r['task']} | {'✓' if r['ok'] else '✗'} | {r['seconds']} с | {r['steps']} | "
                  f"{'; '.join(r['fails']).replace('|', '/')[:300]} |" for r in results]
        (out_dir / f"{stamp}-{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out_dir / f"{stamp}-{name}.json").write_text(json.dumps(
            [{k: v for k, v in r.items() if k not in ("loop", "system")} for r in results],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"== {name}: {score}/{len(results)}", flush=True)


if __name__ == "__main__":
    main()
