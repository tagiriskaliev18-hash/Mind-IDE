#!/usr/bin/env python3
"""Лаунчер Mind из репозитория — для Windows и для разработки на Linux без сборки образа.

    pythonw launch-mind.py            окно без чёрной консоли (так запускает ярлык на рабочем столе)
    python  launch-mind.py --ask "…"  те же ключи, что у aisktag-mind

Подставляет пути к модулям, дизайн-токенам, каталогу моделей и иконке из репозитория. Если окно не смогло
открыться, причина пишется в mind-launch.log (рядом с настройками Mind) и показывается в окне ошибки:
при запуске через pythonw консоли нет, и без этого ярлык «молча не открывался бы».
"""
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
os.environ.setdefault("AISKTAG_MIND_ICON", str(SHARE / "icons" / "hicolor" / "256x256" / "apps" / "aisktagos-mind.jpg"))

if sys.platform == "win32":
    # Своя группа на панели задач и иконка Mind вместо иконки Python
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AIsktagOS.Mind")
    except (OSError, AttributeError):
        pass


def _log_path() -> Path:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.environ.get("XDG_CONFIG_HOME")
    path = Path(base or Path.home() / ".config") / "aisktagos" / "mind-launch.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fail(text: str) -> None:
    try:
        _log_path().write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"Mind не запустился.\n\n{text[-1500:]}\n\nЖурнал: {_log_path()}",
                                         "Mind", 0x10)
    else:
        print(text, file=sys.stderr)


def main() -> int:
    mind_script = LIB_DIR / "aisktag-mind.py"
    sys.argv[0] = str(mind_script)
    try:
        namespace = {"__file__": str(mind_script), "__name__": "aisktag_mind"}
        exec(compile(mind_script.read_text(encoding="utf-8"), str(mind_script), "exec"), namespace)
        return namespace["main"]()
    except ImportError as e:
        _fail(f"Не хватает библиотеки: {e}\n\nУстановите её: python -m pip install PyQt6\n\n{traceback.format_exc()}")
    except Exception:  # noqa: BLE001 — любая ошибка старта должна быть видна пользователю
        _fail(traceback.format_exc())
    return 1


if __name__ == "__main__":
    sys.exit(main())
