# Phase 7 Production Integration - Complete

## Summary

Все модули v2.0 полностью интегрированы в production pipeline.

---

## Обновлённые файлы

### Market Data
| Файл | Изменения |
|------|-----------|
| `app/market_data/service.py` | +`get_ohlc()`, `get_atr_history()`, `get_24h_prices()`, `get_close_prices()`, `get_market_data_for_context()` |

### Execution
| Файл | Изменения |
|------|-----------|
| `app/execution/service.py` | +`record_agent_data_for_trade()`, `on_trade_closed()`, `save_performance_state()`, `load_performance_state()` |

### Storage
| Файл | Изменения |
|------|-----------|
| `app/storage/performance_tracker_repo.py` | NEW - persistence для AgentPerformanceTracker |
| `app/storage/repositories.py` | +`performance_tracker` в `make_repos()` |

---

## Использование

### 1. MarketDataService - получение данных для Context

```python
from app.market_data.service import MarketDataService

# Получить все данные для контекста одним вызовом
market_data = market_data_service.get_market_data_for_context("EURUSD")

# market_data содержит:
# {
#   "ohlc": {"opens": [...], "highs": [...], "lows": [...], "closes": [...]},
#   "atr_history": [0.005, 0.0052, ...],
#   "close_prices": [1.0850, 1.0855, ...],
#   "current_prices": {"EURUSD": 1.0850, "GBPUSD": 1.2650, ...},
#   "previous_24h_prices": {"EURUSD": 1.0800, ...}
# }

# Или по отдельности:
ohlc = market_data_service.get_ohlc("EURUSD", n_bars=50)
atr_history = market_data_service.get_atr_history("EURUSD", n_periods=20)
current_prices = market_data_service.get_current_prices()
prices_24h = market_data_service.get_24h_prices()
```

### 2. ExecutionService - запись результатов сделок

```python
from app.execution.service import ExecutionService

# Создание с performance tracker
exec_service = ExecutionService(
    risk_events_repo=repos["risk_events"],
    trades_history_repo=repos["trades_history"],
)

# Выполнение сделки с agent data
result = exec_service.execute(
    decision=decision,
    verdict=verdict,
    settings=settings,
    agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
    agent_confidences={"TechnicalAgent": 0.75, "RiskAgent": 0.6},
    final_signal="LONG",
)

# При закрытии сделки (SL/TP hit или manual)
weight_changes = exec_service.on_trade_closed(
    trade_id="123",
    symbol="EURUSD",
    direction="BUY",
    entry_price=1.0850,
    exit_price=1.0900,
    pnl=50.0,
)
# weight_changes = {"TechnicalAgent": +0.01, "RiskAgent": -0.005}
```

### 3. Persistence - сохранение/загрузка state

```python
from app.storage.performance_tracker_repo import PerformanceTrackerRepo

# Сохранение при shutdown
state = exec_service.save_performance_state()
if state:
    repos["performance_tracker"].save_state(state, key="default")

# Загрузка при startup
state = repos["performance_tracker"].load_state(key="default")
if state:
    exec_service.load_performance_state(state)

# Или напрямую через repo
repos["performance_tracker"].save_state({
    "weights": {"TechnicalAgent": 0.27, ...},
    "outcomes": [...],
    "agent_stats": {...}
})
```

---

## SQL для таблицы performance_tracker_state

Выполнить в Supabase SQL Editor:

```sql
CREATE TABLE IF NOT EXISTS performance_tracker_state (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    key TEXT UNIQUE NOT NULL DEFAULT 'default',
    state_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_trades INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS idx_performance_tracker_key 
    ON performance_tracker_state(key);

-- Optional: RLS policies
ALTER TABLE performance_tracker_state ENABLE ROW LEVEL SECURITY;
```

---

## Полный flow интеграции

```
┌─────────────────────────────────────────────────────────────────┐
│                        STARTUP                                   │
├─────────────────────────────────────────────────────────────────┤
│ 1. Load performance_tracker_state from DB                        │
│    state = repos["performance_tracker"].load_state()             │
│    exec_service.load_performance_state(state)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     TRADING LOOP                                 │
├─────────────────────────────────────────────────────────────────┤
│ 1. MarketDataService.process() → bars cached                     │
│                                                                  │
│ 2. For each signal:                                              │
│    market_data = service.get_market_data_for_context(symbol)     │
│                                                                  │
│ 3. ContextBuilder.build(                                         │
│        ohlc_data=market_data["ohlc"],                            │
│        atr_history=market_data["atr_history"],                   │
│        current_prices=market_data["current_prices"],             │
│        previous_24h_prices=market_data["previous_24h_prices"],   │
│    )                                                             │
│                                                                  │
│ 4. Agents produce signals (with integrated modules)              │
│                                                                  │
│ 5. ScoreAggregator.aggregate(signals)                            │
│    → Uses dynamic weights from tracker                           │
│                                                                  │
│ 6. ExecutionService.execute(                                     │
│        agent_votes=result.agent_signals,                         │
│        agent_confidences=result.agent_confidences,               │
│        final_signal=result.llm_signal,                           │
│    )                                                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TRADE CLOSE                                   │
├─────────────────────────────────────────────────────────────────┤
│ When SL/TP hit or manual close:                                  │
│                                                                  │
│ exec_service.on_trade_closed(                                    │
│     trade_id, symbol, direction, entry, exit, pnl                │
│ )                                                                │
│ → Updates weights in tracker                                     │
│ → Returns weight changes                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SHUTDOWN                                   │
├─────────────────────────────────────────────────────────────────┤
│ state = exec_service.save_performance_state()                    │
│ repos["performance_tracker"].save_state(state)                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Checklist

### Production Ready ✅
- [x] MarketDataService.get_ohlc()
- [x] MarketDataService.get_atr_history()
- [x] MarketDataService.get_24h_prices()
- [x] MarketDataService.get_close_prices()
- [x] MarketDataService.get_market_data_for_context()
- [x] ExecutionService.on_trade_closed()
- [x] ExecutionService.record_agent_data_for_trade()
- [x] ExecutionService.save_performance_state()
- [x] ExecutionService.load_performance_state()
- [x] PerformanceTrackerRepo.save_state()
- [x] PerformanceTrackerRepo.load_state()

### Tests
- [x] 43 unit tests (test_github_modules.py)
- [x] 15 integration tests (test_pipeline_integration.py)

### Documentation
- [x] docs/PHASE7_INTEGRATION.md
- [x] docs/MODULES_V2_CHANGELOG.md
- [x] docs/PRODUCTION_INTEGRATION.md (this file)

---

## Metrics Available

После интеграции доступны метрики:

| Метрика | Источник | Как получить |
|---------|----------|--------------|
| Agent weights | PerformanceTracker | `tracker.get_adjusted_weights()` |
| Agent accuracy | PerformanceTracker | `tracker.get_agent_metrics(name)` |
| Trades recorded | PerformanceTracker | `len(tracker.outcomes)` |
| Weight changes | ExecutionService | Return value of `on_trade_closed()` |
| Dynamic weights active | ScoreAggregator | `result.weights_from_tracker` |
