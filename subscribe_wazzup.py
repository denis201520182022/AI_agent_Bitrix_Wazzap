# subscribe_wazzup.py
import os
import requests
from dotenv import load_dotenv
import json

def subscribe_to_wazzup_webhooks():
    """
    Отправляет PATCH-запрос в Wazzup для подписки на получение вебхуков.
    """
    print("Загружаем переменные окружения...")
    load_dotenv()

    API_URL = os.getenv("WAZZUP_API_URL")
    API_KEY = os.getenv("WAZZUP_API_KEY")

    if not API_URL or not API_KEY:
        print("❌ Ошибка: не удалось найти WAZZUP_API_URL или WAZZUP_API_KEY в .env файле.")
        return

    # --- ВСТАВЬТЕ СЮДА АКТУАЛЬНЫЙ URL ---
    # Он должен быть запущен и указывать на ваш порт
    NGROK_URL = "http://..."

    if "your-unique-url" in NGROK_URL or NGROK_URL == "http://...": 
        print("\n!!! ПОЖАЛУЙСТА, УБЕДИТЕСЬ, ЧТО NGROK_URL АКТУАЛЕН И УКАЗЫВАЕТ НА ВАШ РАБОТАЮЩИЙ СЕРВЕР !!!\n")
        print(f"!!! Текущий NGROK_URL: {NGROK_URL} !!!\n")
        
    webhook_uri = f"{NGROK_URL}/webhook/wazzup"

    url = f"{API_URL}/webhooks"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "webhooksUri": webhook_uri,
        "subscriptions": {
            "messagesAndStatuses": True
        }
    }

    print("-" * 50)
    print("🚀 Отправляем запрос на подписку вебхуков Wazzup:")
    print(f"   Метод: PATCH")
    print(f"   URL: {url}")
    print(f"   Headers: {json.dumps(headers, indent=2)}")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    print("-" * 50)

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=10)

        print("\n🎉 Получен ответ от Wazzup:")
        print(f"   Статус код: {response.status_code}")
        print(f"   Headers: {json.dumps(dict(response.headers), indent=2)}")
        print(f"   Тело ответа: {response.text}")
        print("-" * 50)

        response.raise_for_status()

        print("\n✅ Успешно! Wazzup принял запрос на подписку.")
        print(f"   Вебхуки будут отправляться на: {webhook_uri}")
        print("   Теперь Wazzup должен прислать тестовый запрос на ваш сервер.")
        print("   Смотрите в консоль, где запущен `python src/main.py`.")

    except requests.exceptions.RequestException as e:
        print("\n❌ Ошибка при подписке на вебхуки Wazzup:")
        print(f"   Детали ошибки: {e}")
        
        if e.response is not None:
            print(f"   Ответ сервера (статус {e.response.status_code}):")
            print(f"      Headers: {json.dumps(dict(e.response.headers), indent=2)}")
            print(f"      Тело ответа: {e.response.text}")
        else:
            print("   Нет ответа от сервера (возможно, проблема с сетью или DNS).")
        print("-" * 50)


if __name__ == "__main__":
    subscribe_to_wazzup_webhooks()
