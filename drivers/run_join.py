"""Stage 4 driver — join trials ⨝ petitions ⨝ patent features.

Reads every stage 1–3 parquet from `paths.processed_dir()` plus the cached
raw patent payloads under `data/raw/patents/`, runs the canonical T₀-safe
aggregator per (trial, app), and writes `data/processed/joined_trials.parquet`.
No HTTP calls — pure post-processing. Run after stages 1–3 are populated.
"""

import logging

from ml_uspto import paths
from ml_uspto.clients import local
from ml_uspto.parse.joiner import load_and_join


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    df, report = load_and_join()
    out = paths.joined_trials_parquet()
    local.save_parquet(df, out)
    print(f"joined: {len(df)} rows -> {out}")
    print(f"  trials in:                   {report.n_trials_input}")
    print(f"  labeled (post-preprocess):   {report.n_trials_labeled}")
    print(f"  petition quarantine:         {report.n_petition_quarantine}")
    print(f"  patent quarantine:           {report.n_patent_quarantine}")
    print(f"  with patent features:        {report.n_with_patent_features}")
    print(f"  without patent features:     {report.n_without_patent_features}")


if __name__ == "__main__":
    main()
