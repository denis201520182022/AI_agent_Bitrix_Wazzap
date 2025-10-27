# src/services/llm_service.py
import os
import json
import httpx
import logging
from openai import AsyncOpenAI, OpenAIError # Импортируем OpenAIError для ретраев
from services import prompt_service
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type # Импортируем tenacity

# --- Настройка логирования ---
logger = logging.getLogger(__name__)
# В реальном приложении logging.basicConfig лучше вынести в центральный модуль (например, app.py или config.py)
# Для этого примера оставлю здесь, чтобы было видно.
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


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
        logger.info("✅ OpenAI клиент успешно инициализирован через прокси.")
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать OpenAI клиент через прокси: %s", e)
else:
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        logger.info("✅ OpenAI клиент успешно инициализирован (без прокси).")
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать OpenAI клиент: %s", e)


# Перенес инструкцию в гугл док
JSON_FORMAT_INSTRUCTION = """  """


# --- 3. НОВАЯ УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ---

# Добавляем декоратор для ретраев
@retry(
    stop=stop_after_attempt(3), # Повторить 3 раза
    wait=wait_fixed(2),         # Ждать 2 секунды между попытками
    retry=(retry_if_exception_type(OpenAIError) | retry_if_exception_type(json.JSONDecodeError)), # Повторять при ошибках OpenAI или некорректном JSON
    reraise=True # Пробросить исключение, если все попытки неудачны
)
async def _call_llm_with_retries(messages_for_llm: list):
    """Внутренняя функция для вызова LLM с ретраями."""
    logger.info(f"Вызов LLM. Сообщений в контексте: {len(messages_for_llm)}")
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages_for_llm,
        temperature=0.3,
        response_format={"type": "json_object"}
    )
    return response


async def get_bot_decision(conversation_history: list, system_prompt: str) -> dict | None:
    """
    Получает от LLM решение в формате JSON, включающее ответ, новое состояние и действие.
    """
    if not client:
        logger.error("❌ Ошибка: OpenAI клиент не инициализирован. Невозможно получить решение LLM.")
        return None

    full_system_prompt = system_prompt + JSON_FORMAT_INSTRUCTION

    messages_for_llm = [
        {"role": "system", "content": full_system_prompt}
    ] + conversation_history

    try:
        logger.debug(f"Запрос решения от LLM с {len(messages_for_llm)} сообщениями в контексте...")
        
        response = await _call_llm_with_retries(messages_for_llm) # Используем обернутую функцию

        response_content = response.choices[0].message.content
        logger.info(f"✅ LLM вернул решение: %s", response_content)

        # --- НАЧАЛО БЛОКА ЛОГИРОВАНИЯ ТОКЕНОВ ---
        usage = response.usage
        
        # Безопасно получаем кешированные токены, если они есть
        cached_tokens = 0
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details is not None:
            cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0)

        logger.info("\n--- 📊 Статистика по токенам ---")
        logger.info(f"   - Всего использовано: {usage.total_tokens} токенов")
        logger.info(f"   - Токены запроса (Input): {usage.prompt_tokens}")
        logger.info(f"   - Токены ответа (Output): {usage.completion_tokens}")
        logger.info(f"   - Кешированные токены (Input): {cached_tokens}")
        
        if usage.prompt_tokens > 0:
            cache_percent = (cached_tokens / usage.prompt_tokens) * 100
            logger.info(f"   - Эффективность кеша: {cache_percent:.1f}%%")
        logger.info("--------------------------------\n")
        # --- КОНЕЦ БЛОКА ЛОГИРОВАНИЯ ТОКЕНОВ ---
        
        return json.loads(response_content)

    except OpenAIError as e: # Отдельно логгируем ошибки OpenAI
        logger.error(f"❌ Ошибка при обращении к OpenAI API после всех ретраев: %s", e)
        return {
            "response_text": "К сожалению, произошла внутренняя техническая ошибка при обращении к ИИ. Мой коллега-менеджер скоро свяжется с вами, чтобы всё прояснить.",
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"Ошибка OpenAI API после ретраев: {e}. Требуется срочное вмешательство."
            }
        }
    except json.JSONDecodeError as e: # Отдельно логгируем ошибки парсинга JSON
        logger.error(f"❌ Ошибка парсинга JSON ответа от LLM после всех ретраев: %s", e)
        return {
            "response_text": None,
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"LLM вернул невалидный JSON: {e}. Требуется срочное вмешательство."
            }
        }
    except Exception as e: # Все остальные критические ошибки
        logger.critical(f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при получении решения от LLM: %s", e)
        return {
            "response_text": None,
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"Критическая неизвестная ошибка при обращении к LLM: {e}. Требуется срочное вмешательство."
            }
        }