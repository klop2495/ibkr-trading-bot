# IBKR Trading Bot — Контекст для продолжения
**Дата:** 2025-12-20

---

## 🎯 Текущее состояние проекта

### Система ГОТОВА к деплою

| Компонент | Статус | Детали |
|-----------|--------|--------|
| Backend (Python) | ✅ READY | 188 тестов проходят |
| Frontend (Next.js) | ✅ READY | 49 тестов проходят |
| CI/CD | ✅ Настроен | GitHub Actions |
| Execution System | ✅ READY | DRY_RUN режим активен |
| База данных | ✅ Работает | 946 verdicts, 0 approved |

---

## 🔥 НЕМЕДЛЕННЫЕ ЗАДАЧИ

### 1. SSH доступ к VPS (БЛОКЕР)
**Проблема:** Не знаем пароль root для Hetzner VPS

**VPS данные:**
- Имя: `ubuntu-4gb-hel1-2`
- IP: `65.108.83.67`
- Локация: Helsinki (eu-central)
- План: CX23, 40GB
- Статус: Running (зелёный)

**Решение:**
1. В Hetzner Console → кликнуть на сервер → найти "Rescue" или "Reset root password"
2. Или проверить email от Hetzner с начальным паролем

### 2. После получения доступа — деплой бота
```bash
# На VPS:
apt update && apt install -y docker.io docker-compose
# Клонировать репо и запустить
```

---

## 🔐 КРИТИЧЕСКАЯ БЕЗОПАСНОСТЬ

### Скомпрометированные креденшлы (требуют ротации)
- `SUPABASE_SERVICE_ROLE_KEY` — был виден в логах

**После деплоя ОБЯЗАТЕЛЬНО:**
1. Supabase Dashboard → Settings → API → Regenerate service_role key
2. Обновить `.env` на VPS
3. Перезапустить бота

---

## 📁 Структура проекта

### Backend: `/Users/olegnikishin/ibkr-trading-bot`
```
app/
├── main.py              # Control Plane (точка входа)
├── execution/           # Система исполнения ордеров
│   ├── service.py       # ExecutionService (DRY_RUN/PAPER/LIVE)
│   ├── gates.py         # Проверки перед исполнением
│   ├── runner.py        # Запуск execution loop
│   └── adapters/        # IBKR и Null адаптеры
├── broker/              # IBKR клиент
│   ├── connection_manager.py
│   ├── ibkr_client.py
│   └── oms.py           # Order Management System
├── risk/                # Risk Engine V1
├── signals/             # Signal Engine V1
├── agents/              # AI агенты для анализа
└── storage/             # Supabase репозитории
```

### Frontend: `/Users/olegnikishin/ibkr-trading-fronend`
- Next.js 14 + Supabase Auth
- Dashboard для мониторинга

---

## ⚙️ Ключевые переменные окружения

```bash
# .env файл
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...  # ТРЕБУЕТ РОТАЦИИ!
BOT_OWNER_USER_ID=fb7e03c2-aef5-4215-acd0-47902df9c721
BOT_MODE=paper          # paper | live
TRADING_ENABLED=false   # true для реальной торговли
EXECUTION_MODE=DRY_RUN  # DISABLED | DRY_RUN | PAPER | LIVE

# Загрузка env:
set -a && source .env && set +a
```

---

## 🐛 Исправленные баги (эта сессия)

### 1. owner_uuid_str initialization order
**Файл:** `app/main.py` (строка 862)
```python
# БЫЛО: owner_uuid_str использовался до инициализации
# СТАЛО: инициализация перенесена выше
```

### 2. risk_verdicts schema mismatch
**Файл:** `app/main.py` (строки 684, 761)
```python
# БЫЛО: warnings=...
# СТАЛО: flags=...
```

---

## 📊 Состояние базы данных

```sql
-- Проверка (в Supabase SQL Editor):
SELECT 
  (SELECT COUNT(*) FROM decisions) as decisions,
  (SELECT COUNT(*) FROM risk_verdicts) as verdicts,
  (SELECT COUNT(*) FROM risk_verdicts WHERE approved = true) as approved;
```

**Текущие значения:**
- decisions: 946
- verdicts: 946 (паритет, 0 orphans)
- approved: 0 (консервативные настройки)

---

## 🚀 План деплоя на VPS

### Шаг 1: Доступ к серверу
```bash
ssh root@65.108.83.67
# Ввести пароль (после сброса через Hetzner)
```

### Шаг 2: Установка Docker
```bash
apt update && apt install -y docker.io docker-compose
systemctl enable docker
```

### Шаг 3: Клонирование и запуск
```bash
git clone https://github.com/YOUR_REPO/ibkr-trading-bot.git
cd ibkr-trading-bot
# Создать .env с актуальными креденшлами
docker-compose up -d
```

---

## 📋 Роадмап (после деплоя)

| # | Задача | Приоритет |
|---|--------|-----------|
| 1 | ✅ Execution Engine (done) | — |
| 2 | Stop-Loss / Take-Profit | HIGH |
| 3 | Account Equity Sync | HIGH |
| 4 | WebSocket price streaming | MEDIUM |
| 5 | Alerting (Telegram/Email) | MEDIUM |

---

## 📚 Документация в проекте

- `AUDIT_REPORT.md` — Полный аудит backend
- `FRONTEND_AUDIT_REPORT.md` — Аудит frontend
- `CHANGELOG.md` — История изменений
- `OPS.md` — Операционные процедуры
- `docs/spec_v1.md` — Спецификация системы
- `docs/signals_rules_v1.md` — Правила сигналов

---

## 🔗 Транскрипты предыдущих сессий

Полная история работы (8 сессий за день):
```
/mnt/transcripts/
├── 2025-12-20-13-54-25-ibkr-trading-bot-audit.txt      # Начальный аудит
├── 2025-12-20-14-13-56-ibkr-bot-audit-remediation.txt  # Исправления
├── 2025-12-20-14-48-58-ibkr-execution-implementation.txt
├── 2025-12-20-15-07-04-backfill-idle-optimization.txt
├── 2025-12-20-19-34-51-execution-integration-complete.txt
├── 2025-12-20-19-35-37-execution-activation-hetzner.txt
├── 2025-12-20-19-36-33-hetzner-vps-deployment-ready.txt
└── 2025-12-20-19-36-56-ssh-password-reset-hetzner.txt
```

---

## ⚡ Быстрый старт для нового чата

```
Привет! Продолжаем работу над IBKR Trading Bot.

Текущая задача: деплой на VPS 65.108.83.67
Блокер: нужен пароль root (сбросить через Hetzner Console)

После SSH доступа:
1. Установить Docker
2. Задеплоить бота
3. Ротировать SUPABASE_SERVICE_ROLE_KEY

Execution система готова в DRY_RUN режиме.
Тесты: 237 passing (188 backend + 49 frontend).
```

---

## 🛠️ MCP интеграция

Claude Desktop имеет доступ через MCP к:
- `/Users/olegnikishin/ibkr-trading-bot` (filesystem)
- `/Users/olegnikishin/ibkr-trading-fronend` (filesystem)
- Render.com (для деплоя)

Конфиг: `claude_desktop_config.json` с полными путями через nvm.
