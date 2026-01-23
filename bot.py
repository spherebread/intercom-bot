import logging
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

from config import Config
from database import Database, Access
from intercom_service import IntercomService

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
    user_id = update.effective_user.id
    
    # Получение доступа пользователя через сессию
    session = db.get_session()
    try:
        access = session.query(Access).filter(
            Access.activated_by == user_id,
            Access.is_active == True
        ).first()
        
        if not access:
            await update.message.reply_text("❌ У вас нет активного доступа.", reply_markup=get_user_menu())
            return
        
        if not access.is_usable():
            await update.message.reply_text("❌ Ваш доступ больше недействителен (истек срок или достигнут лимит использований).", reply_markup=get_user_menu())
            return
        
        # Использование доступа
        access.current_uses += 1
        session.commit()
    finally:
        session.close()
    
    # Открытие двери
    success, message = IntercomService.open_door()
    
    if success:
        await update.message.reply_text(f"✅ {message}", reply_markup=get_user_menu())
    else:
        await update.message.reply_text(f"❌ {message}", reply_markup=get_user_menu())

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status - просмотр статуса доступа"""
    user_id = update.effective_user.id
    
    access = db.get_user_access(user_id)
    
    if not access:
        await update.message.reply_text("❌ У вас нет активного доступа.", reply_markup=get_user_menu())
        return
    
    remaining = access.remaining_uses()
    expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
    
    status_text = (
        f"📊 Статус вашего доступа:\n\n"
        f"Оставшееся количество использований: {remaining}/{access.max_uses}\n"
        f"Срок действия: {expires_text}\n"
        f"Статус: {'✅ Активен' if access.is_usable() else '❌ Неактивен'}"
    )
    
    await update.message.reply_text(status_text, reply_markup=get_user_menu())

# Администраторские команды и диалоги

async def create_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало диалога создания доступа"""
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    
    if not is_admin(user_id):
        text = "❌ У вас нет прав администратора."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
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
        "Или нажмите кнопку для использования значения по умолчанию:"
    )
    
    keyboard = [
        [InlineKeyboardButton("1 (по умолчанию)", callback_data="create_uses_1")],
        [InlineKeyboardButton("5", callback_data="create_uses_5")],
        [InlineKeyboardButton("10", callback_data="create_uses_10")],
        [InlineKeyboardButton("Отмена", callback_data="create_cancel")]
    ]
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return MAX_USES

async def create_access_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка количества использований"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "create_cancel":
            await query.edit_message_text("❌ Создание доступа отменено.", reply_markup=get_admin_menu())
            return ConversationHandler.END
        
        if query.data.startswith("create_uses_"):
            uses = int(query.data.split("_")[2])
            context.user_data['max_uses'] = uses
            
            text = (
                f"✅ Количество использований: {uses}\n\n"
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
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return EXPIRES_DAYS
    else:
        # Текстовый ввод
        try:
            uses = int(update.message.text)
            if uses < 1:
                await update.message.reply_text("❌ Количество использований должно быть больше 0. Попробуйте снова:")
                return MAX_USES
            
            context.user_data['max_uses'] = uses
            
            text = (
                f"✅ Количество использований: {uses}\n\n"
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
            await update.message.reply_text("❌ Неверный формат. Введите число:")
            return MAX_USES

async def create_access_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка срока действия"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "create_cancel":
            await query.edit_message_text("❌ Создание доступа отменено.", reply_markup=get_admin_menu())
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
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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
            await query.edit_message_text("❌ Создание доступа отменено.", reply_markup=get_admin_menu())
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
    
    text = (
        "Шаг 4/4: Подтверждение\n\n"
        f"Количество использований: {context.user_data['max_uses']}\n"
        f"Срок действия: {expires_text}\n"
        f"Комментарий: {comment_text}\n\n"
        "Подтвердите создание доступа:"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Создать", callback_data="create_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="create_cancel")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return CONFIRM

async def create_access_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и создание доступа"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "create_cancel":
        await query.edit_message_text("❌ Создание доступа отменено.", reply_markup=get_admin_menu())
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
        
        response_text = (
            f"✅ Доступ создан!\n\n"
            f"ID: {access.id}\n"
            f"Количество использований: {context.user_data['max_uses']}\n"
            f"Срок действия: {expires_text}\n"
            f"Комментарий: {comment_text}\n\n"
            f"Ссылка для активации:\n`{link}`"
        )
        
        keyboard = [
            [InlineKeyboardButton("📋 Список доступов", callback_data="admin_list")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]
        ]
        
        await query.edit_message_text(response_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        
        # Очистка данных
        context.user_data.clear()
        return ConversationHandler.END

async def create_access_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена создания доступа"""
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Создание доступа отменено.", reply_markup=get_admin_menu())
    return ConversationHandler.END

async def list_accesses(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    """Обработчик команды /list_accesses - список всех доступов с пагинацией"""
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    
    if not is_admin(user_id):
        text = "❌ У вас нет прав администратора."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return
    
    accesses = db.get_all_accesses()
    
    if not accesses:
        text = "📋 Список доступов пуст."
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]]
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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
    
    # Формирование списка
    text_parts = [f"📋 Список доступов (стр. {page + 1}/{total_pages}):\n"]
    
    keyboard = []
    
    for access in page_accesses:
        activated_text = "Нет" if not access.activated_by else f"Да (ID: {access.activated_by})"
        expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
        status_emoji = "✅" if access.is_usable() else "❌"
        
        text_parts.append(
            f"\n{status_emoji} <b>ID: {access.id}</b>\n"
            f"Использований: {access.current_uses}/{access.max_uses}\n"
            f"Срок: {expires_text}\n"
            f"Активирован: {activated_text}\n"
            f"Комментарий: {access.comment or 'Нет'}"
        )
        
        if access.is_active:
            keyboard.append([InlineKeyboardButton(f"❌ Отозвать {access.id}", callback_data=f"revoke_{access.id}")])
    
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
        await update.callback_query.edit_message_text(reply_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
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
    
    user_id = query.from_user.id
    data = query.data
    
    # Главное меню администратора
    if data == "admin_menu":
        await query.edit_message_text(
            "👋 Добро пожаловать, администратор!\n\nВыберите действие:",
            reply_markup=get_admin_menu()
        )
        return
    
    # Главное меню пользователя
    if data == "user_menu":
        access = db.get_user_access(user_id)
        if access:
            await query.edit_message_text(
                "👋 Добро пожаловать!\n\nУ вас есть активный доступ. Выберите действие:",
                reply_markup=get_user_menu()
            )
        else:
            await query.edit_message_text(
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
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            return MAX_USES
        
        elif data == "admin_list":
            await list_accesses(update, context, page=0)
            return
        
        elif data.startswith("revoke_"):
            access_id = int(data.split("_")[1])
            if db.revoke_access(access_id):
                keyboard = [
                    [InlineKeyboardButton("📋 Обновить список", callback_data="admin_list")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_menu")]
                ]
                await query.edit_message_text(
                    f"✅ Доступ {access_id} успешно отозван.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.edit_message_text(f"❌ Доступ {access_id} не найден.", reply_markup=get_admin_menu())
            return
        
        elif data.startswith("list_page_"):
            page = int(data.split("_")[2])
            await list_accesses(update, context, page=page)
            return
    
    # Пользовательские действия
    if data == "user_open":
        # Открытие двери через кнопку
        session = db.get_session()
        try:
            access = session.query(Access).filter(
                Access.activated_by == user_id,
                Access.is_active == True
            ).first()
            
            if not access:
                await query.edit_message_text("❌ У вас нет активного доступа.", reply_markup=get_user_menu())
                return
            
            if not access.is_usable():
                await query.edit_message_text("❌ Ваш доступ больше недействителен (истек срок или достигнут лимит использований).", reply_markup=get_user_menu())
                return
            
            # Использование доступа
            access.current_uses += 1
            session.commit()
        finally:
            session.close()
        
        # Открытие двери
        success, message = IntercomService.open_door()
        
        if success:
            await query.edit_message_text(f"✅ {message}", reply_markup=get_user_menu())
        else:
            await query.edit_message_text(f"❌ {message}", reply_markup=get_user_menu())
        return
    
    elif data == "user_status":
        access = db.get_user_access(user_id)
        
        if not access:
            await query.edit_message_text("❌ У вас нет активного доступа.", reply_markup=get_user_menu())
            return
        
        remaining = access.remaining_uses()
        expires_text = "Бессрочно" if access.expires_at is None else access.expires_at.strftime("%d.%m.%Y %H:%M")
        
        status_text = (
            f"📊 Статус вашего доступа:\n\n"
            f"Оставшееся количество использований: {remaining}/{access.max_uses}\n"
            f"Срок действия: {expires_text}\n"
            f"Статус: {'✅ Активен' if access.is_usable() else '❌ Неактивен'}"
        )
        
        await query.edit_message_text(status_text, reply_markup=get_user_menu())
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
    application.add_handler(CommandHandler("open", open_door))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("list_accesses", lambda u, c: list_accesses(u, c, page=0)))
    application.add_handler(create_access_conv)
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Запуск бота
    logger.info("Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
