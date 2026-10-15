# jev-as-orchestrator

Эксперименты по выбору первого `get_skill(name)` на размеченном корпусе.
Ровно два раннера: `run_openrouter.py` для Jev через OpenRouter и `run_hf.py`
для открытых моделей с Hugging Face, локально или через API.

## Установка

Рекомендуется отдельное окружение для каждого семейства моделей: зависимости их
нативных библиотек могут различаться. Базовый код требует Python 3.10+, Kev — 3.12/3.13.

```bash
cd /Users/tsagoll/SberAI/my_name_is_jev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Для локальной модели установи **один** нужный набор зависимостей:

```bash
python -m pip install -r requirements-frida.txt
# либо
python -m pip install -r requirements-decider.txt
# либо (Python 3.12/3.13)
python -m pip install -r requirements-kev.txt
```

Для CUDA нужна подходящая сборка PyTorch под GPU/драйвер машины.
Веса скачиваются с HF при первом локальном запуске и затем используются из кеша.
У Kev также скачивается базовая модель, если checkpoint содержит LoRA.
`--model` принимает HF repo ID либо путь к уже скачанному checkpoint;
`--adapter` нужен, если семейство нельзя определить по имени пути.
Модели с другой архитектурой требуют своего адаптера или совместимого endpoint.

Экспортируй `OPENROUTER_API_KEY` / `HF_TOKEN` или заполни их в `.env`
по образцу `.env.example`. Существующий `.env` менять не требуется.
Значения из окружения имеют приоритет. В конфиг попадают только имена переменных,
ключи не сохраняются. В URL endpoint не следует помещать токены.

## Запуск

Одношаговый Jev: выбор по `name`, `title`, `description`, `tags` всех скиллов.

```bash
python run_openrouter.py --model typesafe/jev-1.13 --strategy 1-step \
  --system-prompt prompts/routing.txt
```

Двухшаговый Jev: top-5 по метаданным, затем новое решение по полным текстам этих пяти скиллов.
`--shortlist-k` принимает 3, 4 или 5, по умолчанию 5.

```bash
python run_openrouter.py --model typesafe/jev-1.13 --strategy 2-step \
  --shortlist-k 5 --system-prompt prompts/routing.txt
```

Локальные открытые модели на GPU:

```bash
python run_hf.py --model ai-forever/FRIDA-Decisions --mode local \
  --strategy 2-step --shortlist-k 5 --device cuda --dtype bfloat16 \
  --system-prompt prompts/routing.txt

python run_hf.py --model Mapika/decider-2b --mode local \
  --strategy 1-step --device cuda --system-prompt prompts/routing.txt

python run_hf.py --model jaredpalmer/kev-4b --mode local \
  --strategy 2-step --shortlist-k 3 --device cuda --dtype bfloat16 \
  --system-prompt prompts/routing.txt
```

Можно задать `--revision <commit-or-tag>` и `--cache-dir /path/to/cache`.
Для воспроизводимых сравнений фиксируй ревизию весов; версии установленных
библиотек, хеши кода и данных сохраняются в конфиге.

Проверка форматов без API, скачивания весов и запуска модели:

```bash
python run_openrouter.py --model typesafe/jev-1.13 --strategy 1-step --dry-run
python run_hf.py --model ai-forever/FRIDA-Decisions --strategy 2-step --dry-run
```

`--dry-run` создаёт конфиг, снимки данных, пример запроса первого шага и метрики
со статусом `dry_run` и `null` вместо accuracy. Он не проверяет доступность модели/API.

Полезные общие флаги:

- `--limit 10`, `--offset 0` — выбрать часть корпуса.
- `--metadata-fields name title description tags` — поля первого шага.
- `--shuffle-skills`, `--shuffle-samples`, `--seed 42` — порядок вариантов/примеров.
  Перемешивание каталога происходит один раз на прогон, порядок сохраняется.
- `--corpus`, `--skills-dir`, `--output-dir` — свои пути; дефолтные пути привязаны к корню проекта.
- `--missing-labels keep|error` — сохранить примеры с отсутствующими скиллами либо остановиться до инференса.
- `--timeout 120`, `--retries 3` — таймаут HTTP и число повторов временных ошибок.
- `--fail-fast` — остановиться на первом неудачном примере.
- `--max-input-tokens 32768` — локальный лимит: у FRIDA на state+инструкцию+одного кандидата,
  у Decider/Kev на полный запрос. Большие значения требуют больше памяти.
- `--env-file /path/to/.env`, `--api-key-env OTHER_VARIABLE` — загрузка ключа API.

## Hugging Face API

Раннер умеет обращаться к HF Inference Endpoint с custom handler:

```bash
python run_hf.py --model ai-forever/FRIDA-Decisions --mode api \
  --endpoint https://YOUR-ENDPOINT.endpoints.huggingface.cloud \
  --api-format hf-handler --strategy 2-step --shortlist-k 5 \
  --system-prompt prompts/routing.txt
```

Тело запроса — `{"inputs": {"model": "...", "state": {}, "questions": {}}}`.
Ответ — нативный объект с `answers.skill.probabilities` для **всех** кандидатов
и `answers.skill.choice`; допустим также список с единственным таким объектом.
HF repo ID сам по себе не означает доступность serverless inference:
этим decision heads нужен свой обработчик. Chat-completions и сгенерированные
моделью JSON с «вероятностями» здесь не используются.

Шаблон для развёртывания находится в `deploy/hf_endpoint/`:

1. Создай HF model repo с нужными весами и конфигами (например, копию FRIDA-Decisions).
2. Положи `deploy/hf_endpoint/handler.py` и `requirements.txt` в корень этого repo.
   Также скопируй в корень каталог `routing_bench/` из этого проекта.
3. Создай HF Inference Endpoint с Custom handler. Шаблон по умолчанию использует FRIDA.
   Для Decider/Kev задай `DECISION_ADAPTER=decider` / `kev` и замени нативную зависимость
   в endpoint `requirements.txt` соответствующей строкой из `requirements-decider.txt` / `requirements-kev.txt`.
   При необходимости задай `DECISION_DEVICE`, `DECISION_DTYPE`, `DECISION_MAX_INPUT_TOKENS`.
4. Передай URL раннеру через `--endpoint`, токен — через `HF_TOKEN`.

Для уже работающего TypeSafe-совместимого сервера FRIDA/Decider/Kev:

```bash
python run_hf.py --model jaredpalmer/kev-4b --mode api \
  --endpoint http://127.0.0.1:8009/v1/systemone --api-format systemone \
  --strategy 1-step
```

В API-режиме модель фактически выбирается конфигурацией сервера, а `--model`
передаётся в запросе и используется в имени эксперимента. Некоторые нативные
серверы принимают только свои aliases: тогда передай поддерживаемый alias.
Фактический `model` из ответа всегда остаётся в details. Сам раннер endpoint не развёртывает.

## Артефакты

```text
experiments/
  1-step/
    typesafe__jev-1.13_20261015T150000.123456+0300/
      config.json
      samples.json
      skills.json
      stage_1_winners.jsonl
      stage_1_details.jsonl
      errors.jsonl
      metrics.json
  2-step/
    ai-forever__FRIDA-Decisions_20261015T150000.123456+0300/
      config.json
      samples.json
      skills.json
      stage_1_winners.jsonl
      stage_1_details.jsonl
      stage_2_winners.jsonl
      stage_2_details.jsonl
      errors.jsonl
      metrics.json
```

Время записывается с часовым поясом `Europe/Moscow` и UTC offset; микросекунды
защищают от совпадения имён. Каждый запуск создаёт новую папку.

- `config.json`: модель, стратегия, время начала/конца, статус, все CLI-параметры,
  эффективный системный промпт, структура входа, порядок вариантов и примеров,
  хеши корпуса/кода, версия Python/пакетов, настройки нативного адаптера.
  Нативный Decisions API не имеет chat-роли system: промпт передаётся в
  `questions.skill.instructions`, пользовательский текст — в `state.query`.
- `samples.json`, `skills.json`: снимок выбранных примеров и полного каталога с метаданными,
  исходными текстами и SHA-256 каждого файла. Эталонные ответы модели не передаются.
- `stage_N_winners.jsonl`: по строке на пример: ID, запрос, эталоны, победитель,
  его вероятность, полный ranking и статус.
- `stage_N_details.jsonl`: те же ID плюс вероятности **всех** предложенных вариантов,
  `top_10`, точный запрос, сырой ответ, исходная сумма вероятностей, порядок вариантов
  и время вызова с HTTP-повторами. На втором шаге вариантов только 3–5,
  поэтому top-10 содержит весь shortlist. Объёмы токенов/стоимость сохраняются
  в сыром `response.usage`, когда backend их возвращает.
- `metrics.json`: `accuracy@1`, `accuracy@3`, `accuracy@5` в диапазоне 0..1,
  числители, число оценённых/плановых/необработанных примеров, ошибки и статус.
  В двухшаговом прогоне accuracy берётся **только со второго шага**;
  дополнительно есть диагностический `shortlist_hit_rate` первого шага.
- `errors.jsonl`: ошибки отдельных примеров. После ошибки первого шага второй
  получает статус `skipped`; такой пример считается промахом.

Файлы JSONL дописываются и flush выполняется после каждого шага. При обычном
исключении или Ctrl+C уже записанные результаты сохраняются, конфиг и метрики
получают статус `failed`/`interrupted`. У неполного прогона `complete=false`,
accuracy считается по завершённым примерам, а не выдаётся за метрику полного корпуса.
Коды возврата: 0 — успех/dry-run, 1 — ошибки прогона, 2 — ошибки аргументов/данных,
130 — прерывание.

## Как считается accuracy

Пример считается правильным на `@k`, если хотя бы одна метка `expected_skills`
попала в первые k позиций итогового ранжирования. Это hit@k, не доля найденных
эталонов при нескольких допустимых ответах. Ошибки инференса входят в знаменатель
как промахи. Неизвестные метки не удаляются и не подменяются.

На втором шаге ранжируются только прошедшие отбор кандидаты: эталон, потерянный
на первом шаге, не возвращается в выборку. Вероятности второго шага условны
на shortlist; с вероятностями первого шага они не перемножаются.
`k` ограничен числом вариантов: при shortlist=3 значения `@3` и `@5` совпадут,
а при shortlist=5 `@5` для успешного инференса равен попаданию эталона в shortlist.

Используются нативные вероятности моделей. При округлении API сумма может немного
отличаться от 1: дрейф не больше 0.02 нормализуется, исходный ответ сохраняется.
Пропущенные/лишние кандидаты, NaN, отрицательные вероятности и нулевая масса — ошибки.
При равных вероятностях первым идёт нативный `choice`, затем порядок входных вариантов.
Эти распределения позволяют сравнивать ранжирование; их калибровку этот benchmark не проверяет.

В текущем корпусе 521 пример, 64 скилла. Для `get_education_analytics` и
`headcount_staffing_block` нет файлов; 20 примеров содержат такие метки, у 19 нет
ни одной доступной правильной метки. Они остаются в оценке по умолчанию:
теоретический потолок accuracy на текущем полном корпусе — 502/521 ≈ 96.35%.
Исправление разметки/добавление скиллов — отдельное изменение исходных данных.

У FRIDA стандартные caps могут обрезать инструкции и варианты. Адаптер увеличивает
их до реальной длины сегментов и записывает эффективные лимиты в сырой ответ.
Это даёт полный текст на втором шаге, но может выходить за обученную длину модели
и заметно увеличивать память/время. Вместо тихого усечения превышение
`--max-input-tokens` считается ошибкой. У Decider проверяется длина запроса;
у Kev используются нативная проверка переполнения и дополнительный лимит раннера.
В удалённом API точные токенизацию и ограничения определяет развёрнутый backend;
ответ с явным `truncated=true` отклоняется.

## Проверки и источники интерфейсов

```bash
python -m unittest discover -s tests -v
```

Тесты работают без весов и внешнего API: проверяют парсинг, оба сценария, потерю
эталона в shortlist, несколько допустимых ответов, ошибки, сохранение артефактов,
HTTP envelope/повторы и отсутствие обрезания опций в адаптере FRIDA.
Это не заменяет проверку установленных нативных runtime с реальными весами на GPU.

- [OpenRouter Decisions API](https://openrouter.ai/blog/tutorials/how-to-use-jev/)
- [FRIDA-Decisions v0.4.0](https://github.com/ai-forever/FRIDA-Decisions/tree/v0.4.0)
- [Decider](https://github.com/Mapika/decider)
- [Kev 1.0](https://github.com/jaredpalmer/kev/tree/kev-1.0)
- [HF custom Inference Handler](https://huggingface.co/docs/inference-endpoints/guides/custom_handler)
