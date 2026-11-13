"""Classification evaluation.

Runs the classifier over the labelled dataset and prints the metrics that
matter for autonomous publishing:

* precision, recall and F1 for "is this a genuine freelance or contract
  opportunity";
* false positive and false negative rate;
* automatic approval accuracy (of everything the engine would auto approve);
* scam detection performance (recall on the unsafe cases).

Usage::

    .venv/Scripts/python.exe scripts/evaluate_classification.py
    .venv/Scripts/python.exe scripts/evaluate_classification.py --markdown out.md

The script never makes network calls unless ``CLASSIFICATION_PROVIDER`` is
set to a model provider, in which case the AI stage runs as well.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(ROOT))

from app.services.classification.pipeline import (  # noqa: E402
    DECISION_AUTO_APPROVE,
    classify_post,
)
from app.services.classification.taxonomy import (  # noqa: E402
    PUBLISHABLE_SET,
    UNSAFE_SET,
)

DATASET_PATH = ROOT / "tests" / "fixtures" / "classification_dataset.json"

#: A case is a true positive when the post is a genuine freelance or contract
#: opportunity. The ground truth is the expected classification of the case.
TRUE_POSITIVE_LABELS = PUBLISHABLE_SET


def load_cases(path: Path = DATASET_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"]


def _simulated_provider():
    """A provider that agrees with the rule engine at a fixed confidence.

    It exists to measure the autonomous path without a live model. It is a
    simulation, never a measurement of a real model.
    """
    from app.services.classification.schema import AIPostClassification

    class _SimulatedProvider:
        """Returns the classification it is told to agree with."""

        name = "llm"
        model = "simulated-agreement"

        def __init__(self):
            self.current = "freelance_job"

        def classify(self, text: str) -> AIPostClassification:  # pragma: no cover
            publishable = self.current in TRUE_POSITIVE_LABELS
            return AIPostClassification.model_validate(
                {
                    "classification": self.current,
                    "job_category": "software",
                    "engagement_type": self.current if publishable else None,
                    "is_genuine_opportunity": publishable,
                    "is_publishable": publishable,
                    "confidence": 0.98,
                    "title": None,
                    "skills": [],
                    "budget": None,
                    "currency": None,
                    "duration": None,
                    "deadline": None,
                    "client_name": None,
                    "application_url": None,
                    "risk_level": "none",
                    "risk_flags": [],
                    "evidence": ["simulated model agreement"],
                    "rejection_reasons": [],
                }
            )

    return _SimulatedProvider()


def evaluate(cases: list[dict], use_ai: bool = False, simulate_ai: bool = False) -> dict:
    """Classify every case and compute the evaluation metrics."""
    tp = fp = tn = fn = 0
    confusion: list[dict] = []
    auto_approved = 0
    auto_approved_true = 0
    scam_total = 0
    scam_detected = 0
    by_language: dict[str, dict[str, int]] = {}
    auto_approved_ids: list[str] = []

    provider = _simulated_provider() if simulate_ai else None

    for case in cases:
        expected = case["expected_classification"]
        if simulate_ai:
            # The simulated model agrees with the rule engine about this post.
            rule_result = classify_post(case["text"], use_ai=False)
            provider.current = rule_result.classification
        result = classify_post(
            case["text"], provider=provider, use_ai=use_ai or simulate_ai
        )
        predicted = result.classification
        truth_positive = expected in TRUE_POSITIVE_LABELS
        predicted_positive = predicted in TRUE_POSITIVE_LABELS

        if truth_positive and predicted_positive:
            tp += 1
        elif truth_positive and not predicted_positive:
            fn += 1
        elif not truth_positive and predicted_positive:
            fp += 1
        else:
            tn += 1

        if result.decision == DECISION_AUTO_APPROVE:
            auto_approved += 1
            auto_approved_ids.append(case["id"])
            if truth_positive:
                auto_approved_true += 1

        if expected in UNSAFE_SET:
            scam_total += 1
            if predicted in UNSAFE_SET:
                scam_detected += 1

        language = case.get("language", "en")
        bucket = by_language.setdefault(language, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        if truth_positive and predicted_positive:
            bucket["tp"] += 1
        elif truth_positive:
            bucket["fn"] += 1
        elif predicted_positive:
            bucket["fp"] += 1
        else:
            bucket["tn"] += 1

        if predicted != expected:
            confusion.append(
                {
                    "id": case["id"],
                    "expected": expected,
                    "predicted": predicted,
                    "confidence": round(result.confidence, 3),
                    "risk_level": result.risk_level,
                }
            )

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    total = tp + fp + tn + fn

    return {
        "cases": total,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
        "false_negative_rate": round(fn / (fn + tp), 4) if (fn + tp) else 0.0,
        "accuracy": round((tp + tn) / total, 4) if total else 0.0,
        "auto_approved": auto_approved,
        "auto_approved_ids": auto_approved_ids,
        "auto_approval_accuracy": (
            round(auto_approved_true / auto_approved, 4) if auto_approved else None
        ),
        "scam_cases": scam_total,
        "scam_detected": scam_detected,
        "scam_recall": round(scam_detected / scam_total, 4) if scam_total else 1.0,
        "by_language": by_language,
        "mismatches": confusion,
    }


def format_report(metrics: dict) -> str:
    lines = [
        "Yekis Intelligence classification evaluation",
        "=" * 44,
        f"dataset cases           : {metrics['cases']}",
        f"true positives          : {metrics['true_positives']}",
        f"false positives         : {metrics['false_positives']}",
        f"true negatives          : {metrics['true_negatives']}",
        f"false negatives         : {metrics['false_negatives']}",
        "",
        f"precision               : {metrics['precision']:.4f}",
        f"recall                  : {metrics['recall']:.4f}",
        f"f1                      : {metrics['f1']:.4f}",
        f"false positive rate     : {metrics['false_positive_rate']:.4f}",
        f"false negative rate     : {metrics['false_negative_rate']:.4f}",
        f"accuracy                : {metrics['accuracy']:.4f}",
        "",
        f"auto approved           : {metrics['auto_approved']}",
        ("auto approval accuracy  : "
        f"{metrics['auto_approval_accuracy']}"),
        (
            "auto approved cases     : "
            + (", ".join(metrics["auto_approved_ids"]) or "none")
        ),
        (f"scam detection recall   : {metrics['scam_recall']:.4f} "
        f"({metrics['scam_detected']}/{metrics['scam_cases']})"),
        "",
        "per language:",
    ]
    for language, bucket in sorted(metrics["by_language"].items()):
        lines.append(
            f"  {language:6s} tp={bucket['tp']} fp={bucket['fp']} "
            f"fn={bucket['fn']} tn={bucket['tn']}"
        )
    if metrics["mismatches"]:
        lines.append("")
        lines.append("mismatches:")
        for row in metrics["mismatches"]:
            lines.append(
                f"  {row['id']}: expected {row['expected']}, got {row['predicted']} "
                f"(confidence {row['confidence']}, risk {row['risk_level']})"
            )
    else:
        lines.append("")
        lines.append("mismatches: none")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DATASET_PATH))
    parser.add_argument("--markdown", default=None, help="write a markdown report")
    parser.add_argument(
        "--with-ai",
        action="store_true",
        help="also run the AI stage (needs a configured model provider)",
    )
    parser.add_argument(
        "--simulate-ai",
        action="store_true",
        help="simulate a model that agrees with the rule engine (0.98 confidence)",
    )
    args = parser.parse_args()

    cases = load_cases(Path(args.dataset))
    metrics = evaluate(cases, use_ai=args.with_ai, simulate_ai=args.simulate_ai)
    report = format_report(metrics)
    print(report)

    if args.markdown:
        Path(args.markdown).write_text(
            "# Classification evaluation\n\n```\n" + report + "\n```\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
