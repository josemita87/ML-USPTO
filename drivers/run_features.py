"""Stage 5 driver — joined frame → model-ready feature matrix.

Reads `Frame.JOINED_TRIALS` and writes `Frame.FEATURES`. Pure pandas:
no HTTP, no raw-cache reads. T₀ leakage discipline + per-trial patent
feature aggregation + static-feature transforms all happen inside
`features.transforms.build_features`.

Run after `run_join`.
"""

import logging

from ml_uspto.clients.storage import get_storage
from ml_uspto.features.transforms import build_features
from ml_uspto.schemas.enums import Frame


def main() -> None:
    """Build the feature matrix from the joined trials frame."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    storage = get_storage()
    joined = storage.load_frame(Frame.JOINED_TRIALS)
    matrix = build_features(joined)
    storage.save_frame(matrix, Frame.FEATURES)
    print(
        f"features: {matrix.shape[0]} rows × {matrix.shape[1]} cols "
        f"-> frame {Frame.FEATURES.value}"
    )


if __name__ == "__main__":
    main()
