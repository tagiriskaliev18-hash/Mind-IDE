#!/usr/bin/env python3
"""Обучить MindAI одной командой: датасет → Soup → модель в Ollama → экзамен.

    python mindai/train.py                         # Qwen3.5-9B, модель «mindai»
    python mindai/train.py --config mindai-sft-14b.yaml --name mindai-14b
    python mindai/train.py --config pipeline-check.yaml --name mindai-check   # быстрая проверка конвейера

Обучение идёт в C:\\Users\\<вы>\\MindAI\\work (не в OneDrive: веса — десятки гигабайт).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_model as bm  # noqa: E402

WORK = Path.home() / "MindAI" / "work"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="mindai-sft.yaml")
    ap.add_argument("--name", default="mindai")
    ap.add_argument("--no-bench", action="store_true")
    args = ap.parse_args()

    subprocess.run([sys.executable, str(HERE / "make_dataset.py")], check=True)
    (WORK / "soup").mkdir(parents=True, exist_ok=True)
    (WORK / "data").mkdir(exist_ok=True)
    for f in ("sft.jsonl", "dpo.jsonl"):
        if (HERE / "data" / f).exists():
            shutil.copy2(HERE / "data" / f, WORK / "data" / f)
    shutil.copy2(HERE / "soup" / args.config, WORK / "soup" / args.config)
    bm.sh([bm.SOUP, "--no-telemetry", "train", "-c", args.config, "-y"], cwd=WORK / "soup")

    out = next(l.split(":", 1)[1].strip() for l in (WORK / "soup" / args.config).read_text(encoding="utf-8").splitlines()
               if l.startswith("output:"))
    bm.sh([sys.executable, HERE / "build_model.py", "--adapter", (WORK / "soup" / out).resolve(), "--name", args.name])
    if not args.no_bench:
        bm.sh([sys.executable, HERE / "bench" / "run_bench.py", "--model", args.name])


if __name__ == "__main__":
    main()
