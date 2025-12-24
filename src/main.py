# src/main.py
import uvicorn
import os
from app import app # Импортируем наше приложение из app.py

if __name__ == "__main__":
    # Эта команда запускает сервер uvicorn.
    # host="0.0.0.0" делает сервер доступным извне
    # port=8001 - стандартный порт (или берем из ENV)
    # reload=True - сервер будет автоматически перезапускаться при изменении кода
    
    port = int(os.getenv("PORT", 8001))

    # ВАЖНО: log_config=None отключает стандартный перехват логов Uvicorn'ом.
    # Теперь он будет использовать наш профессиональный JSON-логгер из src/logger_config.py,
    # и access-логи (кто куда зашел) тоже будут в формате JSON.
    uvicorn.run(
        "app:app", 
        host="0.0.0.0", 
        port=port, 
        reload=True, 
        log_config=None
    )
