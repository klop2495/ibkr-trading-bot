# IBKR Gateway API Connection Guide

## Обзор архитектуры подключения

Торговый бот подключается к Interactive Brokers через **IB Gateway** — headless версию TWS (Trader Workstation), которая работает на VPS и предоставляет API доступ к торговым функциям.

## Стандартные порты IBKR

| Порт | Режим | Описание |
|------|-------|----------|
| **4001** | TWS Live | Реальная торговля через TWS |
| **4002** | TWS Paper | Бумажная торговля через TWS |
| **4003** | Gateway Live | Реальная торговля через IB Gateway |
| **4004** | Gateway Paper | Бумажная торговля через IB Gateway |

## Почему используется порт 4004

В нашей конфигурации используется **порт 4004** потому что:

1. **IB Gateway** (не TWS) — headless режим для серверов без GUI
2. **Paper Trading** — безопасное тестирование без риска реальных денег
3. **Стандартный порт** для IB Gateway Paper Trading

## Конфигурация IB Gateway

### 1. Настройка через VNC

IB Gateway запускается в Docker контейнере с VNC для удалённого доступа к GUI настройкам.

**Доступ к VNC:**
```
Host: 65.108.83.67
Port: 5900
Password: (установленный при настройке)
```

### 2. Критические настройки в IB Gateway

В IB Gateway GUI (Configure → Settings → API):

#### API Settings
- ✅ **Enable ActiveX and Socket Clients** — включить
- ❌ **Allow connections from localhost only** — ВЫКЛЮЧИТЬ (важно для Docker!)
- ✅ **Socket port**: 4004
- ✅ **Master API client ID**: оставить пустым или установить

#### Важно!
Опция "Allow connections from localhost only" должна быть **ВЫКЛЮЧЕНА**, иначе контейнер trading-bot не сможет подключиться к IB Gateway даже через Docker network.

### 3. Проверка настроек портов

В IB Gateway проверьте:
- **Configure → Settings → API → Socket Port** = 4004
- Порт должен совпадать с `IBKR_PORT` в .env файле бота

## Docker Network Architecture

### Проблема: Изолированные сети

По умолчанию каждый docker-compose создаёт свою сеть:
```
ib-gateway_default      ← IB Gateway контейнер
ibkr-trading-bot_default ← Trading Bot контейнер
```

Контейнеры в разных сетях **не могут** общаться через `localhost` или `127.0.0.1`.

### Решение: Общая сеть

Trading bot должен быть в той же сети, что и IB Gateway:

**docker-compose.yml (trading-bot):**
```yaml
version: '3.8'

services:
  trading-bot:
    build: .
    container_name: ibkr-trading-bot
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - PYTHONUNBUFFERED=1
    networks:
      - default
      - ib-gateway_default  # Подключение к сети IB Gateway
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

networks:
  ib-gateway_default:
    external: true  # Использовать существующую сеть
```

### Переменные окружения (.env)

```bash
# ВАЖНО: использовать имя контейнера, не IP
IB_GATEWAY_HOST=ib-gateway
IB_GATEWAY_PORT=4004
IBKR_HOST=ib-gateway
IBKR_PORT=4004
IBKR_CLIENT_ID=10
```

**Примечание:** Используйте имя контейнера `ib-gateway` вместо `127.0.0.1`, так как Docker разрешает имена контейнеров в IP внутри общей сети.

## Проверка подключения

### 1. Проверка сетей контейнеров

```bash
docker ps --format "{{.Names}}: {{.Networks}}"
```

Ожидаемый результат:
```
ibkr-trading-bot: ibkr-trading-bot_default,ib-gateway_default
ib-gateway: ib-gateway_default
```

### 2. Проверка доступности порта

```bash
docker exec ibkr-trading-bot python -c "
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = sock.connect_ex(('ib-gateway', 4004))
print('ib-gateway:4004:', 'OPEN' if result == 0 else 'CLOSED')
sock.close()
"
```

### 3. Проверка в логах бота

```bash
docker-compose logs --tail 50 | grep -E "ibkr|gateway|connect"
```

Успешное подключение:
```
ibkr_fetch symbol=EURUSD tf=M15 duration=4 D bars=300
market_data symbol=EURUSD tf=M15 bars=205 warmup_min=200
```

### 4. Проверка в IB Gateway UI

В окне IB Gateway должно показывать:
- **API Client**: 1 connected (или более)
- Вкладки Client 10, Client 20 и т.д. появляются при подключении

## Частые проблемы и решения

### Проблема 1: "Connectivity lost" ошибки

```
Error 1100: Connectivity between IBKR and Trader Workstation has been lost
```

**Причина:** IB Gateway потерял связь с серверами IBKR (интернет, maintenance)

**Решение:** Обычно восстанавливается автоматически. Проверьте:
```bash
docker logs ib-gateway --tail 20
```

### Проблема 2: "No security definition"

```
Error 200: No security definition has been found for the request
```

**Причина:** Неправильный формат символа или недоступный инструмент

**Решение:** Проверьте формат символа (EURUSD, не EUR/USD)

### Проблема 3: Порт закрыт

```
ib-gateway:4004: CLOSED
```

**Причины:**
1. IB Gateway не запущен
2. Контейнеры в разных сетях
3. "localhost only" включен в настройках

**Решение:**
1. `docker restart ib-gateway`
2. Проверить docker-compose.yml на networks
3. Через VNC отключить "localhost only"

### Проблема 4: Client ID конфликт

```
Error 326: Unable to connect as the client id is already in use
```

**Причина:** Другой клиент уже использует этот ID

**Решение:** Изменить `IBKR_CLIENT_ID` в .env на уникальное значение (10, 20, 30...)

## Процедура переподключения

1. **Остановить бот:**
   ```bash
   cd /root/ibkr-trading-bot
   docker-compose down
   ```

2. **Перезапустить IB Gateway (если нужно):**
   ```bash
   cd /root/ib-gateway
   docker-compose restart
   ```

3. **Через VNC проверить:**
   - IB Gateway залогинен
   - API Client показывает "connected"
   - Socket port = 4004

4. **Запустить бот:**
   ```bash
   cd /root/ibkr-trading-bot
   docker-compose up -d
   ```

5. **Проверить логи:**
   ```bash
   docker-compose logs -f | head -50
   ```

## Мониторинг статуса

### IB Gateway Connection Status (через VNC)

| Индикатор | Цвет | Значение |
|-----------|------|----------|
| Interactive Brokers API Server | 🟢 Зелёный | Подключен к IBKR |
| Market Data Farm | 🟢 Зелёный | Данные доступны |
| Historical Data Farm | 🟡 Жёлтый | Частично (нормально для Forex) |
| API Client | 🟢 Зелёный | Бот подключен |

### Логи бота

Успешная работа:
```
Phase 1: Data sources ENABLED mode=REAL
ibkr_fetch symbol=EURUSD tf=M15 duration=4 D bars=300
market_data symbol=EURUSD tf=M15 bars=205 warmup_min=200
signal_gen generated=16 warmup_ready=True errors=0 mode=active
```

## Текущая конфигурация (Production)

**VPS:** 65.108.83.67 (Hetzner)

**IB Gateway:**
- Container: `ib-gateway`
- Network: `ib-gateway_default`
- Port: 4004 (Paper Trading)
- VNC: 5900

**Trading Bot:**
- Container: `ibkr-trading-bot`
- Networks: `ibkr-trading-bot_default`, `ib-gateway_default`
- IBKR_HOST: `ib-gateway`
- IBKR_PORT: 4004
- IBKR_CLIENT_ID: 10 (для market data), 20 (для execution)

## Ссылки

- [IBKR API Documentation](https://interactivebrokers.github.io/tws-api/)
- [IB Gateway User Guide](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php)
- [Docker Networking](https://docs.docker.com/network/)
