# Proceedings Dataset

Data fetched from the USPTO PTAB API. Each row represents a single PTAB proceeding (e.g., IPR, PGR, CBM).

This file documents only the **proceedings** endpoint (`/trials/proceedings/search`), which provides trial metadata and is the source of the target variable (`trial_status` / `trial_outcome`). Two other endpoints are also wired up and feed the feature pipeline:

- **Decisions** (`/trials/decisions/search`) — outcome categories, decision types, statutes cited, document metadata. Fetched via `fetch_ipr_decisions()`.
- **Documents** (`/trials/{trialNumber}/documents`) — per-trial filings (petition, preliminary response, institution decision, FWD, etc.). Fetched via `get_trial_documents()`.

Bulk CSV/JSON download endpoints are the preferred path for full-dataset feature extraction; the per-record API calls are used for targeted exploration.

## Columns

### Identifiers

| Column | Description |
|--------|-------------|
| `trial_number` | Unique identifier for the PTAB proceeding (e.g., `IPR2016-00001`). |
| `patent_number` | The patent number being challenged in the proceeding. |
| `application_number` | The original USPTO application number associated with the patent. |

### Trial Metadata

| Column | Description |
|--------|-------------|
| `trial_type` | Type of proceeding code (e.g., `IPR`, `PGR`, `CBM`). |
| `trial_status` | Current status category of the trial (e.g., `FWD Entered`, `Terminated-Settled`, `Instituted`). |
| `petition_filing_date` | Date the petitioner filed the challenge against the patent. |
| `accorded_filing_date` | The official filing date accorded by the PTAB (may differ from petition filing date). |
| `institution_decision_date` | Date the PTAB decided whether to institute (formally begin) the trial. |
| `latest_decision_date` | Date of the most recent decision issued in the proceeding. |
| `termination_date` | Date the proceeding was terminated (if applicable). |

### Patent Owner Data

| Column | Description |
|--------|-------------|
| `owner_real_party` | The real party in interest — the actual company or entity that owns the patent. |
| `owner_counsel` | The attorney or law firm representing the patent owner in the proceeding. |
| `grant_date` | Date the patent was originally granted/issued by the USPTO, before any PTAB challenge. Useful for computing patent age at time of petition. |
| `group_art_unit` | 4-digit code identifying the USPTO examining unit that originally examined the patent. The first two digits map to the `technology_center`. Some art units may correlate with higher invalidation rates. |
| `technology_center` | Broader technology category (first two digits of `group_art_unit`). E.g., 2600 = Communications, 1600 = Biotechnology/Chemistry. |
| `inventor_name` | Name of the inventor(s) listed on the patent. |

### Petitioner Data

| Column | Description |
|--------|-------------|
| `petitioner_real_party` | The real party in interest challenging the patent (the entity behind the petition). |
| `petitioner_counsel` | The attorney or law firm representing the petitioner. |
