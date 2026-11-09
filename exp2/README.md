# exp2: subagent routing

По умолчанию используются **193 запроса** из
`data/subagent_routing_exp2/subagents_routing_bucket_only_enabled.csv` и
**17 включённых субагентов** из `subagents_routing_corpus.csv`, отфильтрованного
по `enabled=1`. Полная корзина `subagents_routing_bucket.csv` (199 запросов)
и полный каталог (21 субагент) остаются доступными через `--include-disabled`.

CSV: UTF-8 с BOM, разделитель `;`, многострочные ячейки читаются штатным CSV parser.
Любой CSV можно заменить соответствующей `.jsonl` копией через `--bucket` / `--corpus`.
Корзины содержат `Вопрос в новой корзине`, `skill_name`, `SubAgent`.
Каталог содержит `target_subagent`, `target_subagent_description`,
`target_subagent_full_text`, **`enabled`**. В JSONL enabled — целое число 0/1,
в CSV — значение 0/1. Иные значения, пропущенная колонка и дубли имён отклоняются.

`enabled` взят из YAML front matter исходных `agents/*/AGENTS.md`: true → 1,
false → 0. Для `mcp_internet` установлен 1 по явному правилу пользователя,
несмотря на отсутствие поля в его AGENTS.md. Дополнительно по решению пользователя
от 09.11.2026 для устаревшего **analytics** установлен enabled=0 в каталоге.
Его исходный AGENTS.md сохранён; CSV/JSONL являются источником enabled для раннера.
Выключены **analytics**, **interview**, **people-search**, **react-analytics**.
Из исходной корзины исключены 4 запроса с эталоном analytics и 2 с эталоном interview;
у people-search и react-analytics эталонных примеров нет. Порядок и все ячейки оставшихся строк сохранены.
Оригинальная корзина не изменена. `sample_id` — индекс в выбранном файле корзины;
после удаления строк эти индексы могут отличаться от старого полного прогона.

`SubAgent` — единственная эталонная метка. `skill_name` сохраняется только для
происхождения примера. `enabled` управляет фильтрацией и сохраняется в metadata
снимка каталога, но не добавляется в state/description/full text, передаваемые модели.
В full text по-прежнему только front matter name/description и исходное тело AGENTS.md.
Исходные AGENTS.md раннер во время запуска не читает: актуальные значения enabled
должны находиться в CSV/JSONL каталога.

Включённые кандидаты без примеров также участвуют: meetings, metrics, outstaff,
recruiter-brief, service_navigation. Фильтрация не зависит от наличия gold-примеров.
Другие runtime-настройки, включая scope, и внутренние skills каталог не фильтруют.

## Выбор корзины и кандидатов

- Без дополнительных флагов: `bucket_only_enabled.csv` и `corpus[enabled=1]`.
- `--include-disabled`: исходный `bucket.csv` и полный corpus, включая enabled=0.
- `--bucket PATH` и `--corpus PATH` заменяют соответствующие пути. Явный --bucket
  имеет приоритет над выбором файла по умолчанию, в том числе с --include-disabled.
- Без --include-disabled из явно выбранной корзины дополнительно удаляются строки
  с эталонами выключенных субагентов известного каталога. Неизвестные метки
  не удаляются молча: они подчиняются --missing-labels (по умолчанию error).
- Фильтрация выполняется до shuffle, offset и limit. Режим, исключённые кандидаты,
  число строк до фильтрации и IDs исключённых строк сохраняются в config.json.

Команды для только включённых (из корня репозитория):

```bash
# 1-step: все 17 включённых кандидатов, метрики @1/@3/@5 автоматически.
python3 exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 1-step
python3 exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 1-step

# 2-step: descriptions 17 включённых → top-5 → полные тексты этих пяти.
python3 exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 2-step --shortlist-k 5
python3 exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 2-step --shortlist-k 5
```

Прежний режим со всеми 199 запросами / 21 кандидатом:

```bash
python3 exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 1-step --include-disabled
python3 exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 2-step --shortlist-k 5 --include-disabled
```

Тот же полный режим с явными путями к JSONL:

```bash
python3 exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 1-step --include-disabled \
  --bucket data/subagent_routing_exp2/subagents_routing_bucket.jsonl \
  --corpus data/subagent_routing_exp2/subagents_routing_corpus.jsonl
```

## Стратегии

- **1-step:** один вызов; `state.query` — только пользовательский вопрос,
  `questions.subagent.instructions` — `exp2/routing_exp2.txt`, `criteria` —
  имя каждого включённого субагента (17 по умолчанию) → точная строка `target_subagent_description`.
  Никакой JSON-обёртки с title/tags и добавления полного текста на первом шаге.
- **2-step:** такой же первый вызов, сортировка полного распределения,
  shortlist из первых `--shortlist-k 3|5`. Второй независимый вызов с тем же
  `state.query` и инструкцией: `criteria` содержит только выбранных субагентов
  в порядке первого ranking, значения — точные `target_subagent_full_text`.
  Вероятности первого шага не передаются и не перемножаются со вторым.

Нативный Choice выбирает один вариант, раннер строит top-k по распределению.
Эталон, не прошедший в shortlist, остаётся промахом. Запросов по одному: нет
батча разных пользовательских вопросов. У Jeva batch/chunk — про кодирование
вариантов одного решения; это независимые параметры от shortlist-k.

## OpenRouter: Jev и Luna

Проверенные на 05.11.2026 идентификаторы: `typesafe/jev-1.14` (явная версия),
`~typesafe/jev-latest` (плавающий alias), `openai/gpt-6-luna-decisions`.
Последнюю 6.1 Luna Decisions официальные источники не подтвердили.
Для воспроизводимости Jev лучше указывать явную версию.
Все команды выполняются из корня репозитория.

```bash
python exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 1-step
python exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 2-step --shortlist-k 3
python exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 2-step --shortlist-k 5

python exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 1-step
python exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 2-step --shortlist-k 3
python exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 2-step --shortlist-k 5
```

Используется `/api/alpha/decisions`, SystemOne-формат `model/state/questions`.
Это не `/chat/completions`. Прямой OpenAI `/v1/decisions` имеет другой wire format
(`input`, массив questions с choices) и этим OpenRouter-раннером не вызывается.
Примеры форматов и источники: [models_exp2.md](models_exp2.md).

## Открытые модели

Все старые адаптеры доступны: FRIDA, Decider-2B/4B, Kev, Jeva-4B.

```bash
python exp2/run_hf_exp2.py --model ai-forever/FRIDA-Decisions --strategy 1-step --device cuda --dtype bfloat16
python exp2/run_hf_exp2.py --model Mapika/decider-4b --strategy 2-step --shortlist-k 3 --device cuda --dtype bfloat16
python exp2/run_hf_exp2.py --model LIQIIIII/Jeva-4B --strategy 2-step --shortlist-k 5 \
  --device cuda --dtype bfloat16 --jeva-cache hybrid \
  --jeva-option-chunk-size 4 --jeva-option-batch-tokens 4096
```

Jeva cache влияет на повторное использование option memory и время, а не на
возможность decision block учитывать набор вариантов. Для измерения времени
без кеша используй `--jeva-cache none` и фиксируй этот выбор для сравнения.
На длинных full texts при нехватке VRAM уменьши chunk до 1; batch budget не
обрезает один длинный вариант. Нативные ограничения длины checkpoint действуют.

Добавлены **Cloudflare/clef-flash (9B)** и **Cloudflare/clef (27B)**, адаптер `clef`.
Нужен отдельный venv с `requirements-clef.txt`; для 4/8-bit —
`requirements-clef-quantized.txt`. Подходящую CUDA-сборку torch установи под свой GPU.

```bash
python -m pip install -r requirements-clef-quantized.txt
# Стартовая конфигурация для 5060 Ti 16 GB; сначала небольшой smoke run.
python exp2/run_hf_exp2.py --model Cloudflare/clef-flash --adapter clef \
  --strategy 1-step --device cuda --dtype bfloat16 --clef-quantization 4bit --limit 3
python exp2/run_hf_exp2.py --model Cloudflare/clef-flash --adapter clef \
  --strategy 2-step --shortlist-k 3 --device cuda --dtype bfloat16 \
  --clef-quantization 4bit --max-input-tokens 32768 --limit 3

# Clef 27B: нативный endpoint на машине с достаточной памятью.
python exp2/run_hf_exp2.py --model Cloudflare/clef --strategy 2-step --shortlist-k 5 \
  --mode api --endpoint http://127.0.0.1:30000/v1/systemone --api-format systemone
```

Endpoint должен действительно поддерживать Clef joint schema head/SystemOne
(например, поддерживающая Clef сборка SGLang); обычный chat server не подходит.
Его развёртывание не входит в раннер. В API-режиме квантование/лимиты определяет
сервер; `--clef-quantization` действует только на локальную загрузку.

Локально можно загрузить и 27B с `--clef-quantization 4bit`, но на 16 GB такой
прогон не гарантирован: только номинальные веса — около 13.5 GB, плюс неквантованные
компоненты, scale metadata, активации и workspace. 9B BF16 требует около 18 GB
только на номинальные веса и тоже не помещается. Оценки в десятичных GB;
показания GPU обычно в GiB. Квантованный вариант отмечай отдельной конфигурацией:
он может изменить вероятности и качество. Измерений VRAM/скорости на этих весах
в этом изменении не выполнялось.

Ревизии Clef по умолчанию закреплены в `routing_bench/clef.py`, обе на релизах
30.10.2026; override — `--revision`. Native Python-код из этого же checkpoint
загружается вместе с весами, его SHA-256 записывается в config. Используются
`load_release_model` и `systemone` из `joint_schema_model.py`, **не generate()**.
До forward проверяется полный токенизированный вход: тихая обрезка state
нативным encoder запрещена. Общий лимит включает query, schema и все варианты.
У Clef нет Jeva option cache/chunking; их CLI-флаги на Clef не действуют.

## Артефакты и оценка

По умолчанию каждый запуск пишет в `experiments/exp2/<1-step|2-step>/<model>_<time>/`.
`config.json` содержит хеши bucket, corpus, исходников и настройки backend;
`samples.json` и `subagents.json` — снимки входных данных; `request_preview.json`
есть при dry-run. `stage_N_winners.jsonl` и `stage_N_details.jsonl` flush после
каждого вызова; details хранит точный запрос, полный сырой ответ, все вероятности,
порядок кандидатов, timing/usage. Во winners теперь отдельно записываются
`probability` победителя и **нативный** `confidence` (null, если поле отсутствует).
Раннер не подменяет confidence вероятностью победителя.

`accuracy@1/3/5` — per-sample hit@k, от 0 до 1; ошибки считаются промахами.
Для 2-step это итог **второго** вызова, дополнительно есть `stage_1_metrics` и
`shortlist_hit_rate`. При shortlist=3 значения @3 и @5 совпадают. Stage-2
вероятности условны на shortlist и не сравнимы напрямую с вероятностями среди полного списка кандидатов первого шага.
При нескольких допустимых метках общий движок считает успех по любой из них;
в текущем bucket SubAgent содержит одну метку, строка не делится по запятым.

По умолчанию отсутствующая в каталоге метка останавливает exp2 до инференса;
`--missing-labels keep` разрешает сохранить её как недоступную, без подмены.
Неполный прогон помечается `complete=false`, метрика считается по обработанным
примерам, unprocessed записываются отдельно. Это не результат полного корпуса.

```bash
python exp2/run_hf_exp2.py --model Cloudflare/clef-flash --strategy 2-step --dry-run
python exp2/run_openrouter_exp2.py --model openai/gpt-6-luna-decisions --strategy 1-step --dry-run
python -m unittest discover -s tests -v
```

Dry-run проверяет данные/формирование входа без ключей, скачивания весов и API.
Он не проверяет доступность endpoint, лимиты его токенизатора и GPU-совместимость.
Первые реальные 1-step прогоны Jev/Luna выполнены на полном каталоге 199/21.
Их нельзя выдавать за метрики нового режима 193/17: изменение конкурирующих
вариантов требует нового инференса.
