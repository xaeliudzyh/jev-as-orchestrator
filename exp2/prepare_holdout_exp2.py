#!/usr/bin/env python3
"""Freeze a deterministic, label-stratified exp2 train/test split (no inference)."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import unicodedata

ROOT = Path(__file__).resolve().parents[1]

if __package__ in {None, ""}:
    import sys
    sys.path.insert(0, str(ROOT))

from exp2.data_exp2 import BUCKET_FIELDS, CORPUS_FIELDS, read_rows


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized_question(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def split_indices(rows, test_fraction=Fraction(3, 10), seed=42):
    """Constrained largest-remainder allocation; never split identical questions."""
    test_fraction = Fraction(test_fraction)
    if not 0 < test_fraction < 1:
        raise ValueError("test fraction must be between 0 and 1")
    groups, seen = defaultdict(list), set()
    for index, row in enumerate(rows):
        key = normalized_question(row[BUCKET_FIELDS[0]])
        if not key or key in seen:
            raise ValueError("Empty/duplicate normalized question: group or deduplicate before splitting")
        seen.add(key)
        groups[row["SubAgent"]].append(index)
    if not groups or any(len(ids) < 2 for ids in groups.values()):
        raise ValueError("Each observed label needs at least two rows for both train and test")
    n_test = math.ceil(len(rows) * test_fraction)
    if not len(groups) <= n_test <= len(rows) - len(groups):
        raise ValueError("Requested ratio cannot represent every label in both subsets")
    ideals = {label: len(ids) * test_fraction for label, ids in groups.items()}
    counts = {label: max(1, min(len(groups[label]) - 1, math.floor(ideal))) for label, ideal in ideals.items()}
    while sum(counts.values()) < n_test:
        eligible = [label for label in groups if counts[label] < len(groups[label]) - 1]
        # Largest shortfall first, alphabetic label breaks exact quota ties.
        label = min(eligible, key=lambda label: (-(ideals[label] - counts[label]), label))
        counts[label] += 1
    while sum(counts.values()) > n_test:
        eligible = [label for label in groups if counts[label] > 1]
        label = min(eligible, key=lambda label: (-(counts[label] - ideals[label]), label))
        counts[label] -= 1
    test = set()
    for label, ids in groups.items():
        ordered = sorted(ids, key=lambda index: (sha(json.dumps(
            [seed, label, index, rows[index][BUCKET_FIELDS[0]]], ensure_ascii=False).encode()), index))
        test.update(ordered[:counts[label]])
    return {"train": [i for i in range(len(rows)) if i not in test], "test": sorted(test)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_bucket(folder, name, rows):
    import csv
    with (folder / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BUCKET_FIELDS, delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (folder / f"{name}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def prepare(bucket, corpus, output, seed=42):
    rows = read_rows(bucket, BUCKET_FIELDS)
    catalog = [r for r in read_rows(corpus, CORPUS_FIELDS) if r["enabled"] == 1]
    names = {r["target_subagent"] for r in catalog}
    if len(names) != len(catalog) or set(r["SubAgent"] for r in rows) - names:
        raise ValueError("Duplicate catalog names or bucket references an unavailable subagent")
    indices = split_indices(rows, seed=seed)
    output.mkdir(parents=True, exist_ok=False)
    write_bucket(output, "bucket_full", rows)
    # Preserve exact full-bucket bytes for comparison against existing runner hashes.
    if bucket.suffix.lower() == ".csv":
        shutil.copyfile(bucket, output / "bucket_full.csv")
    for subset, ids in indices.items():
        write_bucket(output, f"bucket_{subset}", [rows[i] for i in ids])
    catalog_path = output / "catalog_enabled.jsonl"
    catalog_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in catalog), encoding="utf-8")
    # This is the complete input for the fresh author: no test, assignment manifest,
    # skill-level provenance, old prompt, historic prediction or metric is included.
    train_input = {"task": "subagent routing", "examples": [
        {"train_id": j, "query": rows[i][BUCKET_FIELDS[0]], "target_subagent": rows[i]["SubAgent"]}
        for j, i in enumerate(indices["train"])], "candidate_catalog": catalog}
    write_json(output / "author_train_input.json", train_input)
    assignment = [{"original_sample_id": i, "split": subset, "split_sample_id": j,
                   "question_sha256": sha(rows[i][BUCKET_FIELDS[0]].encode()), "target_subagent": rows[i]["SubAgent"]}
                  for subset, ids in indices.items() for j, i in enumerate(ids)]
    manifest = {"schema_version": 1, "seed": seed, "test_fraction": "3/10", "n_full": len(rows),
        "n_train": len(indices["train"]), "n_test": len(indices["test"]),
        "n_candidates": len(catalog), "n_observed_labels": len(set(r["SubAgent"] for r in rows)),
        "allocation": "ceil(N*0.30); constrained largest shortfall, 1<=test_per_label<=N_label-1; alphabetic quota ties",
        "selection": "sha256(JSON[seed,label,original_sample_id,query]); preserve original order within each export",
        "stratification": "SubAgent only; seed chosen before prompt authoring, not selected by metrics",
        "duplicate_policy": "Reject duplicate questions after NFKC/casefold/whitespace normalization",
        "source_bucket_sha256": sha(bucket.read_bytes()), "source_corpus_sha256": sha(corpus.read_bytes()),
        "label_counts": {label: {"full": sum(r["SubAgent"] == label for r in rows),
             **{subset: sum(rows[i]["SubAgent"] == label for i in ids) for subset, ids in indices.items()}}
             for label in sorted(set(r["SubAgent"] for r in rows))},
        "train_original_ids": indices["train"], "test_original_ids": indices["test"], "assignment": assignment,
        "files_sha256": {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file()}}
    write_json(output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", type=Path, default=ROOT / "data/subagent_routing_exp2/subagents_routing_bucket_only_enabled.csv")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/subagent_routing_exp2/subagents_routing_corpus.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/subagent_routing_exp2/splits/stratified70_seed42")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    m = prepare(args.bucket, args.corpus, args.output_dir, args.seed)
    print(f"Frozen train={m['n_train']}, test={m['n_test']}, candidates={m['n_candidates']} in {args.output_dir}")


if __name__ == "__main__":
    main()
