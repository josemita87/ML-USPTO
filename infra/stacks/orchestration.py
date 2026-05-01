"""Orchestration stack — Step Functions DAG + weekly EventBridge trigger.

Composes the long-lived primitives from `FoundationStack` (bucket, secret,
task role) and `ComputeStack` (cluster, task definition) into a runnable
weekly pipeline.

DAG (mirrors `docs/ops/refresh_lifecycle.md` §2):

    Parallel {
        branch A: proceedings → decisions → fwd_texts
        branch B: petitions
    } → patents → join → features

Why this shape:
  - Petitions is independent of trials (per doc), so it runs alongside
    branch A from t=0.
  - Patents needs both proceedings + petitions; placing it after the
    parallel block keeps the DAG legible. ODP enforces burst=1 per key,
    so parallel branches against the API serialize anyway — Step
    Functions parallelism here is for failure isolation, not throughput.
  - Join runs after every ingest stage is populated.
  - Features runs last — pure pandas on the joined frame, no HTTP.

Each stage is one `EcsRunTask` invocation against the shared task
definition, parameterised at run-time by the `command` override
(`-m drivers.<name>`). Same image, same task role, different entrypoint.

EventBridge rule fires weekly (Mondays 02:00 UTC) — adjust schedule
in-stack if cadence changes.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct


class OrchestrationStack(cdk.Stack):
    """Wire the weekly ECS-backed ingestion and feature pipeline."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cluster: ecs.ICluster,
        task_definition: ecs.FargateTaskDefinition,
        **kwargs,
    ) -> None:
        """Create the state machine, stage tasks, and weekly trigger."""
        super().__init__(scope, construct_id, **kwargs)

        # One SG shared across every state — avoids per-task SG sprawl.
        # Egress-only (default); no ingress needed (no inbound traffic).
        task_sg = ec2.SecurityGroup(
            self,
            "TaskSecurityGroup",
            vpc=cluster.vpc,
            description="Shared SG for ml-uspto stage-driver Fargate tasks",
            allow_all_outbound=True,
        )

        container = task_definition.find_container("driver")
        if container is None:  # pragma: no cover — CDK guarantees by construction
            raise RuntimeError("ComputeStack task definition is missing the 'driver' container")

        def stage(construct_id: str, driver: str) -> sfn_tasks.EcsRunTask:
            task = sfn_tasks.EcsRunTask(
                self,
                construct_id,
                cluster=cluster,
                task_definition=task_definition,
                launch_target=sfn_tasks.EcsFargateLaunchTarget(
                    platform_version=ecs.FargatePlatformVersion.LATEST,
                ),
                # Public subnets + public IP: tasks need ECR pull + ODP API
                # egress, no NAT GW provisioned. Tighten with NAT later if
                # the bucket grows enough to justify the fixed cost.
                subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                assign_public_ip=True,
                security_groups=[task_sg],
                container_overrides=[
                    sfn_tasks.ContainerOverride(
                        container_definition=container,
                        command=["-m", f"drivers.{driver}"],
                    )
                ],
                # Sync — block on task completion, surface non-zero exit as state failure.
                integration_pattern=sfn.IntegrationPattern.RUN_JOB,
                # Hard ceiling per stage; observed wall-clocks all fit
                # comfortably (cold FWD-text backfill ~63 min for 1,089 PDFs).
                task_timeout=sfn.Timeout.duration(cdk.Duration.hours(2)),
            )
            task.add_retry(
                errors=["States.ALL"],
                max_attempts=2,
                interval=cdk.Duration.seconds(30),
                backoff_rate=2.0,
            )
            return task

        proceedings = stage("RunProceedings", "run_ingest_proceedings")
        decisions = stage("RunDecisions", "run_ingest_decisions")
        fwd_texts = stage("RunFwdTexts", "run_ingest_decision_texts")
        petitions = stage("RunPetitions", "run_ingest_petitions")
        patents = stage("RunPatents", "run_ingest_patents")
        join = stage("RunJoin", "run_join")
        features = stage("RunFeatures", "run_features")

        branch_a = proceedings.next(decisions).next(fwd_texts)
        branch_b = petitions

        parallel = (
            sfn.Parallel(self, "TrialsAndPetitions")
            .branch(branch_a)
            .branch(branch_b)
        )

        definition = parallel.next(patents).next(join).next(features)

        self.state_machine = sfn.StateMachine(
            self,
            "WeeklyPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=cdk.Duration.hours(8),
        )

        # Weekly cron — Mondays 02:00 UTC. Tune in-stack if cadence changes.
        events.Rule(
            self,
            "WeeklySchedule",
            schedule=events.Schedule.cron(minute="0", hour="2", week_day="MON"),
            targets=[targets.SfnStateMachine(self.state_machine)],
            description="Weekly trigger for the ml-uspto refresh pipeline",
        )

        cdk.CfnOutput(self, "StateMachineArn", value=self.state_machine.state_machine_arn)
        cdk.CfnOutput(self, "TaskSecurityGroupId", value=task_sg.security_group_id)
