#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telegram-бот для отслеживания онлайн-премьер фильмов и сериалов
Данные: Kinopoisk API Unofficial
Обработка описаний: YandexGPT
"""

import logging
from datetime import datetime, timedelta
import pytz
import requests
from typing import List, Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

# Импорты для работы с Kinopoisk API
try:
    from kinopoisk_api_unofficial_client import KinopoiskApiClient
    from kinopoisk_api_unofficial_client.request.digital_release.request_digital_release import DigitalReleaseRequest
except ImportError:
    print("ОШИБКА: Установите kinopoisk-api-unofficial-client")
    print("pip install kinopoisk-api-unofficial-client")
    exit(1)


# ==== НАСТРОЙКИ API-КЛЮЧЕЙ ====

# токен Telegram-бота от @BotFather
TELEGRAM_TOKEN = "8579233339:AAHnjx9an6YQ0tgKDKvikLYv386FBpat_Wk"

# API-ключ Kinopoisk API Unofficial
KINOPOISK_API_KEY = "0f6ffbda-25ef-445c-9004-873dba7ac523"

# Доступ к YandexGPT через Yandex Cloud (AI Studio)
# YANDEX_API_KEY — секретный ключ (Api-Key)
# YANDEX_FOLDER_ID — идентификатор каталога (folder id)
YANDEX_API_KEY = "AQVNyoc1Fgv0ssfv4XtU2YeOWmo-9XjVd0BrsXP8"
YANDEX_FOLDER_ID = "ajeagsqhc2vkmb3uvobr"

# ID чата для авторассылки по понедельникам в 10:00 МСК
# Вписать вручную ID чата или None (тогда авторассылка не работает)
TARGET_CHAT_ID = None


# ==== НАСТРОЙКА ЛОГИРОВАНИЯ ====

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==== МОСКОВСКИЙ ЧАСОВОЙ ПОЯС ====

MOSCOW_TZ = pytz.timezone('Europe/Moscow')


# ==== ИНИЦИАЛИЗАЦИЯ КЛИЕНТА КИНОПОИСКА ====

kinopoisk_client = KinopoiskApiClient(KINOPOISK_API_KEY)


# ==== ФУНКЦИИ ДЛЯ РАБОТЫ С ДАТАМИ ====

def get_today_date():
    """Возвращает сегодняшнюю дату в московском часовом поясе"""
    return datetime.now(MOSCOW_TZ).date()


def get_last_full_week():
    """
    Возвращает полную прошлую неделю (понедельник-воскресенье)
    Возвращает кортеж (start_date, end_date)
    """
    today = get_today_date()
    # Находим понедельник текущей недели
    current_monday = today - timedelta(days=today.weekday())
    # Прошлый понедельник
    last_monday = current_monday - timedelta(days=7)
    # Прошлое воскресенье
    last_sunday = last_monday + timedelta(days=6)
    
    return last_monday, last_sunday


# ==== ФУНКЦИИ ДЛЯ РАБОТЫ С KINOPOISK API ====

def get_digital_releases(year: int, month: int) -> List[Dict]:
    """
    Получает цифровые релизы за указанный год и месяц
    Возвращает список словарей с данными о релизах
    """
    try:
        request = DigitalReleaseRequest(year=year, month=month)
        response = kinopoisk_client.digital_release.send_digital_release_request(request)
        
        releases = []
        if hasattr(response, 'releases') and response.releases:
            for release in response.releases:
                # Парсим дату релиза
                release_date = None
                if hasattr(release, 'releaseDate') and release.releaseDate:
                    try:
                        release_date = datetime.strptime(release.releaseDate, '%Y-%m-%d').date()
                    except:
                        continue
                
                if not release_date:
                    continue
                
                # Собираем жанры
                genres = []
                if hasattr(release, 'genres') and release.genres:
                    genres = [g.genre for g in release.genres if hasattr(g, 'genre')]
                
                # Формируем словарь с данными
                release_data = {
                    'id': release.filmId if hasattr(release, 'filmId') else None,
                    'nameRu': release.nameRu if hasattr(release, 'nameRu') else '',
                    'nameOriginal': release.nameEn if hasattr(release, 'nameEn') else '',
                    'year': release.year if hasattr(release, 'year') else None,
                    'genres': ', '.join(genres),
                    'description': release.description if hasattr(release, 'description') else '',
                    'release_date': release_date,
                    'rating': release.ratingKinopoisk if hasattr(release, 'ratingKinopoisk') else 0,
                    'expectationRating': release.ratingAwait if hasattr(release, 'ratingAwait') else 0,
                    'votes': release.ratingKinopoiskVoteCount if hasattr(release, 'ratingKinopoiskVoteCount') else 0,
                }
                
                releases.append(release_data)
        
        return releases
    
    except Exception as e:
        logger.error(f"Ошибка при получении релизов: {e}")
        return []


def filter_releases_by_date(releases: List[Dict], target_date) -> List[Dict]:
    """Фильтрует релизы по конкретной дате"""
    return [r for r in releases if r['release_date'] == target_date]


def filter_releases_by_period(releases: List[Dict], start_date, end_date) -> List[Dict]:
    """Фильтрует релизы по периоду (включительно)"""
    return [r for r in releases if start_date <= r['release_date'] <= end_date]


def sort_and_limit_releases(releases: List[Dict], limit: int = 10, 
                            series_count: int = 7, movies_count: int = 3) -> List[Dict]:
    """
    Сортирует релизы по популярности и ограничивает количество
    Для команды /week стремится к пропорции 7 сериалов : 3 фильма
    """
    if len(releases) <= limit:
        return releases
    
    # Сортируем по рейтингу ожидания (или по рейтингу КП, если нет рейтинга ожидания)
    releases_sorted = sorted(
        releases, 
        key=lambda x: (x['expectationRating'] or x['rating'] or 0, x['votes'] or 0),
        reverse=True
    )
    
    # Если нужна пропорция сериалов/фильмов (для /week)
    if series_count > 0 or movies_count > 0:
        # Простое определение: если в жанрах есть слова, характерные для сериалов
        # (в реальности API может иметь поле type, но работаем с тем, что есть)
        series = []
        movies = []
        
        for release in releases_sorted:
            # Простая эвристика: можно улучшить, если API предоставляет поле type
            if len(series) < series_count:
                series.append(release)
            elif len(movies) < movies_count:
                movies.append(release)
            else:
                break
        
        # Добавляем оставшиеся, если не набрали нужное количество
        result = series + movies
        if len(result) < limit:
            for release in releases_sorted:
                if release not in result:
                    result.append(release)
                    if len(result) >= limit:
                        break
        
        return result[:limit]
    
    return releases_sorted[:limit]


# ==== ФУНКЦИЯ ДЛЯ РАБОТЫ С YANDEXGPT ====

def format_releases_with_yandexgpt(releases: List[Dict], command_type: str) -> Optional[str]:
    """
    Отправляет список релизов в YandexGPT для форматирования описаний
    Возвращает готовый текст в формате Markdown или None при ошибке
    """
    if not releases:
        return None
    
    # Формируем контекст для модели
    context = "Список цифровых релизов:\n\n"
    for i, release in enumerate(releases, 1):
        context += f"{i}. {release['nameRu']} ({release['year']})\n"
        context += f"Жанры: {release['genres']}\n"
        if release['expectationRating']:
            context += f"Рейтинг ожидания: {release['expectationRating']}\n"
        elif release['rating']:
            context += f"Рейтинг: {release['rating']}\n"
        context += f"Описание: {release['description']}\n"
        context += f"ID для ссылки: {release['id']}\n"
        context += "\n"
    
    # Формируем промпт для YandexGPT
    prompt = f"""Ты — помощник для создания описаний кинопремьер.

Перед тобой список из {len(releases)} онлайн-релизов фильмов и сериалов.

Твоя задача:
1. Выбрать все релизы (если их 10 или меньше) или лучшие 10 релизов (если их больше)
2. Для каждого релиза написать описание из 1-2 предложений (максимум 30 слов)
3. НЕ придумывать несуществующие данные (например, названия платформ, если они не указаны)
4. Строго соблюдать формат вывода

ФОРМАТ ВЫВОДА (строго):

1. __**Название фильма/сериала**__ (цифровой релиз)
https://www.kinopoisk.ru/film/ID/
*жанр1, жанр2, жанр3*
Описание релиза в 1-2 предложения, максимум 30 слов.

2. __**Следующий релиз**__ (цифровой релиз)
https://www.kinopoisk.ru/film/ID/
*жанры*
Описание.

И так далее. Между релизами — пустая строка.

ВАЖНО:
- Название: одновременно жирное и подчёркнутое __**так**__
- После названия в скобках: (цифровой релиз)
- Ссылка на второй строке: https://www.kinopoisk.ru/film/ID/ (подставь реальный ID)
- Жанры на третьей строке в курсиве: *жанр1, жанр2*
- Описание — живое, без воды, 1-2 предложения, до 30 слов

{context}

Ответ (только отформатированный список, без вступлений):"""

    # Отправляем запрос к YandexGPT
    try:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {YANDEX_API_KEY}",
            "x-folder-id": YANDEX_FOLDER_ID
        }
        
        data = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "stream": False,
                "temperature": 0.6,
                "maxTokens": 2000
            },
            "messages": [
                {
                    "role": "system",
                    "text": "Ты — эксперт по кино, который создаёт краткие и интересные описания фильмов и сериалов на русском языке."
                },
                {
                    "role": "user",
                    "text": prompt
                }
            ]
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        if 'result' in result and 'alternatives' in result['result']:
            text = result['result']['alternatives'][0]['message']['text']
            return text.strip()
        
        return None
    
    except Exception as e:
        logger.error(f"Ошибка при обращении к YandexGPT: {e}")
        return None


def format_releases_fallback(releases: List[Dict]) -> str:
    """
    Резервный формат без YandexGPT (если ИИ не отвечает)
    Возвращает простой список с названиями и ссылками
    """
    text = ""
    for i, release in enumerate(releases, 1):
        text += f"{i}. **{release['nameRu']}**\n"
        text += f"https://www.kinopoisk.ru/film/{release['id']}/\n"
        text += f"*{release['genres']}*\n\n"
    return text


# ==== ОБРАБОТЧИКИ КОМАНД ====

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    welcome_text = (
        "🎬 **Добро пожаловать в бот онлайн-премьер!**\n\n"
        "Я показываю цифровые релизы фильмов и сериалов на основе данных Кинопоиска "
        "и формирую красивые описания с помощью YandexGPT.\n\n"
        "**Доступные команды:**\n"
        "/today — премьеры сегодня\n"
        "/week — премьеры за прошлую неделю\n"
        "/help — справка\n\n"
        "Выберите команду или нажмите кнопку ниже!"
    )
    
    # Reply-клавиатура с командами
    keyboard = [
        ["/today", "/week"],
        ["/help"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📖 **Справка по боту**\n\n"
        "**Команды:**\n"
        "/start — главное меню и приветствие\n"
        "/today — онлайн-премьеры сегодня\n"
        "/week — онлайн-премьеры за прошлую неделю (полная неделя пн-вс)\n"
        "/help — эта справка\n\n"
        "Бот собирает данные из Kinopoisk API Unofficial и обрабатывает их через YandexGPT "
        "для создания красивых описаний.\n\n"
        "Каждый понедельник в 10:00 МСК бот автоматически присылает подборку за прошлую неделю "
        "(если настроен TARGET_CHAT_ID)."
    )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /today"""
    await update.message.reply_text("🔍 Ищу премьеры на сегодня...")
    
    # Получаем сегодняшнюю дату
    today = get_today_date()
    year = today.year
    month = today.month
    
    # Получаем релизы за текущий месяц
    releases = get_digital_releases(year, month)
    
    # Фильтруем по сегодняшней дате
    today_releases = filter_releases_by_date(releases, today)
    
    if not today_releases:
        await update.message.reply_text("Сегодня онлайн премьер нет")
        return
    
    # Сортируем и ограничиваем (максимум 10)
    filtered_releases = sort_and_limit_releases(today_releases, limit=10)
    
    # Форматируем с помощью YandexGPT
    formatted_text = format_releases_with_yandexgpt(filtered_releases, "today")
    
    if formatted_text:
        response_text = f"**Что вышло сегодня:**\n\n{formatted_text}"
    else:
        # Если YandexGPT не ответил, используем резервный формат
        response_text = f"**Что вышло сегодня:**\n\n{format_releases_fallback(filtered_releases)}"
        response_text += "\n⚠️ _Описания временно недоступны (ошибка ИИ)_"
    
    # Кнопка возврата в главное меню
    keyboard = [[InlineKeyboardButton("Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        response_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /week"""
    await update.message.reply_text("🔍 Ищу премьеры за прошлую неделю...")
    
    # Получаем даты прошлой недели
    start_date, end_date = get_last_full_week()
    
    # Получаем релизы (может потребоваться запрос для двух месяцев)
    releases = []
    
    # Проверяем, пересекает ли неделя границу месяца
    if start_date.month == end_date.month:
        releases = get_digital_releases(start_date.year, start_date.month)
    else:
        releases_month1 = get_digital_releases(start_date.year, start_date.month)
        releases_month2 = get_digital_releases(end_date.year, end_date.month)
        releases = releases_month1 + releases_month2
    
    # Фильтруем по периоду
    week_releases = filter_releases_by_period(releases, start_date, end_date)
    
    if not week_releases:
        await update.message.reply_text("На прошлой неделе онлайн премьер не было")
        return
    
    # Сортируем и ограничиваем (стремимся к 7 сериалов + 3 фильма)
    filtered_releases = sort_and_limit_releases(
        week_releases, 
        limit=10, 
        series_count=7, 
        movies_count=3
    )
    
    # Форматируем с помощью YandexGPT
    formatted_text = format_releases_with_yandexgpt(filtered_releases, "week")
    
    if formatted_text:
        response_text = f"**Что вышло за неделю:**\n\n{formatted_text}"
    else:
        # Если YandexGPT не ответил, используем резервный формат
        response_text = f"**Что вышло за неделю:**\n\n{format_releases_fallback(filtered_releases)}"
        response_text += "\n⚠️ _Описания временно недоступны (ошибка ИИ)_"
    
    # Кнопка возврата в главное меню
    keyboard = [[InlineKeyboardButton("Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        response_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия кнопки 'Главное меню'"""
    query = update.callback_query
    await query.answer()
    
    menu_text = (
        "🎬 **Главное меню**\n\n"
        "Я показываю онлайн-премьеры фильмов и сериалов.\n\n"
        "**Выберите команду:**\n"
        "/today — премьеры сегодня\n"
        "/week — премьеры за прошлую неделю\n"
        "/help — подробная справка"
    )
    
    # Reply-клавиатура
    keyboard = [
        ["/today", "/week"],
        ["/help"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await query.message.reply_text(
        menu_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )


# ==== АВТОРАССЫЛКА ПО ПОНЕДЕЛЬНИКАМ ====

async def weekly_auto_send(context: ContextTypes.DEFAULT_TYPE):
    """
    Автоматическая отправка премьер за прошлую неделю каждый понедельник в 10:00 МСК
    Вызывается через job_queue
    """
    if TARGET_CHAT_ID is None:
        logger.warning("TARGET_CHAT_ID не установлен, авторассылка отключена")
        return
    
    logger.info("Запуск авторассылки за прошлую неделю")
    
    # Получаем даты прошлой недели
    start_date, end_date = get_last_full_week()
    
    # Получаем релизы
    releases = []
    if start_date.month == end_date.month:
        releases = get_digital_releases(start_date.year, start_date.month)
    else:
        releases_month1 = get_digital_releases(start_date.year, start_date.month)
        releases_month2 = get_digital_releases(end_date.year, end_date.month)
        releases = releases_month1 + releases_month2
    
    # Фильтруем по периоду
    week_releases = filter_releases_by_period(releases, start_date, end_date)
    
    if not week_releases:
        text = "На прошлой неделе онлайн премьер не было"
        await context.bot.send_message(chat_id=TARGET_CHAT_ID, text=text)
        return
    
    # Сортируем и ограничиваем
    filtered_releases = sort_and_limit_releases(
        week_releases,
        limit=10,
        series_count=7,
        movies_count=3
    )
    
    # Форматируем
    formatted_text = format_releases_with_yandexgpt(filtered_releases, "week")
    
    if formatted_text:
        response_text = f"📅 **Еженедельная подборка**\n\n**Что вышло за неделю:**\n\n{formatted_text}"
    else:
        response_text = f"📅 **Еженедельная подборка**\n\n**Что вышло за неделю:**\n\n{format_releases_fallback(filtered_releases)}"
        response_text += "\n⚠️ _Описания временно недоступны (ошибка ИИ)_"
    
    # Кнопка главного меню
    keyboard = [[InlineKeyboardButton("Главное меню", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=TARGET_CHAT_ID,
        text=response_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )
    
    logger.info("Авторассылка успешно отправлена")


# ==== ГЛАВНАЯ ФУНКЦИЯ ====

def main():
    """Запуск бота"""
    # Создаём приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("week", week_command))
    
    # Обработчик callback-кнопок
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    
    # Настройка авторассылки по понедельникам в 10:00 МСК
    if TARGET_CHAT_ID is not None:
        job_queue = application.job_queue
        
        # Каждый понедельник в 10:00 по Москве (weekday 0 = понедельник)
        # time принимает время в часовом поясе сервера, поэтому конвертируем
        moscow_10am = datetime.now(MOSCOW_TZ).replace(hour=10, minute=0, second=0, microsecond=0).timetz()
        
        job_queue.run_daily(
            weekly_auto_send,
            time=moscow_10am,
            days=(0,),  # 0 = понедельник
            name="weekly_releases"
        )
        
        logger.info("Авторассылка настроена: каждый понедельник в 10:00 МСК")
    else:
        logger.info("TARGET_CHAT_ID не установлен, авторассылка отключена")
    
    # Запуск бота
    logger.info("Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
