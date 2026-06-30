import os
import requests
from datetime import datetime, timedelta

# Получаем настройки из секретов GitHub
NIGHTSCOUT_URL = os.environ.get("NIGHTSCOUT_URL")
NIGHTSCOUT_API_SECRET = os.environ.get("NIGHTSCOUT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("OPENAI_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

ns_url = NIGHTSCOUT_URL.rstrip('/')

# 1. Запрос логов сахара (последние 288 точек = примерно 24 часа)
entries_url = f"{ns_url}/api/v1/entries/sgv.json?count=288"
headers = {
    "API-SECRET": NIGHTSCOUT_API_SECRET,
    "Accept": "application/json"
}

try:
    r_entries = requests.get(entries_url, headers=headers)
    r_entries.raise_for_status()
    entries = r_entries.json()
except Exception as e:
    print(f"Ошибка при получении сахаров: {e}")
    entries = []

# 2. Запрос введенного инсулина и еды за последние сутки
yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
treatments_url = f"{ns_url}/api/v1/treatments.json?find[created_at][$gte]={yesterday}"
try:
    r_treatments = requests.get(treatments_url, headers=headers)
    r_treatments.raise_for_status()
    treatments = r_treatments.json()
except Exception as e:
    print(f"Ошибка при получении доз и еды: {e}")
    treatments = []

# 3. Форматирование данных для ИИ
formatted_glucose = []
entries.reverse() # Разворачиваем, чтобы время шло от прошлого к настоящему

for idx, entry in enumerate(entries):
    # Берем каждую 3-ю запись (раз в 15 минут), чтобы не раздувать текст для ИИ
    if idx % 3 == 0:
        sgv_mgdl = entry.get("sgv", 0)
        # Переводим в ммоль/л
        sgv_mmol = round(sgv_mgdl / 18.0, 1)
        date_str = entry.get("dateString")
        if date_str:
            # Парсим время и оставляем только часы:минуты
            try:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                time_str = dt.strftime("%H:%M")
                formatted_glucose.append(f"{time_str} - {sgv_mmol} ммоль/л")
            except:
                pass

formatted_treatments = []
for t in treatments:
    t_type = t.get("eventType", "Запись")
    notes = t.get("notes", "")
    carbs = t.get("carbs", "")
    insulin = t.get("insulin", "")
    created_at = t.get("created_at")
    
    time_str = ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            time_str = dt.strftime("%H:%M")
        except:
            pass

    details = []
    if carbs: details.append(f"Еда: {carbs}г углеводов")
    if insulin: details.append(f"Инсулин: {insulin} ед.")
    if notes: details.append(f"Заметка: {notes}")
    
    if details:
        formatted_treatments.append(f"{time_str} | {t_type}: {', '.join(details)}")

# Собираем финальный текст для отправки нейросети
data_summary = (
    "### ЛОГ ГЛЮКОЗЫ ЗА СУТКИ (ммоль/л):\n" + "\n".join(formatted_glucose) + "\n\n"
    "### ВВЕДЕННЫЙ ИНСУЛИН И ЕДА ЗА СУТКИ:\n" + "\n".join(formatted_treatments)
)

# 4. Формируем инструкцию для нейросети
system_instruction = (
    "Ты — профессиональный врач-эндокринолог, эксперт по анализу данных помп и CGM. "
    "Проанализируй суточный график сахара пациента и его текущие дозировки. "
    "Напиши детальный отчет строго на русском языке в дружелюбном, но очень профессиональном стиле. "
    "Структура отчета должна быть следующей:\n"
    "1. Статистика дня (Средний сахар в ммоль/л, Мин, Макс, время в целевом диапазоне).\n"
    "2. Анализ базального инсулина по временным зонам (ночь, утро, день, вечер).\n"
    "3. Предложения по корректировке. Если требуется корректировка базала, "
    "в самом конце обязательно выведи сравнительную таблицу текущего профиля базала против предлагаемого нового, "
    "выделив изменения стрелочками 📈 или 📉 и указав причины.\n"
    "В конце отчета обязательно добавь дисклеймер, что это лишь рекомендация ИИ и любые изменения нужно согласовать с лечащим врачом."
)

# 5. Делаем запрос к API Groq
groq_url = "https://api.groq.com/openai/v1/chat/completions"
groq_headers = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json"
}
payload = {
    "model": LLM_MODEL,
    "messages": [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Вот мои данные сахара и инсулина за последние 24 часа:\n{data_summary}"}
    ],
    "temperature": 0.3
}

try:
    r_groq = requests.post(groq_url, json=payload, headers=groq_headers)
    r_groq.raise_for_status()
    analysis = r_groq.json()["choices"][0]["message"]["content"]
except Exception as e:
    analysis = f"Ошибка при вызове ИИ: {e}"

# 6. Отправка отчета в Telegram
tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
tg_payload = {
    "chat_id": TELEGRAM_CHAT_ID,
    "text": analysis,
    "parse_mode": "Markdown"
}
try:
    r_tg = requests.post(tg_url, json=tg_payload)
    r_tg.raise_for_status()
    print("Отчет успешно отправлен в Telegram!")
except Exception as e:
    print(f"Ошибка при отправке в Telegram: {e}")
