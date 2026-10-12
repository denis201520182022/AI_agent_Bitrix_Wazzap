# src/utils.py
from starlette.datastructures import FormData
import re
from datetime import datetime
import pytz

def parse_form_data(data: FormData) -> dict:
    """
    Преобразует "плоскую" FormData от Битрикс24 во вложенный словарь.
    Пример: ключ 'data[FIELDS][ID]' превратится в {'data': {'FIELDS': {'ID': ...}}}
    """
    parsed_dict = {}
    for key, value in data.items():
        parts = key.replace("]", "").split("[")
        d = parsed_dict
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
    
        d[parts[-1]] = value
        
    return parsed_dict

def normalize_phone(phone_number: str) -> str:
    if not phone_number:
        return ""
    return re.sub(r'\D', '', phone_number)


def is_working_hours():
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    # Пн-Пт = 0-4
    is_weekday = now.weekday() < 5
    # Часы с 9:00 до 18:00
    is_hour_ok = 9 <= now.hour < 18
    
    return is_weekday and is_hour_ok
