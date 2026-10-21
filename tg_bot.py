import io
import logging
import sys
from functools import wraps

import redis
import requests
from environs import Env
from redis.exceptions import RedisError
from telegram import ChatAction, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    Filters,
    MessageHandler,
    Updater,
)

sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("fish-bot")

MESSAGES_TO_DELETE_TTL_SECONDS = 86400


# ==========================================
# 1. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (UTILS)
# ==========================================
def log_errors(func):
    """Декоратор: логирует ошибки, сообщает пользователю и сбрасывает состояние."""

    @wraps(func)
    def wrapper(update, context, *args, **kwargs):
        try:
            return func(update, context, *args, **kwargs)
        except (requests.RequestException, TelegramError, RedisError) as err:
            logger.error(f"Сетевая ошибка в {func.__name__}: {err}")
            chat_id = update.effective_chat.id
            try:
                context.bot.send_message(
                    chat_id=chat_id,
                    text="Произошла ошибка соединения. Пожалуйста, попробуйте ещё раз.",
                )
            except Exception:
                pass
            return "START"
        except Exception as err:
            logger.critical(
                f"КРИТИЧЕСКАЯ ОШИБКА в {func.__name__}: {type(err).__name__} - {err}",
                exc_info=True,
            )
            chat_id = update.effective_chat.id
            try:
                context.bot.send_message(
                    chat_id=chat_id,
                    text="Что-то пошло не так. Возвращаю в главное меню.",
                )
            except Exception:
                pass
            return "START"

    return wrapper


def send_action(action):
    """Отправляет указанный статус, пока работает хендлер."""

    def decorator(func):
        @wraps(func)
        def command_func(update, context, *args, **kwargs):
            context.bot.send_chat_action(
                chat_id=update.effective_message.chat_id, action=action
            )
            return func(update, context, *args, **kwargs)

        return command_func

    return decorator


def _track_message(chat_id, message_id, context):
    """Кладёт ID сообщения в набор на удаление."""
    redis_connect = context.bot_data["redis"]
    key = f"chat_msgs:{chat_id}"
    redis_connect.sadd(key, message_id)
    redis_connect.expire(key, MESSAGES_TO_DELETE_TTL_SECONDS)


def cleanup_chat(chat_id, context):
    """Удаляет все отслеживаемые сообщения чата."""
    redis_connect = context.bot_data["redis"]
    key = f"chat_msgs:{chat_id}"

    for tracked_message_id in redis_connect.smembers(key):
        try:
            context.bot.delete_message(
                chat_id=chat_id, message_id=int(tracked_message_id)
            )
        except BadRequest:
            pass
        redis_connect.srem(key, tracked_message_id)


def _format_cart_text(cart_items):
    """Форматирует список позиций в строку."""
    if not cart_items:
        return "Ваша корзина пуста."

    text = "<b>Ваша корзина:</b>\n\n"
    for item in cart_items:
        attrs = item.get("attributes", {})
        weight = attrs.get("quantity", 0)
        product = attrs.get("product", {}).get("data")
        if product:
            product_attrs = product.get("attributes", {})
            title = product_attrs.get("title", "Без названия")
            price = product_attrs.get("price", "0")
            text += f"{title} — {weight} кг — {price} руб.\n"

    return text


def _finish_checkout(chat_id, context, message_text):
    """Завершает оформление заказа: чистит чат, отправляет сообщение и сбрасывает в меню."""
    cleanup_chat(chat_id, context)
    message = context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("В главное меню", callback_data="back")]]
        ),
    )
    _track_message(chat_id, message.message_id, context)
    return "START"


# ==========================================
# 2. РАБОТА С API STRAPI
# ==========================================


def get_products(strapi_url, session):
    """Запрос к апи для получения списка товара"""
    url = f"{strapi_url}/api/products"
    response = session.get(url, timeout=5)
    return response.status_code, response.json()


def get_product(product_id, strapi_url, session):
    """Запрос к апи для получения информации о товаре"""
    url = f"{strapi_url}/api/products/{product_id}?populate=*"
    response = session.get(url, timeout=5)
    return response.status_code, response.json()


def _fetch_product_details(product_id, strapi_url, session):
    """Запрашивает данные по товару у апи."""
    status, product_info = get_product(product_id, strapi_url, session)
    if status != 200:
        return None

    attributes = product_info.get("data", {}).get("attributes", {})
    return {
        "id": product_id,
        "title": attributes.get("title", "Без названия"),
        "description": attributes.get("description", "Нет описания"),
        "price": attributes.get("price", "Цена не указана"),
        "picture_data": attributes.get("picture", {}).get("data"),
    }


def _load_picture(picture_data, strapi_url, session):
    """Загружает картинку из API."""
    image_url = picture_data["attributes"]["url"]
    if image_url.startswith("/"):
        image_url = f"{strapi_url}{image_url}"

    image_response = session.get(image_url, timeout=10)
    image_bytes = io.BytesIO(image_response.content)
    return image_bytes


def get_or_create_cart(chat_id, strapi_url, session):
    """Ищет корзину по tg_id. Если нет — создаёт пустую."""
    response = session.get(
        f"{strapi_url}/api/carts?filters[tg_id][$eq]={chat_id}", timeout=5
    )
    carts = response.json().get("data") or [] if response.status_code == 200 else []
    if carts:
        return carts[0]["id"]

    response = session.post(
        f"{strapi_url}/api/carts",
        json={"data": {"tg_id": str(chat_id)}},
        timeout=5,
    )
    if response.status_code not in (200, 201):
        raise requests.HTTPError(f"Не удалось создать корзину: {response.text}")
    return response.json()["data"]["id"]


def add_item_to_cart(cart_id, product_id, weight, strapi_url, session):
    """Создаёт позицию и сразу привязывает её к корзине."""
    url_item = f"{strapi_url}/api/cart-items"
    payload_item = {
        "data": {
            "quantity": weight,
            "product": {"connect": [product_id]},
            "cart": cart_id,
        }
    }
    response = session.post(url_item, json=payload_item, timeout=5)
    if response.status_code not in (200, 201):
        raise requests.HTTPError(f"Ошибка создания позиции: {response.text}")
    return response.json()["data"]["id"]


def get_cart_with_products(chat_id, strapi_url, session):
    """Получает корзину пользователя вместе с товарами."""
    url = f"{strapi_url}/api/carts?filters[tg_id][$eq]={chat_id}&populate[cart_items][populate]=product"
    response = session.get(url, timeout=5)
    if response.status_code != 200:
        return []
    carts = response.json().get("data", [])
    if not carts:
        return []
    cart_items = carts[0].get("attributes", {}).get("cart_items") or {}
    return cart_items.get("data", [])


def clear_user_cart(cart_items, strapi_url, session):
    """Очищает корзину."""
    for item in cart_items:
        url = f"{strapi_url}/api/cart-items/{item['id']}"
        response = session.delete(url, timeout=5)
        response.raise_for_status()


def find_client(chat_id, strapi_url, session):
    """Только ищет клиента по tg_id. Возвращает ID или None."""
    url = f"{strapi_url}/api/clients?filters[tg_id][$eq]={chat_id}"
    response = session.get(url, timeout=5)
    if response.status_code == 200:
        clients = response.json().get("data", [])
        if clients:
            return clients[0]["id"]
    return None


def get_or_create_client(chat_id, email, strapi_url, session):
    """Ищет клиента по tg_id. Если нет — создаёт нового с email."""
    client_id = find_client(chat_id, strapi_url, session)
    if client_id:
        return client_id

    url = f"{strapi_url}/api/clients"
    payload = {
        "data": {
            "tg_id": str(chat_id),
            "email": email,
        }
    }
    response = session.post(url, json=payload, timeout=5)
    response.raise_for_status()
    return response.json()["data"]["id"]


# ==========================================
# 3. КЛАВИАТУРЫ И СООБЩЕНИЯ
# ==========================================
def _build_menu(context):
    """Создает инлайн-клавиатуру"""
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]
    status, products_info = get_products(strapi_url, session)
    keyboard = []
    if status != 200:
        keyboard = [
            [InlineKeyboardButton("Ошибка загрузки", callback_data="error")],
        ]
        return InlineKeyboardMarkup(keyboard)
    products = products_info.get("data", [])

    for product in products:
        product_id = str(product["id"])

        title = product["attributes"]["title"]

        keyboard.append(
            [InlineKeyboardButton(title, callback_data=product_id)],
        )

    keyboard.append([InlineKeyboardButton("🛒 Моя корзина", callback_data="cart")])

    if not keyboard:
        keyboard = [
            [InlineKeyboardButton("Товаров нет", callback_data="empty")],
        ]
    return InlineKeyboardMarkup(keyboard)


def add_cart_menu(product_id):
    """Добавляет кнопки корзины и возврат к меню."""

    keyboard = [
        [
            InlineKeyboardButton(
                "Добавить в корзину",
                callback_data=f"add_{product_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🛒 Моя корзина",
                callback_data="cart",
            ),
        ],
        [
            InlineKeyboardButton(
                "Вернуться в меню",
                callback_data="back",
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _send_product_message(update, context, product_data):
    """Отправляет карточку товара."""
    chat_id = update.callback_query.message.chat_id
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]
    text = (
        f"<b>{product_data['title']}</b>\n\n"
        f"{product_data['description']}\n\n"
        f"<b>Цена:</b> {product_data['price']} руб."
    )
    markup = add_cart_menu(product_data["id"])

    if product_data["picture_data"]:
        image_bytes = _load_picture(product_data["picture_data"], strapi_url, session)
        return context.bot.send_photo(
            chat_id=chat_id,
            photo=image_bytes,
            caption=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        return context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )


# ==========================================
# 4. ХЭНДЛЕРЫ И СТЕЙТ-МАШИНА
# ==========================================
@log_errors
@send_action(ChatAction.TYPING)
def start(update, context):
    """Показывает меню: удаляет старое сообщение и шлёт новое."""
    if update.callback_query:
        update.callback_query.answer()

    chat_id = update.effective_chat.id
    cleanup_chat(chat_id, context)

    text = "Пожалуйста, выбери нужный пункт меню:"
    markup = _build_menu(context)

    message = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
    )

    _track_message(chat_id, message.message_id, context)
    return "HANDLE_MENU"


@log_errors
@send_action(ChatAction.UPLOAD_PHOTO)
def handle_menu(update, context):
    """Показывает выбор пользователя."""
    query = update.callback_query

    if query.data == "cart":
        return handle_cart(update, context)

    query.answer()
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]
    product_data = _fetch_product_details(query.data, strapi_url, session)

    if not product_data:
        query.edit_message_text(text="Ошибка: товар не найден.")
        return "START"

    chat_id = query.message.chat_id
    cleanup_chat(chat_id, context)
    sent_message = _send_product_message(update, context, product_data)
    _track_message(chat_id, sent_message.message_id, context)

    return "HANDLE_DESCRIPTION"


@log_errors
@send_action(ChatAction.TYPING)
def handle_description(update, context):
    query = update.callback_query

    if query.data == "back":
        return start(update, context)

    if query.data == "cart":
        return handle_cart(update, context)

    if not query.data.startswith("add_"):
        query.answer()
        return "HANDLE_DESCRIPTION"

    product_id = query.data[len("add_") :]
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]

    try:
        cart_id = get_or_create_cart(query.message.chat_id, strapi_url, session)
        add_item_to_cart(cart_id, product_id, 1, strapi_url, session)
        query.answer("Товар добавлен!", show_alert=True)
    except Exception:
        query.answer("Ошибка при добавлении", show_alert=True)
        raise

    return "HANDLE_DESCRIPTION"


def _render_cart(chat_id, context, strapi_url, session):
    """Внутренняя функция: рисует корзину с кнопками."""
    cart_items = get_cart_with_products(chat_id, strapi_url, session)
    text = _format_cart_text(cart_items)

    keyboard = []
    if cart_items:
        keyboard.append([InlineKeyboardButton("Оплатить", callback_data="pay")])
        keyboard.append(
            [InlineKeyboardButton("Очистить корзину", callback_data="clear_cart")]
        )
    keyboard.append([InlineKeyboardButton("В главное меню", callback_data="back")])

    cleanup_chat(chat_id, context)
    sent_message = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    _track_message(chat_id, sent_message.message_id, context)


def _clear_cart(chat_id, context, strapi_url, query, session):
    """Внутренняя функция: очищает корзину."""
    cart_items = get_cart_with_products(chat_id, strapi_url, session)
    clear_user_cart(cart_items, strapi_url, session)
    query.answer("Корзина очищена!", show_alert=False)
    _render_cart(chat_id, context, strapi_url, session)


def _process_payment(chat_id, context, strapi_url, query):
    """Внутренняя функция: запускает оформление заказа."""
    session = context.bot_data["strapi_session"]
    cart_items = get_cart_with_products(chat_id, strapi_url, session)
    if not cart_items:
        query.answer("Корзина пуста", show_alert=True)
        return "HANDLE_CART"

    query.answer()
    client_id = find_client(chat_id, strapi_url, session)

    if client_id:
        logger.info(f"Клиент {client_id} уже в базе. Быстрое оформление.")
        clear_user_cart(cart_items, strapi_url, session)
        return _finish_checkout(
            chat_id, context, "Спасибо! Заказ оформлен. Менеджер свяжется с вами."
        )
    else:
        cleanup_chat(chat_id, context)
        sent_msg = context.bot.send_message(
            chat_id=chat_id,
            text="Для оформления заказа, пожалуйста, отправьте ваш email:",
        )
        _track_message(chat_id, sent_msg.message_id, context)
        return "WAITING_EMAIL"


@log_errors
@send_action(ChatAction.TYPING)
def handle_cart(update, context):
    """Показывает корзину и роутит нажатия кнопок."""
    query = update.callback_query
    chat_id = query.message.chat_id
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]

    if query.data == "back":
        return start(update, context)
    elif query.data == "clear_cart":
        _clear_cart(chat_id, context, strapi_url, query, session)
        return "HANDLE_CART"
    elif query.data == "pay":
        return _process_payment(chat_id, context, strapi_url, query)
    else:
        query.answer()
        _render_cart(chat_id, context, strapi_url, session)
        return "HANDLE_CART"


@log_errors
@send_action(ChatAction.TYPING)
def handle_email(update, context):
    """Ловит email от пользователя."""
    email = update.message.text.strip()
    chat_id = update.message.chat_id
    strapi_url = context.bot_data["strapi_url"]

    if "@" not in email or "." not in email:
        cleanup_chat(chat_id, context)
        retry_prompt_message = context.bot.send_message(
            chat_id=chat_id,
            text="Похоже, это не email. Попробуйте еще раз:",
        )
        _track_message(chat_id, retry_prompt_message.message_id, context)
        return "WAITING_EMAIL"

    logger.info(f"Получен email от пользователя {chat_id}: {email}")
    session = context.bot_data["strapi_session"]

    try:
        client_id = get_or_create_client(chat_id, email, strapi_url, session)
        logger.info(f"Клиент сохранен в Strapi, ID: {client_id}")
    except requests.HTTPError as err:
        if err.response.status_code == 400:
            logger.warning(f"Strapi отклонил email: {err.response.text}")
            cleanup_chat(chat_id, context)
            retry_prompt_message = context.bot.send_message(
                chat_id=chat_id,
                text="Похоже, такой email не существует или в нём опечатка. Попробуйте еще раз:",
            )
            _track_message(chat_id, retry_prompt_message.message_id, context)
            return "WAITING_EMAIL"
        raise

    cart_items = get_cart_with_products(chat_id, strapi_url, session)
    if cart_items:
        clear_user_cart(cart_items, strapi_url, session)
        logger.info(f"Корзина пользователя {chat_id} очищена.")
    else:
        logger.info(f"Внимание: Корзина пользователя {chat_id} пуста.")

    return _finish_checkout(
        chat_id, context, "Спасибо! Мы получили вашу почту. Менеджер свяжется с вами."
    )


@log_errors
def handle_users_reply(update, context):
    """
    Функция, которая запускается при любом сообщении
    от пользователя и решает как его обработать.
    """
    redis_connect = context.bot_data["redis"]

    if update.message:
        user_reply = update.message.text
        chat_id = update.message.chat_id
        _track_message(chat_id, update.message.message_id, context)
    elif update.callback_query:
        user_reply = update.callback_query.data
        chat_id = update.callback_query.message.chat_id
        _track_message(chat_id, update.callback_query.message.message_id, context)
    else:
        return

    if user_reply == "/start":
        user_state = "START"
    else:
        user_state = redis_connect.get(chat_id) or "START"

    if update.message and user_state != "WAITING_EMAIL":
        user_state = "START"

    states_functions = {
        "START": start,
        "HANDLE_MENU": handle_menu,
        "HANDLE_DESCRIPTION": handle_description,
        "HANDLE_CART": handle_cart,
        "WAITING_EMAIL": handle_email,
    }

    state_handler = states_functions[user_state]

    next_state = state_handler(update, context)

    if next_state:
        redis_connect.set(chat_id, next_state)


# ==========================================
# 5. ЗАПУСК БОТА
# ==========================================
def main():
    env = Env()
    env.read_env()
    strapi_url = env.str("STRAPI_URL", default="http://localhost:1337")
    proxy_url = env.str("TG_PROXY_URL", default=None)
    request_kwargs = {"proxy_url": proxy_url} if proxy_url else {}

    redis_url = env.str("REDIS_URL", default="redis://localhost:6379/0")
    redis_connect = redis.from_url(redis_url, decode_responses=True)

    token = env.str("TG_BOT_TOKEN")
    strapi_session = requests.Session()

    updater = Updater(token, request_kwargs=request_kwargs)

    dispatcher = updater.dispatcher
    dispatcher.bot_data["redis"] = redis_connect
    dispatcher.bot_data["strapi_url"] = strapi_url
    dispatcher.bot_data["strapi_session"] = strapi_session
    dispatcher.add_handler(CommandHandler("start", handle_users_reply))
    dispatcher.add_handler(CallbackQueryHandler(handle_users_reply))
    dispatcher.add_handler(MessageHandler(Filters.text, handle_users_reply))

    logger.info("Бот запущен")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
