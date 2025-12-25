# Обновление модулей v2.0 - Сводка изменений

## Дата: 25 декабря 2025

---

## 1. CandlestickPatternDetector v2.0

**Файл:** `app/market_data/candlestick_patterns.py`

### Добавлено:
- ✅ **ATR-фильтр** — паттерны в низкой волатильности игнорируются (`min_atr_multiplier=0.3`)
- ✅ **Trend context** — паттерны валидируются относительно предшествующего тренда
- ✅ **Configurable thresholds** — все пороги вынесены в `PatternConfig`
- ✅ **Выравнивание с TA-Lib** — пороги приближены к TA-Lib где документировано

### Пороги (PatternConfig):
```python
doji_body_pct = 0.05       # 5% body = doji (TA-Lib ~5%)
small_body_pct = 0.25      # For hammer/star patterns
large_body_pct = 0.60      # For engulfing/soldiers
shadow_long_pct = 0.60     # Long shadow threshold
trend_lookback = 5         # Candles for trend detection
```

### Использование:
```python
from app.market_data.candlestick_patterns import CandlestickPatternDetector

detector = CandlestickPatternDetector()
patterns = detector.detect_all(opens, highs, lows, closes, atr=current_atr)
ctx = detector.format_for_context(patterns)

# ctx = {
#   "candle_pattern": "BULLISH",
#   "candle_pattern_name": "Hammer", 
#   "candle_pattern_bias": 0.75,
#   "candle_patterns_detected": ["Hammer", "Doji"],
#   "candle_pattern_strength": 75
# }
```

---

## 2. CurrencyStrengthMeter v2.0

**Файл:** `app/market_data/currency_strength.py`

### Добавлено:
- ✅ **Multi-Timeframe (MTF)** — агрегация с весами по таймфреймам
- ✅ **EMA smoothing** — сглаживание для уменьшения шума
- ✅ **Z-score нормализация** — стандартные отклонения от среднего
- ✅ **Momentum tracking** — скорость изменения силы

### Конфигурация (StrengthConfig):
```python
timeframe_weights = {"M15": 0.15, "H1": 0.35, "H4": 0.35, "D1": 0.15}
ema_period = 5
zscore_lookback = 20
strong_threshold = 50.0    # > 50 = STRONG_BULLISH
weak_threshold = 20.0      # > 20 = BULLISH
```

### Использование:
```python
from app.market_data.currency_strength import CurrencyStrengthMeter, TimeframeData

meter = CurrencyStrengthMeter()

# Single timeframe
strengths = meter.calculate(current_prices, previous_prices)

# Multi-timeframe (рекомендуется)
strengths = meter.calculate_mtf({
    "H1": TimeframeData(current_h1, prev_h1, weight=0.4),
    "H4": TimeframeData(current_h4, prev_h4, weight=0.4),
    "D1": TimeframeData(current_d1, prev_d1, weight=0.2),
})

# Анализ пары
analysis = meter.analyze_pair("EURUSD", strengths)
# analysis = {
#   "strength_signal": "BULLISH",
#   "strength_differential": 45.5,
#   "base_zscore": 1.2,
#   ...
# }
```

---

## 3. AgentPerformanceTracker v2.0

**Файл:** `app/agents/performance_tracker.py`

### Добавлено:
- ✅ **Weighted PnL** — большие выигрыши/потери влияют сильнее
- ✅ **Time decay** — экспоненциальное затухание (half-life = 14 дней)
- ✅ **Confidence weighting** — высокая уверенность = больше влияния
- ✅ **Value-added tracking** — отслеживание реальной пользы агента

### Конфигурация (TrackerConfig):
```python
base_learning_rate = 0.02   # 2% adjustment per trade
half_life_days = 14.0       # Time decay half-life
pnl_cap_multiplier = 3.0    # Cap large PnL at 3x average
min_trades_for_adjustment = 10
```

### Формулы:

**Time Decay:**
```
decay = 0.5 ^ (days_ago / half_life)
# day 0: 1.0
# day 14: 0.5
# day 28: 0.25
```

**PnL Weight:**
```
ratio = abs(pnl) / average_pnl
capped_ratio = min(ratio, 3.0)
weight = 0.5 + (capped_ratio / 3.0) * 1.5  # Range: 0.5 - 2.0
```

**Effective Learning Rate:**
```
rate = base_rate * confidence_factor * pnl_weight * time_decay
rate = min(rate, max_learning_rate)
```

### Использование:
```python
from app.agents.performance_tracker import AgentPerformanceTracker, TradeOutcome

tracker = AgentPerformanceTracker()

# После закрытия сделки
outcome = TradeOutcome(
    trade_id="123",
    symbol="EURUSD",
    direction="BUY",
    pnl=100.0,
    agent_votes={"TechnicalAgent": "LONG", "RiskAgent": "HOLD"},
    agent_confidences={"TechnicalAgent": 0.85, "RiskAgent": 0.6},
    ...
)
tracker.record_outcome(outcome)

# Получить веса для агрегатора
weights = tracker.get_adjusted_weights()

# Persistence
data = tracker.to_dict()  # Save
tracker = AgentPerformanceTracker.from_dict(data)  # Load
```

---

## 4. VolatilityRegimeDetector (без изменений)

**Файл:** `app/risk/volatility_regime.py`

Модуль уже был прод-готов. Консервативный подход — безопасен для использования.

---

## 5. VIXFetcher (без изменений)

**Файл:** `app/data_sources/vix_index.py`

Модуль уже был прод-готов. Использует стандартную интерпретацию VIX.

---

## Тесты

**Файл:** `tests/test_github_modules.py`

Покрытие:
- CandlestickPatternDetector: 10 тестов
- CurrencyStrengthMeter: 9 тестов  
- VolatilityRegimeDetector: 7 тестов
- VIXFetcher: 4 теста
- AgentPerformanceTracker: 12 тестов
- Integration: 3 теста

**Запуск:**
```bash
cd /Users/olegnikishin/ibkr-trading-bot
python -m pytest tests/test_github_modules.py -v
```

---

## Следующие шаги для интеграции

### 1. Добавить OHLC в context builder

В `app/agents/llm/context_builder.py`:
```python
def build_context(self, symbol: str, snapshot) -> dict:
    return {
        ...
        "ohlc": {
            "opens": snapshot.opens[-50:],
            "highs": snapshot.highs[-50:],
            "lows": snapshot.lows[-50:],
            "closes": snapshot.closes[-50:],
        },
        "atr_current": snapshot.atr,
        "atr_history": snapshot.atr_history[-20:],
    }
```

### 2. Добавить 24h prices для strength meter

В `app/market_data/service.py`:
```python
async def get_24h_prices(self) -> Dict[str, float]:
    """Get prices from 24 hours ago for all pairs"""
    ...
```

### 3. Интегрировать tracker в execution flow

В `app/execution/service.py`:
```python
async def on_trade_closed(self, trade: Trade):
    outcome = TradeOutcome(
        trade_id=trade.id,
        pnl=trade.pnl,
        agent_votes=trade.agent_votes,
        ...
    )
    self.performance_tracker.record_outcome(outcome)
```

---

## Документация

- `docs/GITHUB_INTEGRATION.md` — инструкции по интеграции
- `docs/MODULES_ANALYSIS.md` — анализ соответствия оригиналам
