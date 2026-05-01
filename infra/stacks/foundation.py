"""Foundation stack — storage, registry, secrets, task role.

Owns the long-lived primitives that later stacks (Step Functions state
machine, EventBridge rule) compose on top of:

  - S3 bucket with `raw/` and `processed/` key prefixes, byte-identical
    to the local `data/` layout (`aws s3 sync data/raw/ s3://.../raw/`
    is the bootstrap seed).
  - ECR repo for the stage-driver image built from `containers/Dockerfile`.
  - Secrets Manager entry for the USPTO ODP API key (`X-API-Key` header).
  - IAM task role for Fargate tasks: read/write the bucket, read the
    secret. ECS execution role (image pull, log push) is left to the
    later compute stack which owns the task definitions.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class FoundationStack(cdk.Stack):
    """Long-lived primitives (bucket, ECR repo, secret, task role) for downstream stacks."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        """Provision the S3 bucket, ECR repo, API-key secret, and Fargate task role."""
        super().__init__(scope, construct_id, **kwargs)

        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.repository = ecr.Repository(
            self,
            "DriverImage",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 10 images",
                    max_image_count=10,
                )
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # Secret value is populated out-of-band (one-shot manual write).
        # CDK only creates the empty entry; rotating the key is a console
        # action, not a deploy.
        self.api_key_secret = secretsmanager.Secret(
            self,
            "UsptoApiKey",
            description="USPTO ODP API key (X-API-Key header)",
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        self.task_role = iam.Role(
            self,
            "DriverTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Fargate task role for ml-uspto stage drivers",
        )
        self.bucket.grant_read_write(self.task_role)
        self.api_key_secret.grant_read(self.task_role)

        cdk.CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        cdk.CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        cdk.CfnOutput(self, "ApiKeySecretArn", value=self.api_key_secret.secret_arn)
        cdk.CfnOutput(self, "TaskRoleArn", value=self.task_role.role_arn)
