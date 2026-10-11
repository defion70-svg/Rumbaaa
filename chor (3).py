# -*- coding: utf-8 -*-
"""
Домашний бот для квартиры (Telegram) — совместный быт и задания.

Задания:   /today /todo /add /list /del /edit /preset /undo /vacation
Покупки:   /buy /shopping /bought
Рейтинг:   /top  (итоги недели приходят по воскресеньям)
Интерфейс: /menu /pin /unpin /help

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
# ID администратора (если задан, только этот Telegram ID управляет закреплённым пультом).
# Если пустой "", владельцем становится тот, кто вызвал /pin.
ADMIN_ID = os.environ.get("ADMIN_ID", "")
# --------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
ABBR = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
PREFIX3 = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}

LEVELS = {1: ("🟢", "лёгкая"), 2: ("🟡", "средняя"), 3: ("🔴", "сложная")}

PRAISE = [
    "Красавчик!", "Так держать! 💪", "Чистота — залог здоровья ✨", "Респект!",
    "Ещё одно дело в копилку!", "Вот это порядок!", "Герой дня! 🏆", "Домашний уют +100! 🏡",
]

# Нижняя клавиатура: Сегодня, быстрое дело, добавить дело, все задания, редактировать, рейтинг, покупки, главное меню
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📅 Сегодня"), KeyboardButton("⚡ Добавить быстрое дело")],
        [KeyboardButton("➕ Добавить дело"), KeyboardButton("📋 Все задания")],
        [KeyboardButton("✏️ Редактировать"), KeyboardButton("🏆 Рейтинг")],
        [KeyboardButton("🛒 Список покупок"), KeyboardButton("🔙 Главное меню")],
    ],
    resize_keyboard=True,
)

# Подменю покупок в нижней панели
SHOPPING_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📋 Просмотр списка"), KeyboardButton("➕ Добавить в покупки")],
        [KeyboardButton("✅ Отметить купленное"), KeyboardButton("🔙 Главное меню")],
    ],
    resize_keyboard=True,
)

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
        InlineKeyboardButton("🏆 Рейтинг", callback_data="menu:top"),
    ],
    [
        InlineKeyboardButton("🛒 Список покупок", callback_data="menu:shop"),
        InlineKeyboardButton("🔙 Главное меню", callback_data="menu:main"),
    ],
])

HELP = (
    "🏡 *ДОМАШНИЙ ПОМОЩНИК*\n\n"
    "📋 *ЗАДАНИЯ:*\n"
    "• `/today` — дела на сегодня (интерактивный чек-лист)\n"
    "• `/todo [дело]` — быстрое разовое дело на сегодня\n"
    "• `/add Название | кто | срок | сложность` — добавить регулярное задание\n"
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
    "🏆 *РЕЙТИНГ И ИНТЕРФЕЙС:*\n"
    "• `/top` — рейтинг по баллам за сложность дел\n"
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
    ("top", "Рейтинг и итоги недели"),
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
        ("shopping", []),
        ("vacations", []),
        ("last_morning", None),
        ("last_evening", None),
        ("last_weekly", None),
        ("pinned", None),
        ("last_done", None),
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


# ---------- сводка на сегодня ----------
def check_pinned_access(query, state: dict) -> bool:
    """
    Проверяет права доступа к закреплённому пульту.
    Бытовые действия (✅ Выполнено, ↩️ Отменить, 🛒 Покупки, переключение вкладок)
    всегда доступны ВСЕМ соседям в квартире!
    Административные действия (редактирование, удаление, добавление заданий, настройки)
    в режиме защиты доступны только владельцу/админу.
    """
    data = getattr(query, "data", "") or ""
    # Действия, которые ВСЕГДА открыты для всех соседей:
    allowed_for_all = (
        data.startswith("tdone:") or        # Отметить задание «Выполнено»
        data == "tundo" or                  # Отменить случайное выполнение
        data.startswith("shop:tgl:") or     # Вычеркнуть/вернуть купленный товар
        data.startswith("pinmenu:tab:") or  # Переключение вкладок пульта (дела, покупки, список, топ)
        data == "pinmenu:refresh" or        # Обновить экран
        data == "pinmenu:todo" or           # Добавить быстрое дело на сегодня
        data == "pinmenu:shop_add"          # Добавить товар в список покупок
    )
    if allowed_for_all:
        return True

    p = state.get("pinned")
    if not p or not query.message or query.message.message_id != p.get("message_id"):
        return True  # Обычные сообщения (не закреплённый пульт)

    if not p.get("only_owner", False):
        return True  # Режим полного общего доступа

    allowed_id = ADMIN_ID or p.get("owner_id")
    if not allowed_id:
        return True

    return str(query.from_user.id) == str(allowed_id)


def build_pinned_dashboard(state: dict, view: str = None) -> tuple[str, InlineKeyboardMarkup]:
    """Строит полный интерактивный пульт управления для закреплённого сообщения."""
    p = state.get("pinned", {})
    if view is None:
        view = p.get("view", "today")

    only_owner = p.get("only_owner", True)
    owner_name = p.get("owner_name", "Владелец")
    access_label = f"🔒 Админ: {owner_name[:8]}" if only_owner else "👥 Общий пульт"

    today = today_local()
    label = f"{DAY_NAMES[today.weekday()]}, {today:%d.%m}"

    if view == "today":
        lines = [f"📌 *Список дел на сегодня*\n📅 *{label}*"]
        if state.get("vacations"):
            lines.append(f"🏖 В отпуске: {', '.join(state['vacations'])}\n")

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

        buttons = []
        if not pending and not done and not upcoming:
            lines.append("🎉 Все дела сделаны, порядок!")
        else:
            for tid, t, occ, late in pending:
                suffix = "" if late == 0 else " ⏰ просрочено"
                owner = current_owner(t, state)
                lines.append(f"⬜ {t['title']} {level_emoji(t)} — {owner}{suffix}")
                row = [InlineKeyboardButton(f"✅ {t['title'][:24]}", callback_data=f"tdone:{tid}:{occ.isoformat()}")]
                buttons.append(row)

            for t in done:
                lines.append(f"✅ {t['title']}")

            if upcoming:
                lines.append("")
                for adv, t in upcoming:
                    when = "Завтра" if adv == 1 else f"Через {adv} дн."
                    owner = current_owner(t, state)
                    lines.append(f"🔔 {when}: {t['title']} — {owner}")

        if state.get("last_done"):
            buttons.append([InlineKeyboardButton(f"↩️ Отменить «{state['last_done']['title'][:18]}»", callback_data="tundo")])

        # Панель навигации и действий
        buttons.append([
            InlineKeyboardButton("⚡ +Быстрое", callback_data="pinmenu:todo"),
            InlineKeyboardButton("➕ +Задание", callback_data="pinmenu:add"),
        ])
        buttons.append([
            InlineKeyboardButton("🛒 Покупки", callback_data="pinmenu:tab:shop"),
            InlineKeyboardButton("📋 Все задания", callback_data="pinmenu:tab:tasks"),
            InlineKeyboardButton("🏆 Рейтинг", callback_data="pinmenu:tab:top"),
        ])
        buttons.append([
            InlineKeyboardButton("✏️ Редактор", callback_data="ed:list"),
            InlineKeyboardButton("🔄 Обновить", callback_data="pinmenu:refresh"),
            InlineKeyboardButton(access_label, callback_data="pinmenu:tglaccess"),
        ])

    elif view == "shop":
        items = state.get("shopping", [])
        lines = [f"📌 *Список дел на сегодня | ПОКУПКИ*\nВсего позиций: *{len(items)} шт.*"]
        buttons = []
        has_done = False

        if not items:
            lines.append("\n🛒 Список покупок пуст.")
        else:
            lines.append("\n*Нажмите на товар, чтобы вычеркнуть:*")
            for idx, it in enumerate(items):
                is_done = it.get("done", False)
                if is_done:
                    has_done = True
                    lines.append(f"~{idx + 1}. {it['item']} ({it['by']})~ ✅")
                    btn_text = f"✅ {it['item'][:22]}"
                else:
                    lines.append(f"{idx + 1}. {it['item']} ({it['by']})")
                    btn_text = f"⬜ {it['item'][:22]}"
                buttons.append([InlineKeyboardButton(btn_text, callback_data=f"shop:tgl:{idx}")])

        ctrl_row = []
        if has_done:
            ctrl_row.append(InlineKeyboardButton("🧹 Убрать купленные", callback_data="shop:cleardone"))
        if items:
            ctrl_row.append(InlineKeyboardButton("🗑 Очистить", callback_data="shop:clearall"))
        if ctrl_row:
            buttons.append(ctrl_row)

        buttons.append([
            InlineKeyboardButton("➕ Добавить товар", callback_data="pinmenu:shop_add"),
            InlineKeyboardButton("🔙 Главное меню", callback_data="pinmenu:tab:today"),
        ])
        buttons.append([
            InlineKeyboardButton("📋 Все задания", callback_data="pinmenu:tab:tasks"),
            InlineKeyboardButton("🏆 Рейтинг", callback_data="pinmenu:tab:top"),
            InlineKeyboardButton(access_label, callback_data="pinmenu:tglaccess"),
        ])

    elif view == "tasks":
        lines = ["📌 *Список дел на сегодня | ВСЕ ЗАДАНИЯ*"]
        items = ordered(state)
        if not items:
            lines.append("\nЗаданий пока нет.")
        else:
            lines.append("")
            for pos, (tid, t) in enumerate(items, 1):
                occ = occurrence(t["schedule"], today)
                if occ and (today - occ).days <= window(t):
                    mark = "✅" if t["done_for"] == occ.isoformat() else "⬜"
                else:
                    mark = "▫️"
                owner = current_owner(t, state)
                lines.append(f"{pos}. {mark} {t['title']} {level_emoji(t)} — {owner} ({describe(t['schedule'])})")
            lines.append("\n⬜ ждёт, ✅ сделано, ▫️ срок ещё не наступил.")

        buttons = [
            [
                InlineKeyboardButton("➕ Добавить задание", callback_data="pinmenu:add"),
                InlineKeyboardButton("✏️ Редактировать", callback_data="ed:list"),
            ],
            [
                InlineKeyboardButton("🔙 Главное меню", callback_data="pinmenu:tab:today"),
                InlineKeyboardButton("🛒 Покупки", callback_data="pinmenu:tab:shop"),
                InlineKeyboardButton("🏆 Рейтинг", callback_data="pinmenu:tab:top"),
            ],
            [
                InlineKeyboardButton("🔄 Обновить", callback_data="pinmenu:refresh"),
                InlineKeyboardButton(access_label, callback_data="pinmenu:tglaccess"),
            ],
        ]

    elif view == "top":
        lines = [f"📌 *Список дел на сегодня | РЕЙТИНГ*\n\n{weekly_text(state)}"]
        total = score_lines(state["log"])
        if total:
            lines.append("\n*За всё время:*\n" + "\n".join(f"• {w} — {n} б. ({c} ✅)" for w, n, c in total))

        buttons = [
            [
                InlineKeyboardButton("🔙 Главное меню", callback_data="pinmenu:tab:today"),
                InlineKeyboardButton("🛒 Покупки", callback_data="pinmenu:tab:shop"),
            ],
            [
                InlineKeyboardButton("📋 Все задания", callback_data="pinmenu:tab:tasks"),
                InlineKeyboardButton("🔄 Обновить", callback_data="pinmenu:refresh"),
                InlineKeyboardButton(access_label, callback_data="pinmenu:tglaccess"),
            ],
        ]

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


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

    lines = [f"📌 *Список дел на сегодня*\n📅 *{label}*"]

    # Статус отпусков
    if state.get("vacations"):
        lines.append(f"🏖 В отпуске: {', '.join(state['vacations'])}")
        lines.append("")

    buttons = []
    if not pending and not done and not upcoming:
        lines.append("🎉 Все дела сделаны, порядок!")
    else:
        for tid, t, occ, late in pending:
            suffix = "" if late == 0 else " ⏰ не сделано раньше"
            owner = current_owner(t, state)
            lines.append(f"⬜ {t['title']} {level_emoji(t)} — {owner}{suffix}")
            row = [InlineKeyboardButton(f"✅ {t['title'][:26]}", callback_data=f"tdone:{tid}:{occ.isoformat()}")]
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


async def safe_delete_message(msg):
    """Безопасно удаляет сообщение пользователя при нажатии на кнопку панели."""
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass


# ---------- диалоги добавления дел ----------
async def prompt_quick_todo(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает текст быстрого дела без ввода команд."""
    context.user_data["awaiting"] = "quick_todo"
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚡ *Напишите быстрое дело на сегодня:*\n"
            "Например: `Забрать посылку` или `Помыть обувь | Алина`"
        ),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Дело на сегодня..."),
        parse_mode="Markdown",
    )


async def prompt_full_task(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает параметры регулярного задания без ввода команд."""
    context.user_data["awaiting"] = "full_task"
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "➕ *Добавление регулярного задания*\n\n"
            "Напишите параметры через « | »:\n"
            "`Название | кто | срок | сложность`\n\n"
            "📌 *Примеры срока:*\n"
            "• `каждую среду`\n"
            "• `каждые 14 дней`\n"
            "• `1 числа`\n"
            "• `раз в 3 месяца 1 числа`\n"
            "• `раз в год 15.03`\n\n"
            "💡 *Пример сообщения целиком:*\n"
            "`🍳 Мытьё плиты | Сосед / Мы с женой | каждую субботу | средняя`"
        ),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Название | кто | срок | сложность"),
        parse_mode="Markdown",
    )


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
    """Показывает интерактивную панель меню."""
    await update.message.reply_text(
        "📱 *Панель управления бытом:*\nВыберите нужное действие:",
        reply_markup=INLINE_MENU,
        parse_mode="Markdown",
    )


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
    emoji, name = LEVELS[task["level"]]
    await update.message.reply_text(
        f"Добавлено №{len(state['tasks'])}: {task['title']}\n"
        f"Кто: {' → '.join(task['owners'])}\nСрок: {describe(task['schedule'])}\n"
        f"Сложность: {emoji} {name}"
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
        "level": 1,
    }
    add_task(state, task)
    save_state(state)
    await update_pinned(context.bot, state)
    await update.message.reply_text(f"✅ Добавлено дело на сегодня: {title} — {who}")


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    chat_id = update.effective_chat.id
    if not state["tasks"]:
        await context.bot.send_message(chat_id=chat_id, text="Заданий пока нет. Добавьте через кнопки ниже или /add")
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
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")


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
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="Markdown")


async def preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    ready = [
        ("🛏 Сменить постельное бельё", ["Мы с женой"], "каждые 14 дней", 2),
        ("🧹 Генеральная уборка", ["Мы с женой"], "раз в месяц 1 числа", 3),
        ("🪟 Помыть окна", ["Мы с женой"], "раз в 3 месяца 1 числа", 3),
        ("❄️ Помыть холодильник", ["Мы с женой"], "раз в 2 месяца 1 числа", 2),
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
    _, tid, occ_iso = query.data.split(":", 2)
    state = load_state()
    if not check_pinned_access(query, state):
        owner = state.get("pinned", {}).get("owner_name", "администратора")
        await query.answer(f"⛔ Этот закреплённый пульт настроен только для {owner}!", show_alert=True)
        return
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
    if not check_pinned_access(query, state):
        owner = state.get("pinned", {}).get("owner_name", "администратора")
        await query.answer(f"⛔ Этот закреплённый пульт настроен только для {owner}!", show_alert=True)
        return
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
    if not check_pinned_access(query, state):
        owner = state.get("pinned", {}).get("owner_name", "администратора")
        await query.answer(f"⛔ Этот закреплённый пульт настроен только для {owner}!", show_alert=True)
        return
    task = state["tasks"].get(tid)
    if task and len(task["owners"]) > 1:
        task["idx"] = (task["idx"] + 1) % len(task["owners"])
        save_state(state)
        await query.answer(f"Передано: теперь {current_owner(task, state)}")
    else:
        await query.answer("Передать некому")
    await refresh_today(query, state)
    await update_pinned(context.bot, state, skip_id=query.message.message_id)


# ---------- покупки (интерактивный UX) ----------
def build_shopping_view(state: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    items = state.get("shopping", [])
    if not items:
        return "🛒 *Список покупок пуст.*\n\nНажмите «➕ Добавить в покупки», чтобы внести товары.", None

    lines = ["🛒 *Отметьте купленное:* (нажмите на кнопку товара, чтобы вычеркнуть)"]
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

    buttons.append([
        InlineKeyboardButton("➕ Добавить товар", callback_data="shopmenu:add"),
        InlineKeyboardButton("🔙 Меню покупок", callback_data="shopmenu:main"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def shopping_menu_cmd(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Открывает меню покупок и переключает нижнюю панель на кнопки покупок."""
    state = load_state()
    count = len(state.get("shopping", []))
    info = f"Всего товаров в списке: *{count} шт.*" if count else "Список покупок пока пуст."
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🛒 *Список покупок*\n{info}\n\n"
            "Выберите нужное действие на панели ниже:\n"
            "• *📋 Просмотр списка* — посмотреть текущие покупки\n"
            "• *➕ Добавить в покупки* — добавить товары без лишних команд\n"
            "• *✅ Отметить купленное* — интерактивный список с кнопками\n"
            "• *🔙 Главное меню* — вернуться в основное меню"
        ),
        reply_markup=SHOPPING_KEYBOARD,
        parse_mode="Markdown",
    )


async def show_shopping_list(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий список покупок с разделением на нужные и купленные."""
    state = load_state()
    items = state.get("shopping", [])
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    if not items:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🛒 *Список покупок пуст.*\n\nНажмите кнопку *➕ Добавить в покупки*, чтобы внести товары.",
            reply_markup=SHOPPING_KEYBOARD,
            parse_mode="Markdown",
        )
        return

    pending, done = [], []
    for idx, it in enumerate(items, 1):
        if it.get("done", False):
            done.append(f"~{idx}. {it['item']} ({it['by']})~ ✅")
        else:
            pending.append(f"{idx}. {it['item']} ({it['by']})")

    lines = ["🛒 *Текущий список покупок:*"]
    if pending:
        lines.append("\n*Нужно купить:*")
        lines.extend(f"• {p}" for p in pending)
    if done:
        lines.append("\n*Уже куплено:*")
        lines.extend(f"• {d}" for d in done)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отметить купленное", callback_data="shopmenu:mark"),
         InlineKeyboardButton("➕ Добавить", callback_data="shopmenu:add")],
    ])
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), reply_markup=kb, parse_mode="Markdown")


async def prompt_shopping_add(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает товары для списка покупок без ввода команд."""
    context.user_data["awaiting"] = "shopping_add"
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🛒 *Что нужно купить?*\n\n"
            "Напишите товары (можно через запятую или с новой строки):\n"
            "Например: `Молоко, сыр, хлеб, чай`"
        ),
        reply_markup=ForceReply(selective=True, input_field_placeholder="Молоко, сыр, хлеб..."),
        parse_mode="Markdown",
    )


async def mark_shopping_cmd(target_message, context: ContextTypes.DEFAULT_TYPE):
    """Показывает интерактивный чек-лист для вычеркивания покупок."""
    state = load_state()
    text, kb = build_shopping_view(state)
    chat_id = target_message.chat_id if hasattr(target_message, "chat_id") else target_message.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="Markdown")


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
    if not check_pinned_access(query, state):
        owner = state.get("pinned", {}).get("owner_name", "администратора")
        await query.answer(f"⛔ Этот закреплённый пульт настроен только для {owner}!", show_alert=True)
        return

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
    if not top:
        return "📊 *Итоги недели:* пока ничего не отмечено."
    lines = ["📊 *Итоги недели (баллы за сложность):*"]
    for who, n, c in top:
        lines.append(f"• {who} — {n} б. ({c} ✅)")
    if len(top) > 1 and top[0][1] == top[1][1]:
        lines.append("🏆 Ничья на первом месте!")
    else:
        lines.append(f"🏆 Герой недели: {top[0][0]}")
    return "\n".join(lines)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text = weekly_text(state)
    total = score_lines(state["log"])
    if total:
        text += "\n\n*За всё время:*\n" + "\n".join(f"• {w} — {n} б. ({c} ✅)" for w, n, c in total)
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


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
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)


async def on_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    state = load_state()
    if not check_pinned_access(q, state):
        owner = state.get("pinned", {}).get("owner_name", "администратора")
        await q.answer(f"⛔ Редактор заданий доступен только администратору ({owner})!", show_alert=True)
        return
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


# ---------- закреплённый чек-лист и интерактивный пульт ----------
async def update_pinned(bot, state: dict, skip_id=None) -> None:
    p = state.get("pinned")
    if not p or not p.get("message_id") or p.get("message_id") == skip_id:
        return
    text, kb = build_pinned_dashboard(state, p.get("view", "today"))
    try:
        await bot.edit_message_text(text, chat_id=p["chat_id"], message_id=p["message_id"], reply_markup=kb, parse_mode="Markdown")
    except BadRequest as e:
        err = str(e).lower()
        if "message to edit not found" in err:
            state["pinned"] = None
            save_state(state)
        elif "message is not modified" in err:
            pass
        else:
            logging.warning("Ошибка редактирования закрепленного сообщения: %s", e)
    except Exception as e:
        logging.warning("Не удалось обновить закреплённое сообщение: %s", e)


async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    chat_id = update.effective_chat.id
    user = update.effective_user
    old = state.get("pinned")
    if old:
        try:
            await context.bot.unpin_chat_message(old["chat_id"], old["message_id"])
        except Exception:
            pass

    owner_id = user.id if user else None
    owner_name = user_name(user)
    state["pinned"] = {
        "chat_id": chat_id,
        "message_id": None,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "only_owner": True,
        "view": "today",
    }
    if not state.get("chat_id") or update.effective_chat.type in ("group", "supergroup"):
        state["chat_id"] = chat_id
    save_state(state)

    text, kb = build_pinned_dashboard(state, "today")
    msg = await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")
    state["pinned"]["message_id"] = msg.message_id
    save_state(state)

    try:
        await context.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        await context.bot.send_message(
            chat_id,
            f"📌 *«Список дел на сегодня» закреплён!*\n"
            f"👤 Владелец: *{owner_name}* (ID: `{owner_id}`)\n"
            f"💡 Дела и список покупок могут отмечать все соседи.\n"
            f"🔒 Редактирование и настройки доступны только админу.",
            parse_mode="Markdown",
        )
    except Exception:
        await context.bot.send_message(
            chat_id,
            "Сам закрепить не смог. Удерживайте сообщение с пультом → «Закрепить».\n"
            "Чтобы бот закреплял сам, сделайте его администратором с правом «Закреплять сообщения»."
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


# ---------- обработчики кнопок меню (Inline + Reply) ----------
async def on_pin_menu_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок навигации и управления на закреплённом пульте."""
    query = update.callback_query
    state = load_state()

    # Проверка прав доступа: если пульт в режиме 'только владелец'
    if not check_pinned_access(query, state):
        p = state.get("pinned", {})
        owner = p.get("owner_name", "администратора")
        await query.answer(f"⛔ Этот закреплённый пульт настроен только для {owner}!", show_alert=True)
        return

    data = query.data
    p = state.setdefault("pinned", {})

    if data.startswith("pinmenu:tab:"):
        tab = data.split(":")[2]
        p["view"] = tab
        save_state(state)
        text, kb = build_pinned_dashboard(state, tab)
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
        await query.answer()
        return

    elif data == "pinmenu:refresh":
        text, kb = build_pinned_dashboard(state, p.get("view", "today"))
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            await query.answer("Пульт обновлён 🔄")
        except Exception:
            await query.answer("Без изменений")
        return

    elif data == "pinmenu:tglaccess":
        allowed_id = ADMIN_ID or p.get("owner_id")
        if allowed_id and str(query.from_user.id) != str(allowed_id):
            await query.answer("⛔ Только владелец пульта может менять настройки доступа!", show_alert=True)
            return

        cur = p.get("only_owner", True)
        p["only_owner"] = not cur
        save_state(state)
        mode = "🔒 Защита включена: дела отмечают все соседи, а настройки и редактор только для админа" if p["only_owner"] else "👥 Полный доступ: редактор и настройки открыты всем"
        await query.answer(mode, show_alert=True)
        text, kb = build_pinned_dashboard(state, p.get("view", "today"))
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            pass
        return

    elif data == "pinmenu:todo":
        await query.answer()
        await prompt_quick_todo(query.message, context)
        return

    elif data == "pinmenu:add":
        await query.answer()
        await prompt_full_task(query.message, context)
        return

    elif data == "pinmenu:shop_add":
        await query.answer()
        await prompt_shopping_add(query.message, context)
        return


async def on_shop_menu_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка инлайн-кнопок управления покупками."""
    q = update.callback_query
    await q.answer()
    act = q.data.split(":")[1]
    state = load_state()

    if act == "main":
        count = len(state.get("shopping", []))
        info = f"Всего в списке: *{count} шт.*" if count else "Список пока пуст."
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 Просмотр списка", callback_data="shopmenu:view"),
                InlineKeyboardButton("➕ Добавить", callback_data="shopmenu:add"),
            ],
            [
                InlineKeyboardButton("✅ Отметить купленное", callback_data="shopmenu:mark"),
            ],
            [
                InlineKeyboardButton("🔙 Главное меню", callback_data="menu:main"),
            ],
        ])
        await q.edit_message_text(
            f"🛒 *Список покупок*\n{info}\n\nВыберите действие:",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    elif act == "view":
        items = state.get("shopping", [])
        if not items:
            text = "🛒 *Список покупок пуст.*\n\nНажмите «➕ Добавить», чтобы внести товары."
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить", callback_data="shopmenu:add")],
                [InlineKeyboardButton("🔙 Назад", callback_data="shopmenu:main")],
            ])
        else:
            pending, done = [], []
            for idx, it in enumerate(items, 1):
                if it.get("done", False):
                    done.append(f"~{idx}. {it['item']} ({it['by']})~ ✅")
                else:
                    pending.append(f"{idx}. {it['item']} ({it['by']})")
            lines = ["🛒 *Текущий список покупок:*"]
            if pending:
                lines.append("\n*Нужно купить:*")
                lines.extend(f"• {p}" for p in pending)
            if done:
                lines.append("\n*Уже куплено:*")
                lines.extend(f"• {d}" for d in done)
            text = "\n".join(lines)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Отметить купленное", callback_data="shopmenu:mark"),
                 InlineKeyboardButton("➕ Добавить", callback_data="shopmenu:add")],
                [InlineKeyboardButton("🔙 Назад", callback_data="shopmenu:main")],
            ])
        try:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await q.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    elif act == "add":
        await prompt_shopping_add(q.message, context)
    elif act == "mark":
        text, kb = build_shopping_view(state)
        try:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await q.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def on_menu_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок из /menu (работает 100% стабильно в группах)."""
    q = update.callback_query
    await q.answer()
    act = q.data.split(":")[1]
    state = load_state()

    if act == "today":
        text, kb, _ = build_today(state)
        await q.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    elif act == "todo":
        await prompt_quick_todo(q.message, context)
    elif act == "add":
        await prompt_full_task(q.message, context)
    elif act == "list":
        await list_tasks(update, context)
    elif act == "top":
        await top(update, context)
    elif act == "shop":
        count = len(state.get("shopping", []))
        info = f"Всего в списке: *{count} шт.*" if count else "Список пока пуст."
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 Просмотр списка", callback_data="shopmenu:view"),
                InlineKeyboardButton("➕ Добавить", callback_data="shopmenu:add"),
            ],
            [
                InlineKeyboardButton("✅ Отметить купленное", callback_data="shopmenu:mark"),
            ],
            [
                InlineKeyboardButton("🔙 Главное меню", callback_data="menu:main"),
            ],
        ])
        await q.edit_message_text(
            f"🛒 *Список покупок*\n{info}\n\nВыберите действие:",
            reply_markup=kb,
            parse_mode="Markdown",
        )
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

    # 1. Сначала проверяем нажатия на кнопки меню (они отменяют любой режим ожидания)
    # При нажатии на любую кнопку нижней панели моментально удаляем сообщение пользователя, чтобы не засорять чат
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
    elif "рейтинг" in text_lower or "топ" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await top(update, context)
        return
    elif "просмотр списка" in text_lower or "посмотреть список" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await show_shopping_list(msg, context)
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
        await mark_shopping_cmd(msg, context)
        return
    elif "покупк" in text_lower or "список покупок" in text_lower:
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        await shopping_menu_cmd(msg, context)
        return
    elif "главное меню" in text_lower or text_lower in ("меню", "назад", "в меню"):
        await safe_delete_message(msg)
        context.user_data.pop("awaiting", None)
        context.user_data.pop("edit", None)
        # Бесшумно переключаем панель на MAIN_KEYBOARD без текста "ГЛАВНОЕ МЕНЮ:"
        try:
            tmp = await context.bot.send_message(
                chat_id=msg.chat_id,
                text="🔄",
                reply_markup=MAIN_KEYBOARD,
            )
            await asyncio.sleep(0.2)
            await tmp.delete()
        except Exception:
            pass
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
            "level": 1,
        }
        add_task(state, task)
        save_state(state)
        await update_pinned(context.bot, state)
        await msg.reply_text(f"✅ Добавлено быстрое дело на сегодня:\n*{title}* — {who}", parse_mode="Markdown")
        return

    # 4. Режим прямого ввода регулярного задания (без команды /add)
    if awaiting == "full_task":
        try:
            task = build_task([p.strip() for p in text.split("|")])
        except ValueError as e:
            await msg.reply_text(
                f"{e}\n\nПопробуйте ещё раз (или нажмите кнопку в меню для отмены).\n"
                "Формат: `Название | кто | срок | сложность`",
                parse_mode="Markdown",
            )
            return
        context.user_data.pop("awaiting", None)
        state = load_state()
        add_task(state, task)
        save_state(state)
        await update_pinned(context.bot, state)
        emoji, name = LEVELS[task["level"]]
        await msg.reply_text(
            f"✅ Добавлено регулярное задание №{len(state['tasks'])}:\n"
            f"*{task['title']}*\n"
            f"Кто: {' → '.join(task['owners'])}\n"
            f"Срок: {describe(task['schedule'])}\n"
            f"Сложность: {emoji} {name}",
            parse_mode="Markdown",
        )
        return

    # 5. Режим прямого ввода покупок (без команды /buy)
    if awaiting == "shopping_add":
        context.user_data.pop("awaiting", None)
        items = [i.strip() for i in re.split(r"[,;\n]", text) if i.strip()]
        if not items:
            await msg.reply_text("Не удалось распознать товары. Попробуйте ещё раз.", reply_markup=SHOPPING_KEYBOARD)
            return
        state = load_state()
        who = user_name(update.effective_user)
        for item in items:
            state["shopping"].append({"item": item, "by": who, "done": False})
        save_state(state)
        await msg.reply_text(
            f"🛒 *Добавлено в покупки ({len(items)} шт.):*\n" +
            "\n".join(f"• {it}" for it in items) +
            f"\n\nВсего в списке: *{len(state['shopping'])} шт.*",
            reply_markup=SHOPPING_KEYBOARD,
            parse_mode="Markdown",
        )
        return


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
        ("buy", buy), ("shopping", shopping), ("bought", bought),
        ("top", top),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_today_button, pattern=r"^tdone:"))
    app.add_handler(CallbackQueryHandler(on_undo_button, pattern=r"^tundo$"))
    app.add_handler(CallbackQueryHandler(on_skip, pattern=r"^skip:"))
    app.add_handler(CallbackQueryHandler(on_pin_menu_inline, pattern=r"^pinmenu:"))
    app.add_handler(CallbackQueryHandler(on_shopping_button, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(on_shop_menu_inline, pattern=r"^shopmenu:"))
    app.add_handler(CallbackQueryHandler(on_menu_inline, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(on_edit, pattern=r"^ed:"))

    # Единый обработчик текста
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_general_text))

    app.run_polling()


if __name__ == "__main__":
    main()
