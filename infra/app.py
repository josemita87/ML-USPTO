#!/usr/bin/env python3
"""CDK entry point. Resources are added in later phases."""

import aws_cdk as cdk
from constructs import Construct


class MlUsptoStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)


app = cdk.App()
MlUsptoStack(app, "MlUsptoStack")
app.synth()
