# Shorts Factory

Shorts Factory - это локальный CPU-first конвейер для превращения длинных эпизодов в вертикальные short-видео. В проекте есть полноценный GUI на PySide6, CLI-точка входа, режим диагностики и довольно большая система fallback-логики, чтобы пайплайн продолжал работать даже при частично отсутствующих зависимостях.

Если коротко, рабочий путь такой:

1. Исходное видео пробивается на сцены и окна-кандидаты.
2. Для окон считается аудио-, текстовая и визуальная пригодность.
3. Лучшие окна проходят trimming, транскрипцию, компактификацию пауз, reframing, burn subtitles и титрование.
4. На выходе пишутся вертикальные MP4, метаданные JSON и `episode_report.json`.
5. GUI читает этот отчёт и показывает статус, warnings, метрики и список выходных файлов.

Этот README описывает не только установку, а именно фактическую архитектуру кода и порядок обработки.

## Карта Репозитория

| Путь | Назначение |
|---|---|
| `main.py` | CLI entry point: GUI, batch, diagnostics. |
| `gui.py` | Полный интерфейс на PySide6: очередь, настройки, отчёты, логи, локализация, открытие файлов. |
| `pipeline/highlight.py` | Главный производственный pipeline: выбор кандидатов, скоринг, trimming, subtitle, reframe, export, titling, reporting. |
| `pipeline/config.py` | База default-настроек и нормализация значений. |
| `pipeline/audio_analysis.py` | Извлечение аудио, RMS, speech density, ffmpeg silencedetect. |
| `pipeline/scene_detect.py` | Детект сцен с кэшем и безопасными fallback. |
| `pipeline/active_speaker.py` | Face tracking, active speaker approximation, person detection, evidence scores. |
| `pipeline/face_crop.py` | Построение вертикального crop/reframe и экспорт кадров. |
| `pipeline/subtitle.py` | Основной subtitle pipeline: ASR, correction, retry, quality signals. |
| `pipeline/titling.py` | Генерация заголовков, хэштегов, эмодзи и переименование output-файлов. |
| `pipeline/text_utils.py` | Общие операции с текстом: чистка, токенизация, repair mojibake. |
| `pipeline/remote_enhancer.py` | Stub для remote quality fallback metadata. |
| `pipeline/smolvlm.py` | Optional remote rerank stub, не нужен для локального основного пути. |
| `pipeline/subtitles.py` | Legacy/lightweight helper для субтитров; не основной production path. |
| `pipeline/selection.py` | Старый компактный скорер сегментов, отделён от основного pipeline. |
| `pipeline/render.py` | Минимальный concat helper. |
| `diagnostics.py` | Текстовая диагностика окружения и fallback audit. |
| `utils.py` | Небольшие утилиты общего назначения. |
| `tests/` | Набор unit tests для config, quality governor, duration policy, cache, compaction, title generation и face logic. |
| `examples/sample_config.yaml` | Короткий пример конфига. Канонические defaults живут в `pipeline/config.py`. |
| `whisper_cpp_setup.md` | Отдельный гайд по whisper.cpp для быстрого CPU ASR на Windows. |

## Архитектура Пайплайна

Главный production path находится в `pipeline/highlight.py`. Там объявлен класс `Pipeline`, а также несколько top-level helper-функций.

Пайплайн спроектирован как цепочка, где каждый этап может:

- использовать cache;
- делать retries;
- работать в subprocess с watchdog;
- выдавать soft/hard timeout;
- падать в безопасный fallback вместо silent corruption.

Ключевая идея: clip считается готовым не тогда, когда "что-то экспортировалось", а тогда, когда он прошёл весь quality gate.

### Главные подсистемы

- `video probing`
- `scene discovery`
- `audio summary`
- `dialogue gate`
- `candidate scoring`
- `semantic preview rerank`
- `review pass recovery`
- `trim and compact`
- `subtitle transcription`
- `reframe and crop`
- `quality governor`
- `burn subtitles`
- `title generation`
- `metadata/report writing`

## Точки Входа

### `python main.py`

Если флагов нет, приложение открывает GUI.

### `python main.py --gui`

Явно запускает графический интерфейс.

### `python main.py --batch --input-file <video>`

Запускает production pipeline на одном файле из CLI.

### `python main.py --batch --input-folder <folder>`

Запускает batch mode через GUI contract: папка сканируется на видеофайлы, а каждый эпизод обрабатывается последовательно.

### `python main.py --diagnostics`

Печатает текст диагностики и summary по окружению.

## Поток Выполнения

`main.py` делает простой dispatch:

1. Если указан `--diagnostics`, запускается `run_diagnostics_text()` и `run_diagnostics_summary()`.
2. Если указан `--gui`, вызывается `launch_gui()`.
3. Если указан `--batch` и `--input-folder`, вызывается `run_batch_via_gui_contract()`.
4. Если указан `--batch` и `--input-file`, создаётся `Pipeline`, вызывается `process_episode()`, и код возврата зависит от того, есть ли `generated_outputs`.
5. Во всех остальных случаях стартует GUI.

## GUI

`gui.py` - это не просто обёртка над pipeline, а полноценное приложение с:

- вкладкой очереди;
- вкладкой diagnostics;
- панелью project settings;
- логом событий;
- summary выбранного файла;
- списком готовых short;
- локализацией UI;
- кнопками открытия output folder и выбранного short;
- сохранением настроек обратно в YAML.

### Ключевые объекты

- `FileItem` хранит путь, текущий статус, последнее сообщение и summary отчёта.
- `MainWindow` строит интерфейс, синхронизирует конфиг, запускает batch processing и обновляет UI.
- `launch_gui()` создаёт `QApplication` и показывает окно.

### Основные возможности GUI

- добавление эпизодов в очередь;
- очистка очереди;
- запуск/остановка обработки;
- сохранение настроек;
- выбор output root;
- открытие папки short;
- открытие выбранного short;
- просмотр диагностического вывода;
- локализация текста интерфейса на `ru` и `en`.

### Привязка настроек

UI связывает виджеты с конфигом через `CONFIG_BINDINGS`. Это означает, что controls являются реальной runtime-конфигурацией проекта, а не отдельной абстракцией.

Особенно важные поля:

- `quality_mode`
- `transcription_profile`
- `subtitle_processing_mode`
- `reframe_mode`
- `reframe_priority`
- `story_mode`
- `subtitle_render_mode`
- `subtitle_display_mode`
- `title_generation_enabled`
- `remote_quality_fallback`
- `output_root`

### Особенность `quality_mode`

В `_widgets_to_config()` `quality_mode` меняет сразу несколько параметров:

- `auto`
  - `subtitle_processing_mode = balanced_local`
  - `local_quality_escalation = True`
  - `reframe_priority = stability_first`
- `balanced`
  - `subtitle_processing_mode = balanced_local`
  - `local_quality_escalation = False`
  - `reframe_priority = stability_first`
- `max_quality`
  - `subtitle_processing_mode = enhanced_local`
  - `local_quality_escalation = True`
  - `reframe_priority = stability_first`
  - `reframe_anchor_mode = dialogue_center`

## Production Pipeline

Основной pipeline живёт в `pipeline/highlight.py` и экспортируется через:

- `Pipeline`
- `create_shorts_from_video(video_path, out_dir, cfg)`

### 1. Probing и scene discovery

Первые шаги:

- `probe_video()` и `probe_video_geometry()` вызывают `ffprobe`;
- `detect_scenes()` использует PySceneDetect, если он доступен;
- если scene detection не сработал, применяется безопасный fallback на весь clip или на глобальный scan.

### 2. Формирование candidate windows

`Pipeline._candidate_windows()` строит окна на основе:

- сценных кластеров;
- глобального scan, если сцены отсутствуют или невалидны;
- min/max ограничений на длину окна.

Ключевые параметры:

- `candidate_window_seconds`
- `candidate_step_seconds`
- `min_candidate_seconds`
- `selection_admission_fraction`
- `selection_admission_min_pool`
- `selection_admission_max_pool`

### 3. Audio summary и dialogue gate

`Pipeline._extract_audio_summary()` делает основную работу по аудио:

- извлекает или переиспользует episode WAV;
- держит episode/segment cache;
- считает speech density;
- определяет voiced intervals;
- строит audio summary cache;
- записывает summary на диск.

`Pipeline._dialogue_flow_admission()` решает, пропускать ли окно дальше. Он различает:

- `audio_starvation`
- `low_dialogue_flow`
- `single_block_sparse`
- `multi_turn_dialogue`
- `dense_voiced_dialogue`
- `dialogue_proxy`

То есть окно может быть отклонено не потому, что в нём "мало звука", а потому что оно не выглядит как материал, из которого реально можно собрать short.

### 4. Построение story candidates

Дальше pipeline пытается построить кандидатов по порядку:

1. `_build_story_candidates_from_turns_linear(...)`
2. `_build_story_candidates_from_window(...)`
3. `_fallback_window_candidate(...)`

Fallback-кандидат нужен, когда окно не очень хорошо раскладывается на turns, но всё же содержит достаточно speech energy и контекстной информации.

### 5. Ranking и visual precheck

Переход к ranking происходит через:

- `_ranking_visual_precheck(...)`
- `_score_story_candidate(...)`
- `_score_story_candidate_timeout_fallback(...)`
- `_semantic_preview_rerank(...)`
- `_semantic_preview_single(...)`

`_ranking_visual_precheck()` использует `sample_face_focus_stats()` из `pipeline.active_speaker` и добавляет в scoring:

- `face_evidence_score`
- `visual_subject_score`
- `empty_frame_risk`
- `subject_detector_pass`

`_semantic_preview_rerank()` даёт дополнительную переоценку уже отранжированным кандидатам и умеет fallback-ить при timeouts.

### 6. Review pass

Если основной пул слишком мал, pipeline может включить review pass и попытаться восстановить пригодные окна из соседних или цепочечных сегментов.

Главный метод:

- `_build_review_pass_candidates(...)`

Он учитывает:

- `review_pass_enabled`
- `review_pass_min_outputs`
- `review_pass_output_cap`
- `review_pass_face_floor`
- `review_pass_min_speech_density`
- `review_pass_chain_gap_seconds`
- `review_pass_max_chain_windows`
- `review_pass_max_stitched_seconds`

Review pass не пытается "спасти всё". Он подбирает только те варианты, которые ещё могут пройти визуальный и временной фильтр.

### 7. Duration policy и story mode

Pipeline не использует один фиксированный лимит duration для всех clip. Он пересчитывает policy с учётом subtitle evidence и story mode.

Связанные методы:

- `_candidate_duration_policy(...)`
- `_resolve_candidate_duration_policy(...)`
- `_effective_story_mode(...)`
- `_is_story_override_candidate(...)`

Поддерживаемые modes:

- `standard`
- `auto`
- `tension`

В tension mode используются другие пороги пауз, длины и context window.

### 8. Subtitles

Основной subtitle engine находится в `pipeline/subtitle.py`.

Он работает так:

1. Пытается загрузить `faster_whisper`.
2. Пробует несколько compute types для CPU.
3. Делает language retries.
4. Может добавлять context prompt.
5. Делает correction pass, если текст выглядит подозрительно.
6. Может повторить ASR в enhanced режиме, если quality низкий.
7. Строит sentence-highlight структуру.
8. Возвращает subtitle signals для downstream scoring.

Основные функции:

- `transcribe_segment(...)`
- `remap_subtitle_info_after_cuts(...)`
- `_subtitle_correction_pass(...)`
- `build_sentence_segments(...)`
- `build_ass_word_events(...)`
- `summarize_subtitle_context(...)`
- `subtitle_story_signals(...)`

Сигналы, которые потом попадают в metadata и report:

- `subtitle_confidence`
- `subtitle_text_sanity_score`
- `subtitle_language_consistency`
- `subtitle_quality_score`
- `subtitle_blackout_count`
- `subtitle_event_overlap_count`
- `subtitle_persisted_gaps_count`
- `subtitle_gap_blink_count`
- `subtitle_visual_drop_count`
- `subtitle_phrase_clear_count`
- `subtitle_phrase_replace_count`
- `subtitle_soft_hold_count`
- `subtitle_turn_retire_count`
- `subtitle_hold_duration_p95`

### 9. Trim и dialogue compaction

Сцены могут быть подрезаны по паузам до и после ASR.

Функции:

- `trim_silence_in_candidate_ms(...)`
- `trim_silence_and_limit(...)`
- `_maybe_compact_dialogue_after_subtitles(...)`

Если после cut-ов нужно remap-ить subtitles, используется:

- `remap_subtitle_info_after_cuts(...)`

Если integrity check не проходит, pipeline оставляет более безопасный вариант вместо окончательно сломанного compaction.

Ключевые параметры:

- `drop_silent`
- `remove_silent`
- `keep_dialogue_gap_seconds`
- `story_pause_cut_threshold_seconds`
- `story_pause_keep_max_seconds`
- `story_extension_max_pause_seconds`
- `tension_pause_cut_threshold_seconds`
- `tension_pause_keep_max_seconds`

### 10. Reframe и active speaker

`pipeline/active_speaker.py` и `pipeline/face_crop.py` отвечают за визуальную часть.

`active_speaker.py` делает:

- MediaPipe face detection, если доступен;
- OpenCV Haar fallback;
- HOG-based person detection;
- track assignment;
- speaker/listener scoring;
- scene-change sensitivity;
- mouth-motion proxy;
- recent face memory.

`face_crop.py` строит crop windows и экспортирует вертикальный clip. Он умеет:

- face_locked crop;
- context padded crop;
- dialogue-centered crop;
- wide subject crop;
- square canvas composition;
- center-safe fallback;
- face-preserving fallback.

Ключевые controls:

- `reframe_mode`
- `reframe_priority`
- `reframe_transition_mode`
- `reframe_anchor_mode`
- `framing_mode`
- `reframe_track_count_limit`
- `speaker_lock_mode`
- `speaker_lock_strict_mode`
- `speaker_center_strict_mode`
- `empty_frame_guard_enabled`
- `reframe_scene_interest_fallback`
- `reframe_listener_face_fallback`

### 11. Quality governor

Одна из самых важных функций:

- `_quality_governor_decision(candidate, subtitle_info, reframe_debug)`

Она решает, можно ли принять clip, или нужно reject/retry.

Проверяет:

- subtitle confidence;
- subtitle text sanity;
- subtitle quality;
- subtitle blackout behavior;
- visual subject evidence;
- face evidence peaks;
- no-subject state;
- speaker center offset;
- speaker-centered rate;
- listener fallback usage;
- subject-person fallback usage;
- empty frame risk;
- final crop evidence.

В тестах отдельно проверяются:

- reject для no-subject center-safe fallback;
- accept для случаев, где есть визуальные доказательства;
- reject/accept в зависимости от subtitle noise и face evidence.

### 12. Export и titling

На финале pipeline:

- пишет final MP4;
- пишет metadata JSON;
- генерирует title через `generate_context_title(...)`;
- может переименовать файл через `maybe_rename_output(...)`;
- добавляет output record в `generated_outputs`.

`pipeline/titling.py` умеет:

- чистить текст;
- выбирать лучший title candidate;
- извлекать keywords;
- собирать hashtags;
- подбирать mood;
- рассчитывать title quality;
- строить `hook_line`, `title_variant_a`, `title_variant_b`;
- вычислять `packaging_quality_score` и `retention_soft_score` (legacy alias `viral_soft_score` сохранён только для совместимости).

## Status Contract

В `pipeline/config.py` определён `status_contract`, который задаёт язык статусов для пайплайна и GUI:

- `queued`
- `analyzing`
- `discovering`
- `building_context`
- `ranking`
- `selecting`
- `refining_boundaries`
- `trimming`
- `reframing`
- `subtitling`
- `exporting`
- `titling`
- `done`
- `warning`
- `failed`

Эти статусы видны в логах, в элементах очереди и в progress callbacks.

## Выходные Артефакты

Для файла `episode.mp4` output folder по умолчанию называется:

- `episode_shorts`

Если задан `output_root`, папка создаётся внутри него.

Типичные артефакты:

- `cand_1.wav`
- `cand_1.srt`
- `cand_1_trimmed.mp4`
- `short_1.mp4`
- `short_1.json`
- `episode_report.json`

Если включено titling и файл переименован, final video может получить более читаемое имя с заголовком и hashtags.

## Формат Отчёта

Основной JSON-отчёт записывается в `episode_report.json`.

### Top-level keys

- `source_file`
- `output_dir`
- `story_mode`
- `status`
- `requested_max`
- `selected_candidates`
- `rejected_candidates`
- `generated_outputs`
- `warnings`
- `stage_timings`
- `stats`
- `gui_summary`

### `generated_outputs`

Каждый элемент содержит:

- `video`
- `metadata`

### `stats`

Туда попадают агрегированные значения по:

- selection;
- ranking;
- subtitle quality;
- reframe quality;
- title generation;
- watchdog;
- review pass;
- starvation reasons;
- speaker and dialogue metrics.

### `gui_summary`

GUI не показывает сырой report напрямую. Он использует агрегированный summary, где есть:

- `outputs`
- `windows`
- `story_candidates`
- `publishable_candidates`
- `main_rejection_reason`
- `avg_*` значения по quality метрикам
- counters по fallback и retry событиям
- timing values
- warnings_count

Если включён `Assisted Ranking Mode`, GUI дополнительно показывает top-5 candidate shorts для текущего эпизода и позволяет быстро собрать человеческую оценку в одном из режимов `excellent`, `good`, `bad`, `boring`, `confusing`. Эти оценки пишутся в локальный JSONL-лог и предназначены для последующей калибровки ранжирования на собственном корпусе.

## Benchmark workflow

Для измеримого сравнения изменений используется filesystem-first benchmark контур:

- snapshot-пакет кандидата сохраняется в `candidate_review/<candidate_id>/`;
- точка входа кандидата - `candidate_manifest.json`;
- в `candidate_manifest.json` дополнительно фиксируются `story_window_plan`, `story_window_segments`, `clarity_score`, `duration_penalty`, `window_expansion_meta` и `merge_reason`;
- в `candidate_manifest.json` также фиксируются `story_thread_id`, `story_coherence_score`, `coherence_merge_reason` и `coherence_rejection_reason`;
- в манифесте фиксируются `pipeline_version`, `config_hash` и `git_commit`, если commit доступен локально;
- отдельные артефакты лежат рядом с manifest: `preview.mp4`, `thumbnail.jpg`, `metadata.json`, `scoring.json`, `feedback.json`, `subtitles.json`, `reframe_debug.json`, `title_debug.json`, `pipeline_context.json`;
- `toolkit/benchmark_corpus/` хранит переиспользуемый корпус, `sessions/` и immutable `golden_set/`;
- `golden_set` не должен мутировать во время оценки;
- для review и compare можно использовать CLI без GUI.

### CLI-примеры

```powershell
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" export "C:\path\to\episode_report.json" --output "C:\path\to\candidate_review"
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" import "C:\path\to\candidate_review" --corpus "C:\Users\User\Desktop\toolkit\benchmark_corpus"
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" compare "C:\path\to\before\candidate_manifest.json" "C:\path\to\after\candidate_manifest.json" --output "C:\path\to\before_after_report.json"
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" audit --corpus "C:\Users\User\Desktop\toolkit\benchmark_corpus"
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" baseline --corpus "C:\Users\User\Desktop\toolkit\benchmark_corpus"
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" gate --corpus "C:\Users\User\Desktop\toolkit\benchmark_corpus"
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" queue --corpus "C:\Users\User\Desktop\toolkit\benchmark_corpus" --unreviewed
python "C:\Users\User\Desktop\toolkit\benchmark_corpus.py" review "C:\path\to\candidate_review\cand_00014" --labels good publishable --failure-reason late_hook bad_title
```

### Root-cause tags

Поверх high-level labels сохраняются root-cause tags:

- `late_hook`
- `late_entry`
- `weak_hook`
- `speaker_unclear`
- `wrong_face_focus`
- `subtitle_overload`
- `bad_pacing`
- `missing_context`
- `weak_payoff`
- `crop_jitter`
- `too_slow`
- `too_fast`
- `confusing_dialogue`
- `bad_title`

Именно эти теги используются для failure cluster analysis и roadmapping.

## Конфиг

Канонический источник правды по настройкам находится в `pipeline/config.py`:

- `DEFAULT_CONFIG`
- `normalize_config(...)`
- `load_config(...)`
- `save_config(...)`

### Важные группы параметров

#### Selection

- `max_shorts`
- `candidate_window_seconds`
- `candidate_step_seconds`
- `min_candidate_seconds`
- `selection_policy`
- `selection_admission_fraction`
- `selection_admission_min_pool`
- `selection_admission_max_pool`

#### Story timing

- Пайплайн собирает short как `story window`, а не как один highlight-кусок: `HOOK → CONTEXT → DEVELOPMENT → PAYOFF`.
- `story_mode`
- `target_story_min_seconds`
- `target_story_seconds`
- `story_soft_max_seconds`
- `story_hard_max_seconds`
- `story_strong_target_seconds`
- `story_exceptional_target_seconds`
- `min_publishable_seconds`
- `allow_story_extension_seconds`

#### Pause policy

- `keep_dialogue_gap_seconds`
- `story_pause_cut_threshold_seconds`
- `story_pause_keep_max_seconds`
- `story_extension_max_pause_seconds`
- `tension_pause_cut_threshold_seconds`
- `tension_pause_keep_max_seconds`

#### Subtitle

- `subtitle_processing_mode`
- `subtitle_language`
- `subtitle_template`
- `subtitle_render_mode`
- `subtitle_display_mode`
- `subtitle_compact_mode`
- `subtitle_max_visible_lines`
- `subtitle_max_chars_per_block`
- `subtitle_max_visible_words`
- `subtitle_sentence_max_words`
- `subtitle_hold_max_seconds`
- `subtitle_tail_hold_seconds`
- `subtitle_phrase_ttl_seconds`
- `subtitle_quality_score_threshold`

#### Reframe

- `reframe_mode`
- `reframe_priority`
- `reframe_transition_mode`
- `reframe_anchor_mode`
- `framing_mode`
- `reframe_track_count_limit`
- `speaker_lock_strict_mode`
- `speaker_center_strict_mode`
- `empty_frame_guard_enabled`
- `face_preserving_fallback_enabled`

#### Titling

- `title_generation_enabled`
- `title_language`
- `title_style`
- `title_max_length`
- `title_include_hashtags`
- `title_max_hashtags`
- `title_include_emoji`
- `title_max_emojis`
- `packaging_profile`

#### Watchdog and performance

- `analysis_fps`
- `face_detection_fps`
- `heartbeat_interval_seconds`
- `timeout_fallback_enabled`
- `watchdog_mode`
- `watchdog_skip_policy`
- `ranking_soft_timeout_seconds`
- `ranking_hard_timeout_seconds`
- `semantic_preview_soft_timeout_seconds`
- `semantic_preview_hard_timeout_seconds`
- `subtitle_soft_timeout_seconds`
- `subtitle_hard_timeout_seconds`
- `reframe_soft_timeout_seconds`
- `reframe_hard_timeout_seconds`

### Нормализация

`normalize_config(...)` не просто merges defaults. Он ещё:

- clamped values into safe ranges;
- normalizes legacy modes;
- keeps subtitle and story policies consistent;
- guarantees that `review_pass_output_cap` не ниже `review_pass_min_outputs`;
- normalizes `remote_quality_fallback`;
- нормализует `active_speaker_scan_profile` из legacy `episode_light` в `light`.

Это важно, потому что GUI редактирует config напрямую, а runtime должен получить безопасные и согласованные значения.

## Зависимости

### Системные требования

- `ffmpeg`
- `ffprobe`

Они должны быть доступны в `PATH`.

### Python dependencies из `requirements.txt`

- `PySide6`
- `opencv-python`
- `moviepy`
- `ffmpeg-python`
- `numpy<2.0`
- `tqdm`
- `scenedetect`
- `webrtcvad`
- `pyyaml`
- `faster-whisper`
- `mediapipe`
- `pydub`
- `requests`
- `pillow`
- `setuptools<81`

### Optional / legacy extras

- `vosk` упоминается только в `pipeline/subtitles.py`, который является legacy helper.
- `pipeline/remote_enhancer.py` и `pipeline/smolvlm.py` не требуют отдельного remote backend для базового локального сценария.

## Диагностика

`diagnostics.py` используется и из CLI, и из GUI.

Логика:

1. Определяет project root и Python executable.
2. Проверяет `ffmpeg` и `ffprobe`.
3. Если рядом существует `toolkit`-папка, пытается запустить `env_snapshot.ps1` и `run_audit.ps1`.
4. Если toolkit отсутствует, выполняет fallback на `python -m compileall main.py gui.py pipeline`.

Есть две функции:

- `run_diagnostics_text(project_root)`
- `run_diagnostics_summary(project_root)`

Summary возвращает компактный словарь с `issues` и boolean-флагами по ключевым проверкам.

## Legacy Modules

Некоторые файлы являются вспомогательными или историческими, и это нормально:

- `pipeline/selection.py` - старый standalone scorer.
- `pipeline/subtitles.py` - legacy subtitle wrapper с fallback на `faster_whisper`, `vosk` и placeholder output.
- `pipeline/render.py` - минимальный concat helper.
- `pipeline/remote_enhancer.py` - stub для remote fallback metadata.
- `pipeline/smolvlm.py` - stub для optional remote rerank.

Основной production path их не требует для успеха.

## Тесты

Тесты написаны на `unittest`.

Покрывают:

- config defaults and normalization;
- dialogue flow gates;
- duration policy;
- quality governor;
- audio cache;
- dialogue compaction;
- title generation;
- active speaker choice;
- review pass recovery;
- ranking precheck and fallback behavior.

Запуск:

```bash
python -m unittest discover -s tests -q
```

Важно: тесты импортируют production pipeline, поэтому runtime dependencies должны быть установлены.

## Установка на Windows

1. Установите Python 3.10 или новее.
2. Установите ffmpeg и добавьте `ffmpeg` и `ffprobe` в `PATH`.
3. Создайте virtual environment.
4. Установите зависимости.
5. Запустите GUI или diagnostics.

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py --gui
```

## Примеры Запуска

Один файл:

```bash
python main.py --batch --input-file "C:\path\to\episode.mp4"
```

Папка:

```bash
python main.py --batch --input-folder "C:\path\to\episodes"
```

Диагностика:

```bash
python main.py --diagnostics
```

## whisper.cpp

Файл `whisper_cpp_setup.md` содержит отдельный гайд по сборке и использованию whisper.cpp на Windows.

Это не обязательный путь для основного Python pipeline, но полезно, если нужен быстрый локальный CPU transcription path вне Python-стека.

## Практические Замечания

- `pipeline/highlight.py` импортирует `numpy` на уровне модуля, поэтому без него production pipeline не загрузится.
- В коде много defensive branches: если optional dependency отсутствует, проект старается работать на более простом уровне, а не падать без объяснения.
- `settings.yaml` - это editable runtime config, но truth source для defaults и нормализации находится в `pipeline/config.py`.
- GUI summary строится из реального `episode_report.json` и metadata файлов, а не из абстрактного состояния окна.
- Output считается полноценным только после export + metadata + report writing.

## Что Уже Есть в Репозитории

В дереве уже присутствуют:

- улучшения GUI, включая preview папки output и per-file status;
- unit tests, которые документируют intended behavior;
- подробный whisper.cpp setup guide;
- optional extension points для remote quality / VLM style integration.

Если вы расширяете проект дальше, держите README в синхронизации с `pipeline/config.py`, `pipeline/highlight.py` и `gui.py`, потому что реальное поведение здесь задаётся не одной функцией, а комбинацией нормализации конфигурации, fallback-веток и watchdog-ограничений.
