"""
Домашний бот для квартиры (Telegram) — расширенная версия для совместного быта.

Задания:   /today /todo /add /list /del /edit /preset /undo /vacation
Питомцы:   /walk /walks /feed /feeds
Покупки:   /buy /shopping /bought
Расходы:   /join /spent /balance /expenses /unspend /settle /history /currency
Рейтинг:   /top  (итоги недели приходят по воскресеньям)
Интерфейс: /pin /unpin /menu /help

Запуск:
  pip install python-telegram-bot
  export BOT_TOKEN="токен от @BotFather"
  export BOT_TZ="Europe/Belgrade"  # или Europe/Moscow
  python chores_bot.py
"""
import asyncio
import calendar
import json
import logging
import os
import random
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

# ---------- НАСТРОЙКИ ----------
TOKEN = os.environ.get("BOT_TOKEN", "")
TZ_NAME = os.environ.get("BOT_TZ", "Europe/Belgrade")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:
    try:
        TZ = ZoneInfo("Europe/Moscow")
    except Exception:
        TZ = timezone(timedelta(hours=2))

REMIND_AT = time(10, 0)     # утренняя сводка
EVENING_AT = time(21, 0)    # вечернее напоминание о несделанных делах
WEEKLY_AT = time(20, 0)     # итоги недели (по воскресеньям)
REPEAT_DAYS = 2             # сколько дней после срока задание остаётся «висеть»
MORNING_MESSAGE_WHEN_PINNED = False  # True: слать отдельное утреннее сообщение даже при закреплённом чек-листе
DEFAULT_CURRENCY = "RSD"    # Валюта по умолчанию (RSD, EUR, RUB, USD)
# --------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
ABBR = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
PREFIX3 = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}

LEVELS = {1: ("🟢", "лёгкая"), 2: ("🟡", "средняя"), 3: ("🔴", "сложная")}

CURRENCY_ALIASES = {
    "rsd": "RSD", "дин": "RSD", "дин.": "RSD", "din": "RSD", "динар": "RSD", "динары": "RSD",
    "eur": "EUR", "евро": "EUR", "€": "EUR", "euro": "EUR",
    "rub": "RUB", "руб": "RUB", "руб.": "RUB", "₽": "RUB", "р": "RUB",
    "usd": "USD", "дол": "USD", "долл": "USD", "$": "USD", "dollar": "USD",
}

PRAISE = [
    "Красавчик!", "Так держать! 💪", "Чистота — залог здоровья ✨", "Респект!",
    "Питомцы гордятся тобой 🐕🐱", "Ещё одно дело в копилку!", "Вот это порядок!",
    "Герой дня! 🏆", "Домашний уют +100! 🏡",
]

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📅 Сегодня"), KeyboardButton("🛒 Покупки")],
        [KeyboardButton("🐕 Прогулка"), KeyboardButton("🥣 Покормили")],
        [KeyboardButton("💸 Баланс"), KeyboardButton("➕ Быстрое дело")],
    ],
    resize_keyboard=True,
)

HELP = (
    "🏡 *ДОМАШНИЙ ПОМОЩНИК*\n\n"
    "📋 *ЗАДАНИЯ:*\n"
    "• `/today` — дела на сегодня (интерактивный чек-лист)\n"
    "• `/todo [дело]` — быстрое разовое дело на сегодня (напр: `/todo Вынести мусор`)\n"
    "• `/add Название | кто | срок | сложность` — добавить регулярное задание\n"
    "• `/list` — все регулярные задания\n"
    "• `/del N` — удалить задание\n"
    "• `/edit` — интерактивная панель настройки заданий\n"
    "• `/undo` — отменить последнее случайное выполнение задания\n"
    "• `/vacation [имя]` — включить/выключить режим «в отпуске» (пропуск очереди)\n"
    "• `/preset` — добавить готовые задания (лоток кота, таблетки собак, корм)\n"
    "• 📌 `/pin` — закрепить чек-лист · `/unpin` — открепить\n\n"
    "🐕🐱 *ПИТОМЦЫ:*\n"
    "• `/walk` — отметить прогулку собак · `/walks` — последние прогулки\n"
    "• `/feed` — отметить кормление (собака / кот, утро / вечер) · `/feeds` — статус\n\n"
    "🛒 *ПОКУПКИ:*\n"
    "• `/buy молоко, хлеб` — добавить в список\n"
    "• `/shopping` — интерактивный список с кнопками для вычеркивания\n"
    "• `/bought [N]` — убрать пункт по номеру или очистить всё\n\n"
    "💸 *РАСХОДЫ И БАЛАНС:*\n"
    "• `/join` — зарегистрироваться в расчете совместных трат\n"
    "• `/spent 1500 [валюта] описание` — записать расход (напр: `/spent 1500 rsd продукты`, `/spent 50 eur ужин`)\n"
    "• `/balance` — баланс и расчет «кто кому должен» по валютам\n"
    "• `/expenses` — последние 10 трат\n"
    "• `/unspend` — отменить свой последний расход\n"
    "• `/settle да` — закрыть расчеты и заархивировать период\n"
    "• `/history` — архив прошлых расчетов\n"
    "• `/currency rsd|eur|rub` — валюта по умолчанию\n\n"
    "🏆 *РЕЙТИНГ И ИТОГИ:*\n"
    "• `/top` — рейтинг по баллам за сложность дел\n"
    "• `/menu` — показать/скрыть нижнюю клавиатуру с кнопками"
)

COMMANDS = [
    ("today", "Дела на сегодня"),
    ("todo", "Быстрое дело на сегодня"),
    ("shopping", "Список покупок"),
    ("walk", "Отметить прогулку собак"),
    ("feed", "Отметить кормление питомцев"),
    ("balance", "Кто кому должен (расходы)"),
    ("spent", "Записать совместный расход"),
    ("undo", "Отменить последнее выполнение"),
    ("vacation", "Режим отпуска (пропуск очереди)"),
    ("edit", "Редактировать задания"),
    ("list", "Все задания"),
    ("add", "Добавить регулярное задание"),
    ("del", "Удалить задание"),
    ("pin", "Закрепить чек-лист"),
    ("top", "Рейтинг и итоги недели"),
    ("menu", "Быстрое меню кнопок"),
    ("help", "Справка по всем командам"),
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
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logging.error("Ошибка чтения state.json, создаём резервную копию: %s", e)
            backup_file = STATE_FILE.with_suffix(".json.bak")
            STATE_FILE.rename(backup_file)
            state = {"chat_id": None}

    if "tasks" not in state:
        state["tasks"] = default_tasks()
        state["next_id"] = 4

    for key, default in (
        ("members", {}),
        ("log", []),
        ("walks", []),
        ("feeds", []),
        ("shopping", []),
        ("expenses", []),
        ("settlements_history", []),
        ("vacations", []),
        ("last_morning", None),
        ("last_evening", None),
        ("last_weekly", None),
        ("pinned", None),
        ("last_done", None),
        ("default_currency", DEFAULT_CURRENCY),
    ):
        state.setdefault(key, default)

    if not state.get("_no_utilities"):
        for tid in [k for k, t in state["tasks"].items() if t["title"] == "💧 Передать показания счётчиков"]:
            del state["tasks"][tid]
        state["_no_utilities"] = True

    return state


def save_state(state: dict) -> None:
    """Атомарное сохранение через временный файл."""
    tmp_file = STATE_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_file, STATE_FILE)


def user_name(user) -> str:
    if not user:
        return "Неизвестный"
    return user.first_name or user.username or str(user.id)


def touch(state: dict, user) -> bool:
    """Запоминаем участников (для расходов и рейтинга)."""
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
            anchor += timedelta(days=1)
        return {"type": "interval", "every": n, "anchor": anchor.isoformat()}

    date_m = re.search(r"\b(\d{1,2})\.(\d{1,2})\b", t)
    if re.search(r"кажд\w*\s+год|раз\s+в\s+год|ежегодно", t):
        if not date_m:
            raise ValueError("Укажите дату: «раз в год 15.03».\nФормат: 15.03")
        day, month = int(date_m.group(1)), int(date_m.group(2))
        try:
            date(2001, month, min(day, 28))
        except ValueError:
            raise ValueError("Не понял дату. Формат: 15.03")
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
        raise ValueError("Укажите число месяца: «раз в 3 месяца 10 числа».\nЧисло от 1 до 31.")
    if day_m:
        day = int(day_m.group(1))
        if not 1 <= day <= 31:
            raise ValueError("Число месяца должно быть от 1 до 31.")
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
    """Последняя дата срока, которая уже наступила (или None). Безопасно для 29-31 чисел."""
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
        max_days_today = calendar.monthrange(today.year, today.month)[1]
        effective_day = min(day, max_days_today)
        idx = month_index(today) - (0 if today.day >= effective_day else 1)
        if every > 1:
            start = s.get("start", idx)
            if idx < start:
                return None
            idx = start + (idx - start) // every * every
        year, month_0 = divmod(idx, 12)
        month = month_0 + 1
        max_days = calendar.monthrange(year, month)[1]
        return date(year, month, min(day, max_days))
    if t == "yearly":
        max_days_curr = calendar.monthrange(today.year, s["month"])[1]
        d_curr = date(today.year, s["month"], min(s["day"], max_days_curr))
        if d_curr <= today:
            return d_curr
        max_days_prev = calendar.monthrange(today.year - 1, s["month"])[1]
        return date(today.year - 1, s["month"], min(s["day"], max_days_prev))
    d = date.fromisoformat(s["date"])
    return d if d <= today else None


def current_owner(task: dict, state: dict = None) -> str:
    """Учитывает режим отпуска: если текущий дежурный в отпуске, пропускает к следующему."""
    owners = task.get("owners", [])
    if not owners:
        return "Никто"
    idx = task.get("idx", 0) % len(owners)
    if not state or not state.get("vacations"):
        return owners[idx]
    vacations = set(state.get("vacations", []))
    for i in range(len(owners)):
        candidate = owners[(idx + i) % len(owners)]
        if candidate not in vacations:
            return candidate
    return f"{owners[idx]} (все в отпуске 🏖)"


def ordered(state: dict) -> list:
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
    """Отмечает задачу выполненной с поддержкой Undo."""
    task = state["tasks"].get(tid)
    if not task:
        return None
    if task["done_for"] == occ_iso:
        return False

    prev_done_for = task.get("done_for")
    prev_idx = task.get("idx", 0)

    task["done_for"] = occ_iso
    task["idx"] = (prev_idx + 1) % len(task["owners"])

    pts = level_of(task)
    log_done(state, who, task["title"], pts)

    # Сохраняем последнее действие для отмены
    state["last_done"] = {
        "tid": tid,
        "occ_iso": occ_iso,
        "who": who,
        "prev_done_for": prev_done_for,
        "prev_idx": prev_idx,
        "pts": pts,
        "title": task["title"],
        "at": datetime.now(TZ).isoformat(),
    }
    return True


def undo_last_done(state: dict) -> tuple[bool, str]:
    """Отменяет последнее выполнение задачи."""
    ld = state.get("last_done")
    if not ld:
        return False, "Нет действий для отмены."

    tid = ld["tid"]
    task = state["tasks"].get(tid)
    if task:
        task["done_for"] = ld["prev_done_for"]
        task["idx"] = ld["prev_idx"]

    if state["log"]:
        for i in range(len(state["log"]) - 1, -1, -1):
            entry = state["log"][i]
            if entry["task"] == ld["title"] and entry["who"] == ld["who"]:
                state["log"].pop(i)
                break

    title = ld["title"]
    state["last_done"] = None
    return True, f"↩️ Отменено выполнение: «{title}» ({ld['who']})"


def fmt_money(x: float) -> str:
    return str(round(x)) if abs(x - round(x)) < 0.005 else f"{x:.2f}"


# ---------- питомцы: статус на сегодня ----------
def get_pets_status(state: dict) -> str:
    today_str = today_local().isoformat()

    # Прогулки сегодня
    today_walks = [w for w in state.get("walks", []) if w["at"][:10] == today_str]
    walk_info = f"{len(today_walks)} раз(а)" if today_walks else "ещё не гуляли"
    if today_walks:
        last_w = today_walks[-1]
        at_time = datetime.fromisoformat(last_w["at"]).strftime("%H:%M")
        walk_info += f" (посл: {at_time} — {last_w['who']})"

    # Кормление сегодня
    feeds_today = [f for f in state.get("feeds", []) if f.get("date") == today_str]
    dog_morning = next((f for f in feeds_today if "собак" in f["pet"].lower() and "утро" in f["slot"].lower()), None)
    dog_evening = next((f for f in feeds_today if "собак" in f["pet"].lower() and "вечер" in f["slot"].lower()), None)
    cat_morning = next((f for f in feeds_today if "кот" in f["pet"].lower() and "утро" in f["slot"].lower()), None)
    cat_evening = next((f for f in feeds_today if "кот" in f["pet"].lower() and "вечер" in f["slot"].lower()), None)

    feed_parts = []
    feed_parts.append(f"🐶 Собака: {'✅' if dog_morning else '⬜'}/утро, {'✅' if dog_evening else '⬜'}/вечер")
    feed_parts.append(f"🐱 Кот: {'✅' if cat_morning else '⬜'}/утро, {'✅' if cat_evening else '⬜'}/вечер")

    return f"🐕 Прогулки: {walk_info}\n🥣 Кормление: {', '.join(feed_parts)}"


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

    count = l
