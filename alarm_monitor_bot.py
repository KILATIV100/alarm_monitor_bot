import telebot
import requests
import time
import logging
import os # Для перевірки наявності файлів

# --- КОНФІГУРАЦІЯ ПРОЄКТУ ---
# Ваш токен бота
BOT_TOKEN = "8457233690:AAHV-YlyvDVakTsDTpMTVNCsZgve8fbKbwU"

# Назва вашого каналу (починається з @) або його ID (-100...)
# ПРИМІТКА: Бот має бути адміністратором цього каналу!
CHANNEL_DESTINATION = "@YourChannelUsernameOrID" # <--- ОБОВ'ЯЗКОВО ЗАМІНІТЬ!

# Шляхи до файлів зображень. Вони мають лежати поруч із цим скриптом.
ALARM_PHOTO_PATH = "airallert.png"
ALL_CLEAR_PHOTO_PATH = "airallert2.png"

# Параметри моніторингу
CHECK_INTERVAL = 7 # Інтервал перевірки API у секундах (для "миттєвості")

# Цільовий регіон (Броварський район)
# ID 'Броварський район' у API карти повітряних тривог
TARGET_AREA_ID = "251675276"
TARGET_AREA_NAME = "Броварський район (Київська область)"

# URL API для отримання даних про тривоги
ALARM_API_URL = "https://api.ukrainealarm.com/api/v3/alerts"
# --- КІНЕЦЬ КОНФІГУРАЦІЇ ---

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ініціалізація бота
try:
    bot = telebot.TeleBot(BOT_TOKEN)
except Exception as e:
    logger.error(f"Помилка ініціалізації бота: {e}")
    exit(1)

# Змінна для зберігання поточного стану тривоги
current_alarm_state = None 

# --- ФУНКЦІЇ ---

def get_alarm_status():
    """Отримує поточний стан тривоги для цільового району з API."""
    try:
        # Встановлюємо таймаут для швидкого виявлення проблем
        response = requests.get(ALARM_API_URL, timeout=10)
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

def post_to_channel(is_alarm_start):
    """Відправляє фото та повідомлення у Телеграм-канал."""
    try:
        if is_alarm_start:
            photo_path = ALARM_PHOTO_PATH
            caption = f"🚨 **УВАГА! ПОВІТРЯНА ТРИВОГА!** 🚨\n\nРайон: **{TARGET_AREA_NAME}**\n\n\n**Терміново прямуйте до найближчого укриття!**"
        else:
            photo_path = ALL_CLEAR_PHOTO_PATH
            caption = f"✅ **ВІДБІЙ ПОВІТРЯНОЇ ТРИВОГИ** ✅\n\nРайон: **{TARGET_AREA_NAME}**\n\n\nМожна повертатися з укриття. Зберігайте спокій."
            
        if not os.path.exists(photo_path):
            logger.error(f"Файл зображення не знайдено за шляхом: {photo_path}")
            # Надсилаємо принаймні текстове повідомлення
            bot.send_message(CHANNEL_DESTINATION, caption, parse_mode='Markdown')
            return
            
        with open(photo_path, 'rb') as photo:
            bot.send_photo(
                CHANNEL_DESTINATION, 
                photo,
                caption=caption,
                parse_mode='Markdown'
            )
        
        status = "ТРИВОГА" if is_alarm_start else "ВІДБІЙ"
        logger.info(f"Надіслано повідомлення про {status}")
        
    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"Помилка Telegram API (перевірте CHANNEL_DESTINATION та права бота): {e}")
    except Exception as e:
        logger.error(f"Невідома помилка при надсиланні: {e}")

# --- ГОЛОВНИЙ ЦИКЛ МОНІТОРИНГУ ---
def start_monitoring():
    """Запускає нескінченний цикл перевірки стану тривоги."""
    global current_alarm_state
    
    logger.warning(f"Бот моніторингу запущено. Ціль: {TARGET_AREA_NAME}")
    
    while True:
        new_alarm_state = get_alarm_status()
        
        if new_alarm_state is not None:
            
            # 1. Ініціалізація стану при першому запуску
            if current_alarm_state is None:
                current_alarm_state = new_alarm_state
                initial_status = "ТРИВОГА" if current_alarm_state else "ВІДБІЙ"
                logger.warning(f"Первинний стан встановлено: {initial_status}")
                
            # 2. Перевірка на зміну стану
            elif new_alarm_state != current_alarm_state:
                
                # Початок / Відбій тривоги
                if new_alarm_state is True:
                    logger.warning("ЗМІНА СТАНУ: ТРИВОГА!")
                    post_to_channel(is_alarm_start=True)
                else:
                    logger.warning("ЗМІНА СТАНУ: ВІДБІЙ!")
                    post_to_channel(is_alarm_start=False)
                
                # Оновлення стану
                current_alarm_state = new_alarm_state
        
        time.sleep(CHECK_INTERVAL)

# --- ЗАПУСК ---
if __name__ == "__main__":
    try:
        start_monitoring()
    except KeyboardInterrupt:
        logger.warning("Бот зупинено користувачем.")
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}")
