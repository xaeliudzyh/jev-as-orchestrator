# Frozen exp2 train/test evaluation

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
| Luna | 1 | full | 193 | 174/193 (90.16) | 193/193 (100.00) | 193/193 (100.00) | 0 |
| Luna | 1 | train | 135 | 127/135 (94.07) | 135/135 (100.00) | 135/135 (100.00) | 0 |
| Luna | 1 | test | 58 | 47/58 (81.03) | 58/58 (100.00) | 58/58 (100.00) | 0 |
| Luna | 2 | full | 193 | 177/193 (91.71) | 193/193 (100.00) | 193/193 (100.00) | 0 |
| Luna | 2 | train | 135 | 128/135 (94.81) | 135/135 (100.00) | 135/135 (100.00) | 0 |
| Luna | 2 | test | 58 | 49/58 (84.48) | 58/58 (100.00) | 58/58 (100.00) | 0 |
| Jev 1.14 | 1 | full | 193 | 180/193 (93.26) | 193/193 (100.00) | 193/193 (100.00) | 0 |
| Jev 1.14 | 1 | train | 135 | 133/135 (98.52) | 135/135 (100.00) | 135/135 (100.00) | 0 |
| Jev 1.14 | 1 | test | 58 | 47/58 (81.03) | 58/58 (100.00) | 58/58 (100.00) | 0 |
| Jev 1.14 | 2 | full | 193 | 183/193 (94.82) | 193/193 (100.00) | 193/193 (100.00) | 0 |
| Jev 1.14 | 2 | train | 135 | 132/135 (97.78) | 135/135 (100.00) | 135/135 (100.00) | 0 |
| Jev 1.14 | 2 | test | 58 | 51/58 (87.93) | 58/58 (100.00) | 58/58 (100.00) | 0 |

## Provenance

- Runs root: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/train70_seed42`
- Split manifest: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/data/subagent_routing_exp2/splits/stratified70_seed42/manifest.json`
- Manifest SHA-256: `378c2e6199602b77f38c3945991ea8044ec79895d656cf7d236e7e31089bf0b5`
- Full bucket SHA-256: `7357b93a64e7a7432f53a7b5506474dda2e1804199140dd20dd2c79d6aca07ff`
- Source corpus SHA-256: `bb39393686bfc937d7e3436a6a28244cfb14c58c403e77ffa66deaf0c070c2fd`
- Prompt: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/exp2/routing_train70_ru_exp2.txt`
- Prompt SHA-256 (runner strips surrounding whitespace): `d5e4a5b97243582166c3f9396c8cbda7f905fdde6ccce4b10ef2f86bb943d6ff`
- Prompt freeze: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/data/subagent_routing_exp2/splits/stratified70_seed42/prompt_freeze.json`

- Luna stage 1: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/train70_seed42/1-step/openai__gpt-6-luna-decisions_20261111T130507.262036+0300` (run `openai__gpt-6-luna-decisions_20261111T130507.262036+0300`)
- Luna stage 2: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/train70_seed42/2-step/openai__gpt-6-luna-decisions_20261111T130507.262036+0300` (run `openai__gpt-6-luna-decisions_20261111T130507.262036+0300`)
- Jev 1.14 stage 1: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/train70_seed42/1-step/typesafe__jev-1.14_20261111T130208.180691+0300` (run `typesafe__jev-1.14_20261111T130208.180691+0300`)
- Jev 1.14 stage 2: `/home/xaeliudzyh/SberAI/jev-as-orchestrator/experiments/exp2/train70_seed42/2-step/typesafe__jev-1.14_20261111T130208.180691+0300` (run `typesafe__jev-1.14_20261111T130208.180691+0300`)

summary.json records exact counts, macro @1 over observed gold classes, per-class metrics,
source artifact SHA-256 hashes, all exclusions, and missing model/stage combinations.
Candidates without gold examples stay in every decision but have no class accuracy denominator.
Different selected run IDs across stages mean those stages came from different saved pipelines;
inspect run_id and paired_artifact_dir before making paired comparisons.

Integrity checks cannot establish train-only prompt authorship or absence of historical exposure.
This split measures a held-out portion of the existing benchmark, not production generalization.

## Exclusions

None.
