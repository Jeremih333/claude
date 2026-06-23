# Profiling Implementation Report

**Дата**: 2026-06-15  
**Статус**: ✅ ЗАВЕРШЕНО

## Резюме

Добавлена **timing instrumentation** в критические функции pipeline для детального профилирования производительности story-режима.

---

## Реализованные изменения

### 1. **Pipeline.find_highlights()** ✅
**Файл**: `pipeline/highlight.py`  
**Строки**: ~2150-2250

**Добавленные timing точки**:
- `timing_start` - начало выполнения
- `timing_story_scoring` - время скоринга всех story-кандидатов
- `timing_story_ranking` - время ранжирования кандидатов
- `timing_legacy_fallback` - время работы legacy-режима (если активен)
- `timing_montage_generation` - время генерации монтажа
- `timing_end` - конец выполнения

**Формат вывода**:
```python
{
    'highlights': [...],
    'mode': 'story',
    'profiling': {
        'story_scoring_sec': 45.2,
        'story_ranking_sec': 0.3,
        'montage_generation_sec': 12.5,
        'total_sec': 58.0
    }
}
```

---

### 2. **Pipeline._score_story_candidate()** ✅
**Файл**: `pipeline/highlight.py`  
**Строки**: ~1730-1900

**Добавленные timing точки**:
- `_timings['init']` - инициализация
- `_timings['face_detection']` - детекция лиц (если включена)
- `_timings['video_metrics']` - расчёт метрик видео
- `_timings['premise_scoring']` - скоринг premise через SmolVLM

**Интеграция в breakdown**:
```python
breakdown = {
    'score': score,
    'weights': {...},
    'components': {...},
    '_timings': {
        'init_ms': 5,
        'face_detection_ms': 1200,
        'video_metrics_ms': 300,
        'premise_scoring_ms': 8500
    }
}
```

---

### 3. **Pipeline._run_in_subprocess_with_timeout()** ✅
**Файл**: `pipeline/highlight.py`  
**Строки**: ~1560-1640

**Добавленные метрики**:
- `watchdog_start` / `watchdog_end` - время работы watchdog
- `actual_runtime_sec` - реальное время выполнения функции
- `timeout_limit_sec` - лимит таймаута
- `timed_out` - флаг превышения таймаута

**Вывод**:
```python
{
    'result': {...},
    '_watchdog': {
        'timeout_limit_sec': 600,
        'actual_runtime_sec': 542.3,
        'timed_out': False
    }
}
```

---

## Использование

### Запуск с профилированием:
```bash
python main.py episode01_test.avi --story-mode --debug
```

### Анализ результатов:
1. **Top-level timing** - смотреть в `result['profiling']`
2. **Per-candidate timing** - смотреть в `breakdown['_timings']` каждого кандидата
3. **Watchdog stats** - смотреть в `result['_watchdog']`

---

## Диагностика проблем

### Если story-режим медленный:
1. Проверить `story_scoring_sec` - основное время на скоринг
2. Если высокое - смотреть `premise_scoring_ms` в breakdown
3. Если premise_scoring > 5000ms - проблема в SmolVLM
4. Проверить `face_detection_ms` - может быть долгим на HD-видео

### Если таймауты:
1. Смотреть `_watchdog.timed_out`
2. Сравнить `actual_runtime_sec` с `timeout_limit_sec`
3. Увеличить `story_hard_max_seconds` в конфиге

---

## Следующие шаги

1. ✅ **Timing instrumentation** - реализовано
2. ⏳ **Запуск production теста** - нужно запустить на реальном эпизоде
3. ⏳ **Анализ узких мест** - после получения данных
4. ⏳ **Оптимизация** - на основе результатов профилирования

---

## Технические детали

### Watchdog mechanism:
- Использует `multiprocessing.Process` + `Queue`
- Таймаут контролируется через `process.join(timeout)`
- Если `process.is_alive()` после join - процесс убивается

### Story scoring flow:
```
find_highlights()
  ↓
  [Story Mode]
  ↓
  for each candidate:
    _score_story_candidate()
      ↓
      _run_in_subprocess_with_timeout()
        ↓
        [Face detection + Video metrics + Premise scoring]
  ↓
  Ranking + Selection
  ↓
  Montage generation
```

### Конфигурация таймаутов:
- `story_hard_max_seconds` (default: 600) - лимит на один кандидат
- `max_duration_sec` - лимит длительности итогового шорта
- Watchdog timeout = `story_hard_max_seconds`

---

## Статус

**✅ Все изменения применены и валидированы**

Код готов к production тестированию. Следующий шаг - запустить на реальном эпизоде и собрать профилировочные данные.
