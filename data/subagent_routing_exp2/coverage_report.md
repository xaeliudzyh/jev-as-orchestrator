# Проверка корзины маршрутизации в субагенты

Источник корзины: `b2e_skills_.csv`. Источник описаний: base64 ZIP из
`subagents_30.10.txt`; обратная операция проверена по `zip_to_byte.py`.

## Корзина запросов: subagents_routing_bucket

- `subagents_routing_bucket.csv` и `.jsonl`: 199 запросов в исходном порядке.
- Только три исходные колонки: `Вопрос в новой корзине`, `skill_name`, `SubAgent`.
- Значения выбранных колонок сохранены из подготовленной корзины. Ранее были
  заполнены 30 пустых `SubAgent`: 18 → `mcp_internet`, 5 → `onboarding_top_600`,
  7 → `education`. Пустых целевых меток нет.
- Исходная полная таблица остаётся в `b2e_skills_.csv`.
- CSV: UTF-8 с BOM, разделитель `;`. JSONL: UTF-8, одна запись на строку,
  те же имена колонок и строковые значения, что в CSV.

## Корпус описаний: subagents_routing_corpus

- `subagents_routing_corpus.csv` и `.jsonl`: 21 запись, по одной на субагента
  из `agents/`, включая семь субагентов без примеров запросов.
- `target_subagent` — значение `name` из соответствующего `AGENTS.md`.
- `target_subagent_description` — полное значение поля `description` без правок.
- `target_subagent_full_text` — начальный YAML-блок только с полями `name`
  и `description`, затем весь исходный текст после YAML-блока без изменений.
- Оригинальные `agents/*/AGENTS.md` сохранены без изменений. Поля `enabled`,
  `scope`, tools/skills и прочие настройки удалены только из начального
  YAML-блока в колонке `target_subagent_full_text`.
- Форматы CSV и JSONL содержат одинаковые записи и три одинаковые колонки.

## Описания

- Восстановлен `agent-analytics-hotfix-0.1.45-subagents.zip`.
- Из архива извлечены 58 файлов без изменения содержимого;
  внешняя папка архива заменена на `agents/`. Внутренние `skills/` сохранены.
- Добавлен `agents/mcp_internet/AGENTS.md`: `name: mcp_internet` и полное
  описание из `mcp_internet_.txt`. Удалены только две начальные технические
  кавычки `""` исходного текста. Настройки enabled/scope не выдумывались.

## Покрытие

Все **14** субагентов из корзины имеют `agents/<SubAgent>/AGENTS.md`
с совпадающим полем `name` и непустым `description`. Пропущенных нет.

| SubAgent | Запросов | Описание |
| --- | ---: | --- |
| `agentic_skills` | 8 | [AGENTS.md](agents/agentic_skills/AGENTS.md) |
| `ai-coach` | 25 | [AGENTS.md](agents/ai-coach/AGENTS.md) |
| `analytics` | 4 | [AGENTS.md](agents/analytics/AGENTS.md) |
| `education` | 7 | [AGENTS.md](agents/education/AGENTS.md) |
| `grill` | 2 | [AGENTS.md](agents/grill/AGENTS.md) |
| `hrp-analytics` | 76 | [AGENTS.md](agents/hrp-analytics/AGENTS.md) |
| `interview` | 2 | [AGENTS.md](agents/interview/AGENTS.md) |
| `mcp_internet` | 18 | [AGENTS.md](agents/mcp_internet/AGENTS.md) |
| `onboarding_top_600` | 5 | [AGENTS.md](agents/onboarding_top_600/AGENTS.md) |
| `recruiter` | 2 | [AGENTS.md](agents/recruiter/AGENTS.md) |
| `talent-research` | 14 | [AGENTS.md](agents/talent-research/AGENTS.md) |
| `team_profile` | 12 | [AGENTS.md](agents/team_profile/AGENTS.md) |
| `top600-successor-rotation` | 11 | [AGENTS.md](agents/top600-successor-rotation/AGENTS.md) |
| `vacation` | 13 | [AGENTS.md](agents/vacation/AGENTS.md) |

В каталоге всего 21 описание. Без примеров в текущей корзине: `meetings`, `metrics`, `outstaff`, `people-search`, `react-analytics`, `recruiter-brief`, `service_navigation`.

Проверка выше относится к наличию описаний, а не к runtime-доступности.
У `interview` в исходном описании стоит `enabled: false`, `scope: none`,
хотя в корзине есть 2 примера. Эти настройки сохранены без изменения.

Скрипты запуска моделей, промпты и прежние эксперименты не менялись.
