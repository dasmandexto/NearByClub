# NearByClub 🗺️

> *Социальный эксперимент: могут ли незнакомые люди найти связь просто потому что они рядом?*

Telegram Mini App + Bot для анонимного обмена сообщениями с людьми в радиусе до 1 км.

---

## Как это работает

1. Пользователь делится геолокацией → сохраняется на 2 часа
2. Пишет сообщение → получают все в радиусе **500м** (до 1 км по слайдеру)
3. Все анонимны — только случайный зверь-аватар (🦊 Fox, 🐺 Wolf, …)
4. Карта с тепловым слоем (OSM + Leaflet.heat) — красные зоны = больше людей

---

## Быстрый старт

### 1. Настройка

```bash
cp .env.example .env
# Заполни BOT_TOKEN и WEBAPP_URL
```

### 2. Запуск через Docker (рекомендуется)

```bash
docker compose up -d
```

### 3. Запуск вручную (dev)

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Запусти PostgreSQL + PostGIS отдельно, затем:
python server.py   # FastAPI на :8000
python bot.py      # Telegram bot
```

### 4. Anniversary blast (5 месяцев 🎉)

```bash
# Сначала подключи свою БД в blast.py → get_all_user_ids()
python blast.py
```

---

## Структура проекта

```
NearByClub/
├── bot.py              # aiogram 3 bot
├── server.py           # FastAPI + WebSocket сервер
├── blast.py            # Разовая рассылка (anniversary)
├── db/
│   └── models.py       # SQLAlchemy + PostGIS модели и гео-запросы
├── webapp/
│   └── index.html      # Telegram Mini App (OSM heatmap)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Настройки (.env)

| Параметр | По умолчанию | Описание |
|---|---|---|
| `DEFAULT_RADIUS_M` | `500` | Радиус по умолчанию (м) |
| `MAX_RADIUS_M` | `1000` | Максимальный радиус (жёсткий лимит) |
| `LOCATION_TTL_HOURS` | `2` | Сколько хранится локация |
| `RATE_LIMIT_PER_HOUR` | `5` | Макс. рассылок в час на пользователя |

---

## Требования

- Python 3.11+
- PostgreSQL 14+ с расширением **PostGIS**
- Redis 7+
- HTTPS домен для Telegram Mini App (Telegram требует HTTPS)

---

*Сделано за ночь. С любовью. 🔴*
