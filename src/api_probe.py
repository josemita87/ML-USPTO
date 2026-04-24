"""Quick exploratory probes against the USPTO PTAB endpoints.

Run all probes:
    python -m src.api_probe

Run a single probe:
    python -m src.api_probe proceedings
    python -m src.api_probe decisions --query Fintiv
    python -m src.api_probe documents --trial IPR2023-00001

Each probe prints: HTTP-level shape (top-level keys), record count, and a
truncated JSON sample of the first record so you can eyeball the schema.
"""

import argparse
import json
from typing import Any, Callable

from data.client import USPTOClient

PREVIEW_CHARS = 2500


def _preview(resp: dict, record_keys: tuple[str, ...] = ()) -> None:
    print(f"  top-level keys: {list(resp.keys())}")
    if "count" in resp:
        print(f"  count: {resp['count']}")
    records: list[Any] = []
    for key in record_keys:
        if key in resp and isinstance(resp[key], list):
            records = resp[key]
            print(f"  records under '{key}': {len(records)}")
            break
    else:
        for k, v in resp.items():
            if isinstance(v, list) and v:
                records = v
                print(f"  records under '{k}': {len(records)}")
                break
    if records:
        sample = json.dumps(records[0], indent=2, default=str)
        print("  sample record:")
        print(sample[:PREVIEW_CHARS])
        if len(sample) > PREVIEW_CHARS:
            print(f"  ... (truncated, full length {len(sample)} chars)")
    else:
        print("  (no list of records found in response)")


def probe_proceedings_search(client: USPTOClient, query: str = "IPR", limit: int = 1) -> None:
    print(f"\n=== GET /trials/proceedings/search (query={query!r}, limit={limit}) ===")
    resp = client.search_proceedings(query=query, limit=limit)
    _preview(resp, record_keys=("patentTrialProceedingDataBag", "results"))


def probe_proceeding_detail(client: USPTOClient, trial: str | None = None) -> None:
    if trial is None:
        search = client.search_proceedings(query="IPR", limit=1)
        records = (
            search.get("patentTrialProceedingDataBag")
            or search.get("results")
            or []
        )
        if not records:
            print("  could not resolve a trial_number from search — pass --trial explicitly")
            return
        trial = (
            records[0].get("trial_number")
            or records[0].get("trialNumber")
            or records[0].get("proceedingNumber")
        )
        if not trial:
            print(f"  couldn't find trial number in record keys: {list(records[0].keys())}")
            return
    print(f"\n=== GET /trials/proceedings/{{trial_number}} (trial={trial}) ===")
    resp = client.get_proceeding(trial)
    _preview(resp)


def probe_decisions_search(client: USPTOClient, query: str = "Fintiv", limit: int = 1) -> None:
    print(f"\n=== GET /trials/decisions/search (query={query!r}, limit={limit}) ===")
    resp = client.search_decisions(query=query, limit=limit)
    _preview(resp, record_keys=("patentTrialDocumentDataBag", "results"))


def probe_trial_documents(client: USPTOClient, trial: str | None = None) -> None:
    if trial is None:
        search = client.search_proceedings(query="IPR", limit=1)
        records = (
            search.get("patentTrialProceedingDataBag")
            or search.get("results")
            or []
        )
        if records:
            trial = (
                records[0].get("trial_number")
                or records[0].get("trialNumber")
                or records[0].get("proceedingNumber")
            )
    if not trial:
        print("  could not resolve a trial_number — pass --trial explicitly")
        return
    print(f"\n=== GET /trials/{{trial_number}}/documents (trial={trial}) ===")
    resp = client.get_trial_documents(trial)
    _preview(resp)


PROBES: dict[str, Callable[..., None]] = {
    "proceedings": probe_proceedings_search,
    "proceeding_detail": probe_proceeding_detail,
    "decisions": probe_decisions_search,
    "documents": probe_trial_documents,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "probe",
        nargs="?",
        choices=[*PROBES.keys(), "all"],
        default="all",
        help="Which probe to run (default: all).",
    )
    parser.add_argument("--query", default=None, help="Query string for search probes.")
    parser.add_argument("--trial", default=None, help="Trial number for detail/documents probes.")
    parser.add_argument("--limit", type=int, default=1, help="Record limit for search probes.")
    args = parser.parse_args()

    client = USPTOClient()
    targets = PROBES.keys() if args.probe == "all" else [args.probe]

    for name in targets:
        fn = PROBES[name]
        kwargs: dict[str, Any] = {}
        if name in {"proceedings", "decisions"}:
            if args.query is not None:
                kwargs["query"] = args.query
            kwargs["limit"] = args.limit
        elif name in {"proceeding_detail", "documents"}:
            if args.trial is not None:
                kwargs["trial"] = args.trial
        try:
            fn(client, **kwargs)
        except Exception as e:
            print(f"  ERROR running probe '{name}': {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
