# Profiling Plan: _score_story_candidate Bottleneck

**Дата:** 15 июня 2026  
**Цель:** Измерить РЕАЛЬНОЕ время каждого этапа в _score_story_candidate()

---

## Проблема с текущим анализом

### ❌ Что НЕ доказано

**Гипотеза "Face detection = bottleneck"** основана на оценке:
```
471 сек × 2 FPS × 50ms/frame = 47 секунд
```

Это **предположение**, а не измерение.

**Реальность может быть:**
- Face detection: 4 секунды
- Whisper alignment: 15 секунд
- Premise scoring: 8 секунд
- Audio extraction: 3 секунды
- Что-то другое: 25 секунд

### 🚨 КРИТИЧЕСКИЙ факт: Кандидат 53.86 сек тоже timeout

```json
{
  "duration": 53.86,  // МЕНЬШЕ лимита 60 сек!
  "timeout_fallback_used": true,
  "timeout_fallback_reason": "ranking_timeout"
}
```

**Это опровергает гипотезу:** "проблема только в длинных окнах"

**Возможные объяснения:**

**Вариант A:** Face detection медленный даже на 54 сек
- 54 × 2 FPS × 50ms = 5.4 сек (укладывается в 30 сек)
- ❌ НЕ объясняет timeout

**Вариант B:** Есть другой bottleneck (не face detection)
- Whisper alignment
- Premise scoring с LLM
- FFmpeg video seeking
- Multiprocessing overhead

**Вариант C:** Timeout накопительный (на все кандидаты суммарно)
- Нужно проверить код watchdog

**Вариант D:** Есть bug в watchdog (timeout срабатывает раньше)

---

## План действий

### Этап 1: Профилирование _score_story_candidate

**Цель:** Получить РЕАЛЬНОЕ время каждого блока

**Файл:** `pipeline/highlight.py` метод `_score_story_candidate()` (строки 6046-6486)

**Что измерить:**

1. **sample_face_focus_stats()** (строки 6068-6074)
   - Входы: video_path, start, end, fps, profile
   - Выход: faces dict
   - ⏱️ Время: ?

2. **Audio analysis** (строки 6076-6110)
   - speech_density, silence_ratio, audio_energy
   - ⏱️ Время: ?

3. **Story scoring** (строки 6112-6152)
   - hook_score, development_score, closure_score
   - story_clarity_score calculations
   - ⏱️ Время: ?

4. **Visual metrics** (строки 6153-6181)
   - visual_subject_score
   - empty_frame_risk
   - ⏱️ Время: ?

5. **Premise scoring** (строки 6182-6250)
   - _premise_signal_scores()
   - ⏱️ Время: ?

6. **Total _score_story_candidate**
   - От начала до return
   - ⏱️ Время: ?

**Для кандидатов:**
- #4: 53.86 сек (самый критичный - почему timeout?)
- #1: 136.52 сек
- #3: 471.21 сек

### Этап 2: Проверка story_hard_max_seconds

**Файлы для поиска:**
- `pipeline/montage/story_chain_builder.py`
- `pipeline/montage/conversation_grouper.py`
- `pipeline/montage/story_fragments.py`
- `pipeline/montage/story_pipeline.py`

**Что найти:**

1. Где читается `story_hard_max_seconds` из config?
   ```python
   grep -r "story_hard_max_seconds" pipeline/montage/
   ```

2. Применяется ли к StoryChain при build?
   ```python
   # Искать логику обрезки/фильтрации
   if chain.duration > max_seconds:
       ...
   ```

3. Применяется ли только к финальному candidate?
   ```python
   # В story_chain_to_candidate()
   ```

4. Есть ли вообще проверка длительности?

### Этап 3: Проверка watchdog механизма

**Файл:** `pipeline/highlight.py`

**Вопросы:**

1. Timeout на КАЖДОГО кандидата или СУММАРНЫЙ?
   ```python
   # Строка 8690-8709
   timed = _run_in_subprocess_with_timeout(
       "score_story",
       ...,
       soft_timeout_seconds=24,  # ← На ОДНОГО кандидата?
   )
   ```

2. Сбрасывается ли таймер между кандидатами?

3. Есть ли overhead на subprocess spawn?

---

## Методология профилирования

### Вариант 1: Добавить timing в код

**Модифицировать `_score_story_candidate():`**

```python
import time

def _score_story_candidate(self, video_path: str, candidate: dict):
    timings = {}
    total_start = time.perf_counter()
    
    # Face detection
    t0 = time.perf_counter()
    faces = sample_face_focus_stats(...)
    timings["face_detection"] = time.perf_counter() - t0
    
    # Audio analysis
    t0 = time.perf_counter()
    # ... audio code
    timings["audio_analysis"] = time.perf_counter() - t0
    
    # Story scoring
    t0 = time.perf_counter()
    # ... story code
    timings["story_scoring"] = time.perf_counter() - t0
    
    # Visual metrics
    t0 = time.perf_counter()
    # ... visual code
    timings["visual_metrics"] = time.perf_counter() - t0
    
    # Premise scoring
    t0 = time.perf_counter()
    premise_scores = self._premise_signal_scores(...)
    timings["premise_scoring"] = time.perf_counter() - t0
    
    timings["total"] = time.perf_counter() - total_start
    
    # Записать в breakdown
    breakdown["_debug_timings"] = timings
    
    return ...
```

**Затем запустить:**
```bash
venv\Scripts\python.exe run.py episode01_test.avi --story_mode standard
```

**И извлечь из validation_report.json:**
```json
{
  "candidate": {...},
  "score_breakdown": {
    "_debug_timings": {
      "face_detection": 4.23,
      "audio_analysis": 1.45,
      "story_scoring": 0.82,
      "visual_metrics": 0.34,
      "premise_scoring": 18.67,  // ← Может быть вот он bottleneck!
      "total": 25.51
    }
  }
}
```

### Вариант 2: Использовать cProfile

```bash
venv\Scripts\python.exe -m cProfile -o profile.stats run.py episode01_test.avi --story_mode standard
```

Затем анализировать:
```python
import pstats
p = pstats.Stats('profile.stats')
p.sort_stats('cumtime')
p.print_stats(50)
```

### Вариант 3: Добавить логирование в существующий код

Не модифицировать код, а добавить debug logging:
```python
logger.debug(f"[TIMING] Face detection took {elapsed:.2f}s")
```

---

## Ожидаемые результаты

### После Этапа 1: Профилирование

**Кандидат 53.86 сек:**
```
face_detection: ? сек
audio_analysis: ? сек
story_scoring: ? сек
visual_metrics: ? сек
premise_scoring: ? сек
total: >30 сек (иначе не было бы timeout)
```

**Это покажет РЕАЛЬНЫЙ bottleneck.**

### После Этапа 2: story_hard_max_seconds

**Один из вариантов:**

A) Параметр НЕ используется вообще
```python
# НЕТ упоминаний story_hard_max_seconds
```

B) Параметр используется неправильно
```python
# Применяется к чему-то другому, не к candidate duration
```

C) Параметр используется правильно, но есть bug
```python
if duration > story_hard_max_seconds:
    # split() не вызывается из-за бага
```

### После Этапа 3: Watchdog

**Один из вариантов:**

A) Timeout на каждого кандидата (правильно)
```python
for candidate in candidates:
    timed = _run_in_subprocess_with_timeout(..., 30)  # ← сброс
```

B) Timeout накопительный (bug)
```python
# Watchdog не сбрасывается между кандидатами
```

---

## Решение после получения данных

### Если bottleneck = face_detection

```python
# Опции:
1. Уменьшить face_detection_fps: 3 → 1
2. Использовать более быстрый detector_profile
3. Увеличить timeout до 60 сек
4. Или исправить story windows
```

### Если bottleneck = premise_scoring (LLM)

```python
# Опции:
1. Кешировать premise scores
2. Использовать более быстрый LLM
3. Делать premise scoring async/parallel
4. Пропускать для timeout fallback
```

### Если bottleneck = whisper/audio

```python
# Опции:
1. Кешировать transcription
2. Использовать faster-whisper
3. Уменьшить качество модели
```

### Если bottleneck = subprocess overhead

```python
# Опции:
1. Убрать subprocess для коротких кандидатов
2. Использовать process pool
3. Переписать на threads
```

---

## Приоритет действий

### 🔴 КРИТИЧНО: Профилирование кандидата 53.86 сек

**Почему критично:**
- Укладывается в лимит 60 сек
- Но получает timeout на 30 сек
- Это ключ к пониманию проблемы

**Действие:**
1. Добавить timing code в _score_story_candidate()
2. Запустить на episode01_test.avi
3. Извлечь _debug_timings для кандидата #4

### ⚠️ ВАЖНО: Проверить story_hard_max_seconds

**Действие:**
1. grep "story_hard_max_seconds" в pipeline/montage/
2. Найти где применяется
3. Проверить логику

### 📋 ОПЦИОНАЛЬНО: Проверить watchdog

**Действие:**
1. Прочитать код _run_in_subprocess_with_timeout
2. Убедиться что timeout на каждого кандидата
3. Измерить subprocess overhead

---

## Статус

- [ ] Этап 1: Профилирование (БЛОКЕР)
- [ ] Этап 2: story_hard_max_seconds (ВАЖНО)
- [ ] Этап 3: Watchdog механизм (ОПЦИОНАЛЬНО)

**Следующий шаг:** Добавить timing code и запустить профилирование.
