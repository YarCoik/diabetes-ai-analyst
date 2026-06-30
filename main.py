import os
import requests
import hashlib
from datetime import datetime, timedelta

# Получаем настройки из секретов GitHub
NIGHTSCOUT_URL = os.environ.get("NIGHTSCOUT_URL")
NIGHTSCOUT_API_SECRET = os.environ.get("NIGHTSCOUT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.environ.get("OPENAI_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL") or "llama-3.3-70b-versatile"

ns_url = NIGHTSCOUT_URL.rstrip('/')

# Хешируем API_SECRET в SHA-1 (это критически важно для авторизации в API Nightscout!)
hashed_secret = hashlib.sha1(NIGHTSCOUT_API_SECRET.encode('utf-8')).hexdigest()

# Настраиваем заголовки запроса
headers = {
    "api-secret": hashed_secret,
    "Accept": "application/json"
}

# 1. Запрос логов сахара (последние 288 точек = примерно 24 часа)
entries_url = f"{ns_url}/api/v1/entries/sgv.json?count=288"

try:
    r_entries = requests.get(entries_url, headers=headers)
    r_entries.raise_for_status()
    entries = r_entries.json()
except Exception as e:
    print(f"Ошибка при получении сахаров: {e}")
    entries = []

print(f"Успешно получено сахаров из Nightscout: {len(entries)} шт.")

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

print(f"Успешно получено процедур лечения из Nightscout: {len(treatments)} шт.")

# 3. Форматирование данных для ИИ
formatted_glucose = []
entries.reverse() # Разворачиваем, чтобы время шло от прошлого к настоящему

for idx, entry in enumerate(entries):
    # Берем каждую 3-ю запись (раз в 15 минут), чтобы не раздувать текст для ИИ
    if idx % 3 == 0:
        sgv_mgdl = entry.get("sgv", 0)
        sgv_mmol = round(sgv_mgdl / 18.0, 1)
        
        # Разбор времени по Unix-таймстампу
        epoch_ms = entry.get("date")
        if epoch_ms:
            try:
                # Переводим миллисекунды в дату UTC и добавляем 5 часов (для Екатеринбурга UTC+5)
                dt = datetime.utcfromtimestamp(epoch_ms / 1000.0) + timedelta(hours=5)
                time_str = dt.strftime("%H:%M")
                formatted_glucose.append(f"{time_str} - {sgv_mmol} ммоль/л")
            except Exception as e:
                print(f"Ошибка конвертации времени для точки сахара: {e}")

formatted_treatments = []
for t in treatments:
    t_type = t.get("eventType", "Запись")
    notes = t.get("notes", "")
    carbs = t.get("carbs", "")
    insulin = t.get("insulin", "")
    created_at = t.get("created_at")
    
    time_str = ""
    epoch_ms = t.get("date")
    if epoch_ms:
        try:
            dt = datetime.utcfromtimestamp(epoch_ms / 1000.0) + timedelta(hours=5)
            time_str = dt.strftime("%H:%M")
        except:
            pass

    details = []
    if carbs: details.append(f"Еда: {carbs}г углеводов")
    if insulin: details.append(f"Инсулин: {insulin} ед.")
    if notes: details.append(f"Заметка: {notes}")
    
    if details:
        formatted_treatments.append(f"{time_str} | {t_type}: {', '.join(details)}")

print(f"Отформатировано точек сахара для отправки в ИИ: {len(formatted_glucose)} шт.")
if formatted_glucose:
    print(f"Пример первой точки сахара: {formatted_glucose[0]}")

data_summary = (
    "### ЛОГ ГЛЮКОЗЫ ЗА СУТКИ (ммоль/л):\n" + "\n".join(formatted_glucose) + "\n\n"
    "### ВВЕДЕННЫЙ ИНСУЛИН И ЕДА ЗА СУТКИ:\n" + "\n".join(formatted_treatments)
)

# 4. Формируем инструкцию для нейросети
system_instruction = (
    "Ты — профессиональный врач-эндокринолог, эксперт по анализу данных помп и CGM. "
    "Проанализируй суточный график сахара пациента и его текущие дозировки. "
    "Напиши детальный отчет строго на русском языке в дружелюбном, но очень профессиональном стиле. "
    "ВАЖНОЕ ТРЕБОВАНИЕ К ФОРМАТИРОВАНИЮ ДЛЯ TELEGRAM:\n"
    "Текст отправляется в Telegram с parse_mode='HTML'. Тебе КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать маркдаун (решетки #, звездочки *).\n"
    "Используй только следующие HTML-теги для красивого оформления:\n"
    "- Для заголовков и разделов используй жирный шрифт: <b>📊 СТАТИСТИКА ДНЯ</b>, <b>🔍 АНАЛИЗ БАЗАЛА</b>, <b>💡 КОРРЕКТИРОВКА</b>.\n"
    "- Для выделения важных слов внутри абзацев используй: <b>жирный текст</b>.\n"
    "- Для примечаний используй курсив: <i>курсивный текст</i>.\n\n"
    "Для сравнительной таблицы базального инсулина ты ОБЯЗАН обернуть её в тег <pre>...</pre>. "
    "Это принудительно сделает шрифт моноширинным, и таблица будет идеально ровной на экранах любых телефонов.\n"
    "Выравнивай столбцы пробелами. Пример оформления таблицы:\n"
    "<pre>\n"
    "Время | Текущий | Новый | Изменение\n"
    "-----------------------------------\n"
    "00:00 |  0.550  | 0.550 | --\n"
    "04:00 |  0.900  | 0.950 | 📈 +0.05\n"
    "16:00 |  0.750  | 0.800 | 📈 +0.05\n"
    "</pre>\n\n"
    "В конце отчета обязательно добавь дисклеймер, что это лишь рекомендация ИИ и любые изменения нужно согласовать с лечащим врачом (оформи его курсивом <i>...</i>)."
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

# Отправляем запрос и детально выводим ошибки
try:
    r_groq = requests.post(groq_url, json=payload, headers=groq_headers)
    if r_groq.status_code != 200:
        analysis = f"Ошибка при вызове ИИ (Код {r_groq.status_code}):\n{r_groq.text}"
    else:
        analysis = r_groq.json()["choices"][0]["message"]["content"]
except Exception as e:
    analysis = f"Критическая ошибка скрипта: {e}"

# 6. Отправка отчета в Telegram
tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
tg_payload = {
    "chat_id": TELEGRAM_CHAT_ID,
    "text": analysis,
    "parse_mode": "HTML"  # МЕНЯЕМ НА HTML!
}
try:
    r_tg = requests.post(tg_url, json=tg_payload)
    r_tg.raise_for_status()
    print("Отчет успешно отправлен в Telegram!")
except Exception as e:
    print(f"Ошибка при отправке в Telegram: {e}")
