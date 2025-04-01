import os
import re
import asyncpg
import pandas as pd
from tempfile import NamedTemporaryFile
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
from telegram.ext import (
    CallbackQueryHandler, MessageHandler, CommandHandler,
    ConversationHandler, filters, ContextTypes
)

from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

(
    WAITING_VIDEO_LINKS,
    WAITING_SCORE,
    WAITING_COMMENT,
    WAITING_AUTHOR_COMMENT
) = range(4)

URL_REGEX = re.compile(
    r'^https?://(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.'
    r'[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&//=]*)$'
)

async def init_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_start')]
    ])
    await update.callback_query.message.reply_text("Выберите режим:", reply_markup=markup)

async def creative_session_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Отправить видео", callback_data='send_video_prompt')],
        [InlineKeyboardButton("⭐ Начать оценку", callback_data='start_review')],
        [InlineKeyboardButton("📥 Скачать таблицу", callback_data='download')],
        [InlineKeyboardButton("🧹 Очистить таблицу", callback_data='clear_table')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='back_to_start')]
    ])
    await update.callback_query.message.reply_text("Добро пожаловать в Креативную сессию!", reply_markup=markup)

creative_session_handler = CallbackQueryHandler(creative_session_menu, pattern='^creative_session$')

async def send_video_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    keyboard = [
        [InlineKeyboardButton("qeep", callback_data="video_cat_qeep")],
        [InlineKeyboardButton("Harley", callback_data="video_cat_Harley")],
        [InlineKeyboardButton("Алтея", callback_data="video_cat_Алтея")]
    ]
    await update.callback_query.message.reply_text("Выберите категорию для отправки видео:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def select_video_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    category = update.callback_query.data.split("_")[-1]
    context.user_data["category"] = category
    await update.callback_query.message.reply_text("Отправьте ссылки через пробел:")
    return WAITING_VIDEO_LINKS

async def receive_video_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("Ошибка: отправьте текст со ссылками!")
        return WAITING_VIDEO_LINKS

    tokens = update.message.text.split()
    valid_links = [token.strip() for token in tokens if URL_REGEX.match(token)]

    if not valid_links:
        await update.message.reply_text("Не найдено ни одной корректной ссылки!")
        return WAITING_VIDEO_LINKS

    db_pool = context.bot_data.get("db_pool")
    if not db_pool:
        await update.message.reply_text("Ошибка подключения к БД!")
        return ConversationHandler.END

    category = context.user_data.get("category")
    async with db_pool.acquire() as conn:
        for link in valid_links:
            await conn.execute(
                "INSERT INTO videos (link, category) VALUES ($1, $2) ON CONFLICT (link, category) DO NOTHING",
                link, category
            )

    if len(valid_links) == 1:
        context.user_data["uploaded_video"] = valid_links[0]
        keyboard = [
            [InlineKeyboardButton("Оставить комментарий", callback_data="author_comment")],
            [InlineKeyboardButton("Пропустить", callback_data="skip_author_comment")]
        ]
        await update.message.reply_text(
            "✅ Ссылка сохранена! Хотите оставить комментарий к вашему видео?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_AUTHOR_COMMENT
    else:
        await update.message.reply_text(f"✅ Сохранено ссылок: {len(valid_links)}!")
        await back_to_menu(update, context)
        return ConversationHandler.END

async def prompt_author_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Введите ваш комментарий к видео:")
    return WAITING_AUTHOR_COMMENT

async def skip_author_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Видео сохранено!")
    await back_to_menu(update, context)
    return ConversationHandler.END

async def receive_author_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    db_pool = context.bot_data.get("db_pool")
    if not db_pool:
        await update.message.reply_text("Ошибка подключения к БД!")
        return ConversationHandler.END

    video_link = context.user_data.get("uploaded_video")
    category = context.user_data.get("category")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE videos SET author_comment = $1 WHERE link = $2 AND category = $3",
            comment, video_link, category
        )

    await update.message.reply_text("✅ Комментарий автора сохранён!")
    await back_to_menu(update, context)
    return ConversationHandler.END

async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    keyboard = [
        [InlineKeyboardButton("qeep", callback_data="rating_cat_qeep")],
        [InlineKeyboardButton("Harley", callback_data="rating_cat_Harley")],
        [InlineKeyboardButton("Алтея", callback_data="video_cat_Алтея")]
    ]
    await update.callback_query.message.reply_text("Выберите категорию для оценки видео:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def select_rating_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    category = update.callback_query.data.split("_")[-1]
    context.user_data["category"] = category
    return await ask_for_rating(update, context)

async def ask_for_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_pool = context.bot_data.get("db_pool")
    user_id = update.effective_user.id
    category = context.user_data.get("category")
    async with db_pool.acquire() as conn:
        video = await conn.fetchrow(
            """
            SELECT link FROM videos
            WHERE category = $2
              AND link NOT IN (
                  SELECT video_link FROM user_ratings
                  WHERE user_id = $1 AND category = $2
              )
            ORDER BY random() LIMIT 1
            """,
            user_id, category
        )

    if not video:
        await update.callback_query.message.reply_text("Вы оценили все видео в этой категории!")
        await back_to_menu(update, context)
        return ConversationHandler.END

    context.user_data["current_video"] = video["link"]
    await update.callback_query.message.reply_text(f"Оцените видео от 1 до 10:\n{video['link']}")
    return WAITING_SCORE

async def receive_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rating = int(update.message.text)
        if not 1 <= rating <= 10:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Введите число от 1 до 10.")
        return WAITING_SCORE

    db_pool = context.bot_data.get("db_pool")
    if not db_pool:
        await update.message.reply_text("Ошибка подключения к БД!")
        return ConversationHandler.END

    video_link = context.user_data.get("current_video")
    category = context.user_data.get("category")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE videos
            SET total_score = total_score + $1,
                ratings_count = ratings_count + 1,
                avg_score = (total_score + $1)::FLOAT / (ratings_count + 1)
            WHERE link = $2 AND category = $3
            """,
            rating, video_link, category
        )

    await update.message.reply_text("Оценка сохранена. Теперь оставьте комментарий:")
    return WAITING_COMMENT

async def receive_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    db_pool = context.bot_data.get("db_pool")
    if not db_pool:
        await update.message.reply_text("Ошибка подключения к БД!")
        return ConversationHandler.END

    video_link = context.user_data.get("current_video")
    category = context.user_data.get("category")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE videos SET comments = array_append(comments, $1) WHERE link = $2 AND category = $3",
            comment, video_link, category
        )
        await conn.execute(
            "INSERT INTO user_ratings (user_id, video_link, category) VALUES ($1, $2, $3)",
            update.effective_user.id, video_link, category
        )

    await update.message.reply_text("✅ Комментарий сохранён!")
    return await ask_for_rating(update, context)

async def help_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "ℹ️ Помощь:\n\n"
        "🎥 Отправка видео — загрузка одного или нескольких видео в систему.\n"
        "⭐ Оценка видео — проставление оценки и комментария другим участникам.\n"
        "📥 Выгрузка — скачать таблицу по каждой категории.\n"
        "🧹 Очистка — очистка базы (только для админов)."
    )

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Укажите ID пользователя: /add_admin <id>")
        return

    try:
        new_id = int(context.args[0])
        if new_id not in ADMIN_IDS:
            ADMIN_IDS.append(new_id)
            await update.message.reply_text(f"✅ Админ добавлен: {new_id}")
        else:
            await update.message.reply_text("Этот ID уже админ.")
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    print("❌ Ошибка:", traceback.format_exc())

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    keyboard = [
        [InlineKeyboardButton("qeep", callback_data="download_qeep")],
        [InlineKeyboardButton("Harley", callback_data="download_Harley")],
        [InlineKeyboardButton("Алтея", callback_data="download_Алтея")]
    ]
    await update.callback_query.message.reply_text(
        "Выберите категорию для скачивания таблицы:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def download_by_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    category = update.callback_query.data.split("_")[1]  # Извлекаем категорию из callback_data
    db_pool = context.bot_data.get("db_pool")  # Получаем пул соединений с БД
    if not db_pool:
        await update.callback_query.message.reply_text("Ошибка подключения к БД!")
        return

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT link, avg_score, ratings_count, comments FROM videos WHERE category = $1",
            category
        )

    if not rows:
        await update.callback_query.message.reply_text("Нет данных для этой категории.")
        return

    df = pd.DataFrame(rows, columns=["Ссылка", "Средняя оценка", "Количество оценок", "Комментарии"])
    with NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        df.to_csv(tmp.name, index=False)
        tmp_path = tmp.name

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=InputFile(tmp_path, filename=f"{category}_videos.csv")
    )
    os.remove(tmp_path)  # Удаляем временный файл после отправки