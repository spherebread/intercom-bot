# Telegram Bot для временного доступа к домофону

Telegram-бот для выдачи временных ссылок доступа к домофону, активации доступа пользователями и открытия двери через API.

## Что умеет бот

### Для администраторов

- Создавать временные доступы через пошаговый диалог в Telegram
- Настраивать количество использований:
  - фиксированное число
  - бесконечное количество использований
- Настраивать срок действия:
  - бессрочно
  - на заданное число дней
- Добавлять комментарий к доступу
- Получать ссылку активации вида `https://t.me/<bot>?start=<token>`
- Просматривать список всех доступов с пагинацией
- Отзывать доступы прямо из списка
- Открывать дверь без проверки доступа
- Открывать шлагбаум без проверки доступа
- Получать уведомление, когда кто-то открыл дверь
- При наличии `PREVIEW_URL` получать уведомление с фото, иначе текстовое сообщение

### Для пользователей

- Активировать доступ по ссылке `/start <token>`
- Открывать дверь через `/open` или кнопку в меню
- Открывать шлагбаум через `/parking` или кнопку в меню
- Проверять статус доступа через `/status` или кнопку в меню
- Пользоваться меню `/menu`

## Команды

### Администратор

- `/start` - открыть главное меню
- `/menu` - показать меню администратора
- `/create_access` - начать создание доступа
- `/list_accesses` - показать список доступов
- `/open` - открыть дверь без ограничений по доступу
- `/parking` - открыть шлагбаум без ограничений по доступу

### Пользователь

- `/start <token>` - активировать доступ по ссылке
- `/menu` - показать пользовательское меню
- `/open` - открыть дверь, если доступ активен
- `/parking` - открыть шлагбаум, если доступ активен
- `/status` - показать состояние доступа

## Как это работает

1. Администратор создает доступ в боте.
2. Бот сохраняет токен, лимит использований, срок действия и комментарий в PostgreSQL.
3. Администратор отправляет пользователю ссылку активации.
4. Пользователь открывает ссылку и привязывает доступ к своему Telegram ID.
5. При открытии двери бот вызывает `INTERCOM_ENDPOINT` через `PUT` с `Bearer`-токеном.
6. При открытии шлагбаума бот вызывает `PARKING_ENDPOINT` через `POST` с тем же `Bearer`-токеном.
7. После успешного открытия двери или шлагбаума бот отправляет уведомление администраторам.

## Переменные окружения

### Обязательные

- `TELEGRAM_BOT_TOKEN` - токен бота от [@BotFather](https://t.me/BotFather)
- `ADMIN_IDS` - Telegram ID администраторов через запятую, например `123456789,987654321`
- `INTERCOM_ENDPOINT` - `PUT`-эндпоинт API домофона для открытия двери
- `PARKING_ENDPOINT` - `POST`-эндпоинт API парковки для открытия шлагбаума
- `INTERCOM_TOKEN` - Bearer-токен для API домофона

### Опциональные

- `PREVIEW_URL` - URL изображения для отправки администраторам после открытия двери
- `DATABASE_URL` - строка подключения к PostgreSQL
  - по умолчанию: `postgresql://intercom_user:intercom_password@postgres:5432/intercom_db`
- `BOT_BASE_URL` - базовый URL бота для генерации ссылок активации
  - по умолчанию: `https://t.me/your_bot_username`

## Запуск через Docker Compose

### 1. Создайте `.env`

Скопируйте шаблон переменных окружения:

```bash
cp .env.example .env
```

Затем при необходимости отредактируйте `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
ADMIN_IDS=123456789
INTERCOM_ENDPOINT=https://example.com/intercom/open
PARKING_ENDPOINT=https://example.com/parking/open
INTERCOM_TOKEN=your_intercom_token
PREVIEW_URL=https://example.com/camera.jpg
BOT_BASE_URL=https://t.me/your_bot_username
DATABASE_URL=postgresql://intercom_user:intercom_password@postgres:5432/intercom_db
```

Если `PREVIEW_URL` не задан, бот будет отправлять администраторам только текстовое уведомление.

## Деплой через GitHub Actions и Portainer

Workflow `.github/workflows/ci-cd.yml` после push в `main` или `master`:

1. Собирает и публикует образ в GHCR.
2. Находит вручную созданный Swarm Stack по имени и нужный service внутри него.
3. Выполняет pull образа и force update только выбранного Swarm service.

В настройках репозитория GitHub добавьте:

- Repository variable `PORTAINER_URL` — URL Portainer без завершающего `/`, например `https://portainer.example.com`
- Repository variable `PORTAINER_STACK_NAME` — точное имя Stack в Portainer
- Repository variable `PORTAINER_SERVICE_NAME` — точное имя Swarm service, например `intercom-bot_bot`
- Repository secret `PORTAINER_API_KEY` — API-ключ Portainer с правами на чтение и обновление Stack

Для Docker Swarm используется API `POST /api/endpoints/{id}/forceupdateservice` с параметрами `ServiceID` и `PullImage: true`. Stack и service должны быть доступны API-ключу пользователя.

### 2. Запустите сервисы

```bash
docker compose up -d --build
```

### 3. Посмотрите логи

```bash
docker compose logs -f bot
```

## Локальный запуск без Docker

Требуется:

- Python 3.11+
- PostgreSQL

Установка и запуск:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Перед запуском убедитесь, что:

- PostgreSQL доступен по `DATABASE_URL`
- все обязательные переменные окружения заданы

## База данных

Приложение использует PostgreSQL и автоматически создает таблицы при запуске.

### Автоматическая миграция

При старте бот пытается автоматически обновить типы Telegram ID до `BIGINT`, если база была создана старой версией приложения.

### Ручная миграция

Если автоматическая миграция не сработала, запустите:

```bash
docker compose exec bot python migrate_telegram_ids.py
```

Если контейнер `bot` еще не поднят:

```bash
docker compose run --rm bot python migrate_telegram_ids.py
```

### Ошибка `integer out of range`

Эта ошибка обычно означает, что в таблицах БД все еще используется старый тип `INTEGER` для Telegram ID. В этом случае выполните миграцию `migrate_telegram_ids.py`.

## Структура проекта

- `bot.py` - основной файл Telegram-бота
- `config.py` - загрузка и проверка переменных окружения
- `database.py` - модели SQLAlchemy и работа с базой данных
- `intercom_service.py` - вызов API домофона
- `migrate_telegram_ids.py` - ручная миграция типов Telegram ID
- `docker-compose.yml` - запуск бота и PostgreSQL
- `Dockerfile` - сборка контейнера приложения

## Особенности реализации

- Используется `python-telegram-bot 20.7`
- Используется polling, webhook не требуется
- Доступ становится привязанным к Telegram ID пользователя после активации ссылки
- Для бессрочного доступа по времени используется `expires_at = NULL`
- Для бесконечного количества использований используется `max_uses = -1`
- Доступ считается неактивным, если:
  - он отозван
  - истек срок действия
  - исчерпан лимит использований
