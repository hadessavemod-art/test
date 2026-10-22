"""Standalone Telegram bot that generates a Telethon StringSession.

Run this once. It asks for API_ID, API_HASH, phone, login code and 2FA password,
then prints the StringSession string. The session is stored in memory of this
process only. Stop the bot after you get the string.
"""
import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

TOKEN = os.getenv('SESSION_BOT_TOKEN', '8609600422:AAGf0Xy1V87YLv-x-aRCM3V4u8wQLe6r8mM').strip()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('session_bot')
dp = Dispatcher()

# Per-user state: api_id, api_hash, phone, client, phone_code_hash
STATE = {}


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        '🔑 <b>Генератор Telethon StringSession</b>\n\n'
        'Этот бот создаст строку сессии для userbot. После получения строки '
        'останови бота — он больше не нужен.\n\n'
        'Пошагово:\n'
        '1. /api &lt;API_ID&gt; &lt;API_HASH&gt;\n'
        '2. /phone &lt;номер в формате +79...&gt;\n'
        '3. Введи код из Telegram обычным сообщением\n'
        '4. Если включён 2FA — введи пароль\n\n'
        '⚠️ Никому не передавай полученную строку. Это доступ к твоему аккаунту.',
        parse_mode='HTML')


@dp.message(Command('api'))
async def set_api(message: Message):
    parts = (message.text or '').split()
    if len(parts) != 3:
        return await message.answer('Формат: /api API_ID API_HASH')
    try:
        api_id = int(parts[1])
    except ValueError:
        return await message.answer('API_ID должен быть числом')
    api_hash = parts[2].strip()
    STATE[message.from_user.id] = {'api_id': api_id, 'api_hash': api_hash}
    await message.answer('✅ API_ID и API_HASH сохранены. Теперь /phone +79...')


@dp.message(Command('phone'))
async def set_phone(message: Message):
    state = STATE.get(message.from_user.id)
    if not state:
        return await message.answer('Сначала /api API_ID API_HASH')
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer('Формат: /phone +79001234567')
    phone = parts[1].strip()
    if not phone.startswith('+'):
        phone = '+' + phone
    client = TelegramClient(StringSession(), state['api_id'], state['api_hash'])
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except Exception as exc:
        await client.disconnect()
        return await message.answer(f'❌ Не удалось отправить код: {exc}')
    state['client'] = client
    state['phone'] = phone
    state['phone_code_hash'] = sent.phone_code_hash
    await message.answer('📩 Код отправлен в Telegram. Введи его сюда.')


@dp.message(F.text.regexp(r'^\d{5,6}$'))
async def enter_code(message: Message):
    state = STATE.get(message.from_user.id)
    if not state or 'client' not in state:
        return
    client = state['client']
    code = message.text.strip()
    try:
        await client.sign_in(
            phone=state['phone'],
            code=code,
            phone_code_hash=state['phone_code_hash'],
        )
    except SessionPasswordNeededError:
        await message.answer('🔐 Включён 2FA. Введи пароль от облачного пароля Telegram.')
        state['awaiting_2fa'] = True
        return
    except Exception as exc:
        return await message.answer(f'❌ Ошибка входа: {exc}')
    await finish(message, client)


@dp.message(F.text)
async def enter_password(message: Message):
    state = STATE.get(message.from_user.id)
    if not state or not state.get('awaiting_2fa'):
        return
    client = state['client']
    try:
        await client.sign_in(password=message.text.strip())
    except Exception as exc:
        return await message.answer(f'❌ Неверный пароль: {exc}')
    state['awaiting_2fa'] = False
    await finish(message, client)


async def finish(message: Message, client: TelegramClient):
    string = client.session.save()
    me = await client.get_me()
    await client.disconnect()
    STATE.pop(message.from_user.id, None)
    await message.answer(
        f'✅ Вход выполнен: <b>{me.first_name or ""} {me.id}</b>\n\n'
        'Твоя строка сессии:\n\n'
        f'<code>{string}</code>\n\n'
        'Скопируй её и вставь в переменную <b>USERBOT_SESSION_STRING</b> на Bothost. '
        'Затем останови этого бота — он больше не нужен.',
        parse_mode='HTML')


async def main():
    if not TOKEN:
        raise SystemExit('Укажи SESSION_BOT_TOKEN в переменных окружения')
    bot = Bot(TOKEN)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
