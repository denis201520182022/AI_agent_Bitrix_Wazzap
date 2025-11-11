import os
import requests
import logging
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Настройка ретраев для Bitrix24 ---
# В случае HTTP-ошибок (4xx, 5xx), сетевых проблем
bitrix_retry_decorator = retry(
    stop=stop_after_attempt(3), # Повторить 3 раза
    wait=wait_fixed(2),         # Ждать 2 секунды между попытками
    retry=retry_if_exception_type(requests.exceptions.RequestException), # Повторять при ошибках Requests
    before_sleep=before_sleep_log(logger, logging.WARNING), # Логировать перед каждой повторной попыткой
    reraise=True # Пробросить исключение, если все попытки неудачны
)

# Получаем базовый URL вебхука из переменных окружения
BASE_URL = os.getenv("BITRIX_WEBHOOK_URL")


@bitrix_retry_decorator
def _make_bitrix_request(method_url: str, params: dict = None) -> dict | None:
    """
    Внутренняя вспомогательная функция для выполнения POST-запросов к Bitrix24 API
    с общей обработкой ошибок и ретраями.
    """
    if not BASE_URL:
        logger.error("❌ Ошибка: URL вебхука для Битрикс24 не задан в .env. Запрос не выполнен.")
        return None

    full_url = f"{BASE_URL}{method_url}"
    
    try:
        logger.debug(f"Выполнение запроса к Bitrix24: {full_url}, параметры: {params}")
        response = requests.post(full_url, json=params)
        response.raise_for_status() # Вызовет исключение для HTTP-ошибок 4xx/5xx
        data = response.json()
        
        if 'error' in data:
            # Bitrix API может возвращать HTTP 200, но с ошибкой в теле JSON
            error_code = data.get('error')
            error_description = data.get('error_description', 'Неизвестная ошибка Bitrix API')
            logger.error(f"❌ Ошибка Bitrix API (код: {error_code}): %s при запросе к {method_url}", error_description)
            return None
        
        logger.debug(f"Успешный ответ от Bitrix24 для {method_url}")
        return data

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка сетевого запроса к Bitrix24 после всех ретраев ({method_url}): %s", e)
        if e.response is not None:
            logger.error(f"   Ответ сервера Bitrix24: %s", e.response.text)
        raise # Перепробрасываем исключение для tenacity
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при запросе к Bitrix24 ({method_url}): %s", e)
        raise # Перепробрасываем исключение для tenacity


def get_deals(limit: int = 5) -> list | None:
    """
    Получает 'limit' последних сделок из Битрикс24.
    """
    logger.info(f"Запрос {limit} последних сделок из Битрикс24.")
    data = _make_bitrix_request(
        "crm.deal.list.json",
        params={
            'order': {"ID": "DESC"},
            'select': ["ID", "TITLE", "STAGE_ID"],
            'limit': limit # Добавляем лимит для более эффективного запроса
        }
    )
    if data and 'result' in data:
        logger.info(f"✅ Получено {len(data['result'])} сделок.")
        return data['result']
    else:
        logger.warning("⚠️ В ответе от Битрикс24 нет данных о сделках. Возможно, их просто нет?")
        return []


def get_deal_details(deal_id: int) -> dict | None:
    """
    Получает детальную информацию о сделке по ее ID.
    """
    logger.info(f"Запрос деталей сделки {deal_id}.")
    data = _make_bitrix_request(
        "crm.deal.get.json",
        params={'id': deal_id}
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали сделки {deal_id} получены.")
        return data['result']
    else:
        logger.warning(f"⚠️ Не удалось получить детали сделки {deal_id}. Ответ API: %s", data)
        return None


def get_contact_details(contact_id: int) -> dict | None:
    """
    Получает детальную информацию о контакте по его ID.
    Целенаправленно запрашивает имя и телефон.
    """
    logger.info(f"Запрос деталей контакта {contact_id}.")
    data = _make_bitrix_request(
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


def get_user_details(user_id: int) -> dict | None:
    """
    Получает информацию о пользователе (менеджере) по его ID.
    """
    logger.info(f"Запрос деталей пользователя {user_id}.")
    data = _make_bitrix_request(
        "user.get.json",
        params={'ID': user_id}
    )
    if data and 'result' in data and len(data['result']) > 0:
        logger.info(f"✅ Детали пользователя {user_id} получены.")
        return data['result'][0]
    else:
        logger.warning(f"⚠️ Ответ от Битрикс24 не содержит данных для пользователя {user_id}. Ответ API: %s", data)
        return None


def get_latest_activity_for_deal(deal_id: int) -> dict | None:
    """
    Получает самое последнее дело (активность), связанное со сделкой.
    """
    logger.info(f"Запрос последней активности для сделки {deal_id}.")
    data = _make_bitrix_request(
        "crm.activity.list.json",
        params={
            'order': {"ID": "DESC"},
            'filter': {
                "OWNER_TYPE_ID": 2, # 2 - это системный ID для "Сделки"
                "OWNER_ID": deal_id
            },
            'select': ["ID", "DESCRIPTION"],
            'limit': 1 # Запрашиваем только одну, самую новую
        }
    )
    if data and 'result' in data and len(data['result']) > 0:
        logger.info(f"✅ Получена последняя активность для сделки {deal_id}.")
        return data['result'][0]
    else:
        logger.warning(f"⚠️ Для сделки {deal_id} не найдено связанных дел/активностей. Ответ API: %s", data)
        return None


def create_activity_for_deal(deal_id: int, responsible_id: int, subject: str, description: str) -> int | None:
    """
    Создает новое универсальное дело (crm.activity.todo.add), привязанное к сделке.
    Возвращает ID созданного дела или None в случае ошибки.
    """
    logger.info(f"Попытка создания дела для сделки {deal_id}, ответственный {responsible_id}.")
    deadline_time = (datetime.now() + timedelta(hours=3)).strftime('%Y-%m-%dT23:59:59')

    data = _make_bitrix_request(
        "crm.activity.todo.add.json",
        params={
            "ownerTypeId": 2,
            "ownerId": deal_id,
            "responsibleId": responsible_id,
            "deadline": deadline_time,
            "title": subject,
            "description": description,
        }
    )
    if data and data['result'].get('id'):
        created_id = data['result']['id']
        logger.info(f"✅ Успешно создано универсальное дело с ID: {created_id} для сделки {deal_id}")
        return created_id
    else:
        error_detail = data.get('error_description') or data.get('error', 'Неизвестно')
        logger.error(f"⚠️ Не удалось создать дело для сделки {deal_id}. Ошибка: %s. Ответ API: %s", error_detail, data)
        return None


def add_comment_to_deal(deal_id: int, comment_text: str) -> bool:
    """
    Добавляет комментарий в таймлайн сделки.
    Используется для логирования действий бота.
    """
    logger.info(f"Добавление комментария к сделке {deal_id}.")
    data = _make_bitrix_request(
        "crm.timeline.comment.add.json",
        params={
            'fields': {
                "ENTITY_ID": deal_id,
                "ENTITY_TYPE": "deal",
                "COMMENT": comment_text
            }
        }
    )
    if data and 'result' in data: # Bitrix возвращает ID комментария или true/false
        logger.info(f"✅ Комментарий успешно добавлен к сделке {deal_id}")
        return True
    else:
        error_detail = data.get('error_description') or data.get('error', 'Неизвестно')
        logger.error(f"❌ Ошибка при добавлении комментария к сделке {deal_id}: %s. Ответ API: %s", error_detail, data)
        return False


def move_deal_to_stage(deal_id: int, stage_id: str) -> bool:
    """
    Перемещает сделку на указанную стадию.
    """
    logger.info(f"Перемещение сделки {deal_id} на стадию {stage_id}.")
    data = _make_bitrix_request(
        "crm.deal.update.json",
        params={
            'id': deal_id,
            'fields': {
                "STAGE_ID": stage_id
            }
        }
    )
    if data and data.get('result') is True: # Bitrix возвращает true при успехе
        logger.info(f"✅ Сделка {deal_id} успешно перемещена на стадию {stage_id}")
        return True
    else:
        error_detail = data.get('error_description') or data.get('error', 'Неизвестно')
        logger.error(f"❌ Ошибка при перемещении сделки {deal_id} на стадию {stage_id}: %s. Ответ API: %s", error_detail, data)
        return False


def escalate_deal_to_manager(deal_id: int, manager_id: int, reason: str):
    """
    Выполняет полную процедуру эскалации:
    1. Создает дело для менеджера с причиной.
    2. Перемещает сделку на стадию 'Касание сегодня'.
    """
    logger.info(f"--- НАЧАЛО ПРОЦЕДУРЫ ЭСКАЛАЦИИ для сделки {deal_id} ---")

    subject = f"Эскалация от чат-бота: Сделка №{deal_id}"
    description = (f"Требуется внимание менеджера.\n"
                   f"Причина эскалации: {reason}")

    activity_created = create_activity_for_deal(
        deal_id=deal_id,
        responsible_id=manager_id,
        subject=subject,
        description=description
    )

    if not activity_created:
        logger.error("   - ❗️ Не удалось создать дело для эскалации.")
        # Здесь можно добавить логику уведомления администратора, если потребуется
        return

    escalation_stage_id = os.getenv("TOUCH_TODAY_STAGE_ID")
    if escalation_stage_id:
        move_deal_to_stage(deal_id, escalation_stage_id)
    else:
        logger.error("   - ❗️ Не удалось переместить сделку: переменная TOUCH_TODAY_STAGE_ID не найдена в .env")

    logger.info(f"--- ПРОЦЕДУРА ЭСКАЛАЦИИ ЗАВЕРШЕНА для сделки {deal_id} ---")


def get_deal_details_2(deal_id: int) -> dict | None:
    """
    Получает детальную информацию о сделке по ее ID.
    """
    logger.info(f"Запрос деталей сделки {deal_id}.")
    data = _make_bitrix_request(
        "crm.deal.get.json",
        params={'id': deal_id, 'select': ["ID", "TITLE", "STAGE_ID", "CONTACT_ID", "COMPANY_ID", "ASSIGNED_BY_ID"]} # Добавляем COMPANY_ID и другие важные поля
    )
    if data and 'result' in data:
        logger.info(f"✅ Детали сделки {deal_id} получены.")
        return data['result']
    else:
        logger.warning(f"⚠️ Не удалось получить детали сделки {deal_id}. Ответ API: %s", data)
        return None

def get_company_details(company_id: int) -> dict | None:
    """
    Получает детальную информацию о компании по ее ID.
    Целенаправленно запрашивает имя и телефон.
    """
    logger.info(f"Запрос деталей компании {company_id}.")
    data = _make_bitrix_request(
        "crm.company.get.json",
        params={
            'id': company_id,
            'select': ["TITLE", "PHONE"] # В Bitrix24 имя компании это "TITLE"
        }
    )
    if not data or 'result' not in data or data['result'] is None:
        logger.warning(f"⚠️ Не удалось получить детали компании {company_id}. Ответ API: %s", data)
        return None
    
    logger.info(f"✅ Детали компании {company_id} получены.")
    return data.get('result')


def get_contact_details_2(contact_id: int) -> dict | None:
    """
    Получает детальную информацию о контакте по его ID.
    Целенаправленно запрашивает имя и телефон.
    
    Возвращает None если контакт не найден (вместо выброса исключения).
    """
    logger.info(f"Запрос деталей контакта {contact_id}.")
    
    try:
        data = _make_bitrix_request(
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
            
    except requests.exceptions.HTTPError as e:
        # Если контакт не найден (404 или 400 с "Not found"), это не критичная ошибка
        if e.response is not None and e.response.status_code in [400, 404]:
            error_data = e.response.json() if e.response.text else {}
            if 'Not found' in error_data.get('error_description', ''):
                logger.warning(f"⚠️ Контакт {contact_id} не найден в Bitrix24.")
                return None
        
        # Для других HTTP ошибок - пробрасываем дальше
        logger.error(f"❌ HTTP ошибка при запросе контакта {contact_id}: {e}")
        raise
        
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при запросе контакта {contact_id}: {e}")
        raise