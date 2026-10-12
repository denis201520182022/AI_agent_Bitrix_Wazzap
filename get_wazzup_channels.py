# get_wazzup_channels.py          Скрпт для проверки подключенных каналов в Вазаппе
import os
import requests
from dotenv import load_dotenv

def fetch_and_print_channels():
    """
    Подключается к Wazzup API и выводит список всех подключенных каналов и их ID.
    """
    print("Загружаем переменные окружения...")
    load_dotenv()

    API_URL = os.getenv("WAZZUP_API_URL")
    API_KEY = os.getenv("WAZZUP_API_KEY")

    if not API_URL or not API_KEY:
        print("❌ Ошибка: не удалось найти WAZZUP_API_URL или WAZZUP_API_KEY в файле .env")
        return

    endpoint = "/channels"
    url = f"{API_URL}{endpoint}"

    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }

    print("Отправляем запрос для получения списка каналов Wazzup...")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        print(data)
        if data:
            print("\n--- ✅ СПИСОК ВАШИХ КАНАЛОВ WAZZUP ---")
            for channel in data:
                channel_id = channel.get('id')
                channel_name = channel.get('name', 'Без имени')
                print(f"Название: {channel_name:<20} | ID: {channel_id}")
            print("-----------------------------------------")
        else:
            print("⚠️ Список каналов пуст. Ответ от Wazzup:", data)

    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка при запросе к API Wazzup: {e}")
        if e.response:
            print(f"   Ответ сервера: {e.response.text}")

if __name__ == "__main__":
    fetch_and_print_channels()
