# Benchmark Model & Throughput Pipeline - QualiText

Dokumen ini mencatat hasil benchmark terukur modul kualitas inti dan throughput ingesti streaming pada **19 November 2026** menggunakan korpus uji sintetis berlabel di [benchmark_models.py](../backend/scripts/benchmark_models.py). Seluruh data uji bersifat sintetis tanpa PII asli.

## Metodologi & Lingkungan Uji

- **Perintah Eksekusi:** `UV_OFFLINE=1 UV_NO_SYNC=1 uv --directory backend run python -m scripts.benchmark_models`
- **Lingkungan:** Sandbox lokal offline (Python 3.12, Polars 1.44.2, backend embedding deterministik `local_hash` + SimHash LSH).
- **Batasan:** Pengujian pada tabel ini mengukur akurasi pola kalibrasi, logika kanonik, dan kecepatan parser streaming line-by-line pada korpus sintetis. Pengukuran beban skala klaster 100.000-5.000.000 baris, `sentence-transformers`, `faiss`, `pgvector`, dan PostgreSQL partisi HASH tetap memerlukan eksekusi di lingkungan target non-sandbox.

## Ringkasan Hasil Aktual

| Kategori Benchmark | Metrik Target (`CLAUDE.md`) | Hasil Aktual | Rincian Sampel |
|---|---|---|---|
| **Deteksi PII Indonesia (`B1`)** | Recall >= `0.95` | **Recall `1.0000`**, Precision `1.0000`, F1 `1.0000` | 12 positif sintetis (`NIK` polos/spasi/strip, `NPWP` titik/16-digit, telepon `08`/`+62`, email, rekening/kartu, nama berlabel/kehormatan, alamat) + 6 kalimat non-PII (`tp=12`, `fp=0`, `fn=0`, `tn=6`). |
| **Deduplikasi Eksak & Semantik (`C1`, `C2`)** | Precision >= `0.90` | **Precision `1.0000`**, Recall `1.0000` | 12 baris sintetis (4 duplikat eksak & parafrasa lebih pendek terhadap kanonik, 8 topik unik berbeda; `tp=4`, `fp=0`, `fn=0`). |
| **Streaming Ingesti JSONL (`A1`, `PERF-01`)** | Parsing baris-demi-baris tanpa buffer utuh | **`49,698.88` baris/detik** (`0.1006` detik) | `5.000` baris SFT valid (`1.025.000` byte payload), `5.000/5.000` tervalidasi via `parse_and_validate_stream`. |
