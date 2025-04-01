import os
import json
import logging
import aiohttp
import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ConversationHandler, CallbackQueryHandler, MessageHandler, 
    filters, ContextTypes, CommandHandler
)

logger = logging.getLogger(__name__)

# Состояния для AI помощника
AI_MENU, AI_SCRIPT_REVIEW_INPUT, AI_NEW_SCRIPT_INPUT, AI_EDITING_INPUT, AI_DESCRIPTION_INPUT = range(5)

# Промты для каждого режима AI помощника
PROMPTS = {
    "script_review": {
        "description": (
            "В этом режиме DeepSeek анализирует ваш готовый сценарий, выделяет его сильные и слабые стороны, "
            "предлагает улучшения и дает рекомендации по съемке."
        ),
        "prompt": "Анализируй сценарий. Выдели сильные и слабые стороны, предложи улучшения и рекомендации по съемке:"
    },
    "new_script": {
        "description": (
            "В этом режиме вы вводите тему, которая вас интересует, и DeepSeek генерирует сценарий, "
            "учитывая начальный хук, финал и давая рекомендации по съемке."
        ),
        "prompt": "Создай сценарий на тему, учитывая начальный хук и финал, и предложи рекомендации по съемке:"
    },
    "editing_assist": {
        "description": (
            "В этом режиме DeepSeek даст советы по монтажу и визуальным эффектам, а также по использованию софта "
            "для монтажа (например, CapCut и других программ)."
        ),
        "prompt": "Дай советы по монтажу и визуальным эффектам для создания короткого видео:"
    },
    "description_gen": {
        "description": (
            "Генератор описания создает креативное описание для вашего видео на основе предоставленного сценария или ключевых идей."
        ),
        "prompt": "Сгенерируй креативное описание для видео на основе данного текста:"
    }
}

# Конфигурация API DeepSeek
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.ai/v1/chat/completions")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

async def get_current_model(db_pool: asyncpg.Pool) -> str:
    """Получает активную модель из базы данных"""
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT model_name FROM model_settings WHERE is_active = TRUE LIMIT 1"
        )

async def set_active_model(db_pool: asyncpg.Pool, model_name: str) -> bool:
    """Устанавливает активную модель в базе данных"""
    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                "UPDATE model_settings SET is_active = (model_name = $1)",
                model_name
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка смены модели: {str(e)}")
            return False

async def call_deepseek_api(prompt: str, text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Улучшенная функция для запросов к DeepSeek API"""
    try:
        db_pool = context.bot_data["db_pool"]
        model = await get_current_model(db_pool)
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Accept": "application/json"
        }
        
        payload = {
            "messages": [{
                "role": "user",
                "content": f"{prompt}\n\n{text}"
            }],
            "model": model,
            "temperature": 0.7,
            "max_tokens": 2000
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                DEEPSEEK_API_URL,
                json=payload,
                headers=headers
            ) as response:
                response.raise_for_status()
                result = await response.json()
                
                if "choices" in result and len(result["choices"]) > 0:
                    return result["choices"][0]["message"]["content"].strip()
                return "Не удалось получить ответ от API"

    except Exception as e:
        logger.error(f"API Error: {str(e)}", exc_info=True)
        return f"Ошибка обработки запроса: {str(e)}"

def get_ai_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("Проверить сценарий", callback_data='ai_script_review')],
        [InlineKeyboardButton("Новый сценарий", callback_data='ai_new_script')],
        [InlineKeyboardButton("Визуальные эффекты и монтаж", callback_data='ai_editing')],
        [InlineKeyboardButton("Генератор описания", callback_data='ai_description')],
        [InlineKeyboardButton("Выход", callback_data='ai_exit')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Добро пожаловать в AI помощник! Выберите задачу:",
        reply_markup=get_ai_menu_keyboard()
    )
    return AI_MENU

async def ai_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    
    handlers = {
        'ai_script_review': ("script_review", AI_SCRIPT_REVIEW_INPUT),
        'ai_new_script': ("new_script", AI_NEW_SCRIPT_INPUT),
        'ai_editing': ("editing_assist", AI_EDITING_INPUT),
        'ai_description': ("description_gen", AI_DESCRIPTION_INPUT)
    }
    
    if choice in handlers:
        key, state = handlers[choice]
        text = PROMPTS[key]["description"] + "\n\n" + PROMPTS[key]["prompt"]
        await query.message.reply_text(text)
        return state
    elif choice == 'ai_exit':
        await query.message.reply_text("Выход из AI помощника. Возвращайтесь, когда понадобится помощь!")
        return ConversationHandler.END
    else:
        await query.message.reply_text("Неверный выбор, попробуйте снова.")
        return AI_MENU

async def process_script_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    script = update.message.text
    response_text = await call_deepseek_api(
        PROMPTS["script_review"]["prompt"], 
        script,
        context
    )
    await update.message.reply_text(response_text)
    await show_ai_menu(update)
    return AI_MENU

async def process_new_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text
    response_text = await call_deepseek_api(
        PROMPTS["new_script"]["prompt"], 
        topic,
        context
    )
    await update.message.reply_text(response_text)
    await show_ai_menu(update)
    return AI_MENU

async def process_editing_assist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = update.message.text
    response_text = await call_deepseek_api(
        PROMPTS["editing_assist"]["prompt"], 
        question,
        context
    )
    await update.message.reply_text(response_text)
    await show_ai_menu(update)
    return AI_MENU

async def process_description_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_text = update.message.text
    response_text = await call_deepseek_api(
        PROMPTS["description_gen"]["prompt"], 
        input_text,
        context
    )
    await update.message.reply_text(response_text)
    await show_ai_menu(update)
    return AI_MENU

async def show_ai_menu(update: Update):
    await update.message.reply_text(
        "Что вы хотите сделать дальше?",
        reply_markup=get_ai_menu_keyboard()
    )

async def ai_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("AI помощник завершен. Возвращайтесь, когда понадобится помощь!")
    return ConversationHandler.END

# Админские команды для управления моделями
async def set_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) not in os.getenv("ADMIN_IDS", "").split(","):
        await update.message.reply_text("❌ Доступно только администраторам!")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /set_model <название_модели>")
        return
    
    model_name = context.args[0]
    success = await set_active_model(context.bot_data["db_pool"], model_name)
    
    if success:
        await update.message.reply_text(f"✅ Модель успешно изменена на: {model_name}")
    else:
        await update.message.reply_text("❌ Ошибка смены модели. Проверьте логи.")

async def list_models_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) not in os.getenv("ADMIN_IDS", "").split(","):
        await update.message.reply_text("❌ Доступно только администраторам!")
        return
    
    async with context.bot_data["db_pool"].acquire() as conn:
        models = await conn.fetch(
            "SELECT model_name, is_active FROM model_settings"
        )
    
    response = ["Доступные модели:"]
    for model in models:
        status = "🟢 Активна" if model["is_active"] else "⚪️ Неактивна"
        response.append(f"- {model['model_name']} {status}")
    
    await update.message.reply_text("\n".join(response))

ai_assistant_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_ai, pattern='^ai_assistant$')],
    states={
        AI_MENU: [CallbackQueryHandler(ai_menu_selection)],
        AI_SCRIPT_REVIEW_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_script_review)],
        AI_NEW_SCRIPT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_new_script)],
        AI_EDITING_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_editing_assist)],
        AI_DESCRIPTION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_description_gen)]
    },
    fallbacks=[MessageHandler(filters.COMMAND, ai_fallback)]
)

def add_handlers(app):
    app.add_handler(ai_assistant_handler)
    app.add_handler(CommandHandler("set_model", set_model_command))
    app.add_handler(CommandHandler("models", list_models_command))
