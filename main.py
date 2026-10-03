# main.py
# Угонщик юзернеймов. Мониторинг освободившихся @username.
# Под Bothost (Pterodactyl). Уведомления в ЛС админу. Кнопки в меню.
# Запуск: python main.py
# Зависимости: pip install telethon

import asyncio
import os
import sqlite3

from telethon import TelegramClient, events, Button
from telethon.errors import (
    UsernameNotOccupiedError,
    UsernameInvalidError,
    UsernameOccupiedError,
    FloodWaitError,
)
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.account import UpdateUsernameRequest


# ============ КОНФИГ ============
API_ID = 34714558
API_HASH = "8850699128:AAF9nd8EM3sKeJA-kfbRv2YQ75Zha3i0crs"
ADMIN_ID = 8754872846

BASE = os.path.dirname(os.path.abspath(__file__))
SESSION_NAME = os.path.join(BASE, "grabber")
DB_PATH = os.path.join(BASE, "grabber.db")
LOG_PATH = os.path.join(BASE, "grabber.log")

# прокси — если IP Bothost ловит FloodWait, раскомментируй и впиши
# PROXY = ("socks5", "ip", 1080, True, "user", "pass")
PROXY = None

CHECK_INTERVAL = 3.0       # пауза между проходами
PER_CHECK_DELAY = 0.8      # пауза между проверками внутри прохода
NOTIFY_ON_FREE = True

# состояние в памяти
state = {
    "running": False,
    "min_len": 3,
    "max_len": 5,
    "found": [],
    "awaiting_add": False,
}


# ============ БАЗА ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            username TEXT PRIMARY KEY,
            len INTEGER,
            status TEXT,
            last_check TEXT,
            found_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS found (
            username TEXT PRIMARY KEY,
            found_at TEXT
        )
    """)
    conn.commit()
    return conn


def db_add_targets(conn, usernames):
    c = conn.cursor()
    for u in usernames:
        u = u.strip().lstrip("@").lower()
        if not u:
            continue
        c.execute(
            "INSERT OR IGNORE INTO targets (username, len, status) VALUES (?, ?, ?)",
            (u, len(u), "unknown"),
        )
    conn.commit()


def db_get_targets(conn):
    c = conn.cursor()
    c.execute("SELECT username, len FROM targets ORDER BY len ASC")
    return c.fetchall()


def db_del_targets(conn, usernames):
    c = conn.cursor()
    for u in usernames:
        c.execute("DELETE FROM targets WHERE username = ?", (u.lstrip("@").lower(),))
    conn.commit()


def db_clear_targets(conn):
    conn.cursor().execute("DELETE FROM targets")
    conn.commit()


def db_mark(conn, username, status):
    conn.cursor().execute(
        "UPDATE targets SET status = ?, last_check = datetime('now') WHERE username = ?",
        (status, username),
    )
    conn.commit()


def db_mark_found(conn, username):
    conn.cursor().execute(
        "INSERT OR IGNORE INTO found (username, found_at) VALUES (?, datetime('now'))",
        (username,),
    )
    conn.cursor().execute(
        "UPDATE targets SET status = 'free', found_at = datetime('now') WHERE username = ?",
        (username,),
    )
    conn.commit()


def db_get_found(conn):
    c = conn.cursor()
    c.execute("SELECT username, found_at FROM found ORDER BY found_at DESC")
    return c.fetchall()


# ============ ЛОГИ ============
def log(msg):
    line = f"[grabber] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============ ПРОВЕРКА ============
async def check_username(client, username):
    """True — занят, False — свободен, None — невалидный."""
    try:
        await client(ResolveUsernameRequest(username))
        return True
    except UsernameNotOccupiedError:
        return False
    except UsernameInvalidError:
        return None
    except FloodWaitError as e:
        log(f"FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds)
        return True
    except Exception as e:
        log(f"check error @{username}: {e}")
        return True


# ============ МОНИТОРИНГ ============
async def monitor_loop(client, conn, bot):
    log("Мониторинг запущен.")
    while state["running"]:
        targets = db_get_targets(conn)
        if not targets:
            await asyncio.sleep(2)
            continue

        for username, length in targets:
            if not state["running"]:
                break
            if not (state["min_len"] <= length <= state["max_len"]):
                continue

            free = await check_username(client, username)
            if free is False:
                db_mark_found(conn, username)
                if username not in state["found"]:
                    state["found"].append(username)
                    log(f"Свободен @{username}")
                    if NOTIFY_ON_FREE:
                        try:
                            await bot.send_message(
                                ADMIN_ID,
                                f"🟢 Свободен: @{username}\n"
                                f"Длина: {length}\n"
                                f"Забрать: /grab {username}\n"
                                f"Удалить из списка: /del {username}",
                            )
                        except Exception as e:
                            log(f"notify error: {e}")
            elif free is True:
                db_mark(conn, username, "busy")
            else:
                db_mark(conn, username, "invalid")

            await asyncio.sleep(PER_CHECK_DELAY)

        await asyncio.sleep(CHECK_INTERVAL)

    log("Мониторинг остановлен.")


# ============ ЗАХВАТ ============
async def try_grab(client, username):
    try:
        await client(UpdateUsernameRequest(username=username))
        return True, None
    except UsernameOccupiedError:
        return False, "занят"
    except FloodWaitError as e:
        return False, f"floodwait {e.seconds}s"
    except Exception as e:
        return False, str(e)


# ============ МЕНЮ ============
def main_menu():
    return [
        [Button.inline("▶️ Старт мониторинга", b"start")],
        [Button.inline("⏹ Стоп мониторинга", b"stop")],
        [Button.inline("📋 Список целей", b"list")],
        [Button.inline("🟢 Найденные", b"found")],
        [Button.inline("🎯 Фильтр по длине", b"len")],
        [Button.inline("➕ Добавить ники", b"add")],
        [Button.inline("🗑 Очистить список", b"clear")],
        [Button.inline("❓ Помощь", b"help")],
    ]


def len_menu():
    return [
        [Button.inline("3 буквы", b"len_3")],
        [Button.inline("4 буквы", b"len_4")],
        [Button.inline("5 букв", b"len_5")],
        [Button.inline("6 букв", b"len_6")],
        [Button.inline("3–4", b"len_34")],
        [Button.inline("3–5", b"len_35")],
        [Button.inline("4–6", b"len_46")],
        [Button.inline("Любая длина", b"len_any")],
        [Button.inline("⬅️ Назад", b"menu")],
    ]


# ============ ОБРАБОТЧИКИ ============
def register_handlers(client, conn):
    @client.on(events.NewMessage(pattern=r"^/start"))
    async def cmd_start(event):
        if event.sender_id != ADMIN_ID:
            return
        await event.respond("Админ-меню:", buttons=main_menu())

    @client.on(events.NewMessage(pattern=r"^/menu"))
    async def cmd_menu(event):
        if event.sender_id != ADMIN_ID:
            return
        await event.respond("Админ-меню:", buttons=main_menu())

    @client.on(events.NewMessage(pattern=r"^/add(?: (.+))?"))
    async def cmd_add(event):
        if event.sender_id != ADMIN_ID:
            return
        text = event.pattern_match.group(1)
        if not text:
            await event.respond("Использование: /add nick1 nick2 nick3")
            return
        names = text.split()
        db_add_targets(conn, names)
        await event.respond(f"➕ Добавлено: {len(names)}")

    @client.on(events.NewMessage(pattern=r"^/del(?: (.+))?"))
    async def cmd_del(event):
        if event.sender_id != ADMIN_ID:
            return
        text = event.pattern_match.group(1)
        if not text:
            await event.respond("Использование: /del nick1 nick2")
            return
        db_del_targets(conn, text.split())
        await event.respond("🗑 Удалено.")

    @client.on(events.NewMessage(pattern=r"^/grab(?: (.+))?"))
    async def cmd_grab(event):
        if event.sender_id != ADMIN_ID:
            return
        name = event.pattern_match.group(1)
        if not name:
            await event.respond("Использование: /grab nick")
            return
        name = name.lstrip("@").lower()
        ok, err = await try_grab(client, name)
        if ok:
            await event.respond(f"✅ Захвачен @{name}")
            log(f"Захвачен @{name}")
        else:
            await event.respond(f"❌ Не удалось @{name}: {err}")

    @client.on(events.NewMessage(pattern=r"^/len(?: (\d+) (\d+))?"))
    async def cmd_len(event):
        if event.sender_id != ADMIN_ID:
            return
        m = event.pattern_match
        if m.group(1) and m.group(2):
            state["min_len"] = int(m.group(1))
            state["max_len"] = int(m.group(2))
            await event.respond(f"🎯 Фильтр: {state['min_len']}–{state['max_len']}")
        else:
            await event.respond("Использование: /len 3 5")

    @client.on(events.CallbackQuery())
    async def on_click(event):
        if event.sender_id != ADMIN_ID:
            await event.answer("Нет доступа.", alert=True)
            return

        data = event.data.decode()

        if data == "menu":
            await event.edit("Админ-меню:", buttons=main_menu())

        elif data == "start":
            if state["running"]:
                await event.answer("Уже запущен.", alert=True)
            else:
                state["running"] = True
                asyncio.create_task(monitor_loop(client, conn, client))
                await event.answer("▶️ Запущен")
                await event.edit(
                    f"▶️ Мониторинг идёт.\n"
                    f"Фильтр: {state['min_len']}–{state['max_len']}\n"
                    f"Целей: {len(db_get_targets(conn))}",
                    buttons=main_menu(),
                )

        elif data == "stop":
            state["running"] = False
            await event.answer("⏹ Остановлен")
            await event.edit("⏹ Мониторинг остановлен.", buttons=main_menu())

        elif data == "list":
            targets = db_get_targets(conn)
            if not targets:
                await event.answer("Список пуст.", alert=True)
                return
            lines = []
            for u, l in targets[:40]:
                lines.append(f"@{u} ({l})")
            tail = "" if len(targets) <= 40 else f"\n...и ещё {len(targets)-40}"
            await event.edit(
                f"📋 Цели ({len(targets)}):\n" + "\n".join(lines) + tail,
                buttons=main_menu(),
            )

        elif data == "found":
            found = db_get_found(conn)
            if not found:
                await event.answer("Пока ничего не найдено.", alert=True)
                return
            lines = [f"@{u} — {t}" for u, t in found[:40]]
            await event.edit(
                f"🟢 Найденные ({len(found)}):\n" + "\n".join(lines),
                buttons=main_menu(),
            )

        elif data == "len":
            await event.edit(
                f"🎯 Текущий фильтр: {state['min_len']}–{state['max_len']}\n"
                f"Выбери диапазон:",
                buttons=len_menu(),
            )

        elif data.startswith("len_"):
            v = data[4:]
            if v == "any":
                state["min_len"], state["max_len"] = 1, 64
            elif v == "34":
                state["min_len"], state["max_len"] = 3, 4
            elif v == "35":
                state["min_len"], state["max_len"] = 3, 5
            elif v == "46":
                state["min_len"], state["max_len"] = 4, 6
            else:
                n = int(v)
                state["min_len"] = state["max_len"] = n
            await event.answer(f"Фильтр: {state['min_len']}–{state['max_len']}")
            await event.edit(
                f"🎯 Фильтр: {state['min_len']}–{state['max_len']}",
                buttons=main_menu(),
            )

        elif data == "add":
            state["awaiting_add"] = True
            await event.edit(
                "➕ Пришли ники одним сообщением, через пробел или с новой строки.\n"
                "Пример:\n`nick1 nick2 nick3`\n\n"
                "После отправки вернись в /menu.",
                buttons=[[Button.inline("⬅️ Назад", b"menu")]],
            )

        elif data == "clear":
            await event.edit(
                "🗑 Удалить ВСЕ цели?",
                buttons=[
                    [Button.inline("✅ Да, удалить", b"clear_yes")],
                    [Button.inline("⬅️ Отмена", b"menu")],
                ],
            )

        elif data == "clear_yes":
            db_clear_targets(conn)
            state["found"].clear()
            await event.answer("Очищено")
            await event.edit("🗑 Список очищен.", buttons=main_menu())

        elif data == "help":
            await event.edit(
                "❓ Помощь\n\n"
                "• /start — меню\n"
                "• /add nick1 nick2 — добавить цели\n"
                "• /del nick1 — удалить цель\n"
                "• /len 3 5 — фильтр по длине\n"
                "• /grab nick — попытаться захватить\n\n"
                "Бот проверяет цели и шлёт сюда, когда ник свободен.",
                buttons=main_menu(),
            )

        else:
            await event.answer("Неизвестная кнопка.", alert=True)


# ============ ЛОГИН ============
async def login():
    client = TelegramClient(
        SESSION_NAME, API_ID, API_HASH,
        proxy=PROXY,
    )
    await client.start()
    return client


# ============ MAIN ============
async def main():
    conn = init_db()
    client = await login()
    me = await client.get_me()
    log(f"Вошли как {me.first_name} id={me.id}")

    if not ADMIN_ID:
        log("ADMIN_ID не задан.")
        return

    register_handlers(client, conn)

    @client.on(events.NewMessage)
    async def catch_add(event):
        if event.sender_id != ADMIN_ID:
            return
        if not state.get("awaiting_add"):
            return
        text = event.raw_text.strip()
        if not text or text.startswith("/"):
            return
        state["awaiting_add"] = False
        names = [x for x in text.replace(",", " ").split() if x]
        db_add_targets(conn, names)
        await event.respond(
            f"➕ Добавлено: {len(names)}\n"
            f"Всего целей: {len(db_get_targets(conn))}",
            buttons=main_menu(),
        )

    await client.send_message(
        ADMIN_ID,
        "🟢 Бот запущен. /start — меню.",
    )
    log("Бот запущен.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
