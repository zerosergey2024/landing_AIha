# AIha Landing

AIha Landing — Flask-приложение для AIha Ecosystem, AIha Studio и AIha Consulting.

Проект включает публичный landing, форму первичного AI-аудита, административную панель, AI-agent workflow и диагностическую цепочку Industrial AI.

## Основные модули

### Public

- `/` — AIha Ecosystem
- `/studio` — AIha Studio
- `/consulting` — AIha Consulting
- `/consulting/audit` — форма первичного AI-аудита
- `/consulting/thanks` — страница после отправки формы

### Admin

- `/admin/login`
- `/admin/leads`
- `/admin/leads/<id>`
- `/admin/leads/<id>/final`
- `/admin/tasks/<id>/run-ai`
- `/admin/tasks/<id>/update`

### Diagnostic Workflow

Проект поддерживает две цепочки обработки:

#### T-chain

Первичный AI-аудит:

```text
audit_form → lead → diagnostic_input_pack → T-001 → T-002 → T-003 → T-004/T-005
```

#### D-chain

Industrial AI diagnostic:

```text
Industrial AI Brief → D-001 → D-002 → D-003 → D-004
```

Этапы:

- `D-001` — оценка готовности кейса, данных и вложений
- `D-002` — MVP design
- `D-003` — внутреннее диагностическое решение
- `D-004` — клиентский отчёт и коммерческий следующий шаг

## Структура проекта

```text
aiha_landing/
├── app.py
├── db.py
├── requirements.txt
├── routes/
│   ├── public.py
│   ├── admin.py
│   ├── api.py
│   └── diagnostic.py
├── services/
│   ├── ai_agent.py
│   ├── prompt_loader.py
│   ├── leads.py
│   ├── consulting.py
│   ├── tasks.py
│   ├── intake_blocks.py
│   ├── diagnostics.py
│   ├── diagnostic_normalizer.py
│   ├── diagnostic_assessment.py
│   ├── mvp_design.py
│   ├── diagnostic_report.py
│   ├── commercial_proposal.py
│   ├── diagnostic_final_outputs.py
│   └── final_outputs.py
├── prompts/
│   ├── system/
│   └── agents/
├── templates/
│   └── consulting/
├── static/
│   ├── css/
│   ├── js/
│   └── img/
└── uploads/
    └── diagnostics/
```

## Локальный запуск

### 1. Создать виртуальное окружение

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Установить зависимости

```powershell
pip install -r requirements.txt
```

### 3. Создать `.env`

```env
FLASK_ENV=development
SECRET_KEY=change-me
OPENAI_API_KEY=change-me
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DATABASE_PATH=leads.db
UPLOAD_FOLDER=uploads/diagnostics
```

### 4. Инициализировать базу

```powershell
python init_db.py
```

### 5. Запустить приложение

```powershell
python app.py
```

Приложение будет доступно по адресу:

```text
http://127.0.0.1:5000
```

## Диагностические команды

Пример запуска D-chain для diagnostic run `7`:

```powershell
curl.exe -X POST -L http://127.0.0.1:5000/admin/diagnostic/7/run-d001
curl.exe -X POST -L http://127.0.0.1:5000/admin/diagnostic/7/run-d002
curl.exe -X POST -L http://127.0.0.1:5000/admin/diagnostic/7/run-d003
curl.exe -X POST -L http://127.0.0.1:5000/admin/diagnostic/7/run-d004
```

## Проверка кода

```powershell
python -m py_compile `
  routes\diagnostic.py `
  services\diagnostics.py `
  services\diagnostic_assessment.py `
  services\mvp_design.py `
  services\diagnostic_report.py `
  services\commercial_proposal.py `
  services\diagnostic_final_outputs.py
```

## Переменные и локальные файлы

В Git не должны попадать:

```text
.env
.venv/
leads.db
leads_archive.db
_backups/
backups/
exports/
uploads/diagnostics/*
__pycache__/
*.pyc
```

Файлы БД и загруженные документы используются локально. Для production-хостинга рекомендуется вынести БД и файлы во внешнее persistent storage.

## Deployment

Для production-запуска можно использовать `gunicorn`.

Минимальные файлы для хостинга:

```text
wsgi.py
Procfile
.env.example
requirements.txt
```

Пример `wsgi.py`:

```python
from app import app
```

Пример `Procfile`:

```text
web: gunicorn wsgi:app
```

SQLite подходит для MVP и тестового хостинга только при наличии persistent disk. Для стабильного production рекомендуется PostgreSQL и внешнее хранилище файлов.

## Репозиторий

```text
https://github.com/zerosergey2024/landing_AIha
```

