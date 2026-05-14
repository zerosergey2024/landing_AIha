def qualify_lead(text: str) -> dict:
    text_lower = text.lower()

    result = {
        "industry": "Общие бизнес-процессы",
        "process": "Не определено",
        "priority": "Средний",
        "ai_type": "AI automation",
        "effect": "Оптимизация процессов"
    }

    production_keywords = [
        "производство",
        "оборудование",
        "цех",
        "станок",
        "простои",
        "oee",
        "диспетчер"
    ]

    service_keywords = [
        "заказы",
        "ресторан",
        "доставка",
        "цветы",
        "меню",
        "клиенты"
    ]

    hr_keywords = [
        "hr",
        "персонал",
        "сотрудники",
        "найм",
        "резюме",
        "кандидаты"
    ]

    logistics_keywords = [
        "логистика",
        "маршрут",
        "склад",
        "доставка"
    ]

    if any(word in text_lower for word in production_keywords):
        result["industry"] = "Производство"
        result["process"] = "Контроль оборудования / производство"
        result["ai_type"] = "Мониторинг + аналитика"
        result["effect"] = "Снижение простоев и повышение производительности"

    elif any(word in text_lower for word in service_keywords):
        result["industry"] = "Сервис / обслуживание"
        result["process"] = "Обработка заказов"
        result["ai_type"] = "AI automation + AI assistant"
        result["effect"] = "Ускорение обработки заказов"

    elif any(word in text_lower for word in hr_keywords):
        result["industry"] = "HR"
        result["process"] = "Работа с персоналом"
        result["ai_type"] = "AI assistant + классификация"
        result["effect"] = "Снижение ручного труда HR"

    elif any(word in text_lower for word in logistics_keywords):
        result["industry"] = "Логистика"
        result["process"] = "Маршруты / поставки"
        result["ai_type"] = "AI monitoring + аналитика"
        result["effect"] = "Оптимизация поставок"

    if "простои" in text_lower:
        result["priority"] = "Высокий"

    return result