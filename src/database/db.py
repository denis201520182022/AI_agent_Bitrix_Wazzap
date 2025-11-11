# src/database/db.py
import os
# Импортируем асинхронные компоненты SQLAlchemy
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# Берем URL из .env (теперь он должен быть в формате postgresql+asyncpg://...)
DATABASE_URL = os.getenv("DATABASE_URL")

# Создаем асинхронный "движок" для работы с БД
# echo=True полезно для отладки, чтобы видеть SQL-запросы в консоли. В продакшене можно убрать.
engine = create_async_engine(DATABASE_URL) #, echo=True)

# Создаем асинхронную фабрику сессий
# expire_on_commit=False - важный параметр для асинхронных сессий,
# чтобы объекты SQLAlchemy оставались доступными после коммита.
AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False, 
    autocommit=False, 
    autoflush=False
)

# Base остается таким же, как и был. Модели от него наследуются без изменений.
Base = declarative_base()

# Важно: Теперь ваши модели (Dialog, Message и т.д.) должны быть определены
# с использованием этого Base, как и раньше. Никаких изменений в моделях не требуется.