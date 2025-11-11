import os
import asyncio
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Импортируем необходимые сервисы и модели из вашего проекта
from src.database.db import SessionLocal, engine, Base
from src.database.models import Dialog 
from src.database import db_service
from src.services import bitrix_service
from src.utils import normalize_phone 

import logging
import traceback

# --- Настройка логирования для скрипта ---
logger = logging.getLogger(__name__)
# Устанавливаем базовую конфигурацию логирования, если она еще не настроена
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- ЗАГРУЗКА НАСТРОЕК ИЗ .ENV ---
TARGET_FUNNEL_ID = os.getenv("TARGET_FUNNEL_ID") 

async def import_legacy_deals():
    logger.info("🚀 Запуск скрипта импорта существующих сделок...")

    if not TARGET_FUNNEL_ID:
        logger.error("❌ Переменная окружения TARGET_FUNNEL_ID не задана. Импорт невозможен.")
        return

    db = SessionLocal()
    
    # Статистика импорта
    stats = {
        'total_deals_fetched': 0,
        'processed_deals': 0,
        'success': 0,
        'skipped_no_manager': 0,
        'skipped_no_phone': 0,
        'skipped_bitrix_api_error': 0,
        'skipped_by_title_filter': 0, # <-- Добавил новый счетчик для статистики
        'errors_db': 0
    }
    
    try:
        # Получаем все сделки из Bitrix24.
        all_deals_simple_data = []
        start = 0
        
        while True:
            logger.info(f"Запрашиваю сделки из Bitrix24, начиная с элемента {start}...")
            
            try:
                # --- ИЗМЕНЕНИЕ 1: Добавлено "TITLE" в 'select' ---
                bitrix_deals_response = bitrix_service._make_bitrix_request(
                    "crm.deal.list.json",
                    params={
                        'filter': {
                            "CATEGORY_ID": TARGET_FUNNEL_ID,
                            "!=STAGE_ID": "C11:UC_O7UAOV"
                        },
                        'select': ["ID", "ASSIGNED_BY_ID", "CONTACT_ID", "TITLE"], # <-- "TITLE" добавлен здесь
                        'start': start
                    }
                )
            except Exception as e:
                logger.error(f"❌ Ошибка при запросе списка сделок из Bitrix24 (start={start}): {e}")
                stats['skipped_bitrix_api_error'] += 1
                break

            if not bitrix_deals_response or 'result' not in bitrix_deals_response:
                logger.error("❌ Не удалось получить список сделок из Bitrix24 или пустой ответ.")
                break
            
            deals_batch = bitrix_deals_response['result']
            if not deals_batch:
                logger.info("Дополнительных сделок не найдено. Завершаю.")
                break

            all_deals_simple_data.extend(deals_batch)
            start += len(deals_batch)
            
            if len(deals_batch) < 50: 
                break
            
            await asyncio.sleep(0.5) 

        stats['total_deals_fetched'] = len(all_deals_simple_data)
        logger.info(f"Найдено {stats['total_deals_fetched']} сделок в целевой воронке ID: {TARGET_FUNNEL_ID}.")

        # Обрабатываем каждую сделку
        for idx, deal_simple_data in enumerate(all_deals_simple_data, 1):
            deal_id_for_log = deal_simple_data.get("ID", "N/A")

            # --- ИЗМЕНЕНИЕ 2: Фильтрация по названию сделки ---
            deal_title = deal_simple_data.get("TITLE", "")
            if deal_title.endswith("(Привлечен доп должник)"):
                logger.info(f"  - Пропускаю сделку {deal_id_for_log} с названием '{deal_title}', т.к. это доп. должник.")
                stats['skipped_by_title_filter'] += 1
                continue # Переходим к следующей сделке
            # --- КОНЕЦ ИЗМЕНЕНИЙ ---

            stats['processed_deals'] += 1
            deal_id = int(deal_simple_data["ID"])

            try:
                deal_full_details = bitrix_service.get_deal_details_2(deal_id)
                if not deal_full_details:
                    logger.warning(f"⚠️ [{idx}/{stats['total_deals_fetched']}] Пропускаю сделку {deal_id}: Не удалось получить полные детали сделки из Bitrix24.")
                    stats['skipped_bitrix_api_error'] += 1
                    continue

                manager_id = int(deal_full_details.get("ASSIGNED_BY_ID")) if deal_full_details.get("ASSIGNED_BY_ID") else None
                contact_id = int(deal_full_details.get("CONTACT_ID")) if deal_full_details.get("CONTACT_ID") else None
                company_id = int(deal_full_details.get("COMPANY_ID")) if deal_full_details.get("COMPANY_ID") else None

                if not manager_id:
                    logger.warning(f"⚠️ [{idx}/{stats['total_deals_fetched']}] Пропускаю сделку {deal_id}: Отсутствует ASSIGNED_BY_ID (менеджер).")
                    stats['skipped_no_manager'] += 1
                    continue

                client_phone = None
                client_name = "Уважаемый клиент"
                phone_source = "Не найден"

                if contact_id:
                    contact_details = bitrix_service.get_contact_details_2(contact_id)
                    if contact_details and contact_details.get("PHONE"):
                        phone_data = contact_details["PHONE"][0]
                        if phone_data and phone_data.get("VALUE"):
                            client_phone = normalize_phone(phone_data["VALUE"])
                            client_name = contact_details.get("NAME", client_name)
                            phone_source = f"Контакт {contact_id}"
                            logger.debug(f"  - [{idx}/{stats['total_deals_fetched']}] Телефон найден у контакта {contact_id} для сделки {deal_id}.")

                if not client_phone and company_id:
                    company_details = bitrix_service.get_company_details(company_id)
                    if company_details and company_details.get("PHONE"):
                        company_phones = company_details["PHONE"]
                        chosen_phone_data = None

                        for phone_entry in company_phones:
                            if phone_entry.get("VALUE_TYPE") == "WORK":
                                chosen_phone_data = phone_entry
                                break

                        if not chosen_phone_data and company_phones:
                            chosen_phone_data = company_phones[0]
                        
                        if chosen_phone_data and chosen_phone_data.get("VALUE"):
                            client_phone = normalize_phone(chosen_phone_data["VALUE"])
                            client_name = company_details.get("TITLE", client_name) 
                            phone_source = f"Компания {company_id}"
                            logger.debug(f"  - [{idx}/{stats['total_deals_fetched']}] Телефон найден у компании {company_id} (источник: {phone_source}) для сделки {deal_id}.")
                                
                if not client_phone:
                    logger.warning(f"⚠️ [{idx}/{stats['total_deals_fetched']}] Пропускаю сделку {deal_id}: Не удалось найти номер телефона ни у контакта ({contact_id}), ни у компании ({company_id}).")
                    stats['skipped_no_phone'] += 1
                    continue

                try:
                    dialog = db_service.get_or_create_dialog(db, client_phone, deal_id, manager_id, TARGET_FUNNEL_ID)
                    db_service.update_dialog(db, client_phone, 'idle', [], needs_reminder=False)
                    stats['success'] += 1
                    
                    if idx % 50 == 0 or idx == stats['total_deals_fetched']:  
                        logger.info(f"  📊 Прогресс: {idx}/{stats['total_deals_fetched']} (Успешно: {stats['success']}). Последняя сделка {deal_id}.")
                    else:
                        logger.debug(f"  - [{idx}/{stats['total_deals_fetched']}] Сделка {deal_id} (клиент: {client_name}, телефон: {client_phone}, источник: {phone_source}) обработана.")
                        
                except Exception as db_error:
                    logger.error(f"❌ [{idx}/{stats['total_deals_fetched']}] Ошибка БД при создании/обновлении диалога для сделки {deal_id}: {db_error}")
                    logger.error(traceback.format_exc())
                    stats['errors_db'] += 1
                    db.rollback()
                    continue

            except Exception as e:
                logger.error(f"❌ [{idx}/{stats['total_deals_fetched']}] Необработанная ошибка при обработке сделки {deal_id}: {e}")
                logger.error(traceback.format_exc())
                stats['errors_db'] += 1
                continue

    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА верхнего уровня во время импорта сделок: {e}")
        logger.critical(traceback.format_exc())
    finally:
        db.close()
        
        logger.info("=" * 60)
        logger.info("📊 СТАТИСТИКА ИМПОРТА:")
        logger.info(f"  Всего сделок найдено в Битрикс: {stats['total_deals_fetched']}")
        logger.info(f"  Попыток обработки сделок: {stats['processed_deals']}")
        logger.info(f"  ✅ Успешно импортировано диалогов: {stats['success']}")
        logger.info(f"  ⚠️ Пропущено (нет менеджера): {stats['skipped_no_manager']}")
        logger.info(f"  ⚠️ Пропущено (нет телефона у контакта/компании): {stats['skipped_no_phone']}")
        logger.info(f"  ⚠️ Пропущено по фильтру названия: {stats['skipped_by_title_filter']}") # <-- Добавил вывод в статистику
        logger.info(f"  ❌ Ошибки Bitrix API: {stats['skipped_bitrix_api_error']}")
        logger.info(f"  ❌ Ошибки БД (не удалось создать/обновить диалог): {stats['errors_db']}")
        logger.info("=" * 60)
        logger.info("✅ Скрипт импорта завершен.")

if __name__ == "__main__":
    asyncio.run(import_legacy_deals())