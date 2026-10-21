import logging
import sys

import redis
import requests
from environs import Env
from telegram import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    Filters,
    MessageHandler,
    Updater,
)

from keyboards import (
    add_cart_menu,
    build_cart_menu,
    build_menu,
)
from strapi_client import (
    add_item_to_cart,
    clear_user_cart,
    fetch_product_details,
    find_client,
    get_cart_with_products,
    get_or_create_cart,
    get_or_create_client,
    load_picture,
)
from tg_bot_utils import (
    cleanup_chat,
    finish_checkout,
    format_cart_text,
    log_errors,
    send_action,
    track_message,
)

sys.stdout.reconfigure(encoding="utf-8")

logger = logging.getLogger("fish-bot")


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
        image_bytes = load_picture(product_data["picture_data"], strapi_url, session)
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


@log_errors
@send_action(ChatAction.TYPING)
def start(update, context):
    """Показывает меню: удаляет старое сообщение и шлёт новое."""
    if update.callback_query:
        update.callback_query.answer()

    chat_id = update.effective_chat.id
    cleanup_chat(chat_id, context)

    text = "Пожалуйста, выбери нужный пункт меню:"
    markup = build_menu(context, offset=0)

    message = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
    )

    track_message(chat_id, message.message_id, context)
    return "HANDLE_MENU"


@log_errors
@send_action(ChatAction.TYPING)
def handle_menu(update, context):
    """Показывает выбор пользователя или пагинацию."""
    query = update.callback_query

    if query.data == "cart":
        return handle_cart(update, context)
    if query.data == "empty":
        query.answer("В магазине пока нет доступных товаров.", show_alert=True)
        return "HANDLE_MENU"

    if query.data.startswith("more_"):
        offset = int(query.data.split("_")[1])
        return show_more_products(update, context, offset)

    query.answer()
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]
    product_data = fetch_product_details(query.data, strapi_url, session)

    if not product_data:
        query.edit_message_text(text="Ошибка: товар не найден.")
        return "START"

    chat_id = query.message.chat_id
    cleanup_chat(chat_id, context)
    sent_message = _send_product_message(update, context, product_data)
    track_message(chat_id, sent_message.message_id, context)

    return "HANDLE_DESCRIPTION"


@log_errors
@send_action(ChatAction.TYPING)
def show_more_products(update, context, offset):
    """Показывает следующую порцию товаров."""
    query = update.callback_query
    query.answer()

    chat_id = query.message.chat_id
    cleanup_chat(chat_id, context)

    text = "Пожалуйста, выбери нужный пункт меню:"
    markup = build_menu(context, offset=offset)

    message = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
    )
    track_message(chat_id, message.message_id, context)

    return "HANDLE_MENU"


@log_errors
@send_action(ChatAction.UPLOAD_PHOTO)
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
    text = format_cart_text(cart_items)

    cleanup_chat(chat_id, context)
    sent_message = context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=build_cart_menu(cart_items),
    )
    track_message(chat_id, sent_message.message_id, context)


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
        return finish_checkout(
            chat_id, context, "Спасибо! Заказ оформлен. Менеджер свяжется с вами."
        )
    cleanup_chat(chat_id, context)
    sent_msg = context.bot.send_message(
        chat_id=chat_id,
        text="Для оформления заказа, пожалуйста, отправьте ваш email:",
    )
    track_message(chat_id, sent_msg.message_id, context)
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
        track_message(chat_id, retry_prompt_message.message_id, context)
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
            track_message(chat_id, retry_prompt_message.message_id, context)
            return "WAITING_EMAIL"
        raise

    cart_items = get_cart_with_products(chat_id, strapi_url, session)
    if cart_items:
        clear_user_cart(cart_items, strapi_url, session)
        logger.info(f"Корзина пользователя {chat_id} очищена.")
    else:
        logger.info(f"Внимание: Корзина пользователя {chat_id} пуста.")

    return finish_checkout(
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
        track_message(chat_id, update.message.message_id, context)
    elif update.callback_query:
        user_reply = update.callback_query.data
        chat_id = update.callback_query.message.chat_id
        track_message(chat_id, update.callback_query.message.message_id, context)
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


def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
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
