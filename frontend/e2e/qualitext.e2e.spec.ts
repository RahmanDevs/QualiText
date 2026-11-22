import fs from 'node:fs/promises';
import { test, expect, type Page } from '@playwright/test';

async function loginAs(page: Page, email: string, password: string) {
  await page.goto('/');
  await expect(page.locator('#email')).toBeVisible();
  await page.fill('#email', email);
  await page.fill('#password', password);
  await page.getByRole('button', { name: /^(Masuk|Sign In)$/i }).click();
  await expect(page.getByRole('heading', { name: /Ringkasan Workspace|Workspace Overview/i })).toBeVisible();
}

async function logout(page: Page) {
  await page.getByRole('button', { name: /^(Keluar|Sign Out)$/i }).click();
  await expect(page.locator('#email')).toBeVisible();
}

function buildVectorTablePdf(text: string): Buffer {
  const escaped = text.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
  const rects = Array.from({ length: 8 }, (_, i) => `${50 + i * 60} 650 55 20 re S`).join('\n');
  const stream = `${rects}\nBT /F1 11 Tf 55 655 Td (${escaped}) Tj ET`;
  const objects = [
    '1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n',
    '2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n',
    '3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n',
    `4 0 obj << /Length ${Buffer.byteLength(stream, 'latin1')} >> stream\n${stream}\nendstream endobj\n`,
    '5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n',
  ];
  const header = '%PDF-1.4\n';
  const offsets: number[] = [];
  let pos = Buffer.byteLength(header, 'latin1');
  for (const obj of objects) {
    offsets.push(pos);
    pos += Buffer.byteLength(obj, 'latin1');
  }
  const xrefPos = pos;
  const xrefLines = [`xref\n0 ${objects.length + 1}\n`, '0000000000 65535 f \n'];
  for (const off of offsets) {
    xrefLines.push(`${String(off).padStart(10, '0')} 00000 n \n`);
  }
  const trailer = `trailer << /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefPos}\n%%EOF\n`;
  return Buffer.from(header + objects.join('') + xrefLines.join('') + trailer, 'latin1');
}

test.describe.serial('QualiText Full-Stack E2E Feature Suite', () => {
  test('1. Auth guards, i18n, dark theme, sidebar collapse, and Command Palette', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /Masuk ke QualiText/i })).toBeVisible();

    // Wrong credentials show error alert
    await page.fill('#email', 'admin@qualitext.id');
    await page.fill('#password', 'WrongPassword999!');
    await page.getByRole('button', { name: /^Masuk$/i }).click();
    await expect(page.getByRole('alert')).toContainText(/Email atau kata sandi salah/i);

    // Valid Admin login
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');

    // Toggle language id -> en -> id
    await page.getByRole('button', { name: /Bahasa/i }).click();
    await expect(page.getByRole('heading', { name: 'Workspace Overview' })).toBeVisible();
    await page.getByRole('button', { name: /Language/i }).click();
    await expect(page.getByRole('heading', { name: 'Ringkasan Workspace' })).toBeVisible();

    // Toggle dark / light mode
    await page.getByRole('button', { name: /Mode Gelap/i }).click();
    await expect(page.locator('html')).toHaveClass(/dark/);
    await page.getByRole('button', { name: /Mode Terang/i }).click();
    await expect(page.locator('html')).not.toHaveClass(/dark/);

    // Collapse & expand sidebar
    const sidebar = page.getByLabel('Navigasi Utama');
    await expect(sidebar).toHaveClass(/w-\[248px\]/);
    await page.getByRole('button', { name: 'Toggle sidebar' }).click();
    await expect(sidebar).toHaveClass(/w-16/);
    await page.getByRole('button', { name: 'Toggle sidebar' }).click();
    await expect(sidebar).toHaveClass(/w-\[248px\]/);

    // Command palette Ctrl+K navigation + Escape close
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: /Palet Perintah Cepat/i });
    await expect(palette).toBeVisible();
    await palette.getByPlaceholder(/Ketik nama halaman atau aksi/i).fill('Kebijakan');
    await palette.getByRole('button', { name: /Kebijakan/i }).click();
    await expect(page).toHaveURL(/\/policies$/);
    await expect(page.getByRole('heading', { name: /Kebijakan Kualitas/i })).toBeVisible();

    await page.keyboard.press('Control+k');
    await expect(page.getByRole('dialog', { name: /Palet Perintah Cepat/i })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: /Palet Perintah Cepat/i })).toBeHidden();
  });

  test('2. Workspace Overview KPIs and attention list navigation', async ({ page }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');

    await expect(page.getByText('Total Dataset')).toBeVisible();
    await expect(page.getByText('Rata-rata DQI')).toBeVisible();
    await expect(page.getByText('PII Kritis Terbuka')).toBeVisible();
    await expect(page.getByText('Run Berjalan')).toBeVisible();

    // Seeded dataset appears in attention list and opens Explorer
    await expect(page.getByText('Korpus Instruksi Bahasa Indonesia (Demo)')).toBeVisible();
    await page.getByRole('button', { name: /^Buka$/i }).first().click();
    await expect(page).toHaveURL(/\/explorer\?versionId=/);
  });

  test('3. Datasets CRUD, magic-bytes guard, column mapping, error downloads, connector SSRF, and delete', async ({
    page,
  }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await expect(page).toHaveURL(/\/datasets$/);

    // Create SFT dataset with retention_days=30
    await page.fill('#ds-name', 'E2E Audit Corpus');
    await page.selectOption('#ds-type', 'sft');
    await page.fill('#ds-retention', '30');
    await page.getByRole('button', { name: /Buat Dataset/i }).click();

    await expect(page.getByText('E2E Audit Corpus')).toBeVisible();
    await expect(page.getByText('Retensi 30h')).toBeVisible();

    // Target card for E2E Audit Corpus
    const corpusCard = page.locator('div.rounded-card', { has: page.getByRole('heading', { name: 'E2E Audit Corpus', exact: true }) });

    // Spoofed PDF bytes uploaded as .jsonl -> rejected cleanly without orphan version
    await corpusCard.locator('input[type="file"]').setInputFiles({
      name: 'spoof.jsonl',
      mimeType: 'application/jsonl',
      buffer: Buffer.from('%PDF-1.7\nspoofed binary payload'),
    });
    await expect(page.getByText(/data biner|gagal mengunggah/i)).toBeVisible();
    await expect(corpusCard.getByText('Belum ada berkas yang diunggah.')).toBeVisible();

    // Upload real JSONL using custom columns (instruction/output), PII, HTML, broken wrap, near-dup & malformed line
    const jsonlRows = [
      JSON.stringify({
        instruction: 'Data <p>NIK saya 3273010101900001</p> email uji@contoh.id kartu 4111-2222-3333-4444',
        output: 'Segera diproses dengan aman.',
      }),
      JSON.stringify({
        instruction: 'Cara merancang pipeline kualitas data LLM dengan Python dan Polars.',
        output: 'Gunakan Polars LazyFrame dan format kolumnar Parquet.',
      }),
      JSON.stringify({
        instruction: 'Cara merancang pipeline kualitas data LLM dgn Python dan Polars.',
        output: 'Gunakan Polars LazyFrame untuk streaming.',
      }),
      JSON.stringify({
        instruction: 'Kata terpo-\ntong saat parsing dokumen PDF.',
        output: 'Diperbaiki memakai heuristik lokal.',
      }),
      '{malformed-json-row',
    ].join('\n');

    await corpusCard.locator('input[type="file"]').setInputFiles({
      name: 'e2e_corpus.jsonl',
      mimeType: 'application/jsonl',
      buffer: Buffer.from(`${jsonlRows}\n`, 'utf-8'),
    });
    await expect(page.getByText('Dataset berhasil diunggah dan divalidasi.')).toBeVisible();
    await expect(corpusCard.getByText(/5 baris/i)).toBeVisible();

    // Column Mapping: instruction -> prompt, output -> response
    await corpusCard.getByRole('button', { name: /Pemetaan Kolom/i }).click();
    await expect(page.getByRole('heading', { name: /Pemetaan Kolom Skema/i })).toBeVisible();
    await page.getByLabel('instruction').selectOption('prompt');
    await page.getByLabel('output').selectOption('response');
    await page.getByRole('button', { name: /Simpan Pemetaan & Validasi Ulang/i }).click();
    await expect(page.getByText('Pemetaan kolom berhasil diperbarui.')).toBeVisible();
    await expect(corpusCard.getByText(/5 baris · 1 tidak valid/i)).toBeVisible();

    // Validation Errors inspection + JSON & CSV downloads
    await corpusCard.getByRole('button', { name: /Lihat Error/i }).click();
    await expect(page.getByText('INVALID_JSON')).toBeVisible();

    const [jsonErrDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /Unduh Error JSON/i }).click(),
    ]);
    const jsonErrPath = await jsonErrDl.path();
    const jsonErrContent = await fs.readFile(jsonErrPath!, 'utf-8');
    expect(JSON.parse(jsonErrContent).errors[0].error_type).toBe('INVALID_JSON');

    const [csvErrDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /Unduh Error CSV/i }).click(),
    ]);
    const csvErrPath = await csvErrDl.path();
    const csvErrContent = await fs.readFile(csvErrPath!, 'utf-8');
    expect(csvErrContent).toContain('line_number,error_type,message');
    expect(csvErrContent).toContain('INVALID_JSON');

    // Connector Import modal SSRF rejection
    await corpusCard.getByRole('button', { name: /Impor Konektor/i }).click();
    const connDialog = page.getByRole('dialog', { name: /Impor Versi via Konektor/i });
    await expect(connDialog).toBeVisible();
    await connDialog.locator('#conn-type').selectOption('sharepoint');
    await connDialog.locator('#conn-uri').fill('https://127.0.0.1/secret.jsonl');
    await connDialog.getByRole('button', { name: /Tarik & Impor Data/i }).click();
    await expect(connDialog.getByRole('alert')).toBeVisible();
    await connDialog.getByRole('button', { name: /Batal/i }).click();
    await expect(connDialog).toBeHidden();

    // Two-step Dataset Delete (UU PDP)
    await page.fill('#ds-name', 'Temp Delete DS');
    await page.getByRole('button', { name: /Buat Dataset/i }).click();
    await expect(page.getByRole('heading', { name: 'Temp Delete DS', exact: true })).toBeVisible();

    const tempCard = page.locator('div.rounded-card', { has: page.getByRole('heading', { name: 'Temp Delete DS', exact: true }) });
    await tempCard.getByRole('button', { name: /^Hapus$/i }).click();
    await tempCard.getByRole('button', { name: /Konfirmasi Hapus/i }).click();
    await expect(page.getByRole('heading', { name: 'Temp Delete DS', exact: true })).toBeHidden();
  });

  test('4. Quality Run, ScoreCard, Histogram, 2D Clusters, HTML/PDF reports, and DQI Trend', async ({
    page,
  }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Dataset$/i }).click();

    const corpusCard = page.locator('div.rounded-card', { has: page.getByRole('heading', { name: 'E2E Audit Corpus', exact: true }) });
    await corpusCard.getByRole('button', { name: /Jalankan Penilaian/i }).click();

    await expect(page).toHaveURL(/\/runs\?runId=/);
    await expect(page.getByText('Data Quality Index (DQI)')).toBeVisible();
    await expect(page.getByText(/Dibatasi Penalti Kritis/i)).toBeVisible();
    await expect(page.getByText('Skor per Dimensi DQI')).toBeVisible();
    await expect(page.getByText('Distribusi Panjang Token (A5)')).toBeVisible();

    // Toggle 2D Topic Clusters and verify PII is masked inside cluster preview
    await page.getByRole('button', { name: /Visualisasi Klaster Topik 2D/i }).click();
    const clusterSvg = page.getByRole('img', { name: /Sebaran Klaster Topik 2D/i });
    await expect(clusterSvg).toBeVisible();
    await clusterSvg.locator('circle').first().click();
    await expect(page.locator('body')).not.toContainText('3273010101900001');

    // Download HTML & PDF reports
    const [htmlDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /Unduh Laporan HTML/i }).click(),
    ]);
    const htmlContent = await fs.readFile((await htmlDl.path())!, 'utf-8');
    expect(htmlContent).toContain('Laporan Kualitas Dataset - QualiText');

    const [pdfDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /Unduh Laporan PDF/i }).click(),
    ]);
    const pdfBuf = await fs.readFile((await pdfDl.path())!);
    expect(pdfBuf.subarray(0, 8).toString('ascii')).toContain('%PDF-1.4');

    // Reports / Runs overview without runId shows DQI Trend chart
    await page.getByRole('link', { name: /^Laporan$/i }).click();
    await expect(page).toHaveURL(/\/reports$/);
    const trendSelect = page.locator('#trend-dataset-select');
    const corpusTrendOpt = trendSelect.locator('option', { hasText: 'E2E Audit Corpus' });
    await expect(corpusTrendOpt).toHaveCount(1);
    const trendDsId = await corpusTrendOpt.getAttribute('value');
    expect(trendDsId).toBeTruthy();
    await trendSelect.selectOption(trendDsId!);
    await expect(page.getByRole('img', { name: /Tren Data Quality Index/i })).toBeVisible();
  });

  test('5. Explorer PII masking, search guard, reveal PII, diff, AI repair, shortcuts, and all 4 exports', async ({
    page,
  }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Dataset$/i }).click();

    const corpusCard = page.locator('div.rounded-card', { has: page.getByRole('heading', { name: 'E2E Audit Corpus', exact: true }) });
    await corpusCard.getByRole('button', { name: /Buka Explorer/i }).click();
    await expect(page).toHaveURL(/\/explorer\?versionId=/);

    // Default PII masked
    await expect(page.getByText('row_000001')).toBeVisible();
    await expect(page.locator('body')).not.toContainText('3273010101900001');

    // Raw PII search oracle blocked (LOGIC-03)
    const searchInput = page.getByPlaceholder(/Cari teks atau row_id/i);
    await searchInput.fill('3273010101900001');
    await expect(page.getByText(/Menampilkan 0-0 dari 0 baris/i)).toBeVisible();
    await searchInput.fill('');
    await expect(page.getByText(/Menampilkan 1-5 dari 5 baris/i)).toBeVisible();

    // Detail row highlight (<mark> for [NIK], [EMAIL], [KARTU]) + Diff preview
    await page.getByText('row_000001').click();
    await expect(page.getByText(/Detail Baris & Pratinjau Diff/i)).toBeVisible();
    const marks = page.locator('mark');
    await expect(marks).toHaveCount(3);
    await page.getByRole('button', { name: /Pratinjau Diff/i }).click();
    await expect(page.getByText(/Pratinjau Transformasi \(B6\)/i)).toBeVisible();

    // Privileged Reveal PII toggle
    await page.getByRole('button', { name: /Buka PII \(Izin Khusus\)/i }).click();
    await expect(page.locator('body')).toContainText('3273010101900001');
    await page.getByRole('button', { name: /Samarkan PII/i }).click();
    await expect(page.locator('body')).not.toContainText('3273010101900001');

    // Density toggle & filter controls
    await page.getByRole('button', { name: /Nyaman \(40px\)/i }).click();
    await expect(page.getByRole('button', { name: /Padat \(32px\)/i })).toBeVisible();
    await page.getByLabel(/Hanya baris tidak valid/i).check();
    await expect(page.getByText('row_000005')).toBeVisible();
    await page.getByLabel(/Hanya baris tidak valid/i).uncheck();

    // Manual edit + AI Repairing on row_000004 (creates v2)
    const row4 = page.locator('tr[data-row-id="row_000004"]');
    await row4.getByRole('button', { name: /Perbaiki/i }).click();
    await page.getByRole('button', { name: /Usulkan Perbaikan AI \(Prompt\)/i }).click();
    await expect(page.getByText('Mode: local_heuristic')).toBeVisible();
    await expect(page.locator('#edit-prompt')).toHaveValue('Kata terpotong saat parsing dokumen PDF.');
    await page.getByRole('button', { name: /Simpan ke Versi Baru/i }).click();
    await expect(page.getByText(/Perbaikan disimpan ke versi baru v2\./i)).toBeVisible();
    await expect(page.locator('tr[data-row-id="row_000001"]')).toBeVisible();

    // Keyboard shortcuts (?, Escape, j, a -> creates v3)
    await page.locator('h1').click();
    await page.keyboard.press('?');
    await expect(page.getByRole('dialog', { name: /Pintasan Keyboard Explorer/i })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: /Pintasan Keyboard Explorer/i })).toBeHidden();

    await page.keyboard.press('j');
    await expect(page.getByText(/Detail Baris & Pratinjau Diff — row_000001/i)).toBeVisible();
    await page.keyboard.press('a');
    await expect(page.getByText(/Versi baru v3 berhasil dibuat\./i)).toBeVisible();
    await expect(page.locator('tr[data-row-id="row_000001"]')).toBeVisible();

    // Export blocked while unmasked PII remains on v3
    await page.getByRole('button', { name: /^Ekspor$/i }).click();
    await expect(page.getByText(/Ekspor diblokir.*PII mentah/i)).toBeVisible();

    // Bulk Mask PII on row_000001 -> creates v4
    await page.getByLabel(/Pilih row_000001/i).check();
    await page.getByRole('button', { name: /^Masking PII$/i }).click();
    await expect(page.getByText(/Versi baru v4 berhasil dibuat\./i)).toBeVisible();

    // Run quality assessment on masked v4 so min_dqi Quality Gate passes for export
    await page.getByRole('link', { name: /^Dataset$/i }).click();
    const corpusV4Card = page.locator('div.rounded-card', { has: page.getByRole('heading', { name: 'E2E Audit Corpus', exact: true }) });
    await expect(corpusV4Card.getByText('v4')).toBeVisible();
    await corpusV4Card.getByRole('button', { name: /Jalankan Penilaian/i }).click();
    await expect(page).toHaveURL(/\/runs\?runId=/);
    await expect(page.getByText('Data Quality Index (DQI)')).toBeVisible();

    // Return to Explorer on v4 via SPA navigation and export all 4 formats
    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await expect(corpusV4Card.getByText('v4')).toBeVisible();
    await corpusV4Card.getByRole('button', { name: /Buka Explorer/i }).click();
    await expect(page).toHaveURL(/\/explorer\?versionId=/);
    await expect(page.getByText('row_000001')).toBeVisible();

    for (const fmt of ['jsonl', 'csv', 'parquet', 'huggingface'] as const) {
      await page.getByLabel(/Format Ekspor/i).selectOption(fmt);
      const [expDl] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: /^Ekspor$/i }).click(),
      ]);
      const expBuf = await fs.readFile((await expDl.path())!);
      expect(expBuf.length).toBeGreaterThan(20);
      if (fmt === 'parquet') {
        expect(expBuf.subarray(0, 4).toString('ascii')).toBe('PAR1');
      } else if (fmt === 'huggingface') {
        expect(expBuf.subarray(0, 2).toString('ascii')).toBe('PK');
      }
    }
  });

  test('6. Semantic Dedup Review threshold slider and duplicate rejection', async ({ page }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /Tinjau Duplikat/i }).click();
    await expect(page).toHaveURL(/\/dedup$/);

    // Select E2E Audit Corpus
    const dsSelect = page.locator('#dedup-dataset');
    const corpusOption = dsSelect.locator('option', { hasText: 'E2E Audit Corpus' });
    const val = await corpusOption.getAttribute('value');
    expect(val).toBeTruthy();
    await dsSelect.selectOption(val!);

    // Adjust cosine threshold to 50% via slider keyboard interaction to surface near-duplicate group
    const slider = page.locator('#dedup-threshold');
    await slider.focus();
    await page.keyboard.press('Home');
    await expect(page.getByText(/Ambang Batas Kemiripan Kosinus: 50%/i)).toBeVisible();
    await expect(page.getByText(/Kanonik \(Dipertahankan\)/i).first()).toBeVisible();

    await page.getByRole('button', { name: /Tolak Semua Duplikat/i }).click();
    await expect(page.getByText(/Aksi kurasi duplikat menghasilkan versi baru v5\./i)).toBeVisible();
  });

  test('7. Quality Policies persistence and restoration', async ({ page }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Kebijakan$/i }).click();
    await expect(page).toHaveURL(/\/policies$/);

    await page.fill('#min-dqi', '70');
    await page.fill('#critical-cap', '55');
    await page.fill('#dim-structure_validity', '80');
    await page.getByRole('button', { name: /Simpan Kebijakan/i }).click();
    await expect(page.getByText('Kebijakan berhasil disimpan.')).toBeVisible();

    // Reload via navigation and verify persisted values
    await page.getByRole('link', { name: /^Ringkasan$/i }).click();
    await page.getByRole('link', { name: /^Kebijakan$/i }).click();
    await expect(page.locator('#min-dqi')).toHaveValue('70');
    await expect(page.locator('#critical-cap')).toHaveValue('55');
    await expect(page.locator('#dim-structure_validity')).toHaveValue('80');

    // Restore clean defaults
    await page.fill('#min-dqi', '60');
    await page.fill('#critical-cap', '60');
    await page.fill('#dim-structure_validity', '0');
    await page.getByRole('button', { name: /Simpan Kebijakan/i }).click();
    await expect(page.getByText('Kebijakan berhasil disimpan.')).toBeVisible();
  });

  test('8. Audit Logs filtering and CSV/JSON export', async ({ page }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Audit Log$/i }).click();
    await expect(page).toHaveURL(/\/audit-logs$/);

    await expect(page.getByText('policy.update').first()).toBeVisible();
    await page.getByPlaceholder(/Filter aksi/i).fill('policy.update');
    await expect(page.getByText('policy.update').first()).toBeVisible();

    const [csvDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /Unduh CSV/i }).click(),
    ]);
    const csvText = await fs.readFile((await csvDl.path())!, 'utf-8');
    expect(csvText).toContain('policy.update');

    const [jsonDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /Unduh JSON/i }).click(),
    ]);
    const jsonText = await fs.readFile((await jsonDl.path())!, 'utf-8');
    const parsed = JSON.parse(jsonText) as Array<{ action: string }>;
    expect(parsed.some((entry) => entry.action === 'policy.update')).toBe(true);
  });

  test('9. Settings Enterprise tabs: API Tokens, Webhooks, SSO, Benchmarks, and UU PDP Retention', async ({
    page,
  }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Pengaturan$/i }).click();
    await expect(page).toHaveURL(/\/settings$/);

    // 1. API Tokens
    await page.fill('#token-name', 'E2E CI Token');
    await page.getByRole('button', { name: /Buat Token API/i }).click();
    await expect(page.locator('code', { hasText: /^qt_/ })).toBeVisible();
    const tokenRow = page.locator('div.p-4', { has: page.getByText('E2E CI Token') }).filter({ hasText: 'Cabut' }).first();
    await tokenRow.getByRole('button', { name: /Cabut/i }).click();
    await expect(page.getByText('Dicabut').first()).toBeVisible();

    // 2. Webhooks (SSRF rejected + valid webhook created & deleted)
    await page.getByRole('tab', { name: /^Webhook$/i }).click();
    await page.fill('#webhook-name', 'Bad Hook');
    await page.fill('#webhook-url', 'http://127.0.0.1:9999/ssrf');
    await page.getByRole('button', { name: /Tambah Webhook/i }).click();
    await expect(page.getByRole('alert')).toContainText(/privat|publik/i);

    await page.fill('#webhook-name', 'E2E Hook');
    await page.fill('#webhook-url', 'https://hooks.example.com/e2e');
    await page.getByRole('button', { name: /Tambah Webhook/i }).click();
    await expect(page.locator('code', { hasText: /^whsec_/ })).toBeVisible();
    await page.getByRole('button', { name: /^Hapus$/i }).first().click();
    await expect(page.getByText('https://hooks.example.com/e2e')).toBeHidden();

    // 3. SSO Corporate status
    await page.getByRole('tab', { name: /SSO Korporat/i }).click();
    await expect(page.getByText('OpenID Connect (OIDC)')).toBeVisible();
    await expect(page.getByText('/api/v1/auth/saml/metadata')).toBeVisible();

    // 4. Contamination Benchmarks
    await page.getByRole('tab', { name: /Benchmark Kontaminasi/i }).click();
    await expect(page.getByText('Evaluasi Dasar NLP Indonesia (Demo)')).toBeVisible();
    await page.fill('#bench-name', 'E2E Eval Suite');
    await page.fill('#bench-prompts', 'Pertanyaan uji satu\nPertanyaan uji dua');
    await page.getByRole('button', { name: /Simpan Set Benchmark/i }).click();
    await expect(page.getByText('Set benchmark berhasil ditambahkan.')).toBeVisible();
    await expect(page.getByText('E2E Eval Suite')).toBeVisible();

    // Delete created E2E benchmark set
    const benchRow = page.locator('div.p-4', { has: page.getByText('E2E Eval Suite') }).filter({ hasText: '2 prompt' }).first();
    await benchRow.getByRole('button', { name: /^Hapus$/i }).click();
    await expect(page.getByText('E2E Eval Suite')).toBeHidden();

    // 5. UU PDP Retention
    await page.getByRole('tab', { name: /Retensi UU PDP/i }).click();
    await page.fill('#retention-days', '45');
    await page.getByRole('button', { name: /Simpan Kebijakan Retensi/i }).click();
    await expect(page.getByText('Kebijakan retensi berhasil diperbarui.')).toBeVisible();

    await page.getByRole('button', { name: /Jalankan Pembersihan Kedaluwarsa Sekarang/i }).click();
    await expect(page.getByText(/dataset .* versi\) kedaluwarsa telah dihapus\./i)).toBeVisible();

    await page.fill('#retention-days', '');
    await page.getByRole('button', { name: /Simpan Kebijakan Retensi/i }).click();
    await expect(page.getByText('Kebijakan retensi berhasil diperbarui.')).toBeVisible();
  });

  test('10. Multi-role RBAC & cache isolation across Viewer and Governance', async ({ page }) => {
    // 1. Login as Viewer -> verify RBAC guards and zero Admin cache leakage
    await loginAs(page, 'viewer@qualitext.id', 'Viewer123!');

    await page.getByRole('link', { name: /^Audit Log$/i }).click();
    await expect(page.getByRole('alert')).toContainText(/tidak memiliki izin/i);

    await page.getByRole('link', { name: /^Pengaturan$/i }).click();
    await page.getByRole('tab', { name: /^Webhook$/i }).click();
    await expect(page.getByRole('alert')).toContainText(/tidak memiliki izin/i);

    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await expect(page.getByText('E2E Audit Corpus')).toBeVisible();
    await page.fill('#ds-name', 'Viewer Should Fail');
    await page.getByRole('button', { name: /Buat Dataset/i }).click();
    await expect(page.getByText(/tidak memiliki izin/i)).toBeVisible();

    // Viewer cannot reveal PII on Explorer and table remains intact
    const corpusCard = page.locator('div.rounded-card', { has: page.getByRole('heading', { name: 'E2E Audit Corpus', exact: true }) });
    await corpusCard.getByRole('button', { name: /Buka Explorer/i }).click();
    await expect(page.getByText('row_000001')).toBeVisible();
    await page.getByRole('button', { name: /Buka PII \(Izin Khusus\)/i }).click();
    await expect(page.getByText(/pii:view/i)).toBeVisible();
    await expect(page.getByText('row_000001')).toBeVisible();

    await logout(page);

    // 2. Login as Governance -> can read Audit Logs and Policies
    await loginAs(page, 'sari@qualitext.id', 'Governance123!');
    await page.getByRole('link', { name: /^Audit Log$/i }).click();
    await expect(page.getByText('pii.reveal').first()).toBeVisible();
    await page.getByRole('link', { name: /^Kebijakan$/i }).click();
    await expect(page.locator('#min-dqi')).toHaveValue('60');
  });

  test('11. Linguistic Quality (B7), VLM PDF Verification (D6), Semantic Chunking + RAG Export, and QA/SFT Synthesis', async ({
    page,
  }) => {
    await loginAs(page, 'admin@qualitext.id', 'Admin123!');
    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await expect(page).toHaveURL(/\/datasets$/);

    // 1. Create RAG dataset and upload vector-table PDF
    await page.fill('#ds-name', 'E2E PDF & RAG Pipeline');
    await page.selectOption('#ds-type', 'rag');
    await page.getByRole('button', { name: /Buat Dataset/i }).click();
    await expect(
      page.getByRole('heading', { name: 'E2E PDF & RAG Pipeline', exact: true }),
    ).toBeVisible();

    const pdfCard = page.locator('div.rounded-card', {
      has: page.getByRole('heading', { name: 'E2E PDF & RAG Pipeline', exact: true }),
    });
    const pdfBuf = buildVectorTablePdf(
      'QualiText adalah platform evaluasi kualitas data korporat dan dokumen tabel terstruktur.',
    );
    await pdfCard.locator('input[type="file"]').setInputFiles({
      name: 'sop_table.pdf',
      mimeType: 'application/pdf',
      buffer: pdfBuf,
    });
    await expect(page.getByText('Dataset berhasil diunggah dan divalidasi.')).toBeVisible();
    await expect(pdfCard.getByText(/1 baris/i)).toBeVisible();

    // 2. Open Pipeline NLP/RAG modal
    await pdfCard.getByRole('button', { name: /Pipeline NLP\/RAG/i }).click();
    const pipeDialog = page.getByRole('dialog', {
      name: /Pipeline Lanjutan NLP, VLM & RAG\/SFT/i,
    });
    await expect(pipeDialog).toBeVisible();

    // Tab 2: VLM PDF Verification detects table grid mismatch
    await pipeDialog.getByRole('tab', { name: /Verifikasi VLM PDF/i }).click();
    await pipeDialog.getByRole('button', { name: /Verifikasi Ekstraksi PDF/i }).click();
    await expect(pipeDialog.getByText('VLM_TABLE_STRUCTURE_MISMATCH')).toBeVisible();
    await expect(pipeDialog.getByText(/local_pdf_structural/i)).toBeVisible();

    // Tab 3: Semantic Chunking (v2) + QA/SFT Generator (new_dataset)
    await pipeDialog.getByRole('tab', { name: /Semantic Chunking & QA\/SFT/i }).click();
    await pipeDialog.getByRole('button', { name: /Jalankan Semantic Chunking/i }).click();
    await expect(pipeDialog.getByText(/Terbentuk .* chunk semantik \(v2/i)).toBeVisible();

    await pipeDialog.locator('#qa-target').selectOption('new_dataset');
    await pipeDialog.getByRole('button', { name: /Buat Pasangan QA\/SFT/i }).click();
    await expect(
      pipeDialog.getByText(/Berhasil menghasilkan .* pasangan QA\/SFT \(mode: local_template\)/i),
    ).toBeVisible();

    // Tab 1: Run custom quality modules including linguistic_quality & vlm_pdf_verifier
    await pipeDialog.getByRole('tab', { name: /Modul Kualitas/i }).click();
    await expect(pipeDialog.getByLabel('linguistic_quality')).toBeChecked();
    await pipeDialog.getByLabel('vlm_pdf_verifier').check();
    await pipeDialog.getByRole('button', { name: /Jalankan Modul Terpilih/i }).click();

    await expect(page).toHaveURL(/\/runs\?runId=/);
    await expect(
      page.getByText(/Kualitas Linguistik — Perplexity & Kosakata \(B7\)/i),
    ).toBeVisible();
    await expect(page.getByText(/Verifikasi VLM & Struktur PDF \(D6\)/i)).toBeVisible();

    // 3. Evaluate v2 (semantic chunked) and verify metadata + rag_vector export
    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await expect(pdfCard.getByText('v2')).toBeVisible();
    await pdfCard.getByRole('button', { name: /Jalankan Penilaian/i }).click();
    await expect(page).toHaveURL(/\/runs\?runId=/);
    await expect(page.getByText('Data Quality Index (DQI)')).toBeVisible();

    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await pdfCard.getByRole('button', { name: /Buka Explorer/i }).click();
    await expect(page).toHaveURL(/\/explorer\?versionId=/);
    await page.getByText('row_000001').click();
    await expect(page.getByText(/Metadata Chunk \/ SFT/i)).toBeVisible();
    await expect(page.getByText(/chunking_strategy:\s*semantic/i)).toBeVisible();

    await page.getByLabel(/Format Ekspor/i).selectOption('rag_vector');
    const [ragDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /^Ekspor$/i }).click(),
    ]);
    const ragText = await fs.readFile((await ragDl.path())!, 'utf-8');
    const ragRow = JSON.parse(ragText.trim().split('\n')[0]) as {
      id: string;
      text: string;
      metadata: Record<string, unknown>;
    };
    expect(ragRow.id).toBe('row_000001');
    expect(ragRow.text).toContain('QualiText');
    expect(ragRow.metadata.chunking_strategy).toBe('semantic');

    // 4. Evaluate generated SFT QA dataset and export sft_chat ChatML JSONL
    await page.getByRole('link', { name: /^Dataset$/i }).click();
    const qaCard = page.locator('div.rounded-card', {
      has: page.getByRole('heading', { name: 'E2E PDF & RAG Pipeline (SFT QA)', exact: true }),
    });
    await expect(qaCard).toBeVisible();
    await qaCard.getByRole('button', { name: /Jalankan Penilaian/i }).click();
    await expect(page).toHaveURL(/\/runs\?runId=/);
    await expect(page.getByText('Data Quality Index (DQI)')).toBeVisible();

    await page.getByRole('link', { name: /^Dataset$/i }).click();
    await qaCard.getByRole('button', { name: /Buka Explorer/i }).click();
    await expect(page).toHaveURL(/\/explorer\?versionId=/);
    await page.getByLabel(/Format Ekspor/i).selectOption('sft_chat');
    const [sftDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: /^Ekspor$/i }).click(),
    ]);
    const sftText = await fs.readFile((await sftDl.path())!, 'utf-8');
    const sftRow = JSON.parse(sftText.trim().split('\n')[0]) as {
      messages: Array<{ role: string; content: string }>;
    };
    expect(sftRow.messages.map((m) => m.role)).toEqual(['system', 'user', 'assistant']);
  });
});
