# src/services/wazzup_service.py
import os
import requests
import logging # Импортируем модуль логирования
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type # Импортируем tenacity

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# Загружаем URL, ключ и ID канала из .env
API_URL = os.getenv("WAZZUP_API_URL")
API_KEY = os.getenv("WAZZUP_API_KEY")
CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID") 


# Добавляем декоратор для ретраев.
# Повторять 3 раза, ждать 2 секунды между попытками, повторять при requests.exceptions.RequestException.
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_fixed(2),         
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True # Пробросить исключение, если все попытки неудачны
)
def _send_message_with_retries(url: str, headers: dict, payload: dict) -> requests.Response:
    """Внутренняя функция для отправки сообщения через Wazzup с ретраями."""
    logger.info(f"Попытка отправки сообщения через Wazzup на {payload.get('chatId')}...")
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status() # Вызовет исключение для статусов 4xx/5xx
    return response


def send_message(phone_number: str, text: str, file_url: str = None, file_name: str = None) -> bool:
    """
    Универсальная функция для отправки текстового сообщения через Wazzup.
    Включает режим отладки в консоли, если DEBUG_CONSOLE_MODE=true.
    """
    # --- НАЧАЛО ИЗМЕНЕНИЙ: ПРОВЕРКА РЕЖИМА ОТЛАДКИ ---
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
        return True # Имитируем успешную отправку для остальной части программы
    # --- КОНЕЦ ИЗМЕНЕНИЙ ---

    # Весь ваш существующий код для отправки через Wazzup остается ниже без изменений.
    # Он будет выполнен, только если DEBUG_CONSOLE_MODE не 'true'.
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

    # Добавляем информацию о файле, если она есть
    if file_url:
        payload["fileUrl"] = file_url
    if file_name:
        payload["fileName"] = file_name
    
    try:
        response = _send_message_with_retries(url, headers, payload)

        logger.info("✅ Сообщение успешно отправлено через Wazzup.")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка при отправке сообщения через Wazzup после всех ретраев на номер {phone_number}: %s", e)
        if e.response is not None:
            logger.error(f"   Ответ сервера Wazzup: %s", e.response.text)
        return False
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при отправке сообщения через Wazzup на номер {phone_number}: %s", e)
        return False