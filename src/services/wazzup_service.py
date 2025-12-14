# src/services/wazzup_service.py
import os
import httpx
import logging
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import json # Импортируем для красивого вывода JSON

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
# Установим уровень логирования на DEBUG, чтобы видеть подробные сообщения
# В продакшене можете изменить на INFO или WARNING
if not logger.handlers:
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Переменные окружения ---
API_URL = os.getenv("WAZZUP_API_URL")
API_KEY = os.getenv("WAZZUP_API_KEY")
CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID") 

# Декоратор для ретраев при ошибках запроса httpx
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_fixed(2),         
    retry=retry_if_exception_type(httpx.RequestError),
    reraise=True # Пробрасывать исключение, если все попытки неудачны
)
async def _send_message_with_retries(url: str, headers: dict, payload: dict) -> httpx.Response:
    """Внутренняя асинхронная функция для отправки сообщения через Wazzup с ретраями."""
    
    # --- Логируем полный запрос перед отправкой ---
    logger.debug(f"📤 Отправка запроса в Wazzup:")
    logger.debug(f"   URL: {url}")
    logger.debug(f"   Headers: {json.dumps(headers, indent=2)}")
    logger.debug(f"   Payload: {json.dumps(payload, indent=2)}")
    # --- Конец логирования запроса ---

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
    
    # httpx автоматически логирует базовую информацию об ответе (например, 400 Bad Request)
    # но raise_for_status() вызовет исключение, если статус 4xx/5xx

    response.raise_for_status() # Вызовет httpx.HTTPStatusError для статусов 4xx/5xx
    logger.info("✅ Сообщение успешно отправлено через Wazzup.")
    return response

async def send_message(phone_number: str, text: str) -> bool:
    """
    Асинхронная функция для отправки текстового сообщения через Wazzup.
    """
    if not all([API_URL, API_KEY, CHANNEL_ID]):
        logger.error("❌ Ошибка Wazzup: API_URL, API_KEY или CHANNEL_ID не заданы в .env. Сообщение не отправлено.")
        return False
    
    if not text or not text.strip(): # Проверяем, что текст не пустой или состоит только из пробелов
        logger.error("❌ Ошибка Wazzup: Текст сообщения не может быть пустым. Сообщение не отправлено.")
        return False

    endpoint = "/message"
    url = f"{API_URL}{endpoint}"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "channelId": CHANNEL_ID,
        "chatType": "whatsapp",
        "chatId": phone_number,
        "text": text
    }
    
    try:
        await _send_message_with_retries(url, headers, payload)
        return True

    # Ловим специфическое исключение для HTTP ошибок, которое вызывает raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ HTTP Ошибка при отправке сообщения через Wazzup на номер {phone_number}: %s", e)
        # Логируем полный ответ сервера при ошибке
        if e.response is not None:
            logger.error(f"   Ответ сервера Wazzup (статус {e.response.status_code}): {e.response.text}")
        return False
    # Ловим любые другие ошибки запроса (например, проблемы с сетью), не связанные с HTTP статусами
    except httpx.RequestError as e:
        logger.error(f"❌ Ошибка сети или запроса Wazzup (не HTTP статус) при отправке на номер {phone_number}: %s", e)
        # Если есть хоть какой-то ответ, логируем его
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"   Ответ сервера Wazzup (статус {e.response.status_code}): %s", e.response.text)
        return False
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при отправке сообщения через Wazzup на номер {phone_number}: %s", e, exc_info=True)
        return False