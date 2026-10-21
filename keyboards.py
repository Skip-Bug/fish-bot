from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from strapi_client import get_products


def build_menu(context):
    """Создает инлайн-клавиатуру для главного меню."""
    strapi_url = context.bot_data["strapi_url"]
    session = context.bot_data["strapi_session"]
    is_ok, products_info = get_products(strapi_url, session)

    if not is_ok:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Ошибка загрузки", callback_data="error")],
            ]
        )

    products = products_info.get("data", [])
    keyboard = []

    if not products:
        keyboard.append([InlineKeyboardButton("Товаров нет", callback_data="empty")])
    else:
        for product in products:
            product_id = str(product["id"])
            if "attributes" in product:
                title = product["attributes"].get("title", "Без названия")
            else:
                title = product.get("title", "Без названия")
            keyboard.append([InlineKeyboardButton(title, callback_data=product_id)])

    keyboard.append([InlineKeyboardButton("🛒 Моя корзина", callback_data="cart")])
    return InlineKeyboardMarkup(keyboard)


def add_cart_menu(product_id):
    """Добавляет кнопки корзины и возврат к меню."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Добавить в корзину", callback_data=f"add_{product_id}"
                )
            ],
            [InlineKeyboardButton("🛒 Моя корзина", callback_data="cart")],
            [InlineKeyboardButton("Вернуться в меню", callback_data="back")],
        ]
    )


def build_cart_menu(cart_items):
    """Клавиатура для экрана корзины."""
    keyboard = []
    if cart_items:
        keyboard.append([InlineKeyboardButton("Оплатить", callback_data="pay")])
        keyboard.append(
            [InlineKeyboardButton("Очистить корзину", callback_data="clear_cart")]
        )
    keyboard.append([InlineKeyboardButton("В главное меню", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


def build_back_menu():
    """Клавиатура с одной кнопкой возврата (для финала и ошибок)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("В главное меню", callback_data="back")]]
    )
