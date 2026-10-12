# src/database/db_service.py

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, timedelta
from .models import Dialog

logger = logging.getLogger(__name__)

async def get_or_create_dialog(db: AsyncSession, chat_id: str, deal_id: int = None, manager_id: int = None, funnel_id: str = None) -> Dialog:
    """
    (Асинхронно) Находит диалог по chat_id. Если не находит - создает новый.
    Если находит, может обновить информацию о сделке.
    """
    stmt = select(Dialog).where(Dialog.chat_id == chat_id)
    result = await db.execute(stmt)
    dialog = result.scalars().first()

    if not dialog:
        dialog = Dialog(
            chat_id=chat_id,
            deal_id=deal_id,
            manager_id=manager_id,
            funnel_id=funnel_id
        )
        db.add(dialog)
        logger.info(
            f"🆕 Создан новый диалог в БД", 
            extra={"chat_id": chat_id, "deal_id": deal_id}
        )
    else:
        updated = False
        if deal_id: 
            dialog.deal_id = deal_id
            updated = True
        if manager_id: 
            dialog.manager_id = manager_id
            updated = True
        if funnel_id: 
            dialog.funnel_id = funnel_id
            updated = True
        
        if updated:
            logger.info(
                f"🔄 Обновлена информация о сделке в диалоге", 
                extra={"chat_id": chat_id, "deal_id": deal_id}
            )

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
        prev_state = dialog.current_state
        dialog.current_state = new_state
        dialog.history = new_history
        flag_modified(dialog, "history")
        
        dialog.reminder_level = 0 
        dialog.needs_reminder = needs_reminder
        
        await db.commit()
        logger.info(
            f"✅ Диалог обновлен", 
            extra={
                "chat_id": chat_id, 
                "old_state": prev_state, 
                "new_state": new_state, 
                "needs_reminder": needs_reminder
            }
        )
    else:
        logger.warning(
            f"⚠️ Попытка обновить несуществующий диалог", 
            extra={"chat_id": chat_id}
        )


async def add_message_to_history(db: AsyncSession, chat_id: str, role: str, content: str):
    """
    (Асинхронно) Добавляет одно сообщение в историю диалога.
    """
    dialog = await get_or_create_dialog(db, chat_id)
    dialog.history.append({"role": role, "content": content})
    flag_modified(dialog, "history")
    await db.commit()
    content_preview = content[:50] + "..." if len(content) > 50 else content
    logger.info(
        f"📝 Сообщение сохранено в историю", 
        extra={"chat_id": chat_id, "role": role, "content_preview": content_preview}
    )


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
    dialog.pending_since = datetime.utcnow() 
    
    flag_modified(dialog, "pending_messages")
    await db.commit()
    logger.info(
        f"📥 Сообщение добавлено в очередь обработки", 
        extra={"chat_id": chat_id}
    )


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
    
    if results:
        logger.info(
            f"⚡ Взято в обработку диалогов: {len(results)}", 
            extra={"count": len(results), "chat_ids": [d['dialog'].chat_id for d in results]}
        )
        
    return results


async def get_due_reminders(db: AsyncSession) -> list[Dialog]:
    """(Асинхронно) Находит диалоги, которые требуют напоминания."""
    now = datetime.now() 
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

async def get_dialog(db: AsyncSession, chat_id: str) -> Dialog | None:
    """
    (Асинхронно) Получает диалог по chat_id, если он существует.
    НЕ создает новый, если не найден.
    """
    stmt = select(Dialog).where(Dialog.chat_id == chat_id)
    result = await db.execute(stmt)
    return result.scalars().first()



async def log_event(db: AsyncSession, chat_id: str, event_type: str, data: dict):
    """
    Универсальная функция для записи любого события в full_log.
    event_type: 'user_message', 'llm_decision', 'error', 'system_action', 'webhook'
    """
    stmt = select(Dialog).filter(Dialog.chat_id == chat_id)
    result = await db.execute(stmt)
    dialog = result.scalar_one_or_none()
    
    if dialog:
        new_event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "details": data
        }
        if dialog.full_log is None:
            dialog.full_log = []
        
        dialog.full_log = dialog.full_log + [new_event]
        await db.commit()