#!/usr/bin/env python3
"""CDK entry point.

One stack today (`foundation`); compute + orchestration stacks are
added in later phases. Account/region come from the active CLI profile
(`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION` populated by `cdk synth`).
"""

import os

import aws_cdk as cdk

from stacks.foundation import FoundationStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION"),
)

FoundationStack(app, "MlUsptoFoundation", env=env)

app.synth()
