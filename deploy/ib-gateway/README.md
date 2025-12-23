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
4. **ВАЖНО:** Configure → Settings → API → Settings:
   - Socket port: **4001**
   - "Allow connections from localhost only": **СНЯТЬ ГАЛОЧКУ**
   - Apply → OK

### 5. Проверить подключение

```bash
# На VPS
nc -zv localhost 4001
# Должно показать: Connection to localhost 4001 port [tcp/*] succeeded!
```

## Порты

| Порт | Описание |
|------|----------|
| 4001 | Paper Trading API (стандартный) |
| 4002 | Live Trading API |
| 5900 | VNC (для настройки) |

**ВАЖНО:** Убедись что Socket port в IB Gateway = 4001 (Configure → Settings → API → Settings)

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

После успешного запуска Gateway, бот должен иметь в `.env`:

```bash
# Для network_mode: host (рекомендуется)
IB_GATEWAY_HOST=127.0.0.1
IB_GATEWAY_PORT=4001
IB_CLIENT_ID=10
SIGNAL_GEN_MOCK=0
```

И в `docker-compose.yml`:
```yaml
services:
  trading-bot:
    network_mode: host
```

Перезапустить бота:
```bash
cd /root/ibkr-trading-bot
docker-compose down && docker-compose up -d
```

## Переключение Demo → Live

### Текущий режим: Paper (Demo)

```bash
# IB Gateway .env
TRADING_MODE=paper
```

### Переключение на Live

1. **Остановить бота:**
```bash
cd /root/ibkr-trading-bot && docker compose down
```

2. **Переключить IB Gateway на live:**
```bash
cd /root/ib-gateway
sed -i 's/TRADING_MODE=paper/TRADING_MODE=live/' .env
docker compose down && docker compose up -d
```

3. **VNC:** залогиниться с **live** credentials и подтвердить 2FA

4. **Переключить бота:**
```bash
cd /root/ibkr-trading-bot
sed -i 's/BOT_MODE=paper/BOT_MODE=live/' .env
docker compose up -d
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
1. Gateway ещё не готов — подождать 1-2 минуты после запуска
2. Проверить Socket port в VNC (Configure → Settings → API → Settings)
3. Убедиться что "Allow connections from localhost only" **СНЯТА**

### Бот не видит IB Gateway (mode=MOCK)
1. Проверить что бот использует `network_mode: host` в docker-compose.yml
2. Проверить переменные: `IB_GATEWAY_HOST=127.0.0.1`, `IB_GATEWAY_PORT=4001`
3. Проверить `SIGNAL_GEN_MOCK=0` (не 1!)

### RSI=100, данные не обновляются
Это признак mock mode. Проверить:
1. IB Gateway подключен (зелёный статус в VNC)
2. `SIGNAL_GEN_MOCK=0` в .env бота
3. Socket port в IB Gateway = 4001 (не 4002 или 4004!)

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
