# src/services/llm_service.py
import os
import json
import httpx
import logging
from openai import AsyncOpenAI, OpenAIError
from services import prompt_service
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

# --- Настройка логирования ---
logger = logging.getLogger(__name__)

# --- 1. КОНФИГУРАЦИЯ КЛИЕНТА ---
SQUID_PROXY_HOST = os.getenv("SQUID_PROXY_HOST")
SQUID_PROXY_PORT = os.getenv("SQUID_PROXY_PORT")
SQUID_PROXY_USER = os.getenv("SQUID_PROXY_USER")
SQUID_PROXY_PASSWORD = os.getenv("SQUID_PROXY_PASSWORD")

client = None
if all([SQUID_PROXY_HOST, SQUID_PROXY_PORT, SQUID_PROXY_USER, SQUID_PROXY_PASSWORD]):
    proxy_url = f"http://{SQUID_PROXY_USER}:{SQUID_PROXY_PASSWORD}@{SQUID_PROXY_HOST}:{SQUID_PROXY_PORT}"
    try:
        async_http_client = httpx.AsyncClient(proxy=proxy_url, timeout=45.0)
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), http_client=async_http_client)
        logger.info("✅ OpenAI клиент инициализирован", extra={"proxy": True, "proxy_host": SQUID_PROXY_HOST})
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать OpenAI клиент через прокси", exc_info=True)
else:
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        logger.info("✅ OpenAI клиент инициализирован (Direct)", extra={"proxy": False})
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать OpenAI клиент", exc_info=True)


# Перенес инструкцию в гугл док
JSON_FORMAT_INSTRUCTION = """
ВАЖНОЕ ТЕХНИЧЕСКОЕ ТРЕБОВАНИЕ:
Твой ответ ОБЯЗАН быть валидным JSON объектом. Не пиши никакого вступительного текста.
Убедись, что ты используешь слово 'json' в своем ответе.
"""


# --- 3. НОВАЯ УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ---

# Добавляем декоратор для ретраев
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_fixed(2),         
    retry=(retry_if_exception_type(OpenAIError) | retry_if_exception_type(json.JSONDecodeError)),
    reraise=True 
)
async def _call_llm_with_retries(messages_for_llm: list):
    """Внутренняя функция для вызова LLM с ретраями."""
    logger.debug("Вызов OpenAI API", extra={"msg_count": len(messages_for_llm), "model": "gpt-4o-mini"})
    
    # ИЗМЕНЕНО: temperature 0.3 -> 0.7 для более живого общения
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages_for_llm,
        temperature=0.7, 
        response_format={"type": "json_object"}
    )
    return response


async def get_bot_decision(conversation_history: list, system_prompt: str) -> dict | None:
    """
    Получает от LLM решение в формате JSON, включающее ответ, новое состояние и действие.
    """
    if not client:
        logger.error("❌ Ошибка: OpenAI клиент не инициализирован.")
        return None

    full_system_prompt = system_prompt + JSON_FORMAT_INSTRUCTION

    messages_for_llm = [
        {"role": "system", "content": full_system_prompt}
    ] + conversation_history

    try:
        logger.info(f"Запрос решения от LLM", extra={"context_len": len(messages_for_llm)})
        
        response = await _call_llm_with_retries(messages_for_llm)

        response_content = response.choices[0].message.content
        logger.debug(f"Получен ответ от LLM", extra={"content_preview": response_content[:100] + "..."})

        # --- БЛОК ЛОГИРОВАНИЯ ТОКЕНОВ ---
        usage = response.usage
        
        cached_tokens = 0
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details is not None:
            cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0)

        cache_percent = 0
        if usage.prompt_tokens > 0:
            cache_percent = (cached_tokens / usage.prompt_tokens) * 100

        # Агрегируем статистику в один JSON объект для логов
        token_stats = {
            "total": usage.total_tokens,
            "input": usage.prompt_tokens,
            "output": usage.completion_tokens,
            "cached": cached_tokens,
            "cache_efficiency_pct": round(cache_percent, 2)
        }

        logger.info("📊 LLM Usage Stats", extra={"token_usage": token_stats})
        # --- КОНЕЦ БЛОКА ---
        
        return json.loads(response_content)

    except OpenAIError as e: 
        logger.error(
            f"❌ Ошибка OpenAI API после ретраев", 
            exc_info=True,
            extra={"error_type": "OpenAIError"}
        )
        return {
            "response_text": None,
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"Ошибка OpenAI API: {e}. Требуется вмешательство."
            }
        }
    except json.JSONDecodeError as e: 
        logger.error(
            f"❌ Ошибка парсинга JSON ответа от LLM", 
            exc_info=True,
            extra={"response_content": response_content if 'response_content' in locals() else "No content"}
        )
        return {
            "response_text": None,
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"LLM вернул невалидный JSON: {e}."
            }
        }
    except Exception as e:
        logger.critical(
            f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА LLM Service", 
            exc_info=True
        )
        return {
            "response_text": None,
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"Критическая ошибка LLM: {e}."
            }
        }