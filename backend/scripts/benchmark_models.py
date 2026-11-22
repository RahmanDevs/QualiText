"""Synthetic quality-module benchmark script for PII recall, dedup precision, and streaming.

NO real PII is constructed or logged. Metrics are emitted via structlog (zero print()).
"""

import argparse
import io
import json
import time
from pathlib import Path

import polars as pl

from app.core.logging import configure_logging, get_logger
from app.modules.base import ModuleConfig
from app.modules.cleaning.pii import detect_pii_spans
from app.modules.ingestion.validator import parse_and_validate_stream
from app.modules.semantic.dedup import ExactAndNearDedupModule
from app.modules.semantic.semantic_dedup import SemanticDedupModule


def run_pii_benchmark() -> dict[str, float]:
    """Measure Indonesian PII recognizer recall & precision on procedurally generated samples."""
    positives: list[tuple[str, str]] = [
        ("NIK saya tercatat 3273010101900001 untuk arsip sintetis.", "PII_NIK"),
        ("Nomor NIK: 3174-0202-0291-0002 sedang diverifikasi sistem.", "PII_NIK"),
        ("NPWP dummy 01.234.567.8-901.000 digunakan untuk format uji.", "PII_NPWP"),
        ("Silakan verifikasi NPWP: 1234567890123456 pada berkas sintetis.", "PII_NPWP"),
        ("Hubungi nomor uji 0812-3456-7890 jam kerja.", "PII_PHONE_ID"),
        ("Kontak WhatsApp sintetis +62-811-2233-4455 tersedia.", "PII_PHONE_ID"),
        ("Kirimkan konfirmasi ke uji.pengguna@contoh.id hari ini.", "PII_EMAIL"),
        ("Rekening dummy: 123456789012 untuk simulasi pembayaran.", "PII_BANK_ACCOUNT"),
        ("Nomor kartu uji 4111-2222-3333-4444 hanya untuk validasi.", "PII_BANK_ACCOUNT"),
        ("Nama: Andi Contoh terdaftar pada modul evaluasi.", "PII_PERSON"),
        ("Pesanan oleh Ibu Sari Contoh telah disetujui.", "PII_PERSON"),
        ("Alamat sintetis Jl. Merdeka Raya No. 10 Bandung terdaftar.", "PII_ADDRESS"),
    ]
    negatives: list[str] = [
        "Ibu kota Indonesia adalah Jakarta yang memiliki sejarah panjang.",
        "Laporan triwulan kedua menunjukkan kenaikan efisiensi komputasi 15 persen.",
        "Model transformer modern membutuhkan data pelatihan yang bersih dan terstruktur.",
        "Jalan menuju kesuksesan rekayasa perangkat lunak memerlukan pengujian otomatis.",
        "Sistem antrian pekerja memproses sekitar 2500 dokumen setiap jam.",
        "Panduan arsitektur mikroservis membahas keandalan basis data relasional.",
    ]

    tp = 0
    fn = 0
    for text, expected_type in positives:
        detected = {m.entity_type for m in detect_pii_spans(text)}
        if expected_type in detected:
            tp += 1
        else:
            fn += 1

    fp = 0
    tn = 0
    for text in negatives:
        if detect_pii_spans(text):
            fp += 1
        else:
            tn += 1

    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "total_positive": float(len(positives)),
        "total_negative": float(len(negatives)),
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
        "tn": float(tn),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
    }


def run_dedup_benchmark() -> dict[str, float]:
    """Measure exact/near + semantic deduplication precision and recall on synthetic pairs."""
    rows = [
        {
            "row_id": "r01",
            "text": "Cara merancang pipeline kualitas data LLM dengan Python dan Polars.",
        },
        {
            "row_id": "r02",
            "text": "Cara merancang pipeline kualitas data LLM dengan Python dan Polars.",
        },
        {
            "row_id": "r03",
            "text": "Cara merancang pipeline kualitas data LLM dgn Python dan Polars.",
        },
        {
            "row_id": "r04",
            "text": "Panduan migrasi skema Alembic untuk basis data relasional terkelola.",
        },
        {
            "row_id": "r05",
            "text": "Panduan migrasi skema Alembic untuk basis data relasional terkelola.",
        },
        {
            "row_id": "r06",
            "text": "Teknik tokenisasi subkata BPE meningkatkan cakupan kosakata multilingua.",
        },
        {
            "row_id": "r07",
            "text": "Teknik tokenisasi subkata BPE tingkatkan cakupan kosakata multilingua.",
        },
        {
            "row_id": "r08",
            "text": "Evaluasi latency inferensi model kuantisasi empat bit pada GPU lokal.",
        },
        {
            "row_id": "r09",
            "text": "Arsitektur kontrol akses berbasis peran membatasi ekspor dataset sensitif.",
        },
        {
            "row_id": "r10",
            "text": "Pembersihan artefak HTML menghilangkan tag sisa pengikisan web otomatis.",
        },
        {
            "row_id": "r11",
            "text": "Kebijakan retensi otomatis menghapus artefak kedaluwarsa sesuai kepatuhan.",
        },
        {
            "row_id": "r12",
            "text": "Pelatihan RLHF memanfaatkan pasangan chosen dan rejected yang konsisten.",
        },
    ]
    expected_dups = {"r02", "r03", "r05", "r07"}
    lazy_df = pl.DataFrame(rows).lazy()

    exact_mod = ExactAndNearDedupModule()
    _, exact_findings = exact_mod.run(lazy_df, ModuleConfig(version="benchmark"), seed=42)
    sem_mod = SemanticDedupModule()
    _, sem_findings = sem_mod.run(
        lazy_df,
        ModuleConfig(version="benchmark", params={"similarity_threshold": 0.88}),
        seed=42,
    )

    predicted_dups: set[str] = set()
    if exact_findings.height > 0:
        predicted_dups.update(str(r["row_id"]) for r in exact_findings.iter_rows(named=True))
    if sem_findings.height > 0:
        predicted_dups.update(str(r["row_id"]) for r in sem_findings.iter_rows(named=True))

    tp = len(predicted_dups & expected_dups)
    fp = len(predicted_dups - expected_dups)
    fn = len(expected_dups - predicted_dups)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "total_rows": float(len(rows)),
        "expected_duplicates": float(len(expected_dups)),
        "predicted_duplicates": float(len(predicted_dups)),
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
    }


def run_streaming_benchmark(num_rows: int = 5000) -> dict[str, float]:
    """Measure streaming JSONL validation throughput across synthetic SFT records."""
    sample_line = json.dumps(
        {
            "prompt": "Jelaskan manfaat normalisasi korpus teks sebelum pelatihan model bahasa.",
            "response": (
                "Normalisasi konsistensi huruf, tanda baca, dan penghapusan duplikat "
                "memperbaiki konvergensi pelatihan."
            ),
        },
        ensure_ascii=False,
    )
    payload = ("\n".join(sample_line for _ in range(num_rows)) + "\n").encode("utf-8")
    stream = io.BytesIO(payload)

    start = time.monotonic()
    out = parse_and_validate_stream(stream, source_format="jsonl", dataset_type="sft")
    elapsed = max(0.0001, time.monotonic() - start)
    return {
        "rows_processed": float(out.total_rows),
        "valid_rows": float(out.valid_rows),
        "payload_bytes": float(len(payload)),
        "elapsed_seconds": round(elapsed, 4),
        "rows_per_second": round(out.total_rows / elapsed, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="QualiText synthetic quality benchmark suite")
    parser.add_argument("--rows", type=int, default=5000, help="Streaming JSONL row count")
    parser.add_argument("--json-out", type=str, default="", help="Optional JSON metrics path")
    args = parser.parse_args()

    configure_logging(log_level="INFO", log_format="json")
    logger = get_logger("qualitext.benchmark")

    pii = run_pii_benchmark()
    dedup = run_dedup_benchmark()
    streaming = run_streaming_benchmark(num_rows=args.rows)

    summary = {"pii": pii, "dedup": dedup, "streaming": streaming}
    logger.info("benchmark_pii_completed", **pii)
    logger.info("benchmark_dedup_completed", **dedup)
    logger.info("benchmark_streaming_completed", **streaming)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
