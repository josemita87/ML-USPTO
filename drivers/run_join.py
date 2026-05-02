"""Stage 4 driver — join trials ⨝ decisions ⨝ petitions ⨝ patents ⨝ petition-text + label.

Reads stage 1–3 frames + the petition-text frame via the storage
backend and writes the `Frame.JOINED_TRIALS` frame. No HTTP calls, no
T₀ feature engineering — that's `drivers/run_features.py`. Run after
stages 1–3 plus `drivers/run_ingest_petition_text.py` are populated.
"""

import logging

from ml_uspto.clients.storage import get_storage
from ml_uspto.parse.joiner import join_all
from ml_uspto.schemas.enums import Frame


def main() -> None:
    """Join the per-stage frames and write the labeled joined-trials frame."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    storage = get_storage()
    petition_texts = storage.load_frame(Frame.PETITION_TEXTS)

    df, report = join_all(
        storage,
        trials=storage.load_frame(Frame.TRIALS),
        decisions=storage.load_frame(Frame.DECISIONS),
        petitions=storage.load_frame(Frame.PETITIONS),
        patents=storage.load_frame(Frame.PATENTS),
        petition_texts=petition_texts,
    )
    storage.save_frame(df, Frame.JOINED_TRIALS)
    n_dropped = report.n_trials_labeled - report.n_joined
    print(f"joined: {len(df)} rows -> frame {Frame.JOINED_TRIALS.value}")
    print(f"  trials in:                   {report.n_trials_input}")
    print(f"  labeled (post-build_labels): {report.n_trials_labeled}")
    print(f"  labeled w/o petition row:    {n_dropped}")
    print(f"  T₀ mismatch (warned):        {report.n_petition_t0_mismatch}")
    print(f"  petition-text rows merged:   {len(petition_texts)}")


if __name__ == "__main__":
    main()
