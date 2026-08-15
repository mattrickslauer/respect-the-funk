# The three-region cluster that makes row-level residency a database property.
#
# This is the infrastructure half of `apps/spindle/schema/024_regional_by_row.sql`. The
# migration says "an EU contact's row lives in the EU"; nothing in the migration can be
# true unless the cluster has an EU region to put it in, and unless the database is
# willing to lose one of them. That is what this file provisions and nothing else.
#
# ## Why this is a separate root module and not an addition to envs/prod
#
# `envs/prod` is RemixKit's AWS estate — Lambda, SQS, Batch, SSM. It has never held a
# database tier, on purpose (see infra/README): the database is CockroachDB Cloud, which
# is a different provider, a different credential, and a different blast radius. Putting
# the cluster in the same state file as the compute would mean that a `terraform destroy`
# aimed at a Lambda could take the system of record with it. They stay apart.
#
# ## Why this creates a new cluster instead of converting the live one
#
# The live cluster is `respect-the-funk` / ae38b92e-c1ad-4a06-a247-489cd5ce9964, Basic,
# single region aws-us-east-1. Converting it is possible — Basic to Standard is an
# in-place plan change, and regions can then be added — but it is a **one-way door**:
# CockroachDB Cloud does not support removing a region from a Basic or Standard cluster
# once it has been added. Verified from the Cloud docs on 2026-08-13; not verifiable
# against the cluster without doing it, which is exactly the point.
#
#   https://www.cockroachlabs.com/docs/cockroachcloud/basic-cluster-management
#   ("You cannot remove a region once it has been added.")
#
# A demo whose teardown step is "you can't" is not a demo, it is a commitment. So the
# demo runs on a cluster that exists to be deleted, and the live cluster is left alone.
# `import.tf.example` in this directory holds the other path — adopting the live cluster
# — for the day the product genuinely wants multi-region, with the irreversibility
# spelled out where whoever uncomments it will read it.
#
# ## Cost, because this one actually has a floor
#
# Everything else in infra/ is priced so that idle costs nothing. This is not that.
# CockroachDB Standard bills provisioned vCPUs by the hour whether or not a query runs:
# 2 vCPU is roughly $0.18/hour, which is ~$4.30/day and ~$130/month. The project budget
# is $10 total. The cluster must therefore be created at the start of the demo window
# and destroyed at the end of it, and `docs/runbooks/multiregion.md` treats teardown as
# a step rather than an afterthought.

terraform {
  required_version = ">= 1.6"

  required_providers {
    cockroach = {
      source  = "cockroachdb/cockroach"
      version = "~> 1.22"
    }
  }

  # Local state, deliberately. This module's whole lifecycle is a few hours long and
  # single-operator; a remote backend would be one more thing to provision for a cluster
  # that is going to be deleted the same afternoon. If a second person ever applies this,
  # move the state first — two people applying against local state is how infrastructure
  # gets destroyed by accident.
}

# Credentials come from COCKROACH_API_KEY in the environment. Not a variable, and not in
# a tfvars file: a Cloud API key can create and delete clusters, and Terraform writes
# every variable it is given into plaintext state.
provider "cockroach" {}

locals {
  # Ordered primary-first for readability only; `primary` below is what decides.
  regions = [
    { name = var.primary_region, primary = true },
    { name = var.eu_region, primary = false },
    { name = var.apac_region, primary = false },
  ]
}

# ---------------------------------------------------------------- the cluster
#
# Standard, not Advanced, and not Basic.
#
#   Basic    supports multi-region, but capacity is on-demand request units and there is
#            no provisioned floor to reason about. It also cannot be relied on for
#            SURVIVE REGION FAILURE — the serverless system database is configured
#            SURVIVE ZONE and the interaction is not something this project has tested.
#   Standard is the tier Cockroach Labs points at multi-region: capacity is provisioned
#            once, cluster-wide, so adding a region does not triple the bill the way
#            Advanced's per-region node counts do.
#   Advanced would price three regions as three sets of nodes. For a demo that is a
#            multiple of the entire project budget.
#
# `regions` carries exactly three entries because SURVIVE REGION FAILURE needs three:
# the survival goal raises the replication factor from 3 to 5 and spreads the replicas
# 2+2+1, which cannot be done across two. Two regions would apply cleanly here and then
# fail at `ALTER DATABASE ... SURVIVE REGION FAILURE` in migration 024, several minutes
# and several dollars later, so the count is asserted in variables.tf instead.
resource "cockroach_cluster" "residency" {
  name           = var.cluster_name
  cloud_provider = "AWS"
  plan           = "STANDARD"

  regions = local.regions

  serverless = {
    # AUTOMATIC because nobody is going to be watching a cluster that lives for an
    # afternoon. MANUAL is the right answer for the live cluster and the wrong one here.
    upgrade_type = "AUTOMATIC"

    usage_limits = {
      # The whole compute bill. Two vCPUs is the smallest Standard configuration and is
      # ample for a demo whose largest table is a few thousand radio stations.
      provisioned_virtual_cpus = var.provisioned_vcpus

      # A hard ceiling on storage, so a runaway ingest cannot turn a $0.54 demo into a
      # storage bill. Note that SURVIVE REGION FAILURE stores five replicas rather than
      # three, so the billable footprint is ~1.67x the logical data size.
      storage_mib_limit = var.storage_mib_limit
    }
  }

  # Explicitly false. This cluster exists to be deleted, and delete protection on it
  # would be a footgun pointed at the budget rather than at the data. The live cluster is
  # the one that should carry protection, and it is not managed here.
  delete_protection = false

  # No `lifecycle { prevent_destroy = true }` for the same reason. Teardown is mandatory.

  labels = {
    project = "respect-the-funk"
    purpose = "residency-demo"
    # Left as a plain label rather than a tag with meaning: nothing reads this, it is
    # here so a human looking at the Cloud console can tell in one glance which cluster
    # is the ephemeral one.
    ephemeral = "true"
  }
}

# ---------------------------------------------------------------- reaching it
#
# A Standard cluster is created with a permissive default allowlist. That is convenient
# and wrong for anything holding contact details for named people, which is what
# `contact_route` is. The allowlist below is the only way in, and it has no default
# value — supply your address, or Terraform stops and asks.
resource "cockroach_allow_list" "operator" {
  cluster_id = cockroach_cluster.residency.id
  name       = "operator"
  cidr_ip    = var.operator_cidr_ip
  cidr_mask  = var.operator_cidr_mask
  sql        = true
  ui         = true
}

resource "cockroach_sql_user" "app" {
  cluster_id = cockroach_cluster.residency.id
  name       = var.sql_user_name
  password   = var.sql_user_password
}

# The connection string the migration runner needs. Computed by the provider rather than
# assembled by hand, because the host is per-region and getting it wrong produces a
# confusing TLS error rather than a clear one.
data "cockroach_connection_string" "app" {
  id       = cockroach_cluster.residency.id
  sql_user = cockroach_sql_user.app.name
  password = var.sql_user_password
  database = "defaultdb"
  os       = "LINUX"
}
