# src/services/llm_service.py
import os
import json
import httpx
from openai import AsyncOpenAI
from services import prompt_service # Убедитесь, что prompt_service импортируется

# --- 1. КОНФИГУРАЦИЯ КЛИЕНТА ---
# (Этот блок остается без изменений)
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
        print("✅ OpenAI клиент успешно инициализирован через прокси.")
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать OpenAI клиент через прокси: {e}")
else:
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        print("✅ OpenAI клиент успешно инициализирован (без прокси).")
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать OpenAI клиент: {e}")



#Перенес инструкцию в гугл док
JSON_FORMAT_INSTRUCTION = """  """


# --- 3. НОВАЯ УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ---

async def get_bot_decision(conversation_history: list, system_prompt: str) -> dict | None:
    """
    Получает от LLM решение в формате JSON, включающее ответ, новое состояние и действие.
    """
    if not client:
        print("❌ Ошибка: OpenAI клиент не инициализирован.")
        return None

    full_system_prompt = system_prompt + JSON_FORMAT_INSTRUCTION

    messages_for_llm = [
        {"role": "system", "content": full_system_prompt}
    ] + conversation_history

    try:
        print(f"Запрос решения от LLM с {len(messages_for_llm)} сообщениями в контексте...")
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_llm,
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        response_content = response.choices[0].message.content
        print(f"✅ LLM вернул решение: {response_content}")

        # --- НАЧАЛО БЛОКА ЛОГИРОВАНИЯ ТОКЕНОВ ---
        usage = response.usage
        
        # Безопасно получаем кешированные токены, если они есть
        cached_tokens = 0
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details is not None:
            cached_tokens = getattr(usage.prompt_tokens_details, "cached_tokens", 0)

        print("\n--- 📊 Статистика по токенам ---")
        print(f"   - Всего использовано: {usage.total_tokens} токенов")
        print(f"   - Токены запроса (Input): {usage.prompt_tokens}")
        print(f"   - Токены ответа (Output): {usage.completion_tokens}")
        print(f"   - Кешированные токены (Input): {cached_tokens}")
        
        if usage.prompt_tokens > 0:
            cache_percent = (cached_tokens / usage.prompt_tokens) * 100
            print(f"   - Эффективность кеша: {cache_percent:.1f}%")
        print("--------------------------------\n")
        # --- КОНЕЦ БЛОКА ЛОГИРОВАНИЯ ТОКЕНОВ ---
        
        return json.loads(response_content)

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА при получении решения от LLM: {e}")
        # Возвращаем "безопасный" JSON для обработки ошибки на верхнем уровне
        return {
            "response_text": "К сожалению, произошла внутренняя техническая ошибка. Мой коллега-менеджер скоро свяжется с вами, чтобы всё прояснить.",
            "new_state": "escalated",
            "action": "ESCALATE_TO_MANAGER",
            "action_params": {
                "comment_text": f"Критическая ошибка при обращении к OpenAI: {e}. Требуется срочное вмешательство."
            }
        }