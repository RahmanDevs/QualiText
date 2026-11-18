"""Data Quality Index (DQI) scoring engine (CLAUDE.md Section 2.7 & docs/dqi.md)."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.modules.base import ModuleResult

DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    "structure_validity": 0.15,
    "cleanliness_privacy": 0.25,
    "uniqueness_diversity": 0.20,
    "instruction_quality": 0.25,
    "safety_bias": 0.15,
}

DIMENSION_LABELS: dict[str, str] = {
    "structure_validity": "Validitas Struktur",
    "cleanliness_privacy": "Kebersihan & Privasi",
    "uniqueness_diversity": "Keunikan & Keberagaman",
    "instruction_quality": "Kualitas Instruksi–Respons",
    "safety_bias": "Keamanan & Bias",
}


@dataclass
class DqiBreakdown:
    """Complete DQI calculation result with per-dimension contribution."""

    dqi_total: float
    status_label: str
    is_partial: bool
    critical_capped: bool
    critical_cap_value: float
    dimensions: list[dict[str, Any]]


def _score_label(score: float) -> str:
    if score >= 90.0:
        return "Siap Latih"
    if score >= 75.0:
        return "Baik"
    if score >= 60.0:
        return "Perlu Perbaikan"
    return "Berisiko"


def _extract_dimension_score(dim: str, results: list[ModuleResult]) -> float:
    """Convert module metrics into a normalized 0-100 score for `dim`."""
    scores: list[float] = []
    for res in results:
        m = res.metrics
        if dim == "structure_validity":
            valid_ratio = m.get("valid_row_ratio", 1.0)
            trunc_risk = m.get("truncation_risk_ratio", 0.0)
            combined = (valid_ratio * 0.8 + (1.0 - trunc_risk) * 0.2) * 100.0
            scores.append(max(0.0, min(100.0, combined)))
        elif dim == "cleanliness_privacy":
            if "pii_clean_ratio" in m:
                scores.append(max(0.0, min(100.0, m["pii_clean_ratio"] * 100.0)))
            elif "artifact_clean_ratio" in m:
                scores.append(max(0.0, min(100.0, m["artifact_clean_ratio"] * 100.0)))
            elif "lang_consistency_ratio" in m:
                scores.append(max(0.0, min(100.0, m["lang_consistency_ratio"] * 100.0)))
        elif dim == "uniqueness_diversity":
            unique_ratio = m.get("unique_ratio", 1.0)
            scores.append(max(0.0, min(100.0, unique_ratio * 100.0)))
        elif dim == "instruction_quality":
            avg_judge = m.get("avg_judge_score", 1.0)
            scores.append(max(0.0, min(100.0, avg_judge * 100.0)))
        elif dim == "safety_bias":
            toxic_ratio = m.get("toxic_ratio", 0.0)
            bias_ratio = m.get("bias_ratio", 0.0)
            clean_safety = max(0.0, 1.0 - toxic_ratio - bias_ratio)
            scores.append(max(0.0, min(100.0, clean_safety * 100.0)))
        else:
            scores.append(100.0)
    return round(sum(scores) / len(scores), 2) if scores else 100.0


def compute_dqi(
    module_results: list[ModuleResult],
    *,
    has_critical_findings: bool,
    critical_cap: float = 60.0,
    custom_weights: dict[str, float] | None = None,
) -> DqiBreakdown:
    """Aggregate module results into a 0-100 DQI with partial-weight redistribution."""
    weights = custom_weights or DEFAULT_DIMENSION_WEIGHTS
    by_dim: dict[str, list[ModuleResult]] = defaultdict(list)
    for res in module_results:
        by_dim[res.dimension].append(res)

    active_dims = [dim for dim in weights if dim in by_dim]
    total_active_weight = sum(weights[d] for d in active_dims) or 1.0
    is_partial = len(active_dims) < len(weights)

    dim_items: list[dict[str, Any]] = []
    raw_total = 0.0

    for dim in active_dims:
        raw_score = _extract_dimension_score(dim, by_dim[dim])
        normalized_weight = weights[dim] / total_active_weight
        contribution = round(raw_score * normalized_weight, 2)
        raw_total += contribution
        dim_items.append(
            {
                "dimension": dim,
                "label": DIMENSION_LABELS.get(dim, dim),
                "score": raw_score,
                "weight": round(normalized_weight, 4),
                "contribution": contribution,
                "findings_count": sum(r.findings_count for r in by_dim[dim]),
            }
        )

    final_score = round(min(100.0, max(0.0, raw_total)), 2)
    capped = False
    if has_critical_findings and final_score > critical_cap:
        final_score = round(critical_cap, 2)
        capped = True

    return DqiBreakdown(
        dqi_total=final_score,
        status_label=_score_label(final_score),
        is_partial=is_partial,
        critical_capped=capped,
        critical_cap_value=critical_cap,
        dimensions=dim_items,
    )
