# Новые decision-модели exp2

Проверено 05.11.2026 по model cards, опубликованному native-коду и документации API.
Новые реальные инференсы ещё не выполнены. Примеры ответов ниже — условные числа.

| Модель / ID | Размер и основа | Высокоуровневое отличие |
| --- | --- | --- |
| `Cloudflare/clef-flash` | Класс 9B; Qwen3.5-9B с vision encoder, отдельная joint schema head | Меньшая открытая модель Cloudflare; решение по общему состоянию и всей схеме |
| `Cloudflare/clef` | Класс 27B; Qwen3.8-27B с vision encoder, joint schema head | Более крупный backbone той же decision-архитектуры |
| `openai/gpt-6-luna-decisions` | GPT-6 Luna, параметры и внутренний readout не раскрыты | Закрытая OpenAI decision-модель; не chat completion с JSON-просьбой |
| `typesafe/jev-1.14` | Размер/внутренний backbone не раскрыты в используемых источниках | Актуальный original Jev; старые результаты exp1 относятся к Jev 1.13 |

У Jev есть плавающий alias `~typesafe/jev-latest`. Для текущего сравнения лучше
явный `typesafe/jev-1.14`. В официальной документации OpenAI Decisions пока указан
`gpt-6-luna`; отдельная версия 6.1 для Decisions не подтверждена. Не следует
подставлять chat-модель `gpt-6.1-*` вместо decision endpoint.
Источники: [Jev на OpenRouter](https://openrouter.ai/docs/guides/community/jev),
[Luna на OpenRouter](https://openrouter.ai/openai/gpt-6-luna-decisions),
[OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions).

## Clef и Clef-flash: устройство

Выпущены открытые merged backbone weights и отдельная head. Обучение в model cards
описано как LoRA post-train замороженного backbone и обучение joint schema head.
Размер класса 9B/27B не равен точному числу элементов всех файлов, а также не
включает оценку памяти активаций.

Native `encode_record` формирует один вход: фиксированная system-инструкция,
STATE, текстовая SCHEMA FIELDS с инструкцией и всеми вариантами. `choice`
варианты при кодировании **сортируются по ID**. Переданное раннером перемешивание
не меняет этот порядок внутри Clef; оно всё ещё может влиять на разрешение
совпадений после округления. Нативный порядок не стоит считать тождественным
порядку остальных моделей.

Backbone возвращает final hidden states, `use_cache=False`; затем голова строит
представления состояния, вопроса и span каждого варианта, связывает их через
attention и вычисляет логиты. В скоринге участвуют lexical prior из output
embeddings и learned residual/joint scorer. Softmax берётся по вариантам каждого
вопроса. Нативный `systemone` не запускает autoregressive `generate()`.
Для Choice `confidence = round(max(probabilities_before_rounding), 4)`:
здесь confidence по определению равен вероятности победителя после округления,
а не отдельной оценке уверенности. Сырые вероятности также округлены до 4 знаков.
Дрейф суммы в допустимом диапазоне нормализуется общим benchmark, raw response остаётся.

Native API умеет JSON/text state, изображения и видео, несколько questions,
типы `noul`, `choice`, `score`. В exp2 используются только текст и один Choice.
Сам head/processor является обязательной частью: обычный HF chat pipeline или
сервер без Clef head даст другой режим работы. Для API нужен SystemOne endpoint
с поддержкой Clef, для local — `joint_schema_model.py` из того же checkpoint.
Исходник и веса берутся с закреплённой ревизии; SHA-256 native-кода попадает в config.
[Clef model card](https://huggingface.co/Cloudflare/clef),
[Clef-flash model card](https://huggingface.co/Cloudflare/clef-flash),
[проверенный native-код](https://huggingface.co/Cloudflare/clef/blob/cfddda043dd190ef3b01cff449d5ee8cff1e8ec6/joint_schema_model.py).

Относительно старых моделей:

- FRIDA отдельно оценивает «state + инструкция + один кандидат» encoder-моделью;
  соседние варианты закрыты маской. У Clef есть общий контекст схемы и joint head.
- Decider получает список вариантов и читает LM logits буквенных меток в позиции
  ответа. Clef использует отдельную обученную голову, а не такие label logits.
- Kev использует pointer readout по позициям вариантов; Clef — schema transformer head.
- Jeva независимо кодирует query и варианты, может кешировать option memory и
  затем учитывает весь набор в decision stack. Clef обрабатывает общий вход;
  Jeva chunk/cache не имеют для него такого же смысла и к нему не применяются.
- Original Jev предоставляет родственный typed API; его внутреннюю архитектуру
  по этому API установить нельзя. Совместимость wire format не доказывает общность весов.

## Фактический вход нашей задачи

Реальный первый вопрос bucket:
«Покажи распределение агентных навыков по ролям в нашем блоке: сколько сотрудников
на каждом уровне n1..n6 и средняя доля ИИ-кода».
Эталон `agentic_skills` в запрос **не добавляется**. Код ниже строит настоящий
первый запрос со всеми 17 включёнными точными descriptions без пересказа:

```python
from pathlib import Path
from exp2.data_exp2 import load_bucket, load_subagents
from routing_bench.experiment import make_payload

root = Path("data/subagent_routing_exp2")
sample = load_bucket(root / "subagents_routing_bucket_only_enabled.csv")[0]
candidates = load_subagents(root / "subagents_routing_corpus.csv")
prompt = Path("exp2/routing_exp2.txt").read_text().strip()
payload = make_payload("Cloudflare/clef-flash", prompt, sample, candidates,
                       full=False, fields=[], question_id="subagent")
```

```text
model: Cloudflare/clef-flash
state: {"query": "Покажи распределение агентных навыков ..."}
questions.subagent.type: choice
questions.subagent.instructions: <полный routing_exp2.txt>
questions.subagent.criteria:
  agentic_skills: <точная target_subagent_description>
  ai-coach:       <точная target_subagent_description>
  ...ещё 15 вариантов...
```

`model` заменяется на нужный ID. Имена — значения выбора, description — данные
классификации, инструкция — задача маршрутизации, state — запрос пользователя.
На stage 2 state и инструкция те же, а критерии выглядят так, если top-3
первого шага оказался `agentic_skills`, `hrp-analytics`, `team_profile`:

```python
by_name = {item.name: item for item in candidates}
shortlist = [by_name[name] for name in ["agentic_skills", "hrp-analytics", "team_profile"]]
stage2 = make_payload(payload["model"], prompt, sample, shortlist,
                      full=True, fields=[], question_id="subagent")
# stage2["questions"]["subagent"]["criteria"]["agentic_skills"]
# == полная ячейка target_subagent_full_text из corpus
```

Shortlist в этом примере условный; настоящий берётся из ranking модели.

## Низкоуровневый Clef input и output

Сокращённое текстовое представление native-токенизации:

```text
<|im_start|>system
<фиксированный SYSTEM_PROMPT из joint_schema_model.py>
<|im_end|>
<|im_start|>user
STATE:
{"query":"Покажи распределение агентных навыков ..."}

SCHEMA FIELDS:
FIELD 1
ID: subagent
TYPE: choice
INSTRUCTION: <полный routing_exp2.txt>
ALLOWED OPTIONS:
OPTION 1: {"description":"<точное описание>","option_id":"agentic_skills"}
OPTION 2: {"description":"<точное описание>","option_id":"ai-coach"}
...17 включённых вариантов на первом шаге, 3/5 на втором...
END FIELD
<|im_end|>
<|im_start|>assistant
<think>

</think>

JOINT SCHEMA DECISIONS:
```

Assistant suffix здесь — часть входного шаблона, не вывод рассуждений.
Пример ответа stage 2 (условные числа):

```json
{
  "model": "Cloudflare/clef-flash",
  "answers": {
    "subagent": {
      "type": "choice",
      "choice": "agentic_skills",
      "confidence": 0.7,
      "probabilities": {"agentic_skills": 0.7, "hrp-analytics": 0.2, "team_profile": 0.1}
    }
  },
  "usage": {"input_tokens": 1234, "output_tokens": 0}
}
```

## Luna Decisions и Jev через OpenRouter

Для обоих в нашем раннере HTTP POST на
`https://openrouter.ai/api/alpha/decisions`, Bearer `OPENROUTER_API_KEY`.
Тело — тот же `model/state/questions` выше. Ответ ожидается в общем формате
`answers.subagent` с Choice, полным распределением и native confidence;
`usage`, `provider`, фактический `model` и другие диагностические поля остаются
в `details.response`. Число параметров, внутренние spans, prompt tokens и
алгоритм readout этих закрытых моделей наш benchmark не задаёт.

У Luna официально есть typed probabilistic decisions по общему evidence:
выбор категории, predicate, score, в том числе с изображениями. Используем
только Choice. Это иной продуктовый интерфейс, чем генерация текста Luna.
По API нельзя обоснованно назвать архитектуру «одинаковой с Clef/Jeva» или
считать confidence равным p_max. Его смысл и калибровку на этой корзине нужно
проверять отдельно; раннер сохраняет именно переданное значение.

**Прямой OpenAI API имеет другую схему** и ниже приведён для сравнения форматов,
а не как тело запроса OpenRouter-раннера:

```python
# POST https://api.openai.com/v1/decisions
openai_body = {
    "model": "gpt-6-luna",
    "input": '{"query":"Покажи распределение агентных навыков ..."}',
    "questions": [{
        "name": "subagent", "type": "choice", "instructions": prompt,
        "choices": [{"value": name, "description": description}
                    for name, description in payload["questions"]["subagent"]["criteria"].items()],
    }],
}
# answers: [{"name": "subagent", "type": "choice", "choice": "agentic_skills",
#            "probabilities": [{"value": "agentic_skills", "probability": ...}, ...],
#            "confidence": ...}]
```

В direct OpenAI возможен `type=refusal`; отказ или неполное распределение не
должны превращаться в успешное предсказание benchmark. Мы не добавляем fallback
к chat completion или автоматическую подмену модели при ошибке API.
[Официальная схема OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions),
[OpenRouter Jev](https://openrouter.ai/docs/guides/community/jev),
[OpenRouter Luna](https://openrouter.ai/openai/gpt-6-luna-decisions).

## Память на 5060 Ti 16 GB

Оценки **только хранения номинальных весов**, десятичные GB; не измеренный пик:

| Модель | BF16, 2 bytes/param | INT8, 1 byte/param | 4-bit, 0.5 byte/param |
| --- | ---: | ---: | ---: |
| 9B | ~18 GB | ~9 GB | ~4.5 GB |
| 27B | ~54 GB | ~27 GB | ~13.5 GB |

Реальный checkpoint включает дополнительные компоненты, а quantization хранит
scales и оставляет часть модулей в float. Плюс активации всех input tokens,
head и attention workspace; полный длинный input дороже короткого. На 16 GB
разумный первый smoke run — Clef-flash 4-bit, один запрос, затем проверка 2-step.
27B 4-bit близок к пределу уже по весам и не гарантирован; предпочтителен endpoint
на большем GPU. `--max-input-tokens` отклоняет слишком длинный вход целиком,
не обрезает его для влезания. Фактический пик и скорость покажет отдельный запуск.

4/8-bit загрузка добавлена через Transformers BitsAndBytesConfig для backbone;
head остаётся в dtype. Это предусмотренный раннером режим, ещё не подтверждённый
GPU smoke run с данными весами. Версии CUDA/torch/bitsandbytes должны поддерживать
5060 Ti. Квантование способно менять ranking: сравнивай такие результаты отдельно.


С 05.11.2026 default exp2 использует bucket_only_enabled и corpus[enabled=1].
После отключения analytics 09.11.2026 это 193 запроса и 17 кандидатов.
Примеры выше соответствуют этому режиму. Для прежнего полного каталога
используй --include-disabled; в Python — load_subagents(..., include_disabled=True)
и исходный subagents_routing_bucket. enabled не передаётся модели как criterion.
