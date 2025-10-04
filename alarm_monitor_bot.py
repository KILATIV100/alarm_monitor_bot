import telebot
import requests
import time
import logging
import os
from datetime import datetime, time as dt_time, timedelta
import pytz # Для точної роботи з часовими поясами

# --- КОНФІГУРАЦІЯ ПРОЄКТУ ---
# Змінні читаються з Environment Variables на Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_DESTINATION = os.environ.get("CHANNEL_DESTINATION")

# Шляхи до файлів зображень. Вони мають лежати поруч із цим скриптом.
ALARM_PHOTO_PATH = "airallert.png"
ALL_CLEAR_PHOTO_PATH = "airallert2.png"
SILENCE_MINUTE_PHOTO_PATH = "hvilina.png" 

# Параметри моніторингу
CHECK_INTERVAL = 7 # Інтервал перевірки API у секундах

# Цільовий регіон (Броварський район)
TARGET_AREA_ID = "251675276"
TARGET_AREA_NAME = "Броварський район (Київська область)"
ALARM_API_URL = "https://api.ukrainealarm.com/api/v3/alerts"

# Параметри для Хвилини мовчання
KYIV_TIMEZONE = pytz.timezone('Europe/Kyiv') # Часовий пояс Києва
SILENCE_TIME = dt_time(9, 0) # Рівно 09:00:00 за Києвом
# --- КІНЕЦЬ КОНФІГУРАЦІЇ ---

# Перевірка наявності змінних оточення
if not BOT_TOKEN or not CHANNEL_DESTINATION:
    # Виводимо помилку, якщо змінні не встановлені (або не читаються)
    raise ValueError("BOT_TOKEN або CHANNEL_DESTINATION не знайдено у змінних оточення!")

# Налаштування логування
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
last_silence_date = None # Дата останньої публікації Хвилини мовчання

# --- ФУНКЦІЇ ---

def get_alarm_status():
    """Отримує поточний стан тривоги для цільового району, використовуючи User-Agent."""
    
    # Додаємо User-Agent для імітації запиту від браузера і обходу 403 Forbidden
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
    }
    
    try:
        response = requests.get(ALARM_API_URL, headers=headers, timeout=10)
        response.raise_for_status() 
        data = response.json()
        
        # Перевірка наявності активної тривоги для цільового ID
        is_alarm = any(
            item.get('alert_type') == 'air_raid' and 
            item.get('location_uid') == TARGET_AREA_ID
            for item in data
        )
        return is_alarm
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка при запиті до API: {e}") 
        return None

def send_photo_message(photo_path, caption, parse_mode='Markdown'):
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
        logger.error(f"Помилка Telegram API (перевірте CHANNEL_DESTINATION та права бота): {e}")
    except Exception as e:
        logger.error(f"Невідома помилка при надсиланні: {e}")
    return False

def check_and_post_silence_minute():
    """Публікує Хвилину мовчання рівно о 9:00 за Києвом, лише один раз на день."""
    global last_silence_date
    
    # Отримуємо поточний час у часовому поясі Києва
    now_kyiv = datetime.now(KYIV_TIMEZONE)
    today = now_kyiv.date()
    
    # Перевірка: чи сьогодні вже постили?
    if last_silence_date == today:
        return
    
    # Визначаємо точний момент 09:00:00 за Києвом
    target_time = datetime.combine(today, SILENCE_TIME, KYIV_TIMEZONE)
    
    # Створюємо невелике вікно перевірки для гарантованого спрацювання (09:00:00 + час одного циклу)
    window_start = target_time
    window_end = target_time + timedelta(seconds=CHECK_INTERVAL * 2) 
    
    if window_start <= now_kyiv < window_end:
        logger.warning(f"Настав час Хвилини мовчання. Київський час: {now_kyiv.strftime('%H:%M:%S')}. Публікація...")
        
        caption = "🇺🇦 **ХВИЛИНА МОВЧАННЯ** 🇺🇦\n\nЩоденно вшановуємо пам'ять українців, які загинули внаслідок збройної агресії Російської Федерації."
        
        success = send_photo_message(SILENCE_MINUTE_PHOTO_PATH, caption)
        
        if success:
            last_silence_date = today # Оновлюємо дату, щоб уникнути повторної публікації


def check_and_post_alarm(new_alarm_state):
    """Обробляє логіку зміни стану тривоги та публікує повідомлення."""
    global current_alarm_state

    # Ініціалізація стану при першому запуску
    if current_alarm_state is None:
        current_alarm_state = new_alarm_state
        initial_status = "ТРИВОГА" if current_alarm_state else "ВІДБІЙ"
        logger.warning(f"Первинний стан встановлено: {initial_status}")
        return
        
    # Публікація лише при зміні стану
    if new_alarm_state != current_alarm_state:
        
        if new_alarm_state is True:
            logger.warning("ЗМІНА СТАНУ: ТРИВОГА!")
            caption = f"🚨 **УВАГА! ПОВІТРЯНА ТРИВОГА!** 🚨\n\nРайон: **{TARGET_AREA_NAME}**\n\n\n**Терміново прямуйте до найближчого укриття!**"
            send_photo_message(ALARM_PHOTO_PATH, caption)
        else:
            logger.warning("ЗМІНА СТАНУ: ВІДБІЙ!")
            caption = f"✅ **ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ** ✅\n\nРайон: **{TARGET_AREA_NAME}**\n\n\nМожна повертатися з укриття. Зберігайте спокій."
            send_photo_message(ALL_CLEAR_PHOTO_PATH, caption)
        
        current_alarm_state = new_alarm_state


# --- ГОЛОВНИЙ ЦИКЛ МОНІТОРИНГУ ---
def start_monitoring():
    """Запускає нескінченний цикл перевірки стану."""
    
    logger.warning("Бот моніторингу запущено...")
    
    while True:
        # 1. Перевірка та публікація Хвилини мовчання
        check_and_post_silence_minute()
        
        # 2. Перевірка стану повітряної тривоги
        new_alarm_state = get_alarm_status()
        
        if new_alarm_state is not None:
            check_and_post_alarm(new_alarm_state)

        time.sleep(CHECK_INTERVAL)

# --- ЗАПУСК ---
if __name__ == "__main__":
    try:
        start_monitoring()
    except KeyboardInterrupt:
        logger.warning("Бот зупинено користувачем.")
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}")
