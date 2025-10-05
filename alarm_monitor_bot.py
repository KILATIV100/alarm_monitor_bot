import telebot
import requests
import time
import logging
import os
from datetime import datetime, time as dt_time, timedelta
import pytz 
from telethon import TelegramClient, events
import asyncio

# --- КОНФІГУРАЦІЯ ПРОЄКТУ ---
# Змінні оточення (для публікації)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_DESTINATION = os.environ.get("CHANNEL_DESTINATION")

# Змінні оточення (для моніторингу Telethon)
API_ID = os.environ.get("TELEGRAM_API_ID") 
API_HASH = os.environ.get("TELEGRAM_API_HASH")
TARGET_CHANNEL_USERNAME = os.environ.get("TARGET_CHANNEL_USERNAME") 

# Шляхи до файлів зображень.
ALARM_PHOTO_PATH = "airallert.png"
ALL_CLEAR_PHOTO_PATH = "airallert2.png"
SILENCE_MINUTE_PHOTO_PATH = "hvilina.png" 

# УВАГА: Інтервал перевірки 60 секунд (1 хвилина)
CHECK_INTERVAL = 60 

# Цільовий регіон (Ми моніторимо Київську область)
TARGET_REGION_ID_NEW = "Київська"
TARGET_AREA_NAME = "Броварський район (Київська область)" 

# НОВИЙ СТАБІЛЬНИЙ URL: Імовірний API з alarmmap.online
ALARM_API_URL = "https://map.ukrainealarm.com/api/v3/alerts" 

# Параметри для Хвилини мовчання
KYIV_TIMEZONE = pytz.timezone('Europe/Kyiv') 
SILENCE_TIME = dt_time(9, 0) 
# --- КІНЕЦЬ КОНФІГУРАЦІЇ ---

if not all([BOT_TOKEN, CHANNEL_DESTINATION, API_ID, API_HASH, TARGET_CHANNEL_USERNAME]):
    raise ValueError("Одна або кілька критичних змінних оточення Telegram відсутні!")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ініціалізація клієнта Telethon та бота pyTelegramBotAPI
client = TelegramClient('alarm_session', int(API_ID), API_HASH)
bot_publisher = telebot.TeleBot(BOT_TOKEN)

# Змінні стану
current_alarm_state = None 
last_silence_date = None 

# --- ФУНКЦІЇ API (Резервний моніторинг) ---

def get_alarm_status():
    """Отримує поточний стан тривоги для Київської області з API."""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
    }
    
    try:
        response = requests.get(ALARM_API_URL, headers=headers, timeout=10)
        response.raise_for_status() 
        data = response.json()
        
        # Логіка парсингу: шукаємо потрібну область
        is_alarm = any(
            item.get('regionName') == TARGET_REGION_ID_NEW and item.get('status') == 'alarm'
            for item in data.get('regions', [])
        )
        
        return is_alarm
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка при запиті до API ({ALARM_API_URL}): {e}") 
        return None

# --- ФУНКЦІЇ ПУБЛІКАЦІЇ ---

def send_photo_message(bot, photo_path, caption, parse_mode='Markdown'):
    """Універсальна функція для надсилання фото з підписом."""
    # ... (Ця функція залишається без змін) ...
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

# --- ЛОГІКА МОНІТОРИНГУ (TELETHON - Основна) ---

@client.on(events.NewMessage(chats=TARGET_CHANNEL_USERNAME))
async def handle_new_alarm_message(event):
    """Обробляє нові повідомлення з моніторингового каналу."""
    global current_alarm_state
    
    text = event.message.to_dict().get('message', '').lower()
    
    # Критерії пошуку в повідомленні
    is_target_region = any(kw.lower() in text for kw in KEYWORDS)
    
    if not is_target_region:
        return 
    
    is_alarm_start = "повітряна тривога" in text or "оголошена" in text or "оголошена по київській" in text
    is_all_clear = any(kw.lower() in text for kw in ALL_CLEAR_KEYWORDS)

    if is_alarm_start and current_alarm_state is not True:
        current_alarm_state = True
        logger.warning("ЗМІНА СТАНУ: ТРИВОГА (через Telegram-моніторинг)!")
        caption = f"🚨 **УВАГА! ПОВІТРЯНА ТРИВОГА!** 🚨\n\n**{TARGET_AREA_NAME}**\n\n\n**Терміново прямуйте до найближчого укриття!**"
        send_photo_message(bot_publisher, ALARM_PHOTO_PATH, caption)
        
    elif is_all_clear and current_alarm_state is not False:
        current_alarm_state = False
        logger.warning("ЗМІНА СТАНУ: ВІДБІЙ (через Telegram-моніторинг)!")
        caption = f"✅ **ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ** ✅\n\n**{TARGET_AREA_NAME}**\n\n\nМожна повертатися з укриття. Зберігайте спокій."
        send_photo_message(bot_publisher, ALL_CLEAR_PHOTO_PATH, caption)


# --- ЛОГІКА ТАЙМЕРА (ХВИЛИНА МОВЧАННЯ) ---

async def check_silence_minute_task():
    """Асинхронна задача для публікації Хвилини мовчання та API Fallback."""
    global last_silence_date
    global current_alarm_state
    
    while True:
        # 1. Логіка Хвилини мовчання
        now_kyiv = datetime.now(KYIV_TIMEZONE)
        today = now_kyiv.date()
        
        # ... (Логіка Хвилини мовчання залишається без змін) ...
        if last_silence_date != today:
            target_time = datetime.combine(today, SILENCE_TIME, KYIV_TIMEZONE)
            window_start = target_time
            window_end = target_time + timedelta(seconds=CHECK_INTERVAL * 2) 
            
            if window_start <= now_kyiv < window_end:
                logger.warning(f"Настав час Хвилини мовчання. Київський час: {now_kyiv.strftime('%H:%M:%S')}. Публікація...")
                caption = "🇺🇦 **ХВИЛИНА МОВЧАННЯ** 🇺🇦\n\nЩоденно вшановуємо пам'ять українців, які загинули внаслідок збройної агресії Російської Федерації."
                success = send_photo_message(bot_publisher, SILENCE_MINUTE_PHOTO_PATH, caption)
                if success:
                    last_silence_date = today 
        
        # 2. Логіка API Fallback (Запуск API, якщо Telethon з якихось причин пропустив повідомлення)
        new_alarm_state = get_alarm_status()
        
        if new_alarm_state is not None and new_alarm_state != current_alarm_state:
            
            if current_alarm_state is None:
                # Ініціалізація стану
                current_alarm_state = new_alarm_state
                logger.warning(f"Первинний стан встановлено API: {'Тривога' if current_alarm_state else 'Відбій'}")
            elif new_alarm_state is True:
                # Тривога
                current_alarm_state = True
                logger.warning("ЗМІНА СТАНУ: ТРИВОГА (через API Fallback)!")
                caption = f"🚨 **УВАГА! ПОВІТРЯНА ТРИВОГА!** 🚨\n\n**{TARGET_AREA_NAME}**\n\n\n**Терміново прямуйте до найближчого укриття!**"
                send_photo_message(bot_publisher, ALARM_PHOTO_PATH, caption)
            else:
                # Відбій
                current_alarm_state = False
                logger.warning("ЗМІНА СТАНУ: ВІДБІЙ (через API Fallback)!")
                caption = f"✅ **ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ** ✅\n\n**{TARGET_AREA_NAME}**\n\n\nМожна повертатися з укриття. Зберігайте спокій."
                send_photo_message(bot_publisher, ALL_CLEAR_PHOTO_PATH, caption)


        await asyncio.sleep(CHECK_INTERVAL)

# --- ЗАПУСК ---
async def main():
    logger.warning("Бот моніторингу запущено...")
    
    try:
        # Запуск Telethon клієнта
        await client.start()
    except Exception as e:
        logger.critical(f"Помилка запуску Telethon клієнта. Перевірте API_ID/HASH: {e}")
        return

    # Запуск задачі для Хвилини мовчання та API Fallback
    asyncio.create_task(check_silence_minute_task())
    
    # Запуск бота на постійну роботу
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        # Для запуску Telethon потрібен асинхронний цикл
        with client:
            client.loop.run_until_complete(main())
    except Exception as e:
        logger.critical(f"Критична помибка виконання: {e}")
