#!/usr/bin/env python3
"""Mind — окно встроенного ИИ-ассистента AIsktagOS (PyQt6).

    aisktag-mind                     открыть (Meta+A)
    aisktag-mind --clipboard         открыть и вложить текст из буфера обмена
    aisktag-mind --ask "вопрос"      открыть и сразу спросить

Режим «Авто» сам выбирает модель под задачу: простые вопросы — быстрой бесплатной модели, код и сложные
задачи — сильной, платные (Claude, OpenAI) — только если другие недоступны. При лимите или сбое ответ
перехватывает следующая модель. «Консилиум» спрашивает несколько моделей сразу и сводит лучший ответ.
Ключи API — в «Настройках ИИ» или в переменных окружения. Окно одно: повторный запуск показывает уже открытое.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, "/usr/lib/aisktagos")
sys.path.insert(0, str(Path(__file__).resolve().parent))   # Windows и запуск из репозитория
import aisktag_ai as ai  # noqa: E402
import aisktag_theme as T  # noqa: E402
from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut  # noqa: E402
from PyQt6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,  # noqa: E402
                             QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget)

MAX_ATTACH = 200_000        # символов вложения: больше малой модели не переварить
HISTORY_LIMIT = 24          # сколько последних сообщений отдавать маршрутизатору (он урежет для простых вопросов)
# Режимы ответа: (подпись, mode, tier)
MODES = [
    ("Авто — экономно", "auto", None),
    ("Быстро", "auto", "fast"),
    ("Код", "auto", "code"),
    ("Максимум", "auto", "deep"),
    ("Консилиум моделей", "council", None),
]
SUGGESTIONS = [
    ("Объясни ошибку из буфера", "Объясни эту ошибку и предложи исправление:"),
    ("Напиши юнит-тест", "Напиши юнит-тесты для этого кода:"),
    ("Что делает команда?", "Объясни, что делает эта команда, по частям:"),
    ("Помоги с Git", "Подскажи команды Git, чтобы "),
]
QSS = T.base_qss() + f"""
QFrame[role="user"] {{ background: {T.rgba('accentStrong', 0.22)}; border: 1px solid {T.rgba('accent', 0.45)}; border-radius: 14px; }}
QFrame[role="assistant"] {{ background: {T.C['surface']}; border: 1px solid {T.rgba('aiText', 0.25)}; border-left: 3px solid {T.C['ai']}; border-radius: 14px; }}
QTextBrowser {{ background: transparent; border: none; padding: 0; selection-background-color: {T.C['accent']}; }}
QLabel#who {{ font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#chip {{ background: {T.C['surface']}; border: 1px solid {T.rgba('focus', 0.18)}; border-radius: 12px; padding: 3px 10px; }}
QLabel#route {{ font-size: 8pt; color: {T.C['muted']}; }}
QLabel#attach {{ background: {T.rgba('ai', 0.2)}; border: 1px solid {T.rgba('aiText', 0.4)}; border-radius: 10px; padding: 3px 10px; color: {T.C['aiText']}; }}
QPushButton#suggest {{ text-align: left; padding: 10px 14px; border-radius: 12px; background: {T.C['surface']}; }}
QPlainTextEdit#input {{ border-radius: 14px; padding: 10px 12px; }}
"""


class Worker(QThread):
    """Читает поток ответа модели в отдельном потоке, чтобы окно не зависало."""
    chunk = pyqtSignal(str)
    failed = pyqtSignal(str)
    routed = pyqtSignal(dict)

    def __init__(self, messages: list, cfg: dict, **opts):
        super().__init__()
        self.messages, self.cfg, self.opts, self._stop = messages, cfg, opts, False

    def run(self) -> None:
        try:
            for piece in ai.stream_chat(self.messages, self.cfg, should_stop=lambda: self._stop,
                                        on_route=self.routed.emit, **self.opts):
                self.chunk.emit(piece)
        except ai.AIError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 — любой сбой показываем пользователю, а не роняем окно
            self.failed.emit(f"Неожиданная ошибка: {e}")

    def stop(self) -> None:
        self._stop = True


class Bubble(QFrame):
    """Сообщение: пользователя (простой текст) или ассистента (Markdown, обновляется по мере генерации)."""

    def __init__(self, role: str):
        super().__init__()
        self.role, self.text = role, ""
        self.setProperty("role", role)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        who = QLabel("ВЫ" if role == "user" else "MIND")
        who.setObjectName("who")
        who.setStyleSheet(f"color: {T.C['accent'] if role == 'user' else T.C['aiText']};")
        lay.addWidget(who)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.document().setDocumentMargin(0)
        lay.addWidget(self.body)
        if role == "assistant":
            row = QHBoxLayout()
            copy = QPushButton("Копировать")
            copy.setObjectName("ghost")
            copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text))
            row.addWidget(copy)
            row.addStretch(1)
            self.route = QLabel("")
            self.route.setObjectName("route")
            row.addWidget(self.route)
            lay.addLayout(row)

    def set_route(self, info: dict) -> None:
        name = ai.PROVIDERS.get(info.get("provider") or "", {}).get("title", info.get("provider") or "?")
        parts = [name, str(info.get("model") or "")]
        if info.get("tier") == "council":
            parts = [f"консилиум: {info.get('voters', name)}", f"свёл {name}"]
        elif info.get("tier"):
            parts.append(ai.TIER_NAMES.get(info["tier"], info["tier"]))
        if info.get("cached"):
            parts.append("из кэша, квота не тратилась")
        if info.get("tried"):
            parts.append(f"резерв после сбоев: {len(info['tried'])}")
        self.route.setText(" · ".join(p for p in parts if p))
        self.route.setToolTip("\n".join(info.get("tried") or []))

    def set_text(self, text: str) -> None:
        self.text = text
        if self.role == "user":
            self.body.setPlainText(text)
        else:
            self.body.setMarkdown(text)
        self.fit()

    def fit(self) -> None:
        width = max(self.body.viewport().width(), 200)
        self.body.document().setTextWidth(width)
        self.body.setFixedHeight(int(self.body.document().size().height()) + 4)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        QTimer.singleShot(0, self.fit)


class EnterFilter(QObject):
    """Enter — отправить, Shift+Enter — новая строка."""

    def __init__(self, send):
        super().__init__()
        self.send = send

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send()
            return True
        return False


class Mind(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Mind — ИИ-ассистент")
        icon_file = os.environ.get("AISKTAG_MIND_ICON", "")
        self.setWindowIcon(QIcon(icon_file) if icon_file and Path(icon_file).exists()
                           else QIcon.fromTheme("aisktagos-mind", QIcon.fromTheme("aisktagos-logo")))
        self.resize(900, 720)
        self.setMinimumSize(520, 460)
        self.cfg = ai.load_config()
        self.persona = "general"
        self.history: list[dict] = []
        self.attachments: list[tuple[str, str]] = []
        self.worker: Worker | None = None
        self.current: Bubble | None = None
        self.buf = ""
        self.dirty = False

        # --- верхняя панель
        bar = QFrame(objectName="bar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 10, 16, 10)
        title = QLabel("Mind")
        title.setObjectName("h2")
        title.setStyleSheet(f"color: {T.C['aiText']};")
        self.chip = QLabel("…")
        self.chip.setObjectName("chip")
        self.persona_box = QComboBox()
        for key, (name, _p) in ai.PERSONAS.items():
            self.persona_box.addItem(name, key)
        self.persona_box.currentIndexChanged.connect(lambda i: setattr(self, "persona", self.persona_box.itemData(i)))
        self.mode_box = QComboBox()
        self.mode_box.setToolTip("Кто отвечает: «Авто» сам выбирает модель и бережёт квоту")
        self.fill_modes()
        new = QPushButton("Новый чат")
        new.setObjectName("ghost")
        new.clicked.connect(self.new_chat)
        cfg_btn = QPushButton("Настройки ИИ")
        cfg_btn.setObjectName("ghost")
        cfg_btn.clicked.connect(self.open_settings)
        for w in (title, self.chip):
            bl.addWidget(w)
        bl.addStretch(1)
        for w in (self.mode_box, self.persona_box, new, cfg_btn):
            bl.addWidget(w)

        # --- лента сообщений
        self.feed = QVBoxLayout()
        self.feed.setContentsMargins(24, 18, 24, 18)
        self.feed.setSpacing(14)
        self.empty = self._empty_state()
        self.feed.addWidget(self.empty)
        self.feed.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.feed)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(holder)

        # --- ввод
        self.attach_row = QHBoxLayout()
        self.attach_row.setContentsMargins(24, 0, 24, 0)
        self.attach_row.addStretch(1)         # плашки вложений не растягиваются на всю ширину
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.input = QPlainTextEdit(objectName="input")
        self.input.setPlaceholderText("Спросите что угодно о коде, терминале или системе…   Enter — отправить, Shift+Enter — новая строка")
        self.input.setFixedHeight(76)
        self._filter = EnterFilter(self.send)
        self.input.installEventFilter(self._filter)
        file_btn = QPushButton("Файл…")
        file_btn.clicked.connect(self.attach_file)
        clip_btn = QPushButton("Из буфера")
        clip_btn.clicked.connect(self.attach_clipboard)
        self.send_btn = QPushButton("Отправить")
        self.send_btn.setObjectName("ai")
        self.send_btn.clicked.connect(self.on_send_clicked)
        btns = QVBoxLayout()
        btns.addWidget(self.send_btn)
        row2 = QHBoxLayout()
        row2.addWidget(file_btn)
        row2.addWidget(clip_btn)
        btns.addLayout(row2)
        inrow = QHBoxLayout()
        inrow.setContentsMargins(24, 0, 24, 16)
        inrow.addWidget(self.input, 1)
        inrow.addLayout(btns)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(bar)
        root.addWidget(self.scroll, 1)
        root.addLayout(self.attach_row)
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(26, 2, 24, 4)
        hint_row.addWidget(self.hint)
        root.addLayout(hint_row)
        root.addLayout(inrow)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_chat)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.input.setFocus)
        self.render_timer = QTimer(self, interval=60, timeout=self.flush)
        self.state_timer = QTimer(self, interval=2500, timeout=self.update_state)
        self.state_timer.start()
        self.update_state()
        self.input.setFocus()

    # --- состояние
    def update_state(self) -> None:
        if self.cfg.get("provider") == "auto":
            have = ai.available_providers(self.cfg)
            if have:
                text, color = f"авто · моделей: {len(have)}", T.C["ok"]
                self.chip.setToolTip("Доступны: " + ", ".join(ai.PROVIDERS[p]["title"] for p in have))
            else:
                text, color = "нет ни одного ИИ — откройте «Настройки ИИ»", T.C["danger"]
                self.chip.setToolTip("")
            self.chip.setText(f'<span style="color:{color}">●</span> {text}')
            return
        if not ai.is_local(self.cfg):
            host = urlparse(self.cfg["base_url"]).netloc
            text, color = f"внешний сервер · {host}", T.C["accent"]
        else:
            text, color = {"ready": ("модель готова", T.C["ok"]), "loading": ("загрузка модели…", T.C["warn"]),
                           "idle": ("спит · проснётся по запросу", T.C["muted"]),
                           "missing": ("ИИ не запущен", T.C["danger"])}[ai.backend_state()]
        self.chip.setText(f'<span style="color:{color}">●</span> {text}')

    def say(self, text: str, ms: int = 4000) -> None:
        self.hint.setText(text)
        QTimer.singleShot(ms, lambda: self.hint.setText("") if self.hint.text() == text else None)

    # --- пустое состояние
    def _empty_state(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setSpacing(10)
        head = QLabel("Чем помочь?")
        head.setObjectName("h1")
        sub = QLabel("Один чат — много моделей: Mind сам выбирает, кто ответит, бесплатные и локальные первыми, "
                     "а при лимите передаёт вопрос следующей модели. Режим и роль — вверху справа.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addSpacing(30)
        lay.addWidget(head)
        lay.addWidget(sub)
        lay.addSpacing(10)
        for label, prefill in SUGGESTIONS:
            b = QPushButton(label)
            b.setObjectName("suggest")
            b.clicked.connect(lambda _=False, t=prefill: self.prefill(t))
            lay.addWidget(b)
        return box

    def prefill(self, text: str) -> None:
        if text.endswith(":") and QGuiApplication.clipboard().text().strip():
            self.attach_clipboard()
        self.input.setPlainText(text)
        self.input.setFocus()
        cur = self.input.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.input.setTextCursor(cur)

    # --- вложения
    def add_attachment(self, name: str, text: str) -> None:
        text = text[:MAX_ATTACH]
        self.attachments.append((name, text))
        chip = QPushButton(f"📎 {name} ✕")
        chip.setObjectName("attach")
        chip.setStyleSheet(f"QPushButton {{ background: {T.rgba('ai', 0.2)}; border: 1px solid {T.rgba('aiText', 0.4)};"
                           f" border-radius: 10px; padding: 3px 10px; color: {T.C['aiText']}; }}")
        chip.clicked.connect(lambda: (self.attachments.remove((name, text)), chip.hide(), chip.deleteLater()))
        self.attach_row.insertWidget(self.attach_row.count() - 1, chip)

    def attach_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Вложить файл")
        if not path:
            return
        try:
            self.add_attachment(Path(path).name, Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            self.say("Файл не удалось прочитать как текст")

    def attach_clipboard(self) -> None:
        text = QGuiApplication.clipboard().text()
        if text.strip():
            self.add_attachment("буфер обмена", text)
        else:
            self.say("Буфер обмена пуст")

    # --- чат
    def add_bubble(self, role: str) -> Bubble:
        self.empty.hide()
        b = Bubble(role)
        self.feed.insertWidget(self.feed.count() - 1, b)
        return b

    def new_chat(self) -> None:
        if self.worker:
            self.worker.stop()
        self.history.clear()
        for i in reversed(range(self.feed.count())):
            w = self.feed.itemAt(i).widget()
            if isinstance(w, Bubble):
                w.deleteLater()
        self.empty.show()
        self.input.setFocus()

    def on_send_clicked(self) -> None:
        if self.worker:
            self.worker.stop()
            self.send_btn.setEnabled(False)
        else:
            self.send()

    def send(self, *_a) -> None:
        text = self.input.toPlainText().strip()
        if self.worker or not (text or self.attachments):
            return
        shown, content = text, text
        for name, body in self.attachments:
            content += f"\n\n[{name}]\n```\n{body}\n```"
            shown += f"\n📎 {name}"
        self.attachments.clear()
        for i in reversed(range(self.attach_row.count())):
            w = self.attach_row.itemAt(i).widget()
            if w:
                w.hide()
                w.deleteLater()
        self.input.clear()
        self.history.append({"role": "user", "content": content})
        self.add_bubble("user").set_text(shown.strip())
        self.current = self.add_bubble("assistant")
        self.current.set_text("…")
        self.buf = ""
        messages = [{"role": "system", "content": ai.PERSONAS[self.persona][1]}] + self.history[-HISTORY_LIMIT:]
        mode, tier = (self.mode_box.currentData() or ("auto", None))
        cfg = dict(self.cfg)
        if mode.startswith("provider:"):
            name = mode.split(":", 1)[1]
            mode = "auto"
            cfg.update(provider=name, base_url=ai.PROVIDERS[name]["base_url"],
                       model=ai.provider_model(name, "code"), api_key=ai.provider_key(name, cfg))
        self.worker = Worker(messages, cfg, persona=self.persona, mode=mode, tier=tier)
        self.worker.routed.connect(self.on_route)
        self.worker.chunk.connect(self.on_chunk)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_done)
        self.send_btn.setText("Стоп")
        self.worker.start()
        self.render_timer.start()
        self.scroll_down()

    def on_route(self, info: dict) -> None:
        if self.current:
            self.current.set_route(info)

    def on_chunk(self, piece: str) -> None:
        self.buf += piece
        self.dirty = True

    def flush(self) -> None:
        if self.dirty and self.current:
            at_bottom = self.scroll.verticalScrollBar().value() >= self.scroll.verticalScrollBar().maximum() - 60
            self.current.set_text(self.buf)
            self.dirty = False
            if at_bottom:
                self.scroll_down()

    def on_failed(self, msg: str) -> None:
        if self.current:
            self.current.set_text(f"⚠️ {msg}")
        self.buf = ""

    def on_done(self) -> None:
        self.render_timer.stop()
        self.dirty = True
        self.flush()
        if self.buf.strip():
            self.history.append({"role": "assistant", "content": self.buf})
        elif self.current and self.current.text == "…":
            self.current.set_text("(пустой ответ)")
        self.worker = None
        self.send_btn.setText("Отправить")
        self.send_btn.setEnabled(True)
        self.update_state()
        self.input.setFocus()

    def fill_modes(self) -> None:
        self.mode_box.clear()
        if self.cfg.get("provider") not in (None, "auto"):
            name = ai.PROVIDERS.get(self.cfg["provider"], {}).get("title", self.cfg.get("base_url", ""))
            self.mode_box.addItem(f"Только {name}", ("auto", None))
            return
        for label, mode, tier in MODES:
            self.mode_box.addItem(label, (mode, tier))
        for p in ai.available_providers(self.cfg):
            self.mode_box.addItem(f"Только {ai.PROVIDERS[p]['title']}", (f"provider:{p}", None))

    def open_settings(self) -> None:
        if SettingsDialog(self).exec():
            self.cfg = ai.load_config()
            self.fill_modes()
            self.update_state()

    def scroll_down(self) -> None:
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))


class SettingsDialog(QDialog):
    """Ключи API провайдеров. Сохраняются в ai.json (доступ только владельцу), в окне не показываются."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки ИИ — Mind")
        self.setMinimumWidth(580)
        self.cfg = ai.load_config()
        lay = QVBoxLayout(self)
        intro = QLabel("Добавьте ключи тех сервисов, что у вас есть. В режиме «Авто» Mind сначала зовёт бесплатные "
                       "и локальные модели, платные — только если остальные недоступны. Ключ из переменной окружения "
                       "важнее ключа отсюда. Сохранённые ключи здесь не показываются.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for name, p in ai.PROVIDERS.items():
            if name == "local":
                continue
            f = QLineEdit()
            f.setEchoMode(QLineEdit.EchoMode.Password)
            env_has = any(os.environ.get(e) for e in p["env"])
            saved = (self.cfg.get("keys") or {}).get(name, "")
            f.setPlaceholderText("задан в переменной окружения" if env_has else
                                 ("сохранён — оставьте пустым, чтобы не менять" if saved else
                                  f"ключ API или переменная {p['env'][0]}"))
            f.setToolTip(p["description"])
            self.fields[name] = f
            tag = " · бесплатно" if p["free"] else " · платно"
            form.addRow(QLabel(f"{p['title']}{tag}"), f)
        lay.addLayout(form)
        self.auto = QCheckBox("Режим «Авто» (рекомендуется): модель выбирается под задачу")
        self.auto.setChecked(self.cfg.get("provider") in (None, "auto"))
        self.cache = QCheckBox("Отвечать из кэша на повторные вопросы (сутки) — экономит квоту")
        self.cache.setChecked(bool(self.cfg.get("cache", True)))
        lay.addWidget(self.auto)
        lay.addWidget(self.cache)
        reset = QPushButton("Снять паузу со всех моделей и очистить кэш")
        reset.setObjectName("ghost")
        reset.clicked.connect(lambda: (ai.reset_cooldowns(), ai.clear_cache(), reset.setText("Готово")))
        lay.addWidget(reset)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def save(self) -> None:
        cfg = ai._read_user_conf()
        keys = dict(cfg.get("keys") or {})
        for name, f in self.fields.items():
            if f.text().strip():
                keys[name] = f.text().strip()
        cfg["keys"] = keys
        if self.auto.isChecked():
            cfg.update(provider="auto", base_url=ai.LOCAL_URL, model="aisktag-mind", api_key="")
        cfg["cache"] = self.cache.isChecked()
        ai.save_config(cfg)
        self.accept()


def single_instance(win_factory):
    """Один экземпляр: повторный запуск (например, Meta+A) поднимает уже открытое окно."""
    name = f"aisktag-mind-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(150):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return None
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)
    win = win_factory()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win.showNormal(), win.raise_(), win.activateWindow()))
    win._server = server
    return win


def main() -> int:
    args = sys.argv[1:]
    app = QApplication(sys.argv)
    app.setApplicationName("aisktag-mind")
    app.setDesktopFileName("aisktag-mind")
    app.setStyleSheet(QSS)
    win = single_instance(Mind)
    if win is None:
        return 0
    win.show()
    if "--clipboard" in args:
        win.attach_clipboard()
    if "--ask" in args and args.index("--ask") + 1 < len(args):
        win.input.setPlainText(args[args.index("--ask") + 1])
        win.send()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
