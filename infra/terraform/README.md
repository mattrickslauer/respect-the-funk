---
title: "RemixKit — Terraform"
subtitle: "infra/architecture.pdf as HCL. Five services, no database tier, no compute floor."
status: "VALIDATED, NOT APPLIED — `tofu validate` passes; no AWS credentials were available to plan or apply."
date: "2026-07-27"
---

## What this is

infra/README ended with: *"A shape to approve, not code to run. Once agreed,
`infra/terraform/` gets: ECR + one image build, the Lambda/Function-URL pair, SQS +
DLQ, a Batch compute environment at `minvCpus: 0`, and the SSM parameters. That is a
genuinely small amount of HCL — which is the point."*

This is that, and it is still small: five modules, ~450 lines including comments.

| Module | infra/README | Why it costs nothing at rest |
|---|---|---|
| `api` | ① Lambda + Function URL | No provisioned concurrency |
| `queue` | ② SQS + DLQ | Pay-per-request, 1M/mo free |
| `worker` | ③ Batch on Fargate Spot | `min_vcpus = 0` |
| `secrets` | ④ SSM Parameter Store | Standard tier is free |
| `ecr` | image storage | ~cents, with a 10-image lifecycle rule |

⑤ **Backblaze B2 is not managed here.** Terraform destroying a bucket that holds every
master and every kit is a risk with no upside. Create it in the Backblaze console and
pass its name as `b2_bucket`.

**No VPC, no NAT.** The worker runs in the default VPC's public subnets with a public
IP. A private subnet would need a NAT gateway to reach B2 and the providers, and a NAT
is ~$32/month of pure idle floor — on its own, more than the rest of this architecture
costs combined.

---

## Apply

```bash
cd infra/terraform/envs/prod
cp terraform.tfvars.example terraform.tfvars   # set b2_bucket

terraform init
terraform apply -target=module.ecr             # the repo must exist before the push
```

Build and push both image tags — the API and the worker are the same image with
different entrypoints, so they cannot drift:

```bash
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"

docker build --target api    -t "$REPO:api-latest"    ../../../../app
docker build --target worker -t "$REPO:worker-latest" ../../../../app
docker push "$REPO:api-latest" && docker push "$REPO:worker-latest"

terraform apply
```

Then populate the secrets. Terraform creates them empty on purpose — values in state
would be plaintext:

```bash
aws ssm put-parameter --name /remixkit/prod/B2_KEY_ID  --type SecureString --value "…" --overwrite
aws ssm put-parameter --name /remixkit/prod/B2_APP_KEY --type SecureString --value "…" --overwrite
aws ssm put-parameter --name /remixkit/prod/GMI_API_KEY --type SecureString --value "…" --overwrite
```

`remixkit/bootstrap.py` reads them by path at process start and exports them as
`RK_*` (and unprefixed, for the keys Genblaze reads itself). A parameter still holding
its placeholder is skipped with a warning rather than passed to a provider as if it
were a credential.

```bash
terraform output console_url
```

---

## The one caveat worth repeating

**The generator is Batch, not Lambda — deliberately.** BUILD-SPEC §4 calls Genblaze
with `timeout=900`, which is *exactly* Lambda's 15-minute ceiling, with no headroom for
cold start, provider retries, or the upload afterwards. It would fail by silent timeout
on the most expensive path in the system. This is the one place the obvious GCP→AWS
mapping is actively wrong, and it is why there are two compute modules instead of one.

**Fargate Spot is safe only because jobs are idempotent.** `KitService.run` returns
early on a kit that is already `ready`, and the SQS `dedupe_key` is the kit id. Relax
that and Spot must become on-demand.

**SQS visibility timeout must exceed the worst-case kit.** It is 1200s against a 900s
generation cap. Too low and SQS redelivers a message that is still being worked, and a
second Batch job starts on the same kit.

---

## Status, honestly

`tofu validate` passes against the AWS provider v5 schema. It has **not** been planned
or applied: the AWS credentials on this machine are expired
(`InvalidClientTokenId`), so nothing here has touched a real account. Treat it as
reviewed-and-syntactically-sound infrastructure, not as proven — the first `apply` is
where argument-level surprises (IAM propagation, Batch service-role creation, image
platform mismatch on arm64 Macs) actually surface.

The likeliest first-apply snag: **build the images for `linux/amd64`.** Both Lambda and
Fargate run amd64 by default, and an image built on an Apple Silicon Mac without
`--platform linux/amd64` will push fine and fail at runtime.
