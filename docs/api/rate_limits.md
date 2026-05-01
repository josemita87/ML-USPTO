# USPTO ODP Rate Limits

Operational constraints on API usage. Captured from the ODP rate-limit documentation, 2026-04-24. Subject to change — re-verify before large ingestion runs.

---

## 1. Three quota buckets

ODP classifies APIs into three categories, each with its own weekly quota and limits.

| Bucket | Weekly quota | What's in it |
|---|---|---|
| **Metadata retrieval APIs** | **5,000,000 calls / week (combined)** | Patent File Wrapper metadata (Application Data, Continuity, Transactions, PTA, Addresses, Attorneys, Assignments, Foreign Priority, Associated Documents); Bulk Datasets catalog (Product Data); **Final Petition Decisions Search, Document Data, and Download** |
| **Patent File Wrapper Documents API** | **1,200,000 calls / week** | Document retrievals under the file wrapper — likely where per-trial PDF fetches via `fileDownloadURI` count |
| **Bulk Datasets Downloads API** | **20 downloads per file per year** (higher for XML); max **5 files per 10 seconds per IP** | The zipped product files from `/datasets/products/files/...` |

Weekly quotas reset **Sunday at 00:00 UTC**.

### Where our workload lands

| Operation | Client method | Bucket |
|---|---|---|
| `POST /trials/proceedings/search` | `search_proceedings_post()` | Metadata (5M/wk) |
| `POST /trials/decisions/search` (with or without OCR) | `search_decisions_post()` | Metadata (5M/wk) |
| `POST /trials/decisions/search/download` | `download_decisions()` | Metadata (5M/wk) — explicitly called out as "Search, Document Data, and Download" |
| `GET /trials/{trial}/documents` | `get_trial_documents()` | Metadata (5M/wk) — "Document Data" is metadata tier |
| `GET /trials/proceedings/{trial}` | `get_proceeding()` | Metadata (5M/wk) |
| Fetching a PDF via `documentData.fileDownloadURI` | `download_pdf()` | Likely **File Wrapper Documents** (1.2M/wk) |
| Downloading a bulk zip (e.g. `PTFWPRE`) | Not in client | **Bulk Downloads** (20/file/yr) |

Note: `documentData.documentOCRText` returned inline by `search_decisions_post()` is a **500-char preview only** (confirmed 2026-04-24) — enough for the case caption and judge names, not the substantive decision text. Full decision text requires fetching the PDF via `documentData.fileDownloadURI`, which falls in the **File Wrapper Documents** bucket (1.2M/wk). Structured decision fields (`statuteAndRuleBag`, `issueTypeBag`, `trialOutcomeCategory`) remain free in the metadata bucket, but `trialOutcomeCategory` is not granular enough for IPR FWD labels in the current corpus.

---

## 2. Concurrency and request rate

These apply to **every** request regardless of bucket:

- **Burst = 1.** No parallel requests per API key. Concurrent calls with the same key are actively blocked.
- **Rate = 4–15 requests/second**, depending on the endpoint.
- **Do not parallelize**. ODP explicitly warns against running the same key in parallel processes.
- **429 handling**: wait **at least 5 seconds** before retrying. ODP "strongly discourages" auto-retries without that delay.
- **Signed URLs for bulk zips expire in 5 seconds** — start downloading redirects immediately.

### Implication for PTAB ingestion

Serial-only. At ~10 req/sec sequential, the binding constraint is **wall-clock time from burst=1 serialization, not quota**.

For the canonical cost model under the current scope, see **`../scope/prediction_scope.md` §5.4**. Summary: the current feature pipeline is metadata-only and is dominated by patent file-wrapper enrichment. The only PDFs fetched today are original-FWD PDFs needed for label fallback, not petition-text features. Petition PDF downloads remain deferred to v2.

---

## 3. Client-side behavior

`USPTOClient._get` / `_post` (see `src/ml_uspto/clients/uspto.py`):

- On 429, back off **5s, 10s, 20s** across three retries (ODP minimum is 5s — not the earlier 2s/4s/8s).
- Raises `RuntimeError` after three 429s.
- Uses a single `requests.Session` — fine for serial usage, but do **not** share it across threads or processes.

Guidelines for ingestion scripts:
- One process, one key, serial calls.
- Chunk large pulls by `rangeFilters` on `petitionFilingDate` (year buckets) rather than a single linear `offset` — safer against deep-pagination caps and easier to resume on failure.
- Cache responses to disk as you go (`data/raw/`) so a partial run can resume without re-hitting the API.
- For bulk zips: download once, never re-download — the 20-per-year cap is hard.

> ⚠ **`fileDownloadURI` PDFs require the `X-API-Key` header.** Empirical (2026-04-27): a stratified probe of 50 petition PDFs returned `403 Forbidden` for every URL when fetched with a bare `requests.get(uri)`. The same URLs returned 200 when fetched through `client.session.get(uri)`, which carries the `X-API-Key` header set in `USPTOClient.__init__`. The URL shape (`https://api.uspto.gov/api/v1/patent/ptab-files/IPR/...`) looks like a static asset path but goes through the same auth gate as the search APIs. **PDF download code must reuse `USPTOClient.download_pdf()` or the authenticated session — bare `requests` calls will silently fail.**

---

## 4. What to verify before a full pull

Three unknowns worth probing before committing to a full ingestion run:

1. **Max `limit` per request.** Default is 25; the spec doesn't state a ceiling. If 100 or 1000 is accepted, per-request count drops 4–40×.
2. **Deep-offset behavior.** Many ODP-style endpoints cap `offset` around 10,000. If so, `rangeFilters` partitioning by date becomes mandatory, not optional.
3. **OCR-text truncation cap.** An earlier probe returned `OCR length: 500` on a trimmed projection; confirm whether a full `documentData` request returns the entire decision text or whether there is a silent cap.

---

## 5. Relationship to other docs

- `api_feature_map.md` — which endpoint to hit for which feature (no rate info).
- `bulk_datasets.md` — bulk product catalog (the 20-per-file-per-year limit applies here).
- `ODP-API-Query-Spec.pdf` — request syntax reference.
