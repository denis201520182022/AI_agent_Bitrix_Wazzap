# src/services/prompt_service.py
import os
import time
import re
import logging 
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError 
from pathlib import Path

# --- Настройка логирования ---
logger = logging.getLogger(__name__)

# --- Настройки ---
MAIN_DOC_ID = os.getenv("GOOGLE_DOC_ID", "1b_EfaKe-iqZG4beYI9lVrCwj2tOMDvK5WqaevJopXVM") 
KNOWLEDGE_BASE_DOC_ID = os.getenv("KNOWLEDGE_BASE_DOC_ID", "1TE1PJWGfSFksvcuh1Nyova91BaeegRlUBmVVeBAZ8iw")
SCOPES = ['https://www.googleapis.com/auth/documents.readonly']
SERVICE_ACCOUNT_FILE = Path(__file__).parent.parent.parent / 'credentials.json'
CACHE_TTL_SECONDS = 120

_cached_prompt_library = None
_cache_timestamp = 0

def _get_text_from_element(element):
    """Извлекает чистый текст из элемента Google Doc."""
    text = ''
    if 'textRun' in element and element.get('textRun').get('content'):
        text += element['textRun']['content']
    return text.strip()

def _parse_table_to_markdown(table: dict) -> str:
    """Конвертирует объект таблицы из Google Docs API в Markdown формат."""
    markdown_table = []
    rows = table.get('tableRows', [])
    if not rows:
        logger.debug("Таблица пуста, возвращаем пустую строку.")
        return ""

    # Обрабатываем первую строку (заголовок)
    header_row = rows[0]
    header_cells = [_get_text_from_cell(cell) for cell in header_row.get('tableCells', [])]
    markdown_table.append(f"| {' | '.join(header_cells)} |")

    # Создаем разделитель
    separator = ["---"] * len(header_cells)
    markdown_table.append(f"| {' | '.join(separator)} |")

    # Обрабатываем остальные строки
    for row in rows[1:]:
        body_cells = [_get_text_from_cell(cell) for cell in row.get('tableCells', [])]
        markdown_table.append(f"| {' | '.join(body_cells)} |")
    
    logger.debug("Таблица успешно конвертирована в Markdown", extra={"rows_count": len(rows)})
    return "\n".join(markdown_table)

def _get_text_from_cell(cell: dict) -> str:
    """Извлекает весь текст из ячейки таблицы."""
    cell_text = ''
    for content_element in cell.get('content', []):
        if 'paragraph' in content_element:
            for element in content_element['paragraph'].get('elements', []):
                cell_text += _get_text_from_element(element)
    return cell_text.replace('\n', ' ') # Убираем переносы строк внутри ячейки

def _read_and_parse_doc(document_id: str) -> dict:
    """Читает и парсит ОДИН Google Doc, включая таблицы."""
    if not document_id:
        logger.warning("ID документа не предоставлен для чтения.")
        return {}
        
<<<<<<< HEAD
    logger.debug(f"Начало чтения Google Doc", extra={"doc_id": document_id})
=======
    logger.info(f"Начало чтения Google Doc", extra={"doc_id": document_id})
>>>>>>> a4b78ad62f0af688240390a9d498809e9fab3a07
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('docs', 'v1', credentials=creds)
        document = service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        
        full_text = ''
        for value in content:
            if 'paragraph' in value:
                elements = value.get('paragraph').get('elements', [])
                for elem in elements:
                    full_text += _get_text_from_element(elem)
            elif 'table' in value:
                markdown_table_str = _parse_table_to_markdown(value['table'])
                full_text += f"\n{markdown_table_str}\n"

        doc_library = {}
        # Используем re.DOTALL, чтобы '.' соответствовал переносам строк
        markers = re.findall(r"(#\w+#)", full_text, re.DOTALL)
        parts = re.split(r"(#\w+#)", full_text, re.DOTALL)

        for i in range(len(parts)):
            if parts[i] in markers and i + 1 < len(parts):
                marker = parts[i]
                text_block = parts[i+1].strip()
                if text_block:
                    doc_library[marker] = text_block
        
<<<<<<< HEAD
        logger.debug(
=======
        logger.info(
>>>>>>> a4b78ad62f0af688240390a9d498809e9fab3a07
            f"✅ Документ успешно прочитан", 
            extra={"doc_id": document_id, "blocks_found": len(doc_library)}
        )
        return doc_library

    except FileNotFoundError:
        logger.critical(
            f"❌ ОШИБКА: Файл учетных данных не найден.", 
            exc_info=True,
            extra={"expected_path": str(SERVICE_ACCOUNT_FILE)}
        )
        return {}
    except HttpError as e:
        logger.error(
            f"❌ ОШИБКА Google Docs API", 
            exc_info=True,
            extra={"doc_id": document_id, "status_code": e.resp.status}
        )
        return {}
    except Exception as e:
        logger.critical(
            f"❌ КРИТИЧЕСКАЯ НЕИЗВЕСТНАЯ ОШИБКА при чтении документа", 
            exc_info=True,
            extra={"doc_id": document_id}
        )
        return {}


def get_prompt_library() -> dict:
    """Читает Основной Промпт и Базу Знаний, объединяет их и кэширует."""
    global _cached_prompt_library, _cache_timestamp
    
    current_time = time.time()
    if _cached_prompt_library and (current_time - _cache_timestamp < CACHE_TTL_SECONDS):
        logger.info(
            "Возврат библиотеки промптов из кэша", 
            extra={"cache_age_seconds": round(current_time - _cache_timestamp, 2)}
        )
        return _cached_prompt_library
    
    logger.info("Обновление библиотеки промптов из Google Docs...")
    
    merged_library = {}
    main_prompts = _read_and_parse_doc(MAIN_DOC_ID)
    merged_library.update(main_prompts)
    
    kb_prompts = _read_and_parse_doc(KNOWLEDGE_BASE_DOC_ID)
    merged_library.update(kb_prompts)

    if not merged_library:
        logger.error("❌ Не удалось загрузить ни одного блока промптов.")
        if _cached_prompt_library:
            logger.warning("⚠️ Использую устаревшую версию из кэша (fallback).")
            return _cached_prompt_library
        
        logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Нет промптов и нет кэша. Использую hardcoded fallback.")
        return {"#ROLE_AND_STYLE#": "Ты - вежливый ассистент."}

<<<<<<< HEAD
    logger.debug(
=======
    logger.info(
>>>>>>> a4b78ad62f0af688240390a9d498809e9fab3a07
        f"✅ Библиотека промптов обновлена.", 
        extra={"total_blocks": len(merged_library), "source_docs": [MAIN_DOC_ID, KNOWLEDGE_BASE_DOC_ID]}
    )
    _cached_prompt_library = merged_library
    _cache_timestamp = current_time
<<<<<<< HEAD
    return merged_library
=======
    return merged_library
>>>>>>> a4b78ad62f0af688240390a9d498809e9fab3a07
