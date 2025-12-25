# Phase 7 Integration Complete

## Summary

Все модули v2.0 интегрированы в основной pipeline.

---

## Обновлённые файлы

### Core Modules (app/market_data/, app/risk/, app/agents/)
| Файл | Изменения |
|------|-----------|
| `candlestick_patterns.py` | v2.0 - ATR фильтр, trend context |
| `currency_strength.py` | v2.0 - MTF, EMA, Z-score |
| `volatility_regime.py` | v1.0 - готов к использованию |
| `vix_index.py` | v1.0 - готов к использованию |
| `performance_tracker.py` | v2.0 - weighted PnL, time decay |

### Pipeline Integration (app/agents/llm/)
| Файл | Изменения |
|------|-----------|
| `context_builder.py` | Phase 7 - добавлены OHLC, ATR history, 24h prices, VIX |
| `technical_agent.py` | Phase 7 - интеграция CandlestickPatternDetector |
| `correlation_agent.py` | Phase 7 - интеграция CurrencyStrengthMeter |
| `risk_agent.py` | Phase 7 - интеграция VIX + VolatilityRegimeDetector |
| `score_aggregator.py` | Phase 7 - интеграция AgentPerformanceTracker |

### Tests
| Файл | Тесты |
|------|-------|
| `test_github_modules.py` | 43 unit tests для модулей |
| `test_pipeline_integration.py` | 15 integration tests для pipeline |

---

## Архитектура интеграции

```
MarketDataService
    │
    ▼
ContextBuilder ─────────────────────────────────────────┐
    │                                                   │
    │ ohlc, atr_history, current_prices, 24h_prices     │
    │                                                   │
    ▼                                                   │
┌─────────────────────────────────────────────────────┐ │
│                    AGENTS                           │ │
│                                                     │ │
│  TechnicalAgent ◄── CandlestickPatternDetector      │ │
│       │                                             │ │
│  CorrelationAgent ◄── CurrencyStrengthMeter         │ │
│       │                                             │ │
│  RiskAgent ◄── VIXFetcher + VolatilityRegimeDetector│ │
│       │                                             │ │
│  MacroAgent                                         │ │
│       │                                             │ │
│  SentimentAgent                                     │ │
│                                                     │ │
└─────────────────────────────────────────────────────┘
    │
    ▼ AgentSignal[]
ScoreAggregator ◄── AgentPerformanceTracker (dynamic weights)
    │
    ▼ LLMContourResult
HybridDecisionEngine (60% Rules + 40% LLM)
    │
    ▼
ExecutionService
    │
    ▼ on_trade_closed
PerformanceTracker.record_outcome() ◄── feedback loop
```

---

## Использование в коде

### 1. Подготовка Context с OHLC данными

```python
from app.agents.llm.context_builder import ContextBuilder

builder = ContextBuilder(
    dxy_fetcher=dxy_fetcher,
    vix_fetcher=vix_fetcher,  # Phase 7
)

# Получить OHLC из MarketDataService
ohlc_data = {
    "opens": [...],
    "highs": [...],
    "lows": [...],
    "closes": [...],
}

# Получить 24h prices для strength meter
current_prices = market_data_service.get_current_prices()
previous_24h = market_data_service.get_24h_prices()

ctx = builder.build(
    symbol="EURUSD",
    signal_preview=preview,
    market_snapshot=snapshot,
    ohlc_data=ohlc_data,          # Phase 7
    atr_history=atr_values[-20:],  # Phase 7
    current_prices=current_prices,  # Phase 7
    previous_24h_prices=previous_24h,  # Phase 7
)
```

### 2. TechnicalAgent автоматически детектит паттерны

```python
from app.agents.llm.technical_agent import TechnicalAgent

agent = TechnicalAgent()

# prepare_input автоматически вызывает CandlestickPatternDetector
input_data = agent.prepare_input(ctx.to_dict(), "EURUSD")

# input_data["data"] содержит:
# - candle_pattern: "BULLISH" | "BEARISH" | "NONE"
# - candle_pattern_name: "Hammer" | "Engulfing" | etc.
# - candle_pattern_bias: -1.0 to +1.0
# - candle_patterns_detected: ["Hammer", "Doji"]
```

### 3. CorrelationAgent анализирует силу валют

```python
from app.agents.llm.correlation_agent import CorrelationAgent

agent = CorrelationAgent()
input_data = agent.prepare_input(ctx.to_dict(), "EURUSD")

# input_data["data"] содержит:
# - currency_strength_available: True/False
# - strength_signal: "BULLISH" | "BEARISH" | "NEUTRAL"
# - strength_differential: -100 to +100
# - base_strength, quote_strength, base_rank, quote_rank
```

### 4. RiskAgent использует VIX и VolatilityRegime

```python
from app.agents.llm.risk_agent import RiskAgent

agent = RiskAgent()
input_data = agent.prepare_input(ctx.to_dict(), "EURUSD")

# input_data["data"] содержит:
# - vix_value: 18.5
# - vix_regime: "GREED" | "FEAR" | "EXTREME_FEAR" | "EXTREME_GREED"
# - vix_elevated: True/False
# - volatility_regime: "LOW" | "NORMAL" | "HIGH" | "EXTREME"
# - volatility_position_multiplier: 0.3 to 1.2
```

### 5. ScoreAggregator с динамическими весами

```python
from app.agents.llm.score_aggregator import ScoreAggregator

aggregator = ScoreAggregator()

# Aggregation использует веса из PerformanceTracker если доступны
result = aggregator.aggregate(signals)

# result содержит:
# - agent_weights: {"TechnicalAgent": 0.27, ...}  # Актуальные веса
# - weights_from_tracker: True/False

# После закрытия сделки - записать результат
aggregator.record_trade_outcome(
    trade_id="123",
    symbol="EURUSD",
    direction="BUY",
    entry_price=1.0850,
    exit_price=1.0900,
    pnl=50.0,
    pnl_pips=50,
    agent_votes=result.agent_signals,
    agent_confidences=result.agent_confidences,
    final_signal=result.llm_signal,
)

# Веса автоматически корректируются для следующих сделок
```

---

## Что осталось

### Для полной production-ready интеграции:

1. **MarketDataService** — добавить методы:
   - `get_ohlc(symbol, n_bars)` → Dict[str, List[float]]
   - `get_24h_prices()` → Dict[str, float]
   - `get_atr_history(symbol, n_periods)` → List[float]

2. **ExecutionService** — добавить вызов:
   ```python
   def on_trade_closed(self, trade: Trade):
       aggregator.record_trade_outcome(...)
   ```

3. **Persistence** — сохранение PerformanceTracker state:
   ```python
   # Save
   data = tracker.to_dict()
   save_to_db(data)
   
   # Load
   data = load_from_db()
   tracker = AgentPerformanceTracker.from_dict(data)
   ```

---

## Тестирование

```bash
# Unit tests для модулей
pytest tests/test_github_modules.py -v

# Integration tests для pipeline
pytest tests/test_pipeline_integration.py -v

# Все тесты
pytest tests/ -v --tb=short
```

---

## Metrics & Monitoring

После интеграции доступны новые метрики:

| Метрика | Источник | Описание |
|---------|----------|----------|
| `candle_pattern_detected` | TechnicalAgent | Паттерн на последней свече |
| `strength_differential` | CorrelationAgent | Разница силы base/quote |
| `vix_value` | RiskAgent | Текущий VIX |
| `volatility_regime` | RiskAgent | LOW/NORMAL/HIGH/EXTREME |
| `weights_from_tracker` | Aggregator | Используются ли динамические веса |
| `agent_accuracy` | PerformanceTracker | Точность каждого агента |

---

## Changelog

### v2.1 (Phase 7) - December 2025
- ✅ CandlestickPatternDetector интегрирован в TechnicalAgent
- ✅ CurrencyStrengthMeter интегрирован в CorrelationAgent
- ✅ VIXFetcher + VolatilityRegimeDetector интегрированы в RiskAgent
- ✅ AgentPerformanceTracker интегрирован в ScoreAggregator
- ✅ ContextBuilder расширен для OHLC, ATR, 24h prices
- ✅ 15 интеграционных тестов добавлены
