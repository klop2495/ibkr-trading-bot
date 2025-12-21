# IBKR Trading Bot v2.1 — Parallel AI Agents

## 🎯 Концепция

**IBKR Trading Bot v2.1** — гибридная торговая система, объединяющая детерминированный rules-based контур с LLM-агентами для принятия торговых решений на рынке Forex.

### Философия проекта

> "Сначала докажи, потом доверяй"

Вместо полной замены rules-based системы на AI, мы запускаем **параллельное тестирование**:
- Rules Engine продолжает работать (текущая логика)
- GPT Agents работают параллельно (новая логика)
- Оба решения логируются и сравниваются
- Paper Trading исполняет выбранную стратегию
- Статистика показывает что лучше

### Ключевые принципы

1. **Shadow mode first** — сначала логируем, потом торгуем
2. **Не ломаем работающее** — текущий execution pipeline остаётся
3. **Data-driven решения** — выбор стратегии на основе статистики
4. **LLM не делает математику** — только HOLD/LONG/SHORT + enum confidence
5. **Budget control** — лимиты на API вызовы и стоимость
6. **Source health** — явные метрики качества данных

---

## 🛡️ Безопасность LLM (КРИТИЧНО)

### Что LLM МОЖЕТ делать

| Разрешено | Пример |
|-----------|--------|
| Направление сделки | `"signal": "LONG"` |
| Уровень уверенности (enum) | `"confidence": "HIGH"` |
| Флаги и причины | `"flags": ["TREND_STRONG"]` |
| Текстовое обоснование | `"reasoning": "Trend aligned across TFs"` |

### Что LLM НИКОГДА не может делать

| Запрещено | Почему |
|-----------|--------|
| Цены входа/выхода | `1.0732` — это торговая математика |
| SL/TP значения | Определяются Risk Engine |
| Размер позиции | Определяется Position Sizer |
| Pip values | Любые числовые расчёты |
| Confidence как float | Заменено на enum LOW/MED/HIGH |

### Response Validator

```python
class AgentResponseValidator:
    """Блокирует ответы LLM с запрещённым контентом"""
    
    FORBIDDEN_PATTERNS = [
        r"\d+\.\d{4,}",           # Цены: 1.0732, 145.6789
        r"SL|TP|stop.?loss|take.?profit",
        r"\d+\s*(lot|unit|position)",
        r"\d+\s*pip",
        r"enter\s+at|exit\s+at",
    ]
    
    def validate(self, response: dict) -> bool:
        reasoning = response.get("reasoning", "")
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, reasoning, re.IGNORECASE):
                logger.warning(f"LLM violated rules: {pattern}")
                return False
        return True
    
    def sanitize(self, response: dict) -> AgentSignal:
        """Возвращает HOLD если ответ невалидный"""
        if not self.validate(response):
            return AgentSignal(
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning="Response contained forbidden content",
                flags=["LLM_SANITIZED"],
            )
        return self._parse_valid_response(response)
```

---

## 🏗 Архитектура v2.1

### Высокоуровневая схема

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  IB Gateway  │  │  Economic    │  │    COT       │  │    DXY       │    │
│  │  OHLCV+MTF   │  │  Calendar    │  │  Reports     │  │   Index      │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │                 │             │
│         ▼                 ▼                 ▼                 ▼             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      SOURCE HEALTH MONITOR                          │   │
│  │  • is_available: bool                                               │   │
│  │  • staleness_minutes: float                                         │   │
│  │  • coverage: float (0.0-1.0)                                        │   │
│  │  • IF stale → agents return HOLD + DATA_STALE flag                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     UnifiedDataContext                              │   │
│  │  • market_snapshots                                                 │   │
│  │  • economic_events                                                  │   │
│  │  • cot_data                                                         │   │
│  │  • dxy_data                                                         │   │
│  │  • source_health: Dict[str, SourceHealth]  ← NEW                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DECISION LAYER                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      SAFETY GATES (NEW)                             │   │
│  │                                                                     │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │   │
│  │  │   Budget    │  │   Agent     │  │  Response   │                 │   │
│  │  │   Limiter   │  │   Cache     │  │  Validator  │                 │   │
│  │  │             │  │             │  │             │                 │   │
│  │  │ max 30/min  │  │ 15 min TTL  │  │ no prices   │                 │   │
│  │  │ max $5/day  │  │ by symbol   │  │ no SL/TP    │                 │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│            ┌───────────────────────┼───────────────────────┐                │
│            │                       │                       │                │
│            ▼                       ▼                       ▼                │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐        │
│   │  RULES ENGINE   │    │   GPT AGENTS    │    │     HYBRID      │        │
│   │   (существует)  │    │    (NEW)        │    │    (NEW)        │        │
│   │                 │    │                 │    │                 │        │
│   │ • SMA Crossover │    │ • Technical 30% │    │ Rules × 0.4     │        │
│   │ • RSI Confirm   │    │ • Macro     25% │    │ GPT   × 0.6     │        │
│   │ • Spread Gate   │    │ • Sentiment 20% │    │                 │        │
│   │ • Quality Gate  │    │ • Correlation15%│    │ Blended score   │        │
│   │                 │    │ • Risk      10% │    │                 │        │
│   └────────┬────────┘    └────────┬────────┘    └────────┬────────┘        │
│            │                      │                      │                  │
│            ▼                      ▼                      ▼                  │
│      decision_rules         decision_gpt          decision_hybrid          │
│            │                      │                      │                  │
│            └──────────────────────┴──────────────────────┘                  │
│                                   │                                         │
│                                   ▼                                         │
│                        ┌─────────────────────┐                              │
│                        │  parallel_decisions │                              │
│                        │     (Supabase)      │                              │
│                        └─────────────────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔒 Safety Gates (НОВОЕ)

### 1. Budget Limiter

Контролирует расходы на OpenAI API.

```python
@dataclass
class BudgetLimiter:
    """Лимитирует вызовы API по количеству и стоимости"""
    
    max_calls_per_minute: int = 30
    max_cost_per_day: float = 5.0
    
    calls_this_minute: int = 0
    cost_today: float = 0.0
    minute_start: datetime = field(default_factory=datetime.now)
    day_start: date = field(default_factory=date.today)
    
    def can_call(self) -> Tuple[bool, str]:
        self._reset_if_needed()
        
        if self.calls_this_minute >= self.max_calls_per_minute:
            return False, "RATE_LIMIT"
        if self.cost_today >= self.max_cost_per_day:
            return False, "BUDGET_LIMIT"
        return True, "OK"
    
    def record_call(self, cost: float = 0.002):  # ~$0.002 per call
        self.calls_this_minute += 1
        self.cost_today += cost
    
    def get_fallback_signal(self, reason: str) -> AgentSignal:
        return AgentSignal(
            signal="HOLD",
            confidence=ConfidenceLevel.LOW,
            reasoning=f"Budget gate: {reason}",
            flags=[f"BUDGET_{reason}"],
        )
```

**Конфигурация:**
```bash
AGENTS_MAX_CALLS_PER_MINUTE=30
AGENTS_MAX_COST_PER_DAY=5.0
```

### 2. Agent Cache

Кэширует ответы агентов для экономии API вызовов.

```python
class AgentCache:
    """Кэширует ответы агентов на 15 минут"""
    
    def __init__(self, ttl_minutes: int = 15):
        self.ttl = timedelta(minutes=ttl_minutes)
        self.cache: Dict[str, Tuple[AgentSignal, datetime]] = {}
    
    def get_key(self, agent_name: str, symbol: str) -> str:
        # Bucket по 15-минутным интервалам
        now = datetime.now(timezone.utc)
        bucket = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        return f"{agent_name}:{symbol}:{bucket.isoformat()}"
    
    def get(self, agent_name: str, symbol: str) -> Optional[AgentSignal]:
        key = self.get_key(agent_name, symbol)
        if key in self.cache:
            signal, cached_at = self.cache[key]
            if datetime.now(timezone.utc) - cached_at < self.ttl:
                signal.flags.append("CACHED")
                return signal
        return None
    
    def set(self, agent_name: str, symbol: str, signal: AgentSignal):
        key = self.get_key(agent_name, symbol)
        self.cache[key] = (signal, datetime.now(timezone.utc))
```

**Эффект:**
- Без кэша: 5 агентов × 16 пар × 6/час = 480 вызовов/час
- С кэшем (15 мин): 5 агентов × 16 пар × 4/час = 320 вызовов/час
- Экономия: ~33%

### 3. Source Health Monitor

Отслеживает качество данных от внешних источников.

```python
@dataclass
class SourceHealth:
    """Метрики здоровья источника данных"""
    
    source_name: str
    is_available: bool
    last_update: datetime
    staleness_minutes: float
    coverage: float  # 0.0 - 1.0 (какой % данных есть)
    error_message: Optional[str] = None
    
    def is_stale(self, max_age_minutes: float = 60) -> bool:
        return self.staleness_minutes > max_age_minutes
    
    def is_healthy(self) -> bool:
        return self.is_available and not self.is_stale() and self.coverage > 0.5


class SourceHealthMonitor:
    """Мониторит все источники данных"""
    
    MAX_STALENESS = {
        "ibkr_ohlcv": 5,        # 5 минут для рыночных данных
        "economic_calendar": 60, # 1 час для календаря
        "cot_reports": 10080,   # 7 дней для COT (еженедельные)
        "dxy_index": 15,        # 15 минут для DXY
    }
    
    def check_all(self, context: UnifiedDataContext) -> Dict[str, SourceHealth]:
        return {
            "ibkr_ohlcv": self._check_ohlcv(context),
            "economic_calendar": self._check_calendar(context),
            "cot_reports": self._check_cot(context),
            "dxy_index": self._check_dxy(context),
        }
    
    def get_stale_sources(self, health: Dict[str, SourceHealth]) -> List[str]:
        return [name for name, h in health.items() if h.is_stale()]
    
    def should_block_trading(self, health: Dict[str, SourceHealth]) -> bool:
        # Блокируем если OHLCV stale (критичный источник)
        return health["ibkr_ohlcv"].is_stale()
```

---

## 🤖 AI Агенты

### Confidence как Enum (ИЗМЕНЕНО)

**Было (опасно):**
```python
confidence: float  # 0.0 - 1.0 от LLM — LLM делает математику
```

**Стало (безопасно):**
```python
class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

# Маппинг в коде, НЕ в LLM
CONFIDENCE_WEIGHTS = {
    ConfidenceLevel.LOW: 0.3,
    ConfidenceLevel.MEDIUM: 0.6,
    ConfidenceLevel.HIGH: 0.9,
}
```

**Промпт для агента:**
```
OUTPUT JSON:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 100 words, NO prices/numbers)"
}

RULES:
- confidence "high": Strong alignment, clear setup
- confidence "medium": Moderate signals, some uncertainty
- confidence "low": Weak or conflicting signals
- NEVER include prices, SL/TP, pip values, or position sizes
```

### Обзор агентов

| Агент | Вес | Фокус | Источники данных |
|-------|-----|-------|------------------|
| **TechnicalAgent** | 30% | Ценовые паттерны, индикаторы | OHLCV, MTF snapshots |
| **MacroAgent** | 25% | Фундаментальный анализ | Economic Calendar |
| **SentimentAgent** | 20% | Позиционирование рынка | COT Reports |
| **CorrelationAgent** | 15% | Межрыночные связи | DXY, кросс-курсы |
| **RiskAgent** | 10% | Оценка рисков | Volatility, Spread |

---

### 1. TechnicalAgent (30%)

**Задача:** Анализ технических индикаторов и ценовых паттернов

**Входные данные:**
```json
{
  "symbol": "EURUSD",
  "timeframes": {
    "M15": {"trend": "UP", "rsi_zone": "NEUTRAL", "ma_alignment": "BULLISH"},
    "H1":  {"trend": "UP", "rsi_zone": "NEUTRAL", "ma_alignment": "BULLISH"},
    "H4":  {"trend": "UP", "rsi_zone": "NEUTRAL", "ma_alignment": "BULLISH"}
  },
  "structure": {
    "higher_highs": true,
    "higher_lows": true,
    "trend_strength": "STRONG"
  }
}
```

**Примечание:** Передаём категории (UP/DOWN/NEUTRAL), не числа.

**Выходные данные:**
```json
{
  "signal": "LONG",
  "confidence": "high",
  "reasoning": "All timeframes show bullish alignment. Price structure confirms uptrend with higher highs and lows. RSI not overbought."
}
```

**Промпт:**
```
You are TechnicalAgent analyzing Forex price action.

INPUTS: Multi-timeframe trend data (M15, H1, H4) with categorical indicators

RULES:
1. Trend alignment across timeframes is strongest signal
2. RSI extremes suggest caution
3. Higher timeframe trend takes precedence
4. NEVER mention specific prices, SL/TP, or pip values

OUTPUT JSON:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 100 words)"
}
```

---

### 2. MacroAgent (25%)

**Задача:** Анализ фундаментальных факторов и экономического календаря

**Входные данные:**
```json
{
  "symbol": "EURUSD",
  "base_currency": "EUR",
  "quote_currency": "USD",
  "upcoming_high_impact": [
    {
      "hours_until": 3.5,
      "currency": "USD",
      "event": "Core PCE Price Index",
      "expected_impact": "HIGH"
    }
  ],
  "recent_surprises": [
    {
      "currency": "EUR",
      "event": "ECB Rate Decision",
      "surprise_direction": "DOVISH"
    }
  ]
}
```

**Выходные данные:**
```json
{
  "signal": "HOLD",
  "confidence": "medium",
  "reasoning": "High impact USD event in 3.5 hours. Recommend avoiding new positions. Recent ECB dovish surprise may weigh on EUR."
}
```

---

### 3. SentimentAgent (20%)

**Задача:** Анализ позиционирования и настроений рынка

**Входные данные:**
```json
{
  "symbol": "EURUSD",
  "cot_positioning": {
    "non_commercial_bias": "LONG",
    "weekly_change": "INCREASING",
    "percentile_52w": "HIGH"
  },
  "retail_sentiment": {
    "crowd_position": "SHORT",
    "extreme_level": false
  }
}
```

**Выходные данные:**
```json
{
  "signal": "LONG",
  "confidence": "medium",
  "reasoning": "Smart money net long and increasing. Retail crowd short - contrarian bullish. Positioning not at extreme yet."
}
```

---

### 4. CorrelationAgent (15%)

**Задача:** Анализ межрыночных корреляций

**Входные данные:**
```json
{
  "symbol": "EURUSD",
  "dxy_trend": "DOWN",
  "dxy_vs_sma": "BELOW",
  "correlated_pairs_alignment": {
    "GBPUSD": "ALIGNED",
    "USDCHF": "ALIGNED"
  },
  "divergences": ["EURUSD lagging GBPUSD move"]
}
```

**Выходные данные:**
```json
{
  "signal": "LONG",
  "confidence": "medium",
  "reasoning": "DXY weakening supports EUR. Correlated pairs confirm USD weakness. EURUSD may catch up to GBPUSD."
}
```

---

### 5. RiskAgent (10%)

**Задача:** Оценка рыночных условий и рисков

**Входные данные:**
```json
{
  "symbol": "EURUSD",
  "volatility_regime": "NORMAL",
  "spread_status": "TIGHT",
  "session": "LONDON_NY_OVERLAP",
  "liquidity": "HIGH",
  "recent_performance": {
    "streak": "NEUTRAL",
    "drawdown_status": "OK"
  }
}
```

**Выходные данные:**
```json
{
  "signal": "LONG",
  "confidence": "high",
  "reasoning": "Optimal conditions. Normal volatility, tight spreads, peak liquidity session. No drawdown concerns."
}
```

---

## ⚙️ Weighted Aggregation

### Формула (ОБНОВЛЕНО)

```python
class WeightedAggregator:
    WEIGHTS = {
        "TechnicalAgent": 0.30,
        "MacroAgent": 0.25,
        "SentimentAgent": 0.20,
        "CorrelationAgent": 0.15,
        "RiskAgent": 0.10,
    }
    
    SIGNAL_VALUES = {
        "LONG": +1.0,
        "SHORT": -1.0,
        "HOLD": 0.0,
    }
    
    # Confidence маппинг в КОДЕ, не от LLM
    CONFIDENCE_WEIGHTS = {
        ConfidenceLevel.LOW: 0.3,
        ConfidenceLevel.MEDIUM: 0.6,
        ConfidenceLevel.HIGH: 0.9,
    }
    
    def aggregate(self, agent_signals: List[AgentSignal]) -> AggregatedDecision:
        weighted_sum = 0.0
        weight_sum = 0.0
        
        for signal in agent_signals:
            signal_val = self.SIGNAL_VALUES[signal.signal]
            agent_weight = self.WEIGHTS[signal.agent_name]
            conf_weight = self.CONFIDENCE_WEIGHTS[signal.confidence]  # enum → float
            
            weighted_sum += signal_val * conf_weight * agent_weight
            weight_sum += conf_weight * agent_weight
        
        score = weighted_sum / weight_sum if weight_sum > 0 else 0.0
        
        # Consensus: минимум 3 из 5 агентов согласны
        long_count = sum(1 for s in agent_signals if s.signal == "LONG")
        short_count = sum(1 for s in agent_signals if s.signal == "SHORT")
        consensus = max(long_count, short_count) >= 3
        
        # Final decision
        if score > 0.20 and consensus and long_count >= 3:
            final = "LONG"
        elif score < -0.20 and consensus and short_count >= 3:
            final = "SHORT"
        else:
            final = "HOLD"
        
        return AggregatedDecision(
            score=score,
            signal=final,
            consensus=consensus,
            consensus_count=max(long_count, short_count),
            agent_signals=agent_signals,
        )
```

### Пример агрегации

```
TechnicalAgent:   LONG,  conf=HIGH   → +1.0 × 0.9 × 0.30 = +0.270
MacroAgent:       HOLD,  conf=MEDIUM → +0.0 × 0.6 × 0.25 =  0.000
SentimentAgent:   LONG,  conf=MEDIUM → +1.0 × 0.6 × 0.20 = +0.120
CorrelationAgent: LONG,  conf=MEDIUM → +1.0 × 0.6 × 0.15 = +0.090
RiskAgent:        LONG,  conf=HIGH   → +1.0 × 0.9 × 0.10 = +0.090

Weighted Sum = 0.270 + 0.000 + 0.120 + 0.090 + 0.090 = 0.570
Weight Sum   = (0.9×0.30) + (0.6×0.25) + (0.6×0.20) + (0.6×0.15) + (0.9×0.10) = 0.600

Score = 0.570 / 0.600 = 0.95

Consensus: 4 LONG, 1 HOLD → ✅ (>= 3)

Final: LONG (score 0.95 > 0.20, consensus OK)
```

---

## 📊 Shadow Mode (Phase 0)

### Концепция

Shadow mode позволяет логировать все решения БЕЗ реальной торговли по GPT.

```
┌─────────────────────────────────────────────────────────────────┐
│                      SHADOW MODE                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Market Data                                                    │
│       │                                                         │
│       ├──────────────────┬──────────────────┐                   │
│       ▼                  ▼                  ▼                   │
│  ┌─────────┐        ┌─────────┐        ┌─────────┐             │
│  │ Rules   │        │   GPT   │        │ Hybrid  │             │
│  │ Engine  │        │ (stubs) │        │         │             │
│  │         │        │  HOLD   │        │         │             │
│  └────┬────┘        └────┬────┘        └────┬────┘             │
│       │                  │                  │                   │
│       ▼                  ▼                  ▼                   │
│  EXECUTED           LOGGED ONLY        LOGGED ONLY             │
│                                                                 │
│  parallel_decisions table:                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ rules_signal: LONG  | gpt_signal: HOLD | hybrid: LONG    │  │
│  │ executed_strategy: rules                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Зачем нужен Shadow Mode

1. **Сразу видим контур** — логирование работает с дня 1
2. **Безопасно** — торгуем только по rules
3. **Baseline** — rules vs HOLD даёт базовую статистику
4. **Инкрементально** — заменяем stubs на агентов по одному

---

## 💰 Оценка стоимости (ОБНОВЛЕНО)

### Без оптимизаций

```
5 агентов × 16 пар × 6 раз/час × 24 часа = 11,520 вызовов/день
GPT-4o-mini: ~$0.002/вызов
Total: ~$23/день = ~$700/месяц  ❌ Дорого
```

### С оптимизациями

```
Budget Limiter: max 30 вызовов/мин, max $5/день
Agent Cache: 15 мин TTL → ~33% меньше вызовов
Stale Data Guard: пропускаем если данные старые

Реально: ~$3-5/день = ~$100-150/месяц  ✅ Приемлемо
```

### ENV переменные для контроля

```bash
# Budget
AGENTS_MAX_CALLS_PER_MINUTE=30
AGENTS_MAX_COST_PER_DAY=5.0

# Cache
AGENTS_CACHE_TTL_MINUTES=15

# Safety
AGENTS_VALIDATE_RESPONSES=true
AGENTS_BLOCK_ON_STALE_DATA=true
```

---

## ⚠️ Риски и митигация (ОБНОВЛЕНО)

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| LLM делает математику | Средняя | ResponseValidator блокирует цены/SL/TP |
| LLM hallucinations | Средняя | Strict JSON schema, confidence as enum |
| API rate limits | Низкая | BudgetLimiter + AgentCache |
| Cost overrun | Низкая | max_cost_per_day = $5 |
| Data source failures | Средняя | SourceHealthMonitor + fallback to HOLD |
| GPT worse than rules | Средняя | Shadow mode, statistics-driven decision |

---

## 📁 Структура проекта v2.1

```
app/
├── agents/
│   ├── llm/
│   │   ├── base_agent.py
│   │   ├── technical_agent.py
│   │   ├── macro_agent.py
│   │   ├── sentiment_agent.py
│   │   ├── correlation_agent.py
│   │   └── risk_agent.py
│   │
│   ├── prompts/
│   │   └── ... (промпты агентов)
│   │
│   ├── safety/                    # NEW
│   │   ├── __init__.py
│   │   ├── budget_limiter.py
│   │   ├── agent_cache.py
│   │   ├── response_validator.py
│   │   └── source_health.py
│   │
│   ├── aggregator.py
│   ├── parallel_runner.py
│   └── config.py
│
├── data_sources/
│   ├── economic_calendar.py
│   ├── cot_reports.py
│   ├── dxy_index.py
│   └── health_monitor.py          # NEW
│
├── models/
│   ├── confidence.py              # NEW: ConfidenceLevel enum
│   ├── agent_signal.py
│   ├── aggregated_decision.py
│   └── parallel_decision.py
│
└── ...
```

---

*Документ создан: 2025-12-21*
*Версия: 2.1 (с Safety Gates)*
*Статус: Ready for implementation*
