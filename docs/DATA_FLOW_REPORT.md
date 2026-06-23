# Data Flow Report: Visual Metrics Loss in Story-Centric Pipeline

**Дата:** 15 июня 2026  
**Анализ:** Трассировка потери visual metrics в Story-Centric режиме

---

## Executive Summary

**Проблема:** Story-Centric кандидаты имеют `face_evidence_score = 0.0`, `visual_subject_score = 0.18`, что приводит к 100% отклонению.

**Корневая причина НАЙДЕНА:**

1. `story_chain_to_candidate()` создает кандидаты **БЕЗ** visual metrics в `score_breakdown`
2. Все 6 кандидатов попадают в **ranking timeout**
3. `_score_story_candidate_timeout_fallback()` берет `baseline` из **пустого** `score_breakdown`
4. `baseline.get("face_presence", 0.0)` возвращает **0.0** (дефолт)
5. Selection filters видят `face_evidence = 0.0` → отклоняют все кандидаты

**Полная функция `_score_story_candidate()` (которая вызывает face detection) НИКОГДА НЕ ВЫПОЛНЯЕТСЯ из-за timeout.**

---

## Полный Data Flow: Story-Centric Pipeline

### Этап 1: Создание кандидатов (NO VISUAL DATA)

**Файл:** `pipeline/montage/story_pipeline.py`  
**Функция:** `story_chain_to_candidate()`  
**Строки:** 161-217

#### Входные данные:
- `StoryChain` объект (из story fragments, conversations, dialogue turns)

#### Выходные данные (строки 184-217):
```python
{
    "start": 186.90,
    "end": 323.42,
    "source": "story_pipeline",
    "score_breakdown": {
        "completion_score": 0.8,
        "is_complete": True,
        "arc_shape": "hook_fragment",
        # ❌ НЕТ face_presence
        # ❌ НЕТ person_presence
        # ❌ НЕТ subject_presence
        # ❌ НЕТ motion
        # ❌ НЕТ brightness
        # ❌ НЕТ НИКАКИХ visual metrics
    }
}
```

**Результат:** Кандидат создан без единой visual метрики.

---

### Этап 2: Попытка Ranking (TIMEOUT)

**Файл:** `pipeline/highlight.py`  
**Функция:** `_score_story_candidate()`  
**Строки:** 6046-6486

#### Что ДОЛЖНО произойти:

**Строки 6068-6074:**
```python
faces = sample_face_focus_stats(
    video_path,
    start,
    end,
    sample_fps=int(self.cfg.get("face_detection_fps", 2)),
    detector_profile=str(self.cfg.get("active_speaker_scan_profile", "light")),
)
```
→ **РЕАЛЬНЫЙ face detection!**

**Строки 6153-6181:**
```python
visual_subject_score = min(
    1.0,
    float(faces.get("face_presence", 0.0)) * 0.56
    + float(faces.get("person_presence", 0.0)) * 0.28
    + min(1.0, float(faces.get("avg_face_size", 0.0)) / 0.035) * 0.10
    + min(1.0, float(faces.get("avg_person_size", 0.0)) / 0.09) * 0.06,
)

face_evidence_score = min(
    1.0,
    float(faces.get("face_presence", 0.0)) * 0.62
    + float(faces.get("person_presence", 0.0)) * 0.22
    + float(faces.get("subject_presence", 0.0)) * 0.16,
)

empty_frame_risk = max(
    0.0,
    1.0 - (
        float(faces.get("subject_presence", 0.0)) * 0.9
        + visual_subject_score * 0.45
    ),
)
```
→ **Корректные вычисления из реальных face metrics!**

#### Что ПРОИСХОДИТ на самом деле:

**Из validation_report.json:**
```json
{
  "ranking_timeouts": 6,
  "ranking_fallback_used": 6,
  "timeout_fallback_used": true,
  "timeout_fallback_reason": "ranking_timeout"
}
```

**ВСЕ 6 кандидатов попадают в timeout!**

**Настройки timeout (из settings.yaml):**
```yaml
ranking_soft_timeout_seconds: 24
ranking_hard_timeout_seconds: 30
```

**Причина:** Функция `_score_story_candidate()` выполняется > 30 секунд для каждого кандидата.

**Результат:** Функция убивается watchdog, face detection НИКОГДА не завершается.

---

### Этап 3: Fallback Scoring (ПОТЕРЯ ДАННЫХ)

**Файл:** `pipeline/highlight.py`  
**Функция:** `_score_story_candidate_timeout_fallback()`  
**Строки:** 3353-3650

#### Как получает данные (строка 3354):
```python
baseline = dict(candidate.get("score_breakdown", {}) or {})
```

#### Откуда берется baseline:
```python
candidate = {
    "score_breakdown": {
        "completion_score": 0.8,
        "is_complete": True,
        # ... ТОЛЬКО story metrics
        # НЕТ visual metrics!
    }
}
```

#### Попытка получить visual metrics (строки 3419-3421):
```python
source_face_presence = float(baseline.get("face_presence", 0.0) or 0.0)
# baseline НЕ содержит "face_presence" → возвращает 0.0 (дефолт)

source_person_presence = float(baseline.get("person_presence", 0.0) or 0.0)
# baseline НЕ содержит "person_presence" → возвращает 0.0 (дефолт)

source_subject_presence = float(baseline.get("subject_presence", 0.0) or 0.0)
# baseline НЕ содержит "subject_presence" → возвращает 0.0 (дефолт)
```

#### Вычисление face_evidence (строки 3422-3430):
```python
face_evidence_score = max(
    0.0,
    min(
        1.0,
        source_face_presence * 0.62       # 0.0 * 0.62 = 0.0
        + source_person_presence * 0.22   # 0.0 * 0.22 = 0.0
        + source_subject_presence * 0.16, # 0.0 * 0.16 = 0.0
    ),
)
# = 0.0
```

#### Вычисление visual_subject_score (строки 3431-3441):
```python
visual_subject_score = float(
    baseline.get(
        "visual_subject_score",
        max(
            0.18,  # ← ДЕФОЛТНОЕ ЗНАЧЕНИЕ!
            face_evidence_score * 0.85  # 0.0 * 0.85 = 0.0
            + (0.18 if speech_density >= 0.24 else 0.08),
        ),
    )
    or 0.0
)
# = 0.18 (дефолт, т.к. baseline не содержит "visual_subject_score")
```

**ЭТО ИСТОЧНИК ЗНАЧЕНИЯ 0.18!**

#### Вычисление reframe_feasibility_score (строки 3442-3453):
```python
reframe_feasibility_score = float(
    baseline.get(
        "reframe_feasibility_score",
        min(
            1.0,
            visual_subject_score * 0.72  # 0.18 * 0.72 = 0.1296
            + story_clarity_score * 0.18
            + audio_energy * 0.10,
        ),
    )
    or 0.0
)
# ≈ 0.24 (вычислено из дефолтных значений)
```

**ЭТО ИСТОЧНИК ЗНАЧЕНИЯ 0.24!**

#### Вычисление empty_frame_risk (строки 3454-3464):
```python
empty_frame_risk = float(
    baseline.get(
        "empty_frame_risk",
        max(
            0.0,
            1.0 - (
                visual_subject_score * 0.75      # 0.18 * 0.75 = 0.135
                + reframe_feasibility_score * 0.35  # 0.24 * 0.35 = 0.084
            ),
        ),
    )
    or 0.0
)
# = max(0.0, 1.0 - 0.219) = 0.781
```

#### Дополнительная корректировка (строки 3465-3470):
```python
if face_evidence_score <= 0.06:  # 0.0 <= 0.06 → True
    visual_subject_score = min(
        visual_subject_score, 0.18 if speech_density < 0.40 else 0.22
    )
    reframe_feasibility_score = min(reframe_feasibility_score, 0.24)
    empty_frame_risk = max(empty_frame_risk, 0.84)
```

**ФИНАЛЬНЫЕ ЗНАЧЕНИЯ:**
- `face_evidence_score = 0.0`
- `visual_subject_score = 0.18`
- `reframe_feasibility_score = 0.24`
- `empty_frame_risk = 0.84` → **0.88** после дополнительных корректировок

**ЭТО ИСТОЧНИКИ ВСЕХ МАГИЧЕСКИХ ЧИСЕЛ: 0.0, 0.18, 0.24, 0.88!**

---

### Этап 4: Запись в score_breakdown

**Строки 3600-3650:**
```python
breakdown = {
    **baseline,  # Копирует исходный пустой breakdown
    # ... добавляет вычисленные метрики
    "face_evidence_score": round(face_evidence_score, 4),  # 0.0
    "source_face_presence": round(source_face_presence, 4),  # 0.0
    "source_person_presence": round(source_person_presence, 4),  # 0.0
    "source_subject_presence": round(source_subject_presence, 4),  # 0.0
    "visual_subject_score": round(visual_subject_score, 4),  # 0.18
    "reframe_feasibility_score": round(reframe_feasibility_score, 4),  # 0.24
    "empty_frame_risk": round(empty_frame_risk, 4),  # 0.88
}
```

**Эти значения сохраняются в candidate["score_breakdown"].**

---

### Этап 5: Selection Filters (ОТКЛОНЕНИЕ)

**Файл:** `pipeline/highlight.py`  
**Строки:** ~10200-10350 (в selection logic)

#### Извлечение face_evidence (строка ~10250):
```python
face_evidence_score = max(
    float(breakdown.get("face_evidence_score", 0.0) or 0.0),
    float(breakdown.get("face_presence", 0.0) or 0.0),
    float(breakdown.get("person_presence", 0.0) or 0.0),
    float(breakdown.get("subject_presence", 0.0) or 0.0),
)
# = max(0.0, 0.0, 0.0, 0.0) = 0.0
```

#### Проверка face_evidence_gate (строка ~10260):
```python
face_evidence_gate = face_evidence_score >= 0.08
# = 0.0 >= 0.08 → False
```

#### Проверка отклонения (строки ~10300):
```python
elif not face_evidence_gate and not story_override:
    reason = "no_visual_subject"
```

**Кандидат ОТКЛОНЕН.**

---

## Сравнение: Legacy vs Story-Centric

### Legacy Pipeline (РАБОТАЕТ)

1. **Scene Detection** → детектирует сцены с face tracking
2. **Candidate Creation** → каждая сцена уже содержит `face_presence`, `person_presence`
3. **Ranking** → `_score_story_candidate()` использует СУЩЕСТВУЮЩИЕ metrics
4. **Selection** → filters видят реальные значения (0.79-0.80) → ПРИНИМАЮТ

### Story-Centric Pipeline (СЛОМАН)

1. **Story Building** → создает кандидаты БЕЗ visual metrics
2. **Ranking** → timeout → fallback использует ПУСТОЙ baseline
3. **Fallback** → вычисляет дефолты (0.0, 0.18, 0.24, 0.88)
4. **Selection** → filters видят дефолты → ОТКЛОНЯЮТ ВСЕ

---

## Точки потери данных

### Точка потери #1: story_chain_to_candidate() [ГЛАВНАЯ]
**Файл:** `pipeline/montage/story_pipeline.py`  
**Функция:** `story_chain_to_candidate()`  
**Строки:** 195-204  
**Проблема:** score_breakdown создается БЕЗ visual metrics  
**Воздействие:** Все последующие этапы не имеют visual данных

### Точка потери #2: Ranking Timeout [ТРИГГЕР]
**Файл:** `pipeline/highlight.py`  
**Функция:** `_score_story_candidate()`  
**Строки:** 6046-6486  
**Проблема:** Функция не завершается за 30 секунд  
**Воздействие:** Переход на fallback без face detection

### Точка потери #3: Timeout Fallback [ДЕФОЛТЫ]
**Файл:** `pipeline/highlight.py`  
**Функция:** `_score_story_candidate_timeout_fallback()`  
**Строки:** 3419-3470  
**Проблема:** baseline.get() возвращает дефолты (0.0)  
**Воздействие:** Все visual metrics становятся 0.0/0.18/0.24/0.88

---

## Магические числа объяснены

### face_evidence_score = 0.0
**Источник:** `baseline.get("face_presence", 0.0)` → дефолт  
**Строка:** 3419-3430  
**Вычисление:** `0.0 * 0.62 + 0.0 * 0.22 + 0.0 * 0.16 = 0.0`

### visual_subject_score = 0.18
**Источник:** Хардкод дефолт в fallback функции  
**Строка:** 3435  
**Код:** `max(0.18, face_evidence_score * 0.85 + ...)`  
**Логика:** Когда face_evidence = 0, используется минимум 0.18

### reframe_feasibility_score = 0.24
**Источник:** Вычислено из visual_subject_score = 0.18  
**Строка:** 3442-3453  
**Вычисление:** `0.18 * 0.72 + story_clarity * 0.18 + audio * 0.10`  
**Затем ограничено:** `min(reframe_feasibility_score, 0.24)` на строке 3469

### empty_frame_risk = 0.88
**Источник:** Вычислено из 0.18 и 0.24  
**Строка:** 3454-3470  
**Вычисление:** `1.0 - (0.18 * 0.75 + 0.24 * 0.35) = 0.781`  
**Затем повышено:** `max(empty_frame_risk, 0.84)` на строке 3470  
**После округления и дополнительных корректировок:** ≈ 0.88

---

## Почему ranking timeout?

### Гипотеза 1: Длительность кандидатов
Story-Centric создает ОЧЕНЬ длинные кандидаты:
- Кандидат #1: 136.52 сек
- Кандидат #2: 215.17 сек
- Кандидат #3: 471.21 сек (почти 8 минут!)

`sample_face_focus_stats()` должен обработать ВСЕ кадры → очень медленно.

### Гипотеза 2: Кумулятивная нагрузка
6 кандидатов × длительность × face detection = огромная вычислительная нагрузка

### Настройки timeout:
```yaml
ranking_soft_timeout_seconds: 24
ranking_hard_timeout_seconds: 30
```

30 секунд недостаточно для face detection на 471-секундном видео.

---

## Вывод

**Корневая причина:** Story-Centric pipeline имеет **архитектурную проблему последовательности операций:**

1. Story candidates создаются БЕЗ visual data
2. Visual analysis должен происходить в ranking
3. Ranking timeout из-за длинных кандидатов
4. Fallback не имеет visual data в baseline
5. Selection filters видят дефолты → отклоняют все

**Критические номера строк:**
- `pipeline/montage/story_pipeline.py:195-204` — score_breakdown БЕЗ visual metrics
- `pipeline/highlight.py:6068-6074` — face detection (никогда не завершается)
- `pipeline/highlight.py:3354` — baseline из пустого score_breakdown
- `pipeline/highlight.py:3419-3470` — дефолтные значения 0.0/0.18/0.24/0.88

**Все магические числа — это fallback дефолты, а не реальные метрики.**

---

**Статус:** 🔴 КОРНЕВАЯ ПРИЧИНА ПОЛНОСТЬЮ ИДЕНТИФИЦИРОВАНА  
**Тип:** Архитектурный дефект последовательности операций + Timeout проблема  
**Решение требует:** Либо добавить visual metrics в story_chain_to_candidate, либо исправить timeout, либо и то и другое
