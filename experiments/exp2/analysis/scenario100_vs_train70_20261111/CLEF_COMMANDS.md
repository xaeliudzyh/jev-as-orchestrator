# Clef: оба сценарных промпта и test-метрики

`Cloudflare/clef-flash` — 9B, `Cloudflare/clef` — 27B.
На текущей RTX 5060 Ti 16 GiB стартовый вариант — Flash **BNB4/NF4**.
Полный пик VRAM на этих промптах не измерялся. BNB8 поддерживается, но оставляет
меньше памяти под длинные входы второго этапа.

**27B текущим локальным загрузчиком на 16 GiB не помещается:** расчётная нижняя
граница backbone BNB4 с BF16 embeddings — около 16.29 GiB, ещё до head/scales/
активаций. Для него приведённая команда предназначена только для другой GPU
с большей памятью: 24 GiB — старт для проверки, 32 GiB дают больше запаса.
Успешный длинный exp2 на них в этом изменении не проверялся. CPU-offload этот
раннер не реализует; generic `device_map=auto` здесь не является готовым решением
из-за нативного loader и обращения head к lexical weights.

## Запуск целиком одной командой

В существующей `.venv-clef` зависимости установлены. Скрипт ниже использует
именно `.venv-clef/bin/python`, в цикле последовательно прогоняет оба промпта
по всем 193 вопросам и затем считает full/train/test из тех же ответов.
Каждый прогон сохраняет 1-step и 2-step, shortlist=5, 4-bit, BF16 compute.
При первой ошибке инференса останавливается. Имена output-директорий содержат
UTC batch timestamp, поэтому старые отчёты не перезаписываются.

```bash
cd ~/SberAI/jev-as-orchestrator
bash experiments/exp2/analysis/scenario100_vs_train70_20261111/run_clef_scenarios.sh 9b
```

**Только на машине с большей VRAM, не на текущих 16 GiB:**

```bash
bash experiments/exp2/analysis/scenario100_vs_train70_20261111/run_clef_scenarios.sh 27b
```

Для проверки аргументов/данных без скачивания весов и GPU-инференса:

```bash
bash experiments/exp2/analysis/scenario100_vs_train70_20261111/run_clef_scenarios.sh 9b --dry-run
```

## Явные команды без вспомогательного скрипта

Ниже переменные для Flash. Для 27B **на GPU с достаточной VRAM** задай
`EXP2_MODEL=Cloudflare/clef` и `EXP2_TAG=clef27_q4` перед теми же командами.

```bash
EXP2_MODEL=Cloudflare/clef-flash
EXP2_TAG=clef9_q4

.venv-clef/bin/python exp2/run_exp2.py \
  --backend hf --mode local --adapter clef --model "$EXP2_MODEL" \
  --system-prompt exp2/prompts/routing_scenarios_ru_exp2.txt \
  --bucket data/subagent_routing_exp2/splits/stratified70_seed42/bucket_full.csv \
  --device cuda --dtype bfloat16 --clef-quantization 4bit \
  --shortlist-k 5 --max-input-tokens 32768 --fail-fast \
  --output-dir "experiments/exp2/${EXP2_TAG}_scenarios100"

.venv-clef/bin/python exp2/evaluate_holdout_exp2.py \
  --runs-root "experiments/exp2/${EXP2_TAG}_scenarios100" \
  --models "$EXP2_MODEL" \
  --prompt exp2/prompts/routing_scenarios_ru_exp2.txt \
  --prompt-evaluation retrospective \
  --output-dir "experiments/exp2/analysis/${EXP2_TAG}_scenarios100"

.venv-clef/bin/python exp2/run_exp2.py \
  --backend hf --mode local --adapter clef --model "$EXP2_MODEL" \
  --system-prompt exp2/prompts/routing_train70_ru_exp2.txt \
  --bucket data/subagent_routing_exp2/splits/stratified70_seed42/bucket_full.csv \
  --device cuda --dtype bfloat16 --clef-quantization 4bit \
  --shortlist-k 5 --max-input-tokens 32768 --fail-fast \
  --output-dir "experiments/exp2/${EXP2_TAG}_train70"

.venv-clef/bin/python exp2/evaluate_holdout_exp2.py \
  --runs-root "experiments/exp2/${EXP2_TAG}_train70" \
  --models "$EXP2_MODEL" \
  --prompt exp2/prompts/routing_train70_ru_exp2.txt \
  --output-dir "experiments/exp2/analysis/${EXP2_TAG}_train70"
```

Для повторного расчёта выбери новую output-dir отчёта. Флаг `--models` нужен:
дефолт оценщика остался Jev/Luna. Для промпта100 обязателен явный режим
`--prompt-evaluation retrospective`: он сохраняет исходный freeze промпта70 и
маркирует test-метрики как ретроспективные, без утверждения о чистом holdout.
Проверки корзины, каталога, выбранного текста промпта и сохранённых запросов/
ответов остаются. Для70 действует прежняя строгая привязка к prompt_freeze.json.

`32768` ограничивает полный вход query+prompt+schema+criteria. Раннер отказывает
при превышении, не обрезая вход. Это не гарантия VRAM на входе такой длины.
Уменьшение shortlist до3 меняет эксперимент; команды сохраняют5.

Источники: [Flash model card](https://huggingface.co/Cloudflare/clef-flash),
[Clef model card](https://huggingface.co/Cloudflare/clef),
[BNB quantization/offload](https://huggingface.co/docs/transformers/quantization/bitsandbytes).
Подробный расчёт памяти — [models_exp2.md](../../../../exp2/models_exp2.md).
