# Data Quality Index (DQI) — Spesifikasi Rumus

Mengacu pada `CLAUDE.md` Bagian 2.7, DQI adalah skor kesehatan dataset pada rentang **0–100**.

## Bobot Default Dimensi

| Dimensi | Bobot Default | Keterangan |
|---|---:|---|
| Validitas Struktur | 15% | % baris valid skema, kelengkapan pasangan, kepatuhan format |
| Kebersihan & Privasi | 25% | PII per 1.000 baris, % artefak, konsistensi bahasa |
| Keunikan & Keberagaman | 20% | Rasio duplikat eksak/near/semantik, entropi cluster, n-gram |
| Kualitas Instruksi–Respons | 25% | Skor LLM-judge, kompleksitas, kepatuhan format |
| Keamanan & Bias | 15% | % toksisitas, indeks bias, risiko halusinasi |

## Aturan Penilaian
1. **Skor Parsial:** Dimensi yang modulnya tidak dijalankan dikecualikan dan bobot dimensi aktif dinormalisasi ulang ke 100%; UI wajib menandai skor sebagai **parsial**.
2. **Penalti Kritis:** Temuan berseverity `critical` (mis. PII mentah belum dimasking) membatasi DQI maksimum (*cap* default **60**, dapat dikonfigurasi Governance).
3. **Label Status:**
   - `90–100`: **Siap Latih** (Hijau)
   - `75–89`: **Baik** (Biru)
   - `60–74`: **Perlu Perbaikan** (Oranye)
   - `< 60`: **Berisiko** (Merah)
