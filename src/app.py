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
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta # !!! ДОБАВИТЬ ЭТУ СТРОКУ
from database import db_service
from database.db import SessionLocal
from services import bitrix_service, wazzup_service, llm_service, prompt_service
from utils import parse_form_data, normalize_phone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
#_________________________________________________________________
import redis.asyncio as aioredis
#________________________________________________________


# --- Настройка логирования ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# --- ЗАГРУЗКА НАСТРОЕК ИЗ .ENV ---
TARGET_FUNNEL_ID = os.getenv("TARGET_FUNNEL_ID") 
WELCOME_STAGE_ID = f"C{TARGET_FUNNEL_ID}:NEW" 
NEW_LOT_STAGE_ID = os.getenv("NEW_LOT_STAGE_ID")
TOUCH_TODAY_STAGE_ID = os.getenv("TOUCH_TODAY_STAGE_ID")


# --- Redis Configuration for Alpha Project --- # ДОБАВИТЬ ЭТОТ БЛОК
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
BOT_ID = "alpha_project" # Уникальный ID этого бота для управления лимитами

REDIS_KEYS = {
    "limit": f"bot:{BOT_ID}:limit",
    "count": f"bot:{BOT_ID}:count",
    "warning": f"bot:{BOT_ID}:warning_sent"
}

async def get_redis_client():
    return aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True
    )
# --- КОНЕЦ БЛОКА Redis Configuration ---


# --- КНИГА РЕЦЕПТОВ ДЛЯ ПРОМПТОВ ---
# Базовые блоки, которые нужны при обработке любого ответа клиента
BASE_PROMPTS = [
    "#start#",
    "#style#",
    "#BASE#",
    "#BASE_KNOW#" # Главный блок с логикой мышления
]

# Карта: состояние диалога -> [список специальных блоков для этого состояния]
PROMPT_RECIPES = {
    # Сценарий 1: Приветствие
    "d1_awaiting_greeting_response": ["#Dialog1#"],
    "d1_awaiting_debtor_clarification": ["#Dialog1#"],
    "d1_awaiting_debtor_details": ["#Dialog1#"],

    # Сценарий 2: Новый лот
    "d2_awaiting_lot_response": ["#Dialog2#"],

    # Сценарий 3: Касание
    "d3_awaiting_touch_response": ["#Dialog3#"],
    "d3_awaiting_details_after_touch": ["#Dialog3#"],

    # Состояние по умолчанию (если состояние не найдено в рецептах)
    # Можно оставить пустым, чтобы использовались только базовые промпты, или добавить общий блок.
    "default": [] 
}
# --- КОНЕЦ КНИГИ РЕЦЕПТОВ ---

# --- НОВЫЙ ВОРКЕР ДЛЯ ВВОДА ИЗ КОНСОЛИ ---
async def console_input_worker():
    logger.info("👨‍💻 Воркер консольного ввода запущен. Введите 'exit' для выхода.")
    
    # Задаем номер телефона для теста один раз
    chat_id = await aioconsole.ainput("Введите номер телефона (chat_id) для этого сеанса тестирования: ")
    if not chat_id:
        chat_id = "79991234567" # Номер по умолчанию
    logger.info(f"Тестирование диалога для: {chat_id}. Теперь можете отправлять сообщения.")

    while True:
        db = None
        try:
            message_text = await aioconsole.ainput(f"[{chat_id}] > ")

            if message_text.lower() == 'exit':
                break
            if not message_text:
                continue

            db = SessionLocal()
            db_service.add_pending_message(db, chat_id, message_text)
            logger.info(f">>> Сообщение от [КОНСОЛЬ/{chat_id}] добавлено в очередь.")

        except Exception as e:
            logger.error(f"Ошибка в воркере консольного ввода: {e}")
        finally:
            if db:
                db.close()


# --- Новый воркер для ежемесячной проверки неактивных диалогов ---
async def monthly_check_worker():
    """
    Этот воркер проверяет диалоги, которые не обновлялись более месяца,
    и перемещает соответствующие сделки в Bitrix24 на стадию 'Касание сегодня'.
    """
    logger.info("🗓️ Воркер ежемесячной проверки запущен!")
    db = None
    
    # Получаем ID стадии из .env
    escalation_stage_id = os.getenv("TOUCH_TODAY_STAGE_ID")
    if not escalation_stage_id:
        logger.critical("❌ ОСТАНОВКА ВОРКЕРА ЕЖЕМЕСЯЧНОЙ ПРОВЕРКИ: Переменная TOUCH_TODAY_STAGE_ID не задана в .env.")
        return

    try:
        db = SessionLocal()
        one_month_ago = datetime.now() - timedelta(days=30) # Определяем период "месяц назад"

        # Находим диалоги, которые не обновлялись более месяца, имеют deal_id и manager_id
        inactive_dialogs = db.query(db_service.Dialog).filter(
            db_service.Dialog.updated_at < one_month_ago,
            db_service.Dialog.deal_id.isnot(None),
            db_service.Dialog.manager_id.isnot(None)
        ).all()

        if not inactive_dialogs:
            logger.info("  - Нет диалогов, неактивных более месяца.")
            return

        logger.info(f"  - Найдено {len(inactive_dialogs)} диалогов, неактивных более месяца, для обработки.")

        for dialog in inactive_dialogs:
            try:
                deal_id = dialog.deal_id
                manager_id = dialog.manager_id
                chat_id = dialog.chat_id

                logger.info(f"    - Обработка неактивного диалога {chat_id} (сделка {deal_id}).")

                # Проверяем текущее состояние сделки, чтобы не перемещать сделки, которые уже закрыты
                deal_details = bitrix_service.get_deal_details_2(deal_id)
                if not deal_details:
                    logger.warning(f"      - Пропускаю сделку {deal_id}: не удалось получить детали сделки из Bitrix24.")
                    continue

                current_stage = deal_details.get("STAGE_ID")
                # Здесь можно добавить более сложную логику, если есть стадии, на которые переводить нельзя (например, "Сделка закрыта успешно" или "Сделка провалена")
                # Для примера, просто перемещаем
                
                # 1. Перемещение сделки на стадию "Касание сегодня"
                move_success = bitrix_service.move_deal_to_stage(deal_id, escalation_stage_id)
                if move_success:
                    logger.info(f"      - ✅ Сделка {deal_id} успешно перемещена на стадию '{escalation_stage_id}'.")
                    
                    # 2. Добавление комментария в таймлайн сделки
                    comment_text = (f"[Чат-бот]: Автоматический перевод сделки на стадию "
                                    f"'Касание сегодня' (из-за отсутствия активности более месяца) "
                                    f"по диалогу {chat_id}.")
                    bitrix_service.add_comment_to_deal(deal_id, comment_text)
                    logger.info(f"      - ✅ Комментарий добавлен в сделку {deal_id}.")

                    # Опционально: можно обновить состояние диалога в БД или его updated_at,
                    # чтобы принудительно сбросить счетчик неактивности.
                    # Однако, Bitrix24 webhook на ONCRMDEALUPDATE должен сам обновить диалог.
                    # Можно просто установить needs_reminder=False и reminder_level=0.
                    dialog.needs_reminder = False
                    dialog.reminder_level = 0
                    dialog.current_state = 'awaiting_touch_today_response' # Или другое подходящее состояние
                    db.add(dialog) # Убедиться, что изменения добавлены в сессию
                    db.commit()
                    logger.info(f"      - Диалог {chat_id} обновлен в БД.")

                else:
                    logger.error(f"      - ❌ Не удалось переместить сделку {deal_id} на стадию '{escalation_stage_id}'.")
                    bitrix_service.add_comment_to_deal(
                        deal_id,
                        f"[Чат-бот] Ошибка: Не удалось автоматически перевести сделку на стадию 'Касание сегодня' "
                        f"(из-за неактивности более месяца) по диалогу {chat_id}."
                    )

            except SQLAlchemyError as e:
                logger.error(f"❌ ОШИБКА БАЗЫ ДАННЫХ при обработке диалога {dialog.chat_id} в воркере ежемесячной проверки: %s", e)
                logger.error(traceback.format_exc())
                if db:
                    db.rollback()
            except Exception as e:
                logger.error(f"❌ НЕИЗВЕСТНАЯ ОШИБКА при обработке диалога {dialog.chat_id} в воркере ежемесячной проверки: %s", e)
                logger.error(traceback.format_exc())

    except SQLAlchemyError as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА БАЗЫ ДАННЫХ В ВОРКЕРЕ ЕЖЕМЕСЯЧНОЙ ПРОВЕРКИ: %s", e)
        logger.critical(traceback.format_exc())
        if db:
            db.rollback()
    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА В ВОРКЕРЕ ЕЖЕМЕСЯЧНОЙ ПРОВЕРКИ: %s", e)
        logger.critical(traceback.format_exc())
    finally:
        if db:
            db.close()
    logger.info("✅ Воркер ежемесячной проверки завершен.")

async def process_pending_messages_worker():
    logger.info("🚀 Воркер-ДИСПЕТЧЕР запущен!")
    while True:
        db = None
        try:
            db = SessionLocal()
            dialog_batches = db_service.get_and_clear_pending_dialogs(db, delay_seconds=10)
            
            for batch in dialog_batches:
                dialog = batch['dialog']
                pending_messages = batch['pending']
                if dialog.current_state == 'escalated' or dialog.current_state == 'scenario_complete':
                    logger.warning(f"Диалог {dialog.chat_id} находится в состоянии 'escalated' или 'scenario_complete'. Обработка прекращена.")
                    continue

                logger.info(f"Обработка {len(pending_messages)} сообщений для chat_id: {dialog.chat_id} в состоянии '{dialog.current_state}'")

                # --- 1. ПОДГОТОВКА КОНТЕКСТА ---
                current_history = db_service.get_dialog_history(db, dialog.chat_id)
                for msg in pending_messages:
                    current_history.append({"role": "user", "content": msg['content']})

                # --- 2. ПОЛУЧЕНИЕ РЕШЕНИЯ ОТ LLM ---
                prompt_library = prompt_service.get_prompt_library()
                current_dialog_state = db_service.get_dialog_state(db, dialog.chat_id)

                # --- НАЧАЛО ИЗМЕНЕНИЙ: Динамическая сборка промпта ---

                # 1. Получаем специальные блоки для текущего состояния из "книги рецептов"
                # Используем .get(), чтобы избежать ошибки, если состояние не найдено. 
                # Если состояние не найдено, берем рецепт для "default".
                specific_keys = PROMPT_RECIPES.get(current_dialog_state, PROMPT_RECIPES.get("default", []))
                
                # 2. Объединяем базовые и специальные блоки
                required_keys = BASE_PROMPTS + specific_keys
                logger.info(f"  - Для состояния '{current_dialog_state}' собираем промпт из блоков: {required_keys}")
                
                # 3. Собираем текст промпта только из нужных блоков
                prompt_parts = [prompt_library.get(key, "") for key in required_keys]
                all_prompts_text = "\n\n".join(filter(None, prompt_parts))

                # 4. Добавляем в конец системного промпта явное указание на текущее состояние
                system_prompt_addition = ""
                if current_dialog_state:
                    system_prompt_addition = f"\n\nТекущее состояние диалога: {current_dialog_state}. Действуй строго в соответствии с этим состоянием."
                
                final_system_prompt = all_prompts_text + system_prompt_addition
                
                # --- КОНЕЦ ИЗМЕНЕНИЙ ---

                llm_decision = await llm_service.get_bot_decision(current_history, final_system_prompt)
                if llm_decision:
                    db_service.log_llm_response(db, dialog.chat_id, llm_decision)

                if not llm_decision:
                    logger.error(f"❌ LLM не вернул решение для диалога {dialog.chat_id} после всех попыток. Пропускаем.")
                    if dialog.deal_id and dialog.manager_id:
                        bitrix_service.escalate_deal_to_manager(
                            dialog.deal_id,
                            dialog.manager_id,
                            f"Критическая ошибка: LLM не смог сгенерировать решение для диалога {dialog.chat_id} после всех попыток."
                        )
                    else:
                        logger.critical(f"Не удалось эскалировать LLM ошибку для {dialog.chat_id}: нет deal_id или manager_id.")
                    continue

                # --- 3. РАЗБОР И ИСПОЛНЕНИЕ КОМАНД ---
                response_text = llm_decision.get("response_text")
                action = llm_decision.get("action")
                action_params = llm_decision.get("action_params", {})
                new_state = llm_decision.get("new_state", dialog.current_state)

                deal_id = dialog.deal_id
                manager_id = dialog.manager_id

                if not deal_id or not manager_id:
                    logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА: В диалоге {dialog.chat_id} отсутствуют deal_id ({deal_id}) или manager_id ({manager_id}). Невозможно выполнить CRM-действие. Пропускаем.")
                    continue

                # --- ШАГ 3.1: ОТПРАВКА СООБЩЕНИЯ КЛИЕНТУ ---
                if response_text:
                    success = wazzup_service.send_message(dialog.chat_id, response_text)
                    if success:
                        current_history.append({"role": "assistant", "content": response_text})
                    else:
                        logger.error(f"❌ Не удалось отправить сообщение клиенту {dialog.chat_id} через Wazzup после всех ретраев.")
                        if dialog.deal_id and dialog.manager_id:
                            bitrix_service.escalate_deal_to_manager(
                                dialog.deal_id,
                                dialog.manager_id,
                                f"Критическая ошибка: Не удалось отправить сообщение клиенту {dialog.chat_id} через Wazzup после всех попыток."
                            )       
                        else:
                            logger.critical(f"Не удалось эскалировать Wazzup ошибку для {dialog.chat_id}: нет deal_id или manager_id.")
                
                # --- ШАГ 3.2: ВЫПОЛНЕНИЕ ДЕЙСТВИЙ В CRM ---
                comment = action_params.get("comment_text")
                task_subject = action_params.get("task_subject")
                task_desc = action_params.get("task_description")

                logger.info(f"  - Действие для CRM: {action} для сделки {deal_id}")

                if action == "LOG_COMMENT" and comment:
                    bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {comment}")
                
                elif action == "CREATE_TASK_AND_LOG":
                    if comment: bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {comment}")
                    if task_desc and task_subject: 
                        bitrix_service.create_activity_for_deal(deal_id, manager_id, task_subject, task_desc)

                elif action == "ESCALATE_TO_MANAGER":
                    reason = comment or "Причина эскалации не указана."
                    bitrix_service.escalate_deal_to_manager(deal_id, manager_id, reason)

                # --- 4. ОБНОВЛЕНИЕ ДИАЛОГА В БД ---
                states_that_need_reminders = [
                    "d1_awaiting_greeting_response",
                    "d1_awaiting_debtor_clarification",
                    "d1_awaiting_debtor_details",
                    "d2_awaiting_lot_response",
                    "d3_awaiting_touch_response",
                    "d3_awaiting_details_after_touch"
                ]
                should_remind = new_state in states_that_need_reminders

                db_service.update_dialog(db, dialog.chat_id, new_state, current_history, needs_reminder=should_remind)
                logger.info(f"  - Диалог {dialog.chat_id} переведен в состояние '{new_state}'.")
            
        except SQLAlchemyError as e:
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА БАЗЫ ДАННЫХ В ВОРКЕРЕ: %s", e)
            logger.critical(traceback.format_exc())
            if db:
                db.rollback()
        except Exception as e:
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА В ВОРКЕРЕ: %s", e)
            logger.critical(traceback.format_exc())
        finally:
            if db:
                db.close()
        
        await asyncio.sleep(5)

async def reminder_worker():
    """Этот воркер запускается по расписанию и проверяет, кому пора напомнить."""
    logger.info("⏰ Воркер-НАПОМИНАТОР запущен!")
    db = None
    try:
        db = SessionLocal()
        dialogs_to_remind = db_service.get_due_reminders(db)
        if not dialogs_to_remind:
            logger.info("  - Нет диалогов для напоминания.")
            return

        logger.info(f"  - Найдено {len(dialogs_to_remind)} диалогов для напоминания.")
        
        for dialog in dialogs_to_remind:
            try:
                
                # !!! ОБНОВЛЕНИЕ: Воркер напоминаний
                # Сначала получаем deal_details_2 для доступа к COMPANY_ID
                deal_details = bitrix_service.get_deal_details_2(dialog.deal_id) # Используем get_deal_details_2
                if not deal_details:
                    logger.warning(f"  - Пропускаю напоминание для {dialog.chat_id}: не удалось получить детали сделки {dialog.deal_id}.")
                    continue
                
                contact_id = int(deal_details.get("CONTACT_ID")) if deal_details.get("CONTACT_ID") else None
                company_id = int(deal_details.get("COMPANY_ID")) if deal_details.get("COMPANY_ID") else None # !!! ДОБАВЛЕНО
                client_name = "Уважаемый клиент"

                # 1. Попытка получить имя клиента из связанной компании (приоритет)
                if company_id:
                    company_details = bitrix_service.get_company_details(company_id)
                    if company_details:
                        client_name = company_details.get("TITLE", client_name)
                        logger.debug(f"  - Имя клиента для напоминания взято из компании {company_id}.")
                
                # 2. Если имя все еще дефолтное, пытаемся получить из связанного контакта
                if client_name == "Уважаемый клиент" and contact_id:
                    contact_details = bitrix_service.get_contact_details_2(contact_id) # Используем get_contact_details_2
                    if contact_details:
                        client_name = contact_details.get("NAME", client_name)
                        logger.debug(f"  - Имя клиента для напоминания взято из контакта {contact_id}.")
                
                if client_name == "Уважаемый клиент":
                    logger.warning(f"  - Не удалось получить имя клиента для диалога {dialog.chat_id}: нет имени у компании ({company_id}) или контакта ({contact_id}).")


                reminder_level = dialog.reminder_level
                instruction = None
                if reminder_level == 0:
                    instruction = "initiate_reminder_1h"
                elif reminder_level == 1:
                    instruction = f"initiate_reminder_3h. ИМЯ_КЛИЕНТА: {client_name}."
                elif reminder_level == 2:
                    instruction = f"initiate_reminder_24h. ИМЯ_КЛИЕНТА: {client_name}."
                else:
                    logger.warning(f"  - Диалог {dialog.chat_id} имеет неожиданный уровень напоминания {reminder_level}. Пропускаю.")
                    continue

                logger.info(f"    - Отправка напоминания уровня {reminder_level + 1} для {dialog.chat_id}")

                if db_service.has_pending_messages(db, dialog.chat_id):
                    logger.info(f"    - ОТМЕНА: Клиент ответил. Сбрасываем напоминания для {dialog.chat_id}")
                    dialog.needs_reminder = False
                    dialog.reminder_level = 0
                    db.commit()
                    continue
                
                if instruction:
                    prompt_library = prompt_service.get_prompt_library()

                    # --- НАЧАЛО ИЗМЕНЕНИЙ ---
                    # Укажите ключи, нужные для сценария "Новый лот"
                    required_keys = [
                        "#start#", 
                        "#style#",
                        "#Reminder#" # Специальный блок для этого сценария
                    ]
                    
                    prompt_parts = [prompt_library.get(key, "") for key in required_keys]
                    all_prompts_text = "\n\n".join(filter(None, prompt_parts))
                    # --- КОНЕЦ ИЗМЕНЕНИЙ ---
                    
                    llm_decision = await llm_service.get_bot_decision(
                        [{"role": "system", "content": instruction}], 
                        all_prompts_text
                    )
                    
                    reminder_text = llm_decision.get("response_text") if llm_decision else None
                    
                    llm_failed_to_remind = False
                    if reminder_text:
                        success = wazzup_service.send_message(dialog.chat_id, reminder_text)
                        if success:
                            dialog.reminder_level += 1
                            db_service.add_message_to_history(db, dialog.chat_id, "assistant", reminder_text)
                            logger.info(f"      - Успешно отправлено напоминание клиенту {dialog.chat_id}. Уровень: {dialog.reminder_level}.")
                        else:
                            logger.error(f"      - НЕ УДАЛОСЬ отправить напоминание клиенту {dialog.chat_id} через Wazzup.")
                            if dialog.deal_id and dialog.manager_id:
                                bitrix_service.escalate_deal_to_manager(
                                    dialog.deal_id,
                                    dialog.manager_id,
                                    f"Критическая ошибка: Не удалось отправить напоминание клиенту {dialog.chat_id} через Wazzup после всех попыток (уровень {dialog.reminder_level + 1})."
                                )
                            else:
                                logger.critical(f"Не удалось эскалировать Wazzup ошибку (напоминание) для {dialog.chat_id}: нет deal_id или manager_id.")
                    else:
                        logger.error(f"      - LLM не сгенерировал текст напоминания для {dialog.chat_id} на уровне {reminder_level + 1}. LLM Decision: {llm_decision}")
                        llm_failed_to_remind = True
                    
                    # Эскалация, если LLM не сгенерировал текст напоминания
                    if llm_failed_to_remind: # Этот if был неверно расположен ранее
                        if dialog.deal_id and dialog.manager_id:
                            bitrix_service.escalate_deal_to_manager(
                                dialog.deal_id,
                                dialog.manager_id,
                                f"Критическая ошибка: LLM не смог сгенерировать текст напоминания для диалога {dialog.chat_id} (уровень {reminder_level + 1})."
                            )
                        else:
                            logger.critical(f"Не удалось эскалировать LLM ошибку (напоминание) для {dialog.chat_id}: нет deal_id или manager_id.")
                
                if dialog.reminder_level >= 3:
                    logger.warning(f"    - ПОСЛЕДНЕЕ НАПОМИНАНИЕ. Эскалация на менеджера для {dialog.chat_id}.")
                    if dialog.manager_id:
                        bitrix_service.escalate_deal_to_manager(
                            dialog.deal_id, 
                            dialog.manager_id, 
                            "Клиент не отвечает на автоматические напоминания в чате."
                        )
                        logger.info(f"      - Запущена эскалация для сделки {dialog.deal_id} на менеджера {dialog.manager_id}.")
                    else:
                        logger.critical(f"      - НЕ УДАЛОСЬ эскалировать сделку {dialog.deal_id}: отсутствует manager_id.")
                    dialog.needs_reminder = False
                
                db.commit()
            except SQLAlchemyError as e:
                logger.error(f"❌ ОШИБКА БАЗЫ ДАННЫХ при обработке диалога {dialog.chat_id} в воркере напоминаний: %s", e)
                logger.error(traceback.format_exc())
                if db:
                    db.rollback()
            except Exception as e:
                logger.error(f"❌ НЕИЗВЕСТНАЯ ОШИБКА при обработке диалога {dialog.chat_id} в воркере напоминаний: %s", e)
                logger.error(traceback.format_exc())

    except SQLAlchemyError as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА БАЗЫ ДАННЫХ В ВОРКЕРЕ НАПОМИНАНИЙ: %s", e)
        logger.critical(traceback.format_exc())
        if db:
            db.rollback()
    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА В ВОРКЕРЕ НАПОМИНАНИЙ: %s", e)
        logger.critical(traceback.format_exc())
    finally:
        if db:
            db.close()

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Приложение запускается...")
    
    # --- Планировщик APScheduler (без изменений) ---
    jobstores = {'default': SQLAlchemyJobStore(url=os.getenv("DATABASE_URL"))}
    scheduler = AsyncIOScheduler(jobstores=jobstores)
    scheduler.add_job(reminder_worker, 'interval', minutes=1, id='reminder_job', replace_existing=True)
    scheduler.add_job(monthly_check_worker, 'cron', hour=12, minute=0, id='monthly_check_job', replace_existing=True)
    scheduler.start()
    logger.info("✅ Планировщик APScheduler запущен.")
    
    # --- НАЧАЛО ИЗМЕНЕНИЙ: Запуск воркеров ---
    worker_tasks = []
    
    # 1. Запускаем основной воркер сообщений всегда
    task = asyncio.create_task(process_pending_messages_worker())
    worker_tasks.append(task)
    logger.info("✅ Воркер process_pending_messages_worker запущен.")

    # 2. Запускаем консольный воркер ТОЛЬКО в режиме отладки
    if os.getenv("DEBUG_CONSOLE_MODE") == "true":
        task = asyncio.create_task(console_input_worker())
        worker_tasks.append(task)
        logger.warning("✅ ВНИМАНИЕ: Запущен режим отладки в консоли!")

    yield
    
    logger.info("Приложение останавливается...")
    scheduler.shutdown()
    logger.info("Планировщик APScheduler остановлен.")

    # Останавливаем все запущенные воркеры
    for task in worker_tasks:
        task.cancel()
    
    await asyncio.gather(*worker_tasks, return_exceptions=True)
    logger.info("Все воркеры успешно остановлены.")
    # --- КОНЕЦ ИЗМЕНЕНИЙ ---
    
    logger.info("Приложение остановлено.")

app = FastAPI(title="Bitrix Wazzup Bot", lifespan=lifespan)

# --- ЗАВИСИМОСТЬ ДЛЯ ПОЛУЧЕНИЯ СЕССИИ БД ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as e:
        logger.error(f"Ошибка БД в зависимости get_db: %s", e)
        raise
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Bot is running"}

# --- ОБНОВЛЕННЫЙ ОБРАБОТЧИК ВЕБХУКОВ BITRIX24 ---
@app.post("/webhook/bitrix")
async def handle_bitrix_webhook(request: Request, db: Session = Depends(get_db)):
    deal_id_str = "Unknown" # Для логирования в случае ошибки до извлечения deal_id
    try:
        form_data = await request.form()
        data = parse_form_data(form_data)
        
        event = data.get("event")
        if event != "ONCRMDEALUPDATE":
            logger.info(f"Получено событие Bitrix24 '{event}', но оно будет проигнорировано (ожидается ONCRMDEALUPDATE).")
            return {"status": "ok", "message": "Event ignored"}

        deal_id_str = data.get("data", {}).get("FIELDS", {}).get("ID")
        if not deal_id_str:
            logger.error("❌ Ошибка: В вебхуке Bitrix24 не найден ID сделки. Пропускаю обработку.")
            return {"status": "error", "message": "No deal ID"}
        
        deal_id = int(deal_id_str)
        logger.info(f"Получен вебхук ONCRMDEALUPDATE для сделки ID: {deal_id}")

        # Получаем клиент Redis один раз на вызов вебхука # ДОБАВИТЬ ЭТУ СТРОКУ
        r = await get_redis_client()
        #________________________________________________________________________-
        deal_details = bitrix_service.get_deal_details_2(deal_id)
        if not deal_details:
            logger.error(f"❌ Ошибка: Не удалось получить детали сделки {deal_id} из Bitrix24. Пропускаю обработку.")
            return {"status": "error", "message": "Failed to get deal details"}

        current_funnel_id = str(deal_details.get("CATEGORY_ID"))
        current_stage = deal_details.get("STAGE_ID")
        
        # --- Сценарий №1: Приветствие на стадии NEW ---
        if current_funnel_id == TARGET_FUNNEL_ID and current_stage == WELCOME_STAGE_ID:
            logger.info(f"✅✅✅ ТРИГГЕР СЦЕНАРИЯ №1 СРАБОТАЛ: Сделка {deal_id} перешла на стадию '{WELCOME_STAGE_ID}'.")

            # # --- ПРОВЕРКА И УВЕЛИЧЕНИЕ ЛИМИТА ДЛЯ СЦЕНАРИЯ 1 (ПРИВЕТСТВИЕ) --- # ДОБАВИТЬ ЭТОТ БЛОК
            # try:
            #     current_limit = int(await r.get(REDIS_KEYS["limit"]) or 0)
            #     current_count = int(await r.get(REDIS_KEYS["count"]) or 0)

            #     if current_limit <= 0 or current_count >= current_limit:
            #         logger.warning(f"❌ СЦЕНАРИЙ 'Приветствие' для сделки {deal_id} ПРОПУЩЕН: Лимит исчерпан или не установлен ({current_count}/{current_limit}).")
            #         bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Сценарий 'Приветствие' пропущен: лимит исчерпан или не установлен ({current_count}/{current_limit}).")
            #         return {"status": "ok", "message": "Limit exceeded or not set, scenario skipped."}
                
            #     await r.incr(REDIS_KEYS["count"])
            #     logger.info(f"  - Лимит использования для '{BOT_ID}' увеличен: {current_count + 1}/{current_limit}.")
            # except Exception as redis_e:
            #     logger.error(f"❌ Ошибка при работе с Redis для сценария 'Приветствие' сделки {deal_id}: {redis_e}")
            #     bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка при проверке/увеличении лимита (Redis) для сценария 'Приветствие': {redis_e}")
            #     return {"status": "error", "message": "Redis error, scenario skipped."}
            # # --- КОНЕЦ БЛОКА ПРОВЕРКИ ЛИМИТА ---






            contact_id = int(deal_details.get("CONTACT_ID")) if deal_details.get("CONTACT_ID") else None
            company_id = int(deal_details.get("COMPANY_ID")) if deal_details.get("COMPANY_ID") else None # !!! ДОБАВЛЕНО
            manager_id = int(deal_details.get("ASSIGNED_BY_ID")) if deal_details.get("ASSIGNED_BY_ID") else None
            
            if not manager_id:
                logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 1 для сделки {deal_id}: Отсутствует ASSIGNED_BY_ID (менеджер).")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: Не удалось запустить сценарий 'Приветствие', отсутствует ID менеджера.")
                return {"status": "error", "message": "No manager ID"}

            client_phone = None
            client_name = "Уважаемый клиент"
            phone_source = "Не найден"

            # 1. Попытка получить телефон из связанной компании (приоритет)
            if company_id:
                company_details = bitrix_service.get_company_details(company_id)
                if company_details and company_details.get("PHONE"):
                    phone_data = company_details["PHONE"][0]
                    if phone_data and phone_data.get("VALUE"):
                        client_phone = normalize_phone(phone_data["VALUE"])
                        client_name = company_details.get("TITLE", client_name) 
                        phone_source = f"Компания {company_id}"
                        logger.debug(f"  - Телефон найден у компании {company_id} для сделки {deal_id} (Сценарий 1).")

            # 2. Если у компании нет телефона, пытаемся получить из связанного контакта
            if not client_phone and contact_id:
                contact_details = bitrix_service.get_contact_details_2(contact_id) # Используем get_contact_details_2
                if contact_details and contact_details.get("PHONE"):
                    phone_data = contact_details["PHONE"][0]
                    if phone_data and phone_data.get("VALUE"):
                        client_phone = normalize_phone(phone_data["VALUE"])
                        client_name = contact_details.get("NAME", client_name)
                        phone_source = f"Контакт {contact_id}"
                        logger.debug(f"  - Телефон найден у контакта {contact_id} для сделки {deal_id} (Сценарий 1).")
            
            if not client_phone:
                logger.warning(f"⚠️ ОСТАНОВКА СЦЕНАРИЯ 1 для сделки {deal_id}: Не удалось найти номер телефона ни у компании ({company_id}), ни у контакта ({contact_id}).")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: Не удалось запустить сценарий 'Приветствие', не найден номер телефона клиента.")
                return {"status": "ok", "message": "No client phone number found for scenario 1"}
            
            
            manager = bitrix_service.get_user_details(manager_id)
            manager_name = f"{manager.get('NAME', '')} {manager.get('LAST_NAME', '')}".strip() if manager else "Ваш менеджер"
            
            logger.info(f"  - Клиент: {client_name} ({client_phone})")
            logger.info(f"  - Менеджер: {manager_name} ({manager_id})")

            
            # 1. Получаем историю
            current_history = db_service.get_dialog_history(db, client_phone)
            logger.info(f"  - Найдено {len(current_history)} сообщений в истории для {client_phone}.")

            # 2. Создаем команду для сценария "Приветствие"
            initial_instruction = {
                "role": "system",
                "content": f"initiate_dialog. ИМЯ_КЛИЕНТА: {client_name}. ИМЯ_МЕНЕДЖЕРА: {manager_name}."
            }
            current_history.append(initial_instruction)

            # 3. Собираем промпт для этого сценария
            prompt_library = prompt_service.get_prompt_library()
            required_keys = [
                "#start#", 
                "#style#",
                "#Dialog1#" # Специальный блок для приветствия
            ]
            prompt_parts = [prompt_library.get(key, "") for key in required_keys]
            all_prompts_text = "\n\n".join(filter(None, prompt_parts))

            # --- КОНЕЦ ИЗМЕНЕНИЙ ---
            llm_decision = await llm_service.get_bot_decision(current_history, all_prompts_text)
            if llm_decision:
                db_service.log_llm_response(db, client_phone, llm_decision)
            if not llm_decision:
                logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 1 для сделки {deal_id}: LLM не вернул решение для сценария 'Приветствие'.")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: LLM не сгенерировал ответ для сценария 'Приветствие'.")
                if manager_id:
                    bitrix_service.escalate_deal_to_manager(
                        deal_id,
                        manager_id,
                        f"Критическая ошибка: LLM не смог сгенерировать сообщение для сделки {deal_id} в сценарии 'Приветствие'."
                    )
                else:
                    logger.critical(f"Не удалось эскалировать LLM ошибку (сценарий Приветствие) для сделки {deal_id}: нет manager_id.")
                return {"status": "error", "message": "LLM failed to provide initial decision"}
            
            response_text = llm_decision.get("response_text")
            action = llm_decision.get("action")
            action_params = llm_decision.get("action_params", {})
            new_state = llm_decision.get("new_state")
            
            if response_text:
                success = wazzup_service.send_message(client_phone, response_text)
                if not success:
                    logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 1 для сделки {deal_id}: Не удалось отправить сообщение на номер {client_phone} через Wazzup.")
                    bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка! Не удалось отправить приветственное сообщение на номер {client_phone}.")
                    if manager_id:
                        bitrix_service.escalate_deal_to_manager(
                            deal_id,
                            manager_id,
                            f"Критическая ошибка: Не удалось отправить приветственное сообщение на номер {client_phone} через Wazzup для сделки {deal_id}."
                        )
                    else:
                        logger.critical(f"Не удалось эскалировать Wazzup ошибку (сценарий Приветствие) для сделки {deal_id}: нет manager_id.")
                    return {"status": "ok", "message": "Wazzup send failed"}
                else:
                    logger.info(f"  - Приветственное сообщение успешно отправлено клиенту {client_phone}.")
            
            if action == "LOG_COMMENT" and action_params.get("comment_text"):
                bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {action_params['comment_text']}")
                logger.info(f"  - Комментарий добавлен в сделку {deal_id}: {action_params['comment_text']}")

            dialog = db_service.get_or_create_dialog(db, client_phone, deal_id, manager_id, current_funnel_id)
            current_history = db_service.get_dialog_history(db, client_phone)
            if response_text:
                current_history.append({"role": "assistant", "content": response_text})
            
            states_that_need_reminders = [
                    "d1_awaiting_greeting_response",
                    "d1_awaiting_debtor_clarification",
                    "d1_awaiting_debtor_details",
                    "d2_awaiting_lot_response",
                    "d3_awaiting_touch_response",
                    "d3_awaiting_details_after_touch"
                ]
            should_remind = new_state in states_that_need_reminders

            db_service.update_dialog(db, client_phone, new_state, current_history, needs_reminder=should_remind)
            logger.info(f"✅ Сценарий №1 для сделки {deal_id} успешно запущен. Диалог {client_phone} переведен в состояние '{new_state}'.")

        # --- Сценарий №2: Уведомление о новом лоте ---
        elif current_funnel_id == TARGET_FUNNEL_ID and current_stage == NEW_LOT_STAGE_ID:
            logger.info(f"✅✅✅ ТРИГГЕР СЦЕНАРИЯ №2 СРАБОТАЛ: Сделка {deal_id} перешла на стадию 'Новый лот' ({NEW_LOT_STAGE_ID}).")
            
            #     # --- ПРОВЕРКА И УВЕЛИЧЕНИЕ ЛИМИТА ДЛЯ СЦЕНАРИЯ 2 (НОВЫЙ ЛОТ) --- # ДОБАВИТЬ ЭТОТ БЛОК
            # try:
            #     current_limit = int(await r.get(REDIS_KEYS["limit"]) or 0)
            #     current_count = int(await r.get(REDIS_KEYS["count"]) or 0)

            #     if current_limit <= 0 or current_count >= current_limit:
            #         logger.warning(f"❌ СЦЕНАРИЙ 'Новый лот' для сделки {deal_id} ПРОПУЩЕН: Лимит исчерпан или не установлен ({current_count}/{current_limit}).")
            #         bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Сценарий 'Новый лот' пропущен: лимит исчерпан или не установлен ({current_count}/{current_limit}).")
            #         return {"status": "ok", "message": "Limit exceeded or not set, scenario skipped."}
                
            #     await r.incr(REDIS_KEYS["count"])
            #     logger.info(f"  - Лимит использования для '{BOT_ID}' увеличен: {current_count + 1}/{current_limit}.")
            # except Exception as redis_e:
            #     logger.error(f"❌ Ошибка при работе с Redis для сценария 'Новый лот' сделки {deal_id}: {redis_e}")
            #     bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка при проверке/увеличении лимита (Redis) для сценария 'Новый лот': {redis_e}")
            #     return {"status": "error", "message": "Redis error, scenario skipped."}
            # # --- КОНЕЦ БЛОКА ПРОВЕРКИ ЛИМИТА ---




            contact_id = int(deal_details.get("CONTACT_ID")) if deal_details.get("CONTACT_ID") else None
            company_id = int(deal_details.get("COMPANY_ID")) if deal_details.get("COMPANY_ID") else None # !!! ДОБАВЛЕНО
            manager_id = int(deal_details.get("ASSIGNED_BY_ID")) if deal_details.get("ASSIGNED_BY_ID") else None

            if not manager_id:
                logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 2 для сделки {deal_id}: Отсутствует ASSIGNED_BY_ID (менеджер).")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: Не удалось запустить сценарий 'Новый лот', отсутствует ID менеджера.")
                return {"status": "error", "message": "No manager ID"}

            client_phone = None
            client_name = "Уважаемый клиент"
            phone_source = "Не найден"

            # 1. Попытка получить телефон из связанной компании (приоритет)
            if company_id:
                company_details = bitrix_service.get_company_details(company_id)
                if company_details and company_details.get("PHONE"):
                    phone_data = company_details["PHONE"][0]
                    if phone_data and phone_data.get("VALUE"):
                        client_phone = normalize_phone(phone_data["VALUE"])
                        client_name = company_details.get("TITLE", client_name) 
                        phone_source = f"Компания {company_id}"
                        logger.debug(f"  - Телефон найден у компании {company_id} для сделки {deal_id} (Сценарий 2).")

            # 2. Если у компании нет телефона, пытаемся получить из связанного контакта
            if not client_phone and contact_id:
                contact_details = bitrix_service.get_contact_details_2(contact_id) # Используем get_contact_details_2
                if contact_details and contact_details.get("PHONE"):
                    phone_data = contact_details["PHONE"][0]
                    if phone_data and phone_data.get("VALUE"):
                        client_phone = normalize_phone(phone_data["VALUE"])
                        client_name = contact_details.get("NAME", client_name)
                        phone_source = f"Контакт {contact_id}"
                        logger.debug(f"  - Телефон найден у контакта {contact_id} для сделки {deal_id} (Сценарий 2).")
            
            if not client_phone:
                logger.warning(f"⚠️ ОСТАНОВКА СЦЕНАРИЯ 2 для сделки {deal_id}: Не удалось найти номер телефона ни у компании ({company_id}), ни у контакта ({contact_id}).")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: Не удалось запустить сценарий 'Новый лот', не найден номер телефона клиента.")
                return {"status": "ok", "message": "No client phone number found for scenario 2"}



            manager = bitrix_service.get_user_details(manager_id)
            manager_name = f"{manager.get('NAME', '')} {manager.get('LAST_NAME', '')}".strip() if manager else "Ваш менеджер"
            
            latest_activity = bitrix_service.get_latest_activity_for_deal(deal_id)
            if not latest_activity or not latest_activity.get("DESCRIPTION"):
                logger.warning(f"⚠️ ОСТАНОВКА СЦЕНАРИЯ 2 для сделки {deal_id}: Не найдено дело с описанием.")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: не удалось запустить сценарий 'Новый лот', т.к. к сделке не привязано дело с описанием.")
                return {"status": "ok", "message": "No activity with description"}
            
            debtor_name_info = latest_activity["DESCRIPTION"]
            
            logger.info(f"  - Клиент: {client_name} ({client_phone})")
            logger.info(f"  - Менеджер: {manager_name} ({manager_id})")
            logger.info(f"  - Инфо о лоте: {debtor_name_info}")

            # 1. Получаем существующую историю диалога
            current_history = db_service.get_dialog_history(db, client_phone)
            logger.info(f"  - Найдено {len(current_history)} сообщений в истории для {client_phone}.")

            # 2. Создаем системную команду для нового сценария
            initial_instruction = {
                "role": "system",
                "content": f"initiate_new_lot_dialog. ИМЯ_КЛИЕНТА: {client_name}. ИМЯ_МЕНЕДЖЕРА: {manager_name}. Для клиента {client_name} появился новый лот/должник ИНФОРМАЦИЯ О ДОЛЖНИКЕ: {debtor_name_info}."
            }
            
            # 3. Добавляем команду в КОНЕЦ существующей истории
            current_history.append(initial_instruction)
            
            # --- КОНЕЦ ИЗМЕНЕНИЙ ---


            prompt_library = prompt_service.get_prompt_library()

            # --- НАЧАЛО ИЗМЕНЕНИЙ ---
            # Укажите ключи, нужные для сценария "Новый лот"
            required_keys = [
                "#start#", 
                "#style#",
                "#Dialog2#" # Специальный блок для этого сценария
            ]
            
            prompt_parts = [prompt_library.get(key, "") for key in required_keys]
            all_prompts_text = "\n\n".join(filter(None, prompt_parts))
            # --- КОНЕЦ ИЗМЕНЕНИЙ ---

            # 4. Отправляем в LLM ПОЛНУЮ историю с новой командой
            llm_decision = await llm_service.get_bot_decision(current_history, all_prompts_text)
            if llm_decision:
                db_service.log_llm_response(db, client_phone, llm_decision)
            if not llm_decision:
                logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 2 для сделки {deal_id}: LLM не вернул решение для сценария 'Новый лот'.")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: LLM не сгенерировал ответ для сценария 'Новый лот'.")
                if manager_id:
                    bitrix_service.escalate_deal_to_manager(
                        deal_id,
                        manager_id,
                        f"Критическая ошибка: LLM не смог сгенерировать сообщение для сделки {deal_id} в сценарии 'Новый лот'."
                    )
                else:
                    logger.critical(f"Не удалось эскалировать LLM ошибку (сценарий Новый лот) для сделки {deal_id}: нет manager_id.")
                return {"status": "error", "message": "LLM failed to provide initial decision"}

            response_text = llm_decision.get("response_text")
            action = llm_decision.get("action")
            action_params = llm_decision.get("action_params", {})
            new_state = llm_decision.get("new_state")
            
            if response_text:
                success = wazzup_service.send_message(client_phone, response_text)
                if not success:
                    logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 2 для сделки {deal_id}: Не удалось отправить сообщение на номер {client_phone} через Wazzup.")
                    bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка! Не удалось отправить сообщение о новом лоте на номер {client_phone}.")
                    if manager_id:
                        bitrix_service.escalate_deal_to_manager(
                            deal_id,
                            manager_id,
                            f"Критическая ошибка: Не удалось отправить сообщение о новом лоте на номер {client_phone} через Wazzup для сделки {deal_id}."
                        )
                    else:
                        logger.critical(f"Не удалось эскалировать Wazzup ошибку (сценарий Новый лот) для сделки {deal_id}: нет manager_id.")
                    return {"status": "ok", "message": "Wazzup send failed"}
                else:
                    logger.info(f"  - Сообщение о новом лоте успешно отправлено клиенту {client_phone}.")
            
            if action == "LOG_COMMENT" and action_params.get("comment_text"):
                bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {action_params['comment_text']}")
                logger.info(f"  - Комментарий добавлен в сделку {deal_id}: {action_params['comment_text']}")

            dialog = db_service.get_or_create_dialog(db, client_phone, deal_id, manager_id, current_funnel_id)
            current_history = db_service.get_dialog_history(db, client_phone)
            if response_text:
                current_history.append({"role": "assistant", "content": response_text})
            
            states_that_need_reminders = [
                    "d1_awaiting_greeting_response",
                    "d1_awaiting_debtor_clarification",
                    "d1_awaiting_debtor_details",
                    "d2_awaiting_lot_response",
                    "d3_awaiting_touch_response",
                    "d3_awaiting_details_after_touch"
                ]
            should_remind = new_state in states_that_need_reminders

            db_service.update_dialog(db, client_phone, new_state, current_history, needs_reminder=should_remind)
            logger.info(f"✅ Сценарий 'Новый лот' для сделки {deal_id} успешно запущен. Диалог {client_phone} переведен в состояние '{new_state}'.")

        # --- Сценарий №3: Касание сегодня ---
        elif current_funnel_id == TARGET_FUNNEL_ID and current_stage == TOUCH_TODAY_STAGE_ID:
            logger.info(f"✅✅✅ ТРИГГЕР СЦЕНАРИЯ №3 СРАБОТАЛ: Сделка {deal_id} перешла на стадию 'Касание сегодня' ({TOUCH_TODAY_STAGE_ID}).")
            
            #     # --- ПРОВЕРКА И УВЕЛИЧЕНИЕ ЛИМИТА ДЛЯ СЦЕНАРИЯ 3 (КАСАНИЕ СЕГОДНЯ) --- # ДОБАВИТЬ ЭТОТ БЛОК
            # try:
            #     current_limit = int(await r.get(REDIS_KEYS["limit"]) or 0)
            #     current_count = int(await r.get(REDIS_KEYS["count"]) or 0)

            #     if current_limit <= 0 or current_count >= current_limit:
            #         logger.warning(f"❌ СЦЕНАРИЙ 'Касание сегодня' для сделки {deal_id} ПРОПУЩЕН: Лимит исчерпан или не установлен ({current_count}/{current_limit}).")
            #         bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Сценарий 'Касание сегодня' пропущен: лимит исчерпан или не установлен ({current_count}/{current_limit}).")
            #         return {"status": "ok", "message": "Limit exceeded or not set, scenario skipped."}
                
            #     await r.incr(REDIS_KEYS["count"])
            #     logger.info(f"  - Лимит использования для '{BOT_ID}' увеличен: {current_count + 1}/{current_limit}.")
            # except Exception as redis_e:
            #     logger.error(f"❌ Ошибка при работе с Redis для сценария 'Касание сегодня' сделки {deal_id}: {redis_e}")
            #     bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка при проверке/увеличении лимита (Redis) для сценария 'Касание сегодня': {redis_e}")
            #     return {"status": "error", "message": "Redis error, scenario skipped."}
            # # --- КОНЕЦ БЛОКА ПРОВЕРКИ ЛИМИТА ---
#___________________________________________________________________________________________________________________________________________________________________
            contact_id = int(deal_details.get("CONTACT_ID")) if deal_details.get("CONTACT_ID") else None
            company_id = int(deal_details.get("COMPANY_ID")) if deal_details.get("COMPANY_ID") else None # !!! ДОБАВЛЕНО
            manager_id = int(deal_details.get("ASSIGNED_BY_ID")) if deal_details.get("ASSIGNED_BY_ID") else None

            if not manager_id:
                logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 3 для сделки {deal_id}: Отсутствует ASSIGNED_BY_ID (менеджер).")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: Не удалось запустить сценарий 'Касание сегодня', отсутствует ID менеджера.")
                return {"status": "error", "message": "No manager ID"}

            client_phone = None
            client_name = "Уважаемый клиент"
            phone_source = "Не найден"

            # 1. Попытка получить телефон из связанной компании (приоритет)
            if company_id:
                company_details = bitrix_service.get_company_details(company_id)
                if company_details and company_details.get("PHONE"):
                    phone_data = company_details["PHONE"][0]
                    if phone_data and phone_data.get("VALUE"):
                        client_phone = normalize_phone(phone_data["VALUE"])
                        client_name = company_details.get("TITLE", client_name) 
                        phone_source = f"Компания {company_id}"
                        logger.debug(f"  - Телефон найден у компании {company_id} для сделки {deal_id} (Сценарий 3).")

            # 2. Если у компании нет телефона, пытаемся получить из связанного контакта
            if not client_phone and contact_id:
                contact_details = bitrix_service.get_contact_details_2(contact_id) # Используем get_contact_details_2
                if contact_details and contact_details.get("PHONE"):
                    phone_data = contact_details["PHONE"][0]
                    if phone_data and phone_data.get("VALUE"):
                        client_phone = normalize_phone(phone_data["VALUE"])
                        client_name = contact_details.get("NAME", client_name)
                        phone_source = f"Контакт {contact_id}"
                        logger.debug(f"  - Телефон найден у контакта {contact_id} для сделки {deal_id} (Сценарий 3).")
            
            if not client_phone:
                logger.warning(f"⚠️ ОСТАНОВКА СЦЕНАРИЯ 3 для сделки {deal_id}: Не удалось найти номер телефона ни у компании ({company_id}), ни у контакта ({contact_id}).")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: Не удалось запустить сценарий 'Касание сегодня', не найден номер телефона клиента.")
                return {"status": "ok", "message": "No client phone number found for scenario 3"}


            manager = bitrix_service.get_user_details(manager_id)
            manager_name = f"{manager.get('NAME', '')} {manager.get('LAST_NAME', '')}".strip() if manager else "Ваш менеджер"
            
            logger.info(f"  - Клиент: {client_name} ({client_phone})")
            logger.info(f"  - Менеджер: {manager_name} ({manager_id})")

            
            # 1. Получаем историю
            current_history = db_service.get_dialog_history(db, client_phone)
            logger.info(f"  - Найдено {len(current_history)} сообщений в истории для {client_phone}.")

            # 2. Создаем команду для сценария "Касание сегодня"
            initial_instruction = {
                "role": "system",
                "content": f"initiate_touch_today_dialog. ИМЯ_КЛИЕНТА: {client_name}. ИМЯ_МЕНЕДЖЕРА: {manager_name}."
            }
            current_history.append(initial_instruction)

            # 3. Собираем промпт для этого сценария
            prompt_library = prompt_service.get_prompt_library()
            required_keys = [
                "#start#", 
                "#style#",
                "#Dialog3#" # Специальный блок для этого касания
            ]
            prompt_parts = [prompt_library.get(key, "") for key in required_keys]
            all_prompts_text = "\n\n".join(filter(None, prompt_parts))

            # 4. Отправляем в LLM
            llm_decision = await llm_service.get_bot_decision(current_history, all_prompts_text)
            if llm_decision:
                db_service.log_llm_response(db, client_phone, llm_decision)
            if not llm_decision:
                logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 3 для сделки {deal_id}: LLM не вернул решение для сценария 'Касание сегодня'.")
                bitrix_service.add_comment_to_deal(deal_id, "[Чат-бот] Ошибка: LLM не сгенерировал ответ для сценария 'Касание сегодня'.")
                if manager_id:
                    bitrix_service.escalate_deal_to_manager(
                        deal_id,
                        manager_id,
                        f"Критическая ошибка: LLM не смог сгенерировать сообщение для сделки {deal_id} в сценарии 'Касание сегодня'."
                    )
                else:
                    logger.critical(f"Не удалось эскалировать LLM ошибку (сценарий Касание сегодня) для сделки {deal_id}: нет manager_id.")
                return {"status": "error", "message": "LLM failed to provide initial decision"}

            response_text = llm_decision.get("response_text")
            action = llm_decision.get("action")
            action_params = llm_decision.get("action_params", {})
            new_state = llm_decision.get("new_state")
            
            if response_text:
                success = wazzup_service.send_message(client_phone, response_text)
                if not success:
                    logger.error(f"❌ ОСТАНОВКА СЦЕНАРИЯ 3 для сделки {deal_id}: Не удалось отправить сообщение на номер {client_phone} через Wazzup.")
                    bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот] Ошибка! Не удалось отправить сообщение 'Касание сегодня' на номер {client_phone}.")
                    if manager_id:
                        bitrix_service.escalate_deal_to_manager(
                            deal_id,
                            manager_id,
                            f"Критическая ошибка: Не удалось отправить сообщение 'Касание сегодня' на номер {client_phone} через Wazzup для сделки {deal_id}."
                        )
                    else:
                        logger.critical(f"Не удалось эскалировать Wazzup ошибку (сценарий Касание сегодня) для сделки {deal_id}: нет manager_id.")
                    return {"status": "ok", "message": "Wazzup send failed"}
                else:
                    logger.info(f"  - Сообщение 'Касание сегодня' успешно отправлено клиенту {client_phone}.")
            
            if action == "LOG_COMMENT" and action_params.get("comment_text"):
                bitrix_service.add_comment_to_deal(deal_id, f"[Чат-бот]: {action_params['comment_text']}")
                logger.info(f"  - Комментарий добавлен в сделку {deal_id}: {action_params['comment_text']}")

            dialog = db_service.get_or_create_dialog(db, client_phone, deal_id, manager_id, current_funnel_id)
            current_history = db_service.get_dialog_history(db, client_phone)
            if response_text:
                current_history.append({"role": "assistant", "content": response_text})
            
            states_that_need_reminders = [
                    "d1_awaiting_greeting_response",
                    "d1_awaiting_debtor_clarification",
                    "d1_awaiting_debtor_details",
                    "d2_awaiting_lot_response",
                    "d3_awaiting_touch_response",
                    "d3_awaiting_details_after_touch"
                ]
            should_remind = new_state in states_that_need_reminders

            db_service.update_dialog(db, client_phone, new_state, current_history, needs_reminder=should_remind)
            logger.info(f"✅ Сценарий 'Касание сегодня' для сделки {deal_id} успешно запущен. Диалог {client_phone} переведен в состояние '{new_state}'.")
        else:
            logger.info(f"Сделка {deal_id} находится в воронке '{current_funnel_id}', на стадии '{current_stage}'. Не соответствует ни одному из триггеров. Игнорирую.")


    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА в обработчике Bitrix24 вебхука для deal_id {deal_id_str}: %s", e)
        logger.critical(traceback.format_exc())
        return {"status": "error", "message": f"Critical internal error: {e}"}

    return {"status": "ok", "message": "Webhook processed"}

# --- ОБРАБОТЧИК WAZZUP ---
@app.post("/webhook/wazzup")
async def handle_wazzup_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка парсинга JSON в вебхуке Wazzup: %s", e)
        logger.error(traceback.format_exc())
        return {"status": "error", "message": "Invalid JSON format"}
    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при получении JSON из вебхука Wazzup: %s", e)
        logger.critical(traceback.format_exc())
        return {"status": "error", "message": f"Critical error processing Wazzup webhook: {e}"}


    if data.get("test") is True:
        logger.info("Получен тестовый вебхук от Wazzup. Игнорирую.")
        return {"status": "ok"}
    
    if "messages" not in data or not data["messages"]:
        logger.warning("Вебхук Wazzup содержит невалидные данные: отсутствует поле 'messages' или оно пустое. Игнорирую.")
        return {"status": "ok"}
    
    message = data["messages"][0]
    if message.get("isEcho"):
        logger.debug("Получено эхо-сообщение от Wazzup. Игнорирую.")
        return {"status": "ok"}

    text = message.get("text")
    chat_id = message.get("chatId")
    
    if text and chat_id:
        normalized_phone = normalize_phone(chat_id)
        try:
            db_service.log_user_message(db, normalized_phone, text)
            db_service.add_pending_message(db, normalized_phone, text)
            logger.info(f">>> Сообщение от {normalized_phone} добавлено в очередь.")
        except SQLAlchemyError as e:
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА БАЗЫ ДАННЫХ при добавлении сообщения Wazzup в очередь для {normalized_phone}: %s", e)
            logger.critical(traceback.format_exc())
            return {"status": "error", "message": "Database error when adding message"}
        except Exception as e:
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при обработке сообщения Wazzup от {normalized_phone}: %s", e)
            logger.critical(traceback.format_exc())
            return {"status": "error", "message": f"Critical error processing Wazzup message: {e}"}
    else:
        logger.warning(f"Вебхук Wazzup содержит сообщение без текста или chat_id. Текст: '{text}', Chat ID: '{chat_id}'. Игнорирую.")
    
    return {"status": "ok"}