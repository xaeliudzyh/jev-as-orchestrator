# Retrospective exp2 evaluation — test exposed

**Retrospective evaluation; test-exposed prompt. No clean holdout claim.**
The chosen prompt may use the full 193-question benchmark, including these 58 test questions.
The train70 prompt freeze is preserved and recorded, but does not bind the chosen retrospective prompt.
The current chosen prompt SHA-256 and saved run/request prompt text must still match exactly.

Offline evaluation of saved native decisions; no model calls or confidence cutoffs.
Each selected 1-step artifact evaluates stage 1, and each selected 2-step artifact evaluates stage 2.
The stage-1 copy inside a 2-step artifact only validates the shortlist and is never counted as another result.

Full (193), train (135), and test (58) metrics use subsets of exactly the same saved responses.
All errors and skipped second-stage responses count as misses and remain in the denominator.
n_errors includes every non-ok record. @k is hit@k with k clipped to the available ranking.

Selection: latest complete eligible run by started_at per model/stage; timestamp ties use source path.
Eligibility requires matching frozen bucket/corpus/current prompt hashes, the 17 enabled candidates,
exact original IDs/questions/gold, request snapshots/native rankings, and saved full @1/@3/@5 agreement.

| Model | Stage | Subset | N | @1 correct/N (%) | @3 correct/N (%) | @5 correct/N (%) | Errors |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Luna | 1 | full | 193 | 183/193 (94.82) | 191/193 (98.96) | 191/193 (98.96) | 1 |
| Luna | 1 | train | 135 | 126/135 (93.33) | 133/135 (98.52) | 133/135 (98.52) | 1 |
| Luna | 1 | test | 58 | 57/58 (98.28) | 58/58 (100.00) | 58/58 (100.00) | 0 |
| Luna | 2 | full | 193 | 182/193 (94.30) | 190/193 (98.45) | 191/193 (98.96) | 1 |
| Luna | 2 | train | 135 | 125/135 (92.59) | 132/135 (97.78) | 133/135 (98.52) | 1 |
| Luna | 2 | test | 58 | 57/58 (98.28) | 58/58 (100.00) | 58/58 (100.00) | 0 |
| Jev 1.14 | 1 | full | 193 | 181/193 (93.78) | 193/193 (100.00) | 193/193 (100.00) | 0 |
| Jev 1.14 | 1 | train | 135 | 124/135 (91.85) | 135/135 (100.00) | 135/135 (100.00) | 0 |
| Jev 1.14 | 1 | test | 58 | 57/58 (98.28) | 58/58 (100.00) | 58/58 (100.00) | 0 |
| Jev 1.14 | 2 | full | 193 | 181/193 (93.78) | 193/193 (100.00) | 193/193 (100.00) | 0 |
| Jev 1.14 | 2 | train | 135 | 123/135 (91.11) | 135/135 (100.00) | 135/135 (100.00) | 0 |
| Jev 1.14 | 2 | test | 58 | 58/58 (100.00) | 58/58 (100.00) | 58/58 (100.00) | 0 |

## Provenance

- Runs root: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2`
- Split manifest: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/data/subagent_routing_exp2/splits/stratified70_seed42/manifest.json`
- Manifest SHA-256: `378c2e6199602b77f38c3945991ea8044ec79895d656cf7d236e7e31089bf0b5`
- Full bucket SHA-256: `7357b93a64e7a7432f53a7b5506474dda2e1804199140dd20dd2c79d6aca07ff`
- Source corpus SHA-256: `bb39393686bfc937d7e3436a6a28244cfb14c58c403e77ffa66deaf0c070c2fd`
- Prompt: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/exp2/routing_scenarios_ru_exp2.txt`
- Prompt SHA-256 (runner strips surrounding whitespace): `a545c15301b0019b48719d6995b8319e07f60e9eeed6dbf122e86f6bb769867d`
- Prompt evaluation: `retrospective`
- Prompt freeze: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/data/subagent_routing_exp2/splits/stratified70_seed42/prompt_freeze.json`
- Prompt freeze enforced: `False`

- Luna stage 1: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261109T134353.903881+0300` (run `openai__gpt-6-luna-decisions_20261109T134353.903881+0300`)
- Luna stage 2: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/openai__gpt-6-luna-decisions_20261109T134353.903881+0300` (run `openai__gpt-6-luna-decisions_20261109T134353.903881+0300`)
- Jev 1.14 stage 1: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261109T133624.365052+0300` (run `typesafe__jev-1.14_20261109T133624.365052+0300`)
- Jev 1.14 stage 2: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/typesafe__jev-1.14_20261109T133624.365052+0300` (run `typesafe__jev-1.14_20261109T133624.365052+0300`)

summary.json records exact counts, macro @1 over observed gold classes, per-class metrics,
source artifact SHA-256 hashes, all exclusions, and missing model/stage combinations.
Candidates without gold examples stay in every decision but have no class accuracy denominator.
Different selected run IDs across stages mean those stages came from different saved pipelines;
inspect run_id and paired_artifact_dir before making paired comparisons.

Integrity checks cannot establish train-only prompt authorship or absence of historical exposure.
These test metrics are retrospective benchmark subset scores with test exposure, not clean holdout results.

## Exclusions

- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/Cloudflare__clef-flash_20261109T181931.735580+0300`: ValueError: Model outside requested model scope
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/Cloudflare__clef-flash_20261109T183722.634479+0300`: ValueError: Model outside requested model scope
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261105T191600.426047+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261105T194805.756214+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261109T110934.530535+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261109T112628.542481+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261109T140105.681731+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/openai__gpt-6-luna-decisions_20261109T143515.560352+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261105T191447.210183+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261105T194655.055101+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261109T110750.875574+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261109T112426.913512+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261109T140439.499619+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/1-step/typesafe__jev-1.14_20261109T143150.585465+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/Cloudflare__clef-flash_20261109T181931.735580+0300`: ValueError: Model outside requested model scope
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/openai__gpt-6-luna-decisions_20261105T195453.143178+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/openai__gpt-6-luna-decisions_20261109T140105.681731+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/openai__gpt-6-luna-decisions_20261109T143515.560352+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/typesafe__jev-1.14_20261105T194959.941204+0300`: ValueError: Strategy/evaluated-stage mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/typesafe__jev-1.14_20261109T140439.499619+0300`: ValueError: Current/frozen prompt mismatch
- `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/2-step/typesafe__jev-1.14_20261109T143150.585465+0300`: ValueError: Current/frozen prompt mismatch
