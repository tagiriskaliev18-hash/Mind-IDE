# Mind IDE

**Mind** — собственная ИИ-среда разработки Тагира Искалиева: рабочее место программиста в духе Antigravity,
где в одном разговоре работают ваши модели, Claude Code и Antigravity.

> Mind — часть экосистемы **[MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem)**.
> Автор и создатель: **Тагир Искалиев** ([@tagiriskaliev18-hash](https://github.com/tagiriskaliev18-hash)).

## Зачем

Философия MindTagSystem — максимально комфортная система для программистов, которую можно встроить в любое
устройство. Mind в ней играет роль «мозга»: это ИИ-ассистент и IDE, встроенные прямо в операционную систему
[AIsktagOS](https://github.com/tagiriskaliev18-hash/AisktagOS), и одновременно самостоятельное приложение для Windows.

## Что умеет

**Mind Studio** (`launch-studio.py`) — веб-интерфейс в окне Qt WebEngine. Один чат обращается к нескольким агентам:

- **Mind** — маршрутизатор моделей: локальная llama.cpp, NVIDIA, Groq, Gemini, OpenRouter, DeepSeek, Kimi, OpenAI, Claude;
- **Claude** — Claude Code на аккаунте пользователя (`claude -p`), сессия продолжается на весь чат;
- **Antigravity** — через agentapi самого Antigravity (мост запускается из его терминала: `--link-antigravity`);
- **Все сразу** — агенты отвечают параллельно, Mind сводит лучший ответ.

Также есть навыки (skills), история чатов и тёмная тема в дизайн-системе Aurora.

**Mind** (`launch-mind.py`) — компактное окно-ассистент на PyQt6 (в AIsktagOS открывается по Meta+A):

- режим **«Авто»** сам выбирает модель под задачу: простые вопросы идут быстрой бесплатной модели,
  код и сложные задачи сильной, платные (Claude, OpenAI) только если остальные недоступны;
- при лимите или сбое ответ перехватывает следующая модель;
- **«Консилиум»** спрашивает несколько моделей сразу и сводит лучший ответ;
- одинаковый вопрос в течение суток отдаётся из кэша.

## Запуск на Windows

Нужны Python 3.12+ и PyQt6 с QtWebEngine:

```powershell
python -m venv .venv
.venv\Scripts\pip install PyQt6 PyQt6-WebEngine
.venv\Scripts\pythonw launch-studio.py          # Mind Studio
.venv\Scripts\pythonw launch-mind.py            # окно-ассистент
powershell -File tools\windows\Install-Mind-Shortcut.ps1   # ярлык Mind на рабочем столе
```

## Ключи API

Ключи берутся из переменных окружения (`NVIDIA_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, …) или из
`%APPDATA%\aisktagos\ai.json` (на Linux `~/.config/aisktagos/ai.json`), поле `"keys": {"nvidia": "…"}`.
Ключи никогда не хранятся в репозитории и не пишутся в журнал.

## Структура

Раскладка файлов повторяет их место в AIsktagOS, поэтому код без изменений работает и в ОС, и отдельно:

| Путь | Что это |
|---|---|
| `overlay/usr/lib/aisktagos/aisktag_ai.py` | библиотека Mind: провайдеры, маршрутизатор «Авто», кэш, консилиум (только stdlib) |
| `overlay/usr/lib/aisktagos/aisktag-mind.py` | окно-ассистент Mind (PyQt6) |
| `overlay/usr/lib/aisktagos/aisktag-studio.py`, `mind_studio/` | Mind Studio: сервер (http.server + SSE), агенты, мост к Antigravity, веб-интерфейс |
| `overlay/usr/lib/aisktagos/aisktag_theme.py`, `overlay/usr/share/aisktagos/design/` | дизайн-система Aurora |
| `overlay/usr/share/aisktagos/ai/models.json` | каталог моделей |
| `overlay/usr/lib/aisktagos/ai/` | помощники для локальной модели llama.cpp |
| `overlay/usr/share/…`, `overlay/etc/…` | ярлыки, иконки и плазмоид для AIsktagOS (KDE Plasma) |
| `launch-mind.py`, `launch-studio.py` | лаунчеры для Windows и разработки |
| `tools/` | тесты и установщик ярлыка Windows |

## Тесты

```powershell
python tools/test-mind-router.py   # маршрутизатор, без сети
python tools/test-studio.py        # API и интерфейс Mind Studio (интерфейс через Playwright)
```

## Экосистема MindTagSystem

Mind связан с другими проектами экосистемы:

- [AisktagOS](https://github.com/tagiriskaliev18-hash/AisktagOS) — операционная система, куда Mind встроен как системный ИИ;
- [ITIS-browser](https://github.com/tagiriskaliev18-hash/ITIS-browser) — браузер экосистемы;
- [multimodel-agent](https://github.com/tagiriskaliev18-hash/multimodel-agent) и
  [antigravity-claude-bridge](https://github.com/tagiriskaliev18-hash/antigravity-claude-bridge) — мультимодельный агент и мост Antigravity ↔ Claude;
- [qwen14b-coder-dev](https://github.com/tagiriskaliev18-hash/qwen14b-coder-dev) — локальная модель для кода.

Полное описание экосистемы, её философии и всех проектов — в репозитории
[MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem).

---

© Тагир Искалиев, создатель MindTagSystem.
