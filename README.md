# Практическая №13 — микросервисы (Flask + SQLite)

Сервисы:
- `user-service` (внутри docker-сети `:5000`) — CRUD пользователей
- `order-service` (внутри docker-сети `:5002`) — CRUD заказов + сетевые вызовы в `user-service` и `notification-service`
- `notification-service` (внутри docker-сети `:5001`) — `POST /notify` (лог + SQLite), просмотр `GET /notifications`

Проброс на хост по умолчанию (можно менять через переменные окружения в `docker compose`):
- `user-service` → `localhost:15000`
- `notification-service` → `localhost:15001`
- `order-service` → `localhost:15002`

## Запуск

```bash
docker compose up --build
```

## Быстрый прогон сценария (curl)

Требуется `jq`.

```bash
./scripts/demo.sh
```

Если запускать через `sh`, shebang игнорируется — используйте именно `./scripts/demo.sh` или `bash ./scripts/demo.sh`.

## Проверка устойчивости (отключение сервиса)

1) Остановить уведомления:
```bash
docker compose stop notification-service
```

2) Создать заказ через `order-service` — заказ создастся, а поле `notification_sent` станет `false`:
```bash
curl -sS -X POST http://localhost:15002/orders \
  -H 'Content-Type: application/json' \
  -d '{"user_id":1,"item":"Pen","amount":1}' | jq .
```

3) Вернуть уведомления:
```bash
docker compose start notification-service
```
