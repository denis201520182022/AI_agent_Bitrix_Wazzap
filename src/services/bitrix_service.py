# src/services/bitrix_service.py
import os
import httpx 
import logging
from datetime import datetime, timedelta, timezone
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log, RetryError

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
# Если хендлеры не настроены (запуск скрипта напрямую), ставим базу. 
# В app.py сработает setup_logging из logger_config.
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Настройка ретраев для асинхронных запросов ---
bitrix_retry_decorator = retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(httpx.RequestError),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True
)

# Получаем базовый URL
BASE_URL = os.getenv("BITRIX_WEBHOOK_URL")


@bitrix_retry_decorator
async def _make_bitrix_request(method_url: str, params: dict = None) -> dict | None:
    """
    (Асинхронная) Внутренняя функция для выполнения POST-запросов к Bitrix24 API.
    """
    if not BASE_URL:
        logger.error("❌ Ошибка конфигурации: URL вебхука Bitrix24 не задан.")
        return None

    full_url = f"{BASE_URL}{method_url}"
    
    try:
        async with httpx.AsyncClient() as client:
            # Логируем параметры как структуру данных, а не часть строки
            logger.debug(
                "Request to Bitrix24", 
                extra={"url": full_url, "params": params, "method": method_url}
            )
            
            response = await client.post(full_url, json=params, timeout=30.0)
        
        response.raise_for_status()
        data = response.json()
        
        if 'error' in data:
            error_code = data.get('error')
            error_desc = data.get('error_description', 'Unknown Bitrix API error')
            
            # Структурная ошибка
            logger.error(
                f"❌ Bitrix API Error: {error_code}", 
                extra={
                    "error_code": error_code, 
                    "error_description": error_desc, 
                    "method": method_url
                }
            )
            return None
        
        logger.debug(f"Success response from Bitrix24", extra={"method": method_url})
        return data

    except httpx.RequestError as e:
        logger.error(
            f"❌ Network error requesting Bitrix24 ({method_url})", 
            exc_info=True, # Добавляет трейсбек для Sentry
            extra={"url": full_url}
        )
        # tenacity поймает это и сделает повтор
    except RetryError as e:
        logger.error(
            f"❌ Failed to reach Bitrix24 after retries ({method_url})", 
            exc_info=True
        )
    except Exception as e:
        logger.critical(
            f"❌ CRITICAL unknown error in Bitrix request ({method_url})", 
            exc_info=True,
            extra={"url": full_url}
        )
    
    return None


async def get_deals(limit: int = 5) -> list | None:
    """
    (Асинхронно) Получает 'limit' последних сделок из Битрикс24.
    """
    logger.info(f"Запрос последних сделок", extra={"limit": limit})
    data = await _make_bitrix_request(
        "crm.deal.list.json",
        params={
            'order': {"ID": "DESC"},
            'select': ["ID", "TITLE", "STAGE_ID"],
            'limit': limit
        }
    )
    if data and 'result' in data:
        count = len(data['result'])
        logger.info(f"✅ Получено сделок: {count}", extra={"count": count})
        return data['result']
    else:
        logger.warning("⚠️ В ответе от Битрикс24 нет данных о сделках.")
        return []


async def get_deal_details(deal_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о сделке по ее ID.
    """
    # deal_id берется из контекста логгера (из app.py), но для надежности можно добавить в extra
    logger.info("Запрос деталей сделки (simple)", extra={"target_deal_id": deal_id})
    data = await _make_bitrix_request(
        "crm.deal.get.json",
        params={'id': deal_id}
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали сделки получены.")
        return data['result']
    else:
        logger.warning(f"⚠️ Не удалось получить детали сделки.", extra={"target_deal_id": deal_id, "api_response": data})
        return None


async def get_contact_details(contact_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о контакте по его ID.
    """
    logger.info("Запрос деталей контакта", extra={"contact_id": contact_id})
    data = await _make_bitrix_request(
        "crm.contact.get.json",
        params={
            'id': contact_id,
            'select': ["NAME", "PHONE"]
        }
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали контакта получены.")
        return data.get('result')
    else:
        logger.warning(f"⚠️ Не удалось получить детали контакта.", extra={"contact_id": contact_id})
        return None


async def get_user_details(user_id: int) -> dict | None:
    """
    (Асинхронно) Получает информацию о пользователе (менеджере) по его ID.
    """
    logger.info("Запрос деталей менеджера", extra={"user_id": user_id})
    data = await _make_bitrix_request(
        "user.get.json",
        params={'ID': user_id}
    )
    if data and 'result' in data and len(data['result']) > 0:
        return data['result'][0]
    else:
        logger.warning(f"⚠️ Данные менеджера не найдены.", extra={"user_id": user_id})
        return None


async def get_latest_activity_for_deal(deal_id: int) -> dict | None:
    """
    (Асинхронно) Получает самое последнее дело (активность), связанное со сделкой.
    """
    logger.info("Запрос последней активности", extra={"target_deal_id": deal_id})
    data = await _make_bitrix_request(
        "crm.activity.list.json",
        params={
            'order': {"ID": "DESC"},
            'filter': {
                "OWNER_TYPE_ID": 2,
                "OWNER_ID": deal_id
            },
            'select': ["ID", "DESCRIPTION"],
            'limit': 1
        }
    )
    if data and 'result' in data and len(data['result']) > 0:
        return data['result'][0]
    else:
        logger.warning(f"⚠️ Активностей не найдено.", extra={"target_deal_id": deal_id})
        return None


async def create_activity_for_deal(deal_id: int, responsible_id: int, subject: str, description: str) -> int | None:
    """
    (Асинхронно) Создает новое дело, привязанное к сделке.
    """
    logger.info("Создание дела (Task)", extra={
        "target_deal_id": deal_id, 
        "responsible_id": responsible_id, 
        "subject": subject
    })
    
    tz_moscow = timezone(timedelta(hours=3))
    deadline_time = (datetime.now() + timedelta(hours=3)).strftime('%Y-%m-%dT23:59:59')

    data = await _make_bitrix_request(
        "crm.activity.todo.add.json",
        params={
            "ownerTypeId": 2,
            "ownerId": deal_id,
            "responsibleId": responsible_id,
            "deadline": deadline_time,
            "subject": subject,
            "description": description,
        }
    )
    if data and data['result'].get('id'):
        created_id = data['result']['id']
        logger.info(f"✅ Дело создано. ID: {created_id}")
        return created_id
    else:
        logger.error(f"⚠️ Не удалось создать дело.", extra={"api_response": data, "target_deal_id": deal_id})
        return None
    

async def add_comment_to_deal(deal_id: int, comment_text: str) -> bool:
    """
    (Асинхронно) Добавляет комментарий в таймлайн сделки.
    """
    logger.info("Добавление комментария", extra={"target_deal_id": deal_id})
    data = await _make_bitrix_request(
        "crm.timeline.comment.add.json",
        params={
            'fields': {
                "ENTITY_ID": deal_id,
                "ENTITY_TYPE": "deal",
                "COMMENT": comment_text
            }
        }
    )
    if data and 'result' in data:
        return True
    else:
        logger.error(f"❌ Ошибка добавления комментария.", extra={"target_deal_id": deal_id, "api_response": data})
        return False


async def move_deal_to_stage(deal_id: int, stage_id: str) -> bool:
    """
    (Асинхронно) Перемещает сделку на указанную стадию.
    """
    logger.info(f"Перемещение сделки", extra={"target_deal_id": deal_id, "new_stage": stage_id})
    data = await _make_bitrix_request(
        "crm.deal.update.json",
        params={
            'id': deal_id,
            'fields': {
                "STAGE_ID": stage_id
            }
        }
    )
    if data and data.get('result') is True:
        logger.info(f"✅ Сделка успешно перемещена.")
        return True
    else:
        logger.error(f"❌ Ошибка перемещения сделки.", extra={"target_deal_id": deal_id, "stage": stage_id, "api_response": data})
        return False


async def escalate_deal_to_manager(deal_id: int, manager_id: int, reason: str):
    """
    (Асинхронно) Выполняет полную процедуру эскалации.
    """
    logger.info(f"🚀 ЭСКАЛАЦИЯ МЕНЕДЖЕРУ", extra={"target_deal_id": deal_id, "reason": reason})

    subject = f"Эскалация от чат-бота: Сделка №{deal_id}"
    description = (f"Требуется внимание менеджера.\n"
                   f"Причина эскалации: {reason}")

    activity_created_id = await create_activity_for_deal(
        deal_id=deal_id,
        responsible_id=manager_id,
        subject=subject,
        description=description
    )

    if not activity_created_id:
        logger.error("❗️ Не удалось создать дело для эскалации.", extra={"target_deal_id": deal_id})
        return

    logger.info(f"✅ Эскалация завершена.")


async def get_deal_details_2(deal_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о сделке по ее ID (расширенный select).
    """
    logger.info("Запрос деталей сделки (full)", extra={"target_deal_id": deal_id})
    data = await _make_bitrix_request(
        "crm.deal.get.json",
        params={'id': deal_id, 'select': ["*", "UF_*"]}
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали сделки получены.")
        return data['result']
    else:
        logger.warning(f"⚠️ Не удалось получить детали сделки.", extra={"target_deal_id": deal_id})
        return None


async def get_company_details(company_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о компании по ее ID.
    """
    logger.info("Запрос деталей компании", extra={"company_id": company_id})
    data = await _make_bitrix_request(
        "crm.company.get.json",
        params={
            'id': company_id,
            'select': ["*", "UF_*"]
        }
    )
    if data and 'result' in data:
        return data.get('result')
    else:
        logger.warning(f"⚠️ Не удалось получить детали компании.", extra={"company_id": company_id})
        return None


async def get_contact_details_2(contact_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о контакте по его ID.
    """
    logger.info("Запрос деталей контакта", extra={"contact_id": contact_id})
    
    try:
        data = await _make_bitrix_request(
            "crm.contact.get.json",
            params={
                'id': contact_id,
                'select': ["*", "UF_*"]
            }
        )
        
        if data and 'result' in data:
            return data.get('result')
        else:
            return None
            
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"⚠️ Контакт не найден (404).", extra={"contact_id": contact_id})
            return None
        
        logger.error(f"❌ HTTP ошибка при запросе контакта.", exc_info=True, extra={"contact_id": contact_id})
        raise
        
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при запросе контакта.", exc_info=True, extra={"contact_id": contact_id})
        return None
