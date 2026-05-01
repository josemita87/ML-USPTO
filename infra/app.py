#!/usr/bin/env python3
"""CDK entry point.

Three stacks: foundation (storage/registry/secret/role), compute (cluster
+ task def), orchestration (Step Functions DAG + weekly EventBridge rule).
Account/region come from the active CLI profile (`CDK_DEFAULT_ACCOUNT` /
`CDK_DEFAULT_REGION` populated by `cdk synth`).
"""

import os

import aws_cdk as cdk
from stacks.compute import ComputeStack
from stacks.foundation import FoundationStack
from stacks.orchestration import OrchestrationStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION"),
)

foundation = FoundationStack(app, "MlUsptoFoundation", env=env)

compute = ComputeStack(
    app,
    "MlUsptoCompute",
    bucket=foundation.bucket,
    repository=foundation.repository,
    api_key_secret=foundation.api_key_secret,
    task_role=foundation.task_role,
    env=env,
)

OrchestrationStack(
    app,
    "MlUsptoOrchestration",
    cluster=compute.cluster,
    task_definition=compute.task_definition,
    env=env,
)

app.synth()
