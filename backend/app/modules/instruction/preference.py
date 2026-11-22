"""Module D4: Preference data (chosen/rejected) consistency check for RLHF/DPO."""

import json
import re

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _token_jaccard(a: str, b: str) -> float:
    ta = {w.lower() for w in _WORD_RE.findall(a)}
    tb = {w.lower() for w in _WORD_RE.findall(b)}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class PreferenceConsistencyModule:
    """Checks chosen/rejected preference pairs for emptiness, identity, overlap, or reversal."""

    name = "preference_consistency"
    dimension = "instruction_quality"
    version = "0.2.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        near_dup_threshold = float(config.params.get("near_dup_threshold", 0.90))
        schema_cols = set(data.collect_schema().names())
        cols = [
            c
            for c in ("row_id", "prompt", "response", "chosen", "rejected", "messages_json")
            if c in schema_cols
        ]
        df = data.select(cols).collect()
        total_rows = df.height
        inconsistent = 0
        length_anomalies = 0
        findings: list[dict[str, object]] = []

        rows = list(df.iter_rows(named=True))
        has_any_explicit_pref = any(
            bool(str(r.get("chosen") or "").strip()) or bool(str(r.get("rejected") or "").strip())
            for r in rows
        )

        for row in rows:
            rid = str(row["row_id"])
            explicit_chosen = str(row.get("chosen") or "").strip()
            explicit_rejected = str(row.get("rejected") or "").strip()

            if explicit_chosen or explicit_rejected or has_any_explicit_pref:
                chosen = explicit_chosen
                rejected = explicit_rejected
            else:
                chosen = str(row.get("prompt") or "").strip()
                rejected = str(row.get("response") or "").strip()
                raw_msgs = str(row.get("messages_json") or "").strip()
                if raw_msgs:
                    try:
                        parsed = json.loads(raw_msgs)
                        if isinstance(parsed, dict):
                            chosen = str(parsed.get("chosen", chosen)).strip()
                            rejected = str(parsed.get("rejected", rejected)).strip()
                    except json.JSONDecodeError:
                        pass

            if not chosen or not rejected:
                inconsistent += 1
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="PREF_PAIR_INCOMPLETE",
                        severity="warning",
                        detail={"reason": "empty_chosen_or_rejected"},
                    ).model_dump()
                )
            elif chosen.lower() == rejected.lower():
                inconsistent += 1
                findings.append(
                    RowFinding(
                        row_id=rid,
                        module=self.name,
                        code="PREF_PAIR_IDENTICAL",
                        severity="warning",
                        detail={"reason": "chosen_equals_rejected"},
                    ).model_dump()
                )
            else:
                jaccard = _token_jaccard(chosen, rejected)
                if jaccard >= near_dup_threshold and len(chosen) > 20:
                    inconsistent += 1
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="PREF_PAIR_NEAR_DUPLICATE",
                            severity="info",
                            score=round(jaccard, 4),
                            detail={"jaccard": round(jaccard, 4)},
                        ).model_dump()
                    )
                elif len(chosen) < 24 and len(rejected) >= 3 * max(1, len(chosen)):
                    length_anomalies += 1
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="PREF_PAIR_LENGTH_ANOMALY",
                            severity="info",
                            detail={
                                "chosen_chars": len(chosen),
                                "rejected_chars": len(rejected),
                            },
                        ).model_dump()
                    )

        penalty_rows = inconsistent + (0.5 * length_anomalies)
        consistency_ratio = max(0.0, 1.0 - (penalty_rows / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "inconsistent_pairs": float(inconsistent),
                "length_anomalies": float(length_anomalies),
                "preference_consistency_ratio": round(consistency_ratio, 4),
                "avg_judge_score": round(consistency_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
