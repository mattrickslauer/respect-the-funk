---
title: "Three-region residency — runbook"
subtitle: "Stand up a 3-region CockroachDB Standard cluster, domicile contact_route per row, prove it, tear it down."
status: "WRITTEN, NOT EXECUTED — nothing in this file has been run. No cloud resource was created producing it."
date: "2026-08-13"
for: "2026-08-17, human-gated"
---

## What this does, in one paragraph

Creates a throwaway CockroachDB Standard cluster across `aws-us-east-1`,
`aws-eu-west-1` and `aws-ap-southeast-1`; applies the schema; applies
`apps/spindle/schema/024_regional_by_row.sql`, which makes `contact_route` `REGIONAL BY ROW`
keyed on a region computed from each contact's country; proves with SQL that an EU
contact's row is replicated in Ireland and a US contact's is not; proves that a contact
in an unmapped jurisdiction is **refused** rather than defaulted; and then deletes the
cluster. Total wall time about ninety minutes. Total cost about seventy cents, and about
$130/month if step 9 is skipped.

---

## 0. Before anything — the two gates

**Budget gate.** CockroachDB Standard bills provisioned vCPUs by the hour whether or not
a query runs. 2 vCPU is ~$0.18/hour → **$4.32/day, ~$130/month**. The project budget is
$10 in total. Do not create the cluster until you are ready to use it, and set a timer
for teardown before you run step 2.

```bash
# Do this first. Genuinely.
echo "TEARDOWN rtf-residency-demo" | at now + 3 hours 2>/dev/null || \
  echo "no atd — set a phone alarm for $(date -d '+3 hours' '+%H:%M')"
```

**Blast-radius gate.** This runbook never touches `respect-the-funk`
(`ae38b92e-c1ad-4a06-a247-489cd5ce9964`), the live Basic cluster holding the system of
record. §7 documents the in-place conversion of that cluster and explains why it is
**not** what we do on the day: adding a region to a CockroachDB Cloud cluster cannot be
undone, so converting the live cluster is a permanent decision taken to make a
temporary point.

```bash
# Confirm the starting state you are NOT going to change.
ccloud cluster list
```

```
NAME              ID                                    PLAN TYPE  ...  VERSION
respect-the-funk  ae38b92e-c1ad-4a06-a247-489cd5ce9964  BASIC      ...  v26.2.5
```

---

## 1. Credentials

The Terraform provider reads `COCKROACH_API_KEY` from the environment. It is not a
Terraform variable, because Terraform writes every variable it is given into plaintext
state and this key can delete clusters.

Create a service account and key in the Cloud console
(Organization → Access Management → Service Accounts) with the **Cluster Admin** role on
the organization, then:

```bash
export COCKROACH_API_KEY='CCDB1_...'
```

> This is the first thing in the runbook that creates anything. A service account and an
> API key are free. Delete the key in step 9 with the cluster.

---

## 2. Provision — the only step that spends money

```bash
cd infra/terraform/multiregion
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars          # your public IP, a throwaway password

terraform init
terraform plan -out=tfplan
```

**Read the plan before applying.** Expected:

```
Terraform will perform the following actions:

  # cockroach_allow_list.operator will be created
  # cockroach_cluster.residency will be created
  # cockroach_sql_user.app will be created

Plan: 3 to add, 0 to change, 0 to destroy.
```

**ABORT if the plan says anything other than `0 to destroy`.** Three creates and nothing
else is the only acceptable plan. Anything mentioning `respect-the-funk`, or any
`must be replaced`, means the wrong state file or the wrong module — stop.

```bash
terraform apply tfplan
```

Cluster creation for Standard is typically a few minutes. Expected tail:

```
Apply complete! Resources: 3 added, 0 changed, 0 destroyed.

Outputs:

cluster_id = "………-………-………"
cluster_name = "rtf-residency-demo"
region_hosts = {
  "ap-southeast-1" = "rtf-residency-demo-….ap-southeast-1.cockroachlabs.cloud"
  "eu-west-1"      = "rtf-residency-demo-….eu-west-1.cockroachlabs.cloud"
  "us-east-1"      = "rtf-residency-demo-….us-east-1.cockroachlabs.cloud"
}
sql_regions = [
  "aws-us-east-1",
  "aws-eu-west-1",
  "aws-ap-southeast-1",
]
teardown = "ccloud cluster delete rtf-residency-demo   # or: terraform destroy"
```

**The clock is now running.** Everything from here to step 9 is billable.

```bash
export DATABASE_URL="$(terraform output -raw connection_string)"
cd ../../..
```

The provider builds that string assuming the CockroachDB Cloud CA certificate is in the
default location. If `psql` answers `root certificate file … does not exist`, fetch it
once — `apply.py` uses psycopg and will hit the same wall:

```bash
mkdir -p ~/.postgresql
curl -o ~/.postgresql/root.crt \
  https://cockroachlabs.cloud/clusters/$(cd infra/terraform/multiregion && terraform output -raw cluster_id)/cert
```

### If provisioning fails — hard abort

There is exactly one correct response to a failed or half-finished apply, and it is not
to retry:

```bash
cd infra/terraform/multiregion
terraform destroy -auto-approve
ccloud cluster list                    # confirm rtf-residency-demo is gone
```

A `cockroach_cluster` that errored partway may still exist and still bill. `terraform
destroy` removes what state knows about; `ccloud cluster list` is the check that state
was not lying. If a cluster appears there that Terraform does not know about:

```bash
ccloud cluster delete rtf-residency-demo
```

Only then diagnose. Do not debug a running Standard cluster.

---

## 3. Apply the schema

A fresh cluster has an empty `defaultdb`. Migrations run in filename order; `005` drops
tables that do not exist yet, which `apply.py` reports as `gone` and permits.

```bash
for f in apps/spindle/schema/0*.sql; do
  echo "=== $f"
  python3 apps/spindle/schema/apply.py "$(basename "$f")" || break
done
```

Expected, per file:

```
=== apps/spindle/schema/001_tenant_artist.sql
-- 001_tenant_artist.sql is additive; no emptiness guards apply --

applying 001_tenant_artist.sql: 4 statements
    1 ok      CREATE TABLE IF NOT EXISTS tenant ( id UUID PRIMARY KEY DEFAULT gen_rand…
```

and for `005`:

```
-- carried over --
  tenant               0
  artist               0
  artist_profile       0
-- must be empty --
  artist_chunk         empty
  …
```

Stop and read if any file fails. This loop deliberately `break`s rather than carrying on:
a migration that failed halfway leaves the schema in a state the next file assumes it is
not in.

> **Note the ordering.** `024` is applied here too, against an *empty* `contact_route`,
> so both of its gates pass trivially. That is intentional: the gates are demonstrated in
> step 6 against real rows, not relied on to have fired here. If you want the stronger
> demo, see §6b.

---

## 4. Confirm the migration landed

```bash
psql "$DATABASE_URL" -c 'SHOW REGIONS FROM CLUSTER'
```

```
     region       | zones
------------------+--------
 aws-ap-southeast-1 | {…}
 aws-eu-west-1      | {…}
 aws-us-east-1      | {…}
(3 rows)
```

```bash
psql "$DATABASE_URL" -c 'SHOW REGIONS FROM DATABASE defaultdb'
psql "$DATABASE_URL" -c 'SHOW SURVIVAL GOAL FROM DATABASE defaultdb'
```

```
 database  | survival_goal
-----------+---------------
 defaultdb | region
```

`region`, not `zone`, is the assertion. `zone` means statement 9 did not take and the
cluster is surviving a single availability zone, which is what the live Basic cluster
already does and is not the point.

---

## 5. Seed three contacts, one per jurisdiction

Not a migration — this is demo data and it does not belong in `apps/spindle/schema/`. It is
also fabricated on purpose and says so in the row: `018` is explicit that putting a false
`contact_route` against a real broadcaster would poison the system of record.

```bash
psql "$DATABASE_URL" <<'SQL'
INSERT INTO tenant (name, slug) VALUES ('Residency demo', 'residency-demo')
  ON CONFLICT (slug) DO NOTHING;

WITH t AS (SELECT id FROM tenant WHERE slug = 'residency-demo')
INSERT INTO party (tenant_id, slug, name, kind, party_class, contact_state, status)
SELECT t.id, v.slug, v.name, 'organisation', 'counterparty', 'contactable', 'active'
  FROM t, (VALUES
      ('demo-station-us', 'Demo Station US (not a real station)'),
      ('demo-station-de', 'Demo Station DE (not a real station)'),
      ('demo-station-sg', 'Demo Station SG (not a real station)')
  ) AS v(slug, name)
  ON CONFLICT (tenant_id, slug) DO NOTHING;

WITH t AS (SELECT id FROM tenant WHERE slug = 'residency-demo')
INSERT INTO contact_route (tenant_id, party_id, channel, value, addressee,
                           provenance, state, source, written_by, contact_country)
SELECT t.id, p.id, 'email', v.email, 'music_director',
       'asserted', 'unverified', 'residency-demo', 'runbook', v.country
  FROM t
  JOIN party p ON p.tenant_id = t.id
  JOIN (VALUES
      ('demo-station-us', 'md@demo-us.invalid', 'US'),
      ('demo-station-de', 'md@demo-de.invalid', 'DE'),
      ('demo-station-sg', 'md@demo-sg.invalid', 'SG')
  ) AS v(slug, email, country) ON v.slug = p.slug
  ON CONFLICT (tenant_id, party_id, channel, value) DO NOTHING;
SQL
```

`.invalid` is reserved by RFC 2606 and can never resolve, so no address here can ever be
mailed even by accident.

---

## 6. The proof

### 6a. The row knows where it lives

```bash
psql "$DATABASE_URL" -c \
  "SELECT value, contact_country, residency_region FROM contact_route ORDER BY 2"
```

```
       value        | contact_country |  residency_region
--------------------+-----------------+--------------------
 md@demo-de.invalid | DE              | aws-eu-west-1
 md@demo-sg.invalid | SG              | aws-ap-southeast-1
 md@demo-us.invalid | US              | aws-us-east-1
```

Nothing in the `INSERT` said `aws-eu-west-1`. The application supplied a country; the
database derived the home.

### 6b. An unplaceable contact is refused, not defaulted

This is the beat that matters. `CN` is a real ISO country code, it passes the shape
`CHECK`, and it is deliberately not in the map because Singapore does not satisfy China's
PIPL localisation requirement.

```bash
psql "$DATABASE_URL" -c \
  "INSERT INTO contact_route (tenant_id, party_id, channel, value, provenance, source, contact_country)
   SELECT t.id, p.id, 'email', 'md@demo-cn.invalid', 'asserted', 'residency-demo', 'CN'
     FROM tenant t JOIN party p ON p.tenant_id = t.id
    WHERE t.slug = 'residency-demo' AND p.slug = 'demo-station-us'"
```

```
ERROR:  null value in column "residency_region" violates not-null constraint
```

Try `'AL'` next — Alabama's FCC state code, Albania's ISO country code — and it fails the
same way. A schema with an `ELSE 'aws-us-east-1'` would have accepted both silently.

### 6c. The storage layer is honouring it — metadata proof

This is the proof to lead with, because it is pure catalogue metadata and works
regardless of what a serverless cluster will tell you about ranges.

```bash
psql "$DATABASE_URL" -c 'SHOW CREATE TABLE contact_route' | tail -5
```

```
… LOCALITY REGIONAL BY ROW AS residency_region
```

```bash
psql "$DATABASE_URL" -c \
  'SHOW ZONE CONFIGURATION FROM PARTITION "aws-eu-west-1" OF TABLE contact_route'
```

```
 num_replicas = 5,
 constraints = '{+region=aws-eu-west-1: 2, +region=aws-us-east-1: 2}',
 lease_preferences = '[[+region=aws-eu-west-1]]'
```

Read that line out loud on the day: **two replicas pinned in Ireland and the leaseholder
required to be in Ireland, for the partition holding every EU contact.** Five replicas
rather than three is `SURVIVE REGION FAILURE` doing what it says. Contrast with:

```bash
psql "$DATABASE_URL" -c \
  'SHOW ZONE CONFIGURATION FROM PARTITION "aws-us-east-1" OF TABLE contact_route'
```

> **UNVERIFIED**: the exact rendering of `constraints` and `lease_preferences` above is
> predicted from the documented behaviour of `REGIONAL BY ROW` under a `REGION` survival
> goal. The shape — `num_replicas = 5`, a `+region=` constraint naming the partition's
> region, and a lease preference for it — is what to check. If the numbers differ, the
> argument does not.

### 6d. The physical proof, if it is available

```bash
psql "$DATABASE_URL" -c \
  'SELECT start_key, replica_localities, lease_holder_locality
     FROM [SHOW RANGES FROM TABLE contact_route WITH DETAILS]'
```

Expected: a range whose `start_key` begins with `/"aws-eu-west-1"` and whose
`replica_localities` include `region=eu-west-1`.

> **UNVERIFIED, and the likeliest thing to fail on the day.** `SHOW RANGES` reports on
> the KV layer. On a CockroachDB Standard cluster the KV layer is shared multi-tenant
> infrastructure, and range and replica metadata may be host-cluster property that a
> tenant is not permitted to read — in which case this returns a permission error or an
> empty result. **This is why 6c comes first.** 6c is the proof; 6d is the bonus. Do not
> rehearse the demo around 6d.

### 6e. Capture it, because §9 destroys the subject

Run this **before teardown**. It executes every proof above against the live throwaway
cluster and writes a timestamped transcript to `docs/evidence/`:

```bash
apps/spindle/bin/multiregion_evidence.sh
```

The gap it closes is structural rather than clerical. §9 deletes the cluster, and from
that moment every claim in §6 stops being checkable — what remains is somebody's
recollection of some `psql` output. This repository's standing rests on its numbers being
executed rather than asserted, and the one demonstration whose subject is destroyed at
the end is the one most in need of a written record made while the subject still exists.

Three properties worth knowing before it runs:

- **It refuses the production cluster by ID**, not by URL spelling — `respect-the-funk`
  is matched on `ae38b92e-…` so a pasted connection string cannot get past it. §0's
  blast-radius gate, enforced rather than remembered.
- **It refuses a single-region cluster.** A transcript captured against one region would
  faithfully record the absence of the thing it claims to demonstrate, which is worse
  than no transcript because it is citable.
- **§6b passes by failing.** The unplaceable-country insert *must* be rejected, so that
  proof is run separately and a *successful* insert fails the run. Every other proof
  fails the run if it errors, and the script exits non-zero rather than leaving a
  transcript that reads like a result.

§6d's absence is recorded and does not fail the run, for the reason stated directly
above: it is the bonus, not the proof.

---

## 7. The path this runbook does not take

For the record, and because it is what a customer would actually do. Converting the live
cluster in place:

```bash
# 1. Basic -> Standard. Zero-downtime, in-place, same region only.
ccloud cluster update respect-the-funk --provisioned-vcpus 2

# 2. Only then, add regions. Region changes require the plan change to have landed.
ccloud cluster update respect-the-funk us-east-1 eu-west-1 ap-southeast-1 \
       --primary-region us-east-1
```

Both flags are present in `ccloud 0.8.23` (`ccloud cluster update --help`, checked
2026-08-13). Neither command has been run.

**Why not on the day.** *"You cannot remove a region once it has been added"* — the Cloud
docs for both Basic and Standard cluster management. Step 2 is permanent. The way back to
a single-region cluster is backup, create a new cluster, restore, repoint every
`DATABASE_URL`, delete the old one. That is a data migration performed under time
pressure on the system of record, to undo something done for a ninety-minute
demonstration. The throwaway cluster costs the same and deletes cleanly.

---

## 8. Resume and reversal

### Resume — `024` failed partway

`apply.py` runs with `autocommit=True` and no transaction. A failure at statement N
leaves 1…N−1 applied. It prints the number, so resume is mechanical.

| Failed at | What it means | What to do |
|---|---|---|
| 5 (`contact_country SET NOT NULL`) | Some route has no country. | Run the query below, decide each one, `UPDATE`, re-run the whole file. |
| 6 (`SET PRIMARY REGION`) | The region is not on the cluster. | Check `SHOW REGIONS FROM CLUSTER`. Provisioning did not finish. |
| 7 or 8 (`ADD REGION`) | Usually "region already added" on a re-run. | Comment those two lines out and re-run. They are the only non-idempotent statements in the file. |
| 11 (`residency_region SET NOT NULL`) | Some row's country is not on any of the three lists. | Run the second query below. Decide the jurisdiction, then either fix the country or extend the map in a new migration. |

```sql
-- Which routes have no country at all
SELECT id, source, written_by, addressee, value
  FROM contact_route WHERE contact_country IS NULL;

-- Which countries the map cannot place (run before statement 11)
SELECT contact_country, count(*)
  FROM contact_route WHERE residency_region IS NULL
 GROUP BY 1 ORDER BY 2 DESC;
```

Neither of these is a licence to pick a region. A row in the second query is a row whose
jurisdiction nobody has researched; the fix is a person reading a statute and a new
migration, or deleting demo data. It is not an `UPDATE` to `'aws-us-east-1'`.

### Reversal

The migration is reversible; the cluster's regions are not.

```sql
-- Statements 12, 11, 10
ALTER TABLE contact_route SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE contact_route DROP COLUMN residency_region;

-- Statement 9. Must precede any DROP REGION: a database on the REGION survival goal
-- with exactly three regions cannot give one up.
ALTER DATABASE defaultdb SURVIVE ZONE FAILURE;

-- Statements 8, 7. Not the primary region — that one cannot be dropped while others
-- remain.
ALTER DATABASE defaultdb DROP REGION "aws-ap-southeast-1";
ALTER DATABASE defaultdb DROP REGION "aws-eu-west-1";

-- Statements 3, 1. Optional; contact_country is a good column regardless of locality.
ALTER TABLE contact_route DROP CONSTRAINT IF EXISTS route_country_shape;
ALTER TABLE contact_route DROP COLUMN contact_country;
```

This script is here rather than in `024` on purpose: `apply.py` scans the raw text of a
migration — comments included — for `DROP TABLE|COLUMN|DATABASE` and refuses to run any
file containing one without an entry in its `DESTRUCTIVE` table. Quoting the reversal
inside `024` would make `024` unappliable. The guard is right and the comment would be
the bug.

Note what reversal does **not** undo: the three regions on the *cluster*. Only the
database's use of them.

---

## 9. Teardown — mandatory

Not optional, not "when convenient". The cluster bills $4.32/day against a $10 project
budget: forgetting it exhausts the budget in two days and eight hours.

```bash
cd infra/terraform/multiregion
terraform destroy
```

```
Plan: 0 to add, 0 to change, 3 to destroy.
…
Destroy complete! Resources: 3 destroyed.
```

Then verify against the API rather than against Terraform's opinion of the API:

```bash
ccloud cluster list
```

```
NAME              ID                                    PLAN TYPE  ...
respect-the-funk  ae38b92e-c1ad-4a06-a247-489cd5ce9964  BASIC      ...
```

**One row. If `rtf-residency-demo` is still listed, Terraform's state is wrong and the
cluster is still billing:**

```bash
ccloud cluster delete rtf-residency-demo
```

Then finish the job:

```bash
rm -f terraform.tfstate terraform.tfstate.backup terraform.tfvars
unset COCKROACH_API_KEY DATABASE_URL
```

`terraform.tfstate` holds the SQL user's password in plaintext — Terraform has no way not
to write it — and `terraform.tfvars` holds it too. Both are gitignored and both should be
gone. Delete the Cloud service-account API key in the console as the last act.

---

## Cost, honestly

| Line | Basis | 3-hour window |
|---|---|---|
| Standard compute, 2 vCPU | ~$0.18/hr, billed on provisioned capacity whether idle or not | **$0.54** |
| Provisioning + verification slack (~30 min) | same rate | $0.09 |
| Storage | <1 GiB logical × 5 replicas under `SURVIVE REGION FAILURE` | <$0.01 |
| Cross-region transfer | three seed rows and a handful of metadata queries | ~$0.00 |
| **Total** | | **~$0.65** |

Against a $10 budget that is comfortable **only** because of step 9. The realistic
overrun is not the demo — it is creating the cluster the night before "to be safe", which
adds twelve to eighteen hours at $0.18/hr, or $2.16–$3.24, and turns a 6.5% spend into
40%. Provision on the day.

The $0.18/hr figure is list price for 2 provisioned vCPUs on Standard as understood on
2026-08-13. It is **not** verified against an invoice, because no such cluster has been
created.

---

## What could not be verified without provisioning

Stated plainly, because this repo's standard is that a claim is checked against the
running cluster and these were not:

1. **That `SHOW RANGES FROM TABLE ... WITH DETAILS` returns replica localities to a
   tenant on Standard.** Serverless tenants share the KV layer. §6d may simply not work.
2. **That `ALTER DATABASE defaultdb SET PRIMARY REGION` is idempotent** when the region
   is already primary, which is what makes statement 6 of `024` safe to re-run.
3. **Whether adding a region at the cluster level automatically adds it to `defaultdb`**,
   which would make statements 7 and 8 fail with "region already added" on a *first* run
   rather than only on a re-run. Harmless either way, but it changes which line the
   `break` in step 3 lands on.
4. **That a `STORED` computed column of type `crdb_internal_region` is accepted as the
   `REGIONAL BY ROW AS` column**, and that the `::crdb_internal_region` casts inside the
   `CASE` are the form the parser wants.
5. **The current contents of `contact_route` on the live cluster.** No `DATABASE_URL` was
   available while writing this, so the backfill in statement 4 of `024` is scoped by
   source rather than sized by a count.
6. **The Basic → Standard in-place plan change and the region addition in §7.** Both are
   documented; neither has been executed, and executing either on the live cluster is
   irreversible in the region case.

## The single most likely way this fails on the day

`SHOW RANGES` returns nothing useful, and the physical replica placement — the picture
everyone wants to see — is unavailable on a serverless tier. Rehearse §6c. The zone
configuration on the `aws-eu-west-1` partition, showing `num_replicas = 5`, a
`+region=eu-west-1` constraint and a lease preference for Ireland, is a *complete* proof
of domiciling, it is pure SQL metadata, and it will be there.
