# src/main.py
import uvicorn
import os
from app import app

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    uvicorn.run(
        "app:app", 
        host="0.0.0.0", 
        port=port, 
        reload=True, 
        log_config=None
    )
