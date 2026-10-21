import io
import logging

import requests

logger = logging.getLogger("fish-bot")


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


def fetch_product_details(product_id, strapi_url, session):
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


def load_picture(picture_data, strapi_url, session):
    """Загружает картинку из API."""
    image_url = picture_data["attributes"]["url"]
    if image_url.startswith("/"):
        image_url = f"{strapi_url}{image_url}"

    image_response = session.get(image_url, timeout=10)
    image_bytes = io.BytesIO(image_response.content)
    return image_bytes


def get_or_create_cart(user_id, strapi_url, session):
    """Ищет корзину по id пользователя. Если нет — создаёт пустую."""
    response = session.get(
        f"{strapi_url}/api/carts?filters[external_id][$eq]={user_id}", timeout=5
    )
    carts = response.json().get("data") or [] if response.status_code == 200 else []
    if carts:
        return carts[0]["id"]

    response = session.post(
        f"{strapi_url}/api/carts",
        json={"data": {"external_id": str(user_id)}},
        timeout=5,
    )
    if response.status_code not in (200, 201):
        logger.error(f"Не удалось создать корзину: {response.text}")
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
        logger.error(f"Ошибка создания позиции: {response.text}")
        raise requests.HTTPError(f"Ошибка создания позиции: {response.text}")
    return response.json()["data"]["id"]


def get_cart_with_products(user_id, strapi_url, session):
    """Получает корзину пользователя вместе с товарами."""
    url = f"{strapi_url}/api/carts?filters[external_id][$eq]={user_id}&populate[cart_items][populate]=product"
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


def find_client(user_id, strapi_url, session):
    """Только ищет клиента по id пользователя. Возвращает ID или None."""
    url = f"{strapi_url}/api/clients?filters[external_id][$eq]={user_id}"
    response = session.get(url, timeout=5)
    if response.status_code == 200:
        clients = response.json().get("data", [])
        if clients:
            return clients[0]["id"]
    return None


def get_or_create_client(user_id, email, strapi_url, session):
    """Ищет клиента по id пользователя. Если нет — создаёт нового с email."""
    client_id = find_client(user_id, strapi_url, session)
    if client_id:
        return client_id

    url = f"{strapi_url}/api/clients"
    payload = {
        "data": {
            "external_id": str(user_id),
            "email": email,
        }
    }
    response = session.post(url, json=payload, timeout=5)
    response.raise_for_status()
    return response.json()["data"]["id"]
