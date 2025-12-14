# src/database/db_service.py

# Импортируем асинхронную сессию и конструктор запросов select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, timedelta
from .models import Dialog
from datetime import datetime 

# --- ВСЕ ФУНКЦИИ ПЕРЕПИСАНЫ НА АСИНХРОННЫЙ СТИЛЬ ---

async def get_or_create_dialog(db: AsyncSession, chat_id: str, deal_id: int = None, manager_id: int = None, funnel_id: str = None) -> Dialog:
    """
    (Асинхронно) Находит диалог по chat_id. Если не находит - создает новый.
    Если находит, может обновить информацию о сделке.
    """
    # 1. Создаем запрос с помощью select()
    stmt = select(Dialog).where(Dialog.chat_id == chat_id)
    # 2. Выполняем запрос асинхронно
    result = await db.execute(stmt)
    # 3. Извлекаем один скалярный (объект) результат
    dialog = result.scalars().first()

    if not dialog:
        dialog = Dialog(
            chat_id=chat_id,
            deal_id=deal_id,
            manager_id=manager_id,
            funnel_id=funnel_id
        )
        db.add(dialog)
        print(f"Создан новый диалог в БД для chat_id: {chat_id} (Сделка: {deal_id})")
    else:
        if deal_id: dialog.deal_id = deal_id
        if manager_id: dialog.manager_id = manager_id
        if funnel_id: dialog.funnel_id = funnel_id
        print(f"Найден существующий диалог для chat_id: {chat_id}. Информация о сделке обновлена.")

    # 4. Коммитим и обновляем объект асинхронно
    await db.commit()
    await db.refresh(dialog)
    return dialog


async def update_dialog(db: AsyncSession, chat_id: str, new_state: str, new_history: list, needs_reminder: bool = False):
    """
    (Асинхронно) Комплексно обновляет диалог: состояние, историю и флаг напоминания.
    """
    stmt = select(Dialog).where(Dialog.chat_id == chat_id)
    result = await db.execute(stmt)
    dialog = result.scalars().first()

    if dialog:
        dialog.current_state = new_state
        dialog.history = new_history
        flag_modified(dialog, "history")
        
        dialog.reminder_level = 0 
        dialog.needs_reminder = needs_reminder
        
        await db.commit()
        print(f"Диалог {chat_id} обновлен. Новое состояние: '{new_state}'. Напоминание: {needs_reminder}")
    else:
        print(f"⚠️ Попытка обновить несуществующий диалог: {chat_id}")


async def add_message_to_history(db: AsyncSession, chat_id: str, role: str, content: str):
    """
    (Асинхронно) Добавляет одно сообщение в историю диалога.
    """
    dialog = await get_or_create_dialog(db, chat_id)
    dialog.history.append({"role": role, "content": content})
    flag_modified(dialog, "history")
    await db.commit()
    print(f"Сообщение от '{role}' сохранено в историю для chat_id: {chat_id}")


async def log_user_message(db: AsyncSession, chat_id: str, content: str):
    """(Асинхронно) Логирует сообщение от пользователя в full_log."""
    dialog = await get_or_create_dialog(db, chat_id)
    log_entry = {
        "type": "user_message",
        "timestamp": datetime.now().isoformat(),
        "content": content
    }
    dialog.full_log.append(log_entry)
    flag_modified(dialog, "full_log")
    await db.commit()


async def log_llm_response(db: AsyncSession, chat_id: str, llm_response_json: dict):
    """(Асинхронно) Логирует полный JSON-ответ от LLM в full_log."""
    dialog = await get_or_create_dialog(db, chat_id)
    log_entry = {
        "type": "llm_response",
        "timestamp": datetime.now().isoformat(),
        "response": llm_response_json
    }
    dialog.full_log.append(log_entry)
    flag_modified(dialog, "full_log")
    await db.commit()


async def get_dialog_history(db: AsyncSession, chat_id: str) -> list:
    """
    (Асинхронно) Получает историю диалога для указанного chat_id.
    """
    # Эта функция вызывает другую асинхронную функцию, поэтому она тоже должна быть async
    dialog = await get_or_create_dialog(db, chat_id)
    return dialog.history


async def add_pending_message(db: AsyncSession, chat_id: str, content: str, file_url: str = None, file_name: str = None):
    """
    (Асинхронно) Добавляет входящее сообщение в очередь ожидания.
    """
    dialog = await get_or_create_dialog(db, chat_id)
    
    message_data = {"role": "user", "content": content}
    if file_url:
        message_data["file_url"] = file_url
    if file_name:
        message_data["file_name"] = file_name

    dialog.pending_messages.append(message_data)
    dialog.pending_since = datetime.utcnow() # func.now() работает и в async
    
    flag_modified(dialog, "pending_messages")
    await db.commit()
    print(f"Сообщение для {chat_id} добавлено в очередь.")


async def get_and_clear_pending_dialogs(db: AsyncSession, delay_seconds: int = 10) -> list[dict]:
    """
    (Асинхронно) Находит диалоги, ожидающие обработки, и очищает их очередь.
    """
    time_filter = Dialog.pending_since <= (datetime.utcnow() - timedelta(seconds=delay_seconds))
    
    stmt = select(Dialog).where(
        func.jsonb_array_length(Dialog.pending_messages) > 0,
        time_filter
    )
    result = await db.execute(stmt)
    dialogs_to_process = result.scalars().all()

    if not dialogs_to_process:
        return []

    results = []
    for dialog in dialogs_to_process:
        pending = list(dialog.pending_messages)
        dialog.pending_messages.clear()
        dialog.pending_since = None
        flag_modified(dialog, "pending_messages")
        results.append({'dialog': dialog, 'pending': pending})
    
    await db.commit()
    return results


async def get_due_reminders(db: AsyncSession) -> list[Dialog]:
    """(Асинхронно) Находит диалоги, которые требуют напоминания."""
    now = datetime.now() # Получаем время один раз для консистентности
    remind_level_1 = and_(Dialog.reminder_level == 0, Dialog.updated_at < now - timedelta(hours=1))
    remind_level_2 = and_(Dialog.reminder_level == 1, Dialog.updated_at < now - timedelta(hours=3))
    remind_level_3 = and_(Dialog.reminder_level == 2, Dialog.updated_at < now - timedelta(days=1))

    stmt = select(Dialog).where(
        Dialog.needs_reminder == True,
        or_(remind_level_1, remind_level_2, remind_level_3)
    )
    result = await db.execute(stmt)
    dialogs_to_remind = result.scalars().all()
    
    return dialogs_to_remind


async def has_pending_messages(db: AsyncSession, chat_id: str) -> bool:
    """(Асинхронно) Проверяет, есть ли сообщения в очереди для данного чата."""
    stmt = select(func.count()).select_from(Dialog).where(
        Dialog.chat_id == chat_id,
        func.jsonb_array_length(Dialog.pending_messages) > 0
    )
    result = await db.execute(stmt)
    count = result.scalar() or 0
    return count > 0


async def get_dialog_state(db: AsyncSession, chat_id: str) -> str | None:
    """
    (Асинхронно) Получает текущее состояние диалога по chat_id.
    """
    stmt = select(Dialog.current_state).where(Dialog.chat_id == chat_id)
    result = await db.execute(stmt)
    state = result.scalars().first()
    return state