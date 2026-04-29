import logging
from io import BytesIO
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler
)
from telegram.constants import ParseMode

import requests
from config import Config
from database import Database, Access
from intercom_service import IntercomService
from telegram.error import BadRequest
from sqlalchemy.exc import OperationalError

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)
logger = logging.getLogger(__name__)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок без уведомления пользователя"""
    logger.exception("Unhandled exception while processing update", exc_info=context.error)

    if isinstance(context.error, OperationalError):
        logger.warning("Database connectivity issue detected (OperationalError)")

    if update and hasattr(update, "callback_query") and update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            logger.exception("Failed to answer callback query in error handler")

async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """Безопасное редактирование сообщения с обработкой ошибки 'Message is not modified'"""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            # Сообщение не изменилось - это нормально, просто игнорируем
            logger.debug("Message not modified, ignoring")
        else:
            # Другая ошибка - пробрасываем дальше
            raise

# Инициализация базы данных
db = Database()

# Состояния для ConversationHandler
(MAX_USES, EXPIRES_DAYS, COMMENT, CONFIRM) = range(4)

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    return user_id in Config.ADMIN_IDS

def get_admin_menu():
    """Главное меню администратора"""
    keyboard = [
        [InlineKeyboardButton("➕ Создать доступ", callback_data="admin_create")],
        [InlineKeyboardButton("📋 Список доступов", callback_data="admin_list")],
        [InlineKeyboardButton("🚪 Открыть дверь", callback_data="admin_open")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_user_menu():
    """Меню пользователя"""
    keyboard = [
        [InlineKeyboardButton("🚪 Открыть дверь", callback_data="user_open")],
        [InlineKeyboardButton("📊 Статус доступа", callback_data="user_status")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="user_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_door_open_caption(user) -> str:
    """Подпись для уведомления администратора об открытии двери"""
    if user.username:
        actor = f"@{user.username}"
    else:
        full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
        actor = full_name or f"пользователь {user.id}"
    opened_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return f"{actor} открыл дверь в {opened_at}"


async def notify_admins_about_open_door(context: ContextTypes.DEFAULT_TYPE, user):
    """Отправка администраторам фото и подписи после открытия двери"""
    if not Config.ADMIN_IDS:
        return

    caption = get_door_open_caption(user)

    if not Config.PREVIEW_URL:
        logger.warning("PREVIEW_URL is not set, sending text-only admin notification")
        for admin_id in Config.ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=caption)
            except Exception:
                logger.exception("Failed to send text notification to admin %s", admin_id)
        return

    try:
        response = requests.get(Config.PREVIEW_URL, timeout=10)
        response.raise_for_status()

        image_bytes = BytesIO(response.content)
        image_bytes.name = "preview.jpg"

        for admin_id in Config.ADMIN_IDS:
            try:
                image_bytes.seek(0)
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=image_bytes,
                    caption=caption
                )
            except Exception:
                logger.exception("Failed to send photo notification to admin %s", admin_id)
    except requests.exceptions.RequestException:
        logger.exception("Failed to fetch preview image from PREVIEW_URL")
        for admin_id in Config.ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=caption)
            except Exception:
                logger.exception("Failed to send fallback text notification to admin %s", admin_id)


async def open_door_and_notify(context: ContextTypes.DEFAULT_TYPE, user):
    """Открытие двери и уведомление администраторов при успехе"""
    success, message, message_type = IntercomService.open_door()
    if success:
        await notify_admins_about_open_door(context, user)
    return success, message, message_type


def get_open_door_message(success: bool, message: str, message_type: str | None) -> str:
    """Форматирование результата открытия двери для пользователя"""
    if success:
        return f"✅ {message}"

    if message_type == "offline_code":
        return f"Домофон недоступен. Попробуйте оффлайн-код: {message}"

    return f"❌ {message}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = user.id
    
    # Получение или создание пользователя
    db.get_or_create_user(
        telegram_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Проверка наличия токена в параметрах (для активации доступа)
    if context.args and len(context.args) > 0:
        token = context.args[0]
        access = db.get_access_by_token(token)
        
        if not access:
            await update.message.reply_text("❌ Неверная ссылка доступа.")
            return
        
        if access.activated_by and access.activated_by != user_id:
            await update.message.reply_text("❌ Эта ссылка уже была использована другим пользователем.")
            return
        
        if not access.is_usable():
            await update.message.reply_text("❌ Этот доступ больше недействителен (истек срок или достигнут лимит использований).")
            return
        
        # Активация доступа (если еще не активирован)
        if not access.activated_by:
            db.activate_access(token, user_id)
            await update.message.reply_text(
                "✅ Доступ активирован!\n\n"
                "Используйте команду /open для открытия двери.\n"
                "Используйте команду /status для просмотра статуса доступа."
            )
        else:
            # Доступ уже активирован этим пользователем
            await update.message.reply_text(
                "✅ У вас уже есть активный доступ!\n\n"
                "Используйте команду /open для открытия двери.\n"
                "Используйте команду /status для просмотра статуса доступа."
            )
        return
    
    # Обычный старт
    if is_admin(user_id):
        await update.message.reply_text(
            "👋 Добро пожаловать, администратор!\n\n"
            "Выберите действие:",
            reply_markup=get_admin_menu()
        )
    else:
        access = db.get_user_access(user_id)
        if access:
            await update.message.reply_text(
                "👋 Добро пожаловать!\n\n"
                "У вас есть активный доступ. Выберите действие:",
                reply_markup=get_user_menu()
            )
        else:
            await update.message.reply_text(
                "👋 Добро пожаловать!\n\n"
                "У вас нет активного доступа. Обратитесь к администратору для получения доступа."
            )

async def open_door(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /open - открытие двери"""
    user = update.effective_user
    user_id = update.effective_user.id

    # Администратор может всегда открыть дверь, минуя систему доступов
    if is_admin(user_id):
        success, message, message_type = await open_door_and_notify(context, user)
        await update.message.reply_text(get_open_door_message(success, message, message_type))
        return
    
    # Получаем актуальный доступ с учётом срока и количества использований
    access = db.get_user_access(user_id)
    if not access:
        await update.message.reply_text("❌ У вас нет активного доступа.", reply_markup=get_user_menu())
        return
    
    if not access.is_usable():
        await update.message.reply_text("❌ Ваш доступ больше недействителен (истек срок или достигнут лимит использований).", reply_markup=get_user_menu())
        return
    
    # Использование доступа (увеличиваем счётчик в отдельной сессии)
    session = db.get_session()
    try:
        db_access = session.query(Access).filter(Access.id == access.id).first()
        if not db_access or not db_access.is_usable():
            await update.message.reply_text("❌ Ваш доступ больше недействителен (истек срок или достигнут лимит использований).", reply_markup=get_user_menu())
            return
        db_access.current_uses += 1
        session.commit()
    finally:
        session.close()
    
    # Открытие двери
    success, message, message_type = await open_door_and_notify(context, user)
    await update.message.reply_text(get_open_door_message(success, message, message_type), reply_markup=get_user_menu())

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status - просмотр статуса доступа"""
    user_id = update.effective_user.id

    if is_admin(user_id):
        await update.message.reply_text(
            "👋 Меню администратора:\n\nВыберите действие:",
            reply_markup=get_admin_menu()
        )
        return
    
    access = db.get_user_access(user_id)
    
    if not access:
        await update.message.reply_text("❌ У вас нет активного доступа.", reply_markup=get_user_menu())
        return
    
    remaining = access.remaining_uses()
    expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
    
    if access.is_unlimited():
        uses_text = f"Использовано: {access.current_uses} / ∞ (бесконечно)"
    else:
        uses_text = f"Оставшееся количество использований: {remaining}/{access.max_uses}"
    
    status_text = (
        f"📊 Статус вашего доступа:\n\n"
        f"{uses_text}\n"
        f"Срок действия: {expires_text}\n"
        f"Статус: {'✅ Активен' if access.is_usable() else '❌ Неактивен'}"
    )
    
    await update.message.reply_text(status_text, reply_markup=get_user_menu())


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /menu - показать актуальное меню"""
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        await update.message.reply_text(
            "👋 Меню администратора:\n\nВыберите действие:",
            reply_markup=get_admin_menu()
        )
    else:
        access = db.get_user_access(user_id)
        if access:
            await update.message.reply_text(
                "👋 Меню пользователя:\n\nУ вас есть активный доступ. Выберите действие:",
                reply_markup=get_user_menu()
            )
        else:
            await update.message.reply_text(
                "👋 Меню пользователя:\n\nУ вас нет активного доступа. Обратитесь к администратору для получения доступа."
            )

# Администраторские команды и диалоги

async def create_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога создания доступа"""
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    
    if not is_admin(user_id):
        text = "❌ У вас нет прав администратора."
        if update.callback_query:
            await update.callback_query.answer()
            await safe_edit_message(update.callback_query, text)
        else:
            await update.message.reply_text(text)
        return ConversationHandler.END
    
    # Инициализация данных
    context.user_data['max_uses'] = 1
    context.user_data['expires_at'] = None
    context.user_data['comment'] = None
    
    text = (
        "➕ Создание нового доступа\n\n"
        "Шаг 1/4: Количество использований\n\n"
        "Введите количество использований (по умолчанию: 1)\n"
        "Или выберите из предложенных вариантов (включая бесконечное количество):"
    )
    
    keyboard = [
        [InlineKeyboardButton("1 (по умолчанию)", callback_data="create_uses_1")],
        [InlineKeyboardButton("5", callback_data="create_uses_5")],
        [InlineKeyboardButton("10", callback_data="create_uses_10")],
        [InlineKeyboardButton("∞ Бесконечно", callback_data="create_uses_unlimited")],
        [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
    ]
    
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message(update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return MAX_USES

async def create_access_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка количества использований"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "create_cancel":
            await safe_edit_message(query, "❌ Создание доступа отменено.", reply_markup=get_admin_menu())
            return ConversationHandler.END
        
        if query.data.startswith("create_uses_"):
            uses_str = query.data.split("_")[2]
            if uses_str == "unlimited":
                uses = -1
                uses_text = "Бесконечно"
            else:
                uses = int(uses_str)
                uses_text = str(uses)
            context.user_data['max_uses'] = uses
            
            text = (
                f"✅ Количество использований: {uses_text}\n\n"
                "Шаг 2/4: Срок действия\n\n"
                "Введите количество дней действия (или 0 для бессрочного доступа)\n"
                "Или выберите из предложенных вариантов:"
            )
            
            keyboard = [
                [InlineKeyboardButton("Бессрочно (0 дней)", callback_data="create_days_0")],
                [InlineKeyboardButton("1 день", callback_data="create_days_1")],
                [InlineKeyboardButton("7 дней", callback_data="create_days_7")],
                [InlineKeyboardButton("30 дней", callback_data="create_days_30")],
                [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
            ]
            
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
            return EXPIRES_DAYS
    else:
        # Текстовый ввод
        try:
            text_input = update.message.text.strip().lower()
            if text_input in ["бесконечно", "unlimited", "∞", "-1"]:
                uses = -1
                uses_text = "Бесконечно"
            else:
                uses = int(text_input)
                if uses < 1:
                    await update.message.reply_text("❌ Количество использований должно быть больше 0 (или введите 'бесконечно' для неограниченного доступа). Попробуйте снова:")
                    return MAX_USES
                uses_text = str(uses)
            
            context.user_data['max_uses'] = uses
            
            text = (
                f"✅ Количество использований: {uses_text}\n\n"
                "Шаг 2/4: Срок действия\n\n"
                "Введите количество дней действия (или 0 для бессрочного доступа)\n"
                "Или выберите из предложенных вариантов:"
            )
            
            keyboard = [
                [InlineKeyboardButton("Бессрочно (0 дней)", callback_data="create_days_0")],
                [InlineKeyboardButton("1 день", callback_data="create_days_1")],
                [InlineKeyboardButton("7 дней", callback_data="create_days_7")],
                [InlineKeyboardButton("30 дней", callback_data="create_days_30")],
                [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
            ]
            
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return EXPIRES_DAYS
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Введите число или 'бесконечно' для неограниченного доступа:")
            return MAX_USES

async def create_access_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка срока действия"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "create_cancel":
            await safe_edit_message(query, "❌ Создание доступа отменено.", reply_markup=get_admin_menu())
            return ConversationHandler.END
        
        if query.data.startswith("create_days_"):
            days = int(query.data.split("_")[2])
            if days == 0:
                context.user_data['expires_at'] = None
                expires_text = "Бессрочно"
            else:
                context.user_data['expires_at'] = datetime.utcnow() + timedelta(days=days)
                expires_text = f"{days} дней"
            
            text = (
                f"✅ Срок действия: {expires_text}\n\n"
                "Шаг 3/4: Комментарий\n\n"
                "Введите комментарий к доступу (или отправьте '-' для пропуска):"
            )
            
            keyboard = [
                [InlineKeyboardButton("Пропустить комментарий", callback_data="create_comment_skip")],
                [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
            ]
            
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
            return COMMENT
    else:
        # Текстовый ввод
        try:
            days = int(update.message.text)
            if days < 0:
                await update.message.reply_text("❌ Количество дней не может быть отрицательным. Попробуйте снова:")
                return EXPIRES_DAYS
            
            if days == 0:
                context.user_data['expires_at'] = None
                expires_text = "Бессрочно"
            else:
                context.user_data['expires_at'] = datetime.utcnow() + timedelta(days=days)
                expires_text = f"{days} дней"
            
            text = (
                f"✅ Срок действия: {expires_text}\n\n"
                "Шаг 3/4: Комментарий\n\n"
                "Введите комментарий к доступу (или отправьте '-' для пропуска):"
            )
            
            keyboard = [
                [InlineKeyboardButton("Пропустить комментарий", callback_data="create_comment_skip")],
                [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
            ]
            
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return COMMENT
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Введите число (или 0 для бессрочного доступа):")
            return EXPIRES_DAYS

async def create_access_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка комментария"""
    comment_text = "Нет"
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "create_cancel":
            await safe_edit_message(query, "❌ Создание доступа отменено.", reply_markup=get_admin_menu())
            return ConversationHandler.END
        
        if query.data == "create_comment_skip":
            context.user_data['comment'] = None
            comment_text = "Нет"
        else:
            # Неизвестный callback, остаемся в состоянии COMMENT
            return COMMENT
    else:
        comment = update.message.text.strip()
        if comment == "-" or comment.lower() == "пропустить":
            context.user_data['comment'] = None
            comment_text = "Нет"
        else:
            context.user_data['comment'] = comment
            comment_text = comment
    
    # Подтверждение
    expires_text = "Бессрочно" if context.user_data['expires_at'] is None else context.user_data['expires_at'].strftime("%d.%m.%Y %H:%M")
    uses_text = "Бесконечно" if context.user_data['max_uses'] == -1 else str(context.user_data['max_uses'])
    
    text = (
        "Шаг 4/4: Подтверждение\n\n"
        f"Количество использований: {uses_text}\n"
        f"Срок действия: {expires_text}\n"
        f"Комментарий: {comment_text}\n\n"
        "Подтвердите создание доступа:"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Создать", callback_data="create_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="create_cancel")]
    ]
    
    if update.callback_query:
        await safe_edit_message(update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return CONFIRM

async def create_access_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и создание доступа"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "create_cancel":
        await safe_edit_message(query, "❌ Создание доступа отменено.", reply_markup=get_admin_menu())
        return ConversationHandler.END
    
    if query.data == "create_confirm":
        user_id = query.from_user.id
        
        # Создание доступа
        access = db.create_access(
            max_uses=context.user_data['max_uses'],
            expires_at=context.user_data['expires_at'],
            comment=context.user_data['comment'],
            created_by=user_id
        )
        
        # Генерация ссылки
        link = f"{Config.BOT_BASE_URL}?start={access.token}"
        
        expires_text = "Бессрочно" if context.user_data['expires_at'] is None else context.user_data['expires_at'].strftime("%d.%m.%Y %H:%M")
        comment_text = context.user_data['comment'] or "Нет"
        uses_text = "Бесконечно" if context.user_data['max_uses'] == -1 else str(context.user_data['max_uses'])
        
        response_text = (
            f"✅ Доступ создан!\n\n"
            f"ID: {access.id}\n"
            f"Количество использований: {uses_text}\n"
            f"Срок действия: {expires_text}\n"
            f"Комментарий: {comment_text}\n\n"
            f"Ссылка для активации:\n`{link}`"
        )
        
        keyboard = [
            [InlineKeyboardButton("📋 Список доступов", callback_data="admin_list")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]
        ]
        
        await safe_edit_message(query, response_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
        # Очистка данных
        context.user_data.clear()
        return ConversationHandler.END

async def create_access_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена создания доступа"""
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message(update.callback_query, "❌ Создание доступа отменено.", reply_markup=get_admin_menu())
    return ConversationHandler.END

async def list_accesses(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    """Обработчик команды /list_accesses - список всех доступов с пагинацией"""
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    
    if not is_admin(user_id):
        text = "❌ У вас нет прав администратора."
        if update.callback_query:
            await update.callback_query.answer()
            await safe_edit_message(update.callback_query, text)
        else:
            await update.message.reply_text(text)
        return
    
    accesses = db.get_all_accesses()
    
    if not accesses:
        text = "📋 Список доступов пуст."
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]]
        if update.callback_query:
            await update.callback_query.answer()
            await safe_edit_message(update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # Пагинация (по 5 доступов на страницу)
    items_per_page = 5
    total_pages = (len(accesses) + items_per_page - 1) // items_per_page
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(accesses))
    page_accesses = accesses[start_idx:end_idx]
    
    # Разделяем доступы на активные (ещё можно использовать) и неактивные
    active_page = [a for a in page_accesses if a.is_usable()]
    inactive_page = [a for a in page_accesses if not a.is_usable()]
    
    # Формирование списка
    text_parts = [f"📋 Список доступов (стр. {page + 1}/{total_pages}):\n"]
    
    keyboard = []
    
    # --- Активные доступы ---
    if active_page:
        text_parts.append("\n<b>Активные доступы:</b>")
    
        for access in active_page:
            # Получение информации о пользователе, который активировал доступ
            if access.activated_by:
                user = db.get_user_by_id(access.activated_by)
                if user:
                    if user.username:
                        activated_text = f"Да (@{user.username})"
                    elif user.first_name:
                        activated_text = f"Да ({user.first_name})"
                    else:
                        activated_text = f"Да (ID: {access.activated_by})"
                else:
                    activated_text = f"Да (ID: {access.activated_by})"
            else:
                activated_text = "Нет"
            
            expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
            status_emoji = "✅" if access.is_usable() else "❌"
            
            if access.is_unlimited():
                uses_text = f"Использований: {access.current_uses}/∞ (бесконечно)"
            else:
                uses_text = f"Использований: {access.current_uses}/{access.max_uses}"
            
            text_parts.append(
                f"\n{status_emoji} <b>ID: {access.id}</b>\n"
                f"{uses_text}\n"
                f"Срок: {expires_text}\n"
                f"Активирован: {activated_text}\n"
                f"Комментарий: {access.comment or 'Нет'}"
            )
            
            # Кнопки действий для активных доступов
            if access.is_active and access.is_usable():
                keyboard.append([
                    InlineKeyboardButton(f"❌ Отозвать {access.id}", callback_data=f"revoke_{access.id}"),
                    InlineKeyboardButton(f"🔗 Получить ссылку", callback_data=f"link_{access.id}")
                ])
    
    # --- Неактивные доступы (просрочены, исчерпаны или отозваны) ---
    if inactive_page:
        text_parts.append("\n<b>Неактивные доступы:</b>")
    
        for access in inactive_page:
            if access.activated_by:
                user = db.get_user_by_id(access.activated_by)
                if user:
                    if user.username:
                        activated_text = f"Да (@{user.username})"
                    elif user.first_name:
                        activated_text = f"Да ({user.first_name})"
                    else:
                        activated_text = f"Да (ID: {access.activated_by})"
                else:
                    activated_text = f"Да (ID: {access.activated_by})"
            else:
                activated_text = "Нет"
            
            expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
            status_emoji = "❌"
            
            if access.is_unlimited():
                uses_text = f"Использований: {access.current_uses}/∞ (бесконечно)"
            else:
                uses_text = f"Использований: {access.current_uses}/{access.max_uses}"
            
            text_parts.append(
                f"\n{status_emoji} <b>ID: {access.id}</b>\n"
                f"{uses_text}\n"
                f"Срок: {expires_text}\n"
                f"Активирован: {activated_text}\n"
                f"Комментарий: {access.comment or 'Нет'}"
            )
    
    # Кнопки пагинации
    nav_buttons = []
    if total_pages > 1:
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"list_page_{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"list_page_{page + 1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")])
    
    reply_text = "\n".join(text_parts)
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message(update.callback_query, reply_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(reply_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /revoke_access - отзыв доступа"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return
    
    if not context.args or len(context.args) == 0:
        await update.message.reply_text("❌ Укажите ID доступа для отзыва.\nИспользование: /revoke_access <access_id>")
        return
    
    try:
        access_id = int(context.args[0])
        if db.revoke_access(access_id):
            await update.message.reply_text(f"✅ Доступ {access_id} успешно отозван.")
        else:
            await update.message.reply_text(f"❌ Доступ {access_id} не найден.")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат ID доступа.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = query.from_user.id
    data = query.data
    
    # Главное меню администратора
    if data == "admin_menu":
        await safe_edit_message(
            query,
            "👋 Добро пожаловать, администратор!\n\nВыберите действие:",
            reply_markup=get_admin_menu()
        )
        return
    
    # Главное меню пользователя
    if data == "user_menu":
        access = db.get_user_access(user_id)
        if access:
            await safe_edit_message(
                query,
                "👋 Добро пожаловать!\n\nУ вас есть активный доступ. Выберите действие:",
                reply_markup=get_user_menu()
            )
        else:
            await safe_edit_message(
                query,
                "👋 Добро пожаловать!\n\nУ вас нет активного доступа. Обратитесь к администратору для получения доступа."
            )
        return
    
    # Администраторские действия
    if is_admin(user_id):
        if data == "admin_create":
            # Начало создания доступа
            context.user_data['max_uses'] = 1
            context.user_data['expires_at'] = None
            context.user_data['comment'] = None
            
            text = (
                "➕ Создание нового доступа\n\n"
                "Шаг 1/4: Количество использований\n\n"
                "Введите количество использований (по умолчанию: 1)\n"
                "Или нажмите кнопку для использования значения по умолчанию:"
            )
            
            keyboard = [
                [InlineKeyboardButton("1 (по умолчанию)", callback_data="create_uses_1")],
                [InlineKeyboardButton("5", callback_data="create_uses_5")],
                [InlineKeyboardButton("10", callback_data="create_uses_10")],
                [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
            ]
            
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard))
            return MAX_USES
        
        elif data == "admin_list":
            await list_accesses(update, context, page=0)
            return
        
        elif data == "admin_open":
            # Открытие двери администратором (без проверки доступа)
            success, message, message_type = await open_door_and_notify(context, user)
            await safe_edit_message(
                query,
                get_open_door_message(success, message, message_type),
                reply_markup=get_admin_menu()
            )
            return
        
        elif data.startswith("revoke_"):
            access_id = int(data.split("_")[1])
            if db.revoke_access(access_id):
                keyboard = [
                    [InlineKeyboardButton("📋 Обновить список", callback_data="admin_list")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]
                ]
                await safe_edit_message(
                    query,
                    f"✅ Доступ {access_id} успешно отозван.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await safe_edit_message(query, f"❌ Доступ {access_id} не найден.", reply_markup=get_admin_menu())
            return
        
        elif data.startswith("link_"):
            # Получение ссылки для активации доступа
            access_id = int(data.split("_")[1])
            access = db.get_access_by_id(access_id)
            if not access:
                await safe_edit_message(query, f"❌ Доступ {access_id} не найден.", reply_markup=get_admin_menu())
                return
            
            link = f"{Config.BOT_BASE_URL}?start={access.token}"
            expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
            uses_text = "Бесконечно" if access.is_unlimited() else f"{access.current_uses}/{access.max_uses}"
            
            text = (
                f"🔗 Ссылка для доступа ID {access.id}:\n"
                f"`{link}`\n\n"
                f"Использований: {uses_text}\n"
                f"Срок действия: {expires_text}\n"
                f"Статус: {'✅ Активен' if access.is_usable() else '❌ Неактивен'}"
            )
            
            keyboard = [
                [InlineKeyboardButton("📋 Вернуться к списку", callback_data="admin_list")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]
            ]
            await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
            return
        
        elif data.startswith("list_page_"):
            page = int(data.split("_")[2])
            await list_accesses(update, context, page=page)
            return
    
    # Пользовательские действия
    if data == "user_open":
        # Открытие двери через кнопку
        access = db.get_user_access(user_id)
        if not access:
            await safe_edit_message(query, "❌ У вас нет активного доступа.", reply_markup=get_user_menu())
            return
        
        if not access.is_usable():
            await safe_edit_message(query, "❌ Ваш доступ больше недействителен (истек срок или достигнут лимит использований).", reply_markup=get_user_menu())
            return
        
        # Использование доступа (увеличиваем счётчик в отдельной сессии)
        session = db.get_session()
        try:
            db_access = session.query(Access).filter(Access.id == access.id).first()
            if not db_access or not db_access.is_usable():
                await safe_edit_message(query, "❌ Ваш доступ больше недействителен (истек срок или достигнут лимит использований).", reply_markup=get_user_menu())
                return
            db_access.current_uses += 1
            session.commit()
        finally:
            session.close()
        
        # Открытие двери
        success, message, message_type = await open_door_and_notify(context, user)
        await safe_edit_message(
            query,
            get_open_door_message(success, message, message_type),
            reply_markup=get_user_menu()
        )
        return
    
    elif data == "user_status":
        access = db.get_user_access(user_id)
        
        if not access:
            await safe_edit_message(query, "❌ У вас нет активного доступа.", reply_markup=get_user_menu())
            return
        
        remaining = access.remaining_uses()
        expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
        
        if access.is_unlimited():
            uses_text = f"Использовано: {access.current_uses} / ∞ (бесконечно)"
        else:
            uses_text = f"Оставшееся количество использований: {remaining}/{access.max_uses}"
        
        status_text = (
            f"📊 Статус вашего доступа:\n\n"
            f"{uses_text}\n"
            f"Срок действия: {expires_text}\n"
            f"Статус: {'✅ Активен' if access.is_usable() else '❌ Неактивен'}"
        )
        
        await safe_edit_message(query, status_text, reply_markup=get_user_menu())
        return

def main():
    """Главная функция запуска бота"""
    # Валидация конфигурации
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        return
    
    # Инициализация базы данных
    db.init_db()
    logger.info("База данных инициализирована")
    
    # Создание приложения
    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
    
    # ConversationHandler для создания доступа
    create_access_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(create_access_start, pattern="^admin_create$"),
            CommandHandler("create_access", create_access_start)
        ],
        states={
            MAX_USES: [
                CallbackQueryHandler(create_access_uses, pattern="^create_uses_|^create_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_access_uses)
            ],
            EXPIRES_DAYS: [
                CallbackQueryHandler(create_access_days, pattern="^create_days_|^create_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_access_days)
            ],
            COMMENT: [
                CallbackQueryHandler(create_access_comment, pattern="^create_comment_|^create_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_access_comment)
            ],
            CONFIRM: [
                CallbackQueryHandler(create_access_confirm, pattern="^create_confirm|^create_cancel$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(create_access_cancel, pattern="^create_cancel$"),
            CommandHandler("cancel", create_access_cancel)
        ]
    )
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("open", open_door))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("list_accesses", lambda u, c: list_accesses(u, c, page=0)))
    application.add_handler(create_access_conv)
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    # Запуск бота
    logger.info("Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
