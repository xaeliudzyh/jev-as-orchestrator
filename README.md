# jev-as-orchestrator

Сравнение decision-моделей для маршрутизации пользовательских запросов.

| Эксперимент | Задача | Данные | Код и результаты |
| --- | --- | --- | --- |
| exp1 | Выбрать первый skill для загрузки через get_skill | `data/skill_routing_exp1/`: 521 запрос, 64 кандидата | [Описание, метрики и запуск](exp1/README.md), `experiments/exp1/` |
| exp2 | Выбрать субагента | `data/subagent_routing_exp2/`: по умолчанию 193 запроса, 17 включённых кандидатов; с `--include-disabled` — 199/21 | [Описание, модели и запуск](exp2/README.md), `experiments/exp2/` |

Общие адаптеры моделей, движок оценки и CLI: `routing_bench/`.
Файлы конкретного эксперимента имеют суффикс `_exp1` / `_exp2`.
Старые корневые `run_hf.py` и `run_openrouter.py` перенесены в `exp1/`.
Пути по умолчанию привязаны к корню репозитория, а не к текущей рабочей папке.

Общий справочник: [OpenSourceModels.md](OpenSourceModels.md).
Форматы нативных SDK: [docs/model_io.md](docs/model_io.md).
Новые Cloudflare Clef/Clef-flash и Luna Decisions: [exp2/models_exp2.md](exp2/models_exp2.md).

Установка базового раннера (отдельное окружение для каждого нативного семейства):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Для OpenRouter нужен `OPENROUTER_API_KEY` в окружении или корневом `.env`.
Для локального инференса установи соответствующий `requirements-<family>.txt`.
Существующий `.env` не меняется; ключи в артефакты не записываются.

Проверка новых входов без API и весов:

```bash
python exp2/run_openrouter_exp2.py --model typesafe/jev-1.14 --strategy 1-step --dry-run
python exp2/run_hf_exp2.py --model Cloudflare/clef-flash --strategy 2-step --shortlist-k 3 --dry-run
python -m unittest discover -s tests -v
```

Dry-run сохраняет только снимки данных, конфиг
и пример запроса, а accuracy остаётся null. Первые реальные прогоны exp2
сохранены для полного каталога; новые прогоны по умолчанию используют только enabled=1. Историческая таблица exp1 находится
в [exp1/README.md](exp1/README.md#результаты-skill-routing).
