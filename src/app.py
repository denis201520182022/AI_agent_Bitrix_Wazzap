# src/app.py
import os
from dotenv import load_dotenv
load_dotenv()
import asyncio
import json
import logging
import traceback
from contextlib import asynccontextmanager
import aioconsole
from fastapi import FastAPI, Request, Depends
# ИЗМЕНЕНО: Импортируем асинхронные компоненты
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select # ИЗМЕНЕНО: Импортируем select для async-запросов
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta
from database import db_service
# ИЗМЕНЕНО: Импортируем асинхронную фабрику сессий
from database.db import AsyncSessionLocal
from database.models import Dialog # ИЗМЕНЕНО: Импортируем модель для запросов
from services import bitrix_service, wazzup_service, llm_service, prompt_service
from utils import parse_form_data, normalize_phone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# ИЗМЕНЕНО: URL для APScheduler теперь должен быть без +asyncpg, если используется стандартный SQLAlchemy
DATABASE_URL_SCHEDULER = os.getenv("DATABASE_URL").replace("+asyncpg", "")
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import redis.asyncio as aioredis


# --- Настройка логирования ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# --- ЗАГРУЗКА НАСТРОЕК ИЗ .ENV ---
TARGET_FUNNEL_ID = os.getenv("TARGET_FUNNEL_ID") 
WELCOME_STAGE_ID = f"C{TARGET_FUNNEL_ID}:NEW" 
NEW_LOT_STAGE_ID = os.getenv("NEW_LOT_STAGE_ID")
TOUCH_TODAY_STAGE_ID = os.getenv("TOUCH_TODAY_STAGE_ID")


# --- Redis Configuration ---
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
BOT_ID = "alpha_project"

REDIS_KEYS = {
    "limit": f"bot:{BOT_ID}:limit",
    "count": f"bot:{BOT_ID}:count",
    "warning": f"bot:{BOT_ID}:warning_sent"
}

async def get_redis_client():
    return aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True
    )

# --- КНИГА РЕЦЕПТОВ ДЛЯ ПРОМПТОВ (без изменений) ---
BASE_PROMPTS = [
    "#start#",
    "#style#",
    "#BASE#",
    "#BASE_KNOW#"
]
PROMPT_RECIPES = {
    "d1_awaiting_greeting_response": ["#Dialog1#"],
    "d1_awaiting_debtor_clarification": ["#Dialog1#"],
    "d1_awaiting_debtor_details": ["#Dialog1#"],
    "d2_awaiting_lot_response": ["#Dialog2#"],
    "d3_awaiting_touch_response": ["#Dialog3#"],
    "d3_awaiting_details_after_touch": ["#Dialog3#"],
    "default": [] 
}


# --- ЗАВИСИМОСТЬ ДЛЯ ПОЛУЧЕНИЯ АСИНХРОННОЙ СЕССИИ БД ---
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except SQLAlchemyError as e:
            logger.error(f"Ошибка БД в зависимости get_db: %s", e)
            await session.rollback()
            raise


# --- ВОРКЕР ДЛЯ ВВОДА ИЗ КОНСОЛИ ---
async def console_input_worker():
    logger.info("👨‍💻 Воркер консольного ввода запущен. Введите 'exit' для выхода.")
    
    chat_id = await aioconsole.ainput("Введите номер телефона (chat_id) для этого сеанса тестирования: ")
    if not chat_id:
        chat_id = "79991234567"
    logger.info(f"Тестирование диалога для: {chat_id}. Теперь можете отправлять сообщения.")

    while True:
        try:
            message_text = await aioconsole.ainput(f"[{chat_id}] > ")

            if message_text.lower() == 'exit':
                break
            if not message_text:
                continue

            # ИЗМЕНЕНО: Используем асинхронный контекстный менеджер для сессии
            async with AsyncSessionLocal() as db:
                await db_service.add_pending_message(db, chat_id, message_text)
            logger.info(f">>> Сообщение от [КОНСОЛЬ/{chat_id}] добавлено в очередь.")

        except Exception as e:
            logger.error(f"Ошибка в воркере консольного ввода: {e}")


# --- ВОРКЕР ДЛЯ ЕЖЕМЕСЯЧНОЙ ПРОВЕРКИ ---
async def monthly_check_worker():
    logger.info("🗓️ Воркер ежемесячной проверки запущен!")
    
    escalation_stage_id = os.getenv("TOUCH_TODAY_STAGE_ID")
    if not escalation_stage_id:
        logger.critical("❌ ОСТАНОВКА ВОРКЕРА ЕЖЕМЕСЯЧНОЙ ПРОВЕРКИ: Переменная TOUCH_TODAY_STAGE_ID не задана в .env.")
        return

    # ИЗМЕНЕНО: Используем асинхронный контекстный менеджер
    async with AsyncSessionLocal() as db:
        try:
            one_month_ago = datetime.now() - timedelta(days=30)
            
            # ИЗМЕНЕНО: Асинхронный синтаксис для запроса SQLAlchemy
            stmt = select(Dialog).filter(
                Dialog.updated_at < one_month_ago,
                Dialog.deal_id.isnot(None),
                Dialog.manager_id.isnot(None)
            )
            result = await db.execute(stmt)
            inactive_dialogs = result.scalars().all()

            if not inactive_dialogs:
                logger.info("  - Нет диалогов, неактивных более месяца.")
                return

            logger.info(f"  - Найдено {len(inactive_dialogs)} диалогов для обработки.")

            for dialog in inactive_dialogs:
                try:
                    deal_id = dialog.deal_id
                    
                    # ИЗМЕНЕНО: Добавлен await
                    deal_details = await bitrix_service.get_deal_details_2(deal_id)
                    if not deal_details:
                        logger.warning(f"      - Пропускаю сделку {deal_id}: не удалось получить детали.")
                        continue
                    
                    # ИЗМЕНЕНО: Добавлен await
                    move_success = await bitrix_service.move_deal_to_stage(deal_id, escalation_stage_id)
                    if move_success:
                        logger.info(f"      - ✅ Сделка {deal_id} перемещена на '{escalation_stage_id}'.")
                        comment_text = f"[Чат-бот]: Автоматический перевод сделки из-за отсутствия активности более месяца по диалогу {dialog.chat_id}."
                        # ИЗМЕНЕНО: Добавлен await
                        await bitrix_service.add_comment_to_deal(deal_id, comment_text)
                        
                        dialog.needs_reminder = False
                        dialog.reminder_level = 0
                        dialog.current_state = 'awaiting_touch_today_response'
                        db.add(dialog)
                        await db.commit()
                        logger.info(f"      - Диалог {dialog.chat_id} обновлен в БД.")
                    else:
                        logger.error(f"      - ❌ Не удалось переместить сделку {deal_id}.")
                        # ИЗМЕНЕНО: Добавлен await
                        await bitrix_service.add_comment_to_deal(
                            deal_id,
                            f"[Чат-бот] Ошибка: Не удалось автоматически перевести сделку из-за неактивности."
                        )
                except Exception as inner_e:
                    logger.error(f"❌ ОШИБКА при обработке диалога {dialog.chat_id} в monthly_worker: %s", inner_e)

        except Exception as e:
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА В ВОРКЕРЕ ЕЖЕМЕСЯЧНОЙ ПРОВЕРКИ: %s", e)
            logger.critical(traceback.format_exc())
            await db.rollback()

    logger.info("✅ Воркер ежемесячной проверки завершен.")


# --- ВОРКЕР-ДИСПЕТЧЕР ---
async def process_pending_messages_worker():
    logger.info("🚀 Воркер-ДИСПЕТЧЕР запущен!")
    while True:
        # ИЗМЕНЕНО: Используем асинхронный контекстный менеджер
        async with AsyncSessionLocal() as db:
            try:
                # ИЗМЕНЕНО: Добавлен await
                dialog_batches = await db_service.get_and_clear_pending_dialogs(db, delay_seconds=10)
                
                for batch in dialog_batches:
                    dialog = batch['dialog']
                    # ---> НАЧАЛО ВСТАВКИ <---
                    # Получаем актуальные детали сделки ПЕРЕД обработкой
                    deal_details = await bitrix_service.get_deal_details(dialog.deal_id) 
                    if deal_details and deal_details.get("STAGE_ID") == "C11:UC_W9JHF3":
                        logger.warning(f"Останавливаю обработку для {dialog.chat_id}: сделка {dialog.deal_id} в стадии 'Номиналы'.")
                        # Опционально: можно эскалировать или просто пропустить
                        continue # Переходим к следующему диалогу

                    pending_messages = batch['pending']
                    if dialog.current_state in ['escalated', 'scenario_complete']:
                        logger.warning(f"Диалог {dialog.chat_id} в состоянии '{dialog.current_state}'. Обработка прекращена.")
                        continue

                    logger.info(f"Обработка {len(pending_messages)} сообщений для {dialog.chat_id} в состоянии '{dialog.current_state}'")

                    # 1. ПОДГОТОВКА КОНТЕКСТА
                    # ИЗМЕНЕНО: Добавлен await
                    current_history = await db_service.get_dialog_history(db, dialog.chat_id)
                    for msg in pending_messages:
                        current_history.append({"role": "user", "content": msg['content']})

                    # 2. ПОЛУЧЕНИЕ РЕШЕНИЯ ОТ LLM
                    # ИЗМЕНЕНО: Выполняем блокирующий вызов в отдельном потоке
                    prompt_library = await asyncio.to_thread(prompt_service.get_prompt_library)
                    # ИЗМЕНЕНО: Добавлен await
                    current_dialog_state = await db_service.get_dialog_state(db, dialog.chat_id)

                    specific_keys = PROMPT_RECIPES.get(current_dialog_state, PROMPT_RECIPES.get("default", []))
                    required_keys = BASE_PROMPTS + specific_keys
                    prompt_parts = [prompt_library.get(key, "") for key in required_keys]
                    all_prompts_text = "\n\n".join(filter(None, prompt_parts))
                    system_prompt_addition = f"\n\nТекущее состояние диалога: {current_dialog_state}. Действуй строго в соответствии с этим состоянием."
                    final_system_prompt = all_prompts_text + system_prompt_addition
                    
                    # llm_service уже асинхронный, await на месте
                    llm_decision = await llm_service.get_bot_decision(current_history, final_system_prompt)
                    if llm_decision:
                        # ИЗМЕНЕНО: Добавлен await
                        await db_service.log_llm_response(db, dialog.chat_id, llm_decision)

                    if not llm_decision:
                        logger.error(f"❌ LLM не вернул решение для {dialog.chat_id}. Эскалация.")
                        if dialog.deal_id and dialog.manager_id:
                            # ИЗМЕНЕНО: Добавлен await
                            await bitrix_service.escalate_deal_to_manager(
                                dialog.deal_id, dialog.manager_id,
                                f"Критическая ошибка: LLM не смог сгенерировать решение для диалога {dialog.chat_id}."
                            )
                        continue

                    response_text = llm_decision.get("response_text")
                    action = llm_decision.get("action")
                    action_params = llm_decision.get("action_params", {})
                    new_state = llm_decision.get("new_state", dialog.current_state)
                    deal_id, manager_id = dialog.deal_id, dialog.manager_id

                    if not (deal_id and manager_id):
                        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА: {dialog.chat_id} нет deal_id/manager_id. Пропуск.")
                        continue

                    if response_text:
                        # ИЗМЕНЕНО: Добавлен await
                        success = await wazzup_service.send_message(dialog.chat_id, response_text)
                        if success:
                            current_history.append({"role": "assistant", "content": response_text})
                        else:
                            logger.error(f"❌ Не удалось отправить сообщение {dialog.chat_id}. Эскалация.")
                            # ИЗМЕНЕНО: Добавлен await
                            await bitrix_service.escalate_deal_to_manager(
                                deal_id, manager_id,
                                f"Критическая ошибка: Не удалось отправить сообщение клиенту {dialog.chat_id} через Wazzup."
                            )
                    
                    comment = action_params.get("comment_text")
                    task_subject = action_params.get("task_subject")
                    task_desc = action_params.get("task_description")

                    logger.info(f"  - Действие для CRM: {action} для сделки {deal_id}")

                    if action == "LOG_COMMENT" and comment:
                        await bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {comment}")
                    elif action == "CREATE_TASK_AND_LOG":
                        if comment: await bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {comment}")
                        if task_desc and task_subject: await bitrix_service.create_activity_for_deal(deal_id, manager_id, task_subject, task_desc)
                    elif action == "ESCALATE_TO_MANAGER":
                        reason = comment or "Причина эскалации не указана."
                        await bitrix_service.escalate_deal_to_manager(deal_id, manager_id, reason)

                    states_that_need_reminders = ["d1_awaiting_greeting_response", "d1_awaiting_debtor_clarification", "d1_awaiting_debtor_details", "d2_awaiting_lot_response", "d3_awaiting_touch_response", "d3_awaiting_details_after_touch"]
                    should_remind = new_state in states_that_need_reminders

                    # ИЗМЕНЕНО: Добавлен await
                    await db_service.update_dialog(db, dialog.chat_id, new_state, current_history, needs_reminder=should_remind)
                    logger.info(f"  - Диалог {dialog.chat_id} переведен в состояние '{new_state}'.")
            
            except Exception as e:
                logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА В ВОРКЕРЕ-ДИСПЕТЧЕРЕ: %s", e)
                logger.critical(traceback.format_exc())
                await db.rollback()
        
        await asyncio.sleep(5)


# --- ВОРКЕР-НАПОМИНАТОР ---
async def reminder_worker():
    logger.info("⏰ Воркер-НАПОМИНАТОР запущен!")
    # ИЗМЕНЕНО: Используем асинхронный контекстный менеджер
    async with AsyncSessionLocal() as db:
        try:
            # ИЗМЕНЕНО: Добавлен await
            dialogs_to_remind = await db_service.get_due_reminders(db)
            if not dialogs_to_remind:
                logger.info("  - Нет диалогов для напоминания.")
                return

            logger.info(f"  - Найдено {len(dialogs_to_remind)} диалогов для напоминания.")
            
            for dialog in dialogs_to_remind:
                try:
                    # ИЗМЕНЕНО: Добавлен await
                    deal_details = await bitrix_service.get_deal_details_2(dialog.deal_id)
                    if not deal_details:
                        logger.warning(f"  - Пропуск {dialog.chat_id}: не удалось получить детали сделки.")
                        continue
                    
                    if deal_details and deal_details.get("STAGE_ID") == "C11:UC_W9JHF3":
                        logger.warning(f"Отменяю напоминание для {dialog.chat_id}: сделка {dialog.deal_id} в стадии 'Номиналы'.")
                        dialog.needs_reminder = False # Отключаем дальнейшие напоминания
                        await db.commit()
                        continue
                    contact_id = int(deal_details.get("CONTACT_ID")) if deal_details.get("CONTACT_ID") else None
                    company_id = int(deal_details.get("COMPANY_ID")) if deal_details.get("COMPANY_ID") else None
                    client_name = "Уважаемый клиент"

                    if company_id:
                        # ИЗМЕНЕНО: Добавлен await
                        company_details = await bitrix_service.get_company_details(company_id)
                        if company_details:
                            client_name = company_details.get("TITLE", client_name)
                    
                    if client_name == "Уважаемый клиент" and contact_id:
                        # ИЗМЕНЕНО: Добавлен await
                        contact_details = await bitrix_service.get_contact_details_2(contact_id)
                        if contact_details:
                            client_name = contact_details.get("NAME", client_name)
                    
                    reminder_level = dialog.reminder_level
                    instruction = None
                    if reminder_level == 0: instruction = "initiate_reminder_1h"
                    elif reminder_level == 1: instruction = f"initiate_reminder_3h. ИМЯ_КЛИЕНТА: {client_name}."
                    elif reminder_level == 2: instruction = f"initiate_reminder_24h. ИМЯ_КЛИЕНТА: {client_name}."
                    else: continue

                    logger.info(f"    - Отправка напоминания уровня {reminder_level + 1} для {dialog.chat_id}")
                    # ИЗМЕНЕНО: Добавлен await
                    if await db_service.has_pending_messages(db, dialog.chat_id):
                        logger.info(f"    - ОТМЕНА: Клиент ответил. Сброс для {dialog.chat_id}")
                        dialog.needs_reminder = False
                        dialog.reminder_level = 0
                        await db.commit()
                        continue
                    
                    if instruction:
                        # ИЗМЕНЕНО: Выполняем блокирующий вызов в отдельном потоке
                        prompt_library = await asyncio.to_thread(prompt_service.get_prompt_library)
                        required_keys = ["#start#", "#style#", "#Reminder#"]
                        prompt_parts = [prompt_library.get(key, "") for key in required_keys]
                        all_prompts_text = "\n\n".join(filter(None, prompt_parts))
                        
                        llm_decision = await llm_service.get_bot_decision([{"role": "system", "content": instruction}], all_prompts_text)
                        reminder_text = llm_decision.get("response_text") if llm_decision else None
                        
                        if reminder_text:
                            # ИЗМЕНЕНО: Добавлен await
                            success = await wazzup_service.send_message(dialog.chat_id, reminder_text)
                            if success:
                                dialog.reminder_level += 1
                                # ИЗМЕНЕНО: Добавлен await
                                await db_service.add_message_to_history(db, dialog.chat_id, "assistant", reminder_text)
                            else:
                                logger.error(f"      - НЕ УДАЛОСЬ отправить напоминание {dialog.chat_id}.")
                                # ИЗМЕНЕНО: Добавлен await
                                await bitrix_service.escalate_deal_to_manager(dialog.deal_id, dialog.manager_id, "Ошибка отправки напоминания Wazzup.")
                        else:
                            logger.error(f"      - LLM не сгенерировал текст напоминания для {dialog.chat_id}.")
                            # ИЗМЕНЕНО: Добавлен await
                            await bitrix_service.escalate_deal_to_manager(dialog.deal_id, dialog.manager_id, "Ошибка LLM при генерации напоминания.")
                    
                    if dialog.reminder_level >= 3:
                        logger.warning(f"    - ПОСЛЕДНЕЕ НАПОМИНАНИЕ. Эскалация на менеджера для {dialog.chat_id}.")
                        # ИЗМЕНЕНО: Добавлен await
                        await bitrix_service.escalate_deal_to_manager(dialog.deal_id, dialog.manager_id, "Клиент не отвечает на напоминания.")
                        dialog.needs_reminder = False
                    
                    await db.commit()
                except Exception as inner_e:
                    logger.error(f"❌ ОШИБКА при обработке диалога {dialog.chat_id} в reminder_worker: %s", inner_e)
                    await db.rollback()
        
        except Exception as e:
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА В ВОРКЕРЕ НАПОМИНАНИЙ: %s", e)
            logger.critical(traceback.format_exc())
            await db.rollback()


# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Приложение запускается...")
    
    jobstores = {'default': SQLAlchemyJobStore(url=DATABASE_URL_SCHEDULER)}
    scheduler = AsyncIOScheduler(jobstores=jobstores)
    scheduler.add_job(reminder_worker, 'interval', minutes=1, id='reminder_job', replace_existing=True)
    scheduler.add_job(monthly_check_worker, 'cron', day=1, hour=12, minute=0, id='monthly_check_job', replace_existing=True) # ИЗМЕНЕНО: на 1-е число месяца
    scheduler.start()
    logger.info("✅ Планировщик APScheduler запущен.")
    
    worker_tasks = []
    task = asyncio.create_task(process_pending_messages_worker())
    worker_tasks.append(task)
    logger.info("✅ Воркер process_pending_messages_worker запущен.")

    if os.getenv("DEBUG_CONSOLE_MODE") == "true":
        task = asyncio.create_task(console_input_worker())
        worker_tasks.append(task)
        logger.warning("✅ ВНИМАНИЕ: Запущен режим отладки в консоли!")

    yield
    
    logger.info("Приложение останавливается...")
    scheduler.shutdown()
    for task in worker_tasks:
        task.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)
    logger.info("Все воркеры и планировщик остановлены.")
    logger.info("Приложение остановлено.")

app = FastAPI(title="Bitrix Wazzup Bot", lifespan=lifespan)

@app.get("/")
async def read_root():
    return {"status": "ok", "message": "Bot is running"}


# --- ОБРАБОТЧИК ВЕБХУКОВ BITRIX24 ---
@app.post("/webhook/bitrix")
async def handle_bitrix_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    deal_id_str = "Unknown"
    try:
        form_data = await request.form()
        data = parse_form_data(form_data)
        
        if data.get("event") != "ONCRMDEALUPDATE":
            return {"status": "ok", "message": "Event ignored"}

        deal_id_str = data.get("data", {}).get("FIELDS", {}).get("ID")
        if not deal_id_str:
            return {"status": "error", "message": "No deal ID"}
        
        deal_id = int(deal_id_str)
        logger.info(f"Получен вебхук ONCRMDEALUPDATE для сделки ID: {deal_id}")

        r = await get_redis_client()
        # ИЗМЕНЕНО: Добавлен await
        deal_details = await bitrix_service.get_deal_details_2(deal_id)
        if not deal_details:
            return {"status": "error", "message": "Failed to get deal details"}

        current_funnel_id = str(deal_details.get("CATEGORY_ID"))
        current_stage = deal_details.get("STAGE_ID")
        
        # --- ОБЩАЯ ЛОГИКА ДЛЯ ВСЕХ СЦЕНАРИЕВ ---
        is_target_scenario = current_funnel_id == TARGET_FUNNEL_ID and current_stage in [WELCOME_STAGE_ID, NEW_LOT_STAGE_ID, TOUCH_TODAY_STAGE_ID]

        if is_target_scenario:
            logger.info(f"✅✅✅ ТРИГГЕР СЦЕНАРИЯ СРАБОТАЛ: Сделка {deal_id} на стадии '{current_stage}'.")
            
            #--- ПРОВЕРКА ЛИМИТА (РАСКОММЕНТИРОВАНО И ИНТЕГРИРОВАНО) ---
            try:
                current_limit = int(await r.get(REDIS_KEYS["limit"]) or 0)
                current_count = int(await r.get(REDIS_KEYS["count"]) or 0)

                if current_limit > 0 and current_count >= current_limit:
                    logger.warning(f"❌ СЦЕНАРИЙ для сделки {deal_id} ПРОПУЩЕН: Лимит исчерпан ({current_count}/{current_limit}).")
                    await bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Сценарий пропущен: лимит исчерпан.")
                    return {"status": "ok", "message": "Limit exceeded"}
                
                await r.incr(REDIS_KEYS["count"])
                logger.info(f"  - Лимит использования увеличен: {current_count + 1}/{current_limit}.")
            except Exception as redis_e:
                logger.error(f"❌ Ошибка Redis для сделки {deal_id}: {redis_e}")
                await bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка при проверке лимита (Redis).")
                return {"status": "error", "message": "Redis error"}

            contact_id = int(deal_details.get("CONTACT_ID")) if deal_details.get("CONTACT_ID") else None
            company_id = int(deal_details.get("COMPANY_ID")) if deal_details.get("COMPANY_ID") else None
            manager_id = int(deal_details.get("ASSIGNED_BY_ID")) if deal_details.get("ASSIGNED_BY_ID") else None
            
            if not manager_id:
                await bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: отсутствует ID менеджера.")
                return {"status": "error", "message": "No manager ID"}

            client_phone, client_name = None, "Уважаемый клиент"

            if company_id:
                # ИЗМЕНЕНО: Добавлен await
                company_details = await bitrix_service.get_company_details(company_id)
                if company_details and company_details.get("PHONE"):
                    client_phone = normalize_phone(company_details["PHONE"][0]["VALUE"])
                    client_name = company_details.get("TITLE", client_name)
            
            if not client_phone and contact_id:
                # ИЗМЕНЕНО: Добавлен await
                contact_details = await bitrix_service.get_contact_details_2(contact_id)
                if contact_details and contact_details.get("PHONE"):
                    client_phone = normalize_phone(contact_details["PHONE"][0]["VALUE"])
                    client_name = contact_details.get("NAME", client_name)
            
            if not client_phone:
                await bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: не найден номер телефона клиента.")
                return {"status": "ok", "message": "No client phone number found"}
            
            # ИЗМЕНЕНО: Добавлен await
            manager = await bitrix_service.get_user_details(manager_id)
            manager_name = f"{manager.get('NAME', '')} {manager.get('LAST_NAME', '')}".strip() if manager else "Ваш менеджер"
            
            logger.info(f"  - Клиент: {client_name} ({client_phone}), Менеджер: {manager_name} ({manager_id})")

            # --- ВЫБОР СЦЕНАРИЯ ---
            instruction_content = ""
            required_prompt_keys = []

            if current_stage == WELCOME_STAGE_ID:
                instruction_content = f"initiate_dialog. ИМЯ_КЛИЕНТА: {client_name}. ИМЯ_МЕНЕДЖЕРА: {manager_name}."
                required_prompt_keys = ["#start#", "#style#", "#Dialog1#"]
            
            elif current_stage == NEW_LOT_STAGE_ID:
                # ИЗМЕНЕНО: Добавлен await
                latest_activity = await bitrix_service.get_latest_activity_for_deal(deal_id)
                if not latest_activity or not latest_activity.get("DESCRIPTION"):
                    await bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: не найдено дело с описанием.")
                    return {"status": "ok", "message": "No activity for new lot scenario"}
                debtor_name_info = latest_activity["DESCRIPTION"]
                instruction_content = f"initiate_new_lot_dialog. ИМЯ_КЛИЕНТА: {client_name}. ИМЯ_МЕНЕДЖЕРА: {manager_name}. Для клиента {client_name} появился новый лот/должник ИНФОРМАЦИЯ О ДОЛЖНИКЕ: {debtor_name_info}."
                required_prompt_keys = ["#start#", "#style#", "#Dialog2#"]
            
            elif current_stage == TOUCH_TODAY_STAGE_ID:
                instruction_content = f"initiate_touch_today_dialog. ИМЯ_КЛИЕНТА: {client_name}. ИМЯ_МЕНЕДЖЕРА: {manager_name}."
                required_prompt_keys = ["#start#", "#style#", "#Dialog3#"]
            dialog = await db_service.get_or_create_dialog(db, client_phone, deal_id, manager_id, current_funnel_id)
            # --- ОБЩАЯ ОБРАБОТКА И ОТПРАВКА ---
            # ИЗМЕНЕНО: Добавлен await
            current_history = await db_service.get_dialog_history(db, client_phone)
            current_history.append({"role": "system", "content": instruction_content})
            
            # ИЗМЕНЕНО: Выполняем блокирующий вызов в отдельном потоке
            prompt_library = await asyncio.to_thread(prompt_service.get_prompt_library)
            prompt_parts = [prompt_library.get(key, "") for key in required_prompt_keys]
            all_prompts_text = "\n\n".join(filter(None, prompt_parts))

            llm_decision = await llm_service.get_bot_decision(current_history, all_prompts_text)
            if llm_decision:
                # ИЗМЕНЕНО: Добавлен await
                await db_service.log_llm_response(db, client_phone, llm_decision)
            
            if not llm_decision:
                await bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: LLM не сгенерировал ответ.")
                await bitrix_service.escalate_deal_to_manager(deal_id, manager_id, "Критическая ошибка: LLM не сгенерировал ответ.")
                return {"status": "error", "message": "LLM failed"}
            
            response_text = llm_decision.get("response_text")
            action = llm_decision.get("action")
            action_params = llm_decision.get("action_params", {})
            new_state = llm_decision.get("new_state")
            if new_state is None:
                new_state = dialog.current_state
            if response_text:
                # ИЗМЕНЕНО: Добавлен await
                success = await wazzup_service.send_message(client_phone, response_text)
                if not success:
                    await bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка! Не удалось отправить сообщение на {client_phone}.")
                    await bitrix_service.escalate_deal_to_manager(deal_id, manager_id, f"Критическая ошибка Wazzup: не удалось отправить сообщение на {client_phone}.")
                    return {"status": "ok", "message": "Wazzup send failed"}
            
            if action == "LOG_COMMENT" and action_params.get("comment_text"):
                await bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {action_params['comment_text']}")

            # ИЗМЕНЕНО: Добавлен await
            await db_service.get_or_create_dialog(db, client_phone, deal_id, manager_id, current_funnel_id)
            if response_text:
                # ИЗМЕНЕНО: Добавлен await
                current_history = await db_service.get_dialog_history(db, client_phone)
                current_history.append({"role": "assistant", "content": response_text})
            
            states_that_need_reminders = ["d1_awaiting_greeting_response", "d1_awaiting_debtor_clarification", "d1_awaiting_debtor_details", "d2_awaiting_lot_response", "d3_awaiting_touch_response", "d3_awaiting_details_after_touch"]
            should_remind = new_state in states_that_need_reminders

            # ИЗМЕНЕНО: Добавлен await
            await db_service.update_dialog(db, client_phone, new_state, current_history, needs_reminder=should_remind)
            logger.info(f"✅ Сценарий для сделки {deal_id} успешно запущен. Диалог {client_phone} переведен в состояние '{new_state}'.")
    
    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА в Bitrix вебхуке для deal_id {deal_id_str}: %s", e)
        logger.critical(traceback.format_exc())
        return {"status": "error", "message": f"Critical internal error: {e}"}

    return {"status": "ok", "message": "Webhook processed"}


# --- ОБРАБОТЧИК WAZZUP ---
@app.post("/webhook/wazzup")
async def handle_wazzup_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        data = await request.json()
        if data.get("test") or "messages" not in data or not data["messages"]:
            return {"status": "ok"}
        
        message = data["messages"][0]
        if message.get("isEcho"):
            return {"status": "ok"}

        text = message.get("text")
        chat_id = message.get("chatId")
        
        if text and chat_id:
            normalized_phone = normalize_phone(chat_id)
            # ИЗМЕНЕНО: Добавлены await
            await db_service.log_user_message(db, normalized_phone, text)
            await db_service.add_pending_message(db, normalized_phone, text)
            logger.info(f">>> Сообщение от {normalized_phone} добавлено в очередь.")
        
    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА в Wazzup вебхуке: %s", e)
        logger.critical(traceback.format_exc())
        return {"status": "error", "message": "Internal server error"}
    
    return {"status": "ok"}