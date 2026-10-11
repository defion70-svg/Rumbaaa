"""
Домашний бот для квартиры (Telegram).

Задания:   /add /today /list /del /preset
Питомцы:   /walk /walks  (и задания для собак через /add)
Покупки:   /buy /shopping /bought
Расходы:   /join /spent /balance /expenses /unspend /settle
Рейтинг:   /top  (итоги недели приходят сами по воскресеньям)

Запуск:
  pip install python-telegram-bot
  export BOT_TOKEN="токен от @BotFather"
  python chores_bot.py
Потом добавьте бота в чат и отправьте там /start.
"""
import asyncio
import json
import logging
import os
import random
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import BotCommand, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters,
)

# ---------- НАСТРОЙКИ ----------
TOKEN = os.environ["BOT_TOKEN"]
try:
    TZ = ZoneInfo("Europe/Moscow")  # ваш часовой пояс
except Exception:  # если в системе нет базы часовых поясов
    TZ = timezone(timedelta(hours=3))  # МСК = UTC+3, поправьте под себя
REMIND_AT = time(10, 0)    # утренняя сводка
WEEKLY_AT = time(20, 0)    # итоги недели (по воскресеньям)
REPEAT_DAYS = 2            # сколько дней после срока задание остаётся «висеть»
MORNING_MESSAGE_WHEN_PINNED = False  # True: слать отдельное утреннее сообщение даже при закреплённом чек-листе
# --------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
logging.basicConfig(level=logging.INFO)

DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
ABBR = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
PREFIX3 = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}

LEVELS = {1: ("🟢", "лёгкая"), 2: ("🟡", "средняя"), 3: ("🔴", "сложная")}

PRAISE = [
    "Красавчик!", "Так держать! 💪", "Чистота — залог здоровья ✨", "Респект!",
    "Собаки гордятся тобой 🐕", "Ещё одно дело в копилку!", "Вот это порядок!",
    "Герой дня! 🏆",
]

HELP = (
    "📋 ЗАДАНИЯ\n"
    "/add Название | кто | срок | сложность — добавить (сложность по желанию)\n"
    "/today — дела на сегодня\n"
    "/list — все задания\n"
    "/del N — удалить (номера сдвинутся)\n"
    "/edit — панель редактирования заданий\n"
    "/preset — готовые задания (блохи/клещи, корм)\n"
    "📌 /pin — закрепить чек-лист на сегодня (обновляется в 10:00) · /unpin — отключить\n\n"
    "Срок: ежедневно · каждую среду · раз в 2 недели сб · каждые 3 дня · "
    "1 числа · раз в 3 месяца 10 числа · раз в год 15.03 · 15.10 (разово).\n"
    "Добавьте «за день» или «за 3 дня» — напомню заранее.\n"
    "«кто»: одно имя, либо несколько через «/» — очередь по кругу.\n"
    "Сложность (лёгкая / средняя / сложная) даёт баллы в рейтинге: 🟢 1, 🟡 2, 🔴 3.\n"
    "Пример: /add 💉 Прививки собак | Мы с женой | раз в год 15.03 за 7 дней\n\n"
    "🐕 /walk — отметить прогулку · /walks — последние\n"
    "🛒 /buy молоко, хлеб — в список · /shopping — список · /bought [N] — убрать\n"
    "💸 /join — стать участником · /spent 1500 продукты · /balance · "
    "/expenses · /unspend · /settle да\n"
    "🏆 /top — рейтинг и итоги недели"
)

COMMANDS = [
    ("today", "Дела на сегодня"), ("pin", "Закрепить чек-лист"), ("edit", "Редактировать задания"), ("list", "Все задания"), ("add", "Добавить задание"),
    ("del", "Удалить задание"), ("walk", "Отметить прогулку собак"),
    ("buy", "Добавить в список покупок"), ("shopping", "Список покупок"),
    ("bought", "Убрать из списка покупок"), ("spent", "Записать расход"),
    ("balance", "Кто кому должен"), ("top", "Рейтинг и итоги недели"),
    ("help", "Справка"),
]


# ---------- время ----------
def today_local() -> date:
    return datetime.now(TZ).date()


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w}"


# ---------- хранение ----------
def default_tasks() -> dict:
    def w(title, owners, wd, level=1):
        return {
            "title": title, "owners": owners, "idx": 0, "done_for": None, "level": level,
            "schedule": {"type": "weekly", "weekday": wd, "every": 1},
        }
    return {
        "1": w("🍳 Кухня", ["Сосед", "Мы с женой"], 2, 2),
        "2": w("🚿 Ванная", ["Мы с женой", "Сосед"], 5, 3),
        "3": w("🛋 Гостиная + пол", ["Мы с женой"], 6, 2),
    }


def load_state() -> dict:
    state = {"chat_id": None}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if "tasks" not in state:  # первый запуск
        state["tasks"] = default_tasks()
        state["next_id"] = 4
    for key, default in (
        ("members", {}), ("log", []), ("walks", []), ("shopping", []),
        ("expenses", []), ("last_morning", None), ("last_weekly", None), ("pinned", None),
    ):
        state.setdefault(key, default)
    if not state.get("_no_utilities"):  # однократно убираем задание про коммуналку
        for tid in [k for k, t in state["tasks"].items()
                    if t["title"] == "💧 Передать показания счётчиков"]:
            del state["tasks"][tid]
        state["_no_utilities"] = True
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def user_name(user) -> str:
    return user.first_name or user.username or str(user.id)


def touch(state: dict, user) -> bool:
    """Запоминаем участников квартиры (для расходов и рейтинга)."""
    if user is None or user.is_bot:
        return False
    name = user_name(user)
    if state["members"].get(str(user.id)) != name:
        state["members"][str(user.id)] = name
        return True
    return False


# ---------- расписание ----------
def month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _parse_base(t: str) -> dict:
    if "ежедневно" in t or "каждый день" in t:
        return {"type": "daily"}

    m = re.search(r"(?:кажд\w*|раз\s+в)\s*(\d+)\s*д(?:ня|ней|ень)", t)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError("Интервал должен быть от 1 дня.")
        if n == 1:
            return {"type": "daily"}
        now = datetime.now(TZ)
        anchor = now.date()
        if now.time() >= REMIND_AT:
            anchor += timedelta(days=1)  # сегодняшняя сводка уже прошла
        return {"type": "interval", "every": n, "anchor": anchor.isoformat()}

    date_m = re.search(r"\b(\d{1,2})\.(\d{1,2})\b", t)
    if re.search(r"кажд\w*\s+год|раз\s+в\s+год|ежегодно", t):
        if not date_m:
            raise ValueError("Укажите дату: «раз в год 15.03».")
        day, month = int(date_m.group(1)), int(date_m.group(2))
        try:
            date(2001, month, day)
        except ValueError:
            raise ValueError("Не понял дату. Формат: 15.03 (29 февраля не подходит).")
        return {"type": "yearly", "month": month, "day": day}

    if date_m:
        day, month = int(date_m.group(1)), int(date_m.group(2))
        today = today_local()
        try:
            d = date(today.year, month, day)
            if d < today:
                d = date(today.year + 1, month, day)
        except ValueError:
            raise ValueError("Не понял дату. Формат: 15.10")
        return {"type": "once", "date": d.isoformat()}

    months_m = re.search(r"(\d+)\s*мес", t)
    day_m = re.search(r"\b(\d{1,2})\s*(?:-?го\s*)?числ", t)
    if months_m and not day_m:
        raise ValueError("Укажите число месяца: «раз в 3 месяца 10 числа».")
    if day_m:
        day = int(day_m.group(1))
        if not 1 <= day <= 28:
            raise ValueError("Число месяца должно быть от 1 до 28.")
        every = max(1, int(months_m.group(1))) if months_m else 1
        sched = {"type": "monthly", "day": day, "every": every}
        if every > 1:
            today = today_local()
            start = month_index(today) + (0 if today.day <= day else 1)
            sched["start"] = start
        return sched

    weekday = None
    for word in re.findall(r"[а-я]+", t):
        if word in ABBR:
            weekday = ABBR[word]
            break
        if word[:3] in PREFIX3:
            weekday = PREFIX3[word[:3]]
            break
    if weekday is None:
        raise ValueError(
            "Не понял срок. Примеры: «каждую среду», «ежедневно», «каждые 3 дня», "
            "«раз в 2 недели сб», «1 числа», «раз в год 15.03», «15.10»."
        )
    every = 1
    m = re.search(r"(\d+)\s*недел", t)
    if m:
        every = max(1, int(m.group(1)))
    sched = {"type": "weekly", "weekday": weekday, "every": every}
    if every > 1:
        today = today_local()
        anchor = today + timedelta(days=(weekday - today.weekday()) % 7)
        sched["anchor"] = anchor.isoformat()
    return sched


def parse_schedule(text: str) -> dict:
    t = text.lower().strip()
    advance = 0
    m = re.search(r"\bза\s*(\d+)?\s*д(?:ень|ня|ней)\b", t)
    if m:
        advance = min(14, max(1, int(m.group(1) or 1)))
        t = t[:m.start()] + " " + t[m.end():]
    sched = _parse_base(t)
    if advance and sched["type"] != "daily":
        sched["advance"] = advance
    return sched


def describe(s: dict) -> str:
    t = s["type"]
    if t == "daily":
        base = "ежедневно"
    elif t == "interval":
        base = f"раз в {s['every']} дн."
    elif t == "weekly":
        day = DAY_NAMES[s["weekday"]]
        base = f"каждую неделю, {day}" if s.get("every", 1) == 1 else f"раз в {s['every']} нед., {day}"
    elif t == "monthly":
        base = (f"{s['day']}-го числа каждого месяца" if s.get("every", 1) == 1
                else f"{s['day']}-го числа раз в {s['every']} мес.")
    elif t == "yearly":
        base = f"раз в год, {s['day']:02d}.{s['month']:02d}"
    else:
        d = date.fromisoformat(s["date"])
        base = f"разово, {d.day:02d}.{d.month:02d}"
    if s.get("advance"):
        base += f", напомню за {s['advance']} дн."
    return base


def occurrence(s: dict, today: date):
    """Последняя дата срока, которая уже наступила (или None)."""
    t = s["type"]
    if t == "daily":
        return today
    if t == "interval":
        anchor = date.fromisoformat(s["anchor"])
        delta = (today - anchor).days
        if delta < 0:
            return None
        return anchor + timedelta(days=delta // s["every"] * s["every"])
    if t == "weekly":
        every = s.get("every", 1)
        if every == 1:
            return today - timedelta(days=(today.weekday() - s["weekday"]) % 7)
        anchor = date.fromisoformat(s["anchor"])
        delta = (today - anchor).days
        if delta < 0:
            return None
        step = 7 * every
        return anchor + timedelta(days=delta // step * step)
    if t == "monthly":
        day, every = s["day"], s.get("every", 1)
        idx = month_index(today) - (0 if today.day >= day else 1)
        if every > 1:
            start = s["start"]
            if idx < start:
                return None
            idx = start + (idx - start) // every * every
        year, month = divmod(idx, 12)
        return date(year, month + 1, day)
    if t == "yearly":
        d = date(today.year, s["month"], s["day"])
        return d if d <= today else date(today.year - 1, s["month"], s["day"])
    d = date.fromisoformat(s["date"])
    return d if d <= today else None


def current_owner(task: dict) -> str:
    return task["owners"][task["idx"] % len(task["owners"])]


def ordered(state: dict) -> list:
    """Задания по порядку; номер в списке = позиция (1, 2, 3...)."""
    return sorted(state["tasks"].items(), key=lambda kv: int(kv[0]))


def window(task: dict) -> int:
    s = task["schedule"]
    if s["type"] == "daily":
        return 0
    if s["type"] == "interval":
        return min(REPEAT_DAYS, s["every"] - 1)
    return REPEAT_DAYS


def level_of(task: dict) -> int:
    return task.get("level", 1)


def level_emoji(task: dict) -> str:
    return LEVELS[level_of(task)][0]


def parse_level(text: str) -> int:
    t = text.lower().strip()
    if t in ("1", "2", "3"):
        return int(t)
    if t.startswith(("лёг", "лег", "прост")):
        return 1
    if t.startswith("средн"):
        return 2
    if t.startswith(("слож", "тяж")):
        return 3
    raise ValueError("Сложность: лёгкая, средняя или сложная (или 1, 2, 3).")


def log_done(state: dict, who: str, title: str, pts: int = 1) -> None:
    state["log"].append({"who": who, "task": title, "date": today_local().isoformat(), "pts": pts})
    del state["log"][:-1000]


def mark_done(state: dict, tid: str, occ_iso: str, who: str):
    """None — задания нет, False — уже отмечено, True — отмечено сейчас."""
    task = state["tasks"].get(tid)
    if not task:
        return None
    if task["done_for"] == occ_iso:
        return False
    task["done_for"] = occ_iso
    task["idx"] = (task["idx"] + 1) % len(task["owners"])
    log_done(state, who, task["title"], level_of(task))
    return True


def fmt_money(x: float) -> str:
    return str(round(x)) if abs(x - round(x)) < 0.005 else f"{x:.2f}"


# ---------- сводка на сегодня ----------
def build_today(state: dict):
    """Возвращает (текст, кнопки, сколько пунктов)."""
    today = today_local()
    pending, done, upcoming = [], [], []
    for tid, t in ordered(state):
        s = t["schedule"]
        occ = occurrence(s, today)
        if occ is not None and (today - occ).days <= window(t):
            if t["done_for"] == occ.isoformat():
                if occ == today:
                    done.append(t)
            else:
                pending.append((tid, t, occ, (today - occ).days))
        adv = s.get("advance", 0)
        if adv:
            target = today + timedelta(days=adv)
            if occurrence(s, target) == target:
                upcoming.append((adv, t))
    count = len(pending) + len(upcoming)
    label = f"{DAY_NAMES[today.weekday()]}, {today:%d.%m}"
    if not pending and not done and not upcoming:
        return f"📅 На сегодня ({label}): дел нет 🎉", None, 0

    lines = [f"📅 На сегодня ({label}):"]
    buttons = []
    for tid, t, occ, late in pending:
        suffix = "" if late == 0 else " ⏰ не сделано раньше"
        lines.append(f"⬜ {t['title']} {level_emoji(t)} — {current_owner(t)}{suffix}")
        row = [InlineKeyboardButton(f"✅ {t['title'][:24]}", callback_data=f"tdone:{tid}:{occ.isoformat()}")]
        if len(t["owners"]) > 1:
            row.append(InlineKeyboardButton("⏭ Передать", callback_data=f"skip:{tid}:{occ.isoformat()}"))
        buttons.append(row)
    for t in done:
        lines.append(f"✅ {t['title']}")
    if upcoming:
        lines.append("")
        for adv, t in upcoming:
            when = "Завтра" if adv == 1 else f"Через {adv} дн."
            lines.append(f"🔔 {when}: {t['title']} — {current_owner(t)}")
    return "\n".join(lines), (InlineKeyboardMarkup(buttons) if buttons else None), count


# ---------- команды: задания ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text("Привет! Я домашний помощник. Напоминания будут приходить в этот чат.\n\n" + HELP)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


def build_task(parts: list) -> dict:
    """parts: [название, кто, срок] и по желанию [сложность]."""
    if len(parts) not in (3, 4) or not all(parts):
        raise ValueError("Нужно 3 части через «|»: Название | кто | срок (и по желанию | сложность)")
    title, owners_s, when = parts[:3]
    owners = [o.strip() for o in owners_s.split("/") if o.strip()]
    if not owners:
        raise ValueError("Укажите, кто отвечает.")
    sched = parse_schedule(when)
    level = parse_level(parts[3]) if len(parts) == 4 else 1
    return {"title": title, "owners": owners, "idx": 0, "schedule": sched, "done_for": None, "level": level}


def add_task(state: dict, task: dict) -> str:
    tid = str(state["next_id"])
    state["next_id"] += 1
    state["tasks"][tid] = task
    return tid


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts_raw = update.message.text.split(None, 1)
    if len(parts_raw) < 2:
        await update.message.reply_text(HELP)
        return
    try:
        task = build_task([p.strip() for p in parts_raw[1].split("|")])
    except ValueError as e:
        await update.message.reply_text(f"{e}\n\n{HELP}")
        return
    state = load_state()
    add_task(state, task)
    if not state.get("chat_id"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update_pinned(context.bot, state)
    emoji, name = LEVELS[task["level"]]
    await update.message.reply_text(
        f"Добавлено №{len(state['tasks'])}: {task['title']}\n"
        f"Кто: {' → '.join(task['owners'])}\nСрок: {describe(task['schedule'])}\n"
        f"Сложность: {emoji} {name}"
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["tasks"]:
        await update.message.reply_text("Заданий пока нет. Добавьте через /add")
        return
    today = today_local()
    lines = ["📋 Задания:"]
    for pos, (tid, t) in enumerate(ordered(state), 1):
        occ = occurrence(t["schedule"], today)
        if occ and (today - occ).days <= window(t):
            mark = "✅" if t["done_for"] == occ.isoformat() else "⬜"
        else:
            mark = "▫️"
        lines.append(f"{pos}. {mark} {t['title']} {level_emoji(t)} — {current_owner(t)} ({describe(t['schedule'])})")
    lines.append("\n⬜ ждёт, ✅ сделано, ▫️ срок ещё не наступил. Удалить: /del N")
    await update.message.reply_text("\n".join(lines))


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Укажите номер: /del 3 (номера видно в /list)")
        return
    state = load_state()
    items = ordered(state)
    pos = int(context.args[0])
    if not 1 <= pos <= len(items):
        await update.message.reply_text("Нет задания с таким номером.")
        return
    tid, task = items[pos - 1]
    del state["tasks"][tid]
    save_state(state)
    await update_pinned(context.bot, state)
    await update.message.reply_text(f"Удалено: {task['title']}\nНомера обновлены, смотрите /list")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb, _ = build_today(load_state())
    await update.message.reply_text(text, reply_markup=kb)


async def preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    ready = [
        ("🐕 Таблетки от блох и клещей", ["Мы с женой"], "1 числа за день"),
        ("🥫 Проверить корм собак", ["Мы с женой"], "каждые 14 дней"),
    ]
    have = {t["title"] for t in state["tasks"].values()}
    added = []
    for title, owners, when in ready:
        if title in have:
            continue
        tid = str(state["next_id"])
        state["next_id"] += 1
        state["tasks"][tid] = {
            "title": title, "owners": owners, "idx": 0,
            "schedule": parse_schedule(when), "done_for": None, "level": 1,
        }
        added.append(title)
    if not state.get("chat_id"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update_pinned(context.bot, state)
    text = ("Добавлено:\n• " + "\n• ".join(added)) if added else "Все готовые задания уже добавлены."
    text += (
        "\n\nЕщё идеи для собак (подставьте свои даты):\n"
        "/add 💉 Прививки собак | Мы с женой | раз в год 15.03 за 7 дней\n"
        "/add 🩺 Визит к ветеринару | Мы с женой | раз в год 10.09 за 7 дней"
    )
    await update.message.reply_text(text)


# ---------- кнопки ----------
async def refresh_today(query, state):
    text, kb, _ = build_today(state)
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except Exception:  # сообщение не изменилось
        pass


async def on_today_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, tid, occ_iso = query.data.split(":", 2)
    state = load_state()
    who = user_name(query.from_user)
    if mark_done(state, tid, occ_iso, who):
        save_state(state)
        await query.answer(f"{random.choice(PRAISE)} ({who})")
    else:
        await query.answer("Уже отмечено или удалено")
    await refresh_today(query, state)
    await update_pinned(context.bot, state, skip_id=query.message.message_id)


async def on_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, tid, _occ = query.data.split(":", 2)
    state = load_state()
    task = state["tasks"].get(tid)
    if task and len(task["owners"]) > 1:
        task["idx"] = (task["idx"] + 1) % len(task["owners"])
        save_state(state)
        await query.answer(f"Передано: теперь {current_owner(task)}")
    else:
        await query.answer("Передать некому")
    await refresh_today(query, state)
    await update_pinned(context.bot, state, skip_id=query.message.message_id)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки из старых сообщений (done:...)."""
    query = update.callback_query
    await query.answer()
    _, tid, occ_iso = query.data.split(":", 2)
    state = load_state()
    task = state["tasks"].get(tid)
    if not task:
        await query.edit_message_text("Это задание уже удалено.")
        return
    who = user_name(query.from_user)
    if mark_done(state, tid, occ_iso, who) is False:
        await query.edit_message_text(f"{task['title']} — уже отмечено ✅")
        return
    save_state(state)
    await update_pinned(context.bot, state)
    text = f"{task['title']} — сделано ✅ ({who})\n{random.choice(PRAISE)}"
    if len(task["owners"]) > 1:
        text += f"\nВ следующий раз: {current_owner(task)}"
    await query.edit_message_text(text)


# ---------- собаки ----------
async def walk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    now = datetime.now(TZ)
    who = user_name(update.effective_user)
    state["walks"].append({"who": who, "at": now.isoformat(timespec="minutes")})
    del state["walks"][:-200]
    save_state(state)
    await update.message.reply_text(f"🐕 {who} выгулял(а) собак в {now:%H:%M} ✅")


async def walks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["walks"]:
        await update.message.reply_text("Прогулок пока не отмечено. Отметьте через /walk")
        return
    lines = ["🐕 Последние прогулки:"]
    for w in state["walks"][-5:][::-1]:
        at = datetime.fromisoformat(w["at"])
        lines.append(f"{at:%d.%m %H:%M} — {w['who']}")
    await update.message.reply_text("\n".join(lines))


# ---------- покупки ----------
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split(None, 1)
    if len(parts) < 2:
        await update.message.reply_text("Напишите, что купить: /buy молоко, хлеб")
        return
    items = [i.strip() for i in re.split(r"[,\n]", parts[1]) if i.strip()]
    state = load_state()
    who = user_name(update.effective_user)
    for item in items:
        state["shopping"].append({"item": item, "by": who})
    save_state(state)
    await update.message.reply_text(f"🛒 Добавлено: {', '.join(items)}\nВсего в списке: {len(state['shopping'])}")


async def shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["shopping"]:
        await update.message.reply_text("Список покупок пуст. Добавить: /buy молоко")
        return
    lines = ["🛒 Список покупок:"]
    for i, e in enumerate(state["shopping"], 1):
        lines.append(f"{i}. {e['item']} ({e['by']})")
    lines.append("\nУбрать пункт: /bought N · очистить всё: /bought")
    await update.message.reply_text("\n".join(lines))


async def bought(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["shopping"]:
        await update.message.reply_text("Список покупок и так пуст.")
        return
    if not context.args:
        n = len(state["shopping"])
        state["shopping"] = []
        save_state(state)
        await update.message.reply_text(f"✅ Список очищен ({n} шт.)")
        return
    if not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= len(state["shopping"]):
        await update.message.reply_text("Нет пункта с таким номером. Смотрите /shopping")
        return
    item = state["shopping"].pop(int(context.args[0]) - 1)
    save_state(state)
    await update.message.reply_text(f"✅ Куплено: {item['item']}")


# ---------- расходы ----------
def compute_balance(state: dict):
    members = dict(state["members"])
    for e in state["expenses"]:
        members.setdefault(e["payer_id"], e["payer"])
    total = sum(e["amount"] for e in state["expenses"])
    transfers = []
    if state["expenses"] and len(members) >= 2:
        share = total / len(members)
        net = {uid: -share for uid in members}
        for e in state["expenses"]:
            net[e["payer_id"]] += e["amount"]
        creditors = sorted(([n, uid] for uid, n in net.items() if n > 0.005), reverse=True)
        debtors = sorted(([-n, uid] for uid, n in net.items() if n < -0.005), reverse=True)
        i = j = 0
        while i < len(debtors) and j < len(creditors):
            pay = min(debtors[i][0], creditors[j][0])
            transfers.append((members[debtors[i][1]], members[creditors[j][1]], pay))
            debtors[i][0] -= pay
            creditors[j][0] -= pay
            if debtors[i][0] < 0.005:
                i += 1
            if creditors[j][0] < 0.005:
                j += 1
    return members, total, transfers


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    touch(state, update.effective_user)
    save_state(state)
    names = ", ".join(state["members"].values())
    await update.message.reply_text(f"👋 Вы в списке. Участники ({len(state['members'])}): {names}")


async def spent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = "Формат: /spent 1500 продукты"
    if not context.args:
        await update.message.reply_text(usage)
        return
    try:
        amount = float(context.args[0].replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(usage)
        return
    desc = " ".join(context.args[1:]) or "без описания"
    state = load_state()
    user = update.effective_user
    touch(state, user)
    state["expenses"].append({
        "payer_id": str(user.id), "payer": user_name(user), "amount": amount,
        "desc": desc, "date": today_local().isoformat(),
    })
    save_state(state)
    text = f"💸 {user_name(user)} заплатил(а) {fmt_money(amount)} — {desc}"
    if len(state["members"]) < 2:
        text += "\n\nПока в учёте один участник. Пусть остальные отправят /join."
    await update.message.reply_text(text)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["expenses"]:
        await update.message.reply_text("Расходов пока нет. Запишите: /spent 1500 продукты")
        return
    members, total, transfers = compute_balance(state)
    lines = [
        f"💸 Всего потрачено: {fmt_money(total)}",
        f"Участники ({len(members)}): {', '.join(members.values())}",
        f"На каждого: {fmt_money(total / len(members))}",
        "",
    ]
    if len(members) < 2:
        lines.append("Нужно минимум двое: пусть остальные отправят /join.")
    elif not transfers:
        lines.append("Все в расчёте 🤝")
    else:
        lines.append("Кто кому должен:")
        for debtor, creditor, amt in transfers:
            lines.append(f"• {debtor} → {creditor}: {fmt_money(amt)}")
    lines.append("\nКогда рассчитаетесь: /settle да")
    await update.message.reply_text("\n".join(lines))


async def expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["expenses"]:
        await update.message.reply_text("Расходов пока нет.")
        return
    lines = ["🧾 Последние расходы:"]
    for e in state["expenses"][-10:][::-1]:
        d = date.fromisoformat(e["date"])
        lines.append(f"{d:%d.%m} {e['payer']}: {fmt_money(e['amount'])} — {e['desc']}")
    await update.message.reply_text("\n".join(lines))


async def unspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    uid = str(update.effective_user.id)
    for i in range(len(state["expenses"]) - 1, -1, -1):
        if state["expenses"][i]["payer_id"] == uid:
            e = state["expenses"].pop(i)
            save_state(state)
            await update.message.reply_text(f"↩️ Удалён ваш расход: {fmt_money(e['amount'])} — {e['desc']}")
            return
    await update.message.reply_text("У вас нет записанных расходов.")


async def settle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("да", "yes"):
        await update.message.reply_text("Это обнулит все расходы. Если вы уже рассчитались, отправьте: /settle да")
        return
    state = load_state()
    n = len(state["expenses"])
    state["expenses"] = []
    save_state(state)
    await update.message.reply_text(f"🤝 Расчёт завершён, записей очищено: {n}. Начинаем с нуля!")


# ---------- рейтинг ----------
def score_lines(entries: list) -> list:
    pts, cnt = Counter(), Counter()
    for e in entries:
        pts[e["who"]] += e.get("pts", 1)
        cnt[e["who"]] += 1
    return [(who, n, cnt[who]) for who, n in pts.most_common()]


def weekly_text(state: dict) -> str:
    since = (today_local() - timedelta(days=6)).isoformat()
    top = score_lines([e for e in state["log"] if e["date"] >= since])
    walks_n = sum(1 for w in state["walks"] if w["at"][:10] >= since)
    if not top:
        return "📊 Итоги недели: пока ничего не отмечено."
    lines = ["📊 Итоги недели (баллы за сложность):"]
    for who, n, c in top:
        lines.append(f"• {who} — {n} б. ({c} ✅)")
    if len(top) > 1 and top[0][1] == top[1][1]:
        lines.append("🏆 Ничья на первом месте!")
    else:
        lines.append(f"🏆 Герой недели: {top[0][0]}")
    if walks_n:
        lines.append(f"🐕 Прогулок за неделю: {walks_n}")
    return "\n".join(lines)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text = weekly_text(state)
    total = score_lines(state["log"])
    if total:
        text += "\n\nЗа всё время:\n" + "\n".join(f"• {w} — {n} б. ({c} ✅)" for w, n, c in total)
    await update.message.reply_text(text)


# ---------- панель редактирования ----------
FIELD_PROMPTS = {
    "title": ("Напишите новое название:", "Название"),
    "owners": ("Кто отвечает? Одно имя или несколько через «/» (очередь по кругу):", "Сосед/Мы с женой"),
    "when": ("Новый срок, например: каждую среду за день · каждые 3 дня · раз в год 15.03:", "каждую среду"),
    "new": ("Новое задание в формате:\nНазвание | кто | срок | сложность\n"
            "Пример: Окна | Сосед/Мы с женой | раз в 2 недели сб | сложная",
            "Название | кто | срок | сложность"),
}


def list_panel(state: dict):
    items = ordered(state)
    text = "✏️ Выберите задание для редактирования:" if items else "Заданий пока нет."
    rows = [[InlineKeyboardButton(f"{pos}. {t['title'][:28]} {level_emoji(t)}", callback_data=f"ed:t:{tid}")]
            for pos, (tid, t) in enumerate(items, 1)]
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data="ed:new"),
                 InlineKeyboardButton("✖ Закрыть", callback_data="ed:close")])
    return text, InlineKeyboardMarkup(rows)


def task_panel(state: dict, tid: str):
    t = state["tasks"].get(tid)
    if not t:
        return None
    pos = [k for k, _ in ordered(state)].index(tid) + 1
    lv = level_of(t)
    emoji, name = LEVELS[lv]
    text = (
        f"✏️ Задание №{pos}\n{t['title']}\n\n"
        f"Кто: {' → '.join(t['owners'])} (сейчас: {current_owner(t)})\n"
        f"Срок: {describe(t['schedule'])}\n"
        f"Сложность: {emoji} {name} ({lv} б.)"
    )
    B = InlineKeyboardButton
    rows = [
        [B("✏️ Название", callback_data=f"ed:f:{tid}:title"), B("👥 Кто", callback_data=f"ed:f:{tid}:owners")],
        [B("🗓 Срок", callback_data=f"ed:f:{tid}:when"), B("🎚 Сложность", callback_data=f"ed:lv:{tid}")],
    ]
    last = []
    if len(t["owners"]) > 1:
        last.append(B("⏭ Передать очередь", callback_data=f"ed:next:{tid}"))
    last.append(B("🗑 Удалить", callback_data=f"ed:del:{tid}"))
    rows.append(last)
    rows.append([B("⬅ К списку", callback_data="ed:list")])
    return text, InlineKeyboardMarkup(rows)


async def show_panel(query, text, kb):
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logging.warning("Панель не обновилась: %s", e)


async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb = list_panel(load_state())
    await update.message.reply_text(text, reply_markup=kb)


async def on_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    tid = parts[2] if len(parts) > 2 else None
    state = load_state()
    toast = None
    B = InlineKeyboardButton

    if action == "list":
        await show_panel(q, *list_panel(state))
    elif action == "close":
        context.user_data.pop("edit", None)
        await show_panel(q, "Редактор закрыт. Открыть снова: /edit", None)
    elif action in ("t", "lv", "del", "next", "sl", "dely", "f") and tid not in state["tasks"]:
        toast = "Это задание уже удалено"
        await show_panel(q, *list_panel(state))
    elif action == "t":
        await show_panel(q, *task_panel(state, tid))
    elif action == "lv":
        t = state["tasks"][tid]
        rows = [[B(f"{LEVELS[n][0]} {LEVELS[n][1].capitalize()} ({n} б.)", callback_data=f"ed:sl:{tid}:{n}")
                 for n in (1, 2, 3)],
                [B("⬅ Назад", callback_data=f"ed:t:{tid}")]]
        await show_panel(q, f"Сложность задания «{t['title']}».\nОт неё зависят баллы в рейтинге:",
                         InlineKeyboardMarkup(rows))
    elif action == "sl":
        state["tasks"][tid]["level"] = int(parts[3])
        save_state(state)
        await update_pinned(context.bot, state)
        toast = "Сложность изменена"
        await show_panel(q, *task_panel(state, tid))
    elif action == "next":
        t = state["tasks"][tid]
        t["idx"] = (t["idx"] + 1) % len(t["owners"])
        save_state(state)
        await update_pinned(context.bot, state)
        toast = f"Теперь очередь: {current_owner(t)}"
        await show_panel(q, *task_panel(state, tid))
    elif action == "del":
        t = state["tasks"][tid]
        rows = [[B("🗑 Да, удалить", callback_data=f"ed:dely:{tid}"), B("Отмена", callback_data=f"ed:t:{tid}")]]
        await show_panel(q, f"Удалить «{t['title']}»?", InlineKeyboardMarkup(rows))
    elif action == "dely":
        title = state["tasks"].pop(tid)["title"]
        save_state(state)
        await update_pinned(context.bot, state)
        toast = f"Удалено: {title}"
        await show_panel(q, *list_panel(state))
    elif action in ("f", "new"):
        field = parts[3] if action == "f" else "new"
        prompt_text, placeholder = FIELD_PROMPTS[field]
        prompt = await context.bot.send_message(
            q.message.chat_id, prompt_text, reply_markup=ForceReply(input_field_placeholder=placeholder))
        context.user_data["edit"] = {
            "field": field, "tid": tid if action == "f" else None,
            "chat_id": q.message.chat_id, "panel_id": q.message.message_id, "prompt_id": prompt.message_id,
        }
        toast = "Ответьте на сообщение ниже ⬇️"
    await q.answer(toast)


async def on_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на вопрос панели («Напишите новое название» и т. п.)."""
    pend = context.user_data.get("edit")
    msg = update.message
    if not pend or not msg.reply_to_message or msg.reply_to_message.message_id != pend["prompt_id"]:
        return
    text = (msg.text or "").strip()
    state = load_state()
    field, tid = pend["field"], pend["tid"]
    try:
        if field == "new":
            task = build_task([p.strip() for p in text.split("|")])
            tid = add_task(state, task)
        else:
            task = state["tasks"].get(tid)
            if not task:
                context.user_data.pop("edit", None)
                await msg.reply_text("Это задание уже удалено. Откройте /edit")
                return
            if field == "title":
                if not text:
                    raise ValueError("Название не может быть пустым.")
                task["title"] = text
            elif field == "owners":
                owners = [o.strip() for o in text.split("/") if o.strip()]
                if not owners:
                    raise ValueError("Укажите хотя бы одно имя.")
                task["owners"], task["idx"] = owners, 0
            else:  # when
                task["schedule"] = parse_schedule(text)
                task["done_for"] = None
    except ValueError as e:
        again = await msg.reply_text(f"{e}\nПопробуйте ещё раз, ответом на это сообщение.",
                                     reply_markup=ForceReply())
        pend["prompt_id"] = again.message_id
        return
    save_state(state)
    await update_pinned(context.bot, state)
    context.user_data.pop("edit", None)
    for mid in (pend["prompt_id"], msg.message_id):  # чистим переписку, если хватает прав
        try:
            await context.bot.delete_message(pend["chat_id"], mid)
        except Exception:
            pass
    panel_text, kb = task_panel(state, tid)
    try:
        await context.bot.edit_message_text(
            panel_text, chat_id=pend["chat_id"], message_id=pend["panel_id"], reply_markup=kb)
    except Exception:
        await context.bot.send_message(pend["chat_id"], panel_text, reply_markup=kb)


# ---------- закреплённый чек-лист ----------
async def update_pinned(bot, state: dict, skip_id=None) -> None:
    p = state.get("pinned")
    if not p or p["message_id"] == skip_id:
        return
    text, kb, _ = build_today(state)
    try:
        await bot.edit_message_text(text, chat_id=p["chat_id"], message_id=p["message_id"], reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logging.warning("Не удалось обновить закреплённое сообщение: %s", e)


async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    chat_id = update.effective_chat.id
    old = state.get("pinned")
    if old:  # старый чек-лист открепляем
        try:
            await context.bot.unpin_chat_message(old["chat_id"], old["message_id"])
        except Exception:
            pass
    text, kb, _ = build_today(state)
    msg = await context.bot.send_message(chat_id, text, reply_markup=kb)
    state["pinned"] = {"chat_id": chat_id, "message_id": msg.message_id}
    if not state.get("chat_id"):
        state["chat_id"] = chat_id
    save_state(state)
    try:
        await context.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        await update.message.reply_text("📌 Чек-лист закреплён. Он обновляется в 10:00 и после каждой отметки.")
    except Exception:
        await update.message.reply_text(
            "Сам закрепить не смог. Удерживайте сообщение с чек-листом → «Закрепить». "
            "Чтобы бот закреплял сам, дайте ему право «Закреплять сообщения» (сделайте администратором)."
        )


async def unpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    p = state.get("pinned")
    if not p:
        await update.message.reply_text("Закреплённого чек-листа нет. Создать: /pin")
        return
    try:
        await context.bot.unpin_chat_message(p["chat_id"], p["message_id"])
    except Exception:
        pass
    state["pinned"] = None
    save_state(state)
    await update.message.reply_text("Чек-лист больше не обновляется. Включить снова: /pin")


# ---------- расписание бота ----------
async def run_morning(bot, state: dict):
    today = today_local()
    for tid, t in list(state["tasks"].items()):  # убираем устаревшие разовые
        s = t["schedule"]
        if s["type"] == "once" and (today - date.fromisoformat(s["date"])).days > REPEAT_DAYS:
            del state["tasks"][tid]
    await update_pinned(bot, state)
    if state.get("pinned") and not MORNING_MESSAGE_WHEN_PINNED:
        return
    text, kb, count = build_today(state)
    if count and state.get("chat_id"):
        await bot.send_message(state["chat_id"], "☀️ Доброе утро!\n" + text, reply_markup=kb)


async def tick(bot):
    state = load_state()
    now = datetime.now(TZ)
    today = now.date().isoformat()
    if REMIND_AT <= now.time() < time(22, 0) and state.get("last_morning") != today:
        state["last_morning"] = today
        save_state(state)
        await run_morning(bot, state)
        save_state(state)
    wk = week_key(now.date())
    if now.weekday() == 6 and now.time() >= WEEKLY_AT and state.get("last_weekly") != wk:
        state["last_weekly"] = wk
        save_state(state)
        if state.get("chat_id"):
            await bot.send_message(state["chat_id"], weekly_text(state))


async def scheduler(app: Application):
    """Раз в минуту проверяет, не пора ли отправить сводку (без JobQueue)."""
    while True:
        try:
            await tick(app.bot)
        except Exception:
            logging.exception("Ошибка в планировщике")
        await asyncio.sleep(60)


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запоминаем всех, кто пользуется ботом."""
    state = load_state()
    if touch(state, update.effective_user):
        save_state(state)


async def post_init(app: Application):
    app.bot_data["scheduler"] = asyncio.create_task(scheduler(app))
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
    except Exception:
        logging.exception("Не удалось задать меню команд")


async def post_shutdown(app: Application):
    task = app.bot_data.get("scheduler")
    if task:
        task.cancel()


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(TypeHandler(Update, track), group=-1)
    for name, fn in [
        ("start", start), ("help", help_cmd), ("add", add), ("list", list_tasks),
        ("today", today_cmd), ("del", delete), ("preset", preset),
        ("pin", pin_cmd), ("unpin", unpin_cmd), ("edit", edit_cmd),
        ("walk", walk), ("walks", walks),
        ("buy", buy), ("shopping", shopping), ("bought", bought),
        ("join", join), ("spent", spent), ("balance", balance),
        ("expenses", expenses), ("unspend", unspend), ("settle", settle),
        ("top", top),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^done:"))
    app.add_handler(CallbackQueryHandler(on_today_button, pattern=r"^tdone:"))
    app.add_handler(CallbackQueryHandler(on_skip, pattern=r"^skip:"))
    app.add_handler(CallbackQueryHandler(on_edit, pattern=r"^ed:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, on_reply))
    app.run_polling()


if __name__ == "__main__":
    main()
