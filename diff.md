diff --git a/tg_bot.py b/tg_bot.py
index c928e36..65f0bd6 100644
--- a/tg_bot.py
+++ b/tg_bot.py
@@ -1,4 +1,3 @@
-import io
 import logging
 import sys
 from functools import wraps
@@ -17,12 +16,20 @@ from telegram.ext import (
     Updater,
 )
 
+from strapi_client import (
+    add_item_to_cart,
+    clear_user_cart,
+    fetch_product_details,
+    find_client,
+    get_cart_with_products,
+    get_or_create_cart,
+    get_or_create_client,
+    get_products,
+    load_picture,
+)
+
 sys.stdout.reconfigure(encoding="utf-8")
 
-logging.basicConfig(
-    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
-    level=logging.INFO,
-)
 logger = logging.getLogger("fish-bot")
 
 MESSAGES_TO_DELETE_TTL_SECONDS = 86400
@@ -38,31 +45,32 @@ def log_errors(func):
     def wrapper(update, context, *args, **kwargs):
         try:
             return func(update, context, *args, **kwargs)
+
         except (requests.RequestException, TelegramError, RedisError) as err:
             logger.error(f"Сетевая ошибка в {func.__name__}: {err}")
-            chat_id = update.effective_chat.id
-            try:
-                context.bot.send_message(
-                    chat_id=chat_id,
-                    text="Произошла ошибка соединения. Пожалуйста, попробуйте ещё раз.",
-                )
-            except Exception:
-                pass
-            return "START"
+            error_text = "Произошла ошибка соединения. Пожалуйста, попробуйте ещё раз."
+
         except Exception as err:
             logger.critical(
                 f"КРИТИЧЕСКАЯ ОШИБКА в {func.__name__}: {type(err).__name__} - {err}",
                 exc_info=True,
             )
-            chat_id = update.effective_chat.id
-            try:
-                context.bot.send_message(
-                    chat_id=chat_id,
-                    text="Что-то пошло не так. Возвращаю в главное меню.",
-                )
-            except Exception:
-                pass
-            return "START"
+            error_text = "Что-то пошло не так. Возвращаю в главное меню."
+
+        chat_id = update.effective_chat.id
+        try:
+            msg = context.bot.send_message(
+                chat_id=chat_id,
+                text=error_text,
+                reply_markup=InlineKeyboardMarkup(
+                    [[InlineKeyboardButton("В главное меню", callback_data="back")]]
+                ),
+            )
+            _track_message(chat_id, msg.message_id, context)
+        except Exception:
+            pass
+
+        return "START"
 
     return wrapper
 
@@ -139,137 +147,6 @@ def _finish_checkout(chat_id, context, message_text):
     return "START"
 
 
-# ==========================================
-# 2. РАБОТА С API STRAPI
-# ==========================================
-
-
-def get_products(strapi_url, session):
-    """Запрос к апи для получения списка товара"""
-    url = f"{strapi_url}/api/products"
-    response = session.get(url, timeout=5)
-    return response.status_code, response.json()
-
-
-def get_product(product_id, strapi_url, session):
-    """Запрос к апи для получения информации о товаре"""
-    url = f"{strapi_url}/api/products/{product_id}?populate=*"
-    response = session.get(url, timeout=5)
-    return response.status_code, response.json()
-
-
-def _fetch_product_details(product_id, strapi_url, session):
-    """Запрашивает данные по товару у апи."""
-    status, product_info = get_product(product_id, strapi_url, session)
-    if status != 200:
-        return None
-
-    attributes = product_info.get("data", {}).get("attributes", {})
-    return {
-        "id": product_id,
-        "title": attributes.get("title", "Без названия"),
-        "description": attributes.get("description", "Нет описания"),
-        "price": attributes.get("price", "Цена не указана"),
-        "picture_data": attributes.get("picture", {}).get("data"),
-    }
-
-
-def _load_picture(picture_data, strapi_url, session):
-    """Загружает картинку из API."""
-    image_url = picture_data["attributes"]["url"]
-    if image_url.startswith("/"):
-        image_url = f"{strapi_url}{image_url}"
-
-    image_response = session.get(image_url, timeout=10)
-    image_bytes = io.BytesIO(image_response.content)
-    return image_bytes
-
-
-def get_or_create_cart(chat_id, strapi_url, session):
-    """Ищет корзину по tg_id. Если нет — создаёт пустую."""
-    response = session.get(
-        f"{strapi_url}/api/carts?filters[tg_id][$eq]={chat_id}", timeout=5
-    )
-    carts = response.json().get("data") or [] if response.status_code == 200 else []
-    if carts:
-        return carts[0]["id"]
-
-    response = session.post(
-        f"{strapi_url}/api/carts",
-        json={"data": {"tg_id": str(chat_id)}},
-        timeout=5,
-    )
-    if response.status_code not in (200, 201):
-        raise requests.HTTPError(f"Не удалось создать корзину: {response.text}")
-    return response.json()["data"]["id"]
-
-
-def add_item_to_cart(cart_id, product_id, weight, strapi_url, session):
-    """Создаёт позицию и сразу привязывает её к корзине."""
-    url_item = f"{strapi_url}/api/cart-items"
-    payload_item = {
-        "data": {
-            "quantity": weight,
-            "product": {"connect": [product_id]},
-            "cart": cart_id,
-        }
-    }
-    response = session.post(url_item, json=payload_item, timeout=5)
-    if response.status_code not in (200, 201):
-        raise requests.HTTPError(f"Ошибка создания позиции: {response.text}")
-    return response.json()["data"]["id"]
-
-
-def get_cart_with_products(chat_id, strapi_url, session):
-    """Получает корзину пользователя вместе с товарами."""
-    url = f"{strapi_url}/api/carts?filters[tg_id][$eq]={chat_id}&populate[cart_items][populate]=product"
-    response = session.get(url, timeout=5)
-    if response.status_code != 200:
-        return []
-    carts = response.json().get("data", [])
-    if not carts:
-        return []
-    cart_items = carts[0].get("attributes", {}).get("cart_items") or {}
-    return cart_items.get("data", [])
-
-
-def clear_user_cart(cart_items, strapi_url, session):
-    """Очищает корзину."""
-    for item in cart_items:
-        url = f"{strapi_url}/api/cart-items/{item['id']}"
-        response = session.delete(url, timeout=5)
-        response.raise_for_status()
-
-
-def find_client(chat_id, strapi_url, session):
-    """Только ищет клиента по tg_id. Возвращает ID или None."""
-    url = f"{strapi_url}/api/clients?filters[tg_id][$eq]={chat_id}"
-    response = session.get(url, timeout=5)
-    if response.status_code == 200:
-        clients = response.json().get("data", [])
-        if clients:
-            return clients[0]["id"]
-    return None
-
-
-def get_or_create_client(chat_id, email, strapi_url, session):
-    """Ищет клиента по tg_id. Если нет — создаёт нового с email."""
-    client_id = find_client(chat_id, strapi_url, session)
-    if client_id:
-        return client_id
-
-    url = f"{strapi_url}/api/clients"
-    payload = {
-        "data": {
-            "tg_id": str(chat_id),
-            "email": email,
-        }
-    }
-    response = session.post(url, json=payload, timeout=5)
-    response.raise_for_status()
-    return response.json()["data"]["id"]
-
-
 # ==========================================
 # 3. КЛАВИАТУРЫ И СООБЩЕНИЯ
 # ==========================================
@@ -286,21 +163,23 @@ def _build_menu(context):
         return InlineKeyboardMarkup(keyboard)
     products = products_info.get("data", [])
 
-    for product in products:
-        product_id = str(product["id"])
+    if not products:
+        keyboard.append([InlineKeyboardButton("Товаров нет", callback_data="empty")])
+    else:
+        for product in products:
+            product_id = str(product["id"])
 
-        title = product["attributes"]["title"]
+            if "attributes" in product:
+                title = product["attributes"].get("title", "Без названия")
+            else:
+                title = product.get("title", "Без названия")
 
-        keyboard.append(
-            [InlineKeyboardButton(title, callback_data=product_id)],
-        )
+            keyboard.append(
+                [InlineKeyboardButton(title, callback_data=product_id)],
+            )
 
     keyboard.append([InlineKeyboardButton("🛒 Моя корзина", callback_data="cart")])
 
-    if not keyboard:
-        keyboard = [
-            [InlineKeyboardButton("Товаров нет", callback_data="empty")],
-        ]
     return InlineKeyboardMarkup(keyboard)
 
 
@@ -343,7 +222,7 @@ def _send_product_message(update, context, product_data):
     markup = add_cart_menu(product_data["id"])
 
     if product_data["picture_data"]:
-        image_bytes = _load_picture(product_data["picture_data"], strapi_url, session)
+        image_bytes = load_picture(product_data["picture_data"], strapi_url, session)
         return context.bot.send_photo(
             chat_id=chat_id,
             photo=image_bytes,
@@ -394,11 +273,13 @@ def handle_menu(update, context):
 
     if query.data == "cart":
         return handle_cart(update, context)
-
+    if query.data == "empty":
+        query.answer("В магазине пока нет доступных товаров.", show_alert=True)
+        return "HANDLE_MENU"
     query.answer()
     strapi_url = context.bot_data["strapi_url"]
     session = context.bot_data["strapi_session"]
-    product_data = _fetch_product_details(query.data, strapi_url, session)
+    product_data = fetch_product_details(query.data, strapi_url, session)
 
     if not product_data:
         query.edit_message_text(text="Ошибка: товар не найден.")
@@ -616,6 +497,10 @@ def handle_users_reply(update, context):
 # 5. ЗАПУСК БОТА
 # ==========================================
 def main():
+    logging.basicConfig(
+        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
+        level=logging.INFO,
+    )
     env = Env()
     env.read_env()
     strapi_url = env.str("STRAPI_URL", default="http://localhost:1337")
