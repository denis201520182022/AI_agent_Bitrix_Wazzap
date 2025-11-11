# init_db.py

import os
from dotenv import load_dotenv
import logging

# 1. Загружаем переменные окружения из .env файла
# Скрипт возьмет DATABASE_URL именно отсюда
load_dotenv()
print("Загружены переменные окружения из .env файла...")

# 2. Импортируем engine и Base из вашего проекта
# Убедитесь, что пути импорта верны относительно корня проекта
from src.database.db import engine, Base
print("Импортированы 'engine' и 'Base' из src.database.db...")

# 3. Импортируем все модели, чтобы SQLAlchemy о них "узнал"
# SQLAlchemy "видит" только те модели, которые были импортированы в момент выполнения
from src.database.models import Dialog
print(f"Обнаружена модель для таблицы: '{Dialog.__tablename__}'")

# Настройка логирования, чтобы видеть SQL-запросы (опционально, но очень полезно для отладки)
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

def create_tables():
    print("\nНачинаю создание таблиц в базе данных...")
    print(f"Целевая база данных: {engine.url.database} на хосте {engine.url.host}")
    
    try:
        # 4. Главная команда: создает все таблицы, которые унаследованы от Base
        # Если таблицы уже существуют, команда ничего не сделает и не вызовет ошибку.
        Base.metadata.create_all(bind=engine)
        print("\n✅ УСПЕШНО! Все таблицы были созданы (или уже существовали).")
        print("База данных готова к работе.")
    except Exception as e:
        print(f"\n❌ ПРОИЗОШЛА ОШИБКА при подключении или создании таблиц: {e}")
        print("ПОЖАЛУЙСТА, ПРОВЕРЬТЕ:")
        print("  - Правильность строки DATABASE_URL в вашем .env файле.")
        print("  - Доступность сервера базы данных с вашего IP-адреса (настройки firewall на сервере).")
        print("  - Что сервис PostgreSQL на сервере запущен.")

if __name__ == "__main__":
    create_tables()