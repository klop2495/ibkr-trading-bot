# Интеграция GitHub модулей в IBKR Trading Bot

## Созданные файлы

Все модули добавлены в соответствующие директории проекта:

```
ibkr-trading-bot/
├── app/
│   ├── agents/
│   │   └── performance_tracker.py    ← NEW: Feedback loop для весов агентов
│   │
│   ├── data_sources/
│   │   └── vix_index.py              ← NEW: VIX fetcher для RiskAgent
│   │
│   ├── market_data/
│   │   ├── candlestick_patterns.py   ← NEW: Детекция свечных паттернов
│   │   └── currency_strength.py      ← NEW: Сила валют для CorrelationAgent
│   │
│   └── risk/
│       └── volatility_regime.py      ← NEW: Режим волатильности
```

## Зависимости

Добавить в `requirements.txt`:
```
yfinance>=0.2.0  # Уже есть для DXY, используется для VIX
```

**Примечание:** CandlestickPatternDetector реализован на **чистом Python** без зависимостей.
TA-Lib НЕ требуется!

## Интеграция в существующие агенты

### 1. TechnicalAgent + CandlestickPatterns

В файле `app/agents/llm/technical_agent.py`:

```python
# Добавить импорт
from app.market_data.candlestick_patterns import CandlestickPatternDetector

class TechnicalAgent(BaseLLMAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pattern_detector = CandlestickPatternDetector()
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        technical = context.get("technical", {})
        
        # Получить OHLC данные из context
        ohlc = context.get("ohlc", {})
        
        # Обнаружить паттерны
        pattern_data = {}
        if ohlc:
            patterns = self.pattern_detector.detect_all(
                ohlc.get("opens", []),
                ohlc.get("highs", []),
                ohlc.get("lows", []),
                ohlc.get("closes", [])
            )
            pattern_data = self.pattern_detector.format_for_context(patterns)
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Существующие поля...
                "trend_short": technical.get("trend_short", "NEUTRAL"),
                # ...
                
                # Новые поля от CandlestickPatternDetector
                **pattern_data,
            }
        }
```

### 2. CorrelationAgent + CurrencyStrength

В файле `app/agents/llm/correlation_agent.py`:

```python
from app.market_data.currency_strength import CurrencyStrengthMeter

class CorrelationAgent(BaseLLMAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.strength_meter = CurrencyStrengthMeter()
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        # Существующий код...
        
        # Добавить анализ силы валют
        current_prices = context.get("current_prices", {})
        previous_prices = context.get("previous_24h_prices", {})
        
        strength_data = {}
        if current_prices and previous_prices:
            strengths = self.strength_meter.calculate(current_prices, previous_prices)
            pair_analysis = self.strength_meter.analyze_pair(symbol, strengths)
            strength_data = {
                **self.strength_meter.format_for_context(strengths),
                **pair_analysis
            }
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Существующие поля DXY...
                
                # Новые поля от CurrencyStrengthMeter
                **strength_data,
            }
        }
```

### 3. RiskAgent + VIX + VolatilityRegime

В файле `app/agents/llm/risk_agent.py`:

```python
from app.data_sources.vix_index import VIXFetcher
from app.risk.volatility_regime import VolatilityRegimeDetector

class RiskAgent(BaseLLMAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vix_fetcher = VIXFetcher()
        self.vol_detector = VolatilityRegimeDetector()
    
    def prepare_input(self, context: Dict[str, Any], symbol: str) -> dict:
        risk = context.get("risk", {})
        
        # Получить VIX
        vix_snapshot = self.vix_fetcher.get_snapshot()
        vix_data = self.vix_fetcher.format_for_context(vix_snapshot)
        
        # Получить режим волатильности (если есть ATR данные)
        atr_values = context.get("atr_values", [])
        close_prices = context.get("close_prices", [])
        
        vol_data = {}
        if atr_values and close_prices:
            regime = self.vol_detector.detect(atr_values, close_prices)
            vol_data = self.vol_detector.format_for_context(regime)
        
        return {
            "symbol": symbol,
            "agent": self.name,
            "data": {
                # Существующие поля...
                "volatility_regime": risk.get("volatility_regime", "NORMAL"),
                
                # Новые поля
                **vix_data,
                **vol_data,
            }
        }
```

### 4. Aggregator + PerformanceTracker

В файле `app/agents/llm/aggregator.py`:

```python
from app.agents.performance_tracker import AgentPerformanceTracker, TradeOutcome

class ScoreAggregator:
    def __init__(self):
        self.performance_tracker = AgentPerformanceTracker()
    
    def aggregate(self, signals: List[AgentSignal]) -> dict:
        # Получить динамические веса
        weights = self.performance_tracker.get_adjusted_weights()
        
        # Использовать веса в расчёте score
        # ...
    
    def record_trade_result(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry: float,
        exit: float,
        pnl: float,
        agent_votes: Dict[str, str]
    ):
        """Вызывать после закрытия сделки"""
        outcome = TradeOutcome(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            exit_price=exit,
            pnl=pnl,
            pnl_pips=pnl / 0.0001,  # Примерный расчёт
            agent_votes=agent_votes,
            final_signal=direction
        )
        self.performance_tracker.record_outcome(outcome)
```

## Приоритет внедрения

| # | Модуль | Сложность | Влияние | Статус |
|---|--------|-----------|---------|--------|
| 1 | VIX Fetcher | ⭐ | Medium | ✅ Создан |
| 2 | CandlestickPatterns | ⭐⭐ | Medium | ✅ Создан |
| 3 | VolatilityRegime | ⭐ | Medium | ✅ Создан |
| 4 | CurrencyStrength | ⭐⭐ | Medium | ✅ Создан |
| 5 | PerformanceTracker | ⭐⭐⭐ | High | ✅ Создан |

## Тестирование

```bash
# Запустить тесты для новых модулей
cd /Users/olegnikishin/ibkr-trading-bot
python -m pytest tests/ -v -k "candlestick or currency_strength or vix or volatility"
```

## Следующие шаги

1. **Добавить OHLC данные в context** - MarketDataService должен передавать raw OHLC
2. **Добавить previous_24h_prices** - Для расчёта силы валют
3. **Интегрировать в main.py** - Подключить PerformanceTracker к execution flow
4. **Создать тесты** - Unit tests для каждого модуля
