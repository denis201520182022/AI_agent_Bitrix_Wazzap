# unsubscribe_wazzup.py
import os
import requests
from dotenv import load_dotenv
import json

def unsubscribe_from_wazzup_webhooks():
    """
    Отправляет PATCH-запрос в Wazzup для отписки от вебхуков,
    устанавливая webhooksUri в null.
    """
    print("Загружаем переменные окружения...")
    load_dotenv()

    API_URL = os.getenv("WAZZUP_API_URL")
    API_KEY = os.getenv("WAZZUP_API_KEY")

    if not API_URL or not API_KEY:
        print("❌ Ошибка: не удалось найти WAZZUP_API_URL или WAZZUP_API_KEY в .env файле.")
        return

    url = f"{API_URL}/webhooks"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    # Для отписки передаем null в качестве адреса
    payload = {
        "webhooksUri": None,
    }

    print("-" * 50)
    print("🚀 Отправляем запрос на ОТПИСКУ от вебхуков Wazzup:")
    print(f"   Метод: PATCH")
    print(f"   URL: {url}")
    print(f"   Payload: {json.dumps(payload, indent=2)}")
    print("-" * 50)

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()

        print("\n🎉 Получен ответ от Wazzup:")
        print(f"   Статус код: {response.status_code}")
        print(f"   Тело ответа: {response.text}")
        print("-" * 50)
        
        if response.status_code == 200:
            print("\n✅ Успешно! Подписка на вебхуки отменена.")
        else:
            print("\n⚠️ Запрос прошел, но статус не 200 OK. Проверьте ответ выше.")

    except requests.exceptions.RequestException as e:
        print(f"\n❌ Ошибка при отписке от вебхуков: {e}")

if __name__ == "__main__":
    unsubscribe_from_wazzup_webhooks()