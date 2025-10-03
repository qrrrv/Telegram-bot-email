import re
import time
import random
import string
import hashlib
import json 
import asyncio 
import requests
from bs4 import BeautifulSoup
from pyrogram.enums import ParseMode, ChatType
from pyrogram import Client, filters

from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# Убедитесь, что ваш config.py находится в той же папке и содержит эти переменные
from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN
)

bot = Client(
    "bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=1000,
    parse_mode=ParseMode.MARKDOWN
)

# --- Блок статистики и данных ---
STATS_FILE = 'stats.json'
STATS = {
    'total_users': 0,
    'total_emails_generated': 0,
    'total_messages_checked': 0,
    'total_new_mail_notifications': 0,
}

# Словарь для хранения токена и ID чата пользователя для мониторинга
# Формат: {token: user_id}
MONITORED_TOKENS = {}

# Временное хранение данных пользователя (используется для мониторинга)
user_data = {} 
token_map = {} # short_id: token
user_tokens = {} # user_id: token
MAX_MESSAGE_LENGTH = 4000

BASE_URL = "https://api.mail.tm"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json"
}

def load_stats():
    """Загружает статистику из JSON файла."""
    global STATS
    try:
        with open(STATS_FILE, 'r') as f:
            STATS.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass # Файл не найден или пуст, используем значения по умолчанию

def save_stats():
    """Сохраняет статистику в JSON файл."""
    with open(STATS_FILE, 'w') as f:
        json.dump(STATS, f, indent=4)
# --- Конец блока статистики ---

def short_id_generator(email):
    unique_string = email + str(time.time())
    return hashlib.md5(unique_string.encode()).hexdigest()[:10]

def generate_random_username(length=8):
    return ''.join(random.choice(string.ascii_lowercase) for i in range(length))

def generate_random_password(length=12):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for i in range(length))

def get_domain():
    response = requests.get(f"{BASE_URL}/domains", headers=HEADERS)
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]['domain']
    elif 'hydra:member' in data and data['hydra:member']:
        return data['hydra:member'][0]['domain']
    return None

def create_account(email, password):
    data = {
        "address": email,
        "password": password
    }
    response = requests.post(f"{BASE_URL}/accounts", headers=HEADERS, json=data)
    if response.status_code in [200, 201]:
        return response.json()
    else:
        return None

def get_token(email, password):
    data = {
        "address": email,
        "password": password
    }
    response = requests.post(f"{BASE_URL}/token", headers=HEADERS, json=data)
    if response.status_code == 200:
        return response.json().get('token')
    else:
        return None

def get_text_from_html(html_content_list):
    html_content = ''.join(html_content_list)
    soup = BeautifulSoup(html_content, 'html.parser')

    # Убираем скрипты и стили
    for script_or_style in soup(['script', 'style']):
        script_or_style.decompose()

    # Заменяем ссылки для лучшего отображения в Markdown
    for a_tag in soup.find_all('a', href=True):
        url = a_tag['href']
        # Используем Markdown-формат для ссылок
        new_content = f"[{a_tag.text or url}]({url})" 
        a_tag.replace_with(new_content)

    text_content = soup.get_text('\n', strip=True) # Получаем текст с переносами строк
    # Удаляем множественные пустые строки
    cleaned_content = re.sub(r'\n\s*\n', '\n\n', text_content).strip()
    return cleaned_content

def list_messages(token):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    # Получаем только 10 последних сообщений
    response = requests.get(f"{BASE_URL}/messages?limit=10", headers=headers) 
    data = response.json()
    if isinstance(data, list):
        return data
    elif 'hydra:member' in data:
        return data['hydra:member']
    else:
        return []

# --- Фоновая задача для мониторинга новых писем ---
async def mail_monitor():
    """Фоновая задача, которая проверяет новые письма для активных токенов."""
    while True:
        await asyncio.sleep(30) # Проверяем каждые 30 секунд

        tokens_to_remove = []
        for token, user_id in list(MONITORED_TOKENS.items()):
            try:
                messages = list_messages(token)
                
                # Получаем предыдущий список сообщений или пустой список
                last_known_messages = user_data.get(user_id, {}).get('last_messages', [])
                
                current_message_ids = {msg['id'] for msg in messages}
                last_known_ids = {msg['id'] for msg in last_known_messages}
                
                new_message_ids = current_message_ids - last_known_ids
                
                if new_message_ids:
                    
                    new_messages = [msg for msg in messages if msg['id'] in new_message_ids]
                    
                    output = "**🔔 НОВОЕ ПИСЬМО! 🔔**\n━━━━━━━━━━━━━━━━━━\n"
                    
                    for idx, msg in enumerate(new_messages):
                        output += f"📧 От: `{msg['from']['address']}`\n"
                        output += f"📌 Тема: `{msg['subject']}`\n"
                        output += f"🕒 Время: `{msg['sentDate'][:16].replace('T', ' ')}`\n"
                        output += "\n"

                    # Обновляем список последних сообщений
                    user_data.setdefault(user_id, {})['last_messages'] = messages

                    # Генерируем клавиатуру для быстрого перехода к письмам
                    buttons = []
                    for msg in new_messages:
                        buttons.append(InlineKeyboardButton(f"Читать", callback_data=f"read_{msg['id']}"))
                    
                    # Отправляем уведомление
                    await bot.send_message(
                        user_id, 
                        output, 
                        reply_markup=InlineKeyboardMarkup([buttons] if buttons else [])
                    )
                    
                    # Обновляем статистику
                    STATS['total_new_mail_notifications'] += len(new_messages)
                    save_stats()

                elif 'last_messages' not in user_data.setdefault(user_id, {}):
                     # Если это первая проверка, просто сохраняем сообщения для сравнения в следующий раз
                    user_data.setdefault(user_id, {})['last_messages'] = messages

            except requests.exceptions.RequestException:
                # Токен невалиден или ошибка сети, удаляем из мониторинга
                tokens_to_remove.append(token)
            except Exception:
                # Неизвестная ошибка
                pass
        
        # Удаляем невалидные токены из мониторинга
        for token in tokens_to_remove:
            if token in MONITORED_TOKENS:
                del MONITORED_TOKENS[token]
# --- Конец фоновой задачи ---

@bot.on_message(filters.command('start'))
async def start(client, message):
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("**Пожалуйста, используйте этого бота только в личных чатах.**")
        return
        
    global STATS
    # Обновляем статистику пользователей
    if message.from_user.id not in user_data:
        STATS['total_users'] += 1
        save_stats()
        user_data[message.from_user.id] = {} # Инициализируем данные пользователя

    welcome_message = (
        "**Добро пожаловать в нашего бота временной почты!** 🎉\n\n"
        "Вы можете использовать следующие команды для управления временными адресами электронной почты:\n\n"
        "➢ `/tmail` - Сгенерировать случайный адрес электронной почты с паролем.\n"
        "➢ `/tmail [имя пользователя]:[пароль]` - Сгенерировать конкретный адрес электронной почты с паролем.\n"
        "➢ `/cmail [токен почты]` - Проверить 10 последних писем, используя ваш токен почты.\n"
        "➢ `/stats` - Посмотреть общую статистику бота.\n\n"
        "✨ **Примечание:** При генерации адреса вы получите **токен**. Он позволяет проверять письма. Сохраните его! 🛡️"
    )
    await message.reply(welcome_message)

@bot.on_message(filters.command('tmail'))
async def generate_mail(client, message):
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("**Пожалуйста, используйте этого бота только в личных чатах.**")
        return

    loading_msg = await message.reply("**Генерация вашего временного адреса электронной почты...**")

    args_text = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    args = args_text.split()
    
    # Парсинг аргументов
    if len(args) == 1 and ':' in args[0]:
        parts = args[0].split(':', 1)
        username = parts[0]
        password = parts[1]
    else:
        username = generate_random_username()
        password = generate_random_password()

    domain = get_domain()
    if not domain:
        await message.reply("**Не удалось получить домен, попробуйте снова**")
        # ИСПРАВЛЕНО: используем .id
        await bot.delete_messages(message.chat.id, [loading_msg.id])
        return

    email = f"{username}@{domain}"
    account = create_account(email, password)
    if not account:
        await message.reply("**Имя пользователя уже занято или произошла ошибка. Выберите другое.**")
        # ИСПРАВЛЕНО: используем .id
        await bot.delete_messages(message.chat.id, [loading_msg.id])
        return

    time.sleep(2)

    token = get_token(email, password)
    if not token:
        await message.reply("**Не удалось получить токен.**")
        # ИСПРАВЛЕНО: используем .id
        await bot.delete_messages(message.chat.id, [loading_msg.id])
        return

    short_id = short_id_generator(email)
    token_map[short_id] = token
    
    # Добавляем токен в мониторинг
    MONITORED_TOKENS[token] = message.from_user.id 
    
    # Обновляем статистику
    STATS['total_emails_generated'] += 1
    save_stats()

    output_message = (
        "**📧 Детали Smart-Email 📧**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"**📧 Email:** `{email}`\n"
        f"**🔑 Пароль:** `{password}`\n"
        f"**🔒 Токен:** `{token}`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "**Примечание: Сохраните токен для доступа к почте. Мониторинг новых писем включен!**"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Проверить письма", callback_data=f"check_{short_id}")]])

    await message.reply(output_message, reply_markup=keyboard)
    # ИСПРАВЛЕНО: используем .id
    await bot.delete_messages(message.chat.id, [loading_msg.id]) 

@bot.on_callback_query(filters.regex(r'^check_'))
async def check_mail(client, callback_query):
    short_id = callback_query.data.split('_')[1]
    token = token_map.get(short_id)
    
    if not token:
        await callback_query.answer("**Сессия истекла, пожалуйста, используйте /cmail с вашим токеном.**", show_alert=True)
        return

    # Запоминаем токен и добавляем в мониторинг (если еще не там)
    user_tokens[callback_query.from_user.id] = token
    MONITORED_TOKENS[token] = callback_query.from_user.id
    
    loading_msg = await callback_query.message.reply("**⏳ Проверка писем... Пожалуйста, подождите.**")

    messages = list_messages(token)
    if not messages:
        await callback_query.answer("Писем не получено ❌", show_alert=True)
        # ИСПРАВЛЕНО: используем .id
        await bot.delete_messages(callback_query.message.chat.id, [loading_msg.id])
        return

    # Обновляем статистику
    STATS['total_messages_checked'] += len(messages)
    save_stats()
    
    # Обновляем список последних сообщений для мониторинга
    user_data.setdefault(callback_query.from_user.id, {})['last_messages'] = messages

    output = "**📧 Ваши сообщения Smart-Mail 📧**\n"
    output += "**━━━━━━━━━━━━━━━━━━**\n"
    
    buttons = []
    for idx, msg in enumerate(messages[:10], 1):
        output += f"{idx}. От: `{msg['from']['address']}` - Тема: `{msg['subject']}`\n"
        button = InlineKeyboardButton(f"{idx}", callback_data=f"read_{msg['id']}")
        buttons.append(button)
    
    keyboard = []
    for i in range(0, len(buttons), 5):
        keyboard.append(buttons[i:i+5])

    await callback_query.message.reply(output, reply_markup=InlineKeyboardMarkup(keyboard))
    # ИСПРАВЛЕНО: используем .id
    await bot.delete_messages(callback_query.message.chat.id, [loading_msg.id])

@bot.on_callback_query(filters.regex(r"^close_message"))
async def close_message(client, callback_query):
    await callback_query.message.delete()

@bot.on_callback_query(filters.regex(r"^read_"))
async def read_message(client, callback_query):
    message_id = callback_query.data.split('_')[1]
    token = user_tokens.get(callback_query.from_user.id)

    if not token:
        await callback_query.answer("**Токен не найден. Пожалуйста, используйте /cmail с вашим токеном снова.**", show_alert=True)
        return

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    response = requests.get(f"{BASE_URL}/messages/{message_id}", headers=headers)

    if response.status_code == 200:
        details = response.json()

        if 'html' in details and details['html']:
            message_text = get_text_from_html(details['html'])
        elif 'text' in details and details['text']:
            message_text = details['text']
        else:
            message_text = "Контент недоступен."
        
        # Обрезаем сообщение, если оно слишком длинное
        if len(message_text) > MAX_MESSAGE_LENGTH:
            message_text = message_text[:MAX_MESSAGE_LENGTH - 100] + "\n\n... [сообщение обрезано]"

        output = f"**От:** `{details['from']['address']}`\n**Тема:** `{details['subject']}`\n━━━━━━━━━━━━━━━━━━\n{message_text}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Закрыть", callback_data="close_message")]
        ])

        await callback_query.message.reply(output, disable_web_page_preview=True, reply_markup=keyboard)

    else:
        await callback_query.answer("**Ошибка при получении деталей сообщения.**", show_alert=True)

@bot.on_message(filters.command('cmail'))
async def manual_check_mail(client, message):
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("**Пожалуйста, используйте этого бота только в личных чатах.**")
        return

    loading_msg = await message.reply("**⏳ Проверка писем... Пожалуйста, подождите.**")

    token = message.text.split(maxsplit=1)[1].strip() if len(message.text.split()) > 1 else ""
    if not token:
        await message.reply("**Пожалуйста, укажите токен после команды /cmail.**")
        # ИСПРАВЛЕНО: используем .id
        await bot.delete_messages(message.chat.id, [loading_msg.id])
        return

    # Запоминаем токен и добавляем в мониторинг
    user_tokens[message.from_user.id] = token
    MONITORED_TOKENS[token] = message.from_user.id
    
    messages = list_messages(token)
    if not messages:
        await message.reply("**❌ Писем не найдено или токен неверный.**")
        # ИСПРАВЛЕНО: используем .id
        await bot.delete_messages(message.chat.id, [loading_msg.id])
        return
        
    # Обновляем статистику
    STATS['total_messages_checked'] += len(messages)
    save_stats()
    
    # Обновляем список последних сообщений для мониторинга
    user_data.setdefault(message.from_user.id, {})['last_messages'] = messages

    output = "**📧 Ваши сообщения Smart-Mail 📧**\n"
    output += "**━━━━━━━━━━━━━━━━━━**\n"
    
    buttons = []
    for idx, msg in enumerate(messages[:10], 1):
        output += f"{idx}. От: `{msg['from']['address']}` - Тема: `{msg['subject']}`\n"
        button = InlineKeyboardButton(f"{idx}", callback_data=f"read_{msg['id']}")
        buttons.append(button)

    keyboard = []
    for i in range(0, len(buttons), 5):
        keyboard.append(buttons[i:i+5])

    await message.reply(output, reply_markup=InlineKeyboardMarkup(keyboard))
    # ИСПРАВЛЕНО: используем .id
    await bot.delete_messages(message.chat.id, [loading_msg.id])

@bot.on_message(filters.command('stats'))
async def show_stats(client, message):
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("**Пожалуйста, используйте этого бота только в личных чатах.**")
        return

    stats_message = (
        "**📊 Общая статистика Smart-Mail Bot 📊**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"**👤 Всего пользователей (уникальные):** `{STATS['total_users']}`\n"
        f"**📧 Сгенерировано Email-адресов:** `{STATS['total_emails_generated']}`\n"
        f"**📩 Писем проверено (всего в запросах):** `{STATS['total_messages_checked']}`\n"
        f"**🔔 Уведомлений о новых письмах:** `{STATS['total_new_mail_notifications']}`\n"
        f"**📡 Активных токенов в мониторинге:** `{len(MONITORED_TOKENS)}`\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )
    await message.reply(stats_message)


# Запускаем бота и фоновую задачу
if __name__ == '__main__':
    load_stats()
    print("Статистика загружена. Запуск бота...")
    
    # Запускаем бота
    bot.start()
    
    # Создаем асинхронную задачу для мониторинга почты
    asyncio.get_event_loop().create_task(mail_monitor())
    
    # ЭТО БЛОКИРУЕТ ВЫПОЛНЕНИЕ И УДЕРЖИВАЕТ БОТА В РАБОТЕ
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        # Для корректной остановки
        pass
    finally:
        bot.stop() 
        print("Бот остановлен.")
