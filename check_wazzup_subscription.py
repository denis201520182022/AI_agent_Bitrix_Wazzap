# check_wazzup_subscription.py
import os
import requests
from dotenv import load_dotenv
import json

def check_wazzup_subscription():
    """
    Отправляет GET-запрос в Wazzup для проверки текущего адреса вебхуков.
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
        "Authorization": f"Bearer {API_KEY}"
    }

    print("-" * 50)
    print("🚀 Отправляем запрос на проверку подписки:")
    print(f"   Метод: GET")
    print(f"   URL: {url}")
    print("-" * 50)

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        print("\n🎉 Получен ответ от Wazzup:")
        print(f"   Статус код: {response.status_code}")
        response_data = response.json()
        print("   Тело ответа:")
        print(json.dumps(response_data, indent=2, ensure_ascii=False))
        print("-" * 50)
        
        uri = response_data.get("webhooksUri")
        if uri:
            print(f"\n✅ Текущий адрес для получения вебхуков: {uri}")
        else:
            print("\n⚠️ Адрес для вебхуков не установлен.")

    except requests.exceptions.RequestException as e:
        print(f"\n❌ Ошибка при проверке подписки: {e}")

if __name__ == "__main__":
    check_wazzup_subscription()