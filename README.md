# AI Agent: Bitrix24 × Wazzup Integration

Асинхронный интеллектуальный сервис для автоматизации клиентских коммуникаций в мессенджерах (WhatsApp, Telegram) через шлюз Wazzup с двусторонней синхронизацией в CRM Bitrix24 и оркестрацией диалогов на базе LLM.

---

## 🎯 Назначение и функционал

Сервис выступает связующим интеллектуальным звеном между контактными каналами Wazzup и CRM Bitrix24:
- **Обработка вебхуков в реальном времени:** приём сообщений от клиентов из Wazzup и событий изменения сущностей (сделки, стадии, ответственные) из Bitrix24.
- **Оркестрация LLM-диалога:** генерация контекстных ответов на базе OpenAI GPT с учетом текущей стадии сделки, воронки и истории переписки.
- **Двусторонняя синхронизация с CRM:** автоматическое создание/обновление сделок в Bitrix24, фиксация контактных данных, перемещение по этапам воронки и эскалация диалогов на живых менеджеров.
- **Интеллектуальный Debouncing сообщений:** буферизация очереди быстрых последовательных сообщений от пользователя (`pending_messages`) с тайм-аутом на ожидание завершения мысли перед вызовом модели.
- **Многоуровневый Reminder Engine:** фоновый воркер автоматических напоминаний и дожима (`reminder_worker`) с отслеживанием интервалов и уровней касаний (`reminder_level`).
- **Динамический Prompt & Knowledge Base Management:** загрузка системных промптов и регламентов базы знаний напрямую из Google Docs API с поддержкой Markdown-таблиц и in-memory TTL-кэшированием.

---

## 🏗 Архитектура и поток данных

```
  [Клиент: WhatsApp / Telegram]
                 │
                 ▼
          [Wazzup Gateway]
                 │  (Webhook)
                 ▼
        ┌────────────────────────────────────────────────────────┐
        │                 FastAPI Core Service                   │
        │                                                        │
        │  • Event Router (/webhook/wazzup, /webhook/bitrix)     │
        │  • Message Batcher (Pending Queue Worker)              │
        │  • Reminder Worker (Follow-up Escalations)             │
        │  • Prompt Service (Google Docs API + In-memory Cache)  │
        │  • Bitrix Service (CRM REST API Client)                │
        └───────┬───────────────────────────┬──────────────┬─────┘
                │                           │              │
                ▼                           ▼              ▼
     [PostgreSQL + JSONB]            [Redis Cache]   [OpenAI LLM]
   (Диалоги, стейты, аудит-логи)   (Блокировки, стейт)
```

---

## ⚙️ Ключевые инженерные решения

- **Асинхронная архитектура:** неблокирующий ввод-вывод на базе `FastAPI`, `asyncio`, `asyncpg` и `SQLAlchemy 2.0 (AsyncSession)`.
- **JSONB-хранилище состояний:** компактное хранение истории переписки (`history`), технических аудит-логов (`full_log`) и входящего буфера (`pending_messages`) в специализированных полях PostgreSQL с индексацией по `chat_id` и `deal_id`.
- **Управление схемами БД:** версионирование и автоматическое применение миграций через `Alembic`.
- **Fault Tolerance:** политики повторных запросов к внешним API (Bitrix24, Wazzup, OpenAI) на базе `tenacity`, перехват необработанных исключений и телеметрия в `Sentry`.
- **Структурированная наблюдаемость:** JSON-логирование (`python-json-logger`) с контекстной трассировкой запросов через middleware.

---

## 🛠 Технологический стек

- **Core & Runtime:** Python 3.12+, FastAPI, Uvicorn, asyncio
- **База данных и кэширование:** PostgreSQL, SQLAlchemy 2.0 (Async), asyncpg, Alembic, Redis
- **AI & Внешние API:** OpenAI API, Google Docs API (google-api-python-client), Bitrix24 REST API, Wazzup Webhooks API
- **Надежность и мониторинг:** Tenacity, Sentry SDK, python-json-logger, APScheduler
