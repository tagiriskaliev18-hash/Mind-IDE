"""Mind-агент: модель не просит пользователя копировать код, а сама делает работу на компьютере.

Модель пишет действия прямо в ответе простыми тегами — так надёжнее всего работают и маленькие локальные
модели Ollama (qwen2.5-coder:7b, qwen3.5:4b), у которых «настоящие» tool calls часто ломаются:

    <write path="game/index.html">…</write>   <run>git status</run>   <shortcut name="Игра" target="game/index.html"/>

Действие выполняется, как только тег закрыт, а в чате появляется строка о сделанном шаге (как в Claude Code
и Antigravity). Результаты возвращаются модели, и цикл идёт, пока ей есть что делать. Сообщения, которые
пользователь пишет во время работы, попадают в задачу между шагами (Inbox).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import aisktag_ai as ai

from . import store

Event = tuple[str, object]
IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_STEPS = 14
TAGS = ("write", "run", "read", "list", "mkdir", "shortcut", "open", "deploy", "github")
HIDDEN = ("think",)            # размышления «думающих» моделей в чат не выводим

OLLAMA_OPTIONS = {"num_ctx": 16384}   # иначе Ollama режет контекст до 4К и файл обрывается


# ---------------------------------------------------------------------------
# Настройки агента
# ---------------------------------------------------------------------------

def desktop() -> Path:
    if IS_WINDOWS:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)          # учитывает перенос рабочего стола в OneDrive
        except (OSError, AttributeError):
            pass
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def options() -> dict:
    st = store.settings()
    return {"bypass": st.get("agent_bypass", True), "confirm_danger": st.get("agent_confirm_danger", True),
            "workspace": st.get("agent_workspace") or str(Path.home() / "MindProjects")}


# ---------------------------------------------------------------------------
# Системный промпт
# ---------------------------------------------------------------------------

def system_prompt(workspace: Path) -> str:
    shell = "PowerShell (Windows)" if IS_WINDOWS else "bash (Linux)"
    return f"""Ты Mind — агент-программист AIsktagOS. Ты сам делаешь работу на компьютере пользователя: создаёшь папки, проекты и файлы, запускаешь команды, делаешь ярлыки на рабочем столе, работаешь с git и публикуешь сайты.

Главное правило: НИКОГДА не проси пользователя скопировать код, сохранить файл или выполнить команду руками и не говори, что не можешь создавать файлы, — ты можешь, делай это сам действиями ниже. Не показывай содержимое файлов в ответе: сразу записывай его в файлы.

Философия: максимальная простота для кодинга. Самое простое рабочее решение, минимум файлов и зависимостей. Сайт или игра — один файл index.html со встроенными CSS и JavaScript, без сборщиков и npm, если об этом не просят. Без лишних вопросов: недостающие детали выбирай сам разумно.

Действия (пиши их прямо в ответе, каждое выполняется сразу):
<write path="папка/файл">полное содержимое файла</write> — создать или перезаписать файл (папки создаются сами)
<run>команда</run> — выполнить команду {shell} в рабочей папке; не запускай серверы, которые не завершаются
<read path="файл"/> — прочитать файл
<list path="папка"/> — посмотреть содержимое папки
<mkdir path="папка"/> — создать папку
<shortcut name="Название" target="папка/файл"/> — ярлык на рабочем столе
<open target="папка/файл или https://адрес"/> — открыть файл, папку или сайт
<deploy dir="папка" name="имя-сайта"/> — бесплатно опубликовать сайт в интернете (Surge), вернёт адрес
<github dir="папка" name="имя-репозитория"/> — git init, коммит и публикация на GitHub

Рабочая папка: {workspace}. Относительные пути считаются от неё; каждый новый проект — в своей папке с короткими латинскими именами.
Рабочий стол: {desktop()}

Качество: ты создаёшь полноценные рабочие программы, а не заготовки. Вся логика доведена до конца: игра перемешивается, реагирует на клики, обновляет экран, считает ходы, показывает победу и кнопку «Заново». Красивый современный дизайн: шрифт -apple-system, мягкие тени, скругления, плавные анимации, адаптивность под телефон. Перед записью мысленно проверь, что код работает.

Как работать:
1. Одна короткая фраза о том, что делаешь. Для программы или игры — ещё 3–6 коротких пунктов плана: что умеет, какое состояние хранит, какие функции его меняют и перерисовывают экран. Затем сразу действия, реализуя каждый пункт.
2. После действий ты получишь их результаты. Если что-то не получилось — исправь сам.
3. Когда всё готово — коротко скажи, что сделано и где лежит, уже без действий.
4. Сделал сайт — предложи одной фразой бесплатно опубликовать его в интернете; если пользователь согласен или сам просит — используй <deploy>.
5. Если пользователь просто задаёт вопрос — просто ответь, без действий.
Отвечай на языке пользователя (обычно по-русски). Никогда не пиши за систему или пользователя — результаты действий придут сами.

Пример ответа на просьбу «сделай сайт-секундомер и ярлык на рабочем столе»:
Создаю сайт-секундомер.
<write path="stopwatch/index.html"><!DOCTYPE html>
<html lang="ru">
…полный рабочий код страницы со стилями и скриптом…
</html></write>
<shortcut name="Секундомер" target="stopwatch/index.html"/>
<open target="stopwatch/index.html"/>

А после результатов: «Готово: секундомер в папке stopwatch, ярлык «Секундомер» на рабочем столе. Опубликовать его в интернете бесплатно?»"""


NUDGE = ("Ты показал код, но не выполнил действия. Не показывай код пользователю — запиши файлы через "
         "<write path=\"папка/файл\">…</write> и выполни остальное (ярлык, команды) действиями. Начинай сразу.")


# ---------------------------------------------------------------------------
# Разбор потока: текст отдаём в чат сразу, теги действий — вырезаем и выполняем
# ---------------------------------------------------------------------------

_OPEN = re.compile(r"<(%s)\b([^<>]*?)(/?)>" % "|".join(TAGS + HIDDEN), re.I)
_ATTR = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'/>]+))""")
_ALL = TAGS + HIDDEN


def parse_attrs(s: str) -> dict:
    return {m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None) for m in _ATTR.finditer(s)}


class ActionStream:
    """Потоковый разбор: feed(кусок) → события ("text", s) | ("start", tag, attrs) | ("action", tag, attrs, body)."""

    def __init__(self):
        self.buf = ""
        self.inside: tuple[str, dict] | None = None

    def _maybe_tag_prefix(self, s: str) -> bool:
        """Начало строки может оказаться открывающим тегом, который ещё не дописан."""
        if ">" in s:
            return False
        low = s[1:].lower()
        return any(t.startswith(low) or (low.startswith(t) and (len(low) == len(t) or not low[len(t)].isalnum()))
                   for t in _ALL)

    def feed(self, chunk: str) -> list[tuple]:
        self.buf += chunk
        out: list[tuple] = []
        while self.buf:
            if self.inside:
                tag, attrs = self.inside
                m = re.search(r"</%s\s*>" % tag, self.buf, re.I)
                if not m:
                    break
                body = self.buf[:m.start()]
                self.buf = self.buf[m.end():]
                self.inside = None
                if tag not in HIDDEN:
                    out.append(("action", tag, attrs, body))
                continue
            i = self.buf.find("<")
            if i < 0:
                out.append(("text", self.buf))
                self.buf = ""
                break
            if i:
                out.append(("text", self.buf[:i]))
                self.buf = self.buf[i:]
            m = _OPEN.match(self.buf)
            if m:
                tag, attrs, closed = m.group(1).lower(), parse_attrs(m.group(2)), bool(m.group(3))
                self.buf = self.buf[m.end():]
                if closed:
                    if tag not in HIDDEN:
                        out.append(("action", tag, attrs, ""))
                else:
                    self.inside = (tag, attrs)
                    if tag not in HIDDEN:
                        out.append(("start", tag, attrs))
                continue
            if self._maybe_tag_prefix(self.buf):
                break
            out.append(("text", "<"))
            self.buf = self.buf[1:]
        return out

    def flush(self) -> list[tuple]:
        """Конец ответа. Незакрытый тег (маленькие модели иногда забывают </write>) — выполняем как есть."""
        out: list[tuple] = []
        if self.inside:
            tag, attrs = self.inside
            if tag not in HIDDEN:
                out.append(("action", tag, attrs, self.buf))
        elif self.buf:
            out.append(("text", self.buf))
        self.buf, self.inside = "", None
        return out


def clean_body(body: str) -> str:
    """Содержимое файла без обёртки ```lang … ``` и лишних пустых строк по краям."""
    b = body.strip("\r\n")
    m = re.fullmatch(r"\s*```[\w+#.-]*[ \t]*\r?\n(.*?)\r?\n?```\s*", b, re.S)
    if m:
        b = m.group(1)
    return b.rstrip() + "\n"


_FENCE = re.compile(r"```([\w+#.-]*)[ \t]*\r?\n(.*?)```", re.S)
_DEFAULT_NAME = {"html": "index.html", "css": "style.css", "javascript": "script.js", "js": "script.js",
                 "python": "main.py", "py": "main.py", "typescript": "main.ts", "json": "data.json"}
_FNAME = re.compile(r"([\w./\\-]+\.(?:html?|css|js|mjs|ts|tsx|jsx|py|json|md|txt|sh|ps1|java|go|rs|c|cpp|h))", re.I)
_CREATE = re.compile(r"созда|сдела|напиши|напиш|разработ|собери|сгенер|create|build|make|write|generate", re.I)


def code_blocks_as_files(text: str, folder: str) -> list[tuple[str, str]]:
    """Модель всё же выдала код блоками — превращаем блоки в файлы (имя ищем в тексте перед блоком)."""
    files: list[tuple[str, str]] = []
    for m in _FENCE.finditer(text):
        lang, code = m.group(1).lower(), m.group(2)
        if len(code.strip()) < 40 or lang in ("bash", "sh", "powershell", "ps", "cmd", "shell", "console", "text"):
            continue
        before = text[max(0, m.start() - 200):m.start()]
        names = _FNAME.findall(before)
        name = names[-1] if names else _DEFAULT_NAME.get(lang)
        if not name or any(n == name for n, _ in files):
            continue
        files.append((f"{folder}/{Path(name.replace(chr(92), '/')).name}", code))
    return files


_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               "a b v g d e e zh z i y k l m n o p r s t u f h ts ch sh sch - y - e yu ya".split()))


def slug(text: str, default: str = "project") -> str:
    s = "".join(_TR.get(c, c) for c in text.lower()).replace("-", " ")
    words = re.findall(r"[a-z0-9]+", s)
    stop = {"sozday", "sdelay", "napishi", "mne", "please", "na", "s", "i", "v", "dlya", "create", "make", "a", "the"}
    words = [w for w in words if w not in stop][:3]
    return "-".join(words) or default


# ---------------------------------------------------------------------------
# Выполнение действий
# ---------------------------------------------------------------------------

_DANGER = re.compile(
    r"\brm\s+-[a-z]*r|\bRemove-Item\b.*-Recurse|\b(del|erase)\s+/[sq]|\brd\s+/s|\brmdir\s+/s|\bformat\s+[a-z]:"
    r"|\bgit\s+push\b.*(--force|-f\b)|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*f|\bshutdown\b|\bdiskpart\b"
    r"|\breg\s+delete\b|Remove-ItemProperty|\bmkfs\b|\bdd\s+if=|Clear-RecycleBin|\bcipher\s+/w", re.I)


class Executor:
    def __init__(self, workspace: Path, opts: dict, should_stop: Callable[[], bool]):
        self.ws = workspace
        self.opts = opts
        self.should_stop = should_stop
        self.touched: set[Path] = set()          # папки проектов, созданные в этом разговоре

    # --- пути
    def path(self, p: str) -> Path:
        p = os.path.expandvars(os.path.expanduser((p or ".").strip().strip("\"'")))
        q = Path(p)
        if not q.is_absolute() and q.parts and q.parts[0].lower() == self.ws.name.lower():
            q = Path(*q.parts[1:]) if len(q.parts) > 1 else Path(".")   # модель повторила имя рабочей папки
        return (q if q.is_absolute() else self.ws / q).resolve()

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.ws)).replace("\\", "/")
        except ValueError:
            return str(p)

    def inside(self, p: Path) -> bool:
        return p == self.ws or self.ws in p.parents

    # --- нужно ли подтверждение пользователя
    def needs_confirm(self, tag: str, attrs: dict, body: str) -> str:
        """Текст причины, если действие требует подтверждения; пустая строка — можно выполнять."""
        if tag in ("read", "list", "open", "mkdir", "shortcut"):
            return ""
        if tag == "write":
            target = self.path(attrs.get("path") or attrs.get("file") or "")
            if not self.inside(target) and target.exists() and (self.opts["confirm_danger"] or not self.opts["bypass"]):
                return f"перезаписать существующий файл вне рабочей папки: {target}"
            return ""
        if tag == "run" and _DANGER.search(body) and (self.opts["confirm_danger"] or not self.opts["bypass"]):
            return f"выполнить необратимую команду: {body.strip()[:200]}"
        if not self.opts["bypass"] and tag in ("run", "deploy", "github"):
            return {"run": f"выполнить команду: {body.strip()[:200]}",
                    "deploy": f"опубликовать папку {attrs.get('dir', '')} в интернете",
                    "github": f"опубликовать папку {attrs.get('dir', '')} на GitHub"}[tag]
        return ""

    # --- действия: каждое возвращает (строка для чата, результат для модели)
    def do(self, tag: str, attrs: dict, body: str) -> tuple[str, str]:
        try:
            return getattr(self, "_" + tag)(attrs, body)
        except Exception as e:  # noqa: BLE001 — модель увидит ошибку и попробует исправить
            return f"⚠️ {tag}: {e}", f"ОШИБКА {tag}: {e}"

    def _write(self, a: dict, body: str) -> tuple[str, str]:
        target = self.path(a.get("path") or a.get("file") or a.get("name") or "")
        if target.is_dir() or not target.name:
            raise ValueError("укажите путь к файлу в path")
        text = clean_body(body)
        if _PLACEHOLDER.search(text) and len(text) < 3000:
            raise ValueError("в файле заглушка вместо кода — запиши файл целиком, с полным рабочим кодом")
        existed = target.exists()
        if existed and target.stat().st_size > 1500 and len(text.encode()) < target.stat().st_size * 0.3:
            raise ValueError(f"новая версия {target.name} подозрительно короткая ({len(text.encode())} байт вместо "
                             f"{target.stat().st_size}) — файл не тронут. Пиши файл целиком")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
        self._mark(target)
        lines = text.count("\n")
        verb = "Обновил" if existed else "Создал"
        shown = f"📄 {verb} файл `{self.rel(target)}` ({lines} строк)"
        res = f"✓ файл записан: {target} ({len(text.encode())} байт, {lines} строк)"
        problem = "\n".join(p for p in (check_js(target, text) or smoke_test(target, text),
                                        missing_refs(target, text)) if p)
        if problem:
            shown += "\n⚠️ Нашёл ошибку — исправляю"
            res += f"\nОШИБКА в этом файле, исправь и перезапиши файл целиком:\n{problem}"
        return shown, res

    def _mkdir(self, a: dict, body: str) -> tuple[str, str]:
        target = self.path(a.get("path") or body)
        target.mkdir(parents=True, exist_ok=True)
        self._mark(target / "x")
        return f"📁 Создал папку `{self.rel(target)}`", f"✓ папка: {target}"

    def _read(self, a: dict, body: str) -> tuple[str, str]:
        target = self.path(a.get("path") or body)
        text = target.read_text(encoding="utf-8", errors="replace")
        cut = text[:12000] + ("\n…(обрезано)" if len(text) > 12000 else "")
        return f"👀 Прочитал `{self.rel(target)}`", f"Содержимое {target}:\n{cut}"

    def _list(self, a: dict, body: str) -> tuple[str, str]:
        target = self.path(a.get("path") or body or ".")
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]
        listing = "\n".join(("📁 " if p.is_dir() else "   ") + p.name for p in items) or "(пусто)"
        return f"👀 Посмотрел папку `{self.rel(target)}`", f"Папка {target}:\n{listing}"

    def _run(self, a: dict, body: str) -> tuple[str, str]:
        cmd = body.strip()
        if cmd.startswith("```"):
            cmd = clean_body(cmd).strip()
        if not cmd:
            raise ValueError("пустая команда")
        cwd = self.path(a.get("cwd") or ".")
        cwd.mkdir(parents=True, exist_ok=True)
        timeout = min(int(a.get("timeout") or 300), 1800)
        code, out = run_shell(cmd, cwd, timeout, self.should_stop)
        tail = out.strip()[-3500:]
        short = "\n".join(tail.splitlines()[-12:])
        mark = "▶" if code == 0 else "⚠️"
        shown = f"{mark} Выполнил `{cmd.splitlines()[0][:120]}`" + ("" if code == 0 else f" — код {code}")
        if short:
            shown += f"\n```\n{short}\n```"
        return shown, f"Команда: {cmd}\nКод выхода: {code}\nВывод:\n{tail or '(пусто)'}"

    def _shortcut(self, a: dict, body: str) -> tuple[str, str]:
        raw = a.get("target") or a.get("path") or body.strip()
        is_url = bool(re.match(r"https?://", raw))
        target = raw if is_url else str(self.path(raw))
        if not is_url and not Path(target).exists():
            raise FileNotFoundError(f"нет такого файла: {target}")
        name = re.sub(r'[\\/:*?"<>|]', "", a.get("name") or Path(target).stem or "Mind").strip() or "Mind"
        lnk = make_shortcut(name, target, a.get("icon", ""))
        return f"🔗 Создал ярлык «{name}» на рабочем столе", f"✓ ярлык создан: {lnk} → {target}"

    def _open(self, a: dict, body: str) -> tuple[str, str]:
        raw = a.get("target") or a.get("path") or a.get("url") or body.strip()
        target = raw if re.match(r"https?://", raw) else str(self.path(raw))
        if not re.match(r"https?://", target) and not Path(target).exists():
            raise FileNotFoundError(f"нет такого файла: {target}")
        open_path(target)
        return f"🚀 Открыл `{self.rel(Path(target)) if not target.startswith('http') else target}`", f"✓ открыто: {target}"

    def _deploy(self, a: dict, body: str) -> tuple[str, str]:
        folder = self.path(a.get("dir") or a.get("path") or ".")
        if not folder.is_dir():
            raise FileNotFoundError(f"нет такой папки: {folder}")
        return deploy_surge(folder, a.get("name") or a.get("domain") or folder.name, self.should_stop)

    def _github(self, a: dict, body: str) -> tuple[str, str]:
        folder = self.path(a.get("dir") or a.get("path") or ".")
        if not folder.is_dir():
            raise FileNotFoundError(f"нет такой папки: {folder}")
        return publish_github(folder, a.get("name") or folder.name, a.get("private", "") in ("1", "true", "yes"),
                              self.should_stop)

    def _mark(self, file: Path) -> None:
        if self.inside(file.parent) and file.parent != self.ws:
            top = self.ws / file.relative_to(self.ws).parts[0]
            self.touched.add(top)


# ---------------------------------------------------------------------------
# Системные помощники: команды, ярлыки, открытие, хостинг, GitHub
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"полный (рабочий )?код|ваш код здесь|your code here|//\s*\.\.\.\s*$|<!--\s*\.\.\.\s*-->", re.I | re.M)
_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*type=[\"'](?:module|text/babel|application/json))[^>]*>(.*?)</script>",
                     re.S | re.I)


def check_js(target: Path, text: str) -> str:
    """Синтаксическая проверка JavaScript через Node (если он есть): маленькие модели часто ошибаются в скобках."""
    node = shutil.which("node")
    suffix = target.suffix.lower()
    if not node or suffix not in (".html", ".htm", ".js"):
        return ""
    code = text if suffix == ".js" else "\n;\n".join(_SCRIPT.findall(text))
    if not code.strip():
        return ""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([node, "--check", f.name], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        os.unlink(f.name)
    if r.returncode == 0:
        return ""
    err = r.stderr.replace(f.name, "script").strip()
    return "\n".join(err.splitlines()[:8])


SMOKE_JS = Path(__file__).resolve().parent / "smoke.js"


def smoke_test(target: Path, text: str) -> str:
    """Запускает страницу в поддельном DOM (smoke.js), кликает по всему кликабельному: падает ли код, оживает ли."""
    node = shutil.which("node")
    if not node or target.suffix.lower() not in (".html", ".htm") or "<script" not in text.lower():
        return ""
    try:
        r = subprocess.run([node, str(SMOKE_JS), str(target)], capture_output=True, text=True, timeout=40,
                           creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        rep = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    probs = [f"ошибка при запуске: {e}" for e in rep.get("errors", [])]
    if rep.get("clickable") and not rep.get("changed"):
        probs.append(f"нажал на все {rep['clickable']} кликабельных элемента — страница никак не изменилась: обработчики "
                     "кликов не меняют состояние или не перерисовывают экран")
    if not rep.get("clickable") and re.search(r"игр|game|puzzle|пазл|головолом", text, re.I) and not rep.get("changed"):
        probs.append("на странице нет ни одного элемента, реагирующего на клик или клавиши — игра не управляется")
    return "\n".join(probs)


_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)["']|url\(\s*["']?([^"')#?]+)["']?\s*\)""", re.I)


def missing_refs(target: Path, text: str) -> str:
    """Ссылки страницы на локальные файлы (картинки, стили, скрипты), которых нет: модель их выдумала."""
    if target.suffix.lower() not in (".html", ".htm", ".css"):
        return ""
    missing = []
    for m in _REF.finditer(text):
        ref = (m.group(1) or m.group(2) or "").strip()
        if not ref or re.match(r"(?i)(https?:|data:|mailto:|tel:|javascript:|//|\$\{|blob:)", ref) or ref == "/":
            continue
        if not (target.parent / ref.lstrip("/")).exists() and ref not in missing:
            missing.append(ref)
    if not missing:
        return ""
    return ("Страница ссылается на файлы, которых нет: " + ", ".join(missing[:8]) +
            ". Не используй внешние картинки: рисуй средствами CSS, SVG, emoji или canvas прямо в файле, "
            "либо создай эти файлы.")


def run_shell(cmd: str, cwd: Path, timeout: int, should_stop: Callable[[], bool] | None = None) -> tuple[int, str]:
    if IS_WINDOWS:
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + cmd]
    else:
        argv = ["bash", "-lc", cmd]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "CI": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW)
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.extend(iter(lambda: proc.stdout.read(4096), b"")), daemon=True)
    reader.start()
    deadline = time.time() + timeout
    while proc.poll() is None:
        if time.time() > deadline or (should_stop and should_stop()):
            _kill_tree(proc)
            reader.join(2)
            why = "остановлено пользователем" if should_stop and should_stop() else f"превышено время {timeout} с"
            return -1, b"".join(chunks).decode("utf-8", errors="replace") + f"\n({why})"
        time.sleep(0.15)
    reader.join(5)
    proc.stdout.close()
    return proc.returncode, b"".join(chunks).decode("utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()


def make_shortcut(name: str, target: str, icon: str = "") -> Path:
    desk = desktop()
    desk.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        if target.startswith("http"):
            lnk = desk / f"{name}.url"
            lnk.write_text(f"[InternetShortcut]\nURL={target}\n", encoding="utf-8")
            return lnk
        lnk = desk / f"{name}.lnk"
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MIND_LNK); $s.TargetPath=$env:MIND_TARGET; "
              "$s.WorkingDirectory=$env:MIND_DIR; if($env:MIND_ICON){$s.IconLocation=$env:MIND_ICON}; $s.Save()")
        env = {**os.environ, "MIND_LNK": str(lnk), "MIND_TARGET": target, "MIND_DIR": str(Path(target).parent),
               "MIND_ICON": icon}
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                           capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        if r.returncode != 0 or not lnk.exists():
            raise OSError(f"ярлык не создан: {(r.stderr or r.stdout).strip()[:200]}")
        return lnk
    lnk = desk / f"{name}.desktop"
    exec_line = f'xdg-open "{target}"'
    lnk.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\nIcon={icon or 'text-html'}\n"
                   "Terminal=false\n", encoding="utf-8")
    lnk.chmod(0o755)
    return lnk


def open_path(target: str) -> None:
    if IS_WINDOWS:
        os.startfile(target)   # noqa: S606 — открыть в приложении по умолчанию
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _visible_console(cmd: str, cwd: Path) -> None:
    """Окно терминала для разового входа (Surge, GitHub) — пароль пользователь вводит сам, Mind его не видит."""
    if IS_WINDOWS:
        subprocess.Popen(["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                         cwd=str(cwd), creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        for term in ("konsole", "x-terminal-emulator", "gnome-terminal", "xterm"):
            if shutil.which(term):
                subprocess.Popen([term, "-e", "bash", "-lc", cmd + "; exec bash"], cwd=str(cwd))
                return
        raise OSError("не найден терминал")


def _surge_cmd() -> list[str]:
    exe = shutil.which("surge")
    if exe:
        return [exe]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "surge"]
    raise OSError("нужен Node.js (https://nodejs.org) — с ним Mind публикует сайты бесплатно через Surge")


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'" if IS_WINDOWS else "'" + s.replace("'", "'\\''") + "'"


def deploy_surge(folder: Path, name: str, should_stop=None) -> tuple[str, str]:
    domain = name if name.endswith(".surge.sh") else slug(name, "mind-site") + ".surge.sh"
    surge = _surge_cmd()
    env = {**os.environ, "CI": "1"}
    who = subprocess.run(surge + ["whoami"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                         env=env, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    if "not authenticated" in (who.stdout + who.stderr).lower() and not os.environ.get("SURGE_TOKEN"):
        _visible_console("surge " + _q(str(folder)) + " " + _q(domain), folder)
        return (f"🌐 Открыл окно Surge: введите там email и пароль один раз (аккаунт бесплатный и создаётся сразу). "
                f"После этого сайт появится на https://{domain}, а дальше Mind публикует сам.",
                f"Пользователь ещё не вошёл в Surge: открыто окно входа, после входа сайт будет на https://{domain}. "
                "Скажи пользователю ввести email и пароль в открывшемся окне.")
    for attempt in range(2):
        r = subprocess.run(surge + [str(folder), domain], capture_output=True, text=True, timeout=300,
                           stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW, encoding="utf-8",
                           errors="replace")
        out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
        if r.returncode == 0 and "success" in out.lower():
            return f"🌐 Опубликовал сайт: https://{domain}", f"✓ сайт опубликован: https://{domain}"
        if attempt == 0 and re.search(r"do not have permission|already|taken", out, re.I):
            domain = domain.replace(".surge.sh", f"-{int(time.time()) % 100000}.surge.sh")
            continue
        raise OSError("Surge не опубликовал сайт: " + out.strip()[-400:])
    raise OSError("Surge не опубликовал сайт")


def publish_github(folder: Path, name: str, private: bool, should_stop=None) -> tuple[str, str]:
    steps = []
    git = lambda *a: subprocess.run(["git", *a], cwd=str(folder), capture_output=True, text=True, timeout=120,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, encoding="utf-8",
                                    errors="replace")
    if not (folder / ".git").exists():
        git("init", "-b", "main")
        steps.append("git init")
    git("add", "-A")
    c = git("commit", "-m", "Mind: обновление проекта")
    if c.returncode == 0:
        steps.append("коммит")
    gh = shutil.which("gh")
    if not gh:
        return ("📦 Сделал " + ", ".join(steps or ["ничего нового"]) + ". Для публикации на GitHub нужен GitHub CLI "
                "(winget install GitHub.cli).", "git готов локально; GitHub CLI не установлен")
    auth = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    if auth.returncode != 0:
        _visible_console("gh auth login --web --git-protocol https", folder)
        return ("📦 Сделал " + ", ".join(steps or ["коммит"]) + ". Открыл окно входа в GitHub: войдите один раз, "
                "и Mind будет публиковать сам.", "Пользователь не вошёл в GitHub: открыто окно gh auth login. "
                "Попроси войти и потом повтори <github>.")
    remote = git("remote", "get-url", "origin")
    if remote.returncode != 0:
        repo = slug(name, "mind-project")
        r = subprocess.run([gh, "repo", "create", repo, "--private" if private else "--public", "--source", str(folder),
                            "--remote", "origin", "--push"], capture_output=True, text=True, timeout=300,
                           creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise OSError("gh repo create: " + (r.stderr or r.stdout).strip()[-300:])
        url = (re.findall(r"https://github\.com/\S+", r.stdout + r.stderr) or [repo])[0]
    else:
        p = git("push", "-u", "origin", "HEAD")
        if p.returncode != 0:
            raise OSError("git push: " + (p.stderr or p.stdout).strip()[-300:])
        url = remote.stdout.strip()
    return f"📦 Опубликовал на GitHub ({', '.join(steps + ['push'])}): {url}", f"✓ GitHub: {url}"


# ---------------------------------------------------------------------------
# Входящие во время работы
# ---------------------------------------------------------------------------

class Inbox:
    """Сообщения пользователя, пришедшие, пока агент работает. Закрывается атомарно, чтобы ничего не потерять."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items: list[str] = []
        self.open = True

    def put(self, text: str) -> bool:
        with self.lock:
            if not self.open:
                return False
            self.items.append(text)
            return True

    def drain(self, close_if_empty: bool = False) -> list[str]:
        with self.lock:
            got, self.items = self.items, []
            if close_if_empty and not got:
                self.open = False
            return got


# ---------------------------------------------------------------------------
# Цикл агента
# ---------------------------------------------------------------------------

_ROLEPLAY = re.compile(r"^\s*\**(Система|System|Результаты|Results|Ты|Пользователь|User|Assistant)\**\s*:", re.I)
_ROLEPLAY_ANY = re.compile(r"\n\s*\**(?:Система|System|Результаты|Results|Ты|Пользователь|User|Assistant)\**\s*:", re.I)
_YES = re.compile(r"^\s*(да|ага|давай|подтвержда\w*|выполн\w*|ок|ok|okay|yes|y|конечно|можно|go)\b", re.I)


def run(chat: dict, history: list[dict], user_text: str, opts: dict, should_stop: Callable[[], bool],
        inbox: Inbox | None = None) -> Iterator[Event]:
    """Поток событий как у остальных агентов + ("inject", текст) — сообщение, пришедшее во время работы."""
    aopts = options()
    ws = Path(chat.get("project_path") or aopts["workspace"]).expanduser()
    ws.mkdir(parents=True, exist_ok=True)
    ex = Executor(ws.resolve(), aopts, should_stop)

    system = system_prompt(ex.ws)
    if opts.get("system"):
        system += "\n\n" + opts["system"]
    loop: list[dict] = [{"role": "user", "content": user_text}]

    # Подтверждение отложенных действий из прошлого ответа
    pending = chat.pop("pending_actions", None)
    if pending:
        if _YES.match(user_text):
            results = []
            for p in pending:
                shown, res = ex.do(p["tag"], p["attrs"], p["body"])
                yield "text", shown + "\n\n"
                results.append(res)
            loop = [{"role": "user", "content": f"{user_text}\n\nПользователь подтвердил. Результаты:\n" + "\n".join(results)}]
        else:
            loop = [{"role": "user", "content": f"{user_text}\n\n(Пользователь не подтвердил отложенные действия — "
                                                 "они отменены.)"}]

    cfg = ai.load_config()
    cfg["ollama_options"] = OLLAMA_OPTIONS
    if opts.get("provider") not in ai.PROVIDERS and not opts.get("tier") == "deep":
        local = agent_model(cfg)
        if local:      # бесплатных облачных моделей нет — берём лучшую для агента локальную модель Ollama
            opts = {**opts, "provider": "ollama", "model": local}
    if opts.get("provider") in ai.PROVIDERS:
        name = opts["provider"]
        cfg.update(provider=name, base_url=ai.PROVIDERS[name]["base_url"],
                   model=opts.get("model") or ai.provider_model(name, "code"), api_key=ai.provider_key(name, cfg))
    tier = "deep" if opts.get("tier") == "deep" else "code"
    wants_build = bool(_CREATE.search(user_text))
    nudged = False
    routed = False
    last_actions: list[str] = []
    reviewed: set[str] = set()     # html/js-файлы, которые модель уже перепроверила
    prev_text = ""
    broken = ""                    # в записанном файле нашлась ошибка — ждём исправленную версию
    fix_nudges = 0
    sites: list[str] = []          # папки сайтов, сделанных в этом ответе (предложим бесплатный хостинг)
    deployed = False

    for _step in range(MAX_STEPS):
        if should_stop():
            return
        msgs = [{"role": "system", "content": system}] + history[-4:] + _compact(loop)
        routes: list[dict] = []
        parser = ActionStream()
        raw: list[str] = []
        results: list[str] = []
        actions: list[str] = []
        blocked: list[dict] = []
        line = [""]          # текст отдаём построчно, чтобы вовремя заметить, что модель пишет «за систему»
        cut = [False]
        hold = [_step > 0]   # итог после действий придерживаем: модель любит повторять уже сказанное
        held: list[str] = []
        said: list[str] = []
        web: list[str] = []

        def flush_line(final: bool = False) -> Iterator[Event]:
            while "\n" in line[0] or (final and line[0]):
                head, sep, rest = line[0].partition("\n")
                if _ROLEPLAY.match(head):
                    cut[0] = True
                    line[0] = ""
                    return
                line[0] = rest
                said.append(head + sep)
                if hold[0]:
                    held.append(head + sep)
                else:
                    yield "text", head + sep

        def handle(evs) -> Iterator[Event]:
            for ev in evs:
                if cut[0]:
                    return
                if ev[0] == "text":
                    line[0] += ev[1]
                    yield from flush_line()
                    continue
                yield from flush_line(final=True)
                if cut[0]:
                    return
                if hold[0]:
                    hold[0] = False
                    for h in held:
                        yield "text", h
                    held.clear()
                if ev[0] == "start":
                    tag, attrs = ev[1], ev[2]
                    where = attrs.get("path") or attrs.get("target") or attrs.get("dir") or ""
                    if where and not where.startswith("http"):
                        where = ex.rel(ex.path(where))
                    yield "activity", {"write": "Пишу файл", "run": "Выполняю команду"}.get(tag, tag) + \
                        (f" {where}" if where else "")
                else:
                    _, tag, attrs, body = ev
                    actions.append(f"{tag}:{json.dumps(attrs, sort_keys=True)}:{hash(body)}")
                    why = ex.needs_confirm(tag, attrs, body)
                    if why:
                        blocked.append({"tag": tag, "attrs": attrs, "body": body, "why": why})
                        continue
                    shown, res = ex.do(tag, attrs, body)
                    results.append(res)
                    if tag == "write" and res.startswith("✓") and \
                            re.search(r"\.(html?|js)$", attrs.get("path") or attrs.get("file") or "", re.I):
                        web.append(attrs.get("path") or attrs.get("file"))
                    yield "text", "\n\n" + shown + "\n\n"

        for piece in ai.stream_chat(msgs, cfg, tier=tier, should_stop=should_stop, use_cache=False,
                                    temperature=0.2, max_tokens=8192, on_route=routes.append):
            while routes:
                r = routes.pop(0)
                if not routed:
                    routed = True
                    yield "route", {"agent": "mind", **r, "tier": "агент"}
            raw.append(piece)
            yield from handle(parser.feed(piece))
            if cut[0]:
                break
        if not cut[0]:
            yield from handle(parser.flush())
            yield from flush_line(final=True)
        if held:
            norm = lambda t: re.sub(r"\W+", " ", t).strip().lower()  # noqa: E731
            if norm("".join(held)) and norm("".join(held)) not in norm(prev_text):
                for h in held:
                    yield "text", h
        prev_text = "".join(said)
        if should_stop():
            return
        reply = "".join(raw)
        if cut[0]:
            reply = _ROLEPLAY_ANY.split(reply)[0]
        loop.append({"role": "assistant", "content": reply})

        if blocked:
            chat["pending_actions"] = [{k: b[k] for k in ("tag", "attrs", "body")} for b in blocked]
            ask = "\n".join(f"• {b['why']}" for b in blocked)
            yield "text", f"\n\n⏸ Нужно ваше подтверждение:\n{ask}\n\nНапишите «да», чтобы выполнить, или что-то другое, чтобы отменить."
            return

        # Модель выдала код вместо действий — один раз подталкиваем, потом сохраняем блоки сами
        if not actions and wants_build and _FENCE.search(reply):
            if not nudged:
                nudged = True
                yield "activity", "Mind сохраняет код в файлы"
                loop.append({"role": "user", "content": NUDGE})
                continue
            folder = slug(chat.get("title") or user_text)
            for path, code in code_blocks_as_files(reply, folder):
                shown, res = ex.do("write", {"path": path}, code)
                results.append(res)
                yield "text", "\n\n" + shown + "\n\n"
            if results:
                loop.append({"role": "user", "content": "Результаты:\n" + "\n".join(results)})
                continue

        if any(a.startswith("deploy:") for a in actions):
            deployed = True
        for w in web:
            if Path(w).name.lower() == "index.html":
                sites.append(str(Path(w).parent).replace("\\", "/"))
        wrote = any(a.startswith("write:") for a in actions)
        if wrote:
            bad = [r for r in results if r.startswith("✓ файл записан") and "ОШИБКА" in r]
            broken = bad[-1].split("ОШИБКА", 1)[1].split(":", 1)[-1].strip() if bad else ""
        elif broken and fix_nudges < 2 and not should_stop():
            # Модель сказала «исправил», но файл не перезаписала
            fix_nudges += 1
            loop.append({"role": "user", "content": "Ты не перезаписал файл — исправление не сохранено. Перезапиши "
                                                    "файл целиком через <write>, исправив найденные ошибки."})
            yield "activity", "Mind исправляет ошибку"
            continue

        extra = inbox.drain() if inbox else []
        for t in extra:
            yield "inject", t
        if results or extra:
            if actions and actions == last_actions and not extra:
                break               # модель повторяет то же самое — хватит
            last_actions = actions
            note = ("Результаты:\n" + "\n".join(results)) if results else ""
            fresh = [w for w in web if w not in reviewed and "ОШИБКА" not in note]
            if reviewed and not fresh and "ОШИБКА" not in note:
                note += "\n\nПроверка уже сделана, больше не проверяй и не переписывай файлы без явной ошибки."
            if fresh and not reviewed:
                reviewed.update(fresh)
                note += (f"\n\nТеперь проверь только что записанный {', '.join(fresh)} как строгий тестировщик. В первой "
                         "версии почти всегда есть недоделки: состояние (переменные) не обновляется после действия, "
                         "экран не перерисовывается, нет перемешивания или старта, нет счёта и сообщения о победе, нет "
                         "кнопки «Заново», ссылки на несуществующие картинки, бедный дизайн. Пройди по коду функция за "
                         "функцией, перечисли найденные проблемы одной короткой строкой и перезапиши файл целиком "
                         "исправленным и улучшенным. Только если проблем действительно нет — коротко скажи итог.")
                yield "activity", "Mind проверяет свою работу"
            if extra:
                note += ("\n\n" if note else "") + "Новое сообщение пользователя (пришло, пока ты работал):\n" + \
                        "\n".join(extra)
            loop.append({"role": "user", "content": note + "\n\nПродолжай. Если всё готово — коротко скажи итог без действий."})
            yield "text", "\n\n"
            continue
        if inbox:
            extra = inbox.drain(close_if_empty=True)
            if extra:
                for t in extra:
                    yield "inject", t
                loop.append({"role": "user", "content": "\n".join(extra)})
                continue
        if broken:
            yield "text", _unfixed(broken)
        elif sites and not deployed and not re.search(r"опублик|хостинг|deploy", prev_text, re.I):
            yield "text", (f"\n\n🌐 Могу бесплатно опубликовать сайт в интернете (Surge) — напишите «опубликуй», "
                           "и через минуту у него будет свой адрес.")
        return
    if broken:
        yield "text", _unfixed(broken)
    if inbox:
        for t in inbox.drain(close_if_empty=True):
            yield "text", f"\n\n(Сообщение «{t[:80]}» пришло в самом конце — отправьте его ещё раз.)"


def _unfixed(problem: str) -> str:
    """Честный итог, если модель так и не смогла починить найденную ошибку."""
    tip = ("Напишите «исправь» — попробую ещё раз. Для сложных программ добавьте бесплатный ключ Groq, Gemini или "
           "NVIDIA в «Настройки → Ключи»: Mind будет писать код сильной облачной моделью."
           if agent_model(ai.load_config()) else "Напишите «исправь» — попробую ещё раз.")
    return f"\n\n⚠️ Не смог до конца исправить ошибку: {problem.splitlines()[0][:300]}\n\n{tip}"


# Какие локальные модели лучше справляются с ролью агента (проверено на задаче «игра-головоломка с ярлыком»:
# qwen3.5:4b пишет полноценную игру, qwen2.5-coder:7b — заготовку). Чем раньше в списке, тем лучше.
AGENT_MODELS = ("qwen3.6", "qwen3.5", "qwen3-coder", "qwen3", "gemma4", "gemma3", "qwen2.5-coder", "deepseek-coder",
                "llama3", "qwen2.5")


def agent_model(cfg: dict) -> str:
    """Локальная модель Ollama для агента, если впереди очереди нет бесплатного облачного провайдера."""
    route = ai.plan_route("code", cfg)
    if not route or route[0] != "ollama":
        return ""
    names = [m["name"] for m in ai.ollama_models()]
    paused = ai._cooldowns()
    ranked = sorted((n for n in names if f"ollama|{n}" not in paused),
                    key=lambda n: next((i for i, k in enumerate(AGENT_MODELS) if n.lower().startswith(k)), 99))
    return ranked[0] if ranked else ""


def _compact(loop: list[dict]) -> list[dict]:
    """Старые шаги цикла укорачиваем: содержимое уже записанных файлов модели повторно не нужно."""
    out = []
    for i, m in enumerate(loop):
        c = m["content"]
        if m["role"] == "assistant" and i < len(loop) - 2:
            c = re.sub(r"(<write\b[^>]*>)(.*?)(</write>)",
                       lambda mm: f"{mm.group(1)}[…{mm.group(2).count(chr(10))} строк записано…]{mm.group(3)}", c, flags=re.S)
        out.append({"role": m["role"], "content": c})
    # первый запрос пользователя и последние шаги — самое важное
    return out if len(out) <= 9 else out[:1] + out[-8:]
