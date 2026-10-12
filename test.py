import os
import requests
from dotenv import load_dotenv
import json

def get_wazzup_webhook_address():
    print("Загружаем переменные окружения...")
    load_dotenv()

    API_URL = os.getenv("WAZZUP_API_URL")
    API_KEY = os.getenv("WAZZUP_API_KEY")

    if not API_URL or not API_KEY:
        print("❌ Ошибка: не удалось найти WAZZUP_API_URL или WAZZUP_API_KEY в .env")
        return

    url = f"{API_URL}/webhooks"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    print(f"Отправляем GET запрос для проверки подписки на URL: {url}...")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        webhook_info = response.json()
        print("✅ Успешно получен текущий адрес вебхуков Wazzup:")
        print(json.dumps(webhook_info, indent=2, ensure_ascii=False))

    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка при получении информации о вебхуках Wazzup: {e}")
        if e.response:
            print(f"   Ответ сервера: {e.response.text}")

if __name__ == "__main__":
    get_wazzup_webhook_address()
