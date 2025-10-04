import telebot
import requests
import time
import logging
import os
from datetime import datetime, time as dt_time, timedelta
import pytz 
from bs4 import BeautifulSoup # Новий імпорт для парсингу HTML

# --- КОНФІГУРАЦІЯ ПРОЄКТУ ---
# Змінні оточення
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_DESTINATION = os.environ.get("CHANNEL_DESTINATION")

# Шляхи до файлів зображень.
ALARM_PHOTO_PATH = "airallert.png"
ALL_CLEAR_PHOTO_PATH = "airallert2.png"
SILENCE_MINUTE_PHOTO_PATH = "hvilina.png" 

# УВАГА: Інтервал перевірки 60 секунд (1 хвилина)
CHECK_INTERVAL = 60 

# Цільовий регіон (Ми моніторимо Київську область)
TARGET_REGION_NAME_HTML = "Київська" 
TARGET_AREA_NAME = "Броварський район (Київська область)" 

# ФІНАЛЬНИЙ URL: Веб-сторінка для прямого парсингу HTML
ALARM_PAGE_URL = "https://map.ukrainealarm.com/" 

# Параметри для Хвилини мовчання
KYIV_TIMEZONE = pytz.timezone('Europe/Kyiv') 
SILENCE_TIME = dt_time(9, 0) 
# --- КІНЕЦЬ КОНФІГУРАЦІЇ ---

# Перевірка наявності змінних оточення
if not BOT_TOKEN or not CHANNEL_DESTINATION:
    raise ValueError("BOT_TOKEN або CHANNEL_DESTINATION не знайдено у змінних оточення!")

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

# --- ФУНКЦІЇ ---

def get_alarm_status():
    """Парсить HTML сторінки для визначення статусу тривоги в Київській області."""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
    }
    
    try:
        response = requests.get(ALARM_PAGE_URL, headers=headers, timeout=15)
        response.raise_for_status() 
        
        # Використовуємо BeautifulSoup для парсингу HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Шукаємо скрипт з даними тривоги
        alarm_data_script = soup.find('script', id='alarm-status-data')
        
        if alarm_data_script:
            # Дані зазвичай зберігаються у JSON форматі в текстовому вмісті тега
            import json
            data = json.loads(alarm_data_script.string)
            
            # Логіка парсингу: перевіряємо, чи є Київська область серед активних
            is_alarm = any(
                item.get('region') == TARGET_REGION_NAME_HTML and item.get('status') == 'alarm'
                for item in data.get('regions', [])
            )
            
            return is_alarm
        
        # Якщо скрипт не знайдено, пробуємо альтернативний метод (пошук за класом)
        # На сторінці тривоги області зазвичай позначаються червоним
        
        # Пошук елемента, що вказує на тривогу в Київській області
        region_div = soup.find('div', string=TARGET_REGION_NAME_HTML)
        
        if region_div and 'alarm-active' in region_div.get('class', []):
            return True
        
        # Якщо нічого не знайдено, припускаємо, що тривоги немає
        return False
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка при запиті (Web Scraping): {e}") 
        return None
    except Exception as e:
        logger.error(f"Помилка парсингу HTML: {e}")
        return None


# ... (Інші функції залишаються без змін) ...
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
        # Критична помилка Telegram API
        if "Forbidden" in str(e):
            logger.critical("❌ ПОМИЛКА TELEGRAM API 403: БОТ НЕ Є АДМІНІСТРАТОРОМ КАНАЛУ! Виправте це вручну.")
        else:
            logger.error(f"Помилка Telegram API: {e}")
        return False
    except Exception as e:
        logger.error(f"Невідома помилка при надсиланні: {e}")
        return False

def check_and_post_silence_minute():
    """Публікує Хвилину мовчання рівно о 9:00 за Києвом, лише один раз на день."""
    global last_silence_date
    
    now_kyiv = datetime.now(KYIV_TIMEZONE)
    today = now_kyiv.date()
    
    if last_silence_date == today:
        return
    
    target_time = datetime.combine(today, dt_time(9, 0), KYIV_TIMEZONE)
    window_start = target_time
    window_end = target_time + timedelta(seconds=CHECK_INTERVAL * 2) 
    
    if window_start <= now_kyiv < window_end:
        logger.warning(f"Настав час Хвилини мовчання. Київський час: {now_kyiv.strftime('%H:%M:%S')}. Публікація...")
        
        caption = "🇺🇦 **ХВИЛИНА МОВЧАННЯ** 🇺🇦\n\nЩоденно вшановуємо пам'ять українців, які загинули внаслідок збройної агресії Російської Федерації."
        
        success = send_photo_message(SILENCE_MINUTE_PHOTO_PATH, caption)
        
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
            send_photo_message(ALARM_PHOTO_PATH, caption)
        else:
            logger.warning("ЗМІНА СТАНУ: ВІДБІЙ!")
            caption = f"✅ **ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ** ✅\n\n**{TARGET_AREA_NAME}**\n\n\nМожна повертатися з укриття. Зберігайте спокій."
            send_photo_message(ALL_CLEAR_PHOTO_PATH, caption)
        
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
    except KeyboardInterrupt:
        logger.warning("Бот зупинено користувачем.")
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}")
