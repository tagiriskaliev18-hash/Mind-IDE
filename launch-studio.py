#!/usr/bin/env python3
"""Лаунчер Mind Studio из репозитория (Windows и разработка на Linux).

    pythonw launch-studio.py                     окно Mind Studio (так запускает ярлык Mind)
    python  launch-studio.py --link-antigravity  мост к Antigravity — выполнить в терминале Antigravity
    python  launch-studio.py --install-antigravity-autostart  Antigravity сам запускает мост при каждом старте
    python  launch-studio.py --server            только сервер, адрес в консоль

Ошибки старта при запуске без консоли показываются окном и пишутся в studio-launch.log.
"""
import json
import os
import sys
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
LIB_DIR = PROJECT / "overlay" / "usr" / "lib" / "aisktagos"
SHARE = PROJECT / "overlay" / "usr" / "share"

sys.path.insert(0, str(LIB_DIR))
os.environ.setdefault("AISKTAG_TOKENS", str(SHARE / "aisktagos" / "design" / "tokens.json"))
os.environ.setdefault("AISKTAG_CATALOG", str(SHARE / "aisktagos" / "ai" / "models.json"))
os.environ.setdefault("AISKTAG_MIND_ICON", str(PROJECT / "tools" / "windows" / "mind.ico") if sys.platform == "win32"
                      else str(SHARE / "icons" / "hicolor" / "256x256" / "apps" / "aisktagos-mind.jpg"))
py = Path(sys.executable)
if py.name.lower() == "pythonw.exe":
    py = py.with_name("python.exe")
os.environ.setdefault("AISKTAG_STUDIO_LINK_CMD", f'& "{py}" "{Path(__file__).resolve()}" --link-antigravity'
                      if sys.platform == "win32" else f'"{py}" "{Path(__file__).resolve()}" --link-antigravity')

os.environ.setdefault("AISKTAG_STUDIO_SELF", json.dumps([str(py), str(Path(__file__).resolve())]))

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AIsktagOS.MindStudio")
    except (OSError, AttributeError):
        pass


def _fail(text: str) -> None:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.environ.get("XDG_CONFIG_HOME")
    log = Path(base or Path.home() / ".config") / "aisktagos" / "studio-launch.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.platform == "win32" and Path(sys.executable).name.lower() == "pythonw.exe":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"Mind Studio не запустился.\n\n{text[-1500:]}\n\nЖурнал: {log}",
                                         "Mind Studio", 0x10)
    else:
        print(text, file=sys.stderr)


def main() -> int:
    script = LIB_DIR / "aisktag-studio.py"
    sys.argv[0] = str(script)
    try:
        ns = {"__file__": str(script), "__name__": "aisktag_studio"}
        exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), ns)
        return ns["main"]()
    except SystemExit as e:
        return int(e.code or 0)
    except Exception:  # noqa: BLE001 — любая ошибка старта должна быть видна
        _fail(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
