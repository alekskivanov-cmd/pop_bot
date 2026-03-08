#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telegram-бот для уведомлений о премьерах фильмов/сериалов
Использует kinopoisk.dev API и YandexGPT для форматирования
"""

import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
import pytz
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ===========================================
# КОНФИГУРАЦИЯ
# ===========================================

TELEGRAM_TOKEN = "8579233339:AAHnjx9an6YQ0tgKDKvikLYv386FBpat_Wk"  # Токен Telegram-бота из BotFather

KINOPOISK_DEV_KEY = "TS3W9GX-KVZM9ZW-KRYPNWY-D90E8K5"  # API-ключ от kinopoisk.dev (X-API-KEY)

YANDEX_API_KEY = "AQVNyoc1Fgv0ssfv4XtU2YeOWmo-9XjVd0BrsXP8"
YANDEX_FOLDER_ID = "ajeagsqhc2vkmb3uvobr"  # YandexGPT из Yandex Cloud AI Studio

TARGET_CHAT_ID = None  # ID чата для еженедельной рассылки (если None, рассылка не работает)
# Для теста можно указать свой ID (найти через @userinfobot): например, 123456789

# ===========================================
# ЛОГИРОВАНИЕ
# ===========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===========================================
# КОНСТАНТЫ
# ===========================================
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# ===========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ДАТ
# ===========================================
def get_today_date() -> date:
    """Получить текущую дату в московской зоне."""
    return datetime.now(MOSCOW_TZ).date()


def get_last_full_week() -> (date, date):
    """
    Получить даты начала и конца предыдущей полной недели (пн-вс).
    """
    today = get_today_date()
    current_monday = today - timedelta(days=today.weekday())
    last_monday = current_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday

# ===========================================
# KINOPOISK.DEV API
# ===========================================
KINOPOISK_DEV_URL = "https://api.kinopoisk.dev/v1.4/movie"


def kp_dev_request_get(params: Dict) -> Dict:
    """
    Отправить GET-запрос к kinopoisk.dev с query-параметрами.
    Используется для фильтрации по премьерам.
    """
    headers = {
        "X-API-KEY": KINOPOISK_DEV_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = requests.get(KINOPOISK_DEV_URL, headers=headers, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def get_releases_for_period(start_date: date, end_date: date) -> List[Dict]:
    """
    Получить релизы за указанный период из kinopoisk.dev.

    Используем фильтр по полю premiere.russia с диапазоном дат.
    """
    from_str = start_date.strftime("%d.%m.%Y")
    to_str = end_date.strftime("%d.%m.%Y")

    docs: List[Dict] = []
    page = 1
    limit = 50

    logger.info(f"Запрос к kinopoisk.dev за период {from_str} - {to_str}")

    while True:
        params = {
            "page": page,
            "limit": limit,
            "type": ["movie", "tv-series"],
            "premiere.russia": f"{from_str}-{to_str}",
            "notNullFields": ["premiere.russia", "name"],
            "sortField": ["premiere.russia"],
            "sortType": [-1],
        }

        try:
            data = kp_dev_request_get(params)
        except Exception as e:
            logger.error(f"Ошибка запроса к kinopoisk.dev: {e}")
            break

        batch = data.get("docs", [])
        if not batch:
            break

        docs.extend(batch)

        total = data.get("total", 0)
        if page * limit >= total:
            break

        page += 1

        if page > 5:
            logger.info("Достигнут лимит страниц (5), прекращаем запросы")
            break

    logger.info(f"Получено {len(docs)} релизов от kinopoisk.dev")

    releases: List[Dict] = []
    for item in docs:
        premiere_info = item.get("premiere") or {}
        date_str = premiere_info.get("russia") or premiere_info.get("world")
        if not date_str:
            continue

        try:
            if "T" in date_str:
                date_str_clean = date_str.split("T")[0]
                rel_date = datetime.strptime(date_str_clean, "%Y-%m-%d").date()
            else:
                rel_date = datetime.fromisoformat(date_str).date()
        except Exception:
            continue

        if not (start_date <= rel_date <= end_date):
            continue

        kp_id = item.get("id")
        name_ru = item.get("name") or ""
        name_orig = item.get("alternativeName") or ""
        year_val = item.get("year")

        genres_list = item.get("genres") or []
        genres = ", ".join(
            g.get("name") if isinstance(g, dict) else str(g)
            for g in genres_list
        )

        description = item.get("shortDescription") or item.get("description") or ""

        rating_block = item.get("rating") or {}
        rating_kp = rating_block.get("kp") or 0

        votes_block = item.get("votes") or {}
        votes_kp = votes_block.get("kp") or 0

        release_data = {
            "id": kp_id,
            "nameRu": name_ru,
            "nameOriginal": name_orig,
            "year": year_val,
            "genres": genres,
            "description": description,
            "releaseDate": rel_date,
            "rating": rating_kp,
            "expectationRating": 0,
            "votes": votes_kp,
        }
        releases.append(release_data)

    return releases


def filter_releases_by_date(releases: List[Dict], target_date: date) -> List[Dict]:
    """Фильтровать релизы по конкретной дате."""
    return [r for r in releases if r["releaseDate"] == target_date]


def filter_releases_by_period(releases: List[Dict], start_date: date, end_date: date) -> List[Dict]:
    """Фильтровать релизы по периоду."""
    return [r for r in releases if start_date <= r["releaseDate"] <= end_date]


def sort_and_limit_releases(
    releases: List[Dict],
    limit: int = 10,
    series_count: int = 7,
    movies_count: int = 3,
) -> List[Dict]:
    """
    Отсортировать релизы по рейтингу и ограничить количество.

    Для команды week используется ограничение количества.
    Для команды today просто берём топ N по рейтингу.
    """
    if len(releases) <= limit:
        return releases

    releases_sorted = sorted(
        releases,
        key=lambda x: (x["rating"] or 0, x["votes"] or 0),
        reverse=True,
    )

    return releases_sorted[:limit]


# ===========================================
# ФОРМАТИРОВАНИЕ С YANDEXGPT
# ===========================================
def format_releases_with_yandex_gpt(releases: List[Dict], command_type: str) -> Optional[str]:
    """
    Отправить список релизов в YandexGPT для форматирования в виде Markdown.
    """
    if not releases:
        return None

    context = ""
    for i, release in enumerate(releases, 1):
        context += f"{i}. {release['nameRu']} ({release['year']})\n"
        context += f"   Жанры: {release['genres']}\n"
        if release["rating"]:
            context += f"   Рейтинг: {release['rating']} ({release['votes']} голосов)\n"
        context += f"   Описание: {release['description']}\n"
        context += f"   ID: {release['id']}\n"

    prompt = f"""Ты — редактор подборок фильмов и сериалов. Ниже список из {len(releases)} релизов.

Отформатируй их в виде Markdown по следующим правилам:
1. Для каждого фильма/сериала создай блок из 3–10 строк
2. Название выдели жирным, укажи год и жанры в одну строку (1-2 жанра, не более 30 символов)
3. Добавь одно эмодзи, подходящее по жанру
4. Дай короткое описание (1-2 предложения, максимум 30 слов)
5. Обязательно укажи ссылку на Кинопоиск: https://www.kinopoisk.ru/film/ID

Пример формата:
🎬 **Название фильма** (2024, драма)
Короткое описание в 1-2 предложения, максимум 30 слов.
[Подробнее на Кинопоиске](https://www.kinopoisk.ru/film/ID)

---

Список релизов:
{context}
"""

    try:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {YANDEX_API_KEY}",
            "x-folder-id": YANDEX_FOLDER_ID,
        }

        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.6,
                "maxTokens": 2000,
            },
            "messages": [
                {
                    "role": "system",
                    "text": "Ты — редактор подборок фильмов и сериалов. Форматируешь списки релизов в красивый Markdown.",
                },
                {
                    "role": "user",
                    "text": prompt,
                },
            ],
        }

        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()

        if "result" in result and "alternatives" in result["result"]:
            text = result["result"]["alternatives"][0]["message"]["text"]
            return text.strip()

        return None
    except Exception as e:
        logger.error(f"Ошибка при обращении к YandexGPT: {e}")
        return None


def format_releases_fallback(releases: List[Dict]) -> str:
    """
    Простое форматирование списка релизов без YandexGPT.
    """
    text = ""
    for i, release in enumerate(releases, 1):
        text += f"{i}. **{release['nameRu']}**\n"
        text += f"   https://www.kinopoisk.ru/film/{release['id']}\n"
        text += f"   {release['genres']}\n"
    return text


# ===========================================
# ОБРАБОТЧИКИ КОМАНД
# ===========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
Привет! 👋

Я помогу узнать о премьерах фильмов и сериалов через kinopoisk.dev и YandexGPT.

Команды:
• /today — что выходит сегодня
• /week — что вышло на прошлой неделе
• /help — справка
"""
    keyboard = [["today"], ["week"], ["help"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
Команды бота:

/start — начало работы
/today — список премьер на сегодня (из kinopoisk.dev)
/week — список премьер за прошлую неделю
/help — эта справка

Бот использует kinopoisk.dev для получения данных и YandexGPT для форматирования.
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ищу премьеры на сегодня...")

    today = get_today_date()
    releases = get_releases_for_period(today, today)

    if not releases:
        await update.message.reply_text("На сегодня премьер не найдено 😔")
        return

    filtered = sort_and_limit_releases(releases, limit=10)
    formatted = format_releases_with_yandex_gpt(filtered, "today")

    if formatted:
        text = f"**Премьеры на сегодня:**\n\n{formatted}"
    else:
        text = f"**Премьеры на сегодня:**\n\n{format_releases_fallback(filtered)}"

    text += "\n\n_Данные от kinopoisk.dev_"

    keyboard = [[InlineKeyboardButton("↩️ Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ищу премьеры за прошлую неделю...")

    start_date, end_date = get_last_full_week()
    releases = get_releases_for_period(start_date, end_date)

    if not releases:
        await update.message.reply_text("За прошлую неделю премьер не найдено 😔")
        return

    filtered = sort_and_limit_releases(releases, limit=10)
    formatted = format_releases_with_yandex_gpt(filtered, "week")

    if formatted:
        text = f"**Премьеры за прошлую неделю:**\n\n{formatted}"
    else:
        text = f"**Премьеры за прошлую неделю:**\n\n{format_releases_fallback(filtered)}"

    text += "\n\n_Данные от kinopoisk.dev_"

    keyboard = [[InlineKeyboardButton("↩️ Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    menu_text = "Выберите команду:"
    keyboard = [["today"], ["week"], ["help"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await query.message.reply_text(
        menu_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
    )


# ===========================================
# АВТОМАТИЧЕСКАЯ РАССЫЛКА
# ===========================================
async def weekly_auto_send(context: ContextTypes.DEFAULT_TYPE):
    """
    Еженедельная автоматическая отправка в указанный чат.
    """
    if TARGET_CHAT_ID is None:
        logger.info("TARGET_CHAT_ID не задан, пропускаем автоотправку")
        return

    start_date, end_date = get_last_full_week()
    releases = get_releases_for_period(start_date, end_date)

    if not releases:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="За прошлую неделю премьер не найдено 😔",
        )
        return

    filtered = sort_and_limit_releases(releases, limit=10)
    formatted = format_releases_with_yandex_gpt(filtered, "week")

    if formatted:
        text = f"**Премьеры за прошлую неделю:**\n\n{formatted}"
    else:
        text = f"**Премьеры за прошлую неделю:**\n\n{format_releases_fallback(filtered)}"

    text += "\n\n_Данные от kinopoisk.dev_"

    keyboard = [[InlineKeyboardButton("↩️ Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


# ===========================================
# MAIN
# ===========================================
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="main_menu"))

    if TARGET_CHAT_ID is not None:
        job_queue = application.job_queue
        moscow_10am = datetime.now(MOSCOW_TZ).replace(hour=10, minute=0, second=0, microsecond=0).timetz()
        job_queue.run_daily(
            weekly_auto_send,
            time=moscow_10am,
            days=(0,),
            name="weekly_releases",
        )
        logger.info("Автоматическая рассылка настроена!")

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
