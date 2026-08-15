---
title: "Three-region CockroachDB Standard — Terraform"
subtitle: "The cluster that makes apps/spindle/schema/024_regional_by_row.sql true."
status: "VALIDATED, NOT APPLIED — `terraform validate` and `terraform fmt` pass against cockroachdb/cockroach v1.22.0. No cluster has been created."
date: "2026-08-13"
---

## What this is

Three files of HCL that create one CockroachDB Standard cluster across
`us-east-1` (primary), `eu-west-1` and `ap-southeast-1`, one SQL user, and one allowlist
entry. That is the whole module.

It exists because `apps/spindle/schema/024_regional_by_row.sql` makes `contact_route`
`REGIONAL BY ROW` — one logical table whose rows are domiciled per data subject, so an EU
music director's email address is replicated in Ireland and not in Virginia. The
migration cannot be true without an EU region to be true in.

The operating procedure is `docs/runbooks/multiregion.md`. **Read it before applying**;
it has the abort path, the verification queries and the teardown, and this module's whole
lifecycle is about ninety minutes.

---

## The three decisions in it

**Standard, not Basic and not Advanced.** Basic supports multi-region but its capacity is
on-demand request units with no floor to reason about, and its interaction with
`SURVIVE REGION FAILURE` is not something this project has tested. Advanced prices three
regions as three sets of nodes — a multiple of the entire project budget. Standard
provisions capacity once, cluster-wide, which is the only tier where adding a region does
not multiply the bill.

**A new, throwaway cluster — not a conversion of the live one.** The live cluster is
`respect-the-funk` / `ae38b92e-c1ad-4a06-a247-489cd5ce9964`: Basic, single-region
`aws-us-east-1`, holding the system of record. Converting it is possible — Basic to
Standard is an in-place plan change and regions can be added afterwards — and it is a
one-way door. CockroachDB Cloud does not support removing a region from a Basic or
Standard cluster once added; the documented way back to single-region is backup, new
cluster, restore. A demo whose teardown step is "you can't" is a commitment, not a demo.
`import.tf.example` holds that path, disarmed, with the irreversibility written where
whoever renames it will read it.

**Exactly three regions, asserted in `variables.tf`.** `SURVIVE REGION FAILURE` raises the
replication factor from three to five, spread 2+2+1. Two regions cannot hold that. Two
regions would apply here perfectly happily and then fail at statement 9 of migration 024,
several minutes and several dollars later.

---

## Apply

```bash
export COCKROACH_API_KEY='CCDB1_...'      # service account, Cluster Admin. Not a tfvar.

cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars                  # your public IP; a throwaway password

terraform init
terraform plan -out=tfplan                # expect: 3 to add, 0 to change, 0 to destroy
terraform apply tfplan

export DATABASE_URL="$(terraform output -raw connection_string)"
```

Abort on any plan that says anything other than `0 to destroy`, or that mentions
`respect-the-funk`.

## Destroy — this is a step, not a cleanup

```bash
terraform destroy
ccloud cluster list          # the check that state was not lying
rm -f terraform.tfstate terraform.tfstate.backup terraform.tfvars
```

Standard bills provisioned vCPUs by the hour whether or not anything is connected:
~$0.18/hr for 2 vCPU, **$4.32/day, ~$130/month**, against a $10 project budget. Every
other module under `infra/` is priced so that idle costs nothing. This one is not, and
that is the only genuinely dangerous thing in this directory.

---

## Things that will bite

**Two spellings of the same region.** The Cloud API says `eu-west-1`. SQL says
`aws-eu-west-1`. `ALTER DATABASE ... ADD REGION` wants the SQL form and fails the short
form with "region not found", which reads like a provisioning failure rather than a
spelling difference. `terraform output sql_regions` prints the SQL form so the runbook
can be copied rather than retyped.

**State holds the SQL password in plaintext.** Terraform has no way not to write it.
Survivable only because both the password and the cluster live for one afternoon: use a
password from nowhere else and delete the state file with the cluster.

**No default allowlist entry.** `operator_cidr_ip` has no default, so Terraform prompts.
The alternative defaults are `0.0.0.0/0` — a public front door on a table of contact
details for named individuals — or a stale address that silently admits the wrong machine.
Neither is a default this repo will ship.

---

## Status, honestly

`terraform validate` passes and `terraform fmt` is clean against
`cockroachdb/cockroach v1.22.0`, whose resource schema was read with
`terraform providers schema -json` rather than from the registry documentation. Nothing
here has been planned against the CockroachDB Cloud API — there was no
`COCKROACH_API_KEY` in the environment where it was written, and obtaining one and
planning with it would have been the first step toward spending the budget.

So: reviewed and syntactically sound, not proven. The likeliest first-apply surprise is
that Standard multi-region cluster creation takes longer than the provider's default
timeout, or that the AWS region trio is not simultaneously available for Standard on this
organization's account. Both surface at `apply`, both are recoverable by
`terraform destroy`, and the runbook's §2 abort path is written for exactly that.
