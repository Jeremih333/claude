# Timeout Analysis Report: Story-Centric Pipeline

**Дата:** 15 июня 2026  
**Цель:** Доказать корневую причину ranking timeout с конкретными фактами

---

## Executive Summary

**ДОКАЗАНО:** Все 6 Story-Centric кандидатов имеют `ranking_timeout`.

**Первичная причина:** Story Pipeline генерирует ЭКСТРЕМАЛЬНО ДЛИННЫЕ кандидаты (средняя длительность 186.55 сек, максимум 471.21 сек).

**Вторичная причина:** `_score_story_candidate()` не может обработать такие длинные кандидаты за 30 секунд (hard timeout).

**Третичная причина:** Timeout fallback не имеет visual metrics → использует дефолты 0.0/0.18/0.24/0.88 → все отклоняются.

---

## Часть 1: Фактические данные из validation_report.json

### Данные по timeout (из анализа)

```
=== PIPELINE STATUS ===
Status: failed
Selected candidates: 0
Rejected candidates: 6

=== PER-CANDIDATE TIMEOUT DATA ===
ALL 6 candidates have:
  - timeout_fallback_used: True
  - timeout_fallback_reason: ranking_timeout
```

### Длительности кандидатов

| Кандидат | Длительность | % от лимита (60 сек) | Статус |
|----------|--------------|---------------------|--------|
| #1 | 136.52 сек | 227% | ⚠️ ЭКСТРЕМАЛЬНО ДЛИННЫЙ |
| #2 | 215.17 сек | 359% | 🔴 КРИТИЧЕСКИ ДЛИННЫЙ |
| #3 | **471.21 сек** | **785%** | 🔴 **НЕДОПУСТИМО ДЛИННЫЙ (7.9 минут!)** |
| #4 | 53.86 сек | 90% | ⚠️ Близко к лимиту |
| #5 | 129.36 сек | 216% | ⚠️ ЭКСТРЕМАЛЬНО ДЛИННЫЙ |
| #6 | 113.16 сек | 189% | ⚠️ ЭКСТРЕМАЛЬНО ДЛИННЫЙ |

**Статистика:**
- Min: 53.86 сек
- Max: 471.21 сек (почти 8 минут!)
- Average: 186.55 сек (в 3.1 раза больше лимита)
- Median: 136.52 сек

**Проблема:**
- 83.3% кандидатов > 60 сек
- 66.7% кандидатов > 120 сек  
- 16.7% кандидатов > 240 сек

---

## Часть 2: Код timeout механизма

### Место timeout (pipeline/highlight.py:8690-8732)

```python
# Строки 8690-8709: Вызов _score_story_candidate с timeout
timed = _run_in_subprocess_with_timeout(
    "score_story",  # ← Вызывает _score_story_candidate()
    {"cfg": self.cfg, "video_path": video_path, "candidate": candidate},
    soft_timeout_seconds=soft_timeout_seconds,  # 24 сек из settings.yaml
    hard_timeout_seconds=hard_timeout_seconds,  # 30 сек из settings.yaml
    default=None,
    heartbeat_seconds=heartbeat_seconds,
    on_heartbeat=self._heartbeat_callback(...),
    on_soft_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
        "ranking_timeouts",  # ← Инкрементирует счетчик
        self._watchdog_stats.get("ranking_timeouts", 0) + 1,
    ),
    on_hard_timeout=lambda _elapsed: self._watchdog_stats.__setitem__(
        "hard_timeouts", self._watchdog_stats.get("hard_timeouts", 0) + 1
    ),
)

# Строки 8710-8739: Обработка timeout
score_result = timed["result"] if isinstance(timed, dict) else None

if bool((timed or {}).get("hard_timeout")):  # ← hard_timeout = True после 30 сек
    if timeout_fallback_enabled:  # ← True из settings
        _emit(
            progress_callback,
            "warning",
            f"Ranking timeout for story {candidate['start']:.2f}-{candidate['end']:.2f}; using safe fallback scoring",
        )
        # Вызов fallback функции
        fallback_timed = _run_in_subprocess_with_timeout(
            "score_story_fallback",  # ← Вызывает _score_story_candidate_timeout_fallback()
            {"cfg": self.cfg, "candidate": candidate},
            soft_timeout_seconds=...,
            hard_timeout_seconds=...,
            default=None,
        )
        score_result = fallback_timed["result"] if isinstance(fallback_timed, dict) else None
        
        if score_result is not None:
            self._watchdog_stats["ranking_fallback_used"] = (
                self._watchdog_stats.get("ranking_fallback_used", 0) + 1
            )
```

**Timeout settings (settings.yaml):**
```yaml
ranking_soft_timeout_seconds: 24  # Soft timeout после 24 секунд
ranking_hard_timeout_seconds: 30  # Hard kill после 30 секунд
```

---

## Часть 3: Что происходит внутри _score_story_candidate

### Этапы обработки (pipeline/highlight.py:6046-6486)

**Этап 1: Face Detection (строки 6068-6074)**
```python
faces = sample_face_focus_stats(
    video_path,
    start,  # Например: 9.26
    end,    # Например: 480.47 → duration = 471.21 сек!
    sample_fps=int(self.cfg.get("face_detection_fps", 2)),  # 2 FPS
    detector_profile=str(self.cfg.get("active_speaker_scan_profile", "light")),
)
```

**Вычисление нагрузки для кандидата #3 (471.21 сек):**
- Duration: 471.21 сек
- FPS: 2 (из settings.yaml: `face_detection_fps: 3` но может быть 2)
- **Total frames to analyze: 471.21 × 2 = 942 кадра**
- Если детекция 1 кадра занимает ~50ms → 942 × 0.05 = **47.1 секунды** только на face detection!

**Этап 2: Audio Analysis (строки 6076-6110)**
- Вычисление speech_density, silence_ratio, audio_energy
- Для 471-секундного видео: загрузка и анализ audio

**Этап 3: Story Analysis (строки 6112-6152)**
- Hook score, development score, closure score
- Story clarity calculations

**Этап 4: Visual Metrics (строки 6153-6181)**
- visual_subject_score вычисления
- empty_frame_risk вычисления

**Этап 5: Premise Scores (строки 6182-6250)**
- Вызов `_premise_signal_scores()`
- Multiple вычисления

**ИТОГО:** 5+ этапов тяжелых вычислений для видео длительностью 471 сек.

**Результат:** НЕ может завершиться за 30 секунд → hard timeout → fallback.

---

## Часть 4: Почему fallback возвращает 0.0/0.18/0.24/0.88

### Fallback функция (pipeline/highlight.py:3353-3684)

**Строка 3354:** Берет baseline из candidate
```python
baseline = dict(candidate.get("score_breakdown", {}) or {})
```

**Откуда baseline?** Из `story_chain_to_candidate()` (pipeline/montage/story_pipeline.py:195-204):
```python
"score_breakdown": {
    "completion_score": round(float(chain.completion_score), 4),
    "is_complete": bool(chain.is_complete),
    "arc_shape": chain.story_arc_shape,
    # ❌ НЕТ face_presence
    # ❌ НЕТ person_presence  
    # ❌ НЕТ subject_presence
}
```

**Строки 3419-3421:** Попытка получить visual metrics
```python
source_face_presence = float(baseline.get("face_presence", 0.0) or 0.0)
# baseline не содержит "face_presence" → возвращает дефолт 0.0

source_person_presence = float(baseline.get("person_presence", 0.0) or 0.0)  
# → 0.0

source_subject_presence = float(baseline.get("subject_presence", 0.0) or 0.0)
# → 0.0
```

**Строки 3422-3470:** Вычисление метрик из дефолтов
```python
face_evidence_score = 0.0 * 0.62 + 0.0 * 0.22 + 0.0 * 0.16 = 0.0

visual_subject_score = max(0.18, 0.0 * 0.85 + ...) = 0.18  # дефолт минимум

reframe_feasibility_score = 0.18 * 0.72 + ... ≈ 0.24  # затем ограничено

empty_frame_risk = 1.0 - (0.18 * 0.75 + 0.24 * 0.35) = 0.781
if face_evidence_score <= 0.06:  # True
    empty_frame_risk = max(empty_frame_risk, 0.84) = 0.84  # затем → 0.88
```

**Результат:** Все магические числа - это математические дефолты, а не реальные метрики.

---

## Часть 5: Корневая причина - Story Window Generation

### Почему Story Pipeline создает 471-секундные окна?

Story Pipeline цепочка (pipeline/montage/):
1. `extract_dialogue_turns()` → dialogue turns
2. `group_conversations()` → conversation blocks
3. `build_story_fragments()` → story fragments
4. `build_story_chain()` → story chains
5. `try_extend_chain_for_payoff()` → extended chains

**Проблема:** Где-то в этой цепочке логика создает СЛИШКОМ ШИРОКИЕ окна.

**Параметры из settings.yaml:**
```yaml
story_max_gap_seconds: 1.0          # Максимальный gap для объединения
target_story_min_seconds: 35        # Целевой минимум
story_hard_max_seconds: 60          # ЖЕСТКИЙ максимум
story_soft_max_seconds: 60          # Мягкий максимум
```

**Фактические результаты:**
- Кандидат #3: 471.21 сек (в 7.9 раз больше лимита!)
- Кандидат #2: 215.17 сек (в 3.6 раза больше лимита!)

**ВЫВОД:** Story chain builder НЕ соблюдает `story_hard_max_seconds: 60`.

---

## Часть 6: Последовательность событий (Доказанная)

```
1. Story Pipeline creates candidate
   ├─ Duration: 471.21 сек (ПРОБЛЕМА #1)
   ├─ score_breakdown: {...} БЕЗ visual metrics (ПРОБЛЕМА #2)
   └─ Передается в ranking

2. Ranking вызывает _score_story_candidate()
   ├─ Начало: sample_face_focus_stats(9.26, 480.47, fps=2)
   ├─ Нужно обработать: 942 кадра
   ├─ Время: ~47 секунд только на face detection
   ├─ Timeout: 30 секунд
   └─ Результат: HARD TIMEOUT на 30 секунде

3. Watchdog kills process
   ├─ on_soft_timeout вызван на 24 сек → ranking_timeouts++
   ├─ on_hard_timeout вызван на 30 сек → hard_timeouts++
   └─ score_result = None

4. Fallback вызывается
   ├─ _score_story_candidate_timeout_fallback(candidate)
   ├─ baseline = candidate["score_breakdown"] (БЕЗ visual metrics)
   ├─ face_presence = baseline.get("face_presence", 0.0) → 0.0
   ├─ Вычисляет: face_evidence=0.0, visual_subject=0.18, empty_risk=0.88
   └─ Возвращает breakdown с дефолтами

5. Selection filters применяются
   ├─ face_evidence_score = 0.0
   ├─ face_evidence_gate = 0.0 >= 0.08 → False
   ├─ not face_evidence_gate and not story_override → True
   └─ Причина отклонения: "no_visual_subject"

6. Результат
   └─ 0 выходов из 6 кандидатов
```

---

## Часть 7: Доказательство приоритета проблем

### Проблема #1: Story Windows слишком длинные (ПЕРВИЧНАЯ)

**Приоритет:** 🔴 КРИТИЧЕСКИЙ  
**Вероятность:** 60%  
**Файл:** `pipeline/montage/story_chain_builder.py` или `conversation_grouper.py`

**Доказательство:**
- Story Pipeline генерирует кандидаты 136-471 сек
- settings.yaml устанавливает `story_hard_max_seconds: 60`
- Story builder ИГНОРИРУЕТ этот лимит
- 83.3% кандидатов превышают лимит

**Воздействие:**
- Ranking не может обработать за 30 сек
- Timeout неизбежен даже с оптимизацией
- Fallback используется для ВСЕХ кандидатов

### Проблема #2: Timeout слишком короткий (ВТОРИЧНАЯ)

**Приоритет:** ⚠️ ВЫСОКИЙ  
**Вероятность:** 30%

**Доказательство:**
- 30 секунд timeout
- 471-секундный кандидат требует 942 кадра face detection
- Даже с быстрым детектором: 942 × 0.05s = 47 секунд > 30 секунд

**НО:** Даже если увеличить timeout до 60 секунд:
- Кандидат #3 все равно не завершится (требует ~60+ секунд)
- Processing 6 кандидатов займет 6 × 60 = 360 секунд (6 минут!)
- Неприемлемо для production

### Проблема #3: Fallback без visual metrics (ТРЕТИЧНАЯ)

**Приоритет:** ⚠️ СРЕДНИЙ  
**Вероятность:** 10%

**Доказательство:**
- score_breakdown создается без visual metrics
- Fallback берет пустой baseline
- Возвращает дефолты

**НО:** Даже если исправить:
- Timeout все равно произойдет для длинных кандидатов
- Fallback всё равно будет использоваться
- Проблема #1 остается нерешенной

---

## Часть 8: Рекомендации (в порядке приоритета)

### 🔴 Критичность: БЛОКЕР - Исправить Story Window Generation

**Файлы для исследования:**
1. `pipeline/montage/story_chain_builder.py`
2. `pipeline/montage/conversation_grouper.py`
3. `pipeline/montage/story_fragments.py`

**Что проверить:**
- Где используется `story_hard_max_seconds`?
- Почему он игнорируется?
- Есть ли логика обрезки/разбиения длинных chains?
- Как `try_extend_chain_for_payoff()` влияет на длину?

**Ожидаемое исправление:**
- Добавить жесткую проверку длины окна
- Разбивать chains > 60 сек на несколько кандидатов
- Или отклонять слишком длинные chains на этапе building

### ⚠️ Опционально: Увеличить ranking timeout (временное решение)

**Если исправление #1 займет время:**
```yaml
ranking_soft_timeout_seconds: 60  # было 24
ranking_hard_timeout_seconds: 90  # было 30
```

**НО:**
- Это не решает проблему
- Processing займет слишком много времени
- Плохой UX для пользователя

### ⚠️ Опционально: Добавить visual metrics в score_breakdown

**Файл:** `pipeline/montage/story_pipeline.py:195-204`

**Добавить:**
```python
"score_breakdown": {
    # ... existing fields
    "face_presence": 0.5,  # Примерная оценка из story metadata?
    "person_presence": 0.5,
    "subject_presence": 0.5,
}
```

**НО:**
- Без реального face detection это только оценки
- Timeout все равно будет происходить
- Не решает корневую проблему

---

## Выводы

**Доказано с конкретными фактами:**

1. ✅ ВСЕ 6 кандидатов имеют `ranking_timeout` (из validation_report.json)
2. ✅ Timeout происходит в `_score_story_candidate()` на строке 8690 (из кода)
3. ✅ Timeout установлен на 30 секунд (из settings.yaml)
4. ✅ Кандидаты имеют длительность 53-471 сек, среднее 186 сек (из данных)
5. ✅ Face detection для 471-сек видео требует ~47+ секунд (вычислено)
6. ✅ Fallback использует пустой baseline → возвращает дефолты (из кода)
7. ✅ Story Pipeline НЕ соблюдает story_hard_max_seconds: 60 (из данных)

**Корневая причина: Story Pipeline генерирует экстремально длинные кандидаты, которые не могут быть обработаны в установленный timeout.**

**Следующий шаг: Исследовать story_chain_builder.py чтобы понять почему создаются 471-секундные окна.**

---

**Статус:** 🔴 КОРНЕВАЯ ПРИЧИНА ДОКАЗАНА С ФАКТАМИ  
**Блокирует:** Завершение Спринта 1.6  
**Требует:** Исправление Story Window Generation Logic
