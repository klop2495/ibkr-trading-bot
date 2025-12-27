# LLM Orchestrator Contract (v1)

Scope: фиксирует контракт между LLM-агентами (data producers), оркестратором и hybrid-раннером. Исходник: `spec_v1` + AI_RULES (LLM не считает числа, только флаги/наблюдения).

## Агент (data producer)
- Возвращает только: `agent_name`, `data_status` (`ok|missing|stale`), `flags` (категории), `observations` (категорические признаки), `source_health` (mock/available/staleness) при наличии.
- Не возвращает и не вычисляет: итоговый сигнал, score, веса, цены/SL/TP/лоты/маржу.
- При `data_status=missing` → ABSTAIN (фактически `signal=HOLD`, `confidence_float=0`), флаги могут содержать причину (`NO_DATA`, `MOCK_MODE`).

## Оркестратор
Вход: список агентских отчётов (как выше).  
Выход:
- `llm_score ∈ [-1..1]` — детерминированно вычисляется внутри оркестратора из flags/observations/weights.
- `llm_signal` (`LONG|SHORT|HOLD`) — из `llm_score` и порога `LLM_THRESHOLD`.
- `data_status` агрегированный: если хоть один `missing|stale` без подтверждённых real-данных → `missing` (см. degrade).
- `risk_veto: bool` и `risk_veto_reason` — только при подтверждённых risk flags и реальных данных, иначе false.

Политика `data_status=MISSING`: degrade-to-rules (default для стабильной торговли):  
`llm_score=0`, `llm_signal=HOLD`, `risk_veto=false`.

## Hybrid
- Формула: `hybrid_score = w_rules * rules_score + w_llm * llm_score`. Веса задаются в config (по умолчанию 0.6/0.4).  
- Порог: `HYBRID_THRESHOLD` определяет LONG/SHORT, иначе HOLD.
- `entry_triggered=false` → `trade_allowed=false` (gate до исполнения).

## Veto
Если оркестратор выставил `risk_veto=true` (на реальных данных), исполнение должно трактовать это как `trade_allowed=false`.

## Idempotency/Fail-safe
- Любая ошибка агентов/оркестратора → `llm_score=0`, `llm_signal=HOLD`, `risk_veto=false`.
- Решение об исполнении идёт через hybrid + downstream risk/execution gates (idempotency per decision_id).
