# Integration Schema v2.1 — IBKR Bot with Safety Gates

## 1. Data Flow Diagram (UPDATED)

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                     EXTERNAL SOURCES                         │
                                    └─────────────────────────────────────────────────────────────┘
                                                              │
                    ┌─────────────────┬───────────────────────┼───────────────────┬─────────────────┐
                    │                 │                       │                   │                 │
                    ▼                 ▼                       ▼                   ▼                 ▼
            ┌───────────────┐ ┌───────────────┐ ┌───────────────────┐ ┌───────────────┐ ┌───────────────┐
            │  IB Gateway   │ │ Forex Factory │ │   CFTC Website    │ │  TradingView  │ │    OANDA      │
            │  OHLCV Data   │ │   Calendar    │ │   COT Reports     │ │   DXY Feed    │ │  Sentiment    │
            │   (port 4001) │ │     (HTTP)    │ │     (HTTP)        │ │    (HTTP)     │ │    (HTTP)     │
            └───────┬───────┘ └───────┬───────┘ └─────────┬─────────┘ └───────┬───────┘ └───────┬───────┘
                    │                 │                   │                   │                 │
                    ▼                 ▼                   ▼                   ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                      SOURCE HEALTH MONITOR (NEW)                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                                                                                     │ │
│  │   Source          │ Max Staleness │ Status      │ Last Update │ Coverage │ Action if Stale         │ │
│  │   ────────────────┼───────────────┼─────────────┼─────────────┼──────────┼───────────────────────── │ │
│  │   ibkr_ohlcv      │ 5 min         │ ✅ HEALTHY  │ 30 sec ago  │ 100%     │ BLOCK all trading       │ │
│  │   economic_cal    │ 60 min        │ ✅ HEALTHY  │ 15 min ago  │ 95%      │ MacroAgent → HOLD       │ │
│  │   cot_reports     │ 7 days        │ ✅ HEALTHY  │ 3 days ago  │ 100%     │ SentimentAgent → HOLD   │ │
│  │   dxy_index       │ 15 min        │ ⚠️ STALE    │ 20 min ago  │ 100%     │ CorrelationAgent → HOLD │ │
│  │                                                                                                     │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                           │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                           DATA SOURCES LAYER                                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  IBKRFetcher    │  │ EconomicCalendar│  │  COTReports     │  │   DXYFetcher    │  │RetailSentiment  │  │
│  │  (существует)   │  │  Fetcher (NEW)  │  │  Fetcher (NEW)  │  │     (NEW)       │  │    (NEW)        │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                    │                    │                    │           │
│           └────────────────────┴──────────┬─────────┴────────────────────┴────────────────────┘           │
│                                           │                                                               │
│                                           ▼                                                               │
│                           ┌───────────────────────────────┐                                               │
│                           │   UnifiedDataContext          │                                               │
│                           │                               │                                               │
│                           │  • market_snapshots           │                                               │
│                           │  • economic_events            │                                               │
│                           │  • cot_data                   │                                               │
│                           │  • dxy_data                   │                                               │
│                           │  • source_health (NEW)        │                                               │
│                           │    └─ Dict[str, SourceHealth] │                                               │
│                           └───────────────┬───────────────┘                                               │
│                                           │                                                               │
└───────────────────────────────────────────┼───────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SAFETY GATES LAYER (NEW)                                               │
├───────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                         BUDGET LIMITER                                              │ │
│  │                                                                                                     │ │
│  │   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐                              │ │
│  │   │ Rate Limit      │     │ Cost Limit      │     │ Action          │                              │ │
│  │   │ 30 calls/min    │     │ $5/day          │     │                 │                              │ │
│  │   │                 │     │                 │     │ IF exceeded:    │                              │ │
│  │   │ Current: 12/30  │     │ Current: $1.20  │     │ → GPT = HOLD    │                              │ │
│  │   │ Status: ✅ OK   │     │ Status: ✅ OK   │     │ → flag: BUDGET  │                              │ │
│  │   └─────────────────┘     └─────────────────┘     └─────────────────┘                              │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                          AGENT CACHE                                                │ │
│  │                                                                                                     │ │
│  │   Key Format: {agent}:{symbol}:{15min_bucket}                                                      │ │
│  │   TTL: 15 minutes                                                                                   │ │
│  │                                                                                                     │ │
│  │   ┌──────────────────────────────────────────┬─────────────┬────────────┐                          │ │
│  │   │ Key                                      │ Cached At   │ Expires    │                          │ │
│  │   ├──────────────────────────────────────────┼─────────────┼────────────┤                          │ │
│  │   │ TechnicalAgent:EURUSD:2025-12-21T14:00   │ 14:02:15    │ 14:17:15   │                          │ │
│  │   │ MacroAgent:EURUSD:2025-12-21T14:00       │ 14:02:18    │ 14:17:18   │                          │ │
│  │   │ TechnicalAgent:GBPUSD:2025-12-21T14:00   │ 14:03:01    │ 14:18:01   │                          │ │
│  │   └──────────────────────────────────────────┴─────────────┴────────────┘                          │ │
│  │                                                                                                     │ │
│  │   Cache Hit → Return cached signal + flag "CACHED"                                                 │ │
│  │   Cache Miss → Call LLM → Store in cache                                                           │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                       RESPONSE VALIDATOR                                            │ │
│  │                                                                                                     │ │
│  │   FORBIDDEN PATTERNS (regex):                                                                       │ │
│  │   ├── \d+\.\d{4,}           → Prices like 1.0732                                                   │ │
│  │   ├── SL|TP|stop.?loss      → Stop loss / Take profit                                              │ │
│  │   ├── \d+\s*(lot|unit)      → Position sizes                                                       │ │
│  │   ├── \d+\s*pip             → Pip calculations                                                     │ │
│  │   └── enter\s+at|exit\s+at  → Entry/exit prices                                                    │ │
│  │                                                                                                     │ │
│  │   IF pattern found:                                                                                 │ │
│  │   → Log warning                                                                                     │ │
│  │   → Return HOLD + flag "LLM_SANITIZED"                                                             │ │
│  │   → Increment violation counter                                                                     │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                                           │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                          DECISION LAYER                                                    │
│                                                                                                           │
│                              ┌─────────────────────────────────────┐                                      │
│                              │        ParallelDecisionRunner       │                                      │
│                              └─────────────────┬───────────────────┘                                      │
│                                                │                                                          │
│                 ┌──────────────────────────────┼──────────────────────────────┐                           │
│                 │                              │                              │                           │
│                 ▼                              ▼                              ▼                           │
│    ┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐                  │
│    │    RULES ENGINE        │   │     GPT AGENTS         │   │       HYBRID           │                  │
│    │    (существует)        │   │                        │   │                        │                  │
│    │                        │   │  Safety Gates Check:   │   │  IF rules == gpt:      │                  │
│    │  SignalEngineV1        │   │  ├── Budget OK? ✅     │   │    score = high        │                  │
│    │  • SMA Crossover       │   │  ├── Cache hit? ❌     │   │  ELSE:                 │                  │
│    │  • RSI Confirm         │   │  ├── Source healthy? ✅│   │    blend weights       │                  │
│    │  • RegimeAgent         │   │  └── Proceed to LLM    │   │                        │                  │
│    │  • QualityAgent        │   │                        │   │  Rules: 0.4            │                  │
│    │                        │   │  ┌──────────────────┐  │   │  GPT:   0.6            │                  │
│    │                        │   │  │ TechnicalAgent   │  │   │                        │                  │
│    │                        │   │  │ conf: HIGH → 0.9 │  │   │                        │                  │
│    │                        │   │  └────────────────┬─┘  │   │                        │                  │
│    │                        │   │  ┌────────────────┼─┐  │   │                        │                  │
│    │                        │   │  │ MacroAgent     │ │  │   │                        │                  │
│    │                        │   │  │ conf: MED → 0.6│ │  │   │                        │                  │
│    │                        │   │  └────────────────┼─┘  │   │                        │                  │
│    │                        │   │  ┌────────────────┼─┐  │   │                        │                  │
│    │                        │   │  │ SentimentAgent │ │  │   │                        │                  │
│    │                        │   │  │ conf: MED → 0.6│ │  │   │                        │                  │
│    │                        │   │  └────────────────┼─┘  │   │                        │                  │
│    │                        │   │  ┌────────────────┼─┐  │   │                        │                  │
│    │                        │   │  │ CorrelationAgt │ │  │   │                        │                  │
│    │                        │   │  │ conf: LOW → 0.3│ │  │   │                        │                  │
│    │                        │   │  └────────────────┼─┘  │   │                        │                  │
│    │                        │   │  ┌────────────────┼─┐  │   │                        │                  │
│    │                        │   │  │ RiskAgent      │ │  │   │                        │                  │
│    │                        │   │  │ conf: HIGH→ 0.9│ │  │   │                        │                  │
│    │                        │   │  └──────────────┬─┘ │  │   │                        │                  │
│    │                        │   │                 │   │  │   │                        │                  │
│    │                        │   │                 ▼   │  │   │                        │                  │
│    │                        │   │  ┌──────────────────┐  │   │                        │                  │
│    │                        │   │  │WeightedAggregator│  │   │                        │                  │
│    │                        │   │  │                  │  │   │                        │                  │
│    │                        │   │  │ Conf enum → float│  │   │                        │                  │
│    │                        │   │  │ (in code, not LLM│  │   │                        │                  │
│    │                        │   │  └──────────────────┘  │   │                        │                  │
│    └───────────┬────────────┘   └───────────┬────────────┘   └───────────┬────────────┘                  │
│                │                            │                            │                               │
│                ▼                            ▼                            ▼                               │
│         decision_rules              decision_gpt                 decision_hybrid                         │
│                │                            │                            │                               │
│                └────────────────────────────┼────────────────────────────┘                               │
│                                             │                                                            │
│                                             ▼                                                            │
│                              ┌──────────────────────────────┐                                            │
│                              │     parallel_decisions       │                                            │
│                              │        (Supabase)            │                                            │
│                              └──────────────────────────────┘                                            │
│                                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Confidence Enum Flow (NEW)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                            CONFIDENCE HANDLING                                           │
└─────────────────────────────────────────────────────────────────────────────────────────┘

                              LLM Response
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │  {                           │
                    │    "signal": "LONG",         │
                    │    "confidence": "high",     │  ← String enum from LLM
                    │    "reasoning": "..."        │
                    │  }                           │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │     Response Validator       │
                    │                              │
                    │  1. Check forbidden patterns │
                    │  2. Validate JSON schema     │
                    │  3. Parse confidence string  │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │    ConfidenceLevel Enum      │
                    │                              │
                    │  class ConfidenceLevel(Enum):│
                    │      LOW = "low"             │
                    │      MEDIUM = "medium"       │
                    │      HIGH = "high"           │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │   WeightedAggregator         │
                    │                              │
                    │  CONFIDENCE_WEIGHTS = {      │
                    │      LOW: 0.3,               │  ← Mapping in CODE
                    │      MEDIUM: 0.6,            │     NOT from LLM
                    │      HIGH: 0.9,              │
                    │  }                           │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                         Used in aggregation
                              formula
```

---

## 3. Agent Execution Flow with Safety Gates

```
┌─────────┐     ┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌────────────┐
│  Timer  │     │ DataContext │     │ SafetyGates  │     │  LLMAgent   │     │ Aggregator │
└────┬────┘     └──────┬──────┘     └──────┬───────┘     └──────┬──────┘     └─────┬──────┘
     │                 │                   │                    │                  │
     │  tick           │                   │                    │                  │
     │────────────────>│                   │                    │                  │
     │                 │                   │                    │                  │
     │                 │ check_health()    │                    │                  │
     │                 │──────────────────>│                    │                  │
     │                 │                   │                    │                  │
     │                 │   SourceHealth[]  │                    │                  │
     │                 │<──────────────────│                    │                  │
     │                 │                   │                    │                  │
     │                 │                   │                    │                  │
     │                 │ FOR EACH AGENT:   │                    │                  │
     │                 │ ┌────────────────────────────────────────────────────────┐
     │                 │ │                 │                    │                  │
     │                 │ │ check_budget()  │                    │                  │
     │                 │ │────────────────>│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ │ IF budget_exceeded:                  │                  │
     │                 │ │ │               │ return HOLD +      │                  │
     │                 │ │ │               │ BUDGET_GATE        │                  │
     │                 │ │ │               │                    │                  │
     │                 │ │ check_cache()   │                    │                  │
     │                 │ │────────────────>│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ │ IF cache_hit:   │                    │                  │
     │                 │ │ │               │ return cached +    │                  │
     │                 │ │ │               │ CACHED flag        │                  │
     │                 │ │ │               │                    │                  │
     │                 │ │ check_source()  │                    │                  │
     │                 │ │────────────────>│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ │ IF source_stale:│                    │                  │
     │                 │ │ │               │ return HOLD +      │                  │
     │                 │ │ │               │ DATA_STALE         │                  │
     │                 │ │ │               │                    │                  │
     │                 │ │ ALL GATES PASS: │                    │                  │
     │                 │ │                 │ call_llm()         │                  │
     │                 │ │                 │───────────────────>│                  │
     │                 │ │                 │                    │                  │
     │                 │ │                 │   LLM Response     │                  │
     │                 │ │                 │<───────────────────│                  │
     │                 │ │                 │                    │                  │
     │                 │ │ validate()      │                    │                  │
     │                 │ │────────────────>│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ │ IF invalid:     │                    │                  │
     │                 │ │ │               │ return HOLD +      │                  │
     │                 │ │ │               │ LLM_SANITIZED      │                  │
     │                 │ │ │               │                    │                  │
     │                 │ │ cache_set()     │                    │                  │
     │                 │ │────────────────>│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ │ record_cost()   │                    │                  │
     │                 │ │────────────────>│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ │   AgentSignal   │                    │                  │
     │                 │ │<────────────────│                    │                  │
     │                 │ │                 │                    │                  │
     │                 │ └────────────────────────────────────────────────────────┘
     │                 │                   │                    │                  │
     │                 │ All AgentSignals  │                    │                  │
     │                 │──────────────────────────────────────────────────────────>│
     │                 │                   │                    │                  │
     │                 │                   │                    │  aggregate()     │
     │                 │                   │                    │                  │
     │                 │   AggregatedDecision                   │                  │
     │                 │<──────────────────────────────────────────────────────────│
     │                 │                   │                    │                  │
```

---

## 4. Supabase Schema (UPDATED)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SUPABASE SCHEMA v2.1                                  │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                      NEW TABLES                                          │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                            parallel_decisions                                    │   │
│  │                                                                                  │   │
│  │  id                    UUID PRIMARY KEY                                          │   │
│  │  ts_utc                TIMESTAMPTZ NOT NULL                                      │   │
│  │  symbol                TEXT NOT NULL                                             │   │
│  │                                                                                  │   │
│  │  -- Rules Engine                                                                 │   │
│  │  rules_signal          TEXT                    -- LONG, SHORT, HOLD              │   │
│  │  rules_confidence      TEXT                    -- LOW, MEDIUM, HIGH (enum)       │   │
│  │  rules_flags           TEXT[]                                                    │   │
│  │                                                                                  │   │
│  │  -- GPT Agents                                                                   │   │
│  │  gpt_signal            TEXT                                                      │   │
│  │  gpt_score             FLOAT                                                     │   │
│  │  gpt_consensus         BOOLEAN                                                   │   │
│  │  gpt_consensus_count   INT                     -- how many agents agreed         │   │
│  │  gpt_agent_details     JSONB                   -- full agent responses           │   │
│  │                                                                                  │   │
│  │  -- Hybrid                                                                       │   │
│  │  hybrid_signal         TEXT                                                      │   │
│  │  hybrid_score          FLOAT                                                     │   │
│  │                                                                                  │   │
│  │  -- Safety Gates Status (NEW)                                                    │   │
│  │  budget_status         TEXT                    -- OK, RATE_LIMIT, BUDGET_LIMIT   │   │
│  │  cache_hits            INT                     -- how many agents used cache     │   │
│  │  source_health         JSONB                   -- health of each source          │   │
│  │  validation_failures   INT                     -- how many responses sanitized   │   │
│  │                                                                                  │   │
│  │  -- Execution                                                                    │   │
│  │  executed_strategy     TEXT                    -- rules, gpt, hybrid, none       │   │
│  │  executed_signal       TEXT                                                      │   │
│  │                                                                                  │   │
│  │  -- Outcome (filled later)                                                       │   │
│  │  outcome_pips          FLOAT                                                     │   │
│  │  outcome_result        TEXT                    -- win, loss, breakeven           │   │
│  │                                                                                  │   │
│  │  created_at            TIMESTAMPTZ DEFAULT NOW()                                 │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              agent_signals                                       │   │
│  │                                                                                  │   │
│  │  id                    UUID PRIMARY KEY                                          │   │
│  │  parallel_decision_id  UUID REFERENCES parallel_decisions(id)                    │   │
│  │  ts_utc                TIMESTAMPTZ NOT NULL                                      │   │
│  │  symbol                TEXT NOT NULL                                             │   │
│  │  agent_name            TEXT NOT NULL           -- TechnicalAgent, MacroAgent...  │   │
│  │  agent_version         TEXT                                                      │   │
│  │                                                                                  │   │
│  │  signal                TEXT NOT NULL           -- LONG, SHORT, HOLD              │   │
│  │  confidence            TEXT NOT NULL           -- LOW, MEDIUM, HIGH (enum!)      │   │
│  │  confidence_weight     FLOAT                   -- 0.3, 0.6, 0.9 (from code)      │   │
│  │  agent_weight          FLOAT                   -- 0.30, 0.25, etc                │   │
│  │  reasoning             TEXT                                                      │   │
│  │  flags                 TEXT[]                                                    │   │
│  │                                                                                  │   │
│  │  -- Safety status                                                                │   │
│  │  was_cached            BOOLEAN DEFAULT FALSE                                     │   │
│  │  was_sanitized         BOOLEAN DEFAULT FALSE                                     │   │
│  │  source_was_stale      BOOLEAN DEFAULT FALSE                                     │   │
│  │                                                                                  │   │
│  │  raw_input             JSONB                   -- what agent received            │   │
│  │  raw_output            JSONB                   -- what LLM returned              │   │
│  │                                                                                  │   │
│  │  created_at            TIMESTAMPTZ DEFAULT NOW()                                 │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                            source_health_log (NEW)                               │   │
│  │                                                                                  │   │
│  │  id                    UUID PRIMARY KEY                                          │   │
│  │  ts_utc                TIMESTAMPTZ NOT NULL                                      │   │
│  │  source_name           TEXT NOT NULL           -- ibkr_ohlcv, economic_cal, etc  │   │
│  │  is_available          BOOLEAN                                                   │   │
│  │  staleness_minutes     FLOAT                                                     │   │
│  │  coverage              FLOAT                                                     │   │
│  │  error_message         TEXT                                                      │   │
│  │                                                                                  │   │
│  │  created_at            TIMESTAMPTZ DEFAULT NOW()                                 │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                            budget_usage_log (NEW)                                │   │
│  │                                                                                  │   │
│  │  id                    UUID PRIMARY KEY                                          │   │
│  │  ts_utc                TIMESTAMPTZ NOT NULL                                      │   │
│  │  calls_this_minute     INT                                                       │   │
│  │  cost_today            FLOAT                                                     │   │
│  │  budget_status         TEXT                    -- OK, RATE_LIMIT, BUDGET_LIMIT   │   │
│  │                                                                                  │   │
│  │  created_at            TIMESTAMPTZ DEFAULT NOW()                                 │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. File Structure (UPDATED)

```
app/
│
├── agents/
│   │
│   ├── ✅ KEEP AS-IS
│   │   ├── errors.py
│   │   └── state.py
│   │
│   ├── ⚠️ MODIFY
│   │   └── config.py                 (+30 lines: safety gate configs)
│   │
│   ├── 📦 MOVE TO legacy/
│   │   ├── runner.py
│   │   ├── gate.py
│   │   ├── openai_client.py
│   │   ├── orchestrator.py
│   │   └── schemas.py
│   │
│   ├── llm/                          ➕ NEW
│   │   ├── __init__.py
│   │   ├── base_agent.py             (~100 lines)
│   │   ├── technical_agent.py        (~120 lines)
│   │   ├── macro_agent.py            (~120 lines)
│   │   ├── sentiment_agent.py        (~120 lines)
│   │   ├── correlation_agent.py      (~120 lines)
│   │   └── risk_agent.py             (~120 lines)
│   │
│   ├── prompts/                      ➕ NEW
│   │   ├── __init__.py
│   │   ├── technical.py
│   │   ├── macro.py
│   │   ├── sentiment.py
│   │   ├── correlation.py
│   │   └── risk.py
│   │
│   ├── safety/                       ➕ NEW (CRITICAL)
│   │   ├── __init__.py               (~10 lines)
│   │   ├── budget_limiter.py         (~80 lines)
│   │   ├── agent_cache.py            (~60 lines)
│   │   ├── response_validator.py     (~100 lines)
│   │   └── source_health.py          (~80 lines)
│   │
│   ├── aggregator.py                 ➕ NEW (~150 lines)
│   └── parallel_runner.py            ➕ NEW (~250 lines)
│
├── data_sources/                     ➕ NEW DIRECTORY
│   ├── __init__.py
│   ├── base_source.py                (~50 lines)
│   ├── economic_calendar.py          (~150 lines)
│   ├── cot_reports.py                (~150 lines)
│   ├── dxy_index.py                  (~100 lines)
│   └── health_monitor.py             ➕ NEW (~100 lines)
│
├── models/
│   ├── ⚠️ MODIFY
│   │   └── signal_preview.py         (+20 lines)
│   │
│   └── ➕ NEW FILES
│       ├── confidence.py             (~20 lines) - ConfidenceLevel enum
│       ├── agent_signal.py           (~60 lines)
│       ├── aggregated_decision.py    (~50 lines)
│       ├── parallel_decision.py      (~80 lines)
│       └── source_health.py          (~40 lines)
│
├── comparison/                       ➕ NEW DIRECTORY
│   ├── __init__.py
│   ├── parallel_executor.py          (~150 lines)
│   ├── strategy_selector.py          (~80 lines)
│   └── analytics.py                  (~200 lines)
│
└── ...


SUMMARY v2.1:
─────────────────────────────────
New files:        ~30 files
New lines:        ~2,800 lines (+300 for safety)
Modified files:   ~6 files
Modified lines:   ~450 lines
Moved to legacy:  ~5 files
─────────────────────────────────
```

---

## 6. ENV Configuration (UPDATED)

```bash
# ═══════════════════════════════════════════════════════════════
# AGENTS CONFIGURATION v2.1
# ═══════════════════════════════════════════════════════════════

# Core
AGENTS_ENABLED=true
ACTIVE_STRATEGY=rules              # rules | gpt | hybrid

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SEC=5.0
OPENAI_MAX_RETRIES=2

# ─────────────────────────────────────────────────────────────────
# SAFETY GATES (NEW)
# ─────────────────────────────────────────────────────────────────

# Budget Limiter
AGENTS_MAX_CALLS_PER_MINUTE=30
AGENTS_MAX_COST_PER_DAY=5.0

# Agent Cache
AGENTS_CACHE_ENABLED=true
AGENTS_CACHE_TTL_MINUTES=15

# Response Validator
AGENTS_VALIDATE_RESPONSES=true
AGENTS_BLOCK_FORBIDDEN_CONTENT=true

# Source Health
AGENTS_BLOCK_ON_STALE_OHLCV=true
AGENTS_MAX_OHLCV_STALENESS_MIN=5
AGENTS_MAX_CALENDAR_STALENESS_MIN=60
AGENTS_MAX_COT_STALENESS_DAYS=7
AGENTS_MAX_DXY_STALENESS_MIN=15
```

---

*Schema Version: 2.1*
*Created: 2025-12-21*
*Updated: Added Safety Gates Layer*
