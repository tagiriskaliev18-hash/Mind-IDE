"""Связь Mind Studio с Google Antigravity, установленным на этом компьютере.

История: разговоры Antigravity читаются из ~/.gemini/antigravity (conversation_summaries.db и transcript.jsonl),
только чтение.

Запросы: у Antigravity есть команда `agentapi` (new-conversation / send-message), но она принимает вызовы только
с адресом и CSRF-токеном, которые Antigravity выдаёт своему терминалу. Поэтому мост запускается ОДИН раз
из терминала Antigravity:

    python launch-studio.py --link-antigravity        (Windows, из репозитория)
    aisktag-studio --link-antigravity                  (AIsktagOS)

Мост слушает только 127.0.0.1, требует собственный случайный секрет (файл antigravity-link.json, доступ
только владельцу) и никуда не пишет токен Antigravity — он остаётся в окружении процесса моста.
Ответ агента Mind Studio читает из транскрипта разговора по мере появления шагов.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator

from . import store

ROOT = Path(os.environ.get("ANTIGRAVITY_DATA", Path.home() / ".gemini" / "antigravity"))
LINK_FILE = store.DATA / "antigravity-link.json"
LINK_ERR = store.DATA / "antigravity-link.error"     # почему мост не запустился (видно в Mind Studio)
MODELS = {"flash_lite": "Gemini Flash Lite", "flash": "Gemini Flash", "pro": "Gemini Pro"}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class AntigravityError(Exception):
    pass


# ---------------------------------------------------------------------------
# История разговоров (только чтение)
# ---------------------------------------------------------------------------

def installed() -> bool:
    return ROOT.is_dir()


def history(limit: int = 40) -> list[dict]:
    db = ROOT / "conversation_summaries.db"
    if not db.exists():
        return []
    # Antigravity держит базу открытой — читаем копию, чтобы не мешать ему
    tmp = Path(tempfile.mkdtemp(prefix="agy-")) / "s.db"
    try:
        shutil.copy(db, tmp)
        con = sqlite3.connect(tmp)
        rows = con.execute("SELECT conversation_id, title, step_count, last_modified_time, workspace_uris, status "
                           "FROM conversation_summaries ORDER BY last_modified_time DESC LIMIT ?", (limit,)).fetchall()
        con.close()
    except (OSError, sqlite3.Error):
        return []
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    out = []
    for cid, title, steps, modified, ws, status in rows:
        try:
            workspace = Path(urllib.request.url2pathname(json.loads(ws)[0].replace("file://", ""))).name
        except (ValueError, IndexError, TypeError):
            workspace = ""
        out.append({"id": cid, "title": title or "Без названия", "steps": steps, "updated": str(modified)[:19],
                    "workspace": workspace, "running": "RUNNING" in (status or "")})
    return out


def _transcript(cid: str) -> Path:
    if not UUID_RE.fullmatch(cid or ""):
        raise AntigravityError("неверный id разговора")
    return ROOT / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"


def read_steps(cid: str) -> list[dict]:
    try:
        lines = _transcript(cid).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    steps = []
    for line in lines:
        try:
            steps.append(json.loads(line))
        except ValueError:
            continue
    return steps


def _clean_user(text: str) -> str:
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text or "", re.S)
    return m.group(1) if m else (text or "")


def conversation(cid: str) -> list[dict]:
    """Сообщения разговора Antigravity в формате чата Mind Studio."""
    msgs = []
    for s in read_steps(cid):
        if s.get("type") == "USER_INPUT":
            msgs.append({"role": "user", "content": _clean_user(s.get("content", ""))})
        elif s.get("type") == "PLANNER_RESPONSE" and s.get("content"):
            msgs.append({"role": "assistant", "content": s["content"], "agent": "antigravity"})
    return msgs


# ---------------------------------------------------------------------------
# Мост (запускается из терминала Antigravity)
# ---------------------------------------------------------------------------

def _agentapi_exe() -> str:
    exe = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
    if exe and Path(exe).exists():
        return exe
    for cand in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "resources" / "bin"
                 / "language_server.exe",
                 Path("/usr/share/antigravity/resources/bin/language_server"),
                 Path("/opt/Antigravity/resources/bin/language_server")):
        if cand.exists():
            return str(cand)
    raise AntigravityError("не найден language_server Antigravity")


def run_agentapi(args: list[str], timeout: int = 120) -> dict:
    exe = _agentapi_exe()
    cmd = [exe] if exe.endswith(("agentapi", "agentapi.bat", "agentapi.exe")) else [exe, "agentapi"]
    try:
        out = subprocess.run(cmd + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise AntigravityError(f"agentapi не запустился: {e}") from e
    try:
        data = json.loads(out)
    except ValueError:
        data = {"raw": out}
    if isinstance(data, dict) and data.get("error"):
        raise AntigravityError(str(data["error"])[:300])
    return data if isinstance(data, dict) else {"raw": out}


def serve_link(port: int = 0) -> int:
    """Запускает мост. Возвращает код выхода (вызывается из командной строки)."""
    store.DATA.mkdir(parents=True, exist_ok=True)
    if not (os.environ.get("ANTIGRAVITY_LS_ADDRESS") and os.environ.get("ANTIGRAVITY_CSRF_TOKEN")):
        msg = ("Мост запущен не агентом Antigravity: в этом окне нет адреса и токена Antigravity. "
               "Отправьте просьбу из раздела «Агенты» в чат Antigravity — его агент запустит мост сам.")
        LINK_ERR.write_text(json.dumps({"error": msg, "time": time.time()}, ensure_ascii=False), encoding="utf-8")
        print(msg, file=sys.stderr)
        return 2
    try:
        run_agentapi(["get-conversation-metadata", "00000000-0000-0000-0000-000000000000"], timeout=20)
    except AntigravityError as e:
        # «разговор не найден» означает, что связь есть; ошибки CSRF и адреса — что её нет
        if any(w in str(e) for w in ("CSRF", "Unauthenticated", "Unavailable", "LS_ADDRESS", "language_server")):
            msg = f"Antigravity не принял мост: {e}"
            LINK_ERR.write_text(json.dumps({"error": msg, "time": time.time()}, ensure_ascii=False), encoding="utf-8")
            print(msg, file=sys.stderr)
            return 3
    try:
        LINK_ERR.unlink()
    except OSError:
        pass
    secret = secrets.token_urlsafe(24)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("X-Link-Secret") != secret:
                return self._reply(403, {"error": "forbidden"})
            self._reply(200, {"ok": True})

        def do_POST(self):
            if self.headers.get("X-Link-Secret") != secret:
                return self._reply(403, {"error": "forbidden"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                prompt = str(req.get("prompt", ""))
                if req.get("conversation_id"):
                    cid = req["conversation_id"]
                    if not UUID_RE.fullmatch(cid):
                        raise AntigravityError("неверный id разговора")
                    data = run_agentapi(["send-message", cid, prompt])
                else:
                    model = req.get("model") if req.get("model") in MODELS else "flash"
                    title = str(req.get("title") or "Mind Studio")[:60]
                    data = run_agentapi(["new-conversation", f"--model={model}", f"--title={title}", prompt])
                    found = UUID_RE.findall(json.dumps(data))
                    cid = found[0] if found else ""
                self._reply(200, {"conversation_id": cid, "response": data})
            except AntigravityError as e:
                self._reply(502, {"error": str(e)})
            except (ValueError, OSError) as e:
                self._reply(400, {"error": str(e)})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    store.DATA.mkdir(parents=True, exist_ok=True)
    LINK_FILE.write_text(json.dumps({"port": srv.server_address[1], "secret": secret, "pid": os.getpid(),
                                     "started": time.time()}), encoding="utf-8")
    try:
        LINK_FILE.chmod(0o600)
    except OSError:
        pass
    print(f"Мост Antigravity ↔ Mind Studio работает (127.0.0.1:{srv.server_address[1]}). "
          "Не закрывайте этот терминал; Ctrl+C — отключить.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            LINK_FILE.unlink()
        except OSError:
            pass
    return 0


# ---------------------------------------------------------------------------
# Клиент моста (в Mind Studio)
# ---------------------------------------------------------------------------

def _link() -> dict | None:
    try:
        return json.loads(LINK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _call(method: str, path: str, data: dict | None = None, timeout: float = 3) -> dict:
    link = _link()
    if not link:
        raise AntigravityError("мост не запущен")
    req = urllib.request.Request(f"http://127.0.0.1:{int(link['port'])}{path}", method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"X-Link-Secret": link["secret"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", e.reason)
        except ValueError:
            msg = e.reason
        raise AntigravityError(str(msg)) from e
    except (urllib.error.URLError, OSError) as e:
        raise AntigravityError("мост не отвечает — запустите его снова из терминала Antigravity") from e


def last_error() -> str:
    """Причина последней неудачной попытки запустить мост (за последний час)."""
    try:
        d = json.loads(LINK_ERR.read_text(encoding="utf-8"))
        return d["error"] if time.time() - d.get("time", 0) < 3600 else ""
    except (OSError, ValueError, KeyError):
        return ""


def linked() -> bool:
    try:
        return bool(_call("GET", "/ping", timeout=0.8).get("ok"))
    except AntigravityError:
        return False


def ask(prompt: str, *, conversation_id: str = "", model: str = "flash", title: str = "",
        should_stop: Callable[[], bool] | None = None,
        on_activity: Callable[[str], None] | None = None, timeout: float = 900) -> Iterator[tuple[str, str]]:
    """Отправляет запрос в Antigravity и отдаёт его ответ по мере появления.

    Возвращает пары ("conversation", id) один раз в начале и ("text", кусок) — ответы агента.
    """
    before = len(read_steps(conversation_id)) if conversation_id else 0
    res = _call("POST", "/ask", {"prompt": prompt, "conversation_id": conversation_id, "model": model,
                                 "title": title}, timeout=180)
    cid = res.get("conversation_id") or conversation_id
    if not cid:
        raise AntigravityError("Antigravity не вернул id разговора: " + json.dumps(res)[:200])
    yield "conversation", cid
    seen = before
    started = time.time()
    last_change = time.time()
    got_user = False      # ответ считаем только после нашего USER_INPUT
    while time.time() - started < timeout:
        if should_stop and should_stop():
            return
        steps = read_steps(cid)
        if len(steps) > seen:
            last_change = time.time()
            for s in steps[seen:]:
                t = s.get("type")
                if t == "USER_INPUT":
                    got_user = True
                elif t == "PLANNER_RESPONSE" and got_user:
                    for call in s.get("tool_calls") or []:
                        if on_activity:
                            on_activity(f"Antigravity: {call.get('name', 'инструмент')}")
                    if s.get("content"):
                        yield "text", s["content"] + "\n\n"
            seen = len(steps)
            last = steps[-1]
            if got_user and last.get("type") == "PLANNER_RESPONSE" and last.get("status") == "DONE" \
                    and not last.get("tool_calls") and last.get("content"):
                return
        elif got_user and seen > before and time.time() - last_change > 120:
            return
        time.sleep(0.7)
    raise AntigravityError("Antigravity не закончил ответ за отведённое время")
