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
    ("undo", "
     
