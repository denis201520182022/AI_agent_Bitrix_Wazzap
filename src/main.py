# src/main.py
import uvicorn
from app import app # Импортируем наше приложение из app.py

if __name__ == "__main__":
    # Эта команда запускает сервер uvicorn.
    # host="0.0.0.0" делает сервер доступным извне (понадобится позже)
    # port=8000 - стандартный порт для веб-разработки
    # reload=True - сервер будет автоматически перезапускаться при изменении кода
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=True)