# IB Gateway Docker Setup

Автономный IBKR Gateway для paper/live trading.

## Быстрый старт

### 1. Скопировать файлы на VPS

```bash
# На локальной машине
scp -r deploy/ib-gateway root@65.108.83.67:/root/
```

### 2. Настроить credentials

```bash
# На VPS
cd /root/ib-gateway
cp .env.example .env
nano .env
```

Заполнить:
- `TWS_USERID` — логин IBKR
- `TWS_PASSWORD` — пароль IBKR
- `VNC_PASSWORD` — пароль для VNC доступа

### 3. Запустить Gateway

```bash
docker-compose up -d
```

### 4. Первый вход через VNC

Gateway требует ручного ввода 2FA кода при первом запуске.

```bash
# На Mac — открыть VNC
open vnc://65.108.83.67:5900

# Или использовать любой VNC клиент
# Host: 65.108.83.67
# Port: 5900
# Password: ваш VNC_PASSWORD
```

В VNC окне:
1. Дождаться загрузки IB Gateway
2. Ввести 2FA код из IBKR app
3. Убедиться что Gateway подключился (зелёный статус)

### 5. Проверить подключение

```bash
# На VPS
nc -zv localhost 4001
# Должно показать: Connection to localhost 4001 port [tcp/*] succeeded!
```

## Порты

| Порт | Описание |
|------|----------|
| 4001 | Paper Trading API |
| 4002 | Live Trading API |
| 5900 | VNC (для настройки) |

## Управление

```bash
# Логи
docker logs -f ib-gateway

# Перезапуск
docker-compose restart

# Остановка
docker-compose down

# Полная очистка (включая сохранённую сессию)
docker-compose down -v
```

## Настройка бота

После успешного запуска Gateway, обновить переменные бота:

```bash
# /root/ibkr-trading-bot/.env
EXECUTION_ENABLED=1
EXECUTION_DRY_RUN=0
IBKR_ENABLED=1
IBKR_HOST=127.0.0.1
IBKR_PORT=4001
IBKR_CLIENT_ID=1
```

Перезапустить бота:
```bash
cd /root/ibkr-trading-bot
docker-compose restart
```

## Troubleshooting

### Gateway не запускается
```bash
# Проверить логи
docker logs ib-gateway

# Убедиться что порты свободны
netstat -tlnp | grep -E '4001|4002|5900'
```

### 2FA код не принимается
1. Убедиться что время на VPS синхронизировано: `timedatectl`
2. Попробовать новый код сразу после его появления в app

### Connection refused на порту 4001
Gateway ещё не готов. Подождать 1-2 минуты после запуска.

### Сессия истекла
Gateway автоматически поддерживает сессию, но IBKR может разлогинить через 24-48 часов.
Решение: подключиться через VNC и заново ввести 2FA.

## Безопасность

⚠️ **Важно:**
- Файл `.env` содержит credentials — не коммитить в git
- VNC порт 5900 открыт — использовать сильный пароль
- Для production рекомендуется закрыть VNC через firewall после настройки:
  ```bash
  ufw deny 5900
  ```
