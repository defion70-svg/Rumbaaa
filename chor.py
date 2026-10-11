# -*- coding: utf-8 -*-
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
  python chor.py
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

    count = len(pending) + len(upcoming)
    label = f"{DAY_NAMES[today.weekday()]}, {today:%d.%m}"

    lines = [f"📅 *На сегодня ({label}):*"]

    # Статус отпусков
    if state.get("vacations"):
        lines.append(f"🏖 В отпуске: {', '.join(state['vacations'])}")

    # Статус питомцев
    lines.append(get_pets_status(state))
    lines.append("")

    buttons = []
    if not pending and not done and not upcoming:
        lines.append("🎉 Все дела сделаны, порядок!")
    else:
        for tid, t, occ, late in pending:
            suffix = "" if late == 0 else " ⏰ не сделано раньше"
            owner = current_owner(t, state)
            lines.append(f"⬜ {t['title']} {level_emoji(t)} — {owner}{suffix}")
            row = [InlineKeyboardButton(f"✅ {t['title'][:22]}", callback_data=f"tdone:{tid}:{occ.isoformat()}")]
            if len(t["owners"]) > 1:
                row.append(InlineKeyboardButton("⏭ Сдать очередь", callback_data=f"skip:{tid}:{occ.isoformat()}"))
            buttons.append(row)

        for t in done:
            lines.append(f"✅ {t['title']}")

        if upcoming:
            lines.append("")
            for adv, t in upcoming:
                when = "Завтра" if adv == 1 else f"Через {adv} дн."
                owner = current_owner(t, state)
                lines.append(f"🔔 {when}: {t['title']} — {owner}")

    # Кнопка отмены последнего действия, если оно было
    if state.get("last_done"):
        buttons.append([InlineKeyboardButton(f"↩️ Отменить «{state['last_done']['title'][:18]}»", callback_data="tundo")])

    return "\n".join(lines), (InlineKeyboardMarkup(buttons) if buttons else None), count


# ---------- команды: задания ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    touch(state, update.effective_user)
    if update.effective_chat.type in ("group", "supergroup") or not state.get("chat_id"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "Привет! Я бот для совместного быта. Напоминания и сводки будут приходить сюда.\n\n" + HELP,
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📱 Главное меню кнопок обновлено:", reply_markup=MAIN_KEYBOARD)


def build_task(parts: list) -> dict:
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
        await update.message.reply_text(HELP, parse_mode="Markdown")
        return
    try:
        task = build_task([p.strip() for p in parts_raw[1].split("|")])
    except ValueError as e:
        await update.message.reply_text(f"{e}\n\n{HELP}", parse_mode="Markdown")
        return
    state = load_state()
    add_task(state, task)
    if not state.get("chat_id") or update.effective_chat.type in ("group", "supergroup"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update_pinned(context.bot, state)
    emoji, name = LEVELS[task["level"]]
    await update.message.reply_text(
        f"Добавлено №{len(state['tasks'])}: {task['title']}\n"
        f"Кто: {' → '.join(task['owners'])}\nСрок: {describe(task['schedule'])}\n"
        f"Сложность: {emoji} {name}"
    )


async def todo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрое разовое дело на сегодня."""
    if not context.args:
        await update.message.reply_text(
            "Напишите дело на сегодня:\n"
            "Пример: `/todo Забрать посылку`\n"
            "Или с указанием исполнителя: `/todo Помыть обувь | Алина`",
            parse_mode="Markdown",
        )
        return
    raw = " ".join(context.args)
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|", 1)]
        title, who = parts[0], parts[1]
    else:
        title = raw.strip()
        who = user_name(update.effective_user)

    state = load_state()
    today = today_local()
    task = {
        "title": f"📌 {title}",
        "owners": [who],
        "idx": 0,
        "schedule": {"type": "once", "date": today.isoformat()},
        "done_for": None,
        "level": 1,
    }
    add_task(state, task)
    save_state(state)
    await update_pinned(context.bot, state)
    await update.message.reply_text(f"✅ Добавлено дело на сегодня: {title} — {who}")


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["tasks"]:
        await update.message.reply_text("Заданий пока нет. Добавьте через /add или /todo")
        return
    today = today_local()
    lines = ["📋 *Задания:*"]
    for pos, (tid, t) in enumerate(ordered(state), 1):
        occ = occurrence(t["schedule"], today)
        if occ and (today - occ).days <= window(t):
            mark = "✅" if t["done_for"] == occ.isoformat() else "⬜"
        else:
            mark = "▫️"
        owner = current_owner(t, state)
        lines.append(f"{pos}. {mark} {t['title']} {level_emoji(t)} — {owner} ({describe(t['schedule'])})")
    lines.append("\n⬜ ждёт, ✅ сделано, ▫️ срок ещё не наступил. Удалить: /del N")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    ok, msg = undo_last_done(state)
    if ok:
        save_state(state)
        await update_pinned(context.bot, state)
    await update.message.reply_text(msg)


async def vacation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключает статус «в отпуске» для пользователя."""
    state = load_state()
    vacations = state.setdefault("vacations", [])
    who = " ".join(context.args).strip() if context.args else user_name(update.effective_user)

    if who in vacations:
        vacations.remove(who)
        msg = f"🏖 {who} возвращается из отпуска! Снова в очереди домашних дел."
    else:
        vacations.append(who)
        msg = f"🏖 {who} теперь в отпуске! Дела автоматически переходят другим участникам."

    save_state(state)
    await update_pinned(context.bot, state)
    await update.message.reply_text(msg)


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, kb, _ = build_today(load_state())
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    ready = [
        ("🐕 Таблетки от блох и клещей", ["Мы с женой"], "1 числа за день", 1),
        ("🥫 Проверить корм собак и кошек", ["Мы с женой"], "каждые 14 дней", 1),
        ("🐱 Почистить кошачий лоток", ["Мы с женой"], "каждые 2 дня", 1),
        ("💉 Прививки питомцев", ["Мы с женой"], "раз в год 15.03 за 7 дней", 2),
        ("🩺 Осмотр у ветеринара", ["Мы с женой"], "раз в год 10.09 за 7 дней", 2),
    ]
    have = {t["title"] for t in state["tasks"].values()}
    added = []
    for title, owners, when, lv in ready:
        if title in have:
            continue
        tid = str(state["next_id"])
        state["next_id"] += 1
        state["tasks"][tid] = {
            "title": title, "owners": owners, "idx": 0,
            "schedule": parse_schedule(when), "done_for": None, "level": lv,
        }
        added.append(title)
    if not state.get("chat_id") or update.effective_chat.type in ("group", "supergroup"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update_pinned(context.bot, state)
    text = ("Добавлены готовые задания для питомцев:\n• " + "\n• ".join(added)) if added else "Все готовые задания уже добавлены."
    await update.message.reply_text(text)


# ---------- кнопки выполнения заданий ----------
async def refresh_today(query, state):
    text, kb, _ = build_today(state)
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
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


async def on_undo_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    state = load_state()
    ok, msg = undo_last_done(state)
    if ok:
        save_state(state)
        await query.answer(msg)
    else:
        await query.answer(msg, show_alert=True)
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
        await query.answer(f"Передано: теперь {current_owner(task, state)}")
    else:
        await query.answer("Передать некому")
    await refresh_today(query, state)
    await update_pinned(context.bot, state, skip_id=query.message.message_id)


# ---------- собаки и кошки ----------
async def walk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    now = datetime.now(TZ)
    who = user_name(update.effective_user)
    state["walks"].append({"who": who, "at": now.isoformat(timespec="minutes")})
    del state["walks"][:-200]
    save_state(state)
    await update_pinned(context.bot, state)
    await update.message.reply_text(f"🐕 {who} выгулял(а) собак в {now:%H:%M} ✅")


async def walks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["walks"]:
        await update.message.reply_text("Прогулок пока не отмечено. Отметьте через /walk")
        return
    lines = ["🐕 *Последние прогулки собак:*"]
    for w in state["walks"][-6:][::-1]:
        at = datetime.fromisoformat(w["at"])
        lines.append(f"• {at:%d.%m %H:%M} — {w['who']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def build_feed_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🐶 Собака: 🥣 Завтрак", callback_data="fd:собака:утро"),
            InlineKeyboardButton("🐶 Собака: 🥣 Ужин", callback_data="fd:собака:вечер"),
        ],
        [
            InlineKeyboardButton("🐱 Кот: 🥣 Завтрак", callback_data="fd:кот:утро"),
            InlineKeyboardButton("🐱 Кот: 🥣 Ужин", callback_data="fd:кот:вечер"),
        ],
    ])


async def feed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Интерактивное меню кормления или быстрая отметка."""
    state = load_state()
    if context.args:
        pet_arg = context.args[0].lower()
        pet = "🐶 Собака" if "соб" in pet_arg or "dog" in pet_arg else "🐱 Кот"
        slot = "☀️ Утро" if datetime.now(TZ).hour < 15 else "🌙 Вечер"
        who = user_name(update.effective_user)
        today_str = today_local().isoformat()
        state["feeds"].append({
            "pet": pet, "slot": slot, "who": who,
            "at": datetime.now(TZ).isoformat(timespec="minutes"), "date": today_str,
        })
        save_state(state)
        await update_pinned(context.bot, state)
        await update.message.reply_text(f"🥣 {who} покормил(а): {pet} ({slot}) ✅")
        return

    text = "🥣 *Кто и кого покормил?*\n\n" + get_pets_status(state)
    await update.message.reply_text(text, reply_markup=build_feed_keyboard(), parse_mode="Markdown")


async def feeds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(f"🥣 *Статус кормления и прогулок на сегодня:*\n\n{get_pets_status(state)}", parse_mode="Markdown")


async def on_feed_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, pet_key, slot_key = query.data.split(":")
    state = load_state()
    who = user_name(query.from_user)
    today_str = today_local().isoformat()

    pet = "🐶 Собака" if pet_key == "собака" else "🐱 Кот"
    slot = "☀️ Утро" if slot_key == "утро" else "🌙 Вечер"

    state["feeds"].append({
        "pet": pet, "slot": slot, "who": who,
        "at": datetime.now(TZ).isoformat(timespec="minutes"), "date": today_str,
    })
    del state["feeds"][:-500]
    save_state(state)
    await update_pinned(context.bot, state)
    await query.answer(f"Записано: {pet} накормлен(а) ({who})!")
    try:
        await query.edit_message_text(
            f"🥣 *Кормление обновлено!*\n\n{get_pets_status(state)}",
            reply_markup=build_feed_keyboard(),
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ---------- покупки (интерактивный UX) ----------
def build_shopping_view(state: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    items = state.get("shopping", [])
    if not items:
        return "🛒 *Список покупок пуст.*\nДобавить: `/buy молоко, хлеб`", None

    lines = ["🛒 *Список покупок:* (нажмите, чтобы вычеркнуть)"]
    buttons = []
    has_done = False

    for idx, it in enumerate(items):
        is_done = it.get("done", False)
        if is_done:
            has_done = True
            lines.append(f"~{idx + 1}. {it['item']} ({it['by']})~ ✅")
            btn_text = f"✅ {it['item'][:24]}"
        else:
            lines.append(f"{idx + 1}. {it['item']} ({it['by']})")
            btn_text = f"⬜ {it['item'][:24]}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"shop:tgl:{idx}")])

    ctrl_row = []
    if has_done:
        ctrl_row.append(InlineKeyboardButton("🧹 Убрать купленные", callback_data="shop:cleardone"))
    ctrl_row.append(InlineKeyboardButton("🗑 Очистить всё", callback_data="shop:clearall"))
    buttons.append(ctrl_row)

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split(None, 1)
    if len(parts) < 2:
        await update.message.reply_text("Напишите, что купить: `/buy молоко, сыр, хлеб`", parse_mode="Markdown")
        return
    items = [i.strip() for i in re.split(r"[,\\n]", parts[1]) if i.strip()]
    state = load_state()
    who = user_name(update.effective_user)
    for item in items:
        state["shopping"].append({"item": item, "by": who, "done": False})
    save_state(state)
    await update.message.reply_text(f"🛒 Добавлено ({len(items)} шт.): {', '.join(items)}\nВсего в списке: {len(state['shopping'])}")


async def shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text, kb = build_shopping_view(state)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def on_shopping_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1]
    state = load_state()

    if action == "tgl":
        idx = int(parts[2])
        if 0 <= idx < len(state["shopping"]):
            cur = state["shopping"][idx].get("done", False)
            state["shopping"][idx]["done"] = not cur
            save_state(state)
            await query.answer("Куплено ✅" if not cur else "Возвращено в список ⬜")
        else:
            await query.answer("Этого пункта уже нет")
    elif action == "cleardone":
        state["shopping"] = [it for it in state["shopping"] if not it.get("done", False)]
        save_state(state)
        await query.answer("Купленные товары удалены!")
    elif action == "clearall":
        state["shopping"] = []
        save_state(state)
        await query.answer("Список полностью очищен!")

    text, kb = build_shopping_view(state)
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass


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


# ---------- расходы и мультивалютность ----------
def parse_expense_input(args: list[str], default_curr: str) -> tuple[float, str, str]:
    """Парсит сумму, валюту и описание: /spent 1500 rsd продукты или /spent 50 eur ужин."""
    if not args:
        raise ValueError("Укажите сумму: `/spent 1500 rsd продукты`")

    amt_str = args[0].replace(",", ".")
    try:
        amount = float(amt_str)
        if amount <= 0:
            raise ValueError
    except ValueError:
        raise ValueError("Сумма должна быть положительным числом, например: `/spent 1500 rsd продукты`")

    curr = default_curr
    desc_start = 1

    if len(args) > 1:
        cand_curr = args[1].lower().rstrip(".")
        if cand_curr in CURRENCY_ALIASES:
            curr = CURRENCY_ALIASES[cand_curr]
            desc_start = 2

    desc = " ".join(args[desc_start:]) or "без описания"
    return amount, curr, desc


def compute_balance_by_currency(state: dict):
    """Считает долги отдельно для каждой валюты."""
    members = dict(state["members"])
    for e in state["expenses"]:
        members.setdefault(e["payer_id"], e["payer"])

    currencies = sorted(list({e.get("currency", state.get("default_currency", "RSD")) for e in state["expenses"]}))
    result = {}

    for curr in currencies:
        curr_expenses = [e for e in state["expenses"] if e.get("currency", state.get("default_currency", "RSD")) == curr]
        total = sum(e["amount"] for e in curr_expenses)
        transfers = []
        if curr_expenses and len(members) >= 2:
            share = total / len(members)
            net = {uid: -share for uid in members}
            for e in curr_expenses:
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
        result[curr] = {"total": total, "transfers": transfers}

    return members, result


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    touch(state, update.effective_user)
    save_state(state)
    names = ", ".join(state["members"].values())
    await update.message.reply_text(f"👋 Вы в списке участников расходов ({len(state['members'])}):\n{names}")


async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not context.args:
        cur = state.get("default_currency", DEFAULT_CURRENCY)
        await update.message.reply_text(f"Текущая валюта по умолчанию: *{cur}*.\nЧтобы изменить: `/currency eur` или `/currency rsd`", parse_mode="Markdown")
        return
    new_c = context.args[0].lower().rstrip(".")
    if new_c in CURRENCY_ALIASES:
        target = CURRENCY_ALIASES[new_c]
        state["default_currency"] = target
        save_state(state)
        await update.message.reply_text(f"✅ Валюта по умолчанию установлена: *{target}*", parse_mode="Markdown")
    else:
        await update.message.reply_text("Неизвестная валюта. Доступны: RSD, EUR, RUB, USD.")


async def spent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    def_curr = state.get("default_currency", DEFAULT_CURRENCY)
    try:
        amount, curr, desc = parse_expense_input(context.args, def_curr)
    except ValueError as e:
        await update.message.reply_text(str(e), parse_mode="Markdown")
        return

    user = update.effective_user
    touch(state, user)
    state["expenses"].append({
        "payer_id": str(user.id),
        "payer": user_name(user),
        "amount": amount,
        "currency": curr,
        "desc": desc,
        "date": today_local().isoformat(),
    })
    save_state(state)

    text = f"💸 *{user_name(user)}* заплатил(а) *{fmt_money(amount)} {curr}* — {desc}"
    if len(state["members"]) < 2:
        text += "\n\n⚠️ Пока в учёте один участник. Пусть остальные отправят `/join`."
    await update.message.reply_text(text, parse_mode="Markdown")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["expenses"]:
        await update.message.reply_text("Расходов пока нет. Запишите: `/spent 1500 rsd продукты`", parse_mode="Markdown")
        return

    members, results = compute_balance_by_currency(state)
    lines = [
        f"👥 *Участники ({len(members)}):* {', '.join(members.values())}",
        "",
    ]

    for curr, data in results.items():
        total = data["total"]
        transfers = data["transfers"]
        lines.append(f"💰 *Валюта {curr}:* всего потрачено *{fmt_money(total)} {curr}* (по {fmt_money(total / max(1, len(members)))} на человека)")
        if len(members) < 2:
            lines.append("Нужно минимум двое участников: пусть отправят `/join`.")
        elif not transfers:
            lines.append("Все в расчёте 🤝")
        else:
            lines.append("Кто кому переводит:")
            for debtor, creditor, amt in transfers:
                lines.append(f"  • {debtor} → {creditor}: *{fmt_money(amt)} {curr}*")
        lines.append("")

    lines.append("Когда рассчитаетесь: `/settle да`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["expenses"]:
        await update.message.reply_text("Расходов пока нет.")
        return
    def_curr = state.get("default_currency", DEFAULT_CURRENCY)
    lines = ["🧾 *Последние расходы:*"]
    for e in state["expenses"][-10:][::-1]:
        d = date.fromisoformat(e["date"])
        curr = e.get("currency", def_curr)
        lines.append(f"• {d:%d.%m} *{e['payer']}*: {fmt_money(e['amount'])} {curr} — {e['desc']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def unspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    uid = str(update.effective_user.id)
    def_curr = state.get("default_currency", DEFAULT_CURRENCY)
    for i in range(len(state["expenses"]) - 1, -1, -1):
        if state["expenses"][i]["payer_id"] == uid:
            e = state["expenses"].pop(i)
            save_state(state)
            curr = e.get("currency", def_curr)
            await update.message.reply_text(f"↩️ Удалён ваш расход: {fmt_money(e['amount'])} {curr} — {e['desc']}")
            return
    await update.message.reply_text("У вас нет записанных расходов.")


async def settle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("да", "yes"):
        await update.message.reply_text(
            "Это закроет текущие расходы и сохранит их в архив.\n"
            "Если все рассчитались, отправьте: `/settle да`",
            parse_mode="Markdown",
        )
        return
    state = load_state()
    if not state["expenses"]:
        await update.message.reply_text("Нет активных расходов для расчёта.")
        return

    members, results = compute_balance_by_currency(state)
    archive_entry = {
        "date": today_local().isoformat(),
        "by": user_name(update.effective_user),
        "expenses_count": len(state["expenses"]),
        "totals": {curr: d["total"] for curr, d in results.items()},
    }
    state.setdefault("settlements_history", []).append(archive_entry)
    n = len(state["expenses"])
    state["expenses"] = []
    save_state(state)
    await update.message.reply_text(f"🤝 Все в расчёте! Записей перенесено в архив: {n}. Начинаем с нуля!")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    history = state.get("settlements_history", [])
    if not history:
        await update.message.reply_text("Архив расчётов пока пуст.")
        return
    lines = ["📜 *История закрытых расчётов:*"]
    for idx, h in enumerate(history[-5:][::-1], 1):
        d = date.fromisoformat(h["date"])
        totals_s = ", ".join(f"{fmt_money(amt)} {cur}" for cur, amt in h.get("totals", {}).items())
        lines.append(f"{idx}. {d:%d.%m.%Y} — закрыл(а) {h['by']} ({h['expenses_count']} трат, сумма: {totals_s or '0'})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
        return "📊 *Итоги недели:* пока ничего не отмечено."
    lines = ["📊 *Итоги недели (баллы за сложность):*"]
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
        text += "\n\n*За всё время:*\n" + "\n".join(f"• {w} — {n} б. ({c} ✅)" for w, n, c in total)
    await update.message.reply_text(text, parse_mode="Markdown")


# ---------- панель редактирования заданий ----------
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
        f"Кто: {' → '.join(t['owners'])} (сейчас: {current_owner(t, state)})\n"
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
        toast = f"Теперь очередь: {current_owner(t, state)}"
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
            else:
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
    for mid in (pend["prompt_id"], msg.message_id):
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
        await bot.edit_message_text(text, chat_id=p["chat_id"], message_id=p["message_id"], reply_markup=kb, parse_mode="Markdown")
    except BadRequest as e:
        err = str(e).lower()
        if "message to edit not found" in err or "message is not modified" in err:
            if "not found" in err:
                state["pinned"] = None
                save_state(state)
        else:
            logging.warning("Ошибка редактирования закрепленного сообщения: %s", e)
    except Exception as e:
        logging.warning("Не удалось обновить закреплённое сообщение: %s", e)


async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    chat_id = update.effective_chat.id
    old = state.get("pinned")
    if old:
        try:
            await context.bot.unpin_chat_message(old["chat_id"], old["message_id"])
        except Exception:
            pass
    text, kb, _ = build_today(state)
    msg = await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")
    state["pinned"] = {"chat_id": chat_id, "message_id": msg.message_id}
    if not state.get("chat_id") or update.effective_chat.type in ("group", "supergroup"):
        state["chat_id"] = chat_id
    save_state(state)
    try:
        await context.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        await update.message.reply_text("📌 Чек-лист закреплён. Он обновляется автоматически!")
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
    for tid, t in list(state["tasks"].items()):
        s = t["schedule"]
        if s["type"] == "once" and (today - date.fromisoformat(s["date"])).days > REPEAT_DAYS:
            del state["tasks"][tid]
    await update_pinned(bot, state)
    if state.get("pinned") and not MORNING_MESSAGE_WHEN_PINNED:
        return
    text, kb, count = build_today(state)
    if count and state.get("chat_id"):
        await bot.send_message(state["chat_id"], "☀️ *Доброе утро!*\n" + text, reply_markup=kb, parse_mode="Markdown")


async def run_evening(bot, state: dict):
    """Вечернее напоминание, если остались невыполненные дела."""
    today = today_local()
    pending = []
    for tid, t in ordered(state):
        s = t["schedule"]
        occ = occurrence(s, today)
        if occ is not None and (today - occ).days <= window(t):
            if t["done_for"] != occ.isoformat():
                pending.append((tid, t))

    if pending and state.get("chat_id"):
        lines = ["🌙 *Вечернее напоминание:* ещё остались незавершённые дела на сегодня:"]
        for tid, t in pending:
            lines.append(f"• {t['title']} — {current_owner(t, state)}")
        lines.append("\nОтметьте выполненное в `/today` или в закреплённом чек-листе!")
        await bot.send_message(state["chat_id"], "\n".join(lines), parse_mode="Markdown")


async def tick(bot):
    state = load_state()
    now = datetime.now(TZ)
    today = now.date().isoformat()

    # Утренняя сводка
    if REMIND_AT <= now.time() < time(22, 0) and state.get("last_morning") != today:
        state["last_morning"] = today
        save_state(state)
        await run_morning(bot, state)
        save_state(state)

    # Вечернее напоминание
    if EVENING_AT <= now.time() < time(23, 0) and state.get("last_evening") != today:
        state["last_evening"] = today
        save_state(state)
        await run_evening(bot, state)
        save_state(state)

    # Воскресные итоги недели
    wk = week_key(now.date())
    if now.weekday() == 6 and now.time() >= WEEKLY_AT and state.get("last_weekly") != wk:
        state["last_weekly"] = wk
        save_state(state)
        if state.get("chat_id"):
            await bot.send_message(state["chat_id"], weekly_text(state), parse_mode="Markdown")


async def scheduler(app: Application):
    while True:
        try:
            await tick(app.bot)
        except Exception:
            logging.exception("Ошибка в планировщике")
        await asyncio.sleep(60)


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if touch(state, update.effective_user):
        save_state(state)


async def on_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки нижней клавиатуры."""
    text = (update.message.text or "").strip()
    if text == "📅 Сегодня":
        await today_cmd(update, context)
    elif text == "🛒 Покупки":
        await shopping(update, context)
    elif text == "🐕 Прогулка":
        await walk(update, context)
    elif text == "🥣 Покормили":
        await feed_cmd(update, context)
    elif text == "💸 Баланс":
        await balance(update, context)
    elif text == "➕ Быстрое дело":
        await update.message.reply_text("Отправьте дело: `/todo Забрать посылку`", parse_mode="Markdown")


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
    if not TOKEN:
        raise ValueError("Переменная окружения BOT_TOKEN не задана!")

    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(TypeHandler(Update, track), group=-1)

    for name, fn in [
        ("start", start), ("help", help_cmd), ("menu", menu_cmd),
        ("add", add), ("todo", todo_cmd), ("list", list_tasks),
        ("today", today_cmd), ("del", delete), ("undo", undo_cmd),
        ("preset", preset), ("vacation", vacation_cmd),
        ("pin", pin_cmd), ("unpin", unpin_cmd), ("edit", edit_cmd),
        ("walk", walk), ("walks", walks),
        ("feed", feed_cmd), ("feeds", feeds_cmd),
        ("buy", buy), ("shopping", shopping), ("bought", bought),
        ("join", join), ("spent", spent), ("balance", balance),
        ("currency", currency_cmd), ("history", history_cmd),
        ("expenses", expenses), ("unspend", unspend), ("settle", settle),
        ("top", top),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_today_button, pattern=r"^tdone:"))
    app.add_handler(CallbackQueryHandler(on_undo_button, pattern=r"^tundo$"))
    app.add_handler(CallbackQueryHandler(on_skip, pattern=r"^skip:"))
    app.add_handler(CallbackQueryHandler(on_feed_button, pattern=r"^fd:"))
    app.add_handler(CallbackQueryHandler(on_shopping_button, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(on_edit, pattern=r"^ed:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, on_reply))

    # Обработчик кнопок нижней клавиатуры
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^(📅 Сегодня|🛒 Покупки|🐕 Прогулка|🥣 Покормили|💸 Баланс|➕ Быстрое дело)$"),
        on_menu_text,
    ))

    app.run_polling()


if __name__ == "__main__":
    main()
