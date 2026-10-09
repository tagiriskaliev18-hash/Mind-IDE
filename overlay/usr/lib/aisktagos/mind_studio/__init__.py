"""Mind Studio — рабочее место в духе Antigravity поверх AIsktagOS.

Один чат обращается к нескольким агентам:
  • Mind     — маршрутизатор моделей aisktag_ai (локальная, NVIDIA, Groq, Gemini…, бесплатные первыми);
  • Claude   — Claude Code на аккаунте пользователя (claude -p), с продолжением сессии на весь чат;
  • Antigravity — через agentapi самого Antigravity (нужен мост, запущенный из его терминала);
  • «Все сразу» — агенты отвечают параллельно, Mind сводит лучший ответ.

Сервер — только стандартная библиотека Python (http.server + SSE), интерфейс — web/ в окне Qt WebEngine.
"""
