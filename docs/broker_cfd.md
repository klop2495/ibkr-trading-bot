# CFD FX (IBKR) — broker-first keying

Этот проект работает в режиме CFD-only. Истина по состоянию — брокер. Все сопоставления выполняются **только** по ключу инструмента:

```
<secType>:<conId>
```

Примеры: `CFD:12345`.

## Правила ключей

- `instrument_key(contract)` возвращает только `secType:conId`.
- `conId` обязателен: если отсутствует/0 — это **ошибка** и переход в safe-mode.
- `contract.symbol` и `localSymbol` используются **только для display/log**, но не для сопоставления.

## CFD-only guard

Если брокер возвращает любой `secType != CFD` **или** объект без `conId`:

- пишется `risk_event` (`UNEXPECTED_SECTYPE` или `MISSING_CONID`),
- включается safe-mode (`trading_enabled=false`),
- синхронизация с БД **не выполняется**.

## Recon правила

- BrokerStateService сопоставляет позиции/ордера/трейды только по `instrument_key`.
- DB матчит по `trades_history.meta.instrument_key`.
- Если у DB записи нет `meta.instrument_key` → safe-mode, никаких DB изменений.

## CFD contract factory

Используйте `create_cfd_fx_contract(ib, pair)`:

- `secType="CFD"`
- `exchange="SMART"`
- после qualify обязателен `conId`

## Проверка

```
python scripts/check_broker_vs_db.py
```

Результат:

- `exit 0` — consistent
- `exit 2` — mismatch
- `exit 3` — broker snapshot не доверяем
