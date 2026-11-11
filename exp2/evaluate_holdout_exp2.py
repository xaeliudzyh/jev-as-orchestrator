#!/usr/bin/env python3
"""Evaluate a frozen 193/135/58 exp2 split from saved responses, without inference."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from exp2.data_exp2 import BUCKET_FIELDS, CORPUS_FIELDS, read_rows

MODELS = {"typesafe/jev-1.14": "Jev 1.14", "openai/gpt-6-luna-decisions": "Luna"}
MODEL_LABELS = {**MODELS, "Cloudflare/clef-flash": "Clef 9B", "Cloudflare/clef": "Clef 27B"}
PROMPT_EVALUATIONS = ("train70", "retrospective")
STAGES = {"1-step": 1, "2-step": 2}
EXPECTED_COUNTS = {"full": 193, "train": 135, "test": 58}
KS = (1, 3, 5)
COMPLETED = {"completed", "completed_with_errors"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def is_int(value):
    return type(value) is int


def check_ids(ids, expected, context):
    require(isinstance(ids, list) and all(is_int(i) for i in ids), f"Invalid sample IDs: {context}")
    require(len(ids) == len(set(ids)) and set(ids) == set(expected), f"Incomplete/duplicate sample IDs: {context}")


def model_scope(models=None):
    """Keep the historical default scope; only opt explicit models into a report."""
    if models is None:
        return dict(MODELS)
    require(not isinstance(models, str), "Models must be a nonempty sequence of model IDs")
    models = list(models)
    require(bool(models) and all(isinstance(model, str) and bool(model.strip()) and model == model.strip()
                               for model in models), "Models must be nonempty, trimmed model IDs")
    require(len(models) == len(set(models)), "Duplicate requested model IDs")
    return {model: MODEL_LABELS.get(model, model) for model in models}


def load_frozen(split_dir, prompt_path, prompt_evaluation="train70"):
    """Verify the split and current prompt before considering any run."""
    require(prompt_evaluation in PROMPT_EVALUATIONS, f"Unsupported prompt evaluation: {prompt_evaluation}")
    split_dir, prompt_path = Path(split_dir), Path(prompt_path)
    manifest_path = split_dir / "manifest.json"
    manifest = read_json(manifest_path)
    require(manifest.get("schema_version") == 1, "Unsupported split manifest schema")
    for subset, n in EXPECTED_COUNTS.items():
        require(manifest.get(f"n_{subset}") == n, f"Expected frozen {subset} size {n}")
    require(manifest.get("n_candidates") == 17, "Expected 17 enabled candidates")
    hashes = manifest.get("files_sha256", {})
    for name in ("bucket_full.csv", "bucket_train.csv", "bucket_test.csv", "catalog_enabled.jsonl"):
        require(name in hashes, f"Missing frozen file hash: {name}")
    for name, expected_hash in hashes.items():
        require(isinstance(name, str) and Path(name).name == name, "Frozen file names must be basenames")
        require(digest(split_dir / name) == expected_hash, f"Frozen file hash mismatch: {name}")
    rows = read_rows(split_dir / "bucket_full.csv", BUCKET_FIELDS)
    require(len(rows) == EXPECTED_COUNTS["full"], "Frozen full bucket size mismatch")
    catalog_rows = read_rows(split_dir / "catalog_enabled.jsonl", CORPUS_FIELDS)
    catalog = {row["target_subagent"]: row for row in catalog_rows}
    require(len(catalog) == len(catalog_rows) == 17 and all(r["enabled"] == 1 for r in catalog_rows),
            "Frozen catalog must contain 17 unique enabled candidates")
    require(all(row["SubAgent"] in catalog for row in rows), "Frozen gold is outside candidate catalog")
    assignment = manifest.get("assignment", [])
    check_ids([a["original_sample_id"] for a in assignment], range(len(rows)), "assignment")
    subset_ids = {"full": list(range(len(rows)))}
    for subset in ("train", "test"):
        ids = manifest[f"{subset}_original_ids"]
        check_ids(ids, [a["original_sample_id"] for a in assignment if a["split"] == subset], subset)
        require(len(ids) == EXPECTED_COUNTS[subset] and ids == sorted(ids), f"Invalid {subset} split order/size")
        entries = sorted((a for a in assignment if a["split"] == subset), key=lambda a: a["split_sample_id"])
        require([a["split_sample_id"] for a in entries] == list(range(len(ids))), f"Invalid {subset} split sample IDs")
        require([a["original_sample_id"] for a in entries] == ids, f"Assignment order differs from {subset} IDs")
        require(read_rows(split_dir / f"bucket_{subset}.csv", BUCKET_FIELDS) == [rows[i] for i in ids],
                f"Frozen {subset} rows differ from assignment")
        subset_ids[subset] = ids
    require(set(subset_ids["train"]).isdisjoint(subset_ids["test"]) and
            set(subset_ids["train"]) | set(subset_ids["test"]) == set(subset_ids["full"]),
            "Train/test must be disjoint and cover the full bucket")
    for entry in assignment:
        index = entry["original_sample_id"]
        require(entry["split"] in {"train", "test"} and
                entry["question_sha256"] == text_digest(rows[index][BUCKET_FIELDS[0]]) and
                entry["target_subagent"] == rows[index]["SubAgent"], "Assignment question/gold mismatch")
    labels = sorted({r["SubAgent"] for r in rows})
    counts = {label: {subset: sum(rows[i]["SubAgent"] == label for i in ids)
                      for subset, ids in subset_ids.items()} for label in labels}
    require(manifest.get("label_counts") == counts, "Frozen label counts mismatch")
    require(manifest.get("n_observed_labels") == len(labels), "Frozen observed-label count mismatch")
    require(all(c["train"] and c["test"] for c in counts.values()), "Every observed label must occur in both subsets")
    corpus_hash = manifest.get("source_corpus_sha256")
    require(isinstance(corpus_hash, str) and len(corpus_hash) == 64 and
            all(c in "0123456789abcdef" for c in corpus_hash), "Invalid frozen source corpus SHA-256")
    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    require(bool(prompt_text), "Current prompt is empty")
    prompt_hash, raw_prompt_hash = text_digest(prompt_text), digest(prompt_path)
    freeze_path = split_dir / "prompt_freeze.json"
    freeze = None
    if freeze_path.exists():
        freeze = read_json(freeze_path)
        require(isinstance(freeze, dict), "Invalid prompt_freeze.json")
        if prompt_evaluation == "train70":
            require(freeze.get("system_prompt_sha256") == prompt_hash, "Current prompt differs from prompt_freeze.json")
            if "raw_file_sha256" in freeze:
                require(freeze["raw_file_sha256"] == raw_prompt_hash, "Raw prompt file differs from prompt_freeze.json")
    return {"manifest": manifest, "rows": rows, "catalog": catalog, "subset_ids": subset_ids,
            "prompt_text": prompt_text, "prompt_hash": prompt_hash,
            "bucket_hash": hashes["bucket_full.csv"], "corpus_hash": corpus_hash,
            "provenance": {"split_dir": str(split_dir.resolve()), "manifest_path": str(manifest_path.resolve()),
                "manifest_sha256": digest(manifest_path), "files_sha256": hashes,
                "source_corpus_sha256": corpus_hash, "prompt_path": str(prompt_path.resolve()),
                "system_prompt_sha256": prompt_hash, "raw_prompt_file_sha256": raw_prompt_hash,
                "prompt_freeze_path": str(freeze_path.resolve()) if freeze is not None else None,
                "prompt_freeze_sha256": digest(freeze_path) if freeze is not None else None,
                "prompt_evaluation": prompt_evaluation,
                "prompt_freeze_enforced": prompt_evaluation == "train70" and freeze is not None,
                "prompt_freeze_system_prompt_sha256": freeze.get("system_prompt_sha256") if freeze is not None else None}}


def validate_sample(row, frozen, context):
    require(isinstance(row, dict), f"Invalid sample record: {context}")
    index = row.get("sample_id")
    require(is_int(index) and 0 <= index < len(frozen["rows"]), f"Invalid original sample ID: {context}")
    original = frozen["rows"][index]
    require(row.get("question") == original[BUCKET_FIELDS[0]], f"Original question mismatch: {context}, sample {index}")
    require(row.get("expected_subagents") == [original["SubAgent"]], f"Gold mismatch: {context}, sample {index}")
    if "source_skill_name" in row:
        require(row["source_skill_name"] == original["skill_name"], f"Provenance mismatch: {context}, sample {index}")


def aggregate(rows):
    """All non-ok rows remain in every applicable denominator as misses."""
    n = len(rows)
    hits = {f"accuracy@{k}": sum(row["status"] == "ok" and
            bool(set(row["ranking"][:k]) & set(row["expected_subagents"])) for row in rows) for k in KS}
    return {"n_samples": n, "n_errors": sum(row["status"] != "ok" for row in rows),
            "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())), "hits": hits,
            **{key: value / n if n else None for key, value in hits.items()}}


def subset_metrics(rows, frozen):
    by_id = {row["sample_id"]: row for row in rows}
    result = {}
    for subset, ids in frozen["subset_ids"].items():
        selected = [by_id[i] for i in ids]
        value = aggregate(selected)
        classes = sorted({r["expected_subagents"][0] for r in selected})
        value["per_class"] = {label: aggregate([r for r in selected if r["expected_subagents"] == [label]])
                              for label in classes}
        value["macro_accuracy@1"] = sum(v["accuracy@1"] for v in value["per_class"].values()) / len(classes)
        result[subset] = value
    return result


def check_saved_metrics(saved, computed, context, final=False):
    require(saved.get("n_samples") == computed["n_samples"], f"Saved metric denominator mismatch: {context}")
    for k in KS:
        key = f"accuracy@{k}"
        hit = saved.get("hits", {}).get(key)
        value = saved.get(key)
        require(is_int(hit) and hit == computed["hits"][key], f"Saved {key} correct-count mismatch: {context}")
        require(type(value) in (int, float) and math.isfinite(value) and
                math.isclose(value, computed[key], rel_tol=0, abs_tol=1e-12), f"Saved {key} rate mismatch: {context}")
    if final:
        require(saved.get("n_errors") == computed["n_errors"], f"Saved error count mismatch: {context}")
        expected_status = "completed_with_errors" if computed["n_errors"] else "completed"
        require(saved.get("status") == expected_status, f"Saved status/error-count mismatch: {context}")


def validate_native_ranking(detail, names, context):
    """Match the runner's native ranking, including its tie rule and normalization."""
    response = detail.get("response")
    require(isinstance(response, dict) and not response.get("truncated"), f"Missing/truncated native response: {context}")
    answer = response.get("answers", {}).get("subagent", {})
    require(answer.get("type", "choice") == "choice", f"Non-choice native answer: {context}")
    probabilities = answer.get("probabilities")
    require(isinstance(probabilities, dict) and set(probabilities) == set(names), f"Native candidate coverage mismatch: {context}")
    require(all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values()),
            f"Invalid native probability: {context}")
    total = sum(probabilities.values())
    require(total > 0 and abs(total - 1) <= .02, f"Invalid native probability mass: {context}")
    choice = answer.get("choice")
    require(choice is None or choice in probabilities and probabilities[choice] == max(probabilities.values()),
            f"Invalid native choice: {context}")
    order = {name: i for i, name in enumerate(names)}
    ranked = sorted(names, key=lambda name: (-probabilities[name], name != choice, order[name]))
    require(detail["ranking"] == ranked, f"Saved ranking differs from native response: {context}")
    probability = detail.get("probability")
    require(type(probability) in (int, float) and math.isfinite(probability) and
            math.isclose(probability, probabilities[ranked[0]] / total, rel_tol=0, abs_tol=1e-12),
            f"Saved winner probability mismatch: {context}")


def load_stage(folder, stage, config, frozen, first=None):
    winners_path = folder / f"stage_{stage}_winners.jsonl"
    details_path = folder / f"stage_{stage}_details.jsonl"
    winners, details = read_jsonl(winners_path), read_jsonl(details_path)
    ids = config["sample_ids"]
    for records, context in ((winners, winners_path.name), (details, details_path.name)):
        check_ids([r.get("sample_id") for r in records], ids, context)
        require([r["sample_id"] for r in records] == ids, f"Record order differs from config sample IDs: {context}")
        for row in records:
            validate_sample(row, frozen, context)
            require(row.get("stage") == stage, f"Wrong stage in {context}")
            require(row.get("status") in {"ok", "error", "skipped"}, f"Unknown status in {context}")
    for row, detail in zip(winners, details):
        index = row["sample_id"]
        context = f"{folder.name}/stage_{stage}, sample {index}"
        for field in ("status", "winner", "ranking", "probability"):
            require(field in row and detail.get(field) == row[field], f"Winners/details {field} mismatch: {context}")
        previous = first[index] if first is not None else None
        previous_failed = previous is not None and previous["status"] != "ok"
        names = (previous["ranking"][:config["shortlist_k"]]
                 if previous is not None and not previous_failed else config["candidate_order"])
        require(detail.get("candidate_order") == names, f"Candidate order/shortlist mismatch: {context}")
        require((row["status"] == "skipped") == previous_failed, f"Invalid stage failure propagation: {context}")
        ranking = row["ranking"]
        require(isinstance(ranking, list) and all(isinstance(name, str) for name in ranking), f"Invalid ranking: {context}")
        if row["status"] == "ok":
            require(len(ranking) == len(set(ranking)) == len(names) and set(ranking) == set(names),
                    f"Ranking candidate inclusion mismatch: {context}")
            require(bool(ranking) and row["winner"] == ranking[0], f"Winner/ranking mismatch: {context}")
            validate_native_ranking(detail, names, context)
        else:
            require(ranking == [] and row["winner"] is None and row["probability"] is None,
                    f"Failed/skipped record must be an empty miss: {context}")
        request = detail.get("request")
        if previous_failed:
            require(request is None, f"Skipped second stage has a request: {context}")
        elif request is not None:
            question = request.get("questions", {}).get("subagent", {})
            criteria = question.get("criteria")
            require(request.get("model") == config["model"] and request.get("state") == {"query": row["question"]},
                    f"Request model/question mismatch: {context}")
            require(question.get("instructions") == frozen["prompt_text"], f"Request prompt mismatch: {context}")
            require(isinstance(criteria, dict) and list(criteria) == names, f"Request candidate inclusion mismatch: {context}")
            field = "target_subagent_description" if stage == 1 else "target_subagent_full_text"
            require(all(criteria[name] == frozen["catalog"][name][field] for name in names),
                    f"Request candidate text mismatch: {context}")
        else:
            require(row["status"] != "ok", f"Successful record has no saved request: {context}")
    return winners, [winners_path, details_path]


def load_run(folder, config, metrics, frozen, strategy):
    stage = STAGES[strategy]
    require(config.get("experiment") == "exp2" and config.get("question_id") == "subagent", "Not an exp2 subagent run")
    require(config.get("strategy") == strategy and config.get("evaluated_stage") == stage and
            metrics.get("evaluated_stage") == stage, "Strategy/evaluated-stage mismatch")
    require(config.get("status") in COMPLETED and metrics.get("status") in COMPLETED and metrics.get("complete") is True,
            "Run is not complete")
    require(config.get("status") == metrics.get("status"), "Config/metrics status mismatch")
    require(config.get("n_samples") == 193 and config.get("n_corpus_samples") == 193 and
            metrics.get("n_samples") == 193 and metrics.get("n_samples_planned") == 193 and
            metrics.get("n_samples_unprocessed") == 0, "Run does not cover the full 193 samples")
    require(config.get("bucket_sha256") == frozen["bucket_hash"], "Frozen full-bucket SHA-256 mismatch")
    require(config.get("corpus_sha256") == frozen["corpus_hash"], "Frozen source-corpus SHA-256 mismatch")
    require(config.get("n_subagents") == 17 and config.get("include_disabled") is False,
            "Run must include exactly 17 enabled candidates")
    require(config.get("system_prompt_sha256") == frozen["prompt_hash"] and
            config.get("system_prompt") == frozen["prompt_text"], "Current/frozen prompt mismatch")
    names = config.get("candidate_order")
    require(isinstance(names, list) and all(isinstance(n, str) for n in names) and
            len(names) == len(set(names)) == 17 and set(names) == set(frozen["catalog"]), "Enabled candidate set mismatch")
    check_ids(config.get("sample_ids"), range(193), "config")
    require(not config.get("missing_labels") and not config.get("unreachable_sample_ids") and
            not config.get("excluded_disabled_sample_ids"), "Run excluded or cannot reach frozen samples")
    if stage == 2:
        require(config.get("shortlist_k") in {3, 5}, "Unsupported second-stage shortlist size")
    samples_path, catalog_path = folder / "samples.json", folder / "subagents.json"
    samples, snapshot = read_json(samples_path), read_json(catalog_path)
    check_ids([row.get("sample_id") for row in samples], range(193), "samples snapshot")
    require([r["sample_id"] for r in samples] == config["sample_ids"], "Snapshot/config sample order mismatch")
    for row in samples:
        validate_sample(row, frozen, "samples snapshot")
    require([r.get("name") for r in snapshot] == names, "Catalog snapshot candidate order mismatch")
    for row in snapshot:
        original = frozen["catalog"][row["name"]]
        require(row.get("metadata", {}).get("enabled") == 1 and row.get("short_text") == original["target_subagent_description"]
                and row.get("text") == original["target_subagent_full_text"], "Catalog snapshot text/enabled mismatch")
    first_rows, first_paths = load_stage(folder, 1, config, frozen)
    source_paths = [folder / "config.json", folder / "metrics.json", samples_path, catalog_path, *first_paths]
    if stage == 2:
        rows, paths = load_stage(folder, 2, config, frozen, {r["sample_id"]: r for r in first_rows})
        source_paths.extend(paths)
        check_saved_metrics(metrics.get("stage_1_metrics", {}), aggregate(first_rows), "embedded stage_1_metrics")
        expected_shortlist_rate = sum(r["status"] == "ok" and
            bool(set(r["ranking"][:config["shortlist_k"]]) & set(r["expected_subagents"])) for r in first_rows) / 193
        rate = metrics.get("shortlist_hit_rate")
        require(type(rate) in (int, float) and math.isfinite(rate) and
                math.isclose(rate, expected_shortlist_rate, rel_tol=0, abs_tol=1e-12), "Saved shortlist hit rate mismatch")
    else:
        rows = first_rows
    computed = subset_metrics(rows, frozen)
    check_saved_metrics(metrics, computed["full"], "final metrics", final=True)
    stamp = datetime.fromisoformat(config["started_at"].replace("Z", "+00:00"))
    require(stamp.tzinfo is not None, "Run started_at must include a timezone")
    return {"model": config["model"], "model_label": MODEL_LABELS.get(config["model"], config["model"]),
            "strategy": strategy, "stage": stage,
            "source_folder": str(folder.resolve()), "run_id": config.get("run_id", folder.name),
            "started_at": config["started_at"], "finished_at": config.get("finished_at"),
            "artifact_origin": config.get("artifact_origin"), "pipeline_status": config.get("pipeline_status"),
            "paired_artifact_dir": config.get("paired_artifact_dir"),
            "system_prompt_sha256": config["system_prompt_sha256"], "bucket_sha256": config["bucket_sha256"],
            "corpus_sha256": config["corpus_sha256"], "candidate_order": names,
            "shortlist_k": config.get("shortlist_k") if stage == 2 else None,
            "source_files_sha256": {p.name: digest(p) for p in source_paths},
            "metrics": computed}, stamp


def collect(runs_root, frozen, models=None):
    """Select latest complete eligible artifact per model/stage, never by its score."""
    requested_models = model_scope(models)
    runs_root = Path(runs_root)
    require(runs_root.is_dir(), f"Runs root is not a directory: {runs_root}")
    selected, exclusions, sort_keys = {}, [], {}
    for strategy, stage in STAGES.items():
        for config_path in sorted((runs_root / strategy).glob("*/config.json")):
            folder = config_path.parent
            model = None
            try:
                config = read_json(config_path)
                model = config.get("model")
                require(model in requested_models, "Model outside requested model scope")
                metrics = read_json(folder / "metrics.json")
                view, stamp = load_run(folder, config, metrics, frozen, strategy)
                key, order = (model, stage), (stamp, str(folder.resolve()))
                if key in selected and order <= sort_keys[key]:
                    exclusions.append({"source_folder": str(folder.resolve()), "model": model, "stage": stage,
                                       "reason": "Superseded by a later complete eligible run"})
                else:
                    if key in selected:
                        exclusions.append({"source_folder": selected[key]["source_folder"], "model": model,
                                           "stage": stage, "reason": "Superseded by a later complete eligible run"})
                    selected[key], sort_keys[key] = view, order
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
                exclusions.append({"source_folder": str(folder.resolve()), "model": model, "stage": stage,
                                   "reason": f"{type(error).__name__}: {error}"})
    views = [selected[key] for key in sorted(selected)]
    missing = [{"model": model, "stage": stage, "strategy": strategy} for model in requested_models
               for strategy, stage in STAGES.items() if (model, stage) not in selected]
    return views, exclusions, missing


def render_readme(summary):
    retrospective = summary.get("prompt_evaluation", "train70") == "retrospective"
    lines = ["# Frozen exp2 train/test evaluation", "",
        "Offline evaluation of saved native decisions; no model calls or confidence cutoffs.",
        "Each selected 1-step artifact evaluates stage 1, and each selected 2-step artifact evaluates stage 2.",
        "The stage-1 copy inside a 2-step artifact only validates the shortlist and is never counted as another result.", "",
        "Full (193), train (135), and test (58) metrics use subsets of exactly the same saved responses.",
        "All errors and skipped second-stage responses count as misses and remain in the denominator.",
        "n_errors includes every non-ok record. @k is hit@k with k clipped to the available ranking.", "",
        "Selection: latest complete eligible run by started_at per model/stage; timestamp ties use source path.",
        "Eligibility requires matching frozen bucket/corpus/current prompt hashes, the 17 enabled candidates,",
        "exact original IDs/questions/gold, request snapshots/native rankings, and saved full @1/@3/@5 agreement.", "",
        "| Model | Stage | Subset | N | @1 correct/N (%) | @3 correct/N (%) | @5 correct/N (%) | Errors |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    if retrospective:
        lines[0] = "# Retrospective exp2 evaluation — test exposed"
        lines[2:2] = ["**Retrospective evaluation; test-exposed prompt. No clean holdout claim.**",
            "The chosen prompt may use the full 193-question benchmark, including these 58 test questions.",
            "The train70 prompt freeze is preserved and recorded, but does not bind the chosen retrospective prompt.",
            "The current chosen prompt SHA-256 and saved run/request prompt text must still match exactly.", ""]
    for view in summary["selected"]:
        for subset in EXPECTED_COUNTS:
            m = view["metrics"][subset]
            values = [f"{m['hits'][f'accuracy@{k}']}/{m['n_samples']} ({100 * m[f'accuracy@{k}']:.2f})" for k in KS]
            lines.append(f"| {view['model_label']} | {view['stage']} | {subset} | {m['n_samples']} | " +
                         " | ".join(values) + f" | {m['n_errors']} |")
    if summary["missing"]:
        lines.extend(["", "Missing eligible results (no score is imputed):"])
        lines.extend(f"- {m['model']}, stage {m['stage']}" for m in summary["missing"])
    lines.extend(["", "## Provenance", "", f"- Runs root: `{summary['runs_root']}`",
        f"- Split manifest: `{summary['frozen']['manifest_path']}`",
        f"- Manifest SHA-256: `{summary['frozen']['manifest_sha256']}`",
        f"- Full bucket SHA-256: `{summary['frozen']['files_sha256']['bucket_full.csv']}`",
        f"- Source corpus SHA-256: `{summary['frozen']['source_corpus_sha256']}`",
        f"- Prompt: `{summary['frozen']['prompt_path']}`",
        f"- Prompt SHA-256 (runner strips surrounding whitespace): `{summary['frozen']['system_prompt_sha256']}`",
        f"- Prompt evaluation: `{summary.get('prompt_evaluation', 'train70')}`",
        f"- Prompt freeze: `{summary['frozen']['prompt_freeze_path']}`",
        f"- Prompt freeze enforced: `{summary['frozen'].get('prompt_freeze_enforced', True)}`", ""])
    for view in summary["selected"]:
        lines.append(f"- {view['model_label']} stage {view['stage']}: `{view['source_folder']}` (run `{view['run_id']}`)")
    lines.extend(["", "summary.json records exact counts, macro @1 over observed gold classes, per-class metrics,",
        "source artifact SHA-256 hashes, all exclusions, and missing model/stage combinations.",
        "Candidates without gold examples stay in every decision but have no class accuracy denominator.",
        "Different selected run IDs across stages mean those stages came from different saved pipelines;",
        "inspect run_id and paired_artifact_dir before making paired comparisons.", "",
        "Integrity checks cannot establish train-only prompt authorship or absence of historical exposure.",
        ("These test metrics are retrospective benchmark subset scores with test exposure, not clean holdout results."
         if retrospective else "This split measures a held-out portion of the existing benchmark, not production generalization."), "",
        "## Exclusions", ""])
    if summary["exclusions"]:
        lines.extend(f"- `{entry['source_folder']}`: {entry['reason']}" for entry in summary["exclusions"])
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def evaluate(runs_root, split_dir, prompt, output_dir, models=None, prompt_evaluation="train70"):
    output_dir = Path(output_dir)
    require(not output_dir.exists(), f"Output directory must be new: {output_dir}")
    requested_models = model_scope(models)
    frozen = load_frozen(split_dir, prompt, prompt_evaluation=prompt_evaluation)
    selected, exclusions, missing = collect(runs_root, frozen, models=requested_models)
    summary = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "runs_root": str(Path(runs_root).resolve()), "complete": not missing,
        "scope": {"models": requested_models, "stages": STAGES, "n_candidates": 17, "subset_sizes": EXPECTED_COUNTS},
        "expected_results": len(requested_models) * len(STAGES), "prompt_evaluation": prompt_evaluation,
        "evaluation_status": "retrospective_test_exposed" if prompt_evaluation == "retrospective" else "train70_frozen_split",
        "evaluation_caveat": ("Test-exposed retrospective prompt; no clean holdout claim. Test metrics are benchmark subset scores."
            if prompt_evaluation == "retrospective" else
            "Integrity checks do not establish train-only prompt authorship or absence of historical exposure."),
        "selection_rule": "Latest complete eligible started_at per model/stage; path breaks timestamp ties; never by score",
        "metric_definition": "Per-sample hit@1/3/5; k clipped to ranking; every non-ok response is a miss; no confidence cutoff",
        "macro_definition": "Unweighted mean of class hit@1 over gold classes observed in each subset",
        "frozen": frozen["provenance"], "selected": selected, "exclusions": exclusions, "missing": missing}
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "README.md").write_text(render_readme(summary), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, required=True, help="Root containing 1-step/ and 2-step/ artifacts")
    parser.add_argument("--split-dir", type=Path, default=ROOT / "data/subagent_routing_exp2/splits/stratified70_seed42")
    parser.add_argument("--prompt", type=Path, default=ROOT / "exp2/prompts/routing_train70_ru_exp2.txt")
    parser.add_argument("--models", nargs="+", help="Explicit model IDs; default: Jev 1.14 and Luna")
    parser.add_argument("--prompt-evaluation", choices=PROMPT_EVALUATIONS, default="train70",
                        help="train70 enforces prompt_freeze.json; retrospective labels test exposure and verifies the chosen prompt")
    parser.add_argument("--output-dir", type=Path, required=True, help="New report directory; existing directories are refused")
    args = parser.parse_args(argv)
    try:
        summary = evaluate(args.runs_root, args.split_dir, args.prompt, args.output_dir,
                           models=args.models, prompt_evaluation=args.prompt_evaluation)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(f"Selected {len(summary['selected'])}/{summary['expected_results']} model/stage results; excluded {len(summary['exclusions'])}; "
          f"report: {args.output_dir}")
    return 0 if summary["selected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
