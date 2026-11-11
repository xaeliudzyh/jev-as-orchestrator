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

## Единый запуск

Основной вход — **`exp2/run_exp2.py`**. По умолчанию: OpenRouter, два этапа,
shortlist=5, новая русская сценарная политика `routing_scenarios_ru_exp2.txt`,
193 запроса и 17 включённых кандидатов. Один запуск получает результаты обеих
стратегий без повторного первого вызова.

Из корня репозитория, в активированном окружении с зависимостями:

```bash
# Сразу 1-step и 2-step: descriptions → top-5 → full texts.
python3 exp2/run_exp2.py --model typesafe/jev-1.14
python3 exp2/run_exp2.py --model openai/gpt-6-luna-decisions

# Только 1-step: один вызов на вопрос, без второго этапа.
python3 exp2/run_exp2.py --model typesafe/jev-1.14 --one-step-only
python3 exp2/run_exp2.py --model openai/gpt-6-luna-decisions --one-step-only

# Альтернативы: shortlist=3, английская версия политики, полный каталог.
python3 exp2/run_exp2.py --model typesafe/jev-1.14 --shortlist-k 3
python3 exp2/run_exp2.py --model typesafe/jev-1.14 --system-prompt exp2/routing_scenarios_en_exp2.txt
python3 exp2/run_exp2.py --model typesafe/jev-1.14 --include-disabled
```

`--strategy 1-step|2-step` (также 1|2) сохранён для совместимости; по умолчанию
2-step. Его нельзя одновременно передавать с `--one-step-only`.
`--shortlist-k` влияет только на второй вызов, а @1/@3/@5 считаются автоматически.

Старые `run_openrouter_exp2.py` и `run_hf_exp2.py` оставлены как тонкие совместимые
входы в тот же раннер с соответствующим backend по умолчанию. У них также новые
defaults и сохранение пары результатов. Для новых команд используй run_exp2.py.
Exp1 сохраняет прежние параметры, промпты и схему сохранения.

Полный режим с явными путями к JSONL:

```bash
python3 exp2/run_exp2.py --model typesafe/jev-1.14 --include-disabled \
  --bucket data/subagent_routing_exp2/subagents_routing_bucket.jsonl \
  --corpus data/subagent_routing_exp2/subagents_routing_corpus.jsonl
```

## Сценарная политика в промпте

Новые эквивалентные версии: [русская](routing_scenarios_ru_exp2.txt) (default)
и [английская](routing_scenarios_en_exp2.txt). Старые `routing_exp2.txt` и
`EN_routing_exp2.txt` сохранены для контрольных сравнений; выбрать любой можно
через `--system-prompt PATH`. Один и тот же текст применяется на обоих этапах.

Формат ответа Choice и смысл confidence определяет нативный backend; top-k
строит раннер. Промпт задаёт семантику и приоритеты маршрутизации, без инструкций
генерировать JSON/объяснение или управлять инструментами. Тексты запроса и
кандидатов рассматриваются как данные для выбора маршрута.

Промпт задаёт Main как оркестратора и определяет владельцев сценариев при
пересечении descriptions. Например, стандартные HR-ссылки, вовлечённость и
создание встреч направляются в hrp-analytics; переработки — в vacation;
командные теги/хобби/навыки, включая срезы по блокам, — в team_profile.
Разделены обычный профиль и развивающая оценка роли, поиск учебных материалов
и статусы назначенного обучения, внешний executive sourcing и внутренние преемники.
Неопределённый результат/уточнение требований имеет приоритет grill; отсутствие
даты/ФИО при понятном действии не меняет специалиста.

Это **политика маршрутизации данного развёртывания**, построенная на наблюдавшихся
ошибках и эталонах, а не утверждение о новых возможностях субагентов.
Она явно имеет приоритет над конфликтующими descriptions. Доступ к данным и
выполнимость проверяет выбранный агент. Каталог, AGENTS.md и разметка не изменены;
эталон и skill_name не отправляются модели. Правила сценариев общие, без таблицы
sample_id → label или копирования всей корзины в инструкцию.

Обоснование, примеры границ и неоднозначные эталоны:
[scenario_policy_notes_exp2.md](scenario_policy_notes_exp2.md).
Сценарный промпт уже дал 91–94% top-1 на текущей корзине. Проверка расчётов,
спорных попаданий и утечки правил из разметки:
[разбор последних запусков](../experiments/exp2/analysis/prompt_generalization_20261109/README.md).
Эти 193 запроса использованы для настройки, поэтому их score является
результатом на данных разработки; перенос на production не измерен.

## Общий промпт для проверки переноса

Добавлены [routing_general_ru_exp2.txt](routing_general_ru_exp2.txt) и
[routing_general_en_exp2.txt](routing_general_en_exp2.txt). В них нет имён агентов,
эталонных маршрутов, догадок о скрытом сегменте пользователя и правил,
добавляющих отсутствующие в каталоге возможности. Выбор строится по действию,
объекту, ожидаемому результату, источникам данных и явно указанным ограничениям.
Полный текст уточняет и при необходимости сужает краткое описание.

Исторический сценарный RU default сохранён для воспроизводимости. Для запуска
**общего** варианта укажи --system-prompt явно:

```bash
python3 exp2/run_exp2.py --model typesafe/jev-1.14 \
  --system-prompt exp2/routing_general_ru_exp2.txt
python3 exp2/run_exp2.py --model openai/gpt-6-luna-decisions \
  --system-prompt exp2/routing_general_ru_exp2.txt

# Английская версия; оба этапа и отдельные метрики также сохраняются.
python3 exp2/run_exp2.py --model typesafe/jev-1.14 \
  --system-prompt exp2/routing_general_en_exp2.txt
```

`--one-step-only` работает с любым промптом. Новый общий вариант проверен только
в dry-run; реальных метрик для него пока нет. Он менее привязан к разметке, но
также создан после просмотра этой корзины. Для независимой оценки нужен новый
набор, охватывающий все 17 агентов и новые семейства сценариев. Снижение score
на неоднозначных/конфликтующих gold не равнозначно ухудшению production-роутинга;
рост переноса также нельзя утверждать без новой проверки.

## Стратегии

- **1-step:** один вызов; `state.query` — только пользовательский вопрос,
  `questions.subagent.instructions` — выбранный системный промпт, `criteria` —
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

Backend `openrouter` используется по умолчанию. Токен — `OPENROUTER_API_KEY`
из окружения или `.env`. `--endpoint` и `--api-key-env` позволяют явно заменить
URL и имя переменной с ключом. Примеры запуска приведены выше.

Используется `/api/alpha/decisions`, SystemOne-формат `model/state/questions`.
Это не `/chat/completions`. Прямой OpenAI `/v1/decisions` имеет другой wire format
(`input`, массив questions с choices) и этим OpenRouter-раннером не вызывается.
Примеры форматов и источники: [models_exp2.md](models_exp2.md).

## Открытые модели

Все старые адаптеры доступны: FRIDA, Decider-2B/4B, Kev, Jeva-4B.

```bash
python3 exp2/run_exp2.py --backend hf --model ai-forever/FRIDA-Decisions --one-step-only --device cuda --dtype bfloat16
python3 exp2/run_exp2.py --backend hf --model Mapika/decider-4b --shortlist-k 3 --device cuda --dtype bfloat16
python3 exp2/run_exp2.py --backend hf --model LIQIIIII/Jeva-4B --shortlist-k 5 \
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
python3 exp2/run_exp2.py --backend hf --model Cloudflare/clef-flash --adapter clef \
  --one-step-only --device cuda --dtype bfloat16 --clef-quantization 4bit --limit 3
python3 exp2/run_exp2.py --backend hf --model Cloudflare/clef-flash --adapter clef \
  --shortlist-k 3 --device cuda --dtype bfloat16 \
  --clef-quantization 4bit --max-input-tokens 32768 --limit 3

# Clef 27B: нативный endpoint на машине с достаточной памятью.
python3 exp2/run_exp2.py --backend hf --model Cloudflare/clef --shortlist-k 5 \
  --mode api --endpoint http://127.0.0.1:30000/v1/systemone --api-format systemone
```

Endpoint должен действительно поддерживать Clef joint schema head/SystemOne
(например, поддерживающая Clef сборка SGLang); обычный chat server не подходит.
Его развёртывание не входит в раннер. В API-режиме квантование/лимиты определяет
сервер; `--clef-quantization` действует только на локальную загрузку.

На 5060 Ti 16 GiB локальный стартовый вариант — **Clef-flash 4-bit**.
Пик на нашей корзине не измерен. Расчётные нижние границы с BF16 embeddings:
Flash BNB4 ≈7.22 GiB / BNB8 ≈10.66 GiB; Clef 27B BNB4 ≈16.29 GiB,
до head/scales/активаций. Поэтому 27B стандартным BNB loader целиком на GPU
не помещается. Прежняя цифра 13.5 GB была идеальной оценкой всех параметров
по 4 бита. Есть альтернативные Artemis GGUF-сборки 27B для llama-server,
но опубликованные пики не проверяют длинные входы exp2 на 5060 Ti.
Подробности и источники: [models_exp2.md](models_exp2.md#память-на-5060-ti-16-gb).

Ревизии Clef по умолчанию закреплены в `routing_bench/clef.py`, обе на релизах
30.10.2026; override — `--revision`. Native Python-код из этого же checkpoint
загружается вместе с весами, его SHA-256 записывается в config. Используются
`load_release_model` и `systemone` из `joint_schema_model.py`, **не generate()**.
До forward проверяется полный токенизированный вход: тихая обрезка state
нативным encoder запрещена. Общий лимит включает query, schema и все варианты.
У Clef нет Jeva option cache/chunking; их CLI-флаги на Clef не действуют.

## Clef через прямой Cloudflare REST API

Добавлен формат **--api-format cloudflare**, раскрывающий REST-обёртку result.
Нужны Cloudflare Account ID и API token Workers AI Read/Edit; аккаунт должен
иметь достаточную квоту/биллинг. [Официальная настройка](https://developers.cloudflare.com/workers-ai/get-started/rest-api/).
Ключ CLOUDFLARE_API_TOKEN положи в окружение или .env. Account ID использует
оболочка при сборке URL, поэтому экспортируй его отдельно:

```bash
export CLOUDFLARE_ACCOUNT_ID='YOUR_ACCOUNT_ID'
```

Полная текущая корзина: 193 запроса, 17 кандидатов; оба этапа, shortlist=5,
обобщённый EN-промпт. Флага --limit нет, поэтому это не частичный запуск:

```bash
python3 exp2/run_exp2.py --backend hf --mode api --api-format cloudflare \
  --model clef-flash \
  --endpoint "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/cloudflare/clef-flash" \
  --api-key-env CLOUDFLARE_API_TOKEN \
  --system-prompt exp2/routing_general_en_exp2.txt --shortlist-k 5 --timeout 180

python3 exp2/run_exp2.py --backend hf --mode api --api-format cloudflare \
  --model clef \
  --endpoint "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/cloudflare/clef" \
  --api-key-env CLOUDFLARE_API_TOKEN \
  --system-prompt exp2/routing_general_en_exp2.txt --shortlist-k 5 --timeout 180
```

Здесь backend hf обозначает общий путь local/native HTTP; сам запрос идёт
на заданный Cloudflare URL, а не в Hugging Face. model — clef-flash/clef,
не Cloudflare/clef-flash и не @cf/cloudflare/clef-flash. Новый формат проверяет
ошибки обёртки до оценки и сохраняет сырой envelope в
stage_N_details.jsonl → response.cloudflare_response.

Для предварительной проверки соединения добавь --limit 2 --fail-fast;
после неё запускай команды выше без --limit. Для только 1-step добавь
--one-step-only. Полный успешный 2-step — 386 вызовов на модель без HTTP retry,
результаты обеих стратегий сохраняются в парные директории experiments/exp2.
Проверь metrics.status=completed, complete=true, n_samples=193 и n_errors=0;
completed_with_errors всё ещё содержит пропуски ответов. Раннер не возобновляет
прерванный прогон автоматически.

На локальную VRAM API-прогон не загружает веса. --clef-quantization, --dtype,
--device и --max-input-tokens не управляют удалённым сервисом. Hosted Flash
имеет контекст 24576, Clef — 65536; данные state и всей schema расходуют его
вместе. Превышение может обрезать state или вызвать ошибку schema.
Актуальный общий промпт короткий, однако гарантия отсутствия обрезки на
удалённом сервисе требует проверки его usage/лимитов; dry-run этого не делает.

Совместимость транспорта проверена mock-тестами и dry-run. Реальные ключи и
платные API-вызовы в этом изменении не использовались.

## Артефакты и оценка

По умолчанию один запуск создаёт **две связанные директории с одинаковым ID**:

```text
experiments/exp2/1-step/<model>_<time>/
experiments/exp2/2-step/<model>_<time>/
```

В 1-step сохраняются точные `stage_1_winners.jsonl`, `stage_1_details.jsonl` и
отдельные `metrics.json` первого этапа. В 2-step сохраняются оба этапа и итоговые
метрики второго. Stage-1 JSONL в двух папках побайтово совпадают: это одни и те же
вызовы, а не независимый повтор. При `--one-step-only` создаётся только 1-step.
При успешном проходе 193 запросов — 386 вызовов для пары результатов либо 193
при one-step-only (HTTP-повторы при временных ошибках сюда не включены).

`config.json` содержит run_id, pipeline_strategy, собственные strategy/evaluated_stage,
paired_artifact_dir, происхождение artifact_origin, фактический текст/хеш промпта,
хеши bucket/corpus/исходников и настройки backend. В first-stage view
`arguments.strategy` отражает исходный запуск всего pipeline (2-step), а
`strategy=1-step` и `evaluated_stage=1` — оцениваемый результат.
`samples.json` и `subagents.json` — одинаковые снимки данных в паре.

`stage_N_winners.jsonl` и `stage_N_details.jsonl` flush после каждого вызова;
details хранит точный запрос, полный сырой ответ, все вероятности, порядок
кандидатов, timing/usage. Winners отдельно хранит `probability` победителя и
**нативный** `confidence` (null при отсутствии поля); одно не подменяет другое.
Ошибки первого этапа находятся в обеих папках, ошибки второго — только в 2-step.
Ошибка stage 1 пропускает stage 2 и даёт промах в обоих результатах.
Ошибка stage 2 сохраняет успешный stage 1 и не увеличивает его n_errors.

При прерывании n_samples, n_samples_unprocessed и complete считаются отдельно
для каждого результата. Например, ответ первого этапа уже мог быть сохранён,
а второй вызов прерван. Первый результат может быть completed, хотя весь pipeline
interrupted/failed: исходный статус и ошибка видны в pipeline_status/pipeline_error.
Незавершённый first-stage view наследует статус interrupted/failed.

`elapsed_seconds` у 2-step — полное время pipeline. У извлечённого 1-step это сумма
latency **завершённых записанных вызовов** stage 1; общая длительность пары вынесена
в pipeline_elapsed_seconds. `timing_scope` объясняет различие,
`stage_inference_seconds` и inference_seconds сохраняют суммы latency.
Время и поведение кеша в смешанном двухэтапном проходе не равны автономному
1-step benchmark; для сравнения скорости используй отдельный --one-step-only.

При dry-run обе папки содержат config, snapshots, null metrics и одинаковый
`request_preview.json` **только первого этапа**. Фактический запрос stage 2 зависит
от ещё не полученного shortlist, поэтому dry-run не выдумывает его.

`accuracy@1/3/5` — per-sample hit@k, от 0 до 1; ошибки считаются промахами.
Для 2-step это итог **второго** вызова, дополнительно есть `stage_1_metrics` и
`shortlist_hit_rate`. При shortlist=3 значения @3 и @5 совпадают.
Без ошибок второго этапа при shortlist=5 итоговая @5 равна first-stage @5
(также shortlist_hit_rate); при shortlist=3 итоговые @3/@5 равны first-stage @3.
Второй этап переставляет варианты, а не возвращает отсутствующего в shortlist
агента. Улучшиться могут метрики с k меньше размера shortlist.
Извлечённая 1-step метрика — точная оценка первых вызовов этого pipeline;
отдельный прогон с теми же флагами может отличаться из-за вариативности API,
версии endpoint, порядка вызовов или кеша. Stage-2
вероятности условны на shortlist и не сравнимы напрямую с вероятностями среди полного списка кандидатов первого шага.
При нескольких допустимых метках общий движок считает успех по любой из них;
в текущем bucket SubAgent содержит одну метку, строка не делится по запятым.

По умолчанию отсутствующая в каталоге метка останавливает exp2 до инференса;
`--missing-labels keep` разрешает сохранить её как недоступную, без подмены.
Неполный прогон помечается `complete=false`, метрика считается по обработанным
примерам, unprocessed записываются отдельно. Это не результат полного корпуса.

```bash
python3 exp2/run_exp2.py --backend hf --model Cloudflare/clef-flash --dry-run
python3 exp2/run_exp2.py --model openai/gpt-6-luna-decisions --one-step-only --dry-run
python -m unittest discover -s tests -v
```

Dry-run проверяет данные/формирование входа без ключей, скачивания весов и API.
Он не проверяет доступность endpoint, лимиты его токенизатора и GPU-совместимость.
Первые реальные 1-step прогоны Jev/Luna выполнены на полном каталоге 199/21.
Их нельзя выдавать за метрики нового режима 193/17: изменение конкурирующих
вариантов требует нового инференса.

## Анализ порогов confidence

[Отчёт по трём русским промптам, Jev/Luna и обоим этапам](../experiments/exp2/analysis/confidence_ru_20261110/README.md)
содержит графики accuracy/покрытия, таблицы порогов, числа принятых и отсеянных
запросов, правильных ответов среди отсеянных и примеры уверенных промахов.
Для базового RU-промпта на корзине 193/17 есть только 1-step.

Повторный расчёт по сохранённым ответам, без моделей и API:

```bash
python3 exp2/analyze_confidence_exp2.py --output-dir /tmp/confidence-exp2-report
```

Для графиков нужен `matplotlib`. Скрипт выбирает последние полные результаты
Jev 1.14/Luna на текущих 193 вопросах/17 кандидатах, проверяя хеши корзины,
каталога и версии каждого RU-промпта; для 2-step требуется shortlist=5.
Нативный `response.answers.subagent.confidence` не заменяется вероятностью
победителя. Принимается успешный ответ с `confidence >= threshold`.
Accuracy считается **среди принятых**, покрытие — от всех 193 вопросов.
API-ошибки и отсутствующий confidence учитываются отдельно от фильтрации.
На втором этапе используется confidence именно второго вызова.

Пороги подбираются для минимального покрытия 90%, 80%, 70% и 50%; основная
таблица сохраняет не менее 80%. При равной точности выбирается большее покрытие.
Это подбор на той же корзине, а не независимая оценка production-качества.
В раннер автоматический отказ по confidence не добавлен: отчёт оценивает фильтр
постфактум, существующие результаты не перезаписываются.
