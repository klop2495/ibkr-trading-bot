
### 7. Полный тест всех 48 снапшотов на NaN/Inf
- **Статус**: ✅ ВСЕ OK
- ALL 48 SNAPSHOTS OK - No NaN/Inf found

### 8. Тест service.process() с debug repo (warmup_bars_min=300)
- **Статус**: ✅ ВСТАВКА РАБОТАЕТ
- SUCCESS: warmup=False, snapshots=48
- Но warmup=False - почему?

---

## Новая гипотеза: warmup условие не выполняется


### 9. Проверка количества баров по таймфреймам
- **Статус**: ❌ НАЙДЕНА ПРИЧИНА
- M15: 447 баров ✅
- H1: 329 баров ✅  
- H4: 208 баров ❌ (меньше 300!)

## ПРИЧИНА НАЙДЕНА

**warmup_bars_min=300**, но H4 при 30-дневном лимите возвращает только ~208 баров.
Warmup condition никогда не выполнится!

## РЕШЕНИЕ

Уменьшить warmup_bars_min до 200 в bot_settings.

---

## РЕШЕНИЕ НАЙДЕНО И ПРИМЕНЕНО

### Проблема 1: warmup_bars_min=300
- H4 возвращает только ~208 баров (30 дней лимит IB)
- **Исправление**: Уменьшили warmup_bars_min до 200 в bot_settings

### Проблема 2: spread=NaN
- `fetch_spread()` иногда возвращает NaN
- `spread or 0.0` не ловит NaN (NaN is truthy!)
- **Исправление** в service.py:
```python
# Было:
spread=spread or 0.0,

# Стало:
spread=0.0 if spread is None or (isinstance(spread, float) and math.isnan(spread)) else spread,
```

### Результат
```
signal_gen generated=16 warmup_ready=True errors=0 mode=active
```

Бот стабильно генерирует 16 сигналов каждый тик!

---

## Файлы изменены
1. `/root/ibkr-trading-bot/app/market_data/service.py` - добавлен import math, исправлена обработка spread
2. `/root/ibkr-trading-bot/app/market_data/ibkr_fetcher.py` - переподключение к IB Gateway
3. БД: bot_settings.warmup_bars_min = 200
