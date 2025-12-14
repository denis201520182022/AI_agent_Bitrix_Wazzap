Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass





(venv) PS D:\мое\забота\Wazzup_Bitrix> python get_stages.py
Загружаем переменные окружения...
Отправляем запрос для получения стадий воронки ID 11...

--- ✅ СПИСОК СТАДИЙ ДЛЯ ВОРОНКИ 'ПОСТОЯННЫЕ' (ID: 11) ---
Название: Новый Действующий              | ID: C11:NEW
Название: Принят в работу                | ID: C11:UC_SHQYAA
Название: В ожидании                     | ID: C11:PREPARATION
Название: Сегодня касание                | ID: C11:PREPAYMENT_INVOICE
Название: Получить выписки по УС         | ID: C11:UC_HL5H2Z
Название: Новый должник/Лот. Возьмите в работу | ID: C11:UC_IH0M8P
Название: Дела по новой заявке в Банке   | ID: C11:UC_U0WT02
Название: Сбор документов                | ID: C11:EXECUTING
Название: Заведение сделки (коммуникация с бек-офисом) | ID: C11:FINAL_INVOICE
Название: Открыт счет/Успешная сделка    | ID: C11:UC_O7UAOV
Название: Номиналы                       | ID: C11:UC_W9JHF3
Название: Открыт счет/Успешная сделка    | ID: C11:WON
Название: Перебил конкурент              | ID: C11:LOSE
Название: Расторжение Договора           | ID: C11:APOLOGY
Название: Долгосрок (6 месяцев не подходит) | ID: C11:1
--------------------------------------------------
(venv) PS D:\мое\забота\Wazzup_Bitrix> 



Че перенести надо

.env
credentials.json
alembic.ini


sudo systemctl stop arb_bot.service
sudo systemctl restart arb_bot.service
sudo journalctl -u arb_bot.service -f