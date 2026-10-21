import logging
from functools import wraps

import requests
from redis.exceptions import RedisError
from telegram.error import BadRequest, TelegramError

from keyboards import build_back_menu

logger = logging.getLogger("fish-bot")

MESSAGES_TO_DELETE_TTL_SECONDS = 86400


def log_errors(func):
    """Декоратор: логирует ошибки, сообщает пользователю и сбрасывает состояние."""

    @wraps(func)
    def wrapper(update, context, *args, **kwargs):
        try:
            return func(update, context, *args, **kwargs)
        except (requests.RequestException, TelegramError, RedisError) as err:
            logger.error(f"Сетевая ошибка в {func.__name__}: {err}")
            error_text = "Произошла ошибка соединения. Пожалуйста, попробуйте ещё раз."
        except Exception as err:
            logger.critical(
                f"КРИТИЧЕСКАЯ ОШИБКА в {func.__name__}: {type(err).__name__} - {err}",
                exc_info=True,
            )
            error_text = "Что-то пошло не так. Возвращаю в главное меню."

        chat_id = update.effective_chat.id
        try:
            msg = context.bot.send_message(
                chat_id=chat_id,
                text=error_text,
                reply_markup=build_back_menu(),
            )
            _track_message(chat_id, msg.message_id, context)
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
        reply_markup=build_back_menu(),
    )
    _track_message(chat_id, message.message_id, context)
    return "START"
