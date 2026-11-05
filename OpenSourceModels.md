# Модели для routing: exp1 и exp2

Справка по моделям, поддержанным нашими раннерами, на 05.11.2026.
Результаты корпуса — в [exp1/README](exp1/README.md#результаты-skill-routing), подробная
сериализация SDK — в [docs/model_io.md](docs/model_io.md).

## Размеры и основные различия

| Модель | Число параметров | Как принимает решение | Что отличает от original Jev |
| --- | --- | --- | --- |
| FRIDA-Decisions | **0.823B**: 823 401 216 в encoder + 1 537 в голове | T5 encoder, отдельная оценка каждого кандидата с запросом; packed rows и block mask | Открытый encoder, ориентированный на русский текст; распределение строится из независимых candidate margins |
| Decider-2B | **1.882B**: 1 881 825 088 в опубликованном checkpoint | Qwen3.5-2B-Base, логиты меток вариантов в позиции ответа | Открытое воспроизведение класса System One; известны backbone, обучение и температуры |
| Decider-4B | **4.206B**: 4 205 751 296 в checkpoint | Qwen3.5-4B-Base, тот же slot readout, что у 2B | Более крупный вариант Decider с другими обучением и калибровкой |
| Jeva-4B | Текстовая часть **4.206B** + decision module **13.370M**, суммарно ≈**4.219B** после слияния LoRA | Независимое кодирование запроса и вариантов, затем decision stack по всему набору | Открытая архитектура с повторным использованием option memory и явной оценкой null |
| Kev-4B | Backbone ≈**4.2B**; LoRA **33.8M** до слияния, плюс pointer head | Qwen3.5-4B-Base, LoRA rank 16 и pointer readout по границам вариантов | Открытая реализация System One с собственной головой, масками и сервером |
| Cloudflare Clef-flash | **9B** (Qwen3.5-9B) + joint schema head | Общий мультимодальный backbone и отдельный transformer decision head, softmax по вариантам | Открытые веса, опубликованный native head; без option cache Jeva |
| Cloudflare Clef | **27B** (Qwen3.8-27B) + joint schema head | Та же схема решения, более крупный backbone | Более тяжёлый открытый decision endpoint; см. VRAM в exp2 |
| GPT-6 Luna Decisions | Не раскрыто | Закрытый OpenAI Decisions API через OpenRouter | Другое семейство и провайдер; внутренний readout не опубликован |
| Original Jev 1.13 / 1.14 | Не установлен из доступных артефактов API | Удалённый typed decision endpoint TypeSafe через OpenRouter | Референс для сравнения; внутренний backbone и токенизация нашим кодом не контролируются |

Точные числа FRIDA и Decider получены суммированием размерностей тензоров в
заголовках локальных safetensors; число decision module Jeva — из `head.pt`.
У Jeva файлы backbone содержат около 4.660B элементов, включая visual/MTP;
это не число параметров, участвующих в нашем текстовом решении. Число параметров
также не равно пику VRAM: нужны активации, рабочие буферы, кеши и служебные компоненты.
У Kev размер backbone и адаптера указан по model card; локального прогона пока нет.

## Общий пример из нашего корпуса

Реальный `sample_id=1`: **«мои делегирования»**, эталон — `delegation_view`.
Эталон используется только после инференса. Первый шаг формирует такой запрос
(код запускается из корня репозитория):

```python
from pathlib import Path
from routing_bench.data import load_skills

state = {"query": "мои делегирования"}
prompt = Path("exp1/routing_exp1.txt").read_text(encoding="utf-8").strip()
skills = load_skills(Path("data/skill_routing_exp1/skills"))
criteria = {
    s.name: s.description(False, ["name", "title", "description", "tags"])
    for s in skills
}
questions = {"skill": {
    "type": "choice", "instructions": prompt, "criteria": criteria,
}}
payload = {"model": model_id, "state": state, "questions": questions}
```

Здесь 64 варианта; значение criterion — строка с JSON метаданных. Например,
`delegation_view` имеет title «Мои делегирования», описание текущих делегирований
в системах Друг и Пульс и tags про делегирования, замещения и полномочия.
Во втором шаге остаётся top-k, а значения заменяются полными исходными файлами
скиллов. State и промпт сохраняются; вероятности первого шага не передаются.

Типовая форма ответа ниже показана **на условных числах и двух вариантах**;
это иллюстрация схемы, а не реальный результат нашего каталога из 64 скиллов:

```json
{
  "answers": {
    "skill": {
      "type": "choice",
      "choice": "delegation_view",
      "probabilities": {"delegation_view": 0.8, "default_links": 0.2},
      "confidence": 0.6
    }
  }
}
```

Число `confidence` зависит от модели; 0.6 в этом примере соответствует формуле
Decider/Kev для двух вариантов. Это поле не следует переносить между моделями.

## FRIDA-Decisions — ai-forever

**Работа.** T5 encoder с линейной головой над средним скрытых состояний токенов
варианта. Вариант видит state, инструкцию и себя; соседние варианты закрыты
маской. Кандидаты пакуются в строки. Из margins получается softmax-распределение;
в нашем вызове дополнительная температура равна 1.

**Вход:** `Judge(payload)`. Нативная компиляция нашего примера:

```text
state:       {"query":"мои делегирования"}
instruction: <routing.txt> + <нативный choice suffix FRIDA>
option:      delegation_view: {"name": "delegation_view", ...}
packed row:  [state][instruction][option_1 EOS][option_2 EOS]...
```

**Выход:** общий `answers.skill` с `choice`, `probabilities`, `confidence`;
дополнительно `margins` и usage по токенам/строкам. Confidence —
`1 - H(p)/ln(K)`, где H — энтропия, K — число вариантов.
В нашем адаптере caps временно расширяются под полные входы и восстанавливаются
после вызова. Плотный attention bias делает длинные строки дорогими по памяти.

**Отличие от остальных:** оценивает пару «запрос + кандидат» encoder-моделью;
Jeva кодирует эти тексты отдельно, а Decider читает общий список вариантов.
FRIDA не использует буквенные метки ответа или авторегрессионную генерацию.

## Decider-2B и Decider-4B — Mapika

**Работа.** Fine-tuned Qwen3.5-Base без генерации текста ответа: в выделенной
позиции считываются логиты меток вариантов, затем softmax с температурой
checkpoint. Для текущих сохранённых весов ответы идентифицируют версии
`decider-2b-v11` и `decider-4b-v2.1`. Нативный пакет — `decider-ai==1.9.0`.

**Вход:** `model.system_one(state, questions, layout="state_first", ...)`.
Plain-представление нашего запроса, с сокращённым списком:

```text
Context:
{"query": "мои делегирования"}

Question: <routing.txt>
Options:
(A) delegation_view: {"name": "delegation_view", ...}
(B) default_links: {"name": "default_links", ...}
Answer: (
```

На реальном первом шаге вариантов 64; SDK использует расширенную таблицу
однотокенных меток и возвращает вероятности обратно по именам скиллов.
Wrapper prompt определяется checkpoint; наш адаптер задаёт порядок state-first.

**Выход:** `choice`, `probabilities`, `confidence`, `x_p_max`, `certainty`, usage.
`x_p_max=max(p)`, `confidence=(K*max(p)-1)/(K-1)`,
`certainty=1-H(p)/ln(K)`. Температуры записываются в config эксперимента.

**Отличие:** общий контекст вариантов и slot readout вместо независимых
представлений Jeva или candidate margins FRIDA. 2B и 4B имеют одинаковую схему
API, но разные веса, размер и калибровку. Это независимый проект, а не веса
original Jev. Название «calibrated» не доказывает калибровку на нашем HR-корпусе.

## Jeva-4B — LIQIIIII

**Работа.** Qwen3.5-4B + LoRA rank 64, pooling последнего токена,
проекции в 768 измерений и один matrix decision block. Запрос с инструкцией
и каждый вариант кодируются отдельно. Затем представление запроса обновляется
с учётом всего набора вариантов; решение зависит от набора.

**Вход:** наш адаптер создаёт нативные typed questions:

```python
from jeva import Choice
result = model.system_one(
    state={"query": "мои делегирования"},
    questions={"skill": Choice(prompt, criteria, allow_null=False)},
)
```

```text
Отдельный текст запроса:
Decision type: choice
Question: <routing.txt>
State:
{"query": "мои делегирования"}

Отдельный текст варианта:
Option for choice: delegation_view: {"name": "delegation_view", ...}
```

**Выход:** нативный dataclass с `kind`, `choice`, `probabilities`,
`conditional_probabilities`, `null_probability`, `confidence`, usage и latency.
Адаптер вызывает `.to_dict()` и добавляет общее поле `type=kind`.
У нас `allow_null=False`: null=0, карты вероятностей совпадают,
confidence=max(p). При нативном `allow_null=True` существует отдельная
вероятность «ничего не подходит»; наш benchmark её не оценивает.

**Отличие:** option memory можно кешировать между разными запросами и
shortlist. Кеш содержит представления вариантов, а не ответы и не эталоны.
Batch/chunk управляют параллельным кодированием, а не доступностью вариантов
модулю решения. Полный файл и метаданные одного скилла — разные записи кеша.
Для повторяющегося каталога это даёт большой выигрыш по времени.

В наших прогонах использовались `cache=hybrid`, chunk=4 и batch budget=4096.
Первый запрос 1-step занял 5.30 с, медиана остальных — около 0.067 с.
Это время с повторным использованием каталога; его следует отделять от времени
первого запроса. Полные прогоны: 47.43 с для 1-step и 120.93 с для 2-step.

## Kev-4B — Jared Palmer

**Работа.** Qwen3.5-4B-Base с LoRA rank 16 и pointer head: представление
финального токена вопроса сопоставляется с представлениями закрывающих токенов
вариантов. Softmax использует температуру checkpoint. Маски и row-режим
учитывают архитектуру backbone; работает без генерации текста ответа.

**Вход:** `SystemOneRequest(**payload)` → `to_record` → `Server.answer`.
Наш state сериализуется иначе, чем у FRIDA/Decider:

```text
query: мои делегирования
```

Внутренний порядок:

```text
[<state> query: мои делегирования]
[<q> <routing.txt> <opt> delegation_view: {...} </opt> ... <decide>]
```

Это смысловые обозначения; SDK использует специальные токены Qwen как границы.
**Выход:** общий JSON `answers.skill` с `choice`, `probabilities`, `confidence`
и usage; confidence для choice равен `(K*max(p)-1)/(K-1)`.

**Отличие:** собственная pointer head по скрытым состояниям вариантов вместо
логитов буквенных меток Decider. Поддерживает System One wire format, но это
самостоятельные веса и inference runtime. В репозитории есть адаптер, однако
эксперимента Kev на нашей корзине ещё нет; численный результат не приводится.

## Original Jev — референс через API

`exp1/run_openrouter_exp1.py` отправляет общий payload на
`https://openrouter.ai/api/alpha/decisions`. Промпт находится в
`questions.skill.instructions`; отдельного chat system message нет.
Веса не загружаются локально, GPU компьютера не используется для инференса.
Внутренние токены, backbone и размер модели из наших ответов API не известны.

Выход содержит `answers.skill.choice/probabilities/confidence` и usage
провайдера. Нативный confidence сохраняется без пересчёта; совпадение с p_max
на отдельных примерах не устанавливает общую формулу. Open-source модели
воспроизводят класс задачи и/или протокол, но не являются копиями original Jev.

## Реальные ответы на «мои делегирования»

Это результаты **полного первого шага с 64 вариантами**, взятые из сохранённых
1-step прогонов. p_max показана после нормализации допустимого дрейфа округления,
confidence — как вернул backend. Полные распределения находятся в details.

| Модель | Победитель | p_max | Нативный confidence |
| --- | --- | ---: | ---: |
| Original Jev | delegation_view | 1.000000 | 1.000000 |
| FRIDA | delegation_view | 0.978809 | 0.957498 |
| Decider-2B | delegation_view | 0.977207 | 0.977100 |
| Decider-4B | delegation_view | 0.999200 | 0.998500 |
| Jeva-4B | delegation_view | 0.194619 | 0.194619 |
| Kev-4B | Нет прогона | — | — |

Нативный confidence сохраняется в `stage_N_details.jsonl` по пути
`response.answers.skill.confidence`. В winners есть `probability` победителя;
это другое поле. Распределение условно на переданный набор, поэтому confidence
моделей с разными формулами и значения до/после отбора shortlist напрямую
не сопоставимы. Все используемые здесь вопросы имеют тип choice; прочие
нативные типы (noul, score, у FRIDA также ranking) этим экспериментом не оцениваются.

## Источники

- [FRIDA-Decisions](https://github.com/ai-forever/FRIDA-Decisions/tree/v0.4.0),
  [protocol](https://github.com/ai-forever/FRIDA-Decisions/blob/v0.4.0/frida_decisions/protocol.py).
- [Decider-2B](https://huggingface.co/Mapika/decider-2b),
  [Decider-4B](https://huggingface.co/Mapika/decider-4b),
  [SDK](https://github.com/Mapika/decider).
- [Jeva model card](https://huggingface.co/LIQIIIII/Jeva-4B),
  [проверенная версия runtime](https://github.com/LiQiiiii/Jeva/tree/e2104e0720100f979f2cdd456a1cbbee96da0abd).
- [Kev-4B model card](https://huggingface.co/jaredpalmer/kev-4b),
  [Kev 1.0](https://github.com/jaredpalmer/kev/tree/kev-1.0).
- [OpenRouter Jev Decisions API](https://openrouter.ai/blog/tutorials/how-to-use-jev/).

Точные ревизии, версии пакетов, запросы и ответы каждого нашего прогона
зафиксированы в его `config.json` и `stage_N_details.jsonl`.


## Новые модели exp2

[Clef, Clef-flash, Luna и актуальный Jev: устройство, точные форматы и примеры](exp2/models_exp2.md).
В exp2 `questions.subagent.criteria` содержит raw description, а не JSON с metadata
из примера exp1 выше. На втором шаге значения заменяются полными текстами shortlist.
Общий справочник старых native SDK применим к обоим экспериментам: отличается
содержимое вариантов и ID вопроса (`subagent` вместо `skill`).
Новые прогоны отдельно сохраняют native confidence в winners; все исходные
native поля по-прежнему доступны в details.response. Исторические файлы не переписаны.
