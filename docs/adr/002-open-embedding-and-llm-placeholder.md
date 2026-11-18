# ADR-002: Pemilihan Model Embedding Open-Source & Placeholder LLM-as-a-Judge

- **Status:** Accepted (Fase 0 — Keputusan Terbuka 2.13)
- **Tanggal:** 2026-11-18

## Keputusan
1. **Embedding Multibahasa (Bahasa Indonesia + Inggris):**
   - Kandidat utama: `intfloat/multilingual-e5-small` (ringan, CPU-friendly) dan `paraphrase-multilingual-MiniLM-L12-v2`.
   - Evaluasi benchmark recall/precision pada sampel dataset Bahasa Indonesia dijadwalkan pada awal Fase 1/2 sebelum mengunci versi di `config_snapshot`.
2. **LLM-as-a-Judge:**
   - Sesuai prinsip *privacy-first* (`CLAUDE.md` 0.1 #3), `ALLOW_EXTERNAL_LLM=false` secara default.
   - Disediakan konfigurasi **placeholder** kompatibel OpenAI API (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) agar admin dapat mengarahkan ke API gratis maupun server lokal (Ollama/vLLM) secara eksplisit.
3. **Vector Store:**
   - MVP memakai `pgvector` di PostgreSQL 16 (atau FAISS lokal untuk *fast-dev*); evaluasi Qdrant terpisah bila skala melebihi jutaan vektor.
4. **Retensi Default:** Konfigurasi retensi disiapkan per workspace pada Fase 1/2.
