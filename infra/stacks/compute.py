"""Compute stack — Fargate cluster + generic stage-driver task definition.

One task definition is registered, parameterised at run-time by the
`command` override (`-m drivers.<name>`). Step Functions states (added
later) supply the override per stage, so we don't define a separate
task def per driver.

Cross-stack inputs from `FoundationStack`:
  - bucket (read/write target)
  - repository (image source, pinned to `:latest`)
  - api_key_secret (USPTO_API_KEY env injection)
  - task_role (already grants bucket + secret access)

Created here:
  - ECS cluster (Fargate-only, no EC2)
  - CloudWatch log group, 30-day retention
  - Execution role: ECR pull + log push + secret read (CFN expands the
    secret value into the env var at task start; the role only needs
    GetSecretValue)
  - Task definition: 1 vCPU / 2 GB, linux/amd64, awslogs driver
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_ecr as ecr,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class ComputeStack(cdk.Stack):
    """Fargate cluster, log group, and a generic stage-driver task definition."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        bucket: s3.IBucket,
        repository: ecr.IRepository,
        api_key_secret: secretsmanager.ISecret,
        task_role: iam.IRole,
        **kwargs,
    ) -> None:
        """Wire the cluster, execution role, and parameterised task definition."""
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = ecs.Cluster(
            self,
            "DriverCluster",
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        self.log_group = logs.LogGroup(
            self,
            "DriverLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        execution_role = iam.Role(
            self,
            "DriverExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        api_key_secret.grant_read(execution_role)

        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "DriverTask",
            cpu=1024,
            memory_limit_mib=2048,
            execution_role=execution_role,
            task_role=task_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        self.task_definition.add_container(
            "driver",
            image=ecs.ContainerImage.from_ecr_repository(repository, "latest"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="driver", log_group=self.log_group),
            environment={
                "ML_USPTO_BACKEND": "s3",
                "ML_USPTO_S3_BUCKET": bucket.bucket_name,
            },
            secrets={
                "USPTO_API_KEY": ecs.Secret.from_secrets_manager(api_key_secret),
            },
        )

        cdk.CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        cdk.CfnOutput(self, "TaskDefinitionArn", value=self.task_definition.task_definition_arn)
        cdk.CfnOutput(self, "LogGroupName", value=self.log_group.log_group_name)
