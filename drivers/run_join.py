"""Stage 4 driver — join trials ⨝ decisions ⨝ petitions ⨝ patents + label.

Reads stage 1–3 frames via the storage backend and writes the
`Frame.JOINED_TRIALS` frame. No HTTP calls, no T₀ feature engineering —
that's `drivers/run_features.py`. Run after stages 1–3 are populated.
"""

import logging

from ml_uspto.clients.storage import get_storage
from ml_uspto.parse.joiner import join_all
from ml_uspto.schemas.enums import Frame


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    storage = get_storage()
    df, report = join_all(
        storage,
        trials=storage.load_frame(Frame.TRIALS),
        decisions=storage.load_frame(Frame.DECISIONS),
        petitions=storage.load_frame(Frame.PETITIONS),
        patents=storage.load_frame(Frame.PATENTS),
    )
    storage.save_frame(df, Frame.JOINED_TRIALS)
    n_dropped = report.n_trials_labeled - report.n_joined
    print(f"joined: {len(df)} rows -> frame {Frame.JOINED_TRIALS.value}")
    print(f"  trials in:                   {report.n_trials_input}")
    print(f"  labeled (post-build_labels): {report.n_trials_labeled}")
    print(f"  labeled w/o petition row:    {n_dropped}")
    print(f"  T₀ mismatch (warned):        {report.n_petition_t0_mismatch}")


if __name__ == "__main__":
    main()
