# src/services/wazzup_service.py
import os
import httpx
import logging
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

# --- Настройка логирования ---
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
API_URL = os.getenv("WAZZUP_API_URL")
API_KEY = os.getenv("WAZZUP_API_KEY")
CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID") 

# Декоратор для ретраев при ошибках запроса httpx
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_fixed(2),         
    retry=retry_if_exception_type(httpx.RequestError),
    reraise=True 
)
async def _send_message_with_retries(url: str, headers: dict, payload: dict) -> httpx.Response:
    """Внутренняя асинхронная функция для отправки сообщения через Wazzup с ретраями."""
    
    # Структурный лог запроса. 
    # Headers не логируем целиком, чтобы не светить API Key.
    logger.debug(
        "📤 Отправка запроса в Wazzup",
        extra={
            "wazzup_url": url,
            "payload": payload
        }
    )

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
    
    response.raise_for_status() 
    logger.info("✅ Сообщение успешно отправлено через Wazzup.")
    return response

async def send_message(phone_number: str, text: str) -> bool:
    """
    Асинхронная функция для отправки текстового сообщения через Wazzup.
    """
    if not all([API_URL, API_KEY, CHANNEL_ID]):
        logger.error(
            "❌ Ошибка конфигурации Wazzup", 
            extra={"missing": "Check API_URL, API_KEY, CHANNEL_ID in .env"}
        )
        return False
    
    if not text or not text.strip():
        logger.error(
            "❌ Ошибка Wazzup: Пустой текст сообщения", 
            extra={"target_phone": phone_number}
        )
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

    except httpx.HTTPStatusError as e:
        # Логируем ошибку API со всеми деталями ответа
        response_text = "No content"
        if e.response is not None:
            response_text = e.response.text

        logger.error(
            f"❌ Wazzup API Error: {e.response.status_code}", 
            exc_info=True,
            extra={
                "target_phone": phone_number,
                "status_code": e.response.status_code,
                "response_text": response_text
            }
        )
        return False

    except httpx.RequestError as e:
        # Ошибки сети
        logger.error(
            f"❌ Network error sending to Wazzup", 
            exc_info=True,
            extra={"target_phone": phone_number}
        )
        return False

    except Exception as e:
        # Критические неизвестные ошибки
        logger.critical(
            f"❌ CRITICAL unknown error in Wazzup service", 
            exc_info=True,
            extra={"target_phone": phone_number}
        )
        return False