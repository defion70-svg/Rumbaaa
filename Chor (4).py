# -*- coding: utf-8 -*-
# Версия: 17 (полная сборка)
"""
Домашний бот для квартиры (Telegram) — совместный быт и задания.

Задания:   /today /todo /add /list /del /edit /preset /undo /vacation
Покупки:   /buy /shopping /bought
Рейтинг:   /top  (итоги недели приходят по воскресеньям)
Интерфейс: /info /alice /menu /pin /unpin /help

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
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
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
        TZ = timezone(timedelta(hours=1))

BOT_START_DATE = date(2026, 10, 10)  # Дата начала работы (10.10): сроки до этой даты не считаются просроченными
REMIND_AT = time(8, 0)     # утренняя сводка
EVENING_AT = time(23, 0)    # вечернее напоминание о несделанных делах
WEEKLY_AT = time(23, 0)     # итоги недели (по воскресеньям)
MORNING_MESSAGE_WHEN_PINNED = True  # True: слать отдельное утреннее сообщение даже при закреплённом чек-листе
SILENT_MESSAGES = True              # True: отправлять ВСЕ сообщения бота без звука и вибрации (тихие уведомления)
ALICE_HOST = os.environ.get("ALICE_HOST", "0.0.0.0")
ALICE_PORT = int(os.environ.get("PORT") or os.environ.get("ALICE_PORT", "8080"))  # Порт вебхука для Яндекс.Алисы (0 — выключить)
ALICE_PATH = os.environ.get("ALICE_PATH", "/alice")     # Путь вебхука (например: https://ваш-домен/alice)
ALICE_SECRET = os.environ.get("ALICE_SECRET", "")       # Опциональный секретный токен в URL (?token=...)
# --------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
ABBR = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
PREFIX3 = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}

PRAISE = [
    "Красавчик!", "Так держать! 💪", "Чистота — залог здоровья ✨", "Респект!",
    "Ещё одно дело в копилку!", "Вот это порядок!", "Герой дня! 🏆", "Домашний уют +100! 🏡",
]

# Нижняя панель кнопок отключена: передаём ReplyKeyboardRemove(), чтобы Telegram скрыл старую клавиатуру у всех в чате
REMOVE_KEYBOARD = ReplyKeyboardRemove()
MAIN_KEYBOARD = REMOVE_KEYBOARD
SHOPPING_KEYBOARD = REMOVE_KEYBOARD

# Инлайн-панель по /menu
INLINE_MENU = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📅 Сегодня", callback_data="menu:today"),
        InlineKeyboardButton("⚡ Быстрое дело", callback_data="menu:todo"),
    ],
    [
        InlineKeyboardButton("➕ Добавить дело", callback_data="menu:add"),
        InlineKeyboardButton("📋 Все задания", callback_data="menu:list"),
    ],
    [
        InlineKeyboardButton("✏️ Редактировать", callback_data="ed:list"),
        InlineKeyboardButton("📊 Итоги", callback_data="menu:top"),
    ],
    [
        InlineKeyboardButton("🛒 Список покупок", callback_data="menu:shop"),
        InlineKeyboardButton("ℹ️ Инфо квартиры", callback_data="menu:info"),
    ],
])

HELP = (
    "🏡 *ДОМАШНИЙ ПОМОЩНИК*\n\n"
    "📋 *ЗАДАНИЯ:*\n"
    "• `/today` — дела на сегодня (интерактивный чек-лист)\n"
    "• `/todo [дело]` — быстрое разовое дело на сегодня\n"
    "• `/add Название | кто | срок` — добавить регулярное задание\n"
    "• `/list` — все регулярные задания\n"
    "• `/del N` — удалить задание\n"
    "• `/edit` — панель настройки и редактирования заданий\n"
    "• `/undo` — отменить последнее случайное выполнение задания\n"
    "• `/vacation [имя]` — режим «в отпуске» (пропуск своей очереди)\n"
    "• `/preset` — добавить готовые базовые задания (уборка, бельё)\n"
    "• 📌 `/pin` — закрепить чек-лист · `/unpin` — открепить\n\n"
    "🛒 *ПОКУПКИ:*\n"
    "• `/buy молоко, хлеб` — добавить в список покупок\n"
    "• `/shopping` — интерактивный список с кнопками для вычеркивания\n"
    "• `/bought [N]` — убрать пункт по номеру или очистить всё\n\n"
    "📊 *ИТОГИ И ИНФО:*\n"
    "• `/top` — список выполненных задач по каждому человеку за неделю\n"
    "• `/info` — инфо-карточка квартиры (счета, Wi-Fi, домофон, контакты)\n"
    "• `/menu` — кнопочная панель управления"
)

COMMANDS = [
    ("today", "Дела на сегодня"),
    ("todo", "Быстрое дело на сегодня"),
    ("add", "Добавить регулярное задание"),
    ("list", "Все задания"),
    ("edit", "Редактировать задания"),
    ("menu", "Кнопочная панель управления"),
    ("shopping", "Список покупок"),
    ("buy", "Добавить в покупки"),
    ("bought", "Убрать из покупок"),
    ("undo", "Отменить последнее выполнение"),
    ("vacation", "Режим отпуска (пропуск очереди)"),
    ("del", "Удалить задание"),
    ("pin", "Закрепить чек-лист"),
    ("top", "Итоги недели (список закрытых дел)"),
    ("info", "Инфо-карточка квартиры"),
    ("alice", "Подключение колонки Алиса"),
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
    def w(title, owners, wd):
        return {
            "title": title, "owners": owners, "idx": 0, "done_for": None,
            "last_done_date": None, "next_due": None,
            "schedule": {"type": "weekly", "weekday": wd, "every": 1},
        }
    return {
        "1": w("🍳 Кухня", ["Сосед", "Мы с женой"], 2),
        "2": w("🚿 Ванная", ["Мы с женой", "Сосед"], 5),
        "3": w("🛋 Гостиная + пол", ["Мы с женой"], 6),
    }

_STATE_CACHE: dict | None = None
_STATE_MTIME: float | None = None

def esc_md(val: str) -> str:
    """Экранирует спецсимволы Markdown v1 в пользовательском тексте."""
    return re.sub(r"([_*`\[])", r"\\\1", str(val))

def load_state() -> dict:
    global _STATE_CACHE, _STATE_MTIME
    state = {"chat_id": None}
    if STATE_FILE.exists():
        try:
            mtime = STATE_FILE.stat().st_mtime
            if _STATE_CACHE is not None and _STATE_MTIME == mtime:
                return _STATE_CACHE
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            _STATE_MTIME = mtime
        except Exception as e:
            logging.error("Ошибка чтения state.json, создаём резервную копию: %s", e)
            backup_file = STATE_FILE.with_suffix(".json.bak")
            STATE_FILE.rename(backup_file)
            state = {"chat_id": None}
            _STATE_MTIME = None
    else:
        _STATE_CACHE = None
        _STATE_MTIME = None

    if "tasks" not in state:
        state["tasks"] = default_tasks()
        state["next_id"] = 4

    for key, default in (
        ("members", {}),
        ("log", []),
        ("shopping", []),
        ("vacations", []),
        ("last_morning", None),
        ("last_evening", None),
        ("last_weekly", None),
        ("pinned", None),
        ("last_done", None),
        ("last_msgs", {}),
        ("info", []),
    ):
        state.setdefault(key, default)

    if not state.get("_no_utilities"):
        for tid in [k for k, t in state["tasks"].items() if t["title"] == "💧 Передать показания счётчиков"]:
            del state["tasks"][tid]
        state["_no_utilities"] = True

    today = today_local()
    start_ref = min(BOT_START_DATE, today)

    # Однократная миграция существующих задач с отсчётом всех шагов строго от 10.10 (включая незакрытые за 10.10–14.10)
    if not state.get("_migrated_v9"):
        log_dates = {}
        for entry in state.get("log", []):
            d_str = entry.get("date", "")
            t_name = entry.get("task", "")
            if d_str >= start_ref.isoformat() and t_name:
                if d_str > log_dates.get(t_name, ""):
                    log_dates[t_name] = d_str

        for t in state["tasks"].values():
            t.pop("level", None)
            s = t["schedule"]
            candidates = [
                d for d in (
                    t.get("last_done_date"),
                    t.get("done_for"),
                    log_dates.get(t["title"]),
                )
                if d and d >= start_ref.isoformat()
            ]
            last_done_iso = max(candidates) if candidates else None
            if last_done_iso:
                t["last_done_date"] = last_done_iso
                if s["type"] == "once":
                    t["next_due"] = None
                else:
                    nd = calc_next_due_from(s, date.fromisoformat(last_done_iso))
                    t["next_due"] = nd.isoformat() if nd else None
            else:
                t["done_for"] = None
                t["last_done_date"] = None
                t["next_due"] = initial_next_due(s, start_ref, min_date=start_ref)

        state["_migrated_v9"] = True
        save_state(state)

    for t in state["tasks"].values():
        t.pop("level", None)
        t.setdefault("last_done_date", None)
        if not t.get("next_due"):
            s = t["schedule"]
            if s["type"] == "once" and t.get("done_for"):
                t["next_due"] = None
            else:
                t["next_due"] = initial_next_due(s, start_ref, min_date=start_ref)
        elif t["schedule"]["type"] != "once" and t["next_due"] < start_ref.isoformat():
            t["next_due"] = initial_next_due(t["schedule"], start_ref, min_date=start_ref)

    _STATE_CACHE = state
    return state

def save_state(state: dict) -> None:
    """Атомарное сохранение через временный файл с обновлением кэша в памяти."""
    global _STATE_CACHE, _STATE_MTIME
    tmp_file = STATE_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_file, STATE_FILE)
    _STATE_CACHE = state
    try:
        _STATE_MTIME = STATE_FILE.stat().st_mtime
    except Exception:
        _STATE_MTIME = None

def user_name(user) -> str:
    if not user:
        return "Неизвестный"
    return user.first_name or user.username or str(user.id)

def touch(state: dict, user) -> bool:
    """Запоминаем участников (для рейтинга)."""
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
        anchor = today_local() + timedelta(days=n)
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
    if weekday is None and "недел" in t:
        weekday = today_local().weekday()
    if weekday is None:
        raise ValueError(
            "Не понял срок. Примеры: «каждую среду», «раз в неделю», «ежедневно», «каждые 3 дня», "
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

def initial_next_due(s: dict, base_date: date, min_date: date = None) -> str:
    """Вычисляет дату первого выполнения задачи с отсчётом циклов от base_date (например 10.10)."""
    if min_date is None:
        min_date = base_date
    t = s["type"]
    if t == "daily":
        return max(base_date, min_date).isoformat()
    if t == "once":
        return s["date"]
    if t == "interval":
        every = s["every"]
        cand = base_date + timedelta(days=every)
        while cand < min_date:
            cand += timedelta(days=every)
        s["anchor"] = cand.isoformat()
        return cand.isoformat()
    if t == "weekly":
        wd = s["weekday"]
        every = s.get("every", 1)
        days_ahead = (wd - base_date.weekday()) % 7
        cand = base_date + timedelta(days=days_ahead)
        if every > 1:
            if days_ahead == 0:
                cand = base_date + timedelta(days=7 * every)
            else:
                cand += timedelta(days=7 * (every - 1))
        step = 7 * every
        while cand < min_date:
            cand += timedelta(days=step)
        if every > 1:
            s["anchor"] = cand.isoformat()
        return cand.isoformat()
    if t == "monthly":
        day, every = s["day"], s.get("every", 1)
        cur_idx = month_index(base_date)
        max_d_cur = calendar.monthrange(base_date.year, base_date.month)[1]
        cand_cur = date(base_date.year, base_date.month, min(day, max_d_cur))
        idx = cur_idx + (every - 1) if cand_cur > base_date else cur_idx + every
        while True:
            year, month_0 = divmod(idx, 12)
            month = month_0 + 1
            max_d = calendar.monthrange(year, month)[1]
            cand = date(year, month, min(day, max_d))
            if cand >= min_date:
                break
            idx += every
        if every > 1:
            s["start"] = idx
        return cand.isoformat()
    if t == "yearly":
        month, day = s["month"], s["day"]
        y = base_date.year
        while True:
            max_d = calendar.monthrange(y, month)[1]
            cand = date(y, month, min(day, max_d))
            if cand >= min_date and cand >= base_date:
                return cand.isoformat()
            y += 1
    return max(base_date, min_date).isoformat()

def calc_next_due_from(s: dict, base_date: date):
    """Считает следующую дату выполнения задачи от даты фактического завершения (base_date)."""
    t = s["type"]
    if t == "daily":
        return base_date + timedelta(days=1)
    if t == "interval":
        next_d = base_date + timedelta(days=s["every"])
        s["anchor"] = next_d.isoformat()
        return next_d
    if t == "weekly":
        every = s.get("every", 1)
        next_d = base_date + timedelta(days=7 * every)
        s["weekday"] = next_d.weekday()
        s["anchor"] = next_d.isoformat()
        return next_d
    if t == "monthly":
        every = s.get("every", 1)
        idx = month_index(base_date) + every
        year, month_0 = divmod(idx, 12)
        month = month_0 + 1
        day = base_date.day
        max_days = calendar.monthrange(year, month)[1]
        next_d = date(year, month, min(day, max_days))
        s["day"] = day
        if every > 1:
            s["start"] = idx
        return next_d
    if t == "yearly":
        year = base_date.year + 1
        month = base_date.month
        day = base_date.day
        max_days = calendar.monthrange(year, month)[1]
        next_d = date(year, month, min(day, max_days))
        s["month"] = month
        s["day"] = day
        return next_d
    return None

def task_due_date(task: dict, today: date):
    """Возвращает текущую дату выполнения задачи (или None, если разовое дело уже закрыто)."""
    s = task["schedule"]
    if s["type"] == "once" and task.get("done_for"):
        return None
    nd = task.get("next_due")
    if nd:
        return date.fromisoformat(nd)
    init_iso = initial_next_due(s, today)
    task["next_due"] = init_iso
    return date.fromisoformat(init_iso)

def log_done(state: dict, who: str, title: str) -> None:
    state["log"].append({"who": who, "task": title, "date": today_local().isoformat()})
    del state["log"][:-1000]

def mark_done(state: dict, tid: str, occ_iso: str, who: str, cover: bool = False):
    """Отмечает задачу выполненной и считает срок следующего выполнения от даты завершения.
    Если cover=True («Я сделаю за него»), очередь не сдвигается — в следующий раз дежурит тот же участник.
    """
    task = state["tasks"].get(tid)
    if not task:
        return None
    today = today_local()
    if task.get("done_for") == occ_iso and task.get("last_done_date") == today.isoformat():
        return False
    due = task_due_date(task, today)
    if due is None or (due > today and task.get("last_done_date") == today.isoformat()):
        return False

    prev_done_for = task.get("done_for")
    prev_last_done_date = task.get("last_done_date")
    prev_next_due = task.get("next_due")
    prev_idx = task.get("idx", 0)
    prev_schedule = json.loads(json.dumps(task["schedule"]))
    prev_last_done_cover = task.get("last_done_cover", False)

    task["done_for"] = occ_iso
    task["last_done_date"] = today.isoformat()
    if cover:
        # Выручил соседа: выполнение засчитывается нажавшему, но следующая очередь остаётся за тем же дежурным
        task["idx"] = prev_idx
        task["last_done_cover"] = True
        log_title = f"{task['title']} 🤝"
    else:
        task["idx"] = (prev_idx + 1) % len(task["owners"])
        task["last_done_cover"] = False
        log_title = task["title"]

    next_d = calc_next_due_from(task["schedule"], today)
    task["next_due"] = next_d.isoformat() if next_d else None

    log_done(state, who, log_title)

    # Сохраняем последнее действие для отмены
    state["last_done"] = {
        "tid": tid,
        "occ_iso": occ_iso,
        "who": who,
        "prev_done_for": prev_done_for,
        "prev_last_done_date": prev_last_done_date,
        "prev_next_due": prev_next_due,
        "prev_idx": prev_idx,
        "prev_schedule": prev_schedule,
        "prev_last_done_cover": prev_last_done_cover,
        "title": task["title"],
        "log_title": log_title,
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
        task["done_for"] = ld.get("prev_done_for")
        task["last_done_date"] = ld.get("prev_last_done_date")
        if "prev_next_due" in ld:
            task["next_due"] = ld["prev_next_due"]
        if "prev_schedule" in ld:
            task["schedule"] = ld["prev_schedule"]
        task["idx"] = ld["prev_idx"]
        task["last_done_cover"] = ld.get("prev_last_done_cover", False)

    target_log_title = ld.get("log_title", ld["title"])
    if state["log"]:
        for i in range(len(state["log"]) - 1, -1, -1):
            entry = state["log"][i]
            if entry["task"] in (ld["title"], target_log_title) and entry["who"] == ld["who"]:
                state["log"].pop(i)
                break

    title = ld["title"]
    state["last_done"] = None
    return True, f"↩️ Отменено выполнение: «{title}» ({ld['who']})"

# ---------- сводка на сегодня (и закреплённый чек-лист) ----------
def build_today(state: dict):
    """Возвращает (текст, кнопки, сколько пунктов).
    В закреплённом сообщении — ТОЛЬКО дела и кнопки «✅ Выполнено».
    Невыполненные дела не пропадают со временем, пока не будут закрыты.
    """
    today = today_local()
    pending, done, upcoming = [], [], []

    for tid, t in ordered(state):
        s = t["schedule"]
        due = task_due_date(t, today)
        if t.get("last_done_date") == today.isoformat() and (due is None or due > today):
            done.append(t)
        elif due is not None and due <= today:
            if t.get("done_for") == due.isoformat() and s["type"] != "daily":
                if due == today:
                    done.append(t)
            else:
                pending.append((tid, t, due, (today - due).days))
        elif due is not None and due > today:
            adv = s.get("advance", 0)
            if adv and (due - today).days == adv:
                upcoming.append((adv, t))

    count = len(pending) + len(upcoming)
    label = f"{DAY_NAMES[today.weekday()]}, {today:%d.%m}"

    lines = [f"📌 *Список дел на сегодня*\n📅 *{label}*"]

    # Статус отпусков
    if state.get("vacations"):
        lines.append(f"🏖 В отпуске: {', '.join(state['vacations'])}\n")

    buttons = []
    if not pending and not done and not upcoming:
        lines.append("🎉 Все дела сделаны, порядок!")
    else:
        for tid, t, due, late in pending:
            suffix = "" if late == 0 else f" ⏰ не сделано ({late} дн.)"
            owner = current_owner(t, state)
            lines.append(f"🌫️ {t['title']} — {owner}{suffix}")
            occ_str = due.isoformat()
            if len(t.get("owners", [])) > 1:
                buttons.append([
                    InlineKeyboardButton(f"✅ {t['title'][:18]}", callback_data=f"tdone:{tid}:{occ_str}"),
                    InlineKeyboardButton("🤝 За него", callback_data=f"tcover:{tid}:{occ_str}"),
                ])
            else:
                buttons.append([InlineKeyboardButton(f"✅ {t['title'][:26]}", callback_data=f"tdone:{tid}:{occ_str}")])

        for t in done:
            cover_mark = " 🤝" if t.get("last_done_cover") else ""
            lines.append(f"✅ {t['title']}{cover_mark}")

        if upcoming:
            lines.append("")
            for adv, t in upcoming:
                when = "Завтра" if adv == 1 else f"Через {adv} дн."
                owner = current_owner(t, state)
                lines.append(f"🔔 {when}: {t['title']} — {owner}")

    kb = InlineKeyboardMarkup(buttons) if buttons else None
    return "\n".join(lines), kb, count

async def safe_delete_message(msg):
    """Безопасно удаляет сообщение пользователя при нажатии на кнопку панели."""
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass

async def ensure_keyboard_removed(bot, state: dict, chat_id: int, force: bool = False) -> None:
    """Удаляет старые сообщения со старой клавиатурой и отправляет неудаляемое сообщение с ReplyKeyboardRemove().
    ВАЖНО: в Telegram нельзя удалять само сообщение с ReplyKeyboardRemove(), иначе клиент
    восстанавливает старую клавиатуру из предыдущих сообщений в истории чата.
    """
    if not chat_id:
        return
    if not force and state.get("_kb_removed_v17"):
        return
    for old_key in ("list", "top", "help", "alice"):
        await delete_tracked_message(bot, state, chat_id, old_key)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="🧹 Нижняя панель кнопок отключена (для меню используйте /menu).",
            reply_markup=REMOVE_KEYBOARD,
        )
        state["_kb_removed_v17"] = True
        save_state(state)
    except Exception as e:
        logging.warning("Не удалось отправить ReplyKeyboardRemove: %s", e)

async def delete_tracked_message(bot, state: dict, chat_id: int, key: str) -> None:
    """Удаляет ранее отправленное ботом сообщение заданного типа (не трогая закреп)."""
    last_msgs = state.setdefault("last_msgs", {})
    chat_bucket = last_msgs.setdefault(str(chat_id), {})
    old_mid = chat_bucket.pop(key, None)
    pinned = state.get("pinned") or {}
    pinned_mid = pinned.get("message_id")

    if old_mid and old_mid != pinned_mid:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_mid)
        except Exception:
            pass

async def send_singleton_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, key: str, text: str, reply_markup=None, parse_mode="Markdown"):
    """Удаляет предыдущее сообщение с итогом вызова этой команды в чате и отправляет актуальное."""
    state = load_state()
    if not state.get("_kb_removed_v17"):
        await ensure_keyboard_removed(context.bot, state, chat_id)
    await delete_tracked_message(context.bot, state, chat_id, key)

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except BadRequest:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
    state.setdefault("last_msgs", {}).setdefault(str(chat_id), {})[key] = msg.message_id
    save_state(state)
    return msg

async def cleanup_prompt(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Удаляет временное сообщение-запрос бота после ответа пользователя."""
    pid = context.user_data.pop("prompt_id", None)
    if pid:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=pid)
        except Exception:
            pass

# ---------- диалоги добавления дел ----------
async def prompt_quick_todo(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает текст быстрого дела без ввода команд."""
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await cleanup_prompt(context, chat_id)
    context.user_data["awaiting"] = "quick_todo"
    p = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚡ *Напишите быстрое дело на сегодня:*\n"
            "Например: `Забрать посылку` или `Помыть обувь | Алина`"
        ),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Дело на сегодня..."),
        parse_mode="Markdown",
    )
    context.user_data["prompt_id"] = p.message_id

async def prompt_full_task(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает параметры регулярного задания без ввода команд."""
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await cleanup_prompt(context, chat_id)
    context.user_data["awaiting"] = "full_task"
    p = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "➕ *Добавление регулярного задания*\n\n"
            "Напишите параметры через « | »:\n"
            "`Название | кто | срок`\n\n"
            "📌 *Примеры срока:*\n"
            "• `каждую среду`\n"
            "• `каждые 14 дней`\n"
            "• `1 числа`\n"
            "• `раз в 3 месяца 1 числа`\n"
            "• `раз в год 15.03`\n\n"
            "💡 *Пример сообщения:* `🍳 Мытьё плиты | Сосед / Мы с женой | каждую субботу`"
        ),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Название | кто | срок"),
        parse_mode="Markdown",
    )
    context.user_data["prompt_id"] = p.message_id

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
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    await send_singleton_message(context, chat_id, "help", HELP, reply_markup=MAIN_KEYBOARD)

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает интерактивную панель меню."""
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    await send_singleton_message(
        context, chat_id, "menu",
        "📱 *Панель управления бытом:*\nВыберите нужное действие:",
        reply_markup=INLINE_MENU,
    )

def build_task(parts: list) -> dict:
    if len(parts) < 3 or not all(parts[:3]):
        raise ValueError("Нужно 3 части через «|»: Название | кто | срок")
    title, owners_s, when = parts[:3]
    owners = [o.strip() for o in owners_s.split("/") if o.strip()]
    if not owners:
        raise ValueError("Укажите, кто отвечает.")
    sched = parse_schedule(when)
    today = today_local()
    return {
        "title": title, "owners": owners, "idx": 0,
        "schedule": sched, "done_for": None,
        "last_done_date": None, "next_due": initial_next_due(sched, today),
    }

def add_task(state: dict, task: dict) -> str:
    tid = str(state["next_id"])
    state["next_id"] += 1
    state["tasks"][tid] = task
    return tid

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts_raw = update.message.text.split(None, 1)
    if len(parts_raw) < 2:
        await prompt_full_task(update.message, context)
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
    await update.message.reply_text(
        f"Добавлено №{len(state['tasks'])}: {task['title']}\n"
        f"Кто: {' → '.join(task['owners'])}\nСрок: {describe(task['schedule'])}"
    )

async def todo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрое разовое дело на сегодня."""
    if not context.args:
        await prompt_quick_todo(update.message, context)
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
        "last_done_date": None,
        "next_due": today.isoformat(),
    }
    add_task(state, task)
    save_state(state)
    await update_pinned(context.bot, state)
    await update.message.reply_text(f"✅ Добавлено дело на сегодня: {title} — {who}")

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    if not state["tasks"]:
        await send_singleton_message(context, chat_id, "list", "Заданий пока нет. Добавьте через /menu или /add", reply_markup=MAIN_KEYBOARD)
        return
    today = today_local()
    lines = ["📋 *Задания:*"]
    for pos, (tid, t) in enumerate(ordered(state), 1):
        due = task_due_date(t, today)
        if t.get("last_done_date") == today.isoformat() and (due is None or due > today):
            mark = "✅"
        elif due is not None and due <= today:
            mark = "🟨"
        else:
            mark = "▫️"
        owner = current_owner(t, state)
        due_info = f", след.: {DAY_NAMES[due.weekday()]}, {due:%d.%m}" if (mark != "🟨" and due is not None) else ""
        cover_mark = " 🤝" if (mark == "✅" and t.get("last_done_cover")) else ""
        lines.append(f"{pos}. {mark} {t['title']}{cover_mark} — {owner} ({describe(t['schedule'])}{due_info})")
    lines.append("\n🟨 ждёт, ✅ сделано, ▫️ срок ещё не наступил. Удалить: /del N")
    await send_singleton_message(context, chat_id, "list", "\n".join(lines), reply_markup=MAIN_KEYBOARD)

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
    state = load_state()
    text, kb, _ = build_today(state)
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    await send_singleton_message(context, chat_id, "today", text, reply_markup=kb)

async def preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    ready = [
        ("🛏 Сменить постельное бельё", ["Мы с женой"], "каждые 14 дней"),
        ("🧹 Генеральная уборка", ["Мы с женой"], "раз в месяц 1 числа"),
        ("🪟 Помыть окна", ["Мы с женой"], "раз в 3 месяца 1 числа"),
        ("❄️ Помыть холодильник", ["Мы с женой"], "раз в 2 месяца 1 числа"),
    ]
    have = {t["title"] for t in state["tasks"].values()}
    added = []
    for title, owners, when in ready:
        if title in have:
            continue
        tid = str(state["next_id"])
        state["next_id"] += 1
        sched = parse_schedule(when)
        state["tasks"][tid] = {
            "title": title, "owners": owners, "idx": 0,
            "schedule": sched, "done_for": None,
            "last_done_date": None, "next_due": initial_next_due(sched, today_local()),
        }
        added.append(title)
    if not state.get("chat_id") or update.effective_chat.type in ("group", "supergroup"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update_pinned(context.bot, state)
    text = ("Добавлены готовые бытовые задания:\n• " + "\n• ".join(added)) if added else "Все готовые задания уже добавлены."
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
    action, tid, occ_iso = query.data.split(":", 2)
    cover = (action == "tcover")
    state = load_state()
    who = user_name(query.from_user)
    if mark_done(state, tid, occ_iso, who, cover=cover):
        save_state(state)
        if cover and tid in state["tasks"]:
            same_owner = current_owner(state["tasks"][tid], state)
            await query.answer(f"🤝 Выполнено за соседа! Следующая очередь остаётся за: {same_owner}")
        else:
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

# ---------- покупки (интерактивный UX в 1 клик) ----------
def build_shopping_view(state: dict) -> tuple[str, InlineKeyboardMarkup]:
    items = state.get("shopping", [])
    if not items:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить товар", callback_data="shopmenu:add")]])
        return "🛒 *Список покупок пуст.*\n\nНажмите кнопку ниже, чтобы добавить товары:", kb

    lines = ["🛒 *Список покупок* (нажмите на товар, чтобы вычеркнуть):"]
    buttons = []
    has_done = False

    for idx, it in enumerate(items):
        is_done = it.get("done", False)
        item_text = esc_md(it["item"])
        by_text = esc_md(it["by"])
        if is_done:
            has_done = True
            lines.append(f"~{idx + 1}. {item_text} ({by_text})~ ✅")
            btn_text = f"✅ {it['item'][:24]}"
        else:
            lines.append(f"{idx + 1}. {item_text} ({by_text})")
            btn_text = f"🟨 {it['item'][:24]}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"shop:tgl:{idx}")])

    ctrl_row = [InlineKeyboardButton("➕ Добавить", callback_data="shopmenu:add")]
    if has_done:
        ctrl_row.append(InlineKeyboardButton("🧹 Убрать купленные", callback_data="shop:cleardone"))
    ctrl_row.append(InlineKeyboardButton("🗑 Очистить всё", callback_data="shop:clearall"))
    buttons.append(ctrl_row)

    return "\n".join(lines), InlineKeyboardMarkup(buttons)

async def shopping_menu_cmd(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Сразу открывает интерактивный список покупок в 1 клик."""
    state = load_state()
    text, kb = build_shopping_view(state)
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await send_singleton_message(context, chat_id, "shopping", text, reply_markup=kb)

async def prompt_shopping_add(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает товары для списка покупок без ввода команд."""
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await cleanup_prompt(context, chat_id)
    context.user_data["awaiting"] = "shopping_add"
    p = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🛒 *Что нужно купить?*\n\n"
            "Напишите товары (можно через запятую или с новой строки):\n"
            "Например: `Молоко, сыр, хлеб, чай`"
        ),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Молоко, сыр, хлеб..."),
        parse_mode="Markdown",
    )
    context.user_data["prompt_id"] = p.message_id

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split(None, 1)
    if len(parts) < 2:
        await prompt_shopping_add(update.message, context)
        return
    items = [i.strip() for i in re.split(r"[,;\n]", parts[1]) if i.strip()]
    state = load_state()
    who = user_name(update.effective_user)
    for item in items:
        state["shopping"].append({"item": item, "by": who, "done": False})
    save_state(state)
    await update.message.reply_text(
        f"🛒 Добавлено ({len(items)} шт.): {', '.join(items)}\nВсего в списке: {len(state['shopping'])}",
        reply_markup=SHOPPING_KEYBOARD,
    )

async def shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await shopping_menu_cmd(update.message, context)

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
            await query.answer("Куплено ✅" if not cur else "Возвращено в список 🟨")
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
        await update.message.reply_text("Список покупок и так пуст.", reply_markup=SHOPPING_KEYBOARD)
        return
    if not context.args:
        n = len(state["shopping"])
        state["shopping"] = []
        save_state(state)
        await update.message.reply_text(f"✅ Список очищен ({n} шт.)", reply_markup=SHOPPING_KEYBOARD)
        return
    if not context.args[0].isdigit() or not 1 <= int(context.args[0]) <= len(state["shopping"]):
        await update.message.reply_text("Нет пункта с таким номером. Смотрите /shopping")
        return
    item = state["shopping"].pop(int(context.args[0]) - 1)
    save_state(state)
    await update.message.reply_text(f"✅ Куплено: {item['item']}", reply_markup=SHOPPING_KEYBOARD)

# ---------- итоги (список закрытых задач по каждому человеку) ----------
def weekly_text(state: dict) -> str:
    """Формирует список закрытых задач с группировкой по каждому человеку за последние 7 дней."""
    since = (today_local() - timedelta(days=6)).isoformat()
    recent = [e for e in state.get("log", []) if e.get("date") >= since]
    if not recent:
        return "📊 *Итоги недели:*\nЗа последние 7 дней пока нет выполненных задач."

    grouped = {}
    for e in recent:
        who = e["who"]
        task_name = e.get("task", "Без названия")
        grouped.setdefault(who, []).append(task_name)

    lines = ["📊 *Итоги недели (выполненные задачи):*"]
    for who, tasks in sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True):
        lines.append(f"\n👤 *{who}* ({len(tasks)} шт.):")
        counts = Counter(tasks)
        for t, cnt in counts.items():
            if cnt > 1:
                lines.append(f"  • {t} (×{cnt})")
            else:
                lines.append(f"  • {t}")

    return "\n".join(lines)

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text = weekly_text(state)
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    await send_singleton_message(context, chat_id, "top", text, reply_markup=MAIN_KEYBOARD)

# ---------- инфо-карточка квартиры (/info) ----------
def build_info_view(state: dict, edit_mode: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    items = state.get("info", [])
    if not items:
        text = (
            "ℹ️ *Инфо-карточка квартиры*\n\n"
            "Пока нет записей. Здесь удобно хранить:\n"
            "• лицевые счета (интернет, электричество, Инфостан)\n"
            "• пароль от гостевого Wi-Fi и код домофона\n"
            "• контакты владельца квартиры или мастеров\n\n"
            "Нажмите кнопку ниже, чтобы добавить запись:"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить запись", callback_data="info:add")]])
        return text, kb

    lines = ["ℹ️ *Инфо-карточка квартиры:*\n"]
    for idx, entry in enumerate(items, 1):
        sep = ":" if ":" in entry else ("|" if "|" in entry else None)
        if sep:
            label, val = entry.split(sep, 1)
            clean_val = val.strip().replace("`", "")
            lines.append(f"{idx}. *{esc_md(label.strip())}:* `{clean_val}`")
        else:
            lines.append(f"{idx}. {esc_md(entry)}")

    B = InlineKeyboardButton
    if not edit_mode:
        lines.append("\n💡 _Нажмите на значение в моноширинном шрифте, чтобы скопировать._")
        kb = InlineKeyboardMarkup([
            [B("➕ Добавить запись", callback_data="info:add"), B("✏️ Изменить / Удалить", callback_data="info:edit")],
        ])
    else:
        lines.append("\n✏️ *Режим редактирования:* выберите запись для изменения или удаления:")
        rows = []
        for idx, entry in enumerate(items):
            short = re.split(r"[:|]", entry)[0].strip()[:20]
            rows.append([
                B(f"✏️ {idx + 1}. {short}", callback_data=f"info:ed:{idx}"),
                B("🗑 Удалить", callback_data=f"info:del:{idx}"),
            ])
        rows.append([B("➕ Добавить", callback_data="info:add"), B("⬅ Готово", callback_data="info:view")])
        kb = InlineKeyboardMarkup(rows)

    return "\n".join(lines), kb

async def prompt_info_add(target_message, context: ContextTypes.DEFAULT_TYPE, edit_idx: int = None):
    """Запрашивает текст для добавления или изменения записи в инфо-карточке."""
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await cleanup_prompt(context, chat_id)
    if edit_idx is None:
        context.user_data["awaiting"] = "info_add"
        prompt_text = (
            "ℹ️ *Добавление в инфо-карточку*\n\n"
            "Напишите запись в формате `Название: Значение` (можно несколько с новой строки):\n"
            "Например:\n"
            "`🌐 Wi-Fi: MyHomeNet`\n"
            "`⚡ Счёт за свет: 123456789`\n"
            "`🔑 Домофон: #4210`"
        )
    else:
        context.user_data["awaiting"] = f"info_edit:{edit_idx}"
        state = load_state()
        cur_val = state["info"][edit_idx] if 0 <= edit_idx < len(state.get("info", [])) else ""
        prompt_text = f"✏️ *Изменение записи №{edit_idx + 1}*\nТекущая: `{cur_val.replace('`', '')}`\n\nНапишите новый текст:"

    p = await context.bot.send_message(
        chat_id=chat_id,
        text=prompt_text,
        reply_markup=ForceReply(selective=True, input_field_placeholder="Название: Значение"),
        parse_mode="Markdown",
    )
    context.user_data["prompt_id"] = p.message_id

async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает инфо-карточку квартиры или сразу добавляет запись при вызове /info Текст."""
    state = load_state()
    chat_id = update.effective_chat.id
    if update.message and context.args:
        raw = update.message.text.split(None, 1)[1].strip()
        for line in [l.strip() for l in raw.splitlines() if l.strip()]:
            state.setdefault("info", []).append(line)
        save_state(state)
    if update.message:
        await safe_delete_message(update.message)
    text, kb = build_info_view(state)
    await send_singleton_message(context, chat_id, "info", text, reply_markup=kb)

async def on_info_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    state = load_state()

    if action == "add":
        await q.answer()
        await prompt_info_add(q.message, context)
        return
    elif action == "edit":
        await q.answer()
        text, kb = build_info_view(state, edit_mode=True)
    elif action == "view":
        await q.answer()
        text, kb = build_info_view(state, edit_mode=False)
    elif action == "del":
        idx = int(parts[2])
        info_list = state.get("info", [])
        if 0 <= idx < len(info_list):
            removed = info_list.pop(idx)
            save_state(state)
            await q.answer(f"Удалено: {removed[:24]}")
        else:
            await q.answer("Запись уже удалена")
        text, kb = build_info_view(state, edit_mode=bool(state.get("info")))
    elif action == "ed":
        idx = int(parts[2])
        await q.answer()
        await prompt_info_add(q.message, context, edit_idx=idx)
        return
    else:
        await q.answer()
        return

    try:
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass

# ---------- панель редактирования заданий ----------
FIELD_PROMPTS = {
    "title": ("Напишите новое название:", "Название"),
    "owners": ("Кто отвечает? Одно имя или несколько через «/» (очередь по кругу):", "Сосед/Мы с женой"),
    "when": ("Новый срок, например: каждую среду за день · каждые 3 дня · раз в год 15.03:", "каждую среду"),
    "new": ("Новое задание в формате:\nНазвание | кто | срок\n"
            "Пример: Окна | Сосед/Мы с женой | раз в 2 недели сб",
            "Название | кто | срок"),
}

def list_panel(state: dict):
    items = ordered(state)
    text = "✏️ Выберите задание для редактирования:" if items else "Заданий пока нет."
    rows = [[InlineKeyboardButton(f"{pos}. {t['title'][:28]}", callback_data=f"ed:t:{tid}")]
            for pos, (tid, t) in enumerate(items, 1)]
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data="ed:new"),
                 InlineKeyboardButton("✖ Закрыть", callback_data="ed:close")])
    return text, InlineKeyboardMarkup(rows)

def task_panel(state: dict, tid: str):
    t = state["tasks"].get(tid)
    if not t:
        return None
    pos = [k for k, _ in ordered(state)].index(tid) + 1
    due = task_due_date(t, today_local())
    due_line = f"\nСледующее выполнение: {DAY_NAMES[due.weekday()]}, {due:%d.%m.%Y}" if due else ""
    text = (
        f"✏️ Задание №{pos}\n{t['title']}\n\n"
        f"Кто: {' → '.join(t['owners'])} (сейчас: {current_owner(t, state)})\n"
        f"Срок: {describe(t['schedule'])}{due_line}"
    )
    B = InlineKeyboardButton
    rows = [
        [B("✏️ Название", callback_data=f"ed:f:{tid}:title"), B("👥 Кто", callback_data=f"ed:f:{tid}:owners")],
        [B("🗓 Срок", callback_data=f"ed:f:{tid}:when"), B("🗑 Удалить", callback_data=f"ed:del:{tid}")],
    ]
    if len(t["owners"]) > 1:
        rows.append([B("⏭ Передать очередь", callback_data=f"ed:next:{tid}")])
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
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    await send_singleton_message(context, chat_id, "edit", text, reply_markup=kb, parse_mode=None)

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
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    elif action in ("t", "del", "next", "dely", "f", "fprompt", "qwhen", "qown") and tid not in state["tasks"]:
        toast = "Это задание уже удалено"
        await show_panel(q, *list_panel(state))
    elif action == "t":
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
    elif action == "f" and len(parts) > 3 and parts[3] == "when":
        t = state["tasks"][tid]
        presets = [
            ("Ежедневно", "ежедневно"),
            ("Каждые 3 дня", "каждые 3 дня"),
            ("Каждую неделю", "раз в неделю"),
            ("Раз в 2 недели", "раз в 2 недели"),
            ("1 числа месяца", "1 числа"),
            ("15 числа месяца", "15 числа"),
        ]
        rows = []
        for i in range(0, len(presets), 2):
            row = [B(label, callback_data=f"ed:qwhen:{tid}:{val}") for label, val in presets[i:i+2]]
            rows.append(row)
        rows.append([B("⌨️ Ввести свой срок вручную", callback_data=f"ed:fprompt:{tid}:when")])
        rows.append([B("⬅ Назад", callback_data=f"ed:t:{tid}")])
        await show_panel(q, f"🗓 Выберите новый срок для «{t['title']}»:", InlineKeyboardMarkup(rows))
    elif action == "qwhen":
        when_str = parts[3]
        t = state["tasks"].get(tid)
        if t:
            t["schedule"] = parse_schedule(when_str)
            t["done_for"] = None
            t["last_done_date"] = None
            t["next_due"] = initial_next_due(t["schedule"], today_local())
            save_state(state)
            await update_pinned(context.bot, state)
            toast = f"Срок изменён: {describe(t['schedule'])}"
            await show_panel(q, *task_panel(state, tid))
    elif action == "f" and len(parts) > 3 and parts[3] == "owners":
        t = state["tasks"][tid]
        known = []
        for task_item in state["tasks"].values():
            combo = " / ".join(task_item.get("owners", []))
            if combo and combo not in known:
                known.append(combo)
            for single in task_item.get("owners", []):
                if single and single not in known:
                    known.append(single)
        for m_name in state.get("members", {}).values():
            if m_name and m_name not in known:
                known.append(m_name)
        rows = [[B(f"👥 {c[:30]}", callback_data=f"ed:qown:{tid}:{idx}")] for idx, c in enumerate(known[:6])]
        context.chat_data["owner_presets"] = known[:6]
        rows.append([B("⌨️ Ввести вручную", callback_data=f"ed:fprompt:{tid}:owners")])
        rows.append([B("⬅ Назад", callback_data=f"ed:t:{tid}")])
        await show_panel(q, f"👥 Кто отвечает за «{t['title']}»?", InlineKeyboardMarkup(rows))
    elif action == "qown":
        preset_idx = int(parts[3])
        presets = context.chat_data.get("owner_presets", [])
        t = state["tasks"].get(tid)
        if t and 0 <= preset_idx < len(presets):
            owners = [o.strip() for o in presets[preset_idx].split("/") if o.strip()]
            t["owners"], t["idx"] = owners, 0
            save_state(state)
            await update_pinned(context.bot, state)
            toast = f"Ответственные: {' → '.join(owners)}"
            await show_panel(q, *task_panel(state, tid))
    elif action in ("f", "fprompt", "new"):
        field = parts[3] if action in ("f", "fprompt") else "new"
        prompt_text, placeholder = FIELD_PROMPTS[field]
        prompt = await context.bot.send_message(
            q.message.chat_id, prompt_text, reply_markup=ForceReply(input_field_placeholder=placeholder))
        context.user_data["edit"] = {
            "field": field, "tid": tid if action in ("f", "fprompt") else None,
            "chat_id": q.message.chat_id, "panel_id": q.message.message_id, "prompt_id": prompt.message_id,
        }
        toast = "Ответьте на сообщение ниже ⬇️"
    await q.answer(toast)

# ---------- закреплённый чек-лист ----------
async def update_pinned(bot, state: dict, skip_id=None) -> None:
    text, kb, _ = build_today(state)
    p = state.get("pinned")
    if p and p.get("message_id") and p.get("message_id") != skip_id:
        try:
            await bot.edit_message_text(text, chat_id=p["chat_id"], message_id=p["message_id"], reply_markup=kb, parse_mode="Markdown")
        except BadRequest as e:
            err = str(e).lower()
            if "message to edit not found" in err:
                state["pinned"] = None
                save_state(state)
            elif "message is not modified" not in err:
                logging.warning("Ошибка редактирования закрепленного сообщения: %s", e)
        except Exception as e:
            logging.warning("Не удалось обновить закреплённое сообщение: %s", e)

    # Синхронизируем также последнее сообщение today в чате, если оно есть
    chat_id = state.get("chat_id")
    if chat_id:
        today_mid = state.get("last_msgs", {}).get(str(chat_id), {}).get("today")
        pinned_mid = p.get("message_id") if p else None
        if today_mid and today_mid != skip_id and today_mid != pinned_mid:
            try:
                await bot.edit_message_text(text, chat_id=chat_id, message_id=today_mid, reply_markup=kb, parse_mode="Markdown")
            except Exception:
                pass

async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    chat_id = update.effective_chat.id
    if not state.get("_kb_removed_v17"):
        await ensure_keyboard_removed(context.bot, state, chat_id)
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
    except Exception:
        pass

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
        if s["type"] == "once" and t.get("done_for") and t.get("last_done_date", "") < today.isoformat():
            del state["tasks"][tid]
    await update_pinned(bot, state)

    chat_id = state.get("chat_id")
    if chat_id:
        # Каждый день удаляем предыдущие сообщения today и вечернее напоминание
        await delete_tracked_message(bot, state, chat_id, "today")
        await delete_tracked_message(bot, state, chat_id, "evening")
        save_state(state)

    if state.get("pinned") and not MORNING_MESSAGE_WHEN_PINNED:
        return
    text, kb, count = build_today(state)
    if count and chat_id:
        msg = await bot.send_message(chat_id, "☀️ *Доброе утро!*\n" + text, reply_markup=kb, parse_mode="Markdown")
        state.setdefault("last_msgs", {}).setdefault(str(chat_id), {})["today"] = msg.message_id
        save_state(state)

async def run_evening(bot, state: dict):
    """Вечернее напоминание, если остались невыполненные дела."""
    today = today_local()
    pending = []
    for tid, t in ordered(state):
        due = task_due_date(t, today)
        if t.get("last_done_date") == today.isoformat() and (due is None or due > today):
            continue
        if due is not None and due <= today:
            if t.get("done_for") != due.isoformat() or t["schedule"]["type"] == "daily":
                pending.append((tid, t))

    chat_id = state.get("chat_id")
    if pending and chat_id:
        await delete_tracked_message(bot, state, chat_id, "evening")
        lines = ["🌙 *Вечернее напоминание:* ещё остались незавершённые дела на сегодня:"]
        for tid, t in pending:
            lines.append(f"• {t['title']} — {current_owner(t, state)}")
        lines.append("\nОтметьте выполненное в `/today` или в закреплённом чек-листе!")
        msg = await bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
        state.setdefault("last_msgs", {}).setdefault(str(chat_id), {})["evening"] = msg.message_id
        save_state(state)

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

# ---------- обработчики кнопок меню (Inline + Reply) ----------
async def on_shop_menu_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка инлайн-кнопок управления покупками."""
    q = update.callback_query
    await q.answer()
    act = q.data.split(":")[1]
    if act == "add":
        await prompt_shopping_add(q.message, context)
    else:
        text, kb = build_shopping_view(load_state())
        try:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await send_singleton_message(context, q.message.chat_id, "shopping", text, reply_markup=kb)

async def on_menu_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок из /menu (работает 100% стабильно в группах)."""
    q = update.callback_query
    await q.answer()
    act = q.data.split(":")[1]
    state = load_state()

    if act == "today":
        text, kb, _ = build_today(state)
        await send_singleton_message(context, q.message.chat_id, "today", text, reply_markup=kb)
    elif act == "todo":
        await prompt_quick_todo(q.message, context)
    elif act == "add":
        await prompt_full_task(q.message, context)
    elif act == "list":
        await list_tasks(update, context)
    elif act == "top":
        await top(update, context)
    elif act == "shop":
        text, kb = build_shopping_view(state)
        await send_singleton_message(context, q.message.chat_id, "shopping", text, reply_markup=kb)
    elif act == "info":
        text, kb = build_info_view(state)
        await send_singleton_message(context, q.message.chat_id, "info", text, reply_markup=kb)
    elif act == "main":
        await q.edit_message_text(
            "📱 *Панель управления бытом:*\nВыберите нужное действие:",
            reply_markup=INLINE_MENU,
            parse_mode="Markdown",
        )

async def on_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик текстовых сообщений: ответы, кнопки и добавление без команд."""
    msg = update.message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    text_lower = text.lower()

    # 1. Сначала проверяем нажатия на кнопки старой нижней панели:
    # если у кого-то на телефоне панель ещё осталась в кэше, при нажатии принудительно закрываем её
    if any(k in text_lower for k in (
        "сегодня", "быстрое дело", "добавить дело", "все задания", "список заданий",
        "редактировать", "редактор", "итог", "рейтинг", "просмотр списка",
        "добавить в покупки", "отметить купленное", "список покупок", "главное меню"
    )) or text_lower in ("меню", "назад", "в меню", "инфо", "памятка"):
        await ensure_keyboard_removed(context.bot, load_state(), msg.chat_id, force=True)

    if "сегодня" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await today_cmd(update, context)
        return
    elif "быстрое дело" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("edit", None)
        await prompt_quick_todo(msg, context)
        return
    elif "добавить дело" in text_lower or (text_lower == "добавить" and context.user_data.get("awaiting") != "shopping_add"):
        await safe_delete_message(msg)
        context.user_data.pop("edit", None)
        await prompt_full_task(msg, context)
        return
    elif "все задания" in text_lower or "список заданий" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await list_tasks(update, context)
        return
    elif "редактировать" in text_lower or "редактор" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await edit_cmd(update, context)
        return
    elif "итог" in text_lower or "рейтинг" in text_lower or "топ" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await top(update, context)
        return
    elif "просмотр списка" in text_lower or "посмотреть список" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await shopping_menu_cmd(msg, context)
        return
    elif "добавить в покупки" in text_lower or "добавить покупку" in text_lower or "добавить в список" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("edit", None)
        await prompt_shopping_add(msg, context)
        return
    elif "отметить купленное" in text_lower or "отметить покупки" in text_lower or "отметить покупку" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await shopping_menu_cmd(msg, context)
        return
    elif "покупк" in text_lower or "список покупок" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await shopping_menu_cmd(msg, context)
        return
    elif "инфо" in text_lower or "памятка" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await info_cmd(update, context)
        return
    elif "главное меню" in text_lower or text_lower in ("меню", "назад", "в меню"):
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await menu_cmd(update, context)
        return

    # 2. Проверяем, не ответ ли это на вопрос в панели /edit
    pend = context.user_data.get("edit")
    if pend and msg.reply_to_message and msg.reply_to_message.message_id == pend.get("prompt_id"):
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
                    task["last_done_date"] = None
                    task["next_due"] = initial_next_due(task["schedule"], today_local())
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
        return

    # 3. Режим прямого ввода быстрого дела (без команды /todo)
    awaiting = context.user_data.get("awaiting")
    if awaiting == "quick_todo":
        context.user_data.pop("awaiting", None)
        await cleanup_prompt(context, msg.chat_id)
        await safe_delete_message(msg)
        if "|" in text:
            parts = [p.strip() for p in text.split("|", 1)]
            title, who = parts[0], parts[1]
        else:
            title = text
            who = user_name(update.effective_user)

        state = load_state()
        today = today_local()
        task = {
            "title": f"📌 {title}",
            "owners": [who],
            "idx": 0,
            "schedule": {"type": "once", "date": today.isoformat()},
            "done_for": None,
            "last_done_date": None,
            "next_due": today.isoformat(),
        }
        add_task(state, task)
        save_state(state)
        await update_pinned(context.bot, state)
        today_text, kb, _ = build_today(state)
        await send_singleton_message(context, msg.chat_id, "today", today_text, reply_markup=kb)
        return

    # 4. Режим прямого ввода регулярного задания (без команды /add)
    if awaiting == "full_task":
        try:
            task = build_task([p.strip() for p in text.split("|")])
        except ValueError as e:
            await msg.reply_text(
                f"{e}\n\nПопробуйте ещё раз (или нажмите кнопку в меню для отмены).\n"
                "Формат: `Название | кто | срок`",
                parse_mode="Markdown",
            )
            return
        context.user_data.pop("awaiting", None)
        await cleanup_prompt(context, msg.chat_id)
        await safe_delete_message(msg)
        state = load_state()
        add_task(state, task)
        save_state(state)
        await update_pinned(context.bot, state)
        await list_tasks(update, context)
        return

    # 5. Режим прямого ввода покупок (без команды /buy)
    if awaiting == "shopping_add":
        context.user_data.pop("awaiting", None)
        await cleanup_prompt(context, msg.chat_id)
        await safe_delete_message(msg)
        items = [i.strip() for i in re.split(r"[,;\n]", text) if i.strip()]
        if not items:
            await msg.reply_text("Не удалось распознать товары. Попробуйте ещё раз.", reply_markup=SHOPPING_KEYBOARD)
            return
        state = load_state()
        who = user_name(update.effective_user)
        for item in items:
            state["shopping"].append({"item": item, "by": who, "done": False})
        save_state(state)
        shop_text, shop_kb = build_shopping_view(state)
        await send_singleton_message(context, msg.chat_id, "shopping", shop_text, reply_markup=shop_kb)
        return

    # 6. Режим ввода записи для инфо-карточки квартиры (/info)
    if awaiting and awaiting.startswith("info_"):
        context.user_data.pop("awaiting", None)
        await cleanup_prompt(context, msg.chat_id)
        await safe_delete_message(msg)
        state = load_state()
        info_list = state.setdefault("info", [])
        if awaiting == "info_add":
            for line in [l.strip() for l in text.splitlines() if l.strip()]:
                info_list.append(line)
        elif awaiting.startswith("info_edit:"):
            idx = int(awaiting.split(":")[1])
            if 0 <= idx < len(info_list):
                info_list[idx] = text.strip()
        save_state(state)
        info_text, info_kb = build_info_view(state)
        await send_singleton_message(context, msg.chat_id, "info", info_text, reply_markup=info_kb)
        return

# ---------- интеграция с Яндекс.Алисой (Webhook) ----------
def clean_speech(text: str) -> str:
    """Очищает текст от эмодзи и Markdown-разметки для красивой озвучки Алисой."""
    s = re.sub(r"[*_`~]", "", str(text))
    s = s.replace("→", "затем").replace("дн.", "дней").replace("шт.", "штук")
    s = re.sub(r"[^\w\s.,!?:;\-—()«»\"/а-яА-ЯёЁ0-9]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"([«(])\s+|\s+([»)])", r"\1\2", s)

def _norm_yo(s: str) -> str:
    return s.lower().replace("ё", "е")

def match_task_by_voice(tasks_items: list, query_text: str):
    """Ищет задачу по ключевым словам из голосовой команды."""
    stop_words = {
        "сделано", "сделал", "сделала", "сделали", "готово", "готова", "выполнено",
        "помыл", "помыла", "помыли", "убрал", "убрала", "убрали", "отметь", "отметить",
        "закрыл", "закрыла", "задача", "задание", "дело", "сегодня", "соседа", "сосед",
        "него", "нее", "жену", "жена", "пожалуйста", "алиса",
    }
    q_words = [w for w in re.findall(r"[а-яё]{3,}", _norm_yo(query_text)) if w not in stop_words]
    if not q_words:
        return None
    for tid, t in tasks_items:
        t_words = re.findall(r"[а-яё]{3,}", _norm_yo(t["title"]))
        for qw in q_words:
            stem = qw[:4] if len(qw) >= 4 else qw
            if any(tw.startswith(stem) or qw.startswith(tw[:4]) for tw in t_words):
                return tid, t
    return None

def build_alice_today_speech(state: dict) -> str:
    """Формирует голосовую сводку дел на сегодня для Алисы."""
    today = today_local()
    pending, done = [], []
    for tid, t in ordered(state):
        s = t["schedule"]
        due = task_due_date(t, today)
        if t.get("last_done_date") == today.isoformat() and (due is None or due > today):
            done.append(t)
        elif due is not None and due <= today:
            if t.get("done_for") == due.isoformat() and s["type"] != "daily":
                if due == today:
                    done.append(t)
            else:
                pending.append((tid, t, (today - due).days))

    parts = []
    if not pending and not done:
        parts.append("На сегодня все домашние дела сделаны, дома полный порядок!")
    else:
        if pending:
            items_str = []
            for _, t, late in pending:
                title_clean = clean_speech(t["title"])
                owner_clean = clean_speech(current_owner(t, state))
                late_str = f" (не сделано {late} дней)" if late > 0 else ""
                items_str.append(f"{title_clean} — дежурит {owner_clean}{late_str}")
            parts.append(f"Дела на сегодня ({len(pending)}): " + ". ".join(items_str) + ".")
        else:
            parts.append("Все основные дела на сегодня уже закрыты!")
        if done:
            done_names = [
                clean_speech(t["title"]) + (" (за соседа)" if t.get("last_done_cover") else "")
                for t in done
            ]
            parts.append("Уже выполнено сегодня: " + ", ".join(done_names) + ".")
    return " ".join(parts)

async def refresh_shopping_if_open(bot, state: dict) -> None:
    """Если в чате открыт список покупок, синхронизирует его после голосовой команды."""
    chat_id = state.get("chat_id")
    if not chat_id:
        return
    shop_mid = state.get("last_msgs", {}).get(str(chat_id), {}).get("shopping")
    if shop_mid:
        text, kb = build_shopping_view(state)
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=shop_mid, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass

async def process_alice_request(data: dict, bot) -> dict:
    """Обрабатывает JSON-запрос от Яндекс.Диалогов и возвращает ответ по протоколу Алисы."""
    version = data.get("version", "1.0")
    session = data.get("session", {})
    req = data.get("request", {})
    cmd = _norm_yo((req.get("command") or "").strip())
    orig = (req.get("original_utterance") or "").strip()
    is_new = bool(session.get("new"))

    if cmd == "ping":
        return {"version": version, "session": session, "response": {"text": "pong", "end_session": True}}

    # Если навык запущен одной быстрой фразой («Алиса, спроси у Помощника...»), сразу завершаем сессию после ответа
    end_session = bool(is_new and cmd)
    state = load_state()

    # 1. Выход
    if cmd in ("хватит", "стоп", "выход", "пока", "спасибо", "закончить", "все"):
        reply = "Обращайтесь! Хорошего дня."
        end_session = True

    # 2. Справка
    elif any(w in cmd for w in ("помощь", "справка", "что ты умеешь", "команды")):
        reply = (
            "Я помогаю следить за делами по дому и покупками. "
            "Спросите: что на сегодня, или что в покупках. "
            "Скажите: купи молоко и хлеб, добавь дело на сегодня забрать посылку, "
            "или скажите: кухня готова."
        )
        end_session = False

    # 3. Отмена последнего выполнения
    elif any(w in cmd for w in ("отмени", "отмена", "верни назад")):
        ok, msg = undo_last_done(state)
        if ok:
            save_state(state)
            await update_pinned(bot, state)
        reply = clean_speech(msg)

    # 4. Очистка списка покупок
    elif any(p in cmd for p in ("очисти покупки", "очистить покупки", "убери купленное", "все купили", "очисти список покупок")):
        if not state.get("shopping"):
            reply = "Список покупок и так пуст."
        elif "куплен" in cmd:
            state["shopping"] = [it for it in state["shopping"] if not it.get("done", False)]
            save_state(state)
            await refresh_shopping_if_open(bot, state)
            reply = "Убрала купленные товары из списка."
        else:
            state["shopping"] = []
            save_state(state)
            await refresh_shopping_if_open(bot, state)
            reply = "Список покупок полностью очищен."

    # 5. Добавление в список покупок
    elif re.match(r"^(?:купи|купить|добавь в покупки|добавить в покупки|в покупки|в список покупок|нужно купить)\b", cmd):
        raw_items = re.sub(
            r"^(?:купи|купить|добавь в покупки|добавить в покупки|в покупки|в список покупок|нужно купить)\s*",
            "", orig, flags=re.IGNORECASE
        ).strip(" .,!?")
        items = [i.strip() for i in re.split(r"[,;\n]|\s+и\s+", raw_items) if i.strip()]
        if not items:
            reply = "Не расслышала, что добавить в покупки. Скажите, например: купи молоко и хлеб."
        else:
            for item in items:
                state["shopping"].append({"item": item, "by": "Алиса 🔊", "done": False})
            save_state(state)
            await refresh_shopping_if_open(bot, state)
            reply = f"Добавила в список покупок: {', '.join(items)}."

    # 6. Чтение списка покупок
    elif "покуп" in cmd or "что купить" in cmd:
        pending_shop = [clean_speech(it["item"]) for it in state.get("shopping", []) if not it.get("done")]
        if not pending_shop:
            reply = "Список покупок пока пуст."
        else:
            reply = f"В списке покупок ({len(pending_shop)}): {', '.join(pending_shop)}."

    # 7. Быстрое разовое дело на сегодня
    elif re.match(r"^(?:быстрое дело|дело на сегодня|задача на сегодня|добавь дело|напомни сегодня)\b", cmd):
        raw_title = re.sub(
            r"^(?:быстрое дело|дело на сегодня|задача на сегодня|добавь дело|напомни сегодня)\s*",
            "", orig, flags=re.IGNORECASE
        ).strip(" .,!?")
        if not raw_title:
            reply = "Скажите название дела, например: дело на сегодня забрать посылку."
        else:
            today = today_local()
            task = {
                "title": f"📌 {raw_title}",
                "owners": ["Мы с женой"],
                "idx": 0,
                "schedule": {"type": "once", "date": today.isoformat()},
                "done_for": None,
                "last_done_date": None,
                "next_due": today.isoformat(),
            }
            add_task(state, task)
            save_state(state)
            await update_pinned(bot, state)
            reply = f"Добавила дело на сегодня: {clean_speech(raw_title)}."

    # 8. Отметка выполнения задачи («кухня готова», «помыл ванную», «сделано кухня за соседа»)
    elif any(w in cmd for w in ("сделан", "сделал", "готов", "помыл", "помыт", "убрал", "убран", "выполнен", "отметь", "закрыл", "закрыт")):
        cover = any(p in cmd for p in ("за соседа", "за него", "за нее", "за жену"))
        today = today_local()
        pending_items = []
        for tid, t in ordered(state):
            due = task_due_date(t, today)
            if due is not None and due <= today and t.get("done_for") != due.isoformat():
                pending_items.append((tid, t))
        matched = match_task_by_voice(pending_items, cmd) or match_task_by_voice(ordered(state), cmd)
        if not matched:
            if len(pending_items) == 1:
                matched = pending_items[0]
            else:
                reply = "Не поняла, какое именно задание отметить. Назовите его, например: кухня готова, или ванная помыта."
        if matched:
            tid, t = matched
            due = task_due_date(t, today) or today
            if mark_done(state, tid, due.isoformat(), "Алиса 🔊", cover=cover):
                save_state(state)
                await update_pinned(bot, state)
                title_clean = clean_speech(t["title"])
                if cover:
                    reply = f"Отлично! Отметила задание «{title_clean}» выполненным за соседа. Следующая очередь сохранена."
                else:
                    reply = f"Отлично! Отметила задание «{title_clean}» выполненным."
            else:
                reply = f"Задание «{clean_speech(t['title'])}» уже было отмечено сегодня."

    # 9. Сводка на сегодня (при запуске навыка или вопросе «что на сегодня / какие дела»)
    elif not cmd or any(w in cmd for w in ("сегодня", "дела", "задач", "список", "дежур", "очеред", "порядок", "привет", "старт")):
        reply = build_alice_today_speech(state)
        if is_new and not cmd:
            reply += " Что сделаем? Можно сказать: список покупок, купи молоко, или кухня готова."
            end_session = False

    # 10. Нераспознанная команда
    else:
        reply = (
            "Не совсем поняла команду. Спросите: какие дела на сегодня, что в списке покупок, "
            "или скажите: купи молоко, либо кухня готова."
        )

    return {
        "version": version,
        "session": session,
        "response": {
            "text": reply,
            "tts": reply,
            "end_session": end_session,
            "buttons": [
                {"title": "📅 Дела на сегодня", "hide": True},
                {"title": "🛒 Список покупок", "hide": True},
                {"title": "❓ Что ты умеешь", "hide": True},
            ],
        },
    }

async def _handle_alice_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, bot) -> None:
    """Встроенный асинхронный HTTP-обработчик вебхука Яндекс.Алисы (без внешних библиотек)."""
    try:
        req_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not req_line:
            writer.close()
            return
        parts = req_line.decode("utf-8", errors="ignore").strip().split()
        method, raw_target = (parts[0], parts[1]) if len(parts) >= 2 else ("GET", "/")
        parsed_url = urlparse(raw_target)

        headers = {}
        while True:
            hline = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not hline or hline in (b"\r\n", b"\n"):
                break
            h_str = hline.decode("utf-8", errors="ignore").strip()
            if ":" in h_str:
                k, v = h_str.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if ALICE_SECRET:
            qs = parse_qs(parsed_url.query)
            token_ok = (qs.get("token", [""])[0] == ALICE_SECRET) or (headers.get("authorization") == f"Bearer {ALICE_SECRET}")
            if not token_ok:
                body = b'{"error":"forbidden"}'
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                await writer.drain()
                writer.close()
                return

        if method == "GET":
            body = json.dumps({"ok": True, "service": "chores-alice-webhook", "path": ALICE_PATH}, ensure_ascii=False).encode("utf-8")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
            writer.close()
            return

        content_length = int(headers.get("content-length", "0"))
        raw_body = await asyncio.wait_for(reader.readexactly(content_length), timeout=5.0) if content_length > 0 else b"{}"
        payload = json.loads(raw_body.decode("utf-8", errors="ignore") or "{}")
        resp_dict = await process_alice_request(payload, bot)
        resp_bytes = json.dumps(resp_dict, ensure_ascii=False).encode("utf-8")

        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Content-Length: " + str(len(resp_bytes)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + resp_bytes
        )
        await writer.drain()
    except Exception as e:
        logging.warning("Ошибка вебхука Алисы: %s", e)
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def alice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус и шпаргалку по голосовому управлению через Яндекс.Алису."""
    chat_id = update.effective_chat.id
    if update.message:
        await safe_delete_message(update.message)
    status = f"Включён (порт `{ALICE_PORT}`, путь `{ALICE_PATH}`)" if ALICE_PORT > 0 else "Выключен (`ALICE_PORT=0`)"
    text = (
        "🔊 *Голосовое управление через Яндекс.Алису*\n\n"
        f"⚙️ *Статус вебхука на VPS:* {status}\n\n"
        "🗣 *Что можно говорить колонке:*\n"
        "• _«Алиса, спроси у Домашнего Помощника, какие дела на сегодня»_\n"
        "• _«...что в списке покупок»_\n"
        "• _«...купи молоко, сыр и хлеб»_\n"
        "• _«...добавь дело на сегодня забрать посылку»_\n"
        "• _«...кухня готова»_ или _«...помыл ванную за соседа»_\n"
        "• _«...отмени последнее выполнение»_\n\n"
        "🔗 *Как подключить на VPS:*\n"
        "1. Направьте HTTPS-домен (через Nginx или Caddy) на `http://127.0.0.1:" + str(ALICE_PORT) + ALICE_PATH + "`\n"
        "2. В кабинете `dialogs.yandex.ru/developer` создайте приватный навык и укажите этот Webhook URL."
    )
    await send_singleton_message(context, chat_id, "alice", text, reply_markup=MAIN_KEYBOARD)

async def post_init(app: Application):
    app.bot_data["scheduler"] = asyncio.create_task(scheduler(app))
    if ALICE_PORT > 0:
        try:
            srv = await asyncio.start_server(
                lambda r, w: _handle_alice_http(r, w, app.bot),
                host=ALICE_HOST, port=ALICE_PORT,
            )
            app.bot_data["alice_server"] = srv
            logging.info("Вебхук Яндекс.Алисы запущен на %s:%s%s", ALICE_HOST, ALICE_PORT, ALICE_PATH)
        except Exception as e:
            logging.warning("Не удалось запустить сервер Алисы на порту %s: %s", ALICE_PORT, e)
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
    except Exception:
        logging.exception("Не удалось задать меню команд")
    state = load_state()
    target_chat_id = state.get("chat_id") or (state.get("pinned") or {}).get("chat_id")
    if target_chat_id and not state.get("_kb_removed_v17"):
        await ensure_keyboard_removed(app.bot, state, target_chat_id)

async def post_shutdown(app: Application):
    task = app.bot_data.get("scheduler")
    if task:
        task.cancel()
    srv = app.bot_data.get("alice_server")
    if srv:
        srv.close()
        await srv.wait_closed()

def main():
    if not TOKEN:
        raise ValueError("Переменная окружения BOT_TOKEN не задана!")

    defaults = Defaults(disable_notification=SILENT_MESSAGES)
    app = Application.builder().token(TOKEN).defaults(defaults).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(TypeHandler(Update, track), group=-1)

    for name, fn in [
        ("start", start), ("help", help_cmd), ("menu", menu_cmd),
        ("add", add), ("todo", todo_cmd), ("list", list_tasks),
        ("today", today_cmd), ("del", delete), ("undo", undo_cmd),
        ("preset", preset), ("vacation", vacation_cmd),
        ("pin", pin_cmd), ("unpin", unpin_cmd), ("edit", edit_cmd),
        ("buy", buy), ("shopping", shopping), ("bought", bought),
        ("top", top), ("info", info_cmd), ("alice", alice_cmd),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_today_button, pattern=r"^t(done|cover):"))
    app.add_handler(CallbackQueryHandler(on_undo_button, pattern=r"^tundo$"))
    app.add_handler(CallbackQueryHandler(on_shopping_button, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(on_shop_menu_inline, pattern=r"^shopmenu:"))
    app.add_handler(CallbackQueryHandler(on_menu_inline, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(on_edit, pattern=r"^ed:"))
    app.add_handler(CallbackQueryHandler(on_info_button, pattern=r"^info:"))

    # Единый обработчик текста
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_general_text))

    app.run_polling()

if __name__ == "__main__":
    main()
# --- Конец кода бота (файл загружен полностью) ---
# ============================================================================
# ============================================================================
# ============================================================================
# ============================================================================
# ============================================================================
# ============================================================================
