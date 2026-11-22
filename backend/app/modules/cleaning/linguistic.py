"""Module B7: Linguistic quality (perplexity, gibberish/OCR noise, valid-word vs OOV ratio)."""

import importlib
import importlib.util
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import polars as pl

from app.modules.base import ModuleConfig, ModuleResult, RowFinding
from app.modules.cleaning.lexicon import (
    EN_BASE_VOCAB,
    ID_BASE_VOCAB,
    REFERENCE_TEXTS,
    TECH_COMMON_VOCAB,
    is_known_word,
)

_MASKED_TOKEN_RE = re.compile(
    r"\[(?:NIK|NPWP|TELEPON|EMAIL|REKENING|KARTU|NAMA|ALAMAT|PII_[A-Z_]+:[0-9a-f]+)\]"
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
_WS_RE = re.compile(r"\s+")
_WHITELIST_ALNUM_RE = re.compile(
    r"^(?:v\d+|p[0-2]|[23]d|s3|e5|gpt\d+|llama\d+|utf\d+|sha\d+|base64|b2[bc]|"
    r"http\d|oauth\d|saml\d|tls\d|pdf\d|iso\d+)$",
    re.IGNORECASE,
)
_CONSONANT_RUN_RE = re.compile(r"[bcdfghjklmnpqrstvwxyz]{5,}", re.IGNORECASE)
_REPEAT_CHAR_RE = re.compile(r"(.)\1{3,}", re.IGNORECASE)
_VOWEL_RE = re.compile(r"[aeiouy]", re.IGNORECASE)


class CharNgramLanguageModel:
    """Deterministic interpolated character trigram/bigram/unigram language model."""

    def __init__(self, corpus_texts: tuple[str, ...]) -> None:
        self._tri_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self._tri_totals: Counter[tuple[str, str]] = Counter()
        self._bi_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._bi_totals: Counter[str] = Counter()
        self._uni_counts: Counter[str] = Counter()
        self._total_chars = 0
        self._alphabet = frozenset("abcdefghijklmnopqrstuvwxyz ")

        for text in corpus_texts:
            self._observe(text)
        self._vocab_size = max(1, len(self._alphabet) + 2)

    def _encode_seq(self, text: str) -> list[str]:
        norm = _WS_RE.sub(" ", text.lower().strip())
        seq = ["^", "^"]
        for ch in norm:
            if ch in self._alphabet:
                seq.append(ch)
            elif ch in ".,!?;:-'\"()[]/":
                seq.append(" ")
            else:
                seq.append("<unk>")
        seq.append("$")
        return seq

    def _observe(self, text: str) -> None:
        seq = self._encode_seq(text)
        for i in range(2, len(seq)):
            c2, c1, cur = seq[i - 2], seq[i - 1], seq[i]
            if cur == "<unk>":
                continue
            self._tri_counts[(c2, c1)][cur] += 1
            self._tri_totals[(c2, c1)] += 1
            self._bi_counts[c1][cur] += 1
            self._bi_totals[c1] += 1
            self._uni_counts[cur] += 1
            self._total_chars += 1

    def char_prob(self, c2: str, c1: str, cur: str) -> float:
        """Compute smoothed interpolated character probability."""
        if cur == "<unk>":
            return 0.002
        tri_tot = self._tri_totals.get((c2, c1), 0)
        p3 = (self._tri_counts[(c2, c1)].get(cur, 0) / tri_tot) if tri_tot > 0 else 0.0

        bi_tot = self._bi_totals.get(c1, 0)
        p2 = (self._bi_counts[c1].get(cur, 0) / bi_tot) if bi_tot > 0 else 0.0

        p1 = (self._uni_counts.get(cur, 0) / self._total_chars) if self._total_chars > 0 else 0.0
        p0 = 1.0 / self._vocab_size
        return max(1e-6, 0.68 * p3 + 0.25 * p2 + 0.06 * p1 + 0.01 * p0)

    def perplexity(self, text: str) -> float:
        """Compute cross-entropy perplexity `exp(-1/N * sum log P(c_i | ctx))`."""
        stripped = _WS_RE.sub(" ", text.strip())
        if not stripped:
            return 1.0
        seq = self._encode_seq(stripped)
        n = max(1, len(seq) - 2)
        log_sum = 0.0
        for i in range(2, len(seq)):
            p = self.char_prob(seq[i - 2], seq[i - 1], seq[i])
            log_sum += math.log(p)
        return round(min(10000.0, math.exp(-log_sum / n)), 4)


@lru_cache(maxsize=1)
def get_default_char_lm() -> CharNgramLanguageModel:
    """Build and cache the deterministic bilingual character LM once."""
    vocab_samples = (
        " ".join(sorted(ID_BASE_VOCAB)),
        " ".join(sorted(EN_BASE_VOCAB)),
        " ".join(sorted(TECH_COMMON_VOCAB)),
    )
    return CharNgramLanguageModel(REFERENCE_TEXTS + vocab_samples)


def compute_text_perplexity(
    text: str,
    *,
    backend: str = "auto",
    kenlm_model_path: str = "",
    hf_model_name: str = "",
) -> tuple[float, str]:
    """Compute perplexity via optional `kenlm` / `hf` hooks or local character n-gram LM."""
    clean_for_lm = _WS_RE.sub(" ", _MASKED_TOKEN_RE.sub(" ", text)).strip()
    if not clean_for_lm:
        return 1.0, "local_char_lm"

    if (
        backend in {"auto", "kenlm"}
        and kenlm_model_path
        and importlib.util.find_spec("kenlm") is not None
    ):
        try:
            kenlm_mod = importlib.import_module("kenlm")
            model = kenlm_mod.Model(kenlm_model_path)
            ppl = float(model.perplexity(clean_for_lm))
            return round(min(10000.0, max(1.0, ppl)), 4), "kenlm"
        except Exception:
            pass

    if (
        backend in {"auto", "hf"}
        and hf_model_name
        and importlib.util.find_spec("transformers") is not None
        and importlib.util.find_spec("torch") is not None
    ):
        try:
            tr_mod = importlib.import_module("transformers")
            torch_mod = importlib.import_module("torch")
            tok = tr_mod.AutoTokenizer.from_pretrained(hf_model_name)
            mdl = tr_mod.AutoModelForCausalLM.from_pretrained(hf_model_name)
            enc = tok(clean_for_lm[:1024], return_tensors="pt")
            with torch_mod.no_grad():
                out = mdl(**enc, labels=enc["input_ids"])
                ppl = float(torch_mod.exp(out.loss).item())
            return round(min(10000.0, max(1.0, ppl)), 4), "hf"
        except Exception:
            pass

    return get_default_char_lm().perplexity(clean_for_lm), "local_char_lm"


@dataclass(frozen=True)
class LinguisticRowStats:
    """Linguistic quality summary for a single text row."""

    total_words: int
    valid_word_count: int
    oov_word_count: int
    valid_word_ratio: float
    oov_ratio: float
    ocr_noise_ratio: float
    perplexity: float
    perplexity_backend: str


def _is_ocr_or_gibberish_token(tok: str, custom_vocab: frozenset[str]) -> bool:
    low = tok.lower()
    if low in custom_vocab or low in TECH_COMMON_VOCAB:
        return False
    has_alpha = any(c.isalpha() for c in low)
    has_digit = any(c.isdigit() for c in low)
    if has_alpha and has_digit and not _WHITELIST_ALNUM_RE.match(low):
        return True
    if _REPEAT_CHAR_RE.search(low):
        return True
    if is_known_word(low, custom_vocab):
        return False
    if _CONSONANT_RUN_RE.search(low):
        return True
    return bool(len(low) >= 4 and low.isalpha() and not _VOWEL_RE.search(low))


def analyze_linguistic_quality(
    text: str,
    *,
    custom_vocab: frozenset[str] = frozenset(),
    perplexity_backend: str = "auto",
    kenlm_model_path: str = "",
    hf_model_name: str = "",
) -> LinguisticRowStats:
    """Compute perplexity, valid/OOV vocabulary ratio, and OCR/gibberish noise ratio."""
    scrubbed = _URL_RE.sub(" ", _MASKED_TOKEN_RE.sub(" ", text))
    raw_tokens = _WORD_RE.findall(scrubbed)
    words = [tok for tok in raw_tokens if len(tok) > 1 and not tok.isdigit()]

    total_words = len(words)
    valid_count = 0
    noise_count = 0
    for tok in words:
        if _is_ocr_or_gibberish_token(tok, custom_vocab):
            noise_count += 1
        elif is_known_word(tok, custom_vocab):
            valid_count += 1

    oov_count = max(0, total_words - valid_count)
    valid_ratio = round(valid_count / total_words, 4) if total_words > 0 else 1.0
    oov_ratio = round(oov_count / total_words, 4) if total_words > 0 else 0.0
    noise_ratio = round(noise_count / total_words, 4) if total_words > 0 else 0.0

    ppl, used_backend = compute_text_perplexity(
        text,
        backend=perplexity_backend,
        kenlm_model_path=kenlm_model_path,
        hf_model_name=hf_model_name,
    )
    return LinguisticRowStats(
        total_words=total_words,
        valid_word_count=valid_count,
        oov_word_count=oov_count,
        valid_word_ratio=valid_ratio,
        oov_ratio=oov_ratio,
        ocr_noise_ratio=noise_ratio,
        perplexity=ppl,
        perplexity_backend=used_backend,
    )


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(math.ceil((pct / 100.0) * len(sorted_vals))) - 1))
    return round(sorted_vals[idx], 4)


class LinguisticQualityModule:
    """Evaluates text perplexity, valid-word vs OOV ratio, and gibberish/OCR noise."""

    name = "linguistic_quality"
    dimension = "cleanliness_privacy"
    version = "0.1.0"

    def run(
        self,
        data: pl.LazyFrame,
        config: ModuleConfig,
        seed: int,
    ) -> tuple[ModuleResult, pl.DataFrame]:
        del seed
        max_perplexity = float(config.params.get("max_perplexity", 35.0))
        max_oov_ratio = float(config.params.get("max_oov_ratio", 0.40))
        max_ocr_noise_ratio = float(config.params.get("max_ocr_noise_ratio", 0.15))
        min_words_for_oov = int(config.params.get("min_words_for_oov", 4))
        ppl_backend = str(config.params.get("perplexity_backend", "auto"))
        kenlm_path = str(config.params.get("kenlm_model_path", ""))
        hf_model = str(config.params.get("hf_model_name", ""))
        raw_vocab = config.params.get("custom_vocab", [])
        custom_vocab = (
            frozenset(str(w).strip().lower() for w in raw_vocab if str(w).strip())
            if isinstance(raw_vocab, list)
            else frozenset()
        )

        total_rows = 0
        flagged_rows = 0
        high_ppl_rows = 0
        high_oov_rows = 0
        gibberish_rows = 0
        perplexities: list[float] = []
        total_words_all = 0
        valid_words_all = 0
        oov_words_all = 0
        findings: list[dict[str, Any]] = []

        for batch in data.select(["row_id", "text"]).collect_batches(chunk_size=10_000):
            total_rows += batch.height
            for row in batch.iter_rows(named=True):
                rid = str(row["row_id"])
                text = str(row.get("text") or "")
                stats = analyze_linguistic_quality(
                    text,
                    custom_vocab=custom_vocab,
                    perplexity_backend=ppl_backend,
                    kenlm_model_path=kenlm_path,
                    hf_model_name=hf_model,
                )
                perplexities.append(stats.perplexity)
                total_words_all += stats.total_words
                valid_words_all += stats.valid_word_count
                oov_words_all += stats.oov_word_count

                row_flagged = False
                if stats.perplexity > max_perplexity:
                    row_flagged = True
                    high_ppl_rows += 1
                    ppl_score = max(0.0, min(1.0, stats.perplexity / max(1.0, max_perplexity * 2)))
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="HIGH_PERPLEXITY",
                            severity="warning",
                            score=ppl_score,
                            detail={
                                "perplexity": stats.perplexity,
                                "max_perplexity": max_perplexity,
                                "backend": stats.perplexity_backend,
                            },
                        ).model_dump()
                    )

                if stats.total_words >= min_words_for_oov and stats.oov_ratio > max_oov_ratio:
                    row_flagged = True
                    high_oov_rows += 1
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="HIGH_OOV_RATIO",
                            severity="warning",
                            score=max(0.0, min(1.0, stats.oov_ratio)),
                            detail={
                                "oov_ratio": stats.oov_ratio,
                                "valid_word_ratio": stats.valid_word_ratio,
                                "total_words": stats.total_words,
                                "oov_word_count": stats.oov_word_count,
                            },
                        ).model_dump()
                    )

                if stats.total_words > 0 and stats.ocr_noise_ratio > max_ocr_noise_ratio:
                    row_flagged = True
                    gibberish_rows += 1
                    findings.append(
                        RowFinding(
                            row_id=rid,
                            module=self.name,
                            code="OCR_GIBBERISH_NOISE",
                            severity="warning",
                            score=max(0.0, min(1.0, stats.ocr_noise_ratio)),
                            detail={
                                "ocr_noise_ratio": stats.ocr_noise_ratio,
                                "total_words": stats.total_words,
                            },
                        ).model_dump()
                    )

                if row_flagged:
                    flagged_rows += 1

        sorted_ppl = sorted(perplexities)
        avg_ppl = round(sum(perplexities) / len(perplexities), 4) if perplexities else 0.0
        p95_ppl = _percentile(sorted_ppl, 95.0)
        corpus_valid_ratio = (
            round(valid_words_all / total_words_all, 4) if total_words_all > 0 else 1.0
        )
        corpus_oov_ratio = round(oov_words_all / total_words_all, 4) if total_words_all > 0 else 0.0
        quality_ratio = round(max(0.0, 1.0 - (flagged_rows / total_rows)), 4) if total_rows else 1.0

        result = ModuleResult(
            module=self.name,
            dimension=self.dimension,
            metrics={
                "total_rows": float(total_rows),
                "avg_perplexity": avg_ppl,
                "p95_perplexity": p95_ppl,
                "valid_word_ratio": corpus_valid_ratio,
                "oov_ratio": corpus_oov_ratio,
                "high_perplexity_rows": float(high_ppl_rows),
                "high_oov_rows": float(high_oov_rows),
                "gibberish_rows": float(gibberish_rows),
                "linguistic_quality_ratio": quality_ratio,
            },
            findings_count=len(findings),
        )
        findings_df = pl.DataFrame(findings) if findings else pl.DataFrame()
        return result, findings_df
