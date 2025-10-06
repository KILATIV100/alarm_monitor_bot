import telebot
import requests
import time
import logging
import os
from datetime import datetime, time as dt_time, timedelta
import pytz 

# --- КОНФІГУРАЦІЯ ПРОЄКТУ ---
# Змінні оточення (з Railway)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_DESTINATION = os.environ.get("CHANNEL_DESTINATION")
UKRAINE_ALARM_API_KEY = os.environ.get("UKRAINE_ALARM_API_KEY") # Ваш ключ

# Шляхи до файлів зображень.
ALARM_PHOTO_PATH = "airallert.png"
ALL_CLEAR_PHOTO_PATH = "airallert2.png"
SILENCE_MINUTE_PHOTO_PATH = "hvilina.png" 

# Інтервал перевірки 60 секунд (1 хвилина)
CHECK_INTERVAL = 60 

# Цільовий регіон (Моніторинг за ID регіону)
# ID Київської області = 11
TARGET_REGION_ID = "11"
TARGET_AREA_NAME = "Броварський район (Київська область)" 

# API UkraineAlarm (Використовуємо ваш ключ)
ALARM_API_URL = "https://api.ukrainealarm.com/api/v3/alerts/status" 

# Параметри для Хвилини мовчання
KYIV_TIMEZONE = pytz.timezone('Europe/Kyiv') 
SILENCE_TIME = dt_time(9, 0) 
# --- КІНЕЦЬ КОНФІГУРАЦІЇ ---

if not all([BOT_TOKEN, CHANNEL_DESTINATION, UKRAINE_ALARM_API_KEY]):
    raise ValueError("Одна або кілька критичних змінних оточення відсутні!")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ініціалізація бота
try:
    bot = telebot.TeleBot(BOT_TOKEN)
except Exception as e:
    logger.critical(f"Помилка ініціалізації бота: {e}")
    exit(1)

# Змінні стану
current_alarm_state = None 
last_silence_date = None 

# --- ФУНКЦІЇ API МОНІТОРИНГУ ---

def get_alarm_status():
    """Отримує поточний стан тривоги, використовуючи наданий API-ключ."""
    
    headers = {
        'Authorization': f'Bearer {UKRAINE_ALARM_API_KEY}',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        response = requests.get(ALARM_API_URL, headers=headers, timeout=10)
        response.raise_for_status() 
        data = response.json()
        
        # Логіка парсингу на основі наданої схеми: перевіряємо, чи є "activeAlerts"
        is_alarm = any(
            item.get('regionId') == TARGET_REGION_ID and item.get('activeAlerts')
            for item in data
        )
        
        return is_alarm
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ ПОМИЛКА API: {e}") 
        return None

# --- ФУНКЦІЇ ПУБЛІКАЦІЇ ---

def send_photo_message(bot, photo_path, caption, parse_mode='Markdown'):
    """Універсальна функція для надсилання фото з підписом."""
    try:
        if not os.path.exists(photo_path):
            logger.error(f"Файл зображення не знайдено: {photo_path}. Надсилаємо текст.")
            bot.send_message(CHANNEL_DESTINATION, caption, parse_mode=parse_mode)
            return True
            
        with open(photo_path, 'rb') as photo:
            bot.send_photo(
                CHANNEL_DESTINATION, 
                photo,
                caption=caption,
                parse_mode=parse_mode
            )
        logger.info(f"Успішно надіслано фото: {photo_path}")
        return True
        
    except telebot.apihelper.ApiTelegramException as e:
        if "Forbidden" in str(e):
            logger.critical("❌ ПОМИЛКА TELEGRAM API 403: БОТ НЕ Є АДМІНІСТРАТОРОМ КАНАЛУ! Виправте це вручну.")
        else:
            logger.error(f"Помилка Telegram API: {e}")
        return False
    except Exception as e:
        logger.error(f"Невідома помилка при надсиланні: {e}")
        return False

# --- ЛОГІКА ХВИЛИНИ МОВЧАННЯ (ОНОВЛЕНА) ---

def check_and_post_silence_minute():
    """Публікує Хвилину мовчання рівно о 9:00 за Києвом, лише один раз на день."""
    global last_silence_date
    
    now_kyiv = datetime.now(KYIV_TIMEZONE)
    today = now_kyiv.date()
    
    # Виводимо час у лог для діагностики
    if now_kyiv.hour == 9 and now_kyiv.minute <= 5: 
        logger.info(f"Kyiv Time Check: {now_kyiv.strftime('%H:%M:%S')}. Last posted: {last_silence_date}")
        
    if last_silence_date == today:
        return
    
    target_time = datetime.combine(today, SILENCE_TIME, KYIV_TIMEZONE)
    
    # Встановлюємо ширше вікно: з 9:00:00 до 9:05:00, щоб компенсувати затримку сервера
    window_start = target_time
    window_end = target_time + timedelta(minutes=5) # Вікно 5 хвилин
    
    if window_start <= now_kyiv < window_end:
        logger.warning(f"Настав час Хвилини мовчання. Київський час: {now_kyiv.strftime('%H:%M:%S')}. Публікація...")
        
        caption = "🇺🇦 **ХВИЛИНА МОВЧАННЯ** 🇺🇦\n\nЩоденно вшановуємо пам'ять українців, які загинули внаслідок збройної агресії Російської Федерації."
        
        success = send_photo_message(bot, SILENCE_MINUTE_PHOTO_PATH, caption)
        
        if success:
            last_silence_date = today 


def check_and_post_alarm(new_alarm_state):
    """Обробляє логіку зміни стану тривоги та публікує повідомлення."""
    global current_alarm_state

    if current_alarm_state is None:
        current_alarm_state = new_alarm_state
        initial_status = "ТРИВОГА" if current_alarm_state else "ВІДБІЙ"
        logger.warning(f"Первинний стан встановлено: {initial_status}")
        return
        
    if new_alarm_state != current_alarm_state:
        
        if new_alarm_state is True:
            logger.warning("ЗМІНА СТАНУ: ТРИВОГА!")
            caption = f"🚨 **УВАГА! ПОВІТРЯНА ТРИВОГА!** 🚨\n\n**{TARGET_AREA_NAME}**\n\n\n**Терміново прямуйте до найближчого укриття!**"
            send_photo_message(bot, ALARM_PHOTO_PATH, caption)
        else:
            logger.warning("ЗМІНА СТАНУ: ВІДБІЙ!")
            caption = f"✅ **ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ** ✅\n\n**{TARGET_AREA_NAME}**\n\n\nМожна повертатися з укриття. Зберігайте спокій."
            send_photo_message(bot, ALL_CLEAR_PHOTO_PATH, caption)
        
        current_alarm_state = new_alarm_state


# --- ГОЛОВНИЙ ЦИКЛ МОНІТОРИНГУ ---
def start_monitoring():
    """Запускає нескінченний цикл перевірки стану."""
    
    logger.warning("Бот моніторингу запущено...")
    
    while True:
        check_and_post_silence_minute()
        
        new_alarm_state = get_alarm_status()
        
        if new_alarm_state is not None:
            check_and_post_alarm(new_alarm_state)

        time.sleep(CHECK_INTERVAL)

# --- ЗАПУСК ---
if __name__ == "__main__":
    try:
        start_monitoring()
    except Exception as e:
        logger.critical(f"Критична помибка виконання: {e}")
