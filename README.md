# AIha Landing & AI Intake System

AIha — Flask-проект для AI-студии, которая внедряет AI в реальные бизнес-процессы: производство, заказы, сервис, HR, логистику, документооборот и внутренние операции компаний.

## Возможности

- Лендинг AIha
- Форма заявки
- Виджет обратного звонка
- Telegram-бот для приема заявок
- Интеграция с локальной Phi-4 Mini через Ollama
- Structured extraction заявок
- SQLite как основная локальная база
- AI-квалификация лидов
- Manager UI
- Lead Pipeline
- Карточка заявки
- Защита admin-панели
- Экспорт заявок в XLSX
- Backup базы
- Архивация завершенных заявок

## Архитектура

```text
Landing / Callback / Telegram Bot
↓
Flask
↓
Phi-4 Mini / Qualification
↓
SQLite
↓
Manager UI
↓
XLSX Export

Локальный запуск
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py

Открыть: http://127.0.0.1:5000
Admin: http://127.0.0.1:5000/admin/leads

Telegram-бот:  python telegram_bot.py

Phi-4 Mini
Требуется установленный Ollama и модель: ollama pull phi4-mini

Переменные окружения
Создать .env: SECRET_KEY=your_secret_key
              TELEGRAM_BOT_TOKEN=your_bot_token
              TELEGRAM_CHAT_ID=your_chat_id
              ADMIN_USERNAME=admin
              ADMIN_PASSWORD=strong_password

Экспорт в Excel - python export_leads_xlsx.py
Файл создается в: exports/leads_export.xlsx

Backup базы - python backup_db.py
Архивация заявок - python archive_leads.py
Архивируются заявки со статусами: Завершено, Архив

Безопасность данных
AIha проектирует решения с приоритетом локального хранения и контролируемой обработки данных.
Персональные данные, базы заявок, архивы, выгрузки и .env не должны попадать в публичный репозиторий.

