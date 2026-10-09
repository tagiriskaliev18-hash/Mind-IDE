#!/usr/bin/env python3
"""Из обученного LoRA-адаптера Soup — готовая модель в Ollama одной командой.

    python mindai/build_model.py --adapter mindai/soup/output/mindai-sft --name mindai
    python mindai/build_model.py --adapter mindai/soup/output/pipeline-check --name mindai-check --keep-f16

Шаги: soup merge (адаптер + база) → вернуть слой MTP из базы (transformers его теряет, а конвертер llama.cpp и
Ollama требуют для Qwen3.5) → convert_hf_to_gguf (f16) → llama-quantize (q4_k_m) → Modelfile → ollama create.
Проверено на этом компьютере на Qwen3.5-0.8B (README, «Проверка конвейера»).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
VENV = Path(os.environ.get("MINDAI_VENV", HOME / "MindAI" / ".venv"))
PY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
SOUP = VENV / ("Scripts/soup.exe" if sys.platform == "win32" else "bin/soup")
LLAMA = Path(os.environ.get("MINDAI_LLAMA_CPP", HOME / ".soup" / "llama.cpp"))
QUANTIZE = Path(os.environ.get("MINDAI_QUANTIZE", HOME / "MindAI" / "llama-bin" /
                               ("llama-quantize.exe" if sys.platform == "win32" else "llama-quantize")))
SYSTEM = (HERE / "Modelfile").read_text(encoding="utf-8").split('SYSTEM """', 1)[1].split('"""', 1)[0]


def sh(cmd: list, **kw) -> None:
    print("▶", " ".join(map(str, cmd)), flush=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "SOUP_NO_AUDIT_LOG": "1"}
    subprocess.run([str(c) for c in cmd], check=True, env=env, stdin=subprocess.DEVNULL, **kw)


def restore_mtp(merged: Path, base_id: str) -> None:
    """Слои multi-token prediction из исходной базы — в папку слитой модели (отдельным файлом + индекс)."""
    cfg = json.loads((merged / "config.json").read_text(encoding="utf-8"))
    if not cfg.get("mtp_num_hidden_layers"):
        return
    code = f"""
import glob, json, os
from safetensors import safe_open
from safetensors.torch import save_file
snap = glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--{base_id.replace('/', '--')}/snapshots/*/"))[0]
mtp = {{}}
for f in glob.glob(snap + "*.safetensors"):
    with safe_open(f, "pt") as s:
        for k in s.keys():
            if k.startswith("mtp."):
                mtp[k] = s.get_tensor(k)
d = r"{merged}"
if mtp:
    save_file(mtp, os.path.join(d, "model-mtp.safetensors"), metadata={{"format": "pt"}})
    wm = {{}}
    for f in glob.glob(os.path.join(d, "model*.safetensors")):
        if f.endswith("model-mtp.safetensors"):
            continue
        with safe_open(f, "pt") as s:
            wm.update({{k: os.path.basename(f) for k in s.keys()}})
    wm.update({{k: "model-mtp.safetensors" for k in mtp}})
    json.dump({{"metadata": {{}}, "weight_map": wm}}, open(os.path.join(d, "model.safetensors.index.json"), "w"))
print("MTP-тензоров:", len(mtp))
"""
    sh([PY, "-c", code])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="папка LoRA-адаптера после soup train")
    ap.add_argument("--name", default="mindai", help="имя модели в Ollama")
    ap.add_argument("--quant", default="Q4_K_M")
    ap.add_argument("--keep-f16", action="store_true")
    args = ap.parse_args()
    adapter = Path(args.adapter).resolve()
    merged = adapter.with_name(adapter.name + "-merged")
    f16 = adapter.with_name(adapter.name + "-f16.gguf")
    out = adapter.with_name(f"{adapter.name}-{args.quant.lower()}.gguf")
    base_id = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))["base_model_name_or_path"]

    if not merged.exists():
        sh([SOUP, "--no-telemetry", "merge", "--adapter", adapter, "--output", merged])
    restore_mtp(merged, base_id)
    sh([PY, LLAMA / "convert_hf_to_gguf.py", merged, "--outfile", f16, "--outtype", "f16"])
    sh([QUANTIZE, f16, out, args.quant])
    if not args.keep_f16:
        f16.unlink()
    modelfile = adapter.with_name(f"Modelfile.{args.name}")
    modelfile.write_text(f'FROM ./{out.name}\nRENDERER qwen3.5\nPARSER qwen3.5\nPARAMETER num_ctx 16384\n'
                         f'PARAMETER temperature 0.3\nPARAMETER top_p 0.9\nPARAMETER top_k 20\n'
                         f'SYSTEM """{SYSTEM}"""\n', encoding="utf-8")
    sh(["ollama", "create", args.name, "-f", modelfile.name], cwd=modelfile.parent)
    print(f"Готово: ollama run {args.name}. Экзамен: python mindai/bench/run_bench.py --model {args.name}")


if __name__ == "__main__":
    main()
