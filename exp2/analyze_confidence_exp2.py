#!/usr/bin/env python3
"""Offline native-confidence sweeps; never invokes a decision backend."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "routing_exp2.txt": ("base", "Базовый"),
    "routing_scenarios_ru_exp2.txt": ("scenario", "Сценарный"),
    "routing_general_ru_exp2.txt": ("general", "Обобщённый"),
}
MODELS = {"typesafe/jev-1.14": "Jev 1.14", "openai/gpt-6-luna-decisions": "Luna"}
FIXED_THRESHOLDS = [0, .3, .4, .5, .6, .7, .8, .9, .95, .98, .99, 1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def valid_confidence(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def wilson(correct, n):
    """Nominal fixed-threshold binomial interval, not post-selection inference."""
    if not n:
        return None
    z = 1.959963984540054
    p = correct / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0, center - half), min(1, center + half)]


def operating_point(rows, threshold):
    if not valid_confidence(threshold):
        raise ValueError("Threshold must be finite and between 0 and 1")
    usable = [row for row in rows if row["status"] == "ok" and valid_confidence(row["confidence"])]
    accepted = [row for row in usable if row["confidence"] >= threshold]
    n, kept = len(rows), len(accepted)
    correct = sum(row["correct"] for row in accepted)
    all_correct = sum(row["correct"] for row in rows)
    usable_correct = sum(row["correct"] for row in usable)
    result = {
        "threshold": threshold, "n_total": n, "accepted": kept, "coverage": kept / n if n else None,
        "accepted_correct": correct, "accepted_wrong": kept - correct,
        "accuracy_at_1_accepted": correct / kept if kept else None,
        "accuracy_wilson_95_nominal": wilson(correct, kept),
        "rejected_total": n - kept, "rejected_low_confidence": len(usable) - kept,
        "unavailable": n - len(usable), "withheld_correct": all_correct - correct,
        "rejected_wrong_predictions": len(usable) - usable_correct - (kept - correct),
        "correct_automated_share_of_bucket": correct / n if n else None,
    }
    assert result["accepted"] + result["rejected_total"] == n
    assert result["accepted_correct"] + result["withheld_correct"] == all_correct
    return result


def sweep(rows):
    cuts = sorted({0.0, 1.0, *(float(row["confidence"]) for row in rows
                             if row["status"] == "ok" and valid_confidence(row["confidence"]))})
    return [operating_point(rows, threshold) for threshold in cuts]


def best_point(points, minimum_coverage):
    feasible = [p for p in points if p["accepted"] and p["coverage"] >= minimum_coverage]
    if not feasible:
        return None
    # Rational comparison avoids treating equal empirical accuracies as unequal floats.
    return max(feasible, key=lambda p: (Fraction(p["accepted_correct"], p["accepted"]),
                                        p["accepted"], -p["threshold"]))


def bins(rows):
    edges = [0, .5, .7, .8, .9, .95, .98, 1]
    result = []
    for index, (lo, hi) in enumerate(zip(edges, edges[1:])):
        subset = [r for r in rows if r["status"] == "ok" and valid_confidence(r["confidence"])
                  and lo <= r["confidence"] and (r["confidence"] < hi or index == len(edges) - 2)]
        result.append({"lower_inclusive": lo, "upper": hi, "upper_inclusive": index == len(edges) - 2,
            "n": len(subset), "correct": sum(r["correct"] for r in subset),
            "accuracy_at_1": sum(r["correct"] for r in subset) / len(subset) if subset else None,
            "mean_native_confidence": mean(r["confidence"] for r in subset) if subset else None})
    return result


def load_view(folder, config, metrics):
    stage = metrics["evaluated_stage"]
    details_path = folder / f"stage_{stage}_details.jsonl"
    details = read_jsonl(details_path)
    first = {r["sample_id"]: r for r in read_jsonl(folder / "stage_1_winners.jsonl")} if stage == 2 else {}
    rows = []
    for detail in details:
        status = detail["status"]
        native = None
        if status == "ok":
            native = detail["response"]["answers"][config["question_id"]].get("confidence")
            if native is not None and not valid_confidence(native):
                raise ValueError(f"Invalid native confidence in {details_path}, sample {detail['sample_id']}")
            if native != detail.get("confidence"):
                raise ValueError(f"Saved confidence differs from native field in {details_path}")
            if not detail["ranking"] or detail["winner"] != detail["ranking"][0]:
                raise ValueError("Winner/ranking mismatch")
        expected = detail["expected_subagents"]
        correct = status == "ok" and detail["winner"] in expected
        previous = first.get(detail["sample_id"])
        shortlist_miss = (not bool(set(previous["ranking"][:config["shortlist_k"]]) & set(expected))
                          if previous is not None and previous["status"] == "ok" else None)
        rows.append({"sample_id": detail["sample_id"], "question": detail["question"],
            "expected_subagents": expected, "winner": detail["winner"], "status": status,
            "confidence": native, "winner_probability": detail.get("probability"), "correct": correct,
            "stage_1_shortlist_miss": shortlist_miss})
    if len(rows) != config["n_samples"] or len({r["sample_id"] for r in rows}) != len(rows):
        raise ValueError(f"Incomplete or duplicate rows: {folder}")
    if sum(r["correct"] for r in rows) != metrics["hits"]["accuracy@1"]:
        raise ValueError(f"Baseline metric mismatch: {folder}")
    prompt_id, prompt_label = PROMPTS[Path(config["arguments"]["system_prompt"]).name]
    points = sweep(rows)
    high_wrong = [r for r in rows if r["status"] == "ok" and not r["correct"]
                  and valid_confidence(r["confidence"]) and r["confidence"] >= .95]
    high_wrong.sort(key=lambda r: (-r["confidence"], r["sample_id"]))
    result = {
        "id": f"{prompt_id}_{'jev' if config['model'].startswith('typesafe/') else 'luna'}_s{stage}",
        "model": config["model"], "model_label": MODELS[config["model"]],
        "prompt": prompt_id, "prompt_label": prompt_label, "stage": stage,
        "source_folder": str(folder.resolve()), "started_at": config["started_at"],
        "source_sha256": {"config.json": digest(folder / "config.json"), "metrics.json": digest(folder / "metrics.json"),
                          details_path.name: digest(details_path)},
        "prompt_sha256": hashlib.sha256(config["system_prompt"].encode()).hexdigest(),
        "bucket_sha256": config["bucket_sha256"], "corpus_sha256": config["corpus_sha256"],
        "n": len(rows), "baseline_hits": sum(r["correct"] for r in rows),
        "baseline_accuracy_at_1": metrics["accuracy@1"],
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "missing_confidence_on_success": sum(r["status"] == "ok" and r["confidence"] is None for r in rows),
        "points": points,
        "fixed_thresholds": [operating_point(rows, t) for t in FIXED_THRESHOLDS],
        "recommendations": {str(int(100 * floor)): best_point(points, floor) for floor in (.9, .8, .7, .5)},
        "confidence_bins": bins(rows), "wrong_confidence_ge_095": len(high_wrong),
        "high_confidence_errors": high_wrong,
        "shortlist_misses": sum(r["stage_1_shortlist_miss"] is True for r in rows),
        "high_confidence_shortlist_misses": sum(r["stage_1_shortlist_miss"] is True
                                               and valid_confidence(r["confidence"]) and r["confidence"] >= .95 for r in rows),
        "certain_shortlist_misses": sum(r["stage_1_shortlist_miss"] is True and r["confidence"] == 1 for r in rows),
        "rows": rows,
    }
    if stage == 2:
        result["source_sha256"]["stage_1_winners.jsonl"] = digest(folder / "stage_1_winners.jsonl")
    return result


def collect(root, bucket, corpus):
    bucket_hash, corpus_hash = digest(bucket), digest(corpus)
    prompt_hashes = {name: hashlib.sha256((root / "exp2" / "prompts" / name).read_text(encoding="utf-8").strip().encode()).hexdigest()
                     for name in PROMPTS}
    selected, exclusions = {}, []
    for path in sorted((root / "experiments/exp2").glob("*/*/config.json")):
        config = json.loads(path.read_text(encoding="utf-8"))
        name = Path(config["arguments"]["system_prompt"]).name
        if config["model"] not in MODELS or name not in PROMPTS:
            continue
        reason = None
        if config.get("bucket_sha256") != bucket_hash or config.get("corpus_sha256") != corpus_hash:
            reason = "different bucket/catalog"
        elif hashlib.sha256(config["system_prompt"].encode()).hexdigest() != prompt_hashes[name]:
            reason = "different prompt revision"
        metrics_path = path.parent / "metrics.json"
        if not metrics_path.is_file():
            reason = "no metrics yet"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.is_file() else {}
        if not metrics.get("complete") or metrics.get("n_samples") != 193 or config.get("n_subagents") != 17:
            reason = reason or "not complete 193/17 view"
        if metrics.get("evaluated_stage") == 2 and config["shortlist_k"] != 5:
            reason = reason or "different shortlist size"
        if reason:
            exclusions.append({"folder": str(path.parent), "reason": reason})
            continue
        key = (config["model"], name, metrics["evaluated_stage"])
        if key not in selected or config["started_at"] > selected[key][1]["started_at"]:
            selected[key] = (path.parent, config, metrics)
    views = [load_view(*item) for item in selected.values()]
    views.sort(key=lambda v: (list(PROMPTS.values()).index((v["prompt"], v["prompt_label"])),
                              list(MODELS).index(v["model"]), v["stage"]))
    return views, exclusions


def pct(value):
    return "—" if value is None else f"{100 * value:.2f}%"


def create_plots(views, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    colors = {1: "#2563eb", 2: "#ea580c"}
    for mode in ("accuracy_coverage", "threshold_tradeoff"):
        fig, axes = plt.subplots(2, 3, figsize=(18, 10.4), squeeze=False)
        for row, model in enumerate(MODELS):
            for col, (prompt_id, label) in enumerate(PROMPTS.values()):
                ax = axes[row][col]
                matches = [v for v in views if v["model"] == model and v["prompt"] == prompt_id]
                if mode == "accuracy_coverage":
                    ax.axvspan(80, 100, color="#dcfce7", alpha=.4)
                    ax.axvline(80, color="#64748b", lw=1, ls=":")
                for view in matches:
                    points = [p for p in view["points"] if p["accepted"]]
                    color = colors[view["stage"]]
                    if mode == "accuracy_coverage":
                        visible = [p for p in points if p["coverage"] >= .25]
                        ax.plot([100 * p["coverage"] for p in visible], [100 * p["accuracy_at_1_accepted"] for p in visible],
                                color=color, lw=2, alpha=.9)
                        rec = view["recommendations"]["80"]
                        if rec is not None:
                            ax.scatter(100 * rec["coverage"], 100 * rec["accuracy_at_1_accepted"],
                                       color=color, s=65, edgecolors="white", zorder=5)
                    else:
                        ax.plot([p["threshold"] for p in points], [100 * p["accuracy_at_1_accepted"] for p in points],
                                color=color, lw=2)
                        ax.plot([p["threshold"] for p in points], [100 * p["coverage"] for p in points],
                                color=color, lw=1.6, ls="--", alpha=.8)
                        ax.axhline(100 * view["baseline_accuracy_at_1"], color=color, lw=.8, ls=":", alpha=.5)
                ax.set_title(f"{MODELS[model]} · {label}", fontweight="bold")
                ax.grid(alpha=.2)
                ax.set_ylim(40 if mode == "accuracy_coverage" else 0, 102)
                ax.set_xlim(25 if mode == "accuracy_coverage" else 0, 100 if mode == "accuracy_coverage" else 1)
                ax.set_xlabel("Принято от всех 193 запросов, %" if mode == "accuracy_coverage" else "Порог нативного confidence (≥)")
                ax.set_ylabel("Accuracy@1 среди принятых, %" if mode == "accuracy_coverage" else "Процент")
                if prompt_id == "base":
                    ax.text(.02, .03, "2-step на 193/17 отсутствует", transform=ax.transAxes, fontsize=9, color="#64748b")
        title = ("Точность и покрытие: кружки — лучшие точки при покрытии ≥80%" if mode == "accuracy_coverage"
                 else "Порог confidence: сплошная — accuracy@1, пунктир — покрытие")
        fig.suptitle(title, fontsize=17, fontweight="bold", y=.99)
        legend = [Line2D([0], [0], color=colors[s], lw=2, label=f"{s}-step") for s in (1, 2)]
        if mode == "threshold_tradeoff":
            legend += [Line2D([0], [0], color="#475569", lw=2, label="Accuracy принятых"),
                       Line2D([0], [0], color="#475569", lw=2, ls="--", label="Покрытие")]
        fig.legend(handles=legend, loc="lower center", ncol=len(legend), frameon=False)
        fig.tight_layout(rect=(0, .045, 1, .95))
        for extension in ("png", "svg"):
            fig.savefig(output / f"{mode}.{extension}", dpi=170, facecolor="white")
        plt.close(fig)


def write_report(views, exclusions, output):
    report = ["# Confidence и accuracy@1: exp2, русские промпты", "",
        "Текущая корзина: 193 запроса, 17 кандидатов. Модели Jev 1.14 и Luna. Только завершённые прогоны с совпадающими SHA-256 bucket/corpus и текущих RU-промптов. Для повторов выбирается последний завершённый прогон каждой комбинации. 2-step использует shortlist=5. Экспорт stage 1 не считается отдельным повтором.", "",
        "## Как читать результаты", "",
        "Принимается успешный ответ с **нативным response.answers.subagent.confidence ≥ порог**. Поле probability/p_max не используется как замена. Coverage = принято / 193; accuracy@1 принятых = правильных среди принятых / принято. Отклонённые вопросы требуют fallback, его качество здесь не измеряется.", "",
        "Исходная accuracy без фильтра включает ошибки API как промахи. При пороге 0 принимаются только успешные ответы с confidence, поэтому у Luna с API-ошибкой уже есть один вопрос за бортом. Таблицы отдельно показывают низкий confidence и отсутствие результата; это не одно и то же.", "",
        "Основное допущение: сохранить **не менее 80%** корзины (не меньше 155/193). Среди допустимых порогов максимизируется accuracy принятых; при равной точности выбирается большее покрытие. Ниже также ограничения 90%, 70% и 50%. Это выбранные аналитиком компромиссы, не заданная пользователем стоимость ошибки.", "",
        "## Основной компромисс: покрытие ≥80%", "",
        "| Промпт | Модель | Этап | Без фильтра @1 | Порог ≥ | Принято | Покрытие | @1 принятых | За бортом | Из них правильных |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for v in views:
        p = v["recommendations"]["80"]
        if p is None:
            continue
        report.append(f"| {v['prompt_label']} | {v['model_label']} | {v['stage']}-step | {pct(v['baseline_accuracy_at_1'])} | {p['threshold']:.2f} | {p['accepted']}/193 | {pct(p['coverage'])} | {pct(p['accuracy_at_1_accepted'])} ({p['accepted_correct']}/{p['accepted']}) | {p['rejected_total']} | {p['withheld_correct']} |")
    report += ["", "![](accuracy_coverage.png)", "", "![](threshold_tradeoff.png)", "",
        "## Другие ограничения покрытия", "",
        "В ячейке: **порог; принято; accuracy@1**. Из-за одинаковых confidence нельзя всегда получить ровно заданное покрытие. Порог не разделяет равные значения.", "",
        "| Промпт | Модель | Этап | ≥90% | ≥80% | ≥70% | ≥50% |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for v in views:
        cells = []
        for floor in ("90", "80", "70", "50"):
            p = v["recommendations"][floor]
            cells.append("—" if p is None else f"{p['threshold']:.2f}; {p['accepted']}; {pct(p['accuracy_at_1_accepted'])}")
        report.append(f"| {v['prompt_label']} | {v['model_label']} | {v['stage']}-step | " + " | ".join(cells) + " |")
    report += ["", "## Почему высокий confidence не устраняет все промахи", ""]
    for v in views:
        if v["prompt"] == "general" and v["stage"] == 2:
            report += [f"{v['model_label']}, обобщённый промпт, 2-step: gold отсутствует в shortlist у {v['shortlist_misses']} вопросов; {v['high_confidence_shortlist_misses']} из них получили confidence ≥0.95, в том числе {v['certain_shortlist_misses']} — ровно 1.", ""]
    report += ["Второй этап выбирает внутри сокращённого набора и может уверенно выбрать неверный глобальный маршрут.", "",
        "Часть уверенных промахов — расхождение описанных возможностей и единственной gold-метки: навигация/service_navigation, вовлечённость/metrics, создание встреч/meetings. Порог не разрешает это противоречие разметки. Сценарный промпт содержит правила, настроенные по этой же корзине.", "",
        "При confidence=1 точность всё ещё не обязана быть 100%:", "",
        "| Промпт | Модель | Этап | Принято с confidence=1 | Правильно | @1 |", "| --- | --- | --- | ---: | ---: | ---: |"]
    for v in views:
        p = operating_point(v["rows"], 1)
        report.append(f"| {v['prompt_label']} | {v['model_label']} | {v['stage']}-step | {p['accepted']} | {p['accepted_correct']} | {pct(p['accuracy_at_1_accepted'])} |")
    report += ["", "## Ограничения подбора", "",
        "Пороги выбраны после просмотра gold на всех этих 193 вопросах. Это operating points на данных разработки, а не доказанные production-пороги. Малая корзина, связанные сценарии и перебор порогов дают оптимистичную оценку. Выбери рабочий порог на отдельном dev-наборе и проверь один раз на новых данных.", "",
        "Эмпирические 100% принятых ответов не дают гарантии. В summary.json сохранены номинальные Wilson 95% интервалы для заранее фиксированного порога; они не учитывают подбор порога/промпта и зависимости между вопросами.", "",
        "Рост accuracy среди принятых не означает рост числа правильных ответов на всей корзине: часть правильных ответов тоже отправляется в fallback. Для этого сохранены accepted_correct, withheld_correct и correct_automated_share_of_bucket. Числа confidence разных моделей и этапов не считаются общей калиброванной шкалой.", "",
        "## Детальные пороги по каждому прогону", ""]
    for v in views:
        report += [f"### {v['prompt_label']} · {v['model_label']} · {v['stage']}-step", "",
            f"Источник: `{v['source_folder']}`. Статусы: `{v['status_counts']}`. Нативный confidence отсутствует у {v['missing_confidence_on_success']} успешных ответов.", "",
            "| Порог ≥ | Принято | Правильно | Ошибочных принято | @1 | Покрытие | За бортом | Низкий confidence | Нет ответа/confidence | Правильных отсеяно |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for p in v["fixed_thresholds"]:
            report.append(f"| {p['threshold']:.2f} | {p['accepted']} | {p['accepted_correct']} | {p['accepted_wrong']} | {pct(p['accuracy_at_1_accepted'])} | {pct(p['coverage'])} | {p['rejected_total']} | {p['rejected_low_confidence']} | {p['unavailable']} | {p['withheld_correct']} |")
        report += ["", "Интервалы confidence (не накопительные пороги):", "",
            "| Интервал | Ответов | Правильно | @1 | Средний native confidence |", "| --- | ---: | ---: | ---: | ---: |"]
        for b in v["confidence_bins"]:
            bracket = "]" if b["upper_inclusive"] else ")"
            avg = f"{b['mean_native_confidence']:.3f}" if b["n"] else "—"
            report.append(f"| [{b['lower_inclusive']:.2f}, {b['upper']:.2f}{bracket} | {b['n']} | {b['correct']} | {pct(b['accuracy_at_1'])} | {avg} |")
        report.append("")
    report += ["## Файлы и воспроизведение", "",
        "- [summary.json](summary.json): полный sweep всех уникальных порогов, рекомендации, интервалы, provenance и списки уверенных промахов.",
        "- [thresholds.jsonl](thresholds.jsonl): все точки sweep, по одной JSON-строке на порог/прогон.",
        "- [recommendations.json](recommendations.json): рекомендуемые точки для ограничений покрытия.",
        f"- [sample_decisions.jsonl](sample_decisions.jsonl): все 193 × {len(views)} исходных решений с native confidence и gold, для аудита.",
        "- SVG-копии графиков доступны рядом с PNG.", "",
        "```bash", "python3 exp2/analyze_confidence_exp2.py --output-dir /tmp/confidence-exp2-report", "```", "",
        "Скрипт работает офлайн; требуются matplotlib для графиков, модели и API не вызываются. Существующая output-dir не перезаписывается. Исторические результаты и runtime-порог маршрутизации не меняются.", "",
        f"Пропущено несовместимых/неполных исторических views: {len(exclusions)}. Их причины записаны в summary.json. У базового RU-промпта 2-step на текущих 193/17 не найден."]
    (output / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--bucket", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    bucket = args.bucket or root / "data/subagent_routing_exp2/subagents_routing_bucket_only_enabled.csv"
    corpus = args.corpus or root / "data/subagent_routing_exp2/subagents_routing_corpus.csv"
    output = args.output_dir or root / "experiments/exp2/analysis" / ("confidence_ru_" + datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y%m%dT%H%M%S"))
    views, exclusions = collect(root, bucket, corpus)
    if not views:
        raise ValueError("No complete matching runs found")
    output.mkdir(parents=True, exist_ok=False)
    summary = {"generated_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
        "confidence_field": "response.answers.subagent.confidence; no p_max substitution",
        "acceptance": "status=ok and finite native confidence >= threshold", "coverage_denominator": 193,
        "objective": "maximum empirical accepted accuracy subject to coverage >= floor; ties prefer coverage",
        "source_script_sha256": digest(Path(__file__)), "views": views, "excluded_views": exclusions}
    write_json(output / "summary.json", summary)
    write_json(output / "recommendations.json", [{k: v[k] for k in ("id", "model", "prompt", "stage", "baseline_accuracy_at_1", "recommendations")} for v in views])
    for name, key in (("thresholds.jsonl", "points"), ("sample_decisions.jsonl", "rows")):
        with (output / name).open("w", encoding="utf-8") as handle:
            for view in views:
                for item in view[key]:
                    handle.write(json.dumps({"view_id": view["id"], **item}, ensure_ascii=False, allow_nan=False) + "\n")
    create_plots(views, output)
    write_report(views, exclusions, output)
    print(f"Saved {len(views)} views to {output}")
    for v in views:
        p = v["recommendations"]["80"]
        print(v["id"], p["threshold"], p["accepted"], p["accepted_correct"], pct(p["accuracy_at_1_accepted"]))


if __name__ == "__main__":
    main()
