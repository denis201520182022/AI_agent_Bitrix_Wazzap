# src/logger_config.py
import logging
import sys
import os
import contextvars
from pythonjsonlogger import jsonlogger
import requests
import datetime

request_id_ctx = contextvars.ContextVar("request_id", default=None)
deal_id_ctx = contextvars.ContextVar("deal_id", default=None)
chat_id_ctx = contextvars.ContextVar("chat_id", default=None)

class ContextFilter(logging.Filter):
    """
    Этот фильтр автоматически добавляет значения из ContextVars в каждую запись лога.
    """
    def filter(self, record):
        req_id = request_id_ctx.get()
        d_id = deal_id_ctx.get()
        c_id = chat_id_ctx.get()

        if req_id:
            record.request_id = req_id
        if d_id:
            record.deal_id = d_id
        if c_id:
            record.chat_id = c_id
        return True

class TelegramBotHandler(logging.Handler):
    def __init__(self, token, chat_id):
        super().__init__()
        self.token = token
        self.chat_id = chat_id

    def emit(self, record):
        if record.levelno < logging.ERROR:
            return

        try:
            log_entry = self.format(record)
            emoji = "🚨" if record.levelno == logging.ERROR else "🔥"
            title = "ERROR" if record.levelno == logging.ERROR else "CRITICAL"
            context_info = ""
            if hasattr(record, 'deal_id'):
                context_info += f"\n🆔 <b>Deal ID:</b> {record.deal_id}"
            if hasattr(record, 'chat_id'):
                context_info += f"\n📱 <b>Chat ID:</b> {record.chat_id}"
            
            msg = (
                f"{emoji} <b>BOT ALERT: {title}</b>\n"
                f"{context_info}\n\n"
                f"<code>{log_entry}</code>"
            )
            
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={
                "chat_id": self.chat_id,
                "text": msg,
                "parse_mode": "HTML"
            }, timeout=5)
        except Exception:
            pass

def setup_logging():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    tg_token = os.getenv("TG_BOT_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")

    logger = logging.getLogger()
    logger.setLevel(log_level)

    if logger.handlers:
        logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s',
        json_ensure_ascii=False,
        timestamp=True 
    )
    console_handler.setFormatter(formatter)
    context_filter = ContextFilter()
    console_handler.addFilter(context_filter)
    logger.addHandler(console_handler)

    if tg_token and tg_chat_id:
        tg_handler = TelegramBotHandler(tg_token, tg_chat_id)
        tg_formatter = logging.Formatter('%(message)s')
        tg_handler.setFormatter(tg_formatter)
        tg_handler.addFilter(context_filter)
        logger.addHandler(tg_handler)
        logging.info("✅ Telegram alerting enabled.")
    else:
        logging.warning("⚠️ Telegram settings not found. Alerts disabled.")

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)

    return logger
