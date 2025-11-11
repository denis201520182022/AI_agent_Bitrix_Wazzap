import os
import httpx # ИЗМЕНЕНО: Импортируем httpx вместо requests
import logging
from datetime import datetime, timedelta
# ИЗМЕНЕНО: tenacity остается, но будет работать с async функциями
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log, RetryError

# --- Настройка логирования (без изменений) ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- ИЗМЕНЕНО: Настройка ретраев для асинхронных запросов ---
bitrix_retry_decorator = retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(httpx.RequestError), # ИЗМЕНЕНО: отслеживаем исключение httpx
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True
)

# Получаем базовый URL (без изменений)
BASE_URL = os.getenv("BITRIX_WEBHOOK_URL")


# ИЗМЕНЕНО: Функция стала асинхронной
@bitrix_retry_decorator
async def _make_bitrix_request(method_url: str, params: dict = None) -> dict | None:
    """
    (Асинхронная) Внутренняя функция для выполнения POST-запросов к Bitrix24 API.
    """
    if not BASE_URL:
        logger.error("❌ Ошибка: URL вебхука для Битрикс24 не задан в .env. Запрос не выполнен.")
        return None

    full_url = f"{BASE_URL}{method_url}"
    
    # ИЗМЕНЕНО: Используем httpx.AsyncClient для асинхронных запросов
    try:
        async with httpx.AsyncClient() as client:
            logger.debug(f"Выполнение запроса к Bitrix24: {full_url}, параметры: {params}")
            # ИЗМЕНЕНО: Выполняем асинхронный POST-запрос
            response = await client.post(full_url, json=params, timeout=30.0)
        
        response.raise_for_status()
        data = response.json()
        
        if 'error' in data:
            error_code = data.get('error')
            error_description = data.get('error_description', 'Неизвестная ошибка Bitrix API')
            logger.error(f"❌ Ошибка Bitrix API (код: {error_code}): %s при запросе к {method_url}", error_description)
            return None
        
        logger.debug(f"Успешный ответ от Bitrix24 для {method_url}")
        return data

    # ИЗМЕНЕНО: Отлавливаем исключения httpx и tenacity
    except httpx.RequestError as e:
        logger.error(f"❌ Ошибка сетевого запроса к Bitrix24 ({method_url}): %s", e)
        # В tenacity reraise=True, поэтому он сам пробросит исключение дальше.
        # Нам не нужно делать raise здесь, декоратор сделает это за нас.
        # Оставим только логгирование.
    except RetryError as e:
        logger.error(f"❌ Запрос к Bitrix24 ({method_url}) провалился после всех попыток. Последняя ошибка: %s", e)
        # reraise=True в декораторе, поэтому исключение будет проброшено дальше
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при запросе к Bitrix24 ({method_url}): %s", e)
    
    # Если мы дошли сюда из-за ошибки, значит что-то пошло не так
    return None


# ИЗМЕНЕНО: Функция стала асинхронной
async def get_deals(limit: int = 5) -> list | None:
    """
    (Асинхронно) Получает 'limit' последних сделок из Битрикс24.
    """
    logger.info(f"Запрос {limit} последних сделок из Битрикс24.")
    # ИЗМЕНЕНО: Используем await для вызова асинхронной функции
    data = await _make_bitrix_request(
        "crm.deal.list.json",
        params={
            'order': {"ID": "DESC"},
            'select': ["ID", "TITLE", "STAGE_ID"],
            'limit': limit
        }
    )
    if data and 'result' in data:
        logger.info(f"✅ Получено {len(data['result'])} сделок.")
        return data['result']
    else:
        logger.warning("⚠️ В ответе от Битрикс24 нет данных о сделках.")
        return []


# ИЗМЕНЕНО: Функция стала асинхронной
async def get_deal_details(deal_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о сделке по ее ID.
    """
    logger.info(f"Запрос деталей сделки {deal_id}.")
    # ИЗМЕНЕНО: Используем await для вызова асинхронной функции
    data = await _make_bitrix_request(
        "crm.deal.get.json",
        params={'id': deal_id}
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали сделки {deal_id} получены.")
        return data['result']
    else:
        logger.warning(f"⚠️ Не удалось получить детали сделки {deal_id}. Ответ API: %s", data)
        return None
    
    # (Предполагается, что асинхронная функция _make_bitrix_request уже определена выше)

# ИЗМЕНЕНО: Функция стала асинхронной
async def get_contact_details(contact_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о контакте по его ID.
    """
    logger.info(f"Запрос деталей контакта {contact_id}.")
    # ИЗМЕНЕНО: Добавлен await
    data = await _make_bitrix_request(
        "crm.contact.get.json",
        params={
            'id': contact_id,
            'select': ["NAME", "PHONE"]
        }
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали контакта {contact_id} получены.")
        return data.get('result')
    else:
        logger.warning(f"⚠️ Не удалось получить детали контакта {contact_id}. Ответ API: %s", data)
        return None


# ИЗМЕНЕНО: Функция стала асинхронной
async def get_user_details(user_id: int) -> dict | None:
    """
    (Асинхронно) Получает информацию о пользователе (менеджере) по его ID.
    """
    logger.info(f"Запрос деталей пользователя {user_id}.")
    # ИЗМЕНЕНО: Добавлен await
    data = await _make_bitrix_request(
        "user.get.json",
        params={'ID': user_id}
    )
    if data and 'result' in data and len(data['result']) > 0:
        logger.info(f"✅ Детали пользователя {user_id} получены.")
        return data['result'][0]
    else:
        logger.warning(f"⚠️ Ответ от Битрикс24 не содержит данных для пользователя {user_id}. Ответ API: %s", data)
        return None


# ИЗМЕНЕНО: Функция стала асинхронной
async def get_latest_activity_for_deal(deal_id: int) -> dict | None:
    """
    (Асинхронно) Получает самое последнее дело (активность), связанное со сделкой.
    """
    logger.info(f"Запрос последней активности для сделки {deal_id}.")
    # ИЗМЕНЕНО: Добавлен await
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
        logger.info(f"✅ Получена последняя активность для сделки {deal_id}.")
        return data['result'][0]
    else:
        logger.warning(f"⚠️ Для сделки {deal_id} не найдено связанных дел/активностей. Ответ API: %s", data)
        return None


# ИЗМЕНЕНО: Функция стала асинхронной
async def create_activity_for_deal(deal_id: int, responsible_id: int, subject: str, description: str) -> int | None:
    """
    (Асинхронно) Создает новое дело, привязанное к сделке.
    Возвращает ID созданного дела или None в случае ошибки.
    """
    logger.info(f"Попытка создания дела для сделки {deal_id}, ответственный {responsible_id}.")
    # Расчет времени остается синхронным, ему не нужен async
    deadline_time = (datetime.now() + timedelta(hours=3)).strftime('%Y-%m-%dT23:59:59')

    # ИЗМЕНЕНО: Добавлен await
    data = await _make_bitrix_request(
        "crm.activity.todo.add.json",
        params={
            "fields": { # ИЗМЕНЕНО: Современный API Битрикс24 часто требует параметры внутри "fields"
                "OWNER_TYPE_ID": 2,
                "OWNER_ID": deal_id,
                "RESPONSIBLE_ID": responsible_id,
                "DEADLINE": deadline_time,
                "SUBJECT": subject,
                "DESCRIPTION": description,
            }
        }
    )
    # Важное примечание: метод crm.activity.todo.add может возвращать ID прямо в 'result'
    if data and isinstance(data.get('result'), int):
        created_id = data['result']
        logger.info(f"✅ Успешно создано дело с ID: {created_id} для сделки {deal_id}")
        return created_id
    else:
        error_detail = data.get('error_description') if data else 'Нет ответа от API'
        logger.error(f"⚠️ Не удалось создать дело для сделки {deal_id}. Ошибка: %s. Ответ API: %s", error_detail, data)
        return None
# ИЗМЕНЕНО: Функция стала асинхронной
async def add_comment_to_deal(deal_id: int, comment_text: str) -> bool:
    """
    (Асинхронно) Добавляет комментарий в таймлайн сделки.
    """
    logger.info(f"Добавление комментария к сделке {deal_id}.")
    # ИЗМЕНЕНО: Добавлен await
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
        logger.info(f"✅ Комментарий успешно добавлен к сделке {deal_id}")
        return True
    else:
        error_detail = data.get('error_description') if data else 'Нет ответа от API'
        logger.error(f"❌ Ошибка при добавлении комментария к сделке {deal_id}: %s. Ответ API: %s", error_detail, data)
        return False


# ИЗМЕНЕНО: Функция стала асинхронной
async def move_deal_to_stage(deal_id: int, stage_id: str) -> bool:
    """
    (Асинхронно) Перемещает сделку на указанную стадию.
    """
    logger.info(f"Перемещение сделки {deal_id} на стадию {stage_id}.")
    # ИЗМЕНЕНО: Добавлен await
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
        logger.info(f"✅ Сделка {deal_id} успешно перемещена на стадию {stage_id}")
        return True
    else:
        error_detail = data.get('error_description') if data else 'Нет ответа от API'
        logger.error(f"❌ Ошибка при перемещении сделки {deal_id} на стадию {stage_id}: %s. Ответ API: %s", error_detail, data)
        return False


# ИЗМЕНЕНО: Функция стала асинхронной и вызывает другие async функции
async def escalate_deal_to_manager(deal_id: int, manager_id: int, reason: str):
    """
    (Асинхронно) Выполняет полную процедуру эскалации.
    """
    logger.info(f"--- НАЧАЛО ПРОЦЕДУРЫ ЭСКАЛАЦИИ для сделки {deal_id} ---")

    subject = f"Эскалация от чат-бота: Сделка №{deal_id}"
    description = (f"Требуется внимание менеджера.\n"
                   f"Причина эскалации: {reason}")

    # ИЗМЕНЕНО: Добавлен await
    activity_created_id = await create_activity_for_deal(
        deal_id=deal_id,
        responsible_id=manager_id,
        subject=subject,
        description=description
    )

    if not activity_created_id:
        logger.error("   - ❗️ Не удалось создать дело для эскалации.")
        return

    # escalation_stage_id = os.getenv("TOUCH_TODAY_STAGE_ID")
    # if escalation_stage_id:
    #     # ИЗМЕНЕНО: Добавлен await
    #     await move_deal_to_stage(deal_id, escalation_stage_id)
    # else:
    #     logger.error("   - ❗️ Не удалось переместить сделку: переменная TOUCH_TODAY_STAGE_ID не найдена в .env")

    logger.info(f"--- ПРОЦЕДУРА ЭСКАЛАЦИИ ЗАВЕРШЕНА для сделки {deal_id} ---")


# ИЗМЕНЕНО: Функция стала асинхронной
async def get_deal_details_2(deal_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о сделке по ее ID.
    """
    logger.info(f"Запрос деталей сделки {deal_id}.")
    # ИЗМЕНЕНО: Добавлен await
    data = await _make_bitrix_request(
        "crm.deal.get.json",
        params={'id': deal_id, 'select': ["*", "UF_*"]} # Запрашиваем все поля, включая пользовательские
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали сделки {deal_id} получены.")
        return data['result']
    else:
        logger.warning(f"⚠️ Не удалось получить детали сделки {deal_id}. Ответ API: %s", data)
        return None


# ИЗМЕНЕНО: Функция стала асинхронной
async def get_company_details(company_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о компании по ее ID.
    """
    logger.info(f"Запрос деталей компании {company_id}.")
    # ИЗМЕНЕНО: Добавлен await
    data = await _make_bitrix_request(
        "crm.company.get.json",
        params={
            'id': company_id,
            'select': ["*", "UF_*"]
        }
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали компании {company_id} получены.")
        return data.get('result')
    else:
        logger.warning(f"⚠️ Не удалось получить детали компании {company_id}. Ответ API: %s", data)
        return None


# ИЗМЕНЕНО: Функция стала асинхронной, обновлена обработка ошибок
async def get_contact_details_2(contact_id: int) -> dict | None:
    """
    (Асинхронно) Получает детальную информацию о контакте по его ID.
    Возвращает None, если контакт не найден.
    """
    logger.info(f"Запрос деталей контакта {contact_id}.")
    
    try:
        # ИЗМЕНЕНО: Добавлен await
        data = await _make_bitrix_request(
            "crm.contact.get.json",
            params={
                'id': contact_id,
                'select': ["*", "UF_*"]
            }
        )
        
        if data and 'result' in data:
            logger.info(f"✅ Детали контакта {contact_id} получены.")
            return data.get('result')
        else:
            # _make_bitrix_request уже залогировал ошибку, здесь просто возвращаем None
            return None
            
    except httpx.HTTPStatusError as e: # ИЗМЕНЕНО: Ловим ошибку httpx
        # Если контакт не найден, API Битрикса часто возвращает 404
        if e.response.status_code == 404:
            logger.warning(f"⚠️ Контакт {contact_id} не найден в Bitrix24 (HTTP 404).")
            return None
        
        # Для других HTTP ошибок - пробрасываем дальше, чтобы tenacity мог их поймать
        logger.error(f"❌ HTTP ошибка при запросе контакта {contact_id}: {e}")
        raise
        
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при запросе контакта {contact_id}: {e}")
        # Не пробрасываем дальше, чтобы не уронить весь воркер
        return None