# 🐟 Fish Shop

## 📖 Описание

Telegram-бот для заказа рыбы и рыбных продуктов. Пользователи просматривают каталог,
добавляют товары в корзину и оформляют заказ — всё прямо в Telegram.
Управление каталогом и данными через Strapi CMS.

[![GitHub Репозиторий](https://img.shields.io/badge/Репозиторий-GitHub-blue?logo=github)](https://github.com/Skip-Bug/fish-shop)
[![Лицензия MIT](https://img.shields.io/badge/Лицензия-MIT-blue.svg)](LICENSE)
[![Python 3.10](https://img.shields.io/badge/Python-3.10-blue)](https://www.python.org/)
[![Strapi 4](https://img.shields.io/badge/Strapi-4-purple)](https://strapi.io/)

## 🧰 Требования

- 🐍 **Python 3.10** (на остальных версиях не тестировалось).

  Проверить версию:

  ```bash
  python --version
  ```

- 🟥 **Redis** ([локально](https://redis.io/download/) или [удалённо](https://redis.com/redis-enterprise-cloud/)).

  Проверить, что запущен:

  ```bash
  redis-cli ping
  # → PONG
  ```

- 🟢 **Node.js 16+** (для Strapi CMS)

  Проверить версию:

  ```bash
  node --version
  ```

## ⚙️ Переменные окружения

### 🤖 Telegram-бот (`fish-bot/.env`)

- 🔑 **TG_BOT_TOKEN**: [инструкция по регистрации бота](https://way23.ru/%D1%80%D0%B5%D0%B3%D0%B8%D1%81%D1%82%D1%80%D0%B0%D1%86%D0%B8%D1%8F-%D0%B1%D0%BE%D1%82%D0%B0-%D0%B2-telegram.html).
- 🟡 **REDIS_URL**: URL подключения к Redis. По умолчанию: `redis://localhost:6379/0`.
- 🌐 **TG_PROXY_URL**: Прокси для Telegram API. По умолчанию: `socks5h://127.0.0.1:2080`.
- 🟣 **STRAPI_URL**: URL Strapi CMS. По умолчанию: `http://localhost:1337`.

Создайте файл `.env` в папке `fish-bot/`:

```env
TG_BOT_TOKEN=ваш_токен_телеграм_бота
REDIS_URL=redis://localhost:6379/0
TG_PROXY_URL=socks5h://127.0.0.1:2080
STRAPI_URL=http://localhost:1337
```

### 🗄️ Strapi CMS (`fish-cms/.env`)

При первом запуске `npm run develop` Strapi автоматически создаст `.env` и сам заполнит все ключи уникальными значениями. Трогать его не нужно.

Файл `.env` **не попадает в git** — у каждого разработчика свой.

## 🧱 Установка

**📦 Установите зависимости Python:**

```bash
cd fish-bot
pip install -r requirements.txt
```

**📦 Установите зависимости Node.js:**

```bash
cd fish-cms
npm install
```

## 🚀 Запуск

**1. Запустите Strapi CMS:**

```bash
cd fish-cms
npm run develop
```

Страница админ-панели: `http://localhost:1337/admin`.

**2. Запустите Telegram-бота:**

```bash
cd fish-bot
python tg_bot.py
```

## 🎮 Как пользоваться

- **/start** — главное меню с каталогом товаров.
- **Выбор товара** — название, описание, цена и фото.
- **Добавить в корзину** — товар добавляется на 1 кг.
- **🛒 Моя корзина** — просмотр и очистка корзины.
- **Оплатить** — оформление заказа (для новых клиентов запрашивается email).

## ⚖️ Лицензия

MIT License

## 🎡 Цель проекта

Код написан в образовательных целях на онлайн-курсе для разработчиков [dvmn.org](https://dvmn.org).
