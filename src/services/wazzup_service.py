# src/services/wazzup_service.py
import os
import httpx # ИЗМЕНЕНО: Импортируем httpx вместо requests
import logging
# ИЗМЕНЕНО: Импортируем асинхронную версию retry из tenacity
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Переменные окружения (без изменений) ---
API_URL = os.getenv("WAZZUP_API_URL")
API_KEY = os.getenv("WAZZUP_API_KEY")
CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID") 

# ИЗМЕНЕНО: Декоратор теперь будет работать с асинхронной функцией.
# Мы также меняем отслеживаемое исключение на httpx.RequestError.
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_fixed(2),         
    retry=retry_if_exception_type(httpx.RequestError), # ИЗМЕНЕНО: отслеживаем исключение httpx
    reraise=True
)
# ИЗМЕНЕНО: Функция стала асинхронной (async def)
async def _send_message_with_retries(url: str, headers: dict, payload: dict) -> httpx.Response:
    """(Асинхронная) Внутренняя функция для отправки сообщения через Wazzup с ретраями."""
    logger.info(f"Попытка отправки сообщения через Wazzup на {payload.get('chatId')}...")
    
    # ИЗМЕНЕНО: Используем асинхронный клиент httpx
    async with httpx.AsyncClient() as client:
        # ИЗМЕНЕНО: Выполняем асинхронный POST-запрос с помощью await
        response = await client.post(url, headers=headers, json=payload)
    
    response.raise_for_status() # Метод остался тем же, что и в requests
    return response

# ИЗМЕНЕНО: Главная функция теперь тоже асинхронная
async def send_message(phone_number: str, text: str, file_url: str = None, file_name: str = None) -> bool:
    """
    (Асинхронная) Универсальная функция для отправки сообщения через Wazzup.
    Включает режим отладки в консоли, если DEBUG_CONSOLE_MODE=true.
    """
    if os.getenv("DEBUG_CONSOLE_MODE") == "true":
        print("\n" + "="*60)
        print(f"🤖 === [БОТ ОТПРАВЛЯЕТ СООБЩЕНИЕ (РЕЖИМ КОНСОЛИ)] === 🤖")
        print(f"    Кому: {phone_number}")
        print(f"    Текст: {text}")
        if file_url:
            print(f"    Файл URL: {file_url}")
        if file_name:
            print(f"    Имя файла: {file_name}")
        print("="*60 + "\n")
        return True
    
    if not all([API_URL, API_KEY, CHANNEL_ID]):
        logger.error("❌ Ошибка Wazzup: API_URL, API_KEY или CHANNEL_ID не заданы в .env. Сообщение не отправлено.")
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

    if file_url:
        payload["fileUrl"] = file_url
    if file_name:
        payload["fileName"] = file_name
    
    try:
        # ИЗМЕНЕНО: Вызываем внутреннюю функцию через await
        response = await _send_message_with_retries(url, headers, payload)
        logger.info("✅ Сообщение успешно отправлено через Wazzup.")
        return True

    # ИЗМЕНЕНО: Ловим исключение httpx.RequestError
    except httpx.RequestError as e:
        logger.error(f"❌ Ошибка при отправке сообщения через Wazzup после всех ретраев на номер {phone_number}: %s", e)
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"   Ответ сервера Wazzup: %s", e.response.text)
        return False
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при отправке сообщения через Wazzup на номер {phone_number}: %s", e)
        return False