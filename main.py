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

# Хешируем API_SECRET в SHA-1 для авторизации
hashed_secret = hashlib.sha1(NIGHTSCOUT_API_SECRET.encode('utf-8')).hexdigest()

# Настраиваем заголовки запроса
headers = {
    "api-secret": hashed_secret,
    "Accept": "application/json"
}

# 1. Запрос активного профиля базала из Найтскаута (то, что мы заполняли на сайте)
profile_url = f"{ns_url}/api/v1/profile.json"
active_basal_rates = []

try:
    r_profile = requests.get(profile_url, headers=headers)
    r_profile.raise_for_status()
    profiles = r_profile.json()
    if profiles:
        latest_profile = profiles[0]
        default_profile_name = latest_profile.get("defaultProfile", "Default")
        store = latest_profile.get("store", {})
        profile_data = store.get(default_profile_name, {})
        basal_list = profile_data.get("basal", [])
        for b in basal_list:
            active_basal_rates.append(f"{b.get('time')} | {b.get('value')} ед/ч")
except Exception as e:
    print(f"Ошибка при получении профиля базала: {e}")

print(f"Успешно получено точек базала из твоего профиля: {len(active_basal_rates)} шт.")

# 2. Запрос логов сахара (последние 288 точек = примерно 24 часа)
entries_url = f"{ns_url}/api/v1/entries/sgv.json?count=288"
try:
    r_entries = requests.get(entries_url, headers=headers)
    r_entries.raise_for_status()
    entries = r_entries.json()
except Exception as e:
    print(f"Ошибка при получении сахаров: {e}")
    entries = []

print(f"Успешно получено сахаров из Nightscout: {len(entries)} шт.")

# 3. Запрос введенного инсулина, еды и заметок за последние сутки
yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
treatments_url = f"{ns_url}/api/v1/treatments.json?find[created_at][$gte]={yesterday}"
try:
    r_treatments = requests.get(treatments_url, headers=headers)
    r_treatments.raise_for_status()
    treatments = r_treatments.json()
except Exception as e:
    print(f"Ошибка при получении доз и еды: {e}")
    treatments = []

print(f"Успешно получено процедур лечения/заметок: {len(treatments)} шт.")

# 4. Форматирование данных сахара для ИИ
formatted_glucose = []
entries.reverse()

for idx, entry in enumerate(entries):
    if idx % 3 == 0:
        sgv_mgdl = entry.get("sgv", 0)
        sgv_mmol = round(sgv_mgdl / 18.0, 1)
        
        epoch_ms = entry.get("date")
        if epoch_ms:
            try:
                dt = datetime.utcfromtimestamp(epoch_ms / 1000.0) + timedelta(hours=5)
                time_str = dt.strftime("%H:%M")
                formatted_glucose.append(f"{time_str} - {sgv_mmol} ммоль/л")
            except Exception as e:
                print(f"Ошибка конвертации времени: {e}")

# 5. Форматирование уколов, еды и текстовых заметок
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

# Собираем данные в единый пакет
basal_profile_text = "\n".join(active_basal_rates) if active_basal_rates else "Базальный профиль в Найтскауте не заполнен."
glucose_text = "\n".join(formatted_glucose) if formatted_glucose else "Нет данных сахара."
treatments_text = "\n".join(formatted_treatments) if formatted_treatments else "Нет записей о еде/инсулине/заметках за сутки."

data_summary = (
    "### ТЕКУЩИЙ АКТИВНЫЙ БАЗАЛЬНЫЙ ПРОФИЛЬ ПАЦИЕНТА НА ПОМПЕ:\n" + basal_profile_text + "\n\n"
    "### ЛОГ ГЛЮКОЗЫ ЗА СУТКИ (ммоль/л):\n" + glucose_text + "\n\n"
    "### ВВЕДЕННЫЙ ИНСУЛИН, ЕДА И ЗАМЕТКИ ЗА СУТКИ:\n" + treatments_text
)

# 6. Формируем инструкцию для нейросети
system_instruction = (
    "Ты — профессиональный врач-эндокринолог, эксперт по анализу данных помп и CGM. "
    "Проанализируй суточный график сахара пациента, его текстовые заметки о событиях (спорт, стресс, еда) и его текущий профиль базала. "
    "Напиши детальный отчет строго на русском языке в дружелюбном, но очень профессиональном стиле. "
    "ВАЖНОЕ ТРЕБОВАНИЕ К ФОРМАТИРОВАНИЮ ДЛЯ TELEGRAM:\n"
    "Текст отправляется в Telegram с parse_mode='HTML'. Тебе КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать маркдаун (решетки #, звездочки *).\n"
    "Используй только следующие HTML-теги для красивого оформления:\n"
    "- Для заголовков и разделов используй жирный шрифт: <b>📊 СТАТИСТИКА ДНЯ</b>, <b>🔍 АНАЛИЗ БАЗАЛА</b>, <b>💡 КОРРЕКТИРОВКА</b>.\n"
    "- Для выделения важных слов внутри абзацев используй: <b>жирный текст</b>.\n"
    "- Для примечаний используй курсив: <i>курсивный текст</i>.\n\n"
    "Для сравнительной таблицы базального инсулина ты ОБЯЗАН обернуть её в тег <pre>...</pre>. "
    "Это принудительно сделает шрифт моноширинным, и таблица будет идеально ровной на экранах любых телефонов.\n"
    "В таблице должно быть строго три колонки: Время, Текущий и Новый. Колонку 'Изменение' выводить НЕ нужно.\n"
    "Выравнивай столбцы пробелами. В таблице сравнивай именно предоставленный ТЕКУЩИЙ профиль базала с твоим новым ПРЕДЛАГАЕМЫМ.\n"
    "Пример оформления таблицы базала:\n"
    "<pre>\n"
    "Время | Текущий | Новый\n"
    "-----------------------\n"
    "00:00 |  0.550  | 0.550\n"
    "04:00 |  0.900  | 0.950\n"
    "16:00 |  0.750  | 0.800\n"
    "</pre>\n\n"
    "В конце отчета обязательно добавь дисклеймер, что это лишь рекомендация ИИ и любые изменения нужно согласовать с лечащим врачом (оформи его курсивом <i>...</i>)."
)

# 7. Делаем запрос к API Groq
groq_url = "https://api.groq.com/openai/v1/chat/completions"
groq_headers = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json"
}
payload = {
    "model": LLM_MODEL,
    "messages": [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Вот мои данные сахара, уколов и текущего базального профиля за последние 24 часа:\n{data_summary}"}
    ],
    "temperature": 0.3
}

try:
    r_groq = requests.post(groq_url, json=payload, headers=groq_headers)
    if r_groq.status_code != 200:
        analysis = f"Ошибка при вызове ИИ (Код {r_groq.status_code}):\n{r_groq.text}"
    else:
        analysis = r_groq.json()["choices"][0]["message"]["content"]
except Exception as e:
    analysis = f"Критическая ошибка скрипта: {e}"

# 8. Отправка отчета в Telegram
tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
tg_payload = {
    "chat_id": TELEGRAM_CHAT_ID,
    "text": analysis,
    "parse_mode": "HTML"
}
try:
    r_tg = requests.post(tg_url, json=tg_payload)
    r_tg.raise_for_status()
    print("Отчет успешно отправлен в Telegram!")
except Exception as e:
    print(f"Ошибка при отправке в Telegram: {e}")
