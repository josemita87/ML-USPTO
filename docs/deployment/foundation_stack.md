# Foundation Stack — Architecture

Visual companion to `infra/stacks/foundation.py`. The stack owns the **long-lived** primitives (data, image registry, credentials, identity); a future phase-2 stack will own the **ephemeral** orchestration (schedule + execution).

---

## 1. Resource map

```mermaid
flowchart TB
    subgraph dev[Dev workstation - manual one-shot]
        seed[seed_secret.py]
        sync[aws s3 sync data to s3]
        push[docker push to ECR]
    end

    subgraph foundation[FoundationStack - RETAIN on destroy]
        bucket[S3 DataBucket<br/>raw/ + processed/<br/>SSE-S3, SSL-only]
        ecr[ECR DriverImage<br/>scan-on-push<br/>keep last 10]
        secret[Secrets Manager<br/>UsptoApiKey<br/>empty shell]
        role[IAM DriverTaskRole<br/>assumed by ecs-tasks]

        role -- grant_read_write --> bucket
        role -- grant_read --> secret
    end

    subgraph outputs[CfnOutputs - seam to phase 2]
        out1[BucketName]
        out2[RepositoryUri]
        out3[ApiKeySecretArn]
        out4[TaskRoleArn]
    end

    bucket --> out1
    ecr --> out2
    secret --> out3
    role --> out4

    seed -. writes value .-> secret
    sync -. seeds cache .-> bucket
    push -. publishes image .-> ecr
```

---

## 2. Runtime path (phase 2, not yet implemented)

```mermaid
flowchart LR
    eb[EventBridge<br/>weekly cron] --> sfn[Step Functions<br/>state machine]
    sfn --> task[ECS Fargate task<br/>one per stage driver]

    task -- assumes --> role[DriverTaskRole]
    task -- pulls image --> ecr[ECR DriverImage]
    task -- reads/writes --> bucket[S3 DataBucket]
    task -- reads secret --> secret[UsptoApiKey]
```

Each weekly run is one Step Functions execution; each stage (trials → petitions/decisions → patents → joiner → features) is one Fargate task with a different `python -m drivers.<X>` argv. All tasks share the same image, role, bucket, and secret — that's why those four resources are foundation-owned.

---

## 3. Why the split

| Concern | Phase 1 (foundation) | Phase 2 (compute/orchestration) |
|---|---|---|
| Durable state | data bucket, image registry, secret | — |
| Identity | task role | execution role |
| Schedule / glue | — | EventBridge, Step Functions, task definitions |
| Removal policy | RETAIN everywhere | DESTROY (safe — no state) |

The split lets you `cdk destroy` the orchestration stack freely while iterating, without risking the cached data or the secret value.

---

## 4. What's deliberately not here

- **No VPC** — Fargate tasks need only HTTPS egress to ODP; default account VPC + a security group is enough. A custom VPC would add NAT gateway costs (~$32/mo) for no security gain.
- **No KMS CMK** — S3-managed keys are free and adequate for this data class.
- **No compute** — task definitions, Step Functions, and EventBridge live in the future phase-2 stack and import the four `CfnOutput`s above.
