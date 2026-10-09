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

- **Mind** — маршрутизатор моделей: локальная llama.cpp, Ollama (модели находятся сами, без ключей), NVIDIA, Groq, Gemini, OpenRouter, DeepSeek, Kimi, OpenAI, Claude;
- **Claude** — Claude Code на аккаунте пользователя (`claude -p`), сессия продолжается на весь чат;
- **Antigravity** — через agentapi самого Antigravity (мост запускается из его терминала: `--link-antigravity`,
  или сам при каждом старте Antigravity после `launch-studio.py --install-antigravity-autostart` / кнопки «Запускать автоматически»);
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

Если установлен [MindKit](https://github.com/tagiriskaliev18-hash/MindTagSystem/blob/main/docs/MINDKIT.md), Mind берёт ключи и из
**связки ключей Mind** (`mindkit keychain set GROQ_API_KEY`), общей для всех проектов экосистемы.

## Handoff

С MindKit в шапке Mind Studio появляется кнопка **«Продолжить на другом устройстве»**: разговор уходит по MindLink
на другие свои устройства и там сам появляется в списке с пометкой «⇄».

```powershell
pip install "mindkit[full] @ git+https://github.com/tagiriskaliev18-hash/MindTagSystem"
mindkit link init          # на первом устройстве; на остальных: mindkit link join КЛЮЧ
mindkit link autostart on
```

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

## 🌐 Часть экосистемы MindTagSystem

Mind IDE входит в **[MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem)** — экосистему для программистов, которую создаёт **Тагир Искалиев** ([@tagiriskaliev18-hash](https://github.com/tagiriskaliev18-hash)): своя операционная система, браузер, IDE, ИИ-ядро и приложения, которые работают вместе и которые можно встроить в любое устройство.

**Роль в экосистеме:** собственная среда разработки экосистемы (слой «Инструменты разработчика»).

| Слой | Проект | Что делает |
|---|---|---|
| Платформа | [AIsktagOS](https://github.com/tagiriskaliev18-hash/AisktagOS) | Операционная система для разработчиков в стиле macOS на любом железе |
| Инструменты разработчика | **Mind IDE** ← вы здесь | ИИ-среда разработки: один чат с моделями, Claude Code и Antigravity |
| Инструменты разработчика | [ITIS Browser](https://github.com/tagiriskaliev18-hash/ITIS-browser) | Браузер с ИИ-агентом, который сам кликает и листает страницы |
| ИИ-ядро | [AI Duo (multimodel-agent)](https://github.com/tagiriskaliev18-hash/multimodel-agent) | Единый ИИ-шлюз с OpenAI-совместимым API для всех моделей |
| ИИ-ядро | [Antigravity ↔ Claude Code Bridge](https://github.com/tagiriskaliev18-hash/antigravity-claude-bridge) | MCP-мост, который связывает Antigravity, Claude Code и пул моделей |
| ИИ-ядро | [Qwen 14B Coder Dev](https://github.com/tagiriskaliev18-hash/qwen14b-coder-dev) | Локальная офлайн-модель для программирования в Ollama |
| Приложения | [FileHub AI](https://github.com/tagiriskaliev18-hash/filehub-ai) | Хранилище файлов с ИИ-агентом для Word, PowerPoint и Excel |
| Приложения | [SortApp (анализатор логов)](https://github.com/tagiriskaliev18-hash/sortapp) | Анализатор журналов доступа к сетевым папкам с отчётами Excel |
| Приложения | [ИИ Доктор (medical-ai-assistant)](https://github.com/tagiriskaliev18-hash/medical-ai-assistant) | Офлайн-ассистент врача приёмного покоя |

Как проекты связаны между собой: [архитектура MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem/blob/main/docs/ARCHITECTURE.md). Автор всех проектов экосистемы — Тагир Искалиев.

---

© Тагир Искалиев, создатель MindTagSystem.
