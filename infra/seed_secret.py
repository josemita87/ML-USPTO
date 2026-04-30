#!/usr/bin/env python3
"""Seed the USPTO API-key secret from local `.credentials.env`.

Reads `USPTO_API_KEY` from the project's credentials file and writes it
to the Secrets Manager entry created by `FoundationStack`. The secret
ARN is resolved from the deployed CFN stack's outputs, so the script
only needs the stack name (default `MlUsptoFoundation`).

Run once after `cdk deploy`, and again only when the key rotates. Kept
deliberately out of the CDK template — secret material in CFN templates
ends up in synth output, deploy logs, and drift snapshots.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = REPO_ROOT / ".credentials.env"
ENV_VAR = "USPTO_API_KEY"
DEFAULT_STACK = "MlUsptoFoundation"
OUTPUT_KEY = "ApiKeySecretArn"


def _read_api_key(path: Path) -> str:
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == ENV_VAR:
            return v.strip().strip('"').strip("'")
    raise SystemExit(f"{ENV_VAR} not found in {path}")


def _resolve_secret_arn(cfn, stack: str) -> str:
    resp = cfn.describe_stacks(StackName=stack)
    for out in resp["Stacks"][0].get("Outputs", []):
        if out["OutputKey"] == OUTPUT_KEY:
            return out["OutputValue"]
    raise SystemExit(f"output {OUTPUT_KEY} not found on stack {stack}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default=DEFAULT_STACK)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    api_key = _read_api_key(CREDENTIALS_FILE)
    session = boto3.session.Session(region_name=args.region)
    cfn = session.client("cloudformation")
    secrets = session.client("secretsmanager")

    arn = _resolve_secret_arn(cfn, args.stack)
    secrets.put_secret_value(SecretId=arn, SecretString=api_key)
    print(f"seeded {arn} from {CREDENTIALS_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
