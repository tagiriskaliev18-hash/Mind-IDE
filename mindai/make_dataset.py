#!/usr/bin/env python3
"""Собирает учебные данные MindAI для Soup из всего, что накопилось.

    python mindai/make_dataset.py

Источники (берётся только проверенное):
  data/distilled.jsonl   решения учителя, прошедшие проверки (distill.py)
  data/solutions.jsonl   задачи экзамена, которые модель решила сама (run_bench.py)
  ~/.config/aisktagos/mindai/trajectories.jsonl (Windows: %APPDATA%\\aisktagos\\mindai)  удачная работа в Mind Studio
  data/critiques.jsonl   провалы — «отвергнутые» ответы для обучения предпочтениям

Результат:
  data/sft.jsonl   {"messages": [...]} — формат chatml для `soup train` (task: sft)
  data/dpo.jsonl   {"prompt", "chosen", "rejected"} — для `soup train` (task: dpo), когда на одну задачу есть и
                   удачное решение, и провал: модель учится не просить копировать код, не бросать ошибки и т. п.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def traces_file() -> Path:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.environ.get("XDG_CONFIG_HOME")
    return Path(base or Path.home() / ".config") / "aisktagos" / "mindai" / "trajectories.jsonl"


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def to_messages(row: dict) -> list[dict] | None:
    loop = row.get("loop") or []
    if not loop or not row.get("system") or not any(m["role"] == "assistant" for m in loop):
        return None
    msgs = [{"role": "system", "content": row["system"]}] + [{"role": m["role"], "content": m["content"]} for m in loop]
    while msgs and msgs[-1]["role"] != "assistant":        # пример должен заканчиваться ответом модели
        msgs.pop()
    return msgs if len(msgs) >= 3 else None


def main() -> None:
    good = [r for r in read(DATA / "distilled.jsonl") if r.get("ok")]
    good += read(DATA / "solutions.jsonl")
    good += [r for r in read(traces_file()) if r.get("ok")]
    bad = read(DATA / "critiques.jsonl")

    seen, sft = set(), []
    for r in good:
        msgs = to_messages(r)
        if not msgs:
            continue
        key = hashlib.sha1(json.dumps(msgs[1:], ensure_ascii=False).encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            sft.append({"messages": msgs})

    by_prompt: dict[str, dict] = {}
    for r in good:
        msgs = to_messages(r)
        if msgs and r.get("prompt", r.get("request")):
            by_prompt.setdefault(r.get("prompt") or r.get("request"), msgs)
    dpo = []
    for r in bad:
        chosen = by_prompt.get(r.get("prompt", ""))
        rejected = to_messages(r)
        if chosen and rejected:
            first = lambda ms: next(m["content"] for m in ms if m["role"] == "assistant")  # noqa: E731
            dpo.append({"prompt": chosen[:2], "chosen": first(chosen), "rejected": first(rejected)})

    DATA.mkdir(exist_ok=True)
    (DATA / "sft.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in sft), encoding="utf-8")
    (DATA / "dpo.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in dpo), encoding="utf-8")
    print(f"SFT: {len(sft)} примеров → {DATA / 'sft.jsonl'}")
    print(f"DPO: {len(dpo)} пар → {DATA / 'dpo.jsonl'}")


if __name__ == "__main__":
    main()
