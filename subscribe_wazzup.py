# subscribe_wazzup.py
import os
import requests
from dotenv import load_dotenv

def subscribe_to_wazzup_webhooks():
    """
    Отправляет PATCH-запрос в Wazzup для подписки на получение вебхуков.
    """
    print("Загружаем переменные окружения...")
    load_dotenv()

    API_URL = os.getenv("WAZZUP_API_URL")
    API_KEY = os.getenv("WAZZUP_API_KEY")

    if not API_URL or not API_KEY:
        print("❌ Ошибка: не удалось найти WAZZUP_API_URL или WAZZUP_API_KEY")
        return

    # --- ВАЖНО: ВСТАВЬТЕ СЮДА ВАШ АКТУАЛЬНЫЙ NGROK URL ---
    # Он должен быть запущен и указывать на ваш порт 8000
    NGROK_URL = "https://277556483b39.ngrok-free.app" 
    # ---------------------------------------------------------

    if "your-unique-url" in NGROK_URL:
        print("\n!!! ПОЖАЛУЙСТА, ЗАПУСТИТЕ NGROK И ВСТАВЬТЕ ВАШ URL В ЭТОТ СКРИПТ !!!\n")
        return

    # Формируем полный URL для вебхуков
    webhook_uri = f"{NGROK_URL}/webhook/wazzup"

    # Готовим запрос, как в документации
    url = f"{API_URL}/webhooks"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "webhooksUri": webhook_uri,
        "subscriptions": {
            "messagesAndStatuses": True # Подписываемся на сообщения
        }
    }

    print(f"Отправляем запрос на подписку для URL: {webhook_uri}...")

    try:
        response = requests.patch(url, headers=headers, json=payload)
        response.raise_for_status()
        print("✅ Успешно! Wazzup принял запрос на подписку.")
        print("   Теперь Wazzup должен прислать тестовый запрос на ваш сервер.")
        print("   Смотрите в консоль, где запущен `python src/main.py`.")

    except requests.exceptions.RequestException as e:
        
        print(f"❌ Ошибка при подписке на вебхуки Wazzup: {e}")
        if e.response:
            print(f"   Ответ сервера: {e.response.text}")


if __name__ == "__main__":
    subscribe_to_wazzup_webhooks()