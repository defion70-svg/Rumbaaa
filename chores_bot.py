"""
Бот для уборки в квартире (Telegram) — версия, где задания добавляете вы сами.

Команды в чате:
  /add Название | кто | срок   — добавить задание
  /list                        — все задания и что сделано
  /del N                       — удалить задание по номеру
  /help                        — подсказка

Примеры:
  /add 🍳 Кухня | Сосед/Мы с женой | каждую среду
  /add Вынести мусор | Сосед | ежедневно
  /add Окна | Мы с женой | раз в 2 недели сб
  /add Разморозить холодильник | Сосед | 1 числа
  /add Купить корм | Мы с женой | 15.10

«кто»: одно имя — задание всегда на нём; несколько через «/» — очередь
меняется по кругу после каждой отметки «Сделано».

Запуск:
  pip install "python-telegram-bot[job-queue]"
  export BOT_TOKEN="токен от @BotFather"
  python chores_bot.py
Потом добавьте бота в чат и отправьте там /start.
"""
import json
import logging
import os
import re
from datetime import date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ---------- НАСТРОЙКИ ----------
TOKEN = os.environ["BOT_TOKEN"]
TZ = ZoneInfo("Europe/Moscow")      # ваш часовой пояс
REMIND_AT = time(10, 0, tzinfo=TZ)  # во сколько напоминать
REPEAT_DAYS = 2                     # сколько дней после срока напоминать повторно
# --------------------------------

STATE_FILE = Path("state.json")
logging.basicConfig(level=logging.INFO)

DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
ABBR = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
PREFIX3 = {"пон": 0, "вто": 1, "сре": 2, "чет": 3, "пят": 4, "суб": 5, "вос": 6}

HELP = (
    "Добавить задание:\n"
    "/add Название | кто | срок\n\n"
    "Срок можно писать так:\n"
    "• ежедневно\n"
    "• каждую среду (или просто «ср»)\n"
    "• раз в 2 недели сб\n"
    "• 1 числа (каждый месяц, число от 1 до 28)\n"
    "• 15.10 (разово)\n\n"
    "«кто»: одно имя, либо несколько через «/» — тогда очередь меняется "
    "после каждого «Сделано».\n\n"
    "Пример:\n/add Кухня | Сосед/Мы с женой | каждую среду\n\n"
    "/list — все задания\n/del N — удалить задание"
)


# ---------- хранение ----------
def default_tasks() -> dict:
    def w(title, owners, wd):
        return {
            "title": title, "owners": owners, "idx": 0, "done_for": None,
            "schedule": {"type": "weekly", "weekday": wd, "every": 1},
        }
    return {
        "1": w("🍳 Кухня", ["Сосед", "Мы с женой"], 2),
        "2": w("🚿 Ванная", ["Мы с женой", "Сосед"], 5),
        "3": w("🛋 Гостиная + пол", ["Мы с женой"], 6),
    }


def load_state() -> dict:
    state = {"chat_id": None}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if "tasks" not in state:  # первый запуск или старый формат
        state["tasks"] = default_tasks()
        state["next_id"] = 4
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- расписание ----------
def parse_schedule(text: str) -> dict:
    t = text.lower().strip()
    if "ежедневно" in t or "каждый день" in t:
        return {"type": "daily"}

    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\b", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        today = date.today()
        try:
            d = date(today.year, month, day)
            if d < today:
                d = date(today.year + 1, month, day)
        except ValueError:
            raise ValueError("Не понял дату. Формат: 15.10")
        return {"type": "once", "date": d.isoformat()}

    m = re.search(r"\b(\d{1,2})\s*(?:-?го\s*)?числ", t)
    if m:
        day = int(m.group(1))
        if not 1 <= day <= 28:
            raise ValueError("Число месяца должно быть от 1 до 28.")
        return {"type": "monthly", "day": day}

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
            "Не понял срок. Примеры: «каждую среду», «ежедневно», "
            "«раз в 2 недели сб», «1 числа», «15.10»."
        )
    every = 1
    m = re.search(r"(\d+)\s*недел", t)
    if m:
        every = max(1, int(m.group(1)))
    sched = {"type": "weekly", "weekday": weekday, "every": every}
    if every > 1:
        today = date.today()
        anchor = today + timedelta(days=(weekday - today.weekday()) % 7)
        sched["anchor"] = anchor.isoformat()
    return sched


def describe(s: dict) -> str:
    t = s["type"]
    if t == "daily":
        return "ежедневно"
    if t == "weekly":
        day = DAY_NAMES[s["weekday"]]
        return f"каждую неделю, {day}" if s.get("every", 1) == 1 else f"раз в {s['every']} нед., {day}"
    if t == "monthly":
        return f"{s['day']}-го числа каждого месяца"
    d = date.fromisoformat(s["date"])
    return f"разово, {d.day:02d}.{d.month:02d}"


def occurrence(s: dict, today: date):
    """Последняя дата срока, которая уже наступила (или None)."""
    t = s["type"]
    if t == "daily":
        return today
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
        d = s["day"]
        if today.day >= d:
            return today.replace(day=d)
        prev = today.replace(day=1) - timedelta(days=1)
        return prev.replace(day=d)
    d = date.fromisoformat(s["date"])
    return d if d <= today else None


def current_owner(task: dict) -> str:
    return task["owners"][task["idx"] % len(task["owners"])]


def window(task: dict) -> int:
    return 0 if task["schedule"]["type"] == "daily" else REPEAT_DAYS


# ---------- команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text("Привет! Буду напоминать об уборке в этом чате.\n\n" + HELP)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts_raw = update.message.text.split(None, 1)
    if len(parts_raw) < 2:
        await update.message.reply_text(HELP)
        return
    parts = [p.strip() for p in parts_raw[1].split("|")]
    if len(parts) != 3 or not all(parts):
        await update.message.reply_text("Нужно три части через «|»: Название | кто | срок\n\n" + HELP)
        return
    title, owners_s, when = parts
    owners = [o.strip() for o in owners_s.split("/") if o.strip()]
    try:
        sched = parse_schedule(when)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return

    state = load_state()
    tid = str(state["next_id"])
    state["next_id"] += 1
    state["tasks"][tid] = {
        "title": title, "owners": owners, "idx": 0, "schedule": sched, "done_for": None,
    }
    if not state.get("chat_id"):
        state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        f"Добавлено №{tid}: {title}\nКто: {' → '.join(owners)}\nСрок: {describe(sched)}"
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state["tasks"]:
        await update.message.reply_text("Заданий пока нет. Добавьте через /add")
        return
    today = date.today()
    lines = ["📋 Задания:"]
    for tid, t in state["tasks"].items():
        occ = occurrence(t["schedule"], today)
        if occ and (today - occ).days <= window(t):
            mark = "✅" if t["done_for"] == occ.isoformat() else "⬜"
        else:
            mark = "▫️"
        lines.append(f"{tid}. {mark} {t['title']} — {current_owner(t)} ({describe(t['schedule'])})")
    lines.append("\n⬜ ждёт, ✅ сделано, ▫️ срок ещё не наступил. Удалить: /del N")
    await update.message.reply_text("\n".join(lines))


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажите номер: /del 3 (номера видно в /list)")
        return
    state = load_state()
    task = state["tasks"].pop(context.args[0], None)
    if not task:
        await update.message.reply_text("Нет задания с таким номером.")
        return
    save_state(state)
    await update.message.reply_text(f"Удалено: {task['title']}")


# ---------- напоминания ----------
async def daily_check(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    today = date.today()
    changed = False
    for tid, t in list(state["tasks"].items()):
        occ = occurrence(t["schedule"], today)
        if occ is None:
            continue
        late = (today - occ).days
        if late > window(t):
            if t["schedule"]["type"] == "once":  # разовое задание устарело
                del state["tasks"][tid]
                changed = True
            continue
        if t["done_for"] == occ.isoformat() or not state.get("chat_id"):
            continue
        owner = current_owner(t)
        text = (
            f"{t['title']} — сегодня: {owner}" if late == 0
            else f"⏰ {t['title']} ещё не отмечено ({owner})"
        )
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Сделано ✅", callback_data=f"done:{tid}:{occ.isoformat()}")]]
        )
        await context.bot.send_message(state["chat_id"], text, reply_markup=kb)
    if changed:
        save_state(state)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, tid, occ_iso = query.data.split(":", 2)
    state = load_state()
    task = state["tasks"].get(tid)
    if not task:
        await query.edit_message_text("Это задание уже удалено.")
        return
    who = query.from_user.first_name
    if task["done_for"] == occ_iso:
        await query.edit_message_text(f"{task['title']} — уже отмечено ✅")
        return
    task["done_for"] = occ_iso
    task["idx"] = (task["idx"] + 1) % len(task["owners"])
    save_state(state)
    text = f"{task['title']} — сделано ✅ ({who})"
    if len(task["owners"]) > 1:
        text += f"\nВ следующий раз: {current_owner(task)}"
    await query.edit_message_text(text)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("del", delete))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^done:"))
    app.job_queue.run_daily(daily_check, REMIND_AT)
    app.run_polling()


if __name__ == "__main__":
    main()
