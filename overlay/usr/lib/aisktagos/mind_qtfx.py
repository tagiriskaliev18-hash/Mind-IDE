"""Эффекты единого стиля Mind для PyQt6: иконки Mind с градиентом, bounce и переливающийся градиент.

Перенесено (vendoring) из MindKit: mindkit/qtfx.py репозитория MindTagSystem (tagiriskaliev18-hash/mindtagsystem),
чтобы Mind IDE не зависел от установленного MindKit. Иконки и стили лежат в /usr/share/aisktagos/design/
(копия design/ из MindKit; при запуске из репозитория — overlay/usr/share/aisktagos/design/).

    import mind_qtfx as fx
    btn.setIcon(fx.icon("send"))
    fx.bounce_on_hover(btn)          # пружина при наведении и нажатии
    fx.shimmer(btn, extra="color: #fff;")   # переливающийся фиолетово-синий фон

PyQt6 импортируется лениво: модуль можно импортировать и без Qt.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DESIGN = next((p for p in (Path(os.environ.get("AISKTAG_DESIGN", "/usr/share/aisktagos/design")),
                           _HERE.parent.parent / "share/aisktagos/design")
               if (p / "icons").is_dir()), Path("/usr/share/aisktagos/design"))
# Фиолетово-синий градиент Mind (если tokens.json не найден)
STOPS = ["#a46cf0", "#7c66df", "#5b8dee", "#49b3f7"]
SPRING_MS = 420
MARK = re.compile(r"\[\[mi:([a-z-]+)\]\]\s?")   # маркер иконки в тексте (как в агенте Mind Studio)


def stops() -> list[str]:
    try:
        return json.loads((DESIGN / "tokens.json").read_text(encoding="utf-8"))["gradient"]["stops"]
    except (OSError, ValueError, KeyError):
        return list(STOPS)


def gradient(x1: float = 0, y1: float = 0, x2: float = 1, y2: float = 1, spread: str = "") -> str:
    """qlineargradient(...) для QSS с цветами Mind."""
    s = stops()
    parts = ", ".join(f"stop:{round(i / (len(s) - 1), 2)} {c}" for i, c in enumerate(s))
    return f"qlineargradient(x1:{x1}, y1:{y1}, x2:{x2}, y2:{y2}, {spread + ', ' if spread else ''}{parts})"


def icon_path(name: str) -> Path:
    return DESIGN / "icons" / f"{name}.svg"


def icon_html(name: str, size: int = 16) -> str:
    """<img> иконки Mind для QTextBrowser/QLabel с rich text."""
    p = icon_path(name)
    return f'<img src="{p.as_uri()}" width="{size}" height="{size}">' if p.exists() else ""


def marks_to_html(text: str, size: int = 16) -> str:
    """Простой текст с маркерами [[mi:имя]] → HTML с иконками Mind."""
    out = html.escape(text).replace("\n", "<br>")
    return MARK.sub(lambda m: icon_html(m.group(1), size) + "&nbsp;", out)


def icon(name: str, size: int = 48, white: bool = False):
    """QIcon иконки Mind с фиолетово-синим градиентом (white=True — белая, для градиентных кнопок)."""
    from PyQt6.QtCore import QByteArray, Qt
    from PyQt6.QtGui import QIcon, QPainter, QPixmap

    p = icon_path(name)
    if not p.exists():
        return QIcon()
    svg = p.read_text(encoding="utf-8")
    if white:
        svg = re.sub(r"stroke='url\(#[^)]+\)'", "stroke='#ffffff'", svg)
    try:
        from PyQt6.QtSvg import QSvgRenderer
    except ImportError:          # нет QtSvg — пусть Qt сам загрузит файл через плагин
        return QIcon(str(p))
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)


def bounce(widget, height: int = 6, duration: int = 520):
    """Один пружинящий прыжок виджета (появление, уведомление, клик)."""
    from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSequentialAnimationGroup

    start = widget.pos()
    up = QPropertyAnimation(widget, b"pos", widget)
    up.setDuration(duration // 3)
    up.setStartValue(start)
    up.setEndValue(start - QPoint(0, height))
    up.setEasingCurve(QEasingCurve.Type.OutQuad)
    down = QPropertyAnimation(widget, b"pos", widget)
    down.setDuration(duration - duration // 3)
    down.setStartValue(start - QPoint(0, height))
    down.setEndValue(start)
    down.setEasingCurve(QEasingCurve.Type.OutBounce)
    group = QSequentialAnimationGroup(widget)
    group.addAnimation(up)
    group.addAnimation(down)
    group.start()
    widget._mt_bounce = group  # держим ссылку, иначе анимацию соберёт сборщик мусора
    return group


def bounce_on_hover(widget, height: int = 3):
    """Пружинка при наведении и при нажатии (кнопки, плитки, значки)."""
    from PyQt6.QtCore import QEvent, QObject

    class _Filter(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802 — имя из Qt
            if ev.type() in (QEvent.Type.Enter, QEvent.Type.MouseButtonRelease):
                anim = getattr(obj, "_mt_bounce", None)
                if anim is None or anim.state() != anim.State.Running:
                    bounce(obj, height, SPRING_MS)
            return False

    f = _Filter(widget)
    widget.installEventFilter(f)
    widget._mt_bounce_filter = f
    return f


def shimmer(widget, prop: str = "background", period_ms: int = 6000, extra: str = "", selector: str = "",
            after: str = ""):
    """Переливающийся градиент Mind (Qt не умеет CSS-анимации — двигаем стопы сами).

    selector — QSS-селектор (например «QPushButton»); after — дополнительные правила (:hover, :pressed)."""
    from PyQt6.QtCore import QVariantAnimation

    def paint(v):
        x = float(v)
        grad = gradient(f"{-x:.3f}", 0, f"{1 - x:.3f}", 1, spread="spread:reflect")
        rule = f"{prop}: {grad};"
        widget.setStyleSheet(f"{selector} {{ {rule} {extra} }} {after}" if selector else f"{rule} {extra}")

    anim = QVariantAnimation(widget)
    anim.setStartValue(0.0)
    anim.setKeyValueAt(0.5, 1.0)
    anim.setEndValue(0.0)
    anim.setDuration(period_ms)
    anim.setLoopCount(-1)
    anim.valueChanged.connect(paint)
    paint(0.0)
    anim.start()
    widget._mt_shimmer = anim
    return anim


def gradient_label(text: str, object_name: str = "", shimmer_ms: int = 6000):
    """QLabel, текст которого залит переливающимся градиентом Mind (QSS-градиент в color Qt рисует неверно)."""
    from PyQt6.QtCore import QPointF, QVariantAnimation
    from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
    from PyQt6.QtWidgets import QLabel

    class GradientLabel(QLabel):
        def __init__(self):
            super().__init__(text)
            if object_name:
                self.setObjectName(object_name)
            self._x = 0.0
            self._anim = QVariantAnimation(self, startValue=0.0, endValue=0.0, duration=shimmer_ms, loopCount=-1)
            self._anim.setKeyValueAt(0.5, 1.0)
            self._anim.valueChanged.connect(self._tick)
            self._anim.start()

        def _tick(self, v):
            self._x = float(v)
            self.update()

        def paintEvent(self, _ev):  # noqa: N802 — имя из Qt
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            fm = self.fontMetrics()
            w = fm.horizontalAdvance(self.text())
            y = (self.height() + fm.ascent() - fm.descent()) / 2
            path = QPainterPath()
            path.addText(QPointF(0, y), self.font(), self.text())
            # градиент шире текста и сдвигается — «перелив», как background-size 200% в вебе
            g = QLinearGradient(QPointF(-w * self._x, 0), QPointF(w * (2 - self._x), 0))
            s = stops() + stops()[-2::-1]
            for i, c in enumerate(s):
                g.setColorAt(i / (len(s) - 1), QColor(c))
            p.fillPath(path, g)
            p.setPen(QPen())
            p.end()

    return GradientLabel()
