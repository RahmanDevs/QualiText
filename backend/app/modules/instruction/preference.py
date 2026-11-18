"""Module D4: Preference data (chosen/rejected) consistency check for RLHF/DPO."""

import json

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding


class PreferenceConsistencyModule:
    """Checks chosen/rejected preference pairs for emptiness or identical responses."""

    name = "preference_consistency"
    dimension = "instruction_quality"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del config, seed
        df = data.select(["row_id", "prompt", "response", "messages_json"]).collect()
        total_rows = df.height
        inconsistent = 0
        findings: list[dict[str, object]] = []

        for row in df.iter_rows(named=True):
            rid = str(row["row_id"])
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

        consistency_ratio = max(0.0, 1.0 - (inconsistent / total_rows)) if total_rows else 1.0
        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "inconsistent_pairs": float(inconsistent),
                "avg_judge_score": round(consistency_ratio, 4),
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
