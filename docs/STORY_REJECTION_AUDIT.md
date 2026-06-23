# Story-Centric Rejection Audit Report

**Дата:** 15 июня 2026  
**Спринт:** 1.6  
**Эпизод:** episode01_test.avi

---

## Executive Summary

**Проблема:** Режим Story-Centric генерирует 0 выходов, в то время как Legacy генерирует 3.

**Главная причина:** Все 6 отклоненных кандидатов Story-Centric имеют **face_evidence_score = 0.0**, что приводит к немедленному отклонению по критерию `no_visual_subject`.

**Критический фильтр:**
```python
face_evidence_gate = face_evidence_score >= 0.08
```

Если `face_evidence_score < 0.08` и нет `story_override`, кандидат отклоняется с причиной `no_visual_subject`.

---

## Детальный анализ отклоненных кандидатов

### Кандидат 1: 186.90-323.42 (136.52 сек)
```json
{
  "candidate_id": "186.90-323.42",
  "duration_seconds": 136.52,
  "rejection_reason": "no_visual_subject",
  "story_completion_score": 0.0173,
  "story_payoff_score": 1.0,
  "story_interest_score": 0.964,
  "face_evidence_score": 0.0,
  "visual_subject_score": 0.18,
  "source_face_presence": 0.0,
  "source_person_presence": 0.0,
  "source_subject_presence": 0.0,
  "hook_score": 0.82,
  "watchability_score": 0.9022,
  "empty_frame_risk": 0.88,
  "reframe_feasibility_score": 0.24
}
```

**Анализ:**
- ✅ Высокий интерес к истории (0.964)
- ✅ Хорошая watchability (0.9022)
- ✅ Сильный хук (0.82)
- ❌ **Нет визуального субъекта:** face_evidence = 0.0
- ❌ Высокий риск пустого кадра (0.88)
- ⚠️ Очень длинный (136 сек, > 60 сек лимита)

**Вердикт:** Отклонен из-за полного отсутствия лиц/людей в кадре.

---

### Кандидат 2: 1202.33-1417.50 (215.17 сек)
```json
{
  "candidate_id": "1202.33-1417.50",
  "duration_seconds": 215.17,
  "rejection_reason": "no_visual_subject",
  "story_completion_score": 0.0191,
  "story_payoff_score": 1.0,
  "story_interest_score": 0.964,
  "face_evidence_score": 0.0,
  "visual_subject_score": 0.18,
  "source_face_presence": 0.0,
  "source_person_presence": 0.0,
  "source_subject_presence": 0.0,
  "hook_score": 0.82,
  "watchability_score": 0.88,
  "empty_frame_risk": 0.88,
  "reframe_feasibility_score": 0.24
}
```

**Анализ:**
- ✅ Высокий интерес к истории (0.964)
- ✅ Хорошая watchability (0.88)
- ✅ Сильный хук (0.82)
- ❌ **Нет визуального субъекта:** face_evidence = 0.0
- ❌ Высокий риск пустого кадра (0.88)
- ⚠️ Критически длинный (215 сек, >> 60 сек лимита)

**Вердикт:** Отклонен из-за полного отсутствия лиц/людей в кадре.

---

### Кандидат 3: 9.26-480.47 (471.21 сек)
```json
{
  "candidate_id": "9.26-480.47",
  "duration_seconds": 471.21,
  "rejection_reason": "low_story_interest",
  "story_completion_score": 0.0173,
  "story_payoff_score": 0.3333,
  "story_interest_score": 0.0,
  "face_evidence_score": 0.0,
  "visual_subject_score": 0.18,
  "source_face_presence": 0.0,
  "source_person_presence": 0.0,
  "source_subject_presence": 0.0,
  "hook_score": 1.0,
  "watchability_score": 0.3,
  "empty_frame_risk": 0.88,
  "reframe_feasibility_score": 0.24
}
```

**Анализ:**
- ✅ Отличный хук (1.0)
- ❌ **Нет интереса к истории** (0.0, порог: 0.52)
- ❌ Низкая watchability (0.3, порог: 0.54)
- ❌ Слабый payoff (0.3333, порог: 0.4)
- ❌ Нет визуального субъекта: face_evidence = 0.0
- ⚠️ Экстремально длинный (471 сек, почти 8 минут!)

**Вердикт:** Отклонен СНАЧАЛА по low_story_interest (первый в цепочке проверок), но и face_evidence = 0.0 тоже критичен.

---

### Кандидат 4: 1126.85-1180.71 (53.86 сек)
```json
{
  "candidate_id": "1126.85-1180.71",
  "duration_seconds": 53.86,
  "rejection_reason": "no_visual_subject",
  "story_completion_score": 0.0191,
  "story_payoff_score": 0.75,
  "story_interest_score": 0.583,
  "face_evidence_score": 0.0,
  "visual_subject_score": 0.18,
  "source_face_presence": 0.0,
  "source_person_presence": 0.0,
  "source_subject_presence": 0.0,
  "hook_score": 0.64,
  "watchability_score": 0.6605,
  "empty_frame_risk": 0.88,
  "reframe_feasibility_score": 0.24
}
```

**Анализ:**
- ✅ Умеренный интерес (0.583, чуть выше порога 0.52)
- ✅ Хорошая watchability (0.6605)
- ✅ Приемлемая длина (53.86 сек)
- ❌ **Нет визуального субъекта:** face_evidence = 0.0
- ❌ Высокий риск пустого кадра (0.88)
- ⚠️ Интерес к истории близок к порогу (0.583 vs 0.52)

**Вердикт:** Отклонен из-за полного отсутствия лиц/людей в кадре. **Наиболее близкий к публикации кандидат!**

---

### Кандидат 5: 1288.14-1417.50 (129.36 сек)
```json
{
  "candidate_id": "1288.14-1417.50",
  "duration_seconds": 129.36,
  "rejection_reason": "weak_premise_hook",
  "story_completion_score": 0.0191,
  "story_payoff_score": 0.63,
  "story_interest_score": 0.8534,
  "face_evidence_score": 0.0,
  "visual_subject_score": 0.18,
  "source_face_presence": 0.0,
  "source_person_presence": 0.0,
  "source_subject_presence": 0.0,
  "hook_score": 0.6,
  "watchability_score": 0.6684,
  "empty_frame_risk": 0.88,
  "reframe_feasibility_score": 0.24,
  "visual_premise_strength": 0.162,
  "sound_off_hook_score": 0.3213,
  "first_second_hook_score": 0.4217,
  "premise_signal_score": 0.2978
}
```

**Анализ:**
- ✅ Хороший интерес к истории (0.8534)
- ✅ Хорошая watchability (0.6684)
- ❌ **Слабый premise hook:** visual_premise = 0.162 (порог: 0.48), sound_off = 0.3213 (порог: 0.56), first_second = 0.4217 (порог: 0.54)
- ❌ Нет визуального субъекта: face_evidence = 0.0
- ⚠️ Длинный (129 сек, > 60 сек)

**Вердикт:** Отклонен по weak_premise_hook (не прошел ни один из 3 premise gate тестов), но face_evidence = 0.0 также критичен.

---

### Кандидат 6: 821.32-934.48 (113.16 сек)
```json
{
  "candidate_id": "821.32-934.48",
  "duration_seconds": 113.16,
  "rejection_reason": "no_visual_subject",
  "story_completion_score": 0.0191,
  "story_payoff_score": 1.0,
  "story_interest_score": 0.8373,
  "face_evidence_score": 0.0,
  "visual_subject_score": 0.18,
  "source_face_presence": 0.0,
  "source_person_presence": 0.0,
  "source_subject_presence": 0.0,
  "hook_score": 0.42,
  "watchability_score": 0.7722,
  "empty_frame_risk": 0.88,
  "reframe_feasibility_score": 0.24
}
```

**Анализ:**
- ✅ Хороший интерес к истории (0.8373)
- ✅ Хорошая watchability (0.7722)
- ✅ Отличный payoff (1.0)
- ❌ **Нет визуального субъекта:** face_evidence = 0.0
- ❌ Высокий риск пустого кадра (0.88)
- ⚠️ Длинный (113 сек, > 60 сек)

**Вердикт:** Отклонен из-за полного отсутствия лиц/людей в кадре.

---

## Сравнение: Legacy vs Story-Centric

### Legacy Mode (3 принятых кандидата)

**Пример успешного кандидата (Legacy #1: 1265.88-1304.56):**
```json
{
  "duration_seconds": 38.68,
  "face_presence": 1.0,
  "face_evidence_score": 0.7988,
  "visual_subject_score": 0.7384,
  "person_presence": 0.0855,
  "subject_presence": 1.0,
  "empty_frame_risk": 0.0,
  "reframe_feasibility_score": 0.9372,
  "story_interest_score": 0.539,
  "watchability_score": 0.6837
}
```

**Ключевые различия:**
| Метрика | Legacy (✅ принят) | Story (❌ отклонен) |
|---------|-------------------|---------------------|
| **face_evidence_score** | **0.7988** | **0.0** |
| **visual_subject_score** | **0.7384** | **0.18** |
| **face_presence** | **1.0** | **0.0** |
| **empty_frame_risk** | **0.0** | **0.88** |
| **reframe_feasibility** | **0.9372** | **0.24** |
| story_interest_score | 0.539 | 0.583-0.964 |
| watchability_score | 0.6837 | 0.6605-0.9022 |

---

## Корневая причина: Почему face_evidence = 0.0?

### Гипотеза 1: Story-Centric не запускает face detection
❓ **Проверка:** Story-Centric использует `source="story_pipeline"`, Legacy использует `source="scene_cluster"`.

В коде `pipeline/highlight.py` есть два разных пути вычисления face_evidence:

#### Legacy Path (scene_cluster):
```python
# Использует реальные метрики из video analysis
face_presence = float(baseline.get("face_presence", 0.0))
person_presence = float(baseline.get("person_presence", 0.0))
subject_presence = float(baseline.get("subject_presence", 0.0))
```

#### Story Path (story_pipeline):
```python
# Использует ОЦЕНОЧНЫЕ метрики, НЕ реальные
source_face_presence = 0.0  # ← НЕ ВЫЧИСЛЯЕТСЯ!
source_person_presence = 0.0  # ← НЕ ВЫЧИСЛЯЕТСЯ!
source_subject_presence = 0.0  # ← НЕ ВЫЧИСЛЯЕТСЯ!
```

**ВЫВОД:** Story-Centric НЕ запускает face/person detection на этапе построения кандидатов, устанавливая все значения в 0.0 по умолчанию.

### Гипотеза 2: Face detection выполняется позже, но не сохраняется

Проверив код, видим:
- Legacy вызывает `_add_visual_scoring()` ДО selection фильтров
- Story использует `_compute_story_visual_baseline()` с дефолтными значениями

**ВЫВОД:** Story-Centric откладывает visual analysis на более поздний этап, но selection filters применяются РАНЬШЕ, используя face_evidence = 0.0.

---

## Пороговые значения (из settings.yaml + код)

### Критические фильтры отклонения

1. **no_visual_subject:**
   ```python
   face_evidence_gate = face_evidence_score >= 0.08
   if not face_evidence_gate and not story_override:
       reason = "no_visual_subject"
   ```
   - **Порог:** face_evidence_score >= 0.08
   - **Story кандидаты:** 0.0 (все отклонены)
   - **Legacy кандидаты:** 0.7965-0.7988 (все прошли)

2. **low_visual_viability:**
   ```python
   visual_subject_score < (0.46 if quality_first else 0.36)
   ```
   - **Порог:** 0.46 (quality_first включен в settings)
   - **Story кандидаты:** 0.18 (все не прошли)
   - **Legacy кандидаты:** 0.7324-0.7384 (все прошли)

3. **low_story_interest:**
   ```yaml
   interestingness_threshold: 0.52
   ```
   - **Порог:** 0.52
   - **Story кандидаты:** 1 отклонен (0.0), остальные прошли
   - **Legacy кандидаты:** 0.539-0.7907 (все прошли)

4. **weak_premise_hook:**
   ```yaml
   visual_premise_threshold: 0.48
   sound_off_hook_threshold: 0.56
   first_second_hook_threshold: 0.54
   ```
   - Нужно пройти хотя бы один из трех
   - **Story кандидат #5:** Не прошел ни один (0.162, 0.3213, 0.4217)

---

## Диагностика: 5 возможных причин

### ✅ 1. Слишком строгие пороговые значения?
**НЕТ.** Пороги разумны:
- face_evidence >= 0.08 (очень низкий порог, 8% присутствия лица)
- visual_subject_score >= 0.46 (разумно для quality_first)

Legacy кандидаты легко проходят эти пороги (0.73-0.80), что показывает их адекватность.

### ✅ 2. Слишком длинные цепочки сюжетов?
**ДА, ЧАСТИЧНО.** Story-Centric генерирует очень длинные кандидаты:
- Кандидат #3: **471.21 сек** (7.8 минут!)
- Кандидат #2: **215.17 сек** (3.6 минуты)
- Кандидат #1: **136.52 сек** (2.3 минуты)

При `story_hard_max_seconds: 60`, эти кандидаты все равно превышают лимит в 2-8 раз.

**Проблема:** Story-Centric строит слишком широкие окна контекста, объединяя множество сцен без face detection.

### ✅ 3. Сбой отслеживания лица?
**ДА, КРИТИЧЕСКАЯ ПРОБЛЕМА.** Story-Centric **НЕ выполняет face detection** на этапе построения кандидатов.

**Код:**
```python
# В _score_story_candidate_with_visual_baseline():
source_face_presence = 0.0  # ← Хардкод!
source_person_presence = 0.0  # ← Хардкод!
source_subject_presence = 0.0  # ← Хардкод!
```

**Legacy код:**
```python
# В _score_legacy_candidate():
face_presence = float(faces.get("face_presence", 0.0))  # ← Реальные данные
person_presence = float(faces.get("person_presence", 0.0))
subject_presence = float(faces.get("subject_presence", 0.0))
```

### ✅ 4. Неверная визуальная оценка?
**ДА.** Оценка не неверна, она **ОТСУТСТВУЕТ**. Story-Centric использует заглушки вместо реальных метрик.

### ❌ 5. Логическая ошибка в фильтрации?
**НЕТ.** Логика фильтрации корректна. Проблема в том, что Story-Centric подает в фильтры некорректные (нулевые) данные.

---

## Технические выводы

### Архитектурный дефект

**Story-Centric pipeline имеет критическую архитектурную проблему:**

1. **Building Context** → создает широкие story windows БЕЗ visual analysis
2. **Ranking** → оценивает кандидаты с face_evidence = 0.0
3. **Selection Filters** → отклоняет все кандидаты из-за no_visual_subject
4. **Visual Analysis** → никогда не выполняется, т.к. все отклонены

**Legacy pipeline:**
1. **Scene Detection** → детектирует сцены с face/person tracking
2. **Building Context** → использует сцены с визуальными данными
3. **Ranking** → оценивает с реальными face_evidence метриками
4. **Selection Filters** → корректно фильтрует на основе реальных данных

### Почему это происходит?

Story-Centric пытается построить контекст на уровне **диалогов и сюжетных дуг**, игнорируя сцены.

**Проблема:** Visual analysis привязан к сценам, а Story-Centric работает без них.

**Решение требует:**
- Добавить face/person detection для story windows
- ИЛИ использовать scene-based visual metrics при построении story candidates
- ИЛИ ослабить visual gates для story_override candidates

---

## Рекомендации (БЕЗ изменения порогов)

### Критичность: 🔴 БЛОКЕР

**Без исправления Story-Centric НЕ МОЖЕТ генерировать выходы.**

### Опция 1: Добавить Visual Analysis для Story Windows ⭐ РЕКОМЕНДУЕТСЯ
```python
# В _build_story_candidates():
for story_window in story_windows:
    # ДОБАВИТЬ:
    visual_metrics = self._analyze_visual_baseline(
        video_path, 
        story_window['start'], 
        story_window['end']
    )
    story_window['face_presence'] = visual_metrics['face_presence']
    story_window['person_presence'] = visual_metrics['person_presence']
    story_window['subject_presence'] = visual_metrics['subject_presence']
```

**Плюсы:**
- ✅ Исправляет корневую причину
- ✅ Story-Centric получает реальные visual метрики
- ✅ Не требует изменения порогов

**Минусы:**
- ⚠️ Увеличит время обработки (face detection для каждого story window)

### Опция 2: Использовать Scene Visual Metrics
```python
# В _build_story_candidates():
# Агрегировать visual metrics из сцен внутри story window
scenes_in_window = [s for s in scenes if overlaps(s, story_window)]
avg_face_presence = mean([s.face_presence for s in scenes_in_window])
```

**Плюсы:**
- ✅ Переиспользует существующие данные
- ✅ Быстро (нет дополнительного face detection)

**Минусы:**
- ⚠️ Требует, чтобы сцены были обнаружены первыми
- ⚠️ Story windows могут не совпадать со сценами

### Опция 3: Отложить Visual Filters
```python
# Переместить visual filters ПОСЛЕ visual analysis
# 1. Selection → принять кандидаты с face_evidence = 0.0
# 2. Visual Analysis → выполнить для принятых
# 3. Final Visual Gate → отклонить если face_evidence < 0.08
```

**Плюсы:**
- ✅ Минимальные изменения кода

**Минусы:**
- ❌ Неэффективно (visual analysis для кандидатов, которые будут отклонены)
- ❌ Не решает проблему для ranking

### Опция 4: Story Override для High-Interest Stories
```python
# Включить publishable_story_override для сильных историй
publishable_story_override_enabled: true
publishable_story_interest_threshold: 0.58  # Понизить с 0.6
publishable_story_completeness_threshold: 0.60  # Понизить с 0.68
```

**Плюсы:**
- ✅ Быстрое исправление (только config)
- ✅ Позволит Story-Centric генерировать выходы

**Минусы:**
- ❌ Обход проблемы, а не исправление
- ❌ Может пропустить кандидаты с реально пустыми кадрами
- ❌ Не исправляет face_evidence = 0.0

---

## Следующие шаги

### Немедленно (Спринт 1.6):
1. ✅ **Собрать доказательства** — ВЫПОЛНЕНО (этот отчет)
2. 🔲 **Выбрать стратегию исправления** — Опция 1 рекомендуется
3. 🔲 **Реализовать исправление**
4. 🔲 **Повторить validation run**
5. 🔲 **Сравнить результаты**

### Будущее (Спринт 1.7):
- Оптимизировать Story window construction (избегать экстремально длинных окон)
- Добавить промежуточные visual gates в ranking
- Рассмотреть адаптивные пороги для story vs scene кандидатов

---

**Статус:** 🔴 КРИТИЧЕСКАЯ ПРОБЛЕМА ВЫЯВЛЕНА  
**Блокирует:** Завершение Спринта 1.6  
**Требует:** Архитектурное исправление Story-Centric visual analysis
