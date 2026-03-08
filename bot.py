#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telegram-бот для отслеживания онлайн-премьер фильмов и сериалов
Данные: kinopoisk.dev (ПоискКино API)
Обработка описаний: YandexGPT
"""

import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional

import pytz
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode


# ==== НАСТРОЙКИ API-КЛЮЧЕЙ ====

# токен Telegram-бота от @BotFather
TELEGRAM_TOKEN = "8579233339:AAHnjx9an6YQ0tgKDKvikLYv386FBpat_Wk"

# API-ключ kinopoisk.dev (X-API-KEY)
KINOPOISK_DEV_KEY = "TS3W9GX-KVZM9ZW-KRYPNWY-D90E8K5"

# Доступ к YandexGPT через Yandex Cloud (AI Studio)
YANDEX_API_KEY = "AQVNyoc1Fgv0ssfv4XtU2YeOWmo-9XjVd0BrsXP8"
YANDEX_FOLDER_ID = "ajeagsqhc2vkmb3uvobr"

# ID чата для авторассылки по понедельникам в 10:00 МСК
TARGET_CHAT_ID = None


# ==== НАСТРОЙКА ЛОГИРОВАНИЯ ====

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ==== МОСКОВСКИЙ ЧАСОВОЙ ПОЯС ====

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


# ==== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДАТ ====

def get_today_date() -> date:
    """Сегодняшняя дата в московском часовом поясе."""
    return datetime.now(MOSCOW_TZ).date()


def get_last_full_week() -> (date, date):
    """Полная прошлая неделя (понедельник-воскресенье)."""
    today = get_today_date()
    current_monday = today - timedelta(days=today.weekday())
    last_monday = current_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


# ==== РАБОТА С kinopoisk.dev ====

KINOPOISK_DEV_URL = "https://api.kinopoisk.dev/v1.4/movie"  # универсальный поиск фильмов[web:82][web:103]


def _kp_dev_request(params: Dict) -> Dict:
    """Сделать запрос к kinopoisk.dev с заданными параметрами."""
    headers = {
        "X-API-KEY": KINOPOISK_DEV_KEY,
        "Accept": "application/json",
    }
    response = requests.get(KINOPOISK_DEV_URL, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def get_releases_for_period(start_date: date, end_date: date) -> List[Dict]:
    """
    Получает премьеры (онлайн/цифровые и вообще релизы) за период [start_date, end_date]
    по данным kinopoisk.dev.

    Стратегия:
    - Используем универсальный поиск /v1.4/movie с фильтрами:
      * typeNumber: 1 (фильмы) и 2 (сериалы) — можно расширять.
      * premiere.world / premiere.russia / releaseYears / yearRange при необходимости.
    - Здесь для простоты фильтруем по полю 'premiere.world' и 'premiere.russia' по датам,
      а также по 'year' в разумных пределах.[web:82][web:115]
    """

    docs: List[Dict] = []
    page = 1
    limit = 50

    # kinopoisk.dev позволяет фильтровать по диапазону дат премьеры, но чтобы не усложнять
    # фильтр на стороне API, мы запрашиваем "окно" по годам и фильтруем по датам в коде.[web:115]

    # Берём год начала и конца периода:
    from_year = start_date.year
    to_year = end_date.year

    while True:
        params = {
            "page": page,
            "limit": limit,
            # Ограничиваем год выпуска, чтобы не тянуть весь архив.
            "year": f"{from_year}-{to_year}",
            # Сортируем по дате мировой премьеры, чтобы свежие были выше.
            "sortField": "premiere.world",
            "sortType": -1,
        }

        try:
            data = _kp_dev_request(params)
        except Exception as e:
            logger.error(f"Ошибка запроса к kinopoisk.dev: {e}")
            break

        batch = data.get("docs", [])
        if not batch:
            break

        docs.extend(batch)

        # Если страница последняя — выходим.
        total = data.get("total", 0)
        if page * limit >= total:
            break

        page += 1
        if page > 5:  # ограничение на количество страниц, чтобы не перетягивать всё API
            break

    releases: List[Dict] = []

    for item in docs:
        # Пытаемся вытащить дату премьеры — сначала мировую, потом российскую.[web:82]
        premiere_info = item.get("premiere") or {}
        date_str = premiere_info.get("world") or premiere_info.get("russia")

        if not date_str:
            continue

        # Даты в kinopoisk.dev обычно в формате YYYY-MM-DD.
        try:
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
            "release_date": rel_date,
            "rating": rating_kp,
            "expectationRating": 0,  # у kinopoisk.dev отдельного "ratingAwait" нет
            "votes": votes_kp,
        }
        releases.append(release_data)

    return releases


def filter_releases_by_date(releases: List[Dict], target_date: date) -> List[Dict]:
    """Фильтрует релизы по конкретной дате."""
    return [r for r in releases if r["release_date"] == target_date]


def filter_releases_by_period(releases: List[Dict], start_date: date, end_date: date) -> List[Dict]:
    """Фильтрует релизы по периоду (включительно)."""
    return [r for r in releases if start_date <= r["release_date"] <= end_date]


def sort_and_limit_releases(
    releases: List[Dict],
    limit: int = 10,
    series_count: int = 7,
    movies_count: int = 3,
) -> List[Dict]:
    """
    Сортирует релизы по популярности и ограничивает количество.
    Для /week стараемся держать пропорцию сериалов/фильмов, но сейчас делим условно.
    """
    if len(releases) <= limit:
        return releases

    releases_sorted = sorted(
        releases,
        key=lambda x: (x["rating"] or 0, x["votes"] or 0),
        reverse=True,
    )

    # Пока нет явного разделения сериал/фильм, делаем простую выборку "верхних N".
    return releases_sorted[:limit]


# ==== YANDEXGPT ====

def format_releases_with_yandexgpt(releases: List[Dict], command_type: str) -> Optional[str]:
    """Отправляет релизы в YandexGPT и получает красиво оформленный Markdown."""
    if not releases:
        return None

    context = "Список релизов:\n\n"
    for i, release in enumerate(releases, 1):
        context += f"{i}. {release['nameRu']} ({release['year']})\n"
        context += f"Жанры: {release['genres']}\n"
        if release["rating"]:
            context += f"Рейтинг Кинопоиска: {release['rating']} (голосов: {release['votes']})\n"
        context += f"Описание: {release['description']}\n"
        context += f"ID для ссылки: {release['id']}\n\n"

    prompt = f"""Ты — помощник для создания описаний кинопремьер.

Перед тобой список из {len(releases)} онлайн-релизов фильмов и сериалов.

Твоя задача:
1. Выбрать все релизы (если их 10 или меньше) или лучшие 10 релизов (если их больше)
2. Для каждого релиза написать описание из 1-2 предложений (максимум 30 слов)
3. НЕ придумывать несуществующие данные (например, названия платформ, если они не указаны)
4. Строго соблюдать формат вывода

ФОРМАТ ВЫВОДА (строго):

1. __**Название фильма/сериала**__ (премьера)
https://www.kinopoisk.ru/film/ID/
*жанр1, жанр2, жанр3*
Описание релиза в 1-2 предложения, максимум 30 слов.

И так далее. Между релизами — пустая строка.

ВАЖНО:
- Название: одновременно жирное и подчёркнутое __**так**__
- После названия в скобках: (премьера)
- Ссылка на второй строке: https://www.kinopoisk.ru/film/ID/ (подставь реальный ID)
- Жанры на третьей строке в курсиве: *жанр1, жанр2*
- Описание — живое, без воды, 1-2 предложения, до 30 слов

{context}

Ответ (только отформатированный список, без вступлений):"""

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
                    "text": "Ты — эксперт по кино, который создаёт краткие и интересные описания фильмов и сериалов на русском языке.",
                },
                {"role": "user", "text": prompt},
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
    """Резервный формат без YandexGPT."""
    text = ""
    for i, release in enumerate(releases, 1):
        text += f"{i}. **{release['nameRu']}**\n"
        text += f"https://www.kinopoisk.ru/film/{release['id']}/\n"
        text += f"*{release['genres']}*\n\n"
    return text


# ==== ОБРАБОТЧИКИ КОМАНД ====

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🎬 **Добро пожаловать в бот премьер!**\n\n"
        "Я показываю премьеры фильмов и сериалов по данным kinopoisk.dev "
        "и формирую красивые описания с помощью YandexGPT.\n\n"
        "**Команды:**\n"
        "/today — премьеры сегодня\n"
        "/week — премьеры за прошлую неделю\n"
        "/help — справка\n"
    )

    keyboard = [["/today", "/week"], ["/help"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 **Справка по боту**\n\n"
        "/start — приветствие и меню\n"
        "/today — премьеры за сегодня (по данным kinopoisk.dev)\n"
        "/week — премьеры за прошлую неделю\n"
        "/help — эта справка\n\n"
        "Данные берутся из kinopoisk.dev, описания дописывает YandexGPT."
    )

    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Ищу премьеры на сегодня...")

    today = get_today_date()
    releases = get_releases_for_period(today, today)

    if not releases:
        await update.message.reply_text("Сегодня премьер не найдено")
        return

    filtered = sort_and_limit_releases(releases, limit=10)
    formatted = format_releases_with_yandexgpt(filtered, "today")

    if formatted:
        text = f"**Премьеры сегодня:**\n\n{formatted}"
    else:
        text = f"**Премьеры сегодня:**\n\n{format_releases_fallback(filtered)}"
        text += "\n⚠️ _Описания временно недоступны (ошибка ИИ)_"

    keyboard = [[InlineKeyboardButton("Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Ищу премьеры за прошлую неделю...")

    start_date, end_date = get_last_full_week()
    releases = get_releases_for_period(start_date, end_date)

    if not releases:
        await update.message.reply_text("На прошлой неделе премьер не найдено")
        return

    filtered = sort_and_limit_releases(releases, limit=10)
    formatted = format_releases_with_yandexgpt(filtered, "week")

    if formatted:
        text = f"**Премьеры за прошлую неделю:**\n\n{formatted}"
    else:
        text = f"**Премьеры за прошлую неделю:**\n\n{format_releases_fallback(filtered)}"
        text += "\n⚠️ _Описания временно недоступны (ошибка ИИ)_"

    keyboard = [[InlineKeyboardButton("Главное меню", callback_data="main_menu")]]
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

    menu_text = (
        "🎬 **Главное меню**\n\n"
        "/today — премьеры сегодня\n"
        "/week — премьеры за прошлую неделю\n"
        "/help — справка"
    )

    keyboard = [["/today", "/week"], ["/help"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await query.message.reply_text(
        menu_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )


# ==== АВТОРАССЫЛКА ====

async def weekly_auto_send(context: ContextTypes.DEFAULT_TYPE):
    if TARGET_CHAT_ID is None:
        logger.info("TARGET_CHAT_ID не установлен, авторассылка отключена")
        return

    start_date, end_date = get_last_full_week()
    releases = get_releases_for_period(start_date, end_date)

    if not releases:
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="На прошлой неделе премьер не найдено",
        )
        return

    filtered = sort_and_limit_releases(releases, limit=10)
    formatted = format_releases_with_yandexgpt(filtered, "week")

    if formatted:
        text = f"📅 **Еженедельная подборка**\n\n**Премьеры за прошлую неделю:**\n\n{formatted}"
    else:
        text = (
            f"📅 **Еженедельная подборка**\n\n**Премьеры за прошлую неделю:**\n\n"
            f"{format_releases_fallback(filtered)}"
        )
        text += "\n⚠️ _Описания временно недоступны (ошибка ИИ)_"

    keyboard = [[InlineKeyboardButton("Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


# ==== MAIN ====

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))

    if TARGET_CHAT_ID is not None:
        job_queue = application.job_queue
        moscow_10am = datetime.now(MOSCOW_TZ).replace(
            hour=10, minute=0, second=0, microsecond=0
        ).timetz()
        job_queue.run_daily(
            weekly_auto_send,
            time=moscow_10am,
            days=(0,),
            name="weekly_releases",
        )

    logger.info("Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
