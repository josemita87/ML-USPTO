# Session Summary — April 8, 2026

## Project Overview

We are building an ML model to predict IPR (Inter Partes Review) outcomes using publicly available data from the USPTO PTAB API. The project is guided by domain expertise from a practicing patent attorney specializing in PTAB proceedings.

## What We Did Today

### 1. Data Exploration

- Explored the **proceedings** dataset (17 columns): trial metadata, patent owner data, petitioner data, dates, technology classifications
- Explored the **decisions** dataset: outcome categories, decision types, statutes cited, document metadata
- Inspected the **documents** endpoint structure for individual trials
- Tested full-text search queries for key legal concepts (Fintiv, Sotera, 325(d))
- Documented all column definitions in `exploration/proceedings.md`

### 2. Domain Knowledge Extraction

Analyzed three call transcripts with our domain expert and distilled key insights into `exploration/domain_notes.md`. Highlights:

- **Fintiv factors** (6 sub-factors) are consistently structured in decision documents with explicit headings and standardized phrasing — highly extractable
- **Political regime cycles** at the USPTO are the strongest macro driver of institution/outcome rates (new director = new policy every ~4 years)
- **New discretionary denial factors** expected June 2025 under the current administration's memo — the data schema may shift
- Key binary signals: Fintiv addressed (Y/N), Sotera stipulation (Y/N), 325(d) addressed (Y/N)
- Revenue incentive: USPTO keeps ~$40-50K per petition regardless of outcome, creating a structural floor on institution rates

### 3. API and Data Pipeline

- **Proceedings endpoint** (`/trials/proceedings/search`) — already wired up and fetching
- **Decisions endpoint** (`/trials/decisions/search`) — wired up today, fetches outcome data
- **Documents endpoint** (`/trials/{trialNumber}/documents`) — wired up today, fetches per-trial filings
- **Bulk download endpoints** available for CSV/JSON export of full datasets — preferred approach for feature extraction at scale over per-record API calls

### 4. Codebase Updates

- Added `fetch_ipr_decisions()` to the data pipeline
- Added `get_trial_documents()` to the API client
- Created exploration scripts for inspecting API response structures
- Created `exploration/proceedings.md` (column documentation) and `exploration/domain_notes.md` (anonymized domain insights)

## Current Direction

### Target Variable

**IPR final outcome** (not institution decision). This is a shift from the initial framing — we are now predicting what happens at the end of the trial rather than whether it gets instituted. Target class definition (binary vs. multi-class, treatment of settlements) is still to be decided.

### Feature Strategy

- **Metadata features** (from API): technology center, patent age, counsel identity, filing dates, policy regime
- **Document-level features** (from bulk download + regex/LLM extraction): Fintiv factor ratings, discretionary denial section length, prior art count, legal concept presence
- **Temporal features**: policy regime indicator based on filing date

### Next Steps

- Define target variable classes from the `trial_status` / `trial_outcome` fields
- Bulk download decisions dataset for offline processing
- Regex-based extraction of Fintiv sub-factors and discretionary denial signals from decision OCR text
- Enrich proceedings data with patent application metadata (prosecution history, assignments, continuity)
- Obtain the domain expert's annotated spreadsheet with labeled Fintiv sub-factors for validation

### Data Scale

- ~20K total IPR decisions since 2012 (~1,500 petitions/year)
- Expandable by including PGR/CBM proceedings, or treating individual claims as observations
- Sufficient for traditional ML approaches (XGBoost, logistic regression); fine-tuning pretrained models for text features is feasible at this scale
