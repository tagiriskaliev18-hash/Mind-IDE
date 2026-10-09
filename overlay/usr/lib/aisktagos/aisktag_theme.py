"""Тема «Aurora 2.0» — Apple-like стиль для PyQt-приложений AIsktagOS.

Философия:
  • Абсолютный чёрный фон — как в OLED-дисплеях Apple
  • Единственный фирменный градиент: фиолетовый → циан
  • Крупные скругления (16–24px на карточках)
  • Frosted-glass оверлеи через полупрозрачные фоны
  • Каждое приложение — свой акцентный цвет (APP_ACCENTS)
  • Всё через токены: никаких вшитых hex-кодов в приложениях
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_TOKENS_FILE = Path(os.environ.get("AISKTAG_TOKENS", "/usr/share/aisktagos/design/tokens.json"))

# Fallback (если токены ещё не сгенерированы) — полная Aurora 2.0 палитра
_FALLBACK: dict[str, str] = {
    "bg0":          "#050508",
    "bg1":          "#09090f",
    "bg2":          "#0f0f1a",
    "surface":      "#141421",
    "surface2":     "#1c1c2e",
    "glass":        "#ffffff0d",
    "text":         "#f5f5f7",
    "text2":        "#a1a1b5",
    "muted":        "#636378",
    "accent":       "#6e56cf",
    "accentHover":  "#7c66df",
    "accentStrong": "#5a45b5",
    "accentGlow":   "#6e56cf40",
    "ai":           "#9B5DE5",
    "aiMid":        "#5b8dee",
    "aiCyan":       "#00F5FF",
    "aiText":       "#c4b5fd",
    "focus":        "#00F5FF",
    "focusSoft":    "#00F5FF28",
    "ok":           "#30d158",
    "warn":         "#ffd60a",
    "danger":       "#ff453a",
    "info":         "#0a84ff",
}

# Акцентные пары на приложение (фон кнопки, цвет текста)
_APP_ACCENTS_FB: dict[str, tuple[str, str]] = {
    "mind":    ("#9B5DE5", "#f5f5f7"),
    "center":  ("#0a84ff", "#f5f5f7"),
    "terminal":("#00F5FF", "#050508"),
    "code":    ("#30d158", "#050508"),
    "design":  ("#ff375f", "#f5f5f7"),
}


def _load() -> dict:
    try:
        data = json.loads(_TOKENS_FILE.read_text(encoding="utf-8"))
        return {**_FALLBACK, **data.get("colors", {})}
    except (OSError, ValueError, KeyError):
        return dict(_FALLBACK)


def _load_accents() -> dict[str, tuple[str, str]]:
    try:
        data = json.loads(_TOKENS_FILE.read_text(encoding="utf-8"))
        raw = data.get("app_accents", {})
        return {k: tuple(v) for k, v in raw.items()} if raw else _APP_ACCENTS_FB
    except (OSError, ValueError):
        return _APP_ACCENTS_FB


C: dict[str, str] = _load()
APP_ACCENTS: dict[str, tuple[str, str]] = _load_accents()


def rgba(name: str, alpha: float) -> str:
    """Возвращает rgba(...) строку для токена с заданной прозрачностью."""
    h = C[name]
    return f"rgba({int(h[1:3], 16)}, {int(h[3:5], 16)}, {int(h[5:7], 16)}, {alpha})"


def app_accent(app: str) -> tuple[str, str]:
    """Возвращает (bg_color, text_color) для приложения."""
    return APP_ACCENTS.get(app, (C["accent"], C["text"]))


def gradient_aurora(angle: int = 135) -> str:
    """CSS-like строка градиента Aurora для использования в QSS."""
    return f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {C['ai']}, stop:0.5 {C['aiMid']}, stop:1 {C['aiCyan']})"


def base_qss() -> str:
    """Apple-like QSS для всех PyQt-приложений AIsktagOS."""
    a = C["accent"]
    return f"""
/* ── Основа ─────────────────────────────────────────────────────── */
QWidget {{
    font-family: 'SF Pro Display', 'Inter', system-ui, sans-serif;
    font-size: 11pt;
    color: {C['text']};
    background: transparent;
}}
QWidget#root {{
    background: {C['bg1']};
}}
QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QToolTip {{
    background: {C['surface2']};
    color: {C['text']};
    border: 1px solid {rgba('focus', 0.25)};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 9.5pt;
}}

/* ── Типографика ─────────────────────────────────────────────────── */
QLabel#display {{ font-size: 56pt; font-weight: 700; letter-spacing: -2px; color: {C['text']}; }}
QLabel#h1      {{ font-size: 34pt; font-weight: 700; letter-spacing: -1px; color: {C['text']}; }}
QLabel#h2      {{ font-size: 22pt; font-weight: 600; letter-spacing: -0.5px; color: {C['text']}; }}
QLabel#h3      {{ font-size: 16pt; font-weight: 600; color: {C['text']}; }}
QLabel#lead    {{ font-size: 13pt; font-weight: 400; color: {C['text2']}; }}
QLabel#muted   {{ font-size: 9.5pt; color: {C['muted']}; }}
QLabel#caption {{ font-size: 8pt;   color: {C['muted']}; letter-spacing: 0.5px; }}

/* ── Поверхности ─────────────────────────────────────────────────── */
QFrame#card {{
    background: {C['surface']};
    border: 1px solid {rgba('text', 0.06)};
    border-radius: 16px;
}}
QFrame#card:hover {{
    border: 1px solid {rgba('accent', 0.35)};
}}
QFrame#glass {{
    background: {rgba('glass', 1.0)};
    border: 1px solid {rgba('text', 0.08)};
    border-radius: 20px;
}}
QFrame#bar {{
    background: {rgba('bg2', 0.96)};
    border: none;
    border-bottom: 1px solid {rgba('text', 0.06)};
}}
QFrame#separator {{
    background: {rgba('text', 0.07)};
    max-height: 1px;
    border: none;
}}

/* ── Кнопки ──────────────────────────────────────────────────────── */
QPushButton {{
    background: {C['surface2']};
    border: 1px solid {rgba('text', 0.08)};
    border-radius: 10px;
    padding: 8px 18px;
    font-size: 11pt;
    font-weight: 500;
    color: {C['text']};
}}
QPushButton:hover {{
    background: {C['surface']};
    border: 1px solid {rgba('text', 0.14)};
}}
QPushButton:pressed {{
    background: {C['bg2']};
    border: 1px solid {rgba('accent', 0.4)};
}}
QPushButton:disabled {{
    color: {C['muted']};
    background: {C['surface']};
    border: 1px solid {rgba('text', 0.04)};
}}

/* Основная кнопка действия */
QPushButton#primary {{
    color: #ffffff;
    font-weight: 600;
    background: {C['accentStrong']};
    border: none;
    border-radius: 10px;
    padding: 9px 22px;
}}
QPushButton#primary:hover {{
    background: {C['accentHover']};
}}
QPushButton#primary:pressed {{
    background: {C['accent']};
}}

/* Кнопка ИИ — Aurora-градиент */
QPushButton#ai {{
    color: #ffffff;
    font-weight: 600;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {C['ai']}, stop:1 {C['aiMid']});
    border: none;
    border-radius: 10px;
    padding: 9px 22px;
}}
QPushButton#ai:hover {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {C['aiMid']}, stop:1 {C['aiCyan']});
}}

/* Призрак-кнопка (без фона) */
QPushButton#ghost {{
    background: transparent;
    border: none;
    color: {C['text2']};
    padding: 6px 12px;
}}
QPushButton#ghost:hover {{
    background: {rgba('text', 0.06)};
    color: {C['text']};
    border-radius: 8px;
}}

/* Pill-кнопка (скруглённая) */
QPushButton#pill {{
    background: {rgba('accent', 0.15)};
    border: 1px solid {rgba('accent', 0.3)};
    border-radius: 999px;
    padding: 5px 16px;
    font-size: 9.5pt;
    color: {C['aiText']};
    font-weight: 500;
}}
QPushButton#pill:hover {{
    background: {rgba('accent', 0.3)};
}}

/* Кнопка-иконка (квадратная) */
QPushButton#icon-btn {{
    background: {C['surface']};
    border: 1px solid {rgba('text', 0.08)};
    border-radius: 10px;
    padding: 8px;
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
}}
QPushButton#icon-btn:hover {{
    background: {C['surface2']};
    border: 1px solid {rgba('accent', 0.3)};
}}

/* ── Поля ввода ──────────────────────────────────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {C['surface']};
    border: 1px solid {rgba('text', 0.1)};
    border-radius: 10px;
    padding: 9px 12px;
    font-size: 11pt;
    color: {C['text']};
    selection-background-color: {rgba('accent', 0.5)};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1.5px solid {C['accent']};
    background: {C['surface2']};
}}
QLineEdit::placeholder, QPlainTextEdit::placeholder {{
    color: {C['muted']};
}}

/* ── ComboBox ────────────────────────────────────────────────────── */
QComboBox {{
    background: {C['surface']};
    border: 1px solid {rgba('text', 0.1)};
    border-radius: 10px;
    padding: 7px 12px;
    color: {C['text']};
    font-size: 10pt;
}}
QComboBox:focus {{ border: 1.5px solid {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {C['surface2']};
    border: 1px solid {rgba('text', 0.12)};
    border-radius: 10px;
    selection-background-color: {C['accentStrong']};
    padding: 4px;
}}

/* ── Полосы прокрутки ────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {rgba('text', 0.18)};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {rgba('accent', 0.6)};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}

/* ── Разделители ─────────────────────────────────────────────────── */
QSplitter::handle {{
    background: {rgba('text', 0.06)};
    width: 1px;
}}
"""
