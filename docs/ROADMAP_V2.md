# Roadmap v2.1 — IBKR Bot Parallel Agents

## 📅 Обзор (UPDATED)

| Фаза | Дни | Фокус | Deliverable |
|------|-----|-------|-------------|
| **0. Shadow Infrastructure** | 0.5-1 | Каркас логирования | Parallel logging без GPT |
| **1. Data + Health** | 2-3 | Источники + мониторинг | Calendar + COT + DXY + Health |
| **2. Safety Gates** | 4-5 | Защитные механизмы | Budget + Cache + Validator |
| **3. LLM Agents** | 6-9 | 5 AI агентов | Агенты с enum confidence |
| **4. Integration** | 10-12 | Полная интеграция | Parallel execution |
| **5. Validation** | 13-14 | Dashboard + тесты | Production ready |

**Общий срок:** 14 рабочих дней (2-3 недели)

---

## 🎯 Ключевые изменения vs v2.0

| Было (v2.0) | Стало (v2.1) | Почему |
|-------------|--------------|--------|
| Comparison в днях 11-14 | Shadow mode в день 0-1 | Сразу видим контур |
| `confidence: float` | `confidence: enum` | LLM не делает математику |
| Без budget control | BudgetLimiter | Контроль расходов |
| Без cache | AgentCache (15 min) | Экономия API |
| Без валидации | ResponseValidator | Блок цен/SL/TP |
| Без health monitoring | SourceHealthMonitor | Stale data guard |

---

## Phase 0: Shadow Infrastructure (День 0.5-1)

### Цель

Создать полный контур логирования БЕЗ GPT. Rules работает реально, GPT = заглушки HOLD.

### День 0.5-1

#### Утро (4 часа)

**Задача 0.1:** Создать parallel_decisions таблицу

```sql
-- supabase/migrations/002_parallel_decisions.sql
CREATE TABLE parallel_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_utc TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    
    -- Rules
    rules_signal TEXT,
    rules_confidence TEXT,  -- LOW, MEDIUM, HIGH (enum)
    rules_flags TEXT[],
    
    -- GPT (stubs for now)
    gpt_signal TEXT DEFAULT 'HOLD',
    gpt_score FLOAT DEFAULT 0.0,
    gpt_consensus BOOLEAN DEFAULT FALSE,
    gpt_consensus_count INT DEFAULT 0,
    gpt_agent_details JSONB DEFAULT '[]'::jsonb,
    
    -- Hybrid
    hybrid_signal TEXT,
    hybrid_score FLOAT,
    
    -- Safety (NEW)
    budget_status TEXT DEFAULT 'OK',
    cache_hits INT DEFAULT 0,
    source_health JSONB,
    validation_failures INT DEFAULT 0,
    
    -- Execution
    executed_strategy TEXT,
    executed_signal TEXT,
    
    -- Outcome
    outcome_pips FLOAT,
    outcome_result TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_parallel_ts ON parallel_decisions(ts_utc);
CREATE INDEX idx_parallel_symbol ON parallel_decisions(symbol);
```

**Файлы:**
- [ ] `supabase/migrations/002_parallel_decisions.sql`
- [ ] `app/models/parallel_decision.py`
- [ ] `app/storage/parallel_decisions_repo.py`

#### Вечер (4 часа)

**Задача 0.2:** ParallelRunner со stubs

```python
# app/agents/parallel_runner.py (skeleton)
class ParallelDecisionRunner:
    def __init__(self, rules_engine: SignalEngineV1):
        self.rules_engine = rules_engine
    
    def run(self, context: UnifiedDataContext, symbol: str) -> ParallelDecision:
        # 1. Rules (реально работает)
        rules_preview = self.rules_engine.compute_preview_for_symbol(...)
        rules_decision = self._preview_to_decision(rules_preview)
        
        # 2. GPT (заглушка — ВСЕГДА HOLD)
        gpt_decision = AggregatedDecision(
            signal="HOLD",
            score=0.0,
            consensus=False,
            consensus_count=0,
            agent_signals=[],
        )
        
        # 3. Hybrid (пока = rules, т.к. GPT = HOLD)
        hybrid_decision = rules_decision
        
        return ParallelDecision(
            ts_utc=datetime.now(timezone.utc),
            symbol=symbol,
            rules_signal=rules_decision.signal,
            rules_confidence=rules_decision.confidence,
            gpt_signal=gpt_decision.signal,
            gpt_score=gpt_decision.score,
            gpt_consensus=gpt_decision.consensus,
            hybrid_signal=hybrid_decision.signal,
            hybrid_score=hybrid_decision.score,
            executed_strategy="rules",  # всегда rules в shadow mode
            executed_signal=rules_decision.signal,
        )
```

**Файлы:**
- [ ] `app/agents/parallel_runner.py` (skeleton)
- [ ] `app/models/aggregated_decision.py`

**Задача 0.3:** Интеграция в main.py

```python
# В main.py добавить:
parallel_runner = ParallelDecisionRunner(rules_engine=signal_engine)

# В main loop:
for symbol in settings.symbols:
    parallel = parallel_runner.run(context, symbol)
    parallel_decisions_repo.insert(parallel)
    
    # Execution по rules (как раньше)
    if parallel.rules_signal != "HOLD":
        # ... existing execution logic ...
```

**Definition of Done Phase 0:**
- [ ] `parallel_decisions` таблица создана
- [ ] Каждый тик пишет запись в таблицу
- [ ] rules_signal заполняется реальными данными
- [ ] gpt_signal = HOLD (заглушка)
- [ ] Dashboard skeleton показывает данные

---

## Phase 1: Data Sources + Health (Дни 2-3)

### День 2: Economic Calendar + Health Monitor

#### Утро (4 часа)

**Задача 1.1:** SourceHealth модель

```python
# app/models/source_health.py
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class SourceHealth:
    source_name: str
    is_available: bool
    last_update: datetime
    staleness_minutes: float
    coverage: float  # 0.0 - 1.0
    error_message: Optional[str] = None
    
    def is_stale(self, max_age_minutes: float) -> bool:
        return self.staleness_minutes > max_age_minutes
    
    def is_healthy(self, max_age_minutes: float) -> bool:
        return self.is_available and not self.is_stale(max_age_minutes) and self.coverage > 0.5
```

**Задача 1.2:** SourceHealthMonitor

```python
# app/agents/safety/source_health.py
class SourceHealthMonitor:
    MAX_STALENESS = {
        "ibkr_ohlcv": 5,        # критичный — блокирует всё
        "economic_calendar": 60,
        "cot_reports": 10080,   # 7 дней
        "dxy_index": 15,
    }
    
    def check_all(self, context) -> Dict[str, SourceHealth]:
        ...
    
    def should_block_trading(self, health: Dict[str, SourceHealth]) -> bool:
        return health["ibkr_ohlcv"].is_stale(self.MAX_STALENESS["ibkr_ohlcv"])
    
    def get_stale_for_agent(self, agent_name: str, health: Dict[str, SourceHealth]) -> List[str]:
        """Какие источники stale для конкретного агента"""
        agent_sources = {
            "TechnicalAgent": ["ibkr_ohlcv"],
            "MacroAgent": ["economic_calendar"],
            "SentimentAgent": ["cot_reports"],
            "CorrelationAgent": ["dxy_index"],
            "RiskAgent": ["ibkr_ohlcv"],
        }
        required = agent_sources.get(agent_name, [])
        return [s for s in required if health[s].is_stale(self.MAX_STALENESS[s])]
```

**Файлы:**
- [ ] `app/models/source_health.py`
- [ ] `app/agents/safety/__init__.py`
- [ ] `app/agents/safety/source_health.py`

#### Вечер (4 часа)

**Задача 1.3:** EconomicCalendarFetcher

```python
# app/data_sources/economic_calendar.py
class EconomicCalendarFetcher:
    """Источник: Forex Factory или Investing.com"""
    
    async def fetch_events(self, days_ahead: int = 7) -> List[EconomicEvent]:
        ...
    
    def get_health(self) -> SourceHealth:
        ...
```

**Файлы:**
- [ ] `app/data_sources/__init__.py`
- [ ] `app/data_sources/base_source.py`
- [ ] `app/data_sources/economic_calendar.py`
- [ ] `app/models/economic_event.py`

**Definition of Done День 2:**
- [ ] SourceHealthMonitor работает
- [ ] EconomicCalendar fetcher работает
- [ ] Health логируется в parallel_decisions

---

### День 3: COT + DXY + UnifiedContext

#### Утро (4 часа)

**Задача 1.4:** COT Reports Fetcher

```python
# app/data_sources/cot_reports.py
class COTReportsFetcher:
    """Источник: CFTC"""
    
    def fetch_latest(self, symbol: str) -> COTReport:
        ...
    
    def get_health(self) -> SourceHealth:
        ...
```

**Задача 1.5:** DXY Fetcher

```python
# app/data_sources/dxy_index.py
class DXYFetcher:
    """Источник: TradingView / Yahoo Finance"""
    
    def fetch_current(self) -> DXYSnapshot:
        ...
    
    def get_health(self) -> SourceHealth:
        ...
```

**Файлы:**
- [ ] `app/data_sources/cot_reports.py`
- [ ] `app/data_sources/dxy_index.py`
- [ ] `app/models/cot_report.py`
- [ ] `app/models/dxy_snapshot.py`

#### Вечер (4 часа)

**Задача 1.6:** UnifiedDataContext с health

```python
# app/market_data/unified_context.py
@dataclass
class UnifiedDataContext:
    market_snapshots: Dict[str, Dict[str, MarketSnapshot]]
    economic_events: List[EconomicEvent]
    cot_data: Dict[str, COTReport]
    dxy: DXYSnapshot
    source_health: Dict[str, SourceHealth]  # NEW
    
    def get_stale_sources(self) -> List[str]:
        return [name for name, h in self.source_health.items() if h.is_stale(...)]
    
    @classmethod
    def build(cls, market_service, calendar, cot, dxy, health_monitor):
        health = health_monitor.check_all(...)
        return cls(
            market_snapshots=...,
            economic_events=...,
            cot_data=...,
            dxy=...,
            source_health=health,
        )
```

**Файлы:**
- [ ] `app/market_data/unified_context.py`

**Definition of Done День 3:**
- [ ] Все 4 источника данных работают
- [ ] UnifiedContext собирает всё вместе
- [ ] source_health доступен во всех решениях

---

## Phase 2: Safety Gates (Дни 4-5)

### День 4: Budget Limiter + Agent Cache

#### Утро (4 часа)

**Задача 2.1:** BudgetLimiter

```python
# app/agents/safety/budget_limiter.py
@dataclass
class BudgetLimiter:
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
    
    def record_call(self, cost: float = 0.002):
        self.calls_this_minute += 1
        self.cost_today += cost
    
    def _reset_if_needed(self):
        now = datetime.now()
        if now.minute != self.minute_start.minute:
            self.calls_this_minute = 0
            self.minute_start = now
        if now.date() != self.day_start:
            self.cost_today = 0.0
            self.day_start = now.date()
```

**Файлы:**
- [ ] `app/agents/safety/budget_limiter.py`

#### Вечер (4 часа)

**Задача 2.2:** AgentCache

```python
# app/agents/safety/agent_cache.py
class AgentCache:
    def __init__(self, ttl_minutes: int = 15):
        self.ttl = timedelta(minutes=ttl_minutes)
        self.cache: Dict[str, Tuple[AgentSignal, datetime]] = {}
    
    def get_key(self, agent_name: str, symbol: str) -> str:
        now = datetime.now(timezone.utc)
        bucket = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        return f"{agent_name}:{symbol}:{bucket.isoformat()}"
    
    def get(self, agent_name: str, symbol: str) -> Optional[AgentSignal]:
        key = self.get_key(agent_name, symbol)
        if key in self.cache:
            signal, cached_at = self.cache[key]
            if datetime.now(timezone.utc) - cached_at < self.ttl:
                # Clone and add CACHED flag
                cached_signal = signal.copy()
                cached_signal.flags.append("CACHED")
                return cached_signal
        return None
    
    def set(self, agent_name: str, symbol: str, signal: AgentSignal):
        key = self.get_key(agent_name, symbol)
        self.cache[key] = (signal, datetime.now(timezone.utc))
    
    def clear_expired(self):
        """Периодическая очистка"""
        now = datetime.now(timezone.utc)
        expired = [k for k, (_, t) in self.cache.items() if now - t > self.ttl]
        for k in expired:
            del self.cache[k]
```

**Файлы:**
- [ ] `app/agents/safety/agent_cache.py`

**Definition of Done День 4:**
- [ ] BudgetLimiter работает
- [ ] AgentCache работает
- [ ] Метрики пишутся в parallel_decisions

---

### День 5: Response Validator + Confidence Enum

#### Утро (4 часа)

**Задача 2.3:** ConfidenceLevel enum

```python
# app/models/confidence.py
from enum import Enum

class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

# Маппинг в КОДЕ, не от LLM
CONFIDENCE_WEIGHTS = {
    ConfidenceLevel.LOW: 0.3,
    ConfidenceLevel.MEDIUM: 0.6,
    ConfidenceLevel.HIGH: 0.9,
}

def confidence_to_weight(conf: ConfidenceLevel) -> float:
    return CONFIDENCE_WEIGHTS[conf]
```

**Задача 2.4:** ResponseValidator

```python
# app/agents/safety/response_validator.py
import re
from typing import Tuple

class ResponseValidator:
    """Блокирует ответы LLM с запрещённым контентом"""
    
    FORBIDDEN_PATTERNS = [
        (r"\d+\.\d{4,}", "price"),           # 1.0732
        (r"SL|TP|stop.?loss|take.?profit", "sl_tp"),
        (r"\d+\s*(lot|unit|position)", "position_size"),
        (r"\d+\s*pip", "pip_calc"),
        (r"enter\s+at|exit\s+at", "entry_price"),
    ]
    
    def validate(self, response: dict) -> Tuple[bool, str]:
        reasoning = response.get("reasoning", "")
        
        for pattern, violation_type in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, reasoning, re.IGNORECASE):
                logger.warning(f"LLM violated rules: {violation_type}")
                return False, violation_type
        
        return True, "OK"
    
    def validate_confidence(self, confidence: str) -> bool:
        """Проверяет что confidence — валидный enum"""
        return confidence in ("low", "medium", "high")
    
    def sanitize(self, response: dict) -> AgentSignal:
        is_valid, violation = self.validate(response)
        
        if not is_valid:
            return AgentSignal(
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"Response sanitized: {violation}",
                flags=["LLM_SANITIZED", f"VIOLATION_{violation.upper()}"],
            )
        
        # Validate confidence
        conf_str = response.get("confidence", "low")
        if not self.validate_confidence(conf_str):
            conf_str = "low"
        
        return AgentSignal(
            signal=response["signal"],
            confidence=ConfidenceLevel(conf_str),
            reasoning=response.get("reasoning", ""),
            flags=response.get("flags", []),
        )
```

**Файлы:**
- [ ] `app/models/confidence.py`
- [ ] `app/agents/safety/response_validator.py`

#### Вечер (4 часа)

**Задача 2.5:** Интеграция Safety Gates в runner

```python
# app/agents/parallel_runner.py (обновление)
class ParallelDecisionRunner:
    def __init__(
        self,
        rules_engine: SignalEngineV1,
        agents: List[BaseLLMAgent],
        aggregator: WeightedAggregator,
        budget_limiter: BudgetLimiter,
        agent_cache: AgentCache,
        response_validator: ResponseValidator,
        health_monitor: SourceHealthMonitor,
    ):
        ...
    
    def _run_single_agent(
        self, 
        agent: BaseLLMAgent, 
        context: UnifiedDataContext, 
        symbol: str
    ) -> AgentSignal:
        # 1. Check budget
        can_call, reason = self.budget_limiter.can_call()
        if not can_call:
            return AgentSignal(
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"Budget gate: {reason}",
                flags=[f"BUDGET_{reason}"],
            )
        
        # 2. Check cache
        cached = self.agent_cache.get(agent.name, symbol)
        if cached:
            return cached
        
        # 3. Check source health
        stale = self.health_monitor.get_stale_for_agent(agent.name, context.source_health)
        if stale:
            return AgentSignal(
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"Data stale: {stale}",
                flags=["DATA_STALE"] + [f"STALE_{s.upper()}" for s in stale],
            )
        
        # 4. Call LLM
        try:
            raw_response = agent.call_llm(context, symbol)
            self.budget_limiter.record_call()
        except Exception as e:
            return AgentSignal(
                signal="HOLD",
                confidence=ConfidenceLevel.LOW,
                reasoning=f"LLM error: {e}",
                flags=["LLM_ERROR"],
            )
        
        # 5. Validate response
        signal = self.response_validator.sanitize(raw_response)
        
        # 6. Cache if valid
        if "LLM_SANITIZED" not in signal.flags:
            self.agent_cache.set(agent.name, symbol, signal)
        
        return signal
```

**Definition of Done День 5:**
- [ ] ConfidenceLevel enum работает
- [ ] ResponseValidator блокирует запрещённый контент
- [ ] Все Safety Gates интегрированы
- [ ] Тесты на edge cases

---

## Phase 3: LLM Agents (Дни 6-9)

### День 6: Base Agent + Technical Agent

#### Утро (4 часа)

**Задача 3.1:** BaseLLMAgent

```python
# app/agents/llm/base_agent.py
from abc import ABC, abstractmethod

class BaseLLMAgent(ABC):
    name: str
    version: str
    weight: float
    
    def __init__(self, openai_client, validator: ResponseValidator):
        self.client = openai_client
        self.validator = validator
    
    @abstractmethod
    def prepare_input(self, context: UnifiedDataContext, symbol: str) -> dict:
        """Подготовка данных — НЕ числа, а категории"""
        ...
    
    @abstractmethod
    def get_system_prompt(self) -> str:
        """Системный промпт с правилами"""
        ...
    
    def call_llm(self, context: UnifiedDataContext, symbol: str) -> dict:
        input_data = self.prepare_input(context, symbol)
        
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": json.dumps(input_data)},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=200,
        )
        
        return json.loads(response.choices[0].message.content)
```

**Файлы:**
- [ ] `app/agents/llm/__init__.py`
- [ ] `app/agents/llm/base_agent.py`

#### Вечер (4 часа)

**Задача 3.2:** TechnicalAgent (первый реальный агент)

```python
# app/agents/llm/technical_agent.py
class TechnicalAgent(BaseLLMAgent):
    name = "TechnicalAgent"
    version = "1.0"
    weight = 0.30
    
    def prepare_input(self, context: UnifiedDataContext, symbol: str) -> dict:
        """Передаём категории, НЕ числа"""
        snapshots = context.market_snapshots[symbol]
        
        return {
            "symbol": symbol,
            "timeframes": {
                tf: {
                    "trend": self._get_trend(snap),      # UP, DOWN, NEUTRAL
                    "rsi_zone": self._get_rsi_zone(snap), # OVERSOLD, NEUTRAL, OVERBOUGHT
                    "ma_alignment": self._get_ma_alignment(snap),  # BULLISH, BEARISH, NEUTRAL
                }
                for tf, snap in snapshots.items()
            },
            "structure": {
                "higher_highs": self._check_hh(snapshots),
                "higher_lows": self._check_hl(snapshots),
                "trend_strength": self._get_strength(snapshots),  # STRONG, MODERATE, WEAK
            }
        }
    
    def _get_trend(self, snap) -> str:
        if snap.ma_fast > snap.ma_slow * 1.001:
            return "UP"
        elif snap.ma_fast < snap.ma_slow * 0.999:
            return "DOWN"
        return "NEUTRAL"
    
    def _get_rsi_zone(self, snap) -> str:
        if snap.rsi > 70:
            return "OVERBOUGHT"
        elif snap.rsi < 30:
            return "OVERSOLD"
        return "NEUTRAL"
```

```python
# app/agents/prompts/technical.py
TECHNICAL_PROMPT = """
You are TechnicalAgent analyzing Forex price action.

INPUT: Multi-timeframe categorical data (trends, RSI zones, MA alignment)

OUTPUT JSON:
{
  "signal": "LONG" | "SHORT" | "HOLD",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation (max 100 words)"
}

RULES:
1. All timeframes aligned = "high" confidence
2. Mixed signals = "medium" or "low"
3. RSI extremes suggest caution
4. NEVER mention specific prices, numbers, SL/TP, or pip values
5. Higher timeframe trend takes precedence

CONFIDENCE GUIDE:
- "high": Strong alignment, clear setup, no conflicts
- "medium": Moderate signals, minor conflicts
- "low": Weak or conflicting signals
"""
```

**Файлы:**
- [ ] `app/agents/llm/technical_agent.py`
- [ ] `app/agents/prompts/technical.py`

**Definition of Done День 6:**
- [ ] TechnicalAgent работает
- [ ] Возвращает enum confidence
- [ ] Заменяет stub в parallel runner
- [ ] Тесты проходят

---

### День 7: Macro + Sentiment Agents

#### Утро (4 часа)

**Задача 3.3:** MacroAgent

```python
# app/agents/llm/macro_agent.py
class MacroAgent(BaseLLMAgent):
    name = "MacroAgent"
    version = "1.0"
    weight = 0.25
    
    def prepare_input(self, context: UnifiedDataContext, symbol: str) -> dict:
        base, quote = symbol[:3], symbol[3:]
        
        upcoming = [e for e in context.economic_events 
                   if e.currency in (base, quote) 
                   and e.impact == "high"
                   and e.hours_until() < 24]
        
        return {
            "symbol": symbol,
            "base_currency": base,
            "quote_currency": quote,
            "upcoming_high_impact": [
                {
                    "hours_until": round(e.hours_until(), 1),
                    "currency": e.currency,
                    "event": e.event_name,
                    "expected_impact": e.impact.upper(),
                }
                for e in upcoming[:3]
            ],
            "recent_surprises": self._get_recent_surprises(context, base, quote),
            "event_risk_level": self._assess_risk(upcoming),  # HIGH, MEDIUM, LOW
        }
```

**Файлы:**
- [ ] `app/agents/llm/macro_agent.py`
- [ ] `app/agents/prompts/macro.py`

#### Вечер (4 часа)

**Задача 3.4:** SentimentAgent

```python
# app/agents/llm/sentiment_agent.py
class SentimentAgent(BaseLLMAgent):
    name = "SentimentAgent"
    version = "1.0"
    weight = 0.20
    
    def prepare_input(self, context: UnifiedDataContext, symbol: str) -> dict:
        cot = context.cot_data.get(symbol)
        
        return {
            "symbol": symbol,
            "cot_positioning": {
                "non_commercial_bias": self._get_bias(cot),  # LONG, SHORT, NEUTRAL
                "weekly_change": self._get_change_direction(cot),  # INCREASING, DECREASING, FLAT
                "percentile_52w": self._get_percentile_bucket(cot),  # HIGH, MEDIUM, LOW
            } if cot else None,
            "retail_sentiment": {
                "crowd_position": self._get_crowd_position(context, symbol),
                "extreme_level": self._is_extreme(context, symbol),
            },
        }
```

**Файлы:**
- [ ] `app/agents/llm/sentiment_agent.py`
- [ ] `app/agents/prompts/sentiment.py`

**Definition of Done День 7:**
- [ ] MacroAgent работает
- [ ] SentimentAgent работает
- [ ] Оба используют категории, не числа
- [ ] Заменяют stubs

---

### День 8: Correlation + Risk Agents

#### Утро (4 часа)

**Задача 3.5:** CorrelationAgent

```python
# app/agents/llm/correlation_agent.py
class CorrelationAgent(BaseLLMAgent):
    name = "CorrelationAgent"
    version = "1.0"
    weight = 0.15
    
    def prepare_input(self, context: UnifiedDataContext, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "dxy_trend": self._get_dxy_trend(context.dxy),  # UP, DOWN, NEUTRAL
            "dxy_vs_sma": self._get_dxy_vs_sma(context.dxy),  # ABOVE, BELOW, AT
            "correlated_pairs_alignment": self._check_correlations(context, symbol),
            "divergences": self._find_divergences(context, symbol),
        }
```

**Файлы:**
- [ ] `app/agents/llm/correlation_agent.py`
- [ ] `app/agents/prompts/correlation.py`

#### Вечер (4 часа)

**Задача 3.6:** RiskAgent

```python
# app/agents/llm/risk_agent.py
class RiskAgent(BaseLLMAgent):
    name = "RiskAgent"
    version = "1.0"
    weight = 0.10
    
    def prepare_input(self, context: UnifiedDataContext, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "volatility_regime": self._get_vol_regime(context, symbol),  # LOW, NORMAL, HIGH, EXTREME
            "spread_status": self._get_spread_status(context, symbol),  # TIGHT, NORMAL, WIDE
            "session": self._get_session(),  # ASIAN, LONDON, NY, OVERLAP
            "liquidity": self._get_liquidity_level(),  # LOW, MEDIUM, HIGH
            "recent_performance": {
                "streak": self._get_streak(),  # WINNING, LOSING, NEUTRAL
                "drawdown_status": self._get_dd_status(),  # OK, WARNING, CRITICAL
            },
        }
```

**Файлы:**
- [ ] `app/agents/llm/risk_agent.py`
- [ ] `app/agents/prompts/risk.py`

**Definition of Done День 8:**
- [ ] Все 5 агентов работают
- [ ] Все используют enum confidence
- [ ] Все передают категории, не числа

---

### День 9: Weighted Aggregator

#### Полный день

**Задача 3.7:** WeightedAggregator с enum support

```python
# app/agents/aggregator.py
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
    
    def aggregate(self, agent_signals: List[AgentSignal]) -> AggregatedDecision:
        weighted_sum = 0.0
        weight_sum = 0.0
        
        for signal in agent_signals:
            signal_val = self.SIGNAL_VALUES[signal.signal]
            agent_weight = self.WEIGHTS[signal.agent_name]
            conf_weight = confidence_to_weight(signal.confidence)  # enum → float
            
            weighted_sum += signal_val * conf_weight * agent_weight
            weight_sum += conf_weight * agent_weight
        
        score = weighted_sum / weight_sum if weight_sum > 0 else 0.0
        
        # Consensus
        long_count = sum(1 for s in agent_signals if s.signal == "LONG")
        short_count = sum(1 for s in agent_signals if s.signal == "SHORT")
        consensus = max(long_count, short_count) >= 3
        
        # Final
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

**Файлы:**
- [ ] `app/agents/aggregator.py`

**Definition of Done День 9:**
- [ ] Aggregator работает с enum confidence
- [ ] Score рассчитывается корректно
- [ ] Тесты на все edge cases

---

## Phase 4: Integration (Дни 10-12)

### День 10: Full Parallel Runner

**Задача 4.1:** Полная интеграция всех компонентов

```python
# app/agents/parallel_runner.py (финальная версия)
class ParallelDecisionRunner:
    def run(self, context: UnifiedDataContext, symbol: str) -> ParallelDecision:
        # 1. Rules (всегда работает)
        rules_decision = self._run_rules(context, symbol)
        
        # 2. GPT agents (с safety gates)
        agent_signals = self._run_all_agents(context, symbol)
        gpt_decision = self.aggregator.aggregate(agent_signals)
        
        # 3. Hybrid
        hybrid_decision = self._compute_hybrid(rules_decision, gpt_decision)
        
        # 4. Collect safety metrics
        safety_metrics = self._collect_safety_metrics(agent_signals)
        
        return ParallelDecision(
            ts_utc=datetime.now(timezone.utc),
            symbol=symbol,
            
            rules_signal=rules_decision.signal,
            rules_confidence=rules_decision.confidence,
            rules_flags=rules_decision.flags,
            
            gpt_signal=gpt_decision.signal,
            gpt_score=gpt_decision.score,
            gpt_consensus=gpt_decision.consensus,
            gpt_consensus_count=gpt_decision.consensus_count,
            gpt_agent_details=[s.to_dict() for s in agent_signals],
            
            hybrid_signal=hybrid_decision.signal,
            hybrid_score=hybrid_decision.score,
            
            budget_status=safety_metrics["budget_status"],
            cache_hits=safety_metrics["cache_hits"],
            source_health=context.source_health,
            validation_failures=safety_metrics["validation_failures"],
        )
```

---

### День 11: Strategy Selector + Main Loop

**Задача 4.2:** StrategySelector

```python
# app/comparison/strategy_selector.py
class StrategySelector:
    def __init__(self, active_strategy: str = "rules"):
        assert active_strategy in ("rules", "gpt", "hybrid")
        self.active_strategy = active_strategy
    
    @classmethod
    def from_env(cls):
        return cls(os.getenv("ACTIVE_STRATEGY", "rules"))
    
    def select(self, parallel: ParallelDecision) -> Tuple[str, str]:
        if self.active_strategy == "rules":
            return parallel.rules_signal, "rules"
        elif self.active_strategy == "gpt":
            return parallel.gpt_signal, "gpt"
        else:
            return parallel.hybrid_signal, "hybrid"
```

**Задача 4.3:** Main loop integration

---

### День 12: Error Handling + Circuit Breaker

**Задача 4.4:** Circuit breaker для GPT failures

---

## Phase 5: Validation (Дни 13-14)

### День 13: Dashboard + Analytics

**Задача 5.1:** Comparison Dashboard

**Задача 5.2:** Outcome Tracker

---

### День 14: E2E Testing + Deploy

**Задача 5.3:** E2E тесты

**Задача 5.4:** VPS deployment

---

## 📊 Чеклист готовности v2.1

### Safety Gates

- [ ] BudgetLimiter работает (max 30/min, max $5/day)
- [ ] AgentCache работает (15 min TTL)
- [ ] ResponseValidator блокирует цены/SL/TP
- [ ] SourceHealthMonitor отслеживает staleness
- [ ] Confidence как enum (LOW/MEDIUM/HIGH)

### Data Flow

- [ ] Market Data Loop работает
- [ ] Economic Calendar fetcher работает
- [ ] COT Reports fetcher работает
- [ ] DXY fetcher работает
- [ ] UnifiedContext собирает всё

### Agents

- [ ] TechnicalAgent работает
- [ ] MacroAgent работает
- [ ] SentimentAgent работает
- [ ] CorrelationAgent работает
- [ ] RiskAgent работает
- [ ] Все передают категории, не числа

### Integration

- [ ] ParallelRunner запускает все стратегии
- [ ] StrategySelector переключает по ENV
- [ ] parallel_decisions записываются
- [ ] Circuit breaker защищает от failures

### Metrics

- [ ] budget_status логируется
- [ ] cache_hits логируется
- [ ] validation_failures логируется
- [ ] source_health логируется

---

## 📈 Критерии успеха

### После Phase 0 (День 1)

- [ ] parallel_decisions таблица заполняется
- [ ] rules_signal реальный, gpt_signal = HOLD
- [ ] Dashboard skeleton работает

### После Phase 2 (День 5)

- [ ] Все Safety Gates работают
- [ ] Метрики записываются
- [ ] Budget не превышается

### После Phase 3 (День 9)

- [ ] Все 5 агентов работают
- [ ] Все используют enum confidence
- [ ] Ни один агент не возвращает цены/SL/TP

### После Phase 5 (День 14)

- [ ] >1000 parallel decisions записано
- [ ] Статистика сравнения доступна
- [ ] Production ready

---

*Roadmap Version: 2.1*
*Created: 2025-12-21*
*Updated: Added Phase 0, Safety Gates, Enum Confidence*
