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
        'total': 0,
        'success': 0,
        'skipped_no_contact': 0,
        'skipped_no_manager': 0,
        'skipped_no_phone': 0,
        'skipped_contact_not_found': 0,
        'errors': 0
    }
    
    try:
        # Получаем все сделки из Bitrix24
        all_deals_in_target_funnel = []
        start = 0
        
        while True:
            logger.info(f"Запрашиваю сделки из Bitrix24, начиная с элемента {start}...")
            
            try:
                bitrix_deals_response = bitrix_service._make_bitrix_request(
                    "crm.deal.list.json",
                    params={
                        'filter': {"CATEGORY_ID": TARGET_FUNNEL_ID},
                        'select': ["ID", "ASSIGNED_BY_ID", "CONTACT_ID", "STAGE_ID"],
                        'start': start
                    }
                )
            except Exception as e:
                logger.error(f"❌ Ошибка при запросе сделок (start={start}): {e}")
                break

            if not bitrix_deals_response or 'result' not in bitrix_deals_response:
                logger.error("❌ Не удалось получить список сделок из Bitrix24 или пустой ответ.")
                break
            
            deals_batch = bitrix_deals_response['result']
            if not deals_batch:
                logger.info("Дополнительных сделок не найдено. Завершаю.")
                break

            all_deals_in_target_funnel.extend(deals_batch)
            start += len(deals_batch)
            
            if len(deals_batch) < 50:
                break
            
            await asyncio.sleep(0.5)

        stats['total'] = len(all_deals_in_target_funnel)
        logger.info(f"Найдено {stats['total']} сделок в целевой воронке ID: {TARGET_FUNNEL_ID}.")

        # Обрабатываем каждую сделку
        for idx, deal_data in enumerate(all_deals_in_target_funnel, 1):
            try:
                deal_id = int(deal_data["ID"])
                manager_id = int(deal_data["ASSIGNED_BY_ID"]) if deal_data.get("ASSIGNED_BY_ID") else None
                contact_id = int(deal_data["CONTACT_ID"]) if deal_data.get("CONTACT_ID") else None
                current_stage = deal_data["STAGE_ID"]

                # Проверяем наличие контакта
                if not contact_id:
                    logger.warning(f"⚠️ [{idx}/{stats['total']}] Пропускаю сделку {deal_id}: Отсутствует CONTACT_ID.")
                    stats['skipped_no_contact'] += 1
                    continue
                
                # Проверяем наличие менеджера
                if not manager_id:
                    logger.warning(f"⚠️ [{idx}/{stats['total']}] Пропускаю сделку {deal_id}: Отсутствует ASSIGNED_BY_ID (менеджер).")
                    stats['skipped_no_manager'] += 1
                    continue

                # Пытаемся получить детали контакта
                try:
                    contact_details = bitrix_service.get_contact_details_2(contact_id)
                except Exception as contact_error:
                    logger.warning(f"⚠️ [{idx}/{stats['total']}] Пропускаю сделку {deal_id}: Контакт {contact_id} не найден или ошибка API ({contact_error})")
                    stats['skipped_contact_not_found'] += 1
                    continue

                # Проверяем наличие телефона
                if not (contact_details and contact_details.get("PHONE")):
                    logger.warning(f"⚠️ [{idx}/{stats['total']}] Пропускаю сделку {deal_id}: У контакта {contact_id} нет номера телефона.")
                    stats['skipped_no_phone'] += 1
                    continue

                # Нормализуем телефон
                try:
                    client_phone = normalize_phone(contact_details["PHONE"][0].get("VALUE"))
                except Exception as phone_error:
                    logger.warning(f"⚠️ [{idx}/{stats['total']}] Пропускаю сделку {deal_id}: Ошибка нормализации телефона ({phone_error})")
                    stats['skipped_no_phone'] += 1
                    continue

                # Создаем/обновляем диалог
                try:
                    dialog = db_service.get_or_create_dialog(
                        db, client_phone, deal_id, manager_id, TARGET_FUNNEL_ID
                    )
                    
                    # Устанавливаем состояние 'idle' и сбрасываем напоминания
                    db_service.update_dialog(
                        db, client_phone, 'idle', [], needs_reminder=False
                    )
                    
                    stats['success'] += 1
                    
                    if idx % 50 == 0:  # Прогресс каждые 50 сделок
                        logger.info(f"  📊 Прогресс: {idx}/{stats['total']} ({stats['success']} успешно импортировано)")
                    else:
                        logger.debug(f"  - [{idx}/{stats['total']}] Сделка {deal_id} (клиент: {client_phone}) обработана.")
                        
                except Exception as db_error:
                    logger.error(f"❌ [{idx}/{stats['total']}] Ошибка БД для сделки {deal_id}: {db_error}")
                    stats['errors'] += 1
                    continue

            except Exception as e:
                logger.error(f"❌ [{idx}/{stats['total']}] Необработанная ошибка при обработке сделки: {e}")
                logger.error(traceback.format_exc())
                stats['errors'] += 1
                continue

    except Exception as e:
        logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА во время импорта сделок: {e}")
        logger.critical(traceback.format_exc())
    finally:
        db.close()
        
        # Выводим финальную статистику
        logger.info("=" * 60)
        logger.info("📊 СТАТИСТИКА ИМПОРТА:")
        logger.info(f"  Всего сделок найдено: {stats['total']}")
        logger.info(f"  ✅ Успешно импортировано: {stats['success']}")
        logger.info(f"  ⚠️ Пропущено (нет CONTACT_ID): {stats['skipped_no_contact']}")
        logger.info(f"  ⚠️ Пропущено (нет менеджера): {stats['skipped_no_manager']}")
        logger.info(f"  ⚠️ Пропущено (контакт не найден): {stats['skipped_contact_not_found']}")
        logger.info(f"  ⚠️ Пропущено (нет телефона): {stats['skipped_no_phone']}")
        logger.info(f"  ❌ Ошибок при импорте: {stats['errors']}")
        logger.info("=" * 60)
        logger.info("✅ Скрипт импорта завершен.")

if __name__ == "__main__":
    asyncio.run(import_legacy_deals())