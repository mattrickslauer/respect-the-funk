#!/usr/bin/env python3
"""Workload and unit-economics model for the MEMORY-SPEC.md memory tier.

    python3 workload.py            # -> memory-workload.pdf + memory-workload.csv

Answers the question infra/README.md and MEMORY-SPEC.md both leave open: what does the
CockroachDB memory branch actually cost to run, and what does it have to buy to be worth
it. MEMORY-SPEC §10 decision 2 is explicit that the idle number was never checked; §8
refuses to restate the "under $1/month" claim until it was. This file checks it.

Three outputs, from one set of constants, for the same reason diagram.py generates its
pictures: the prose, the chart and the AWS line items cannot drift from each other if
they are all computed here.

  memory-workload.pdf     the visual — workload drivers, unit economics, AWS estimate
  memory-workload.csv     the AWS workload estimate, in AWS Pricing Calculator's own
                          export schema (Service / Description / Specs / Monthly / 12mo)
  stdout                  the same tables, for diffing when a rate changes

House rule inherited from research/07-cost-model: price it, date it, link it. Every rate
below carries its source and its verification date. Anything that could not be read from
a primary source is marked UNVERIFIED in-line and again in the PDF — not quietly rounded.

Requires: weasyprint on PATH (`brew install weasyprint`). No Python deps.
"""

import csv
import html
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
VERIFIED = "2026-07-26"

# ---------------------------------------------------------------------------
# RATES — us-east-1. Everything marked [API] was read from the AWS Price List
# Bulk API on 2026-07-26, which is AWS's own primary source and does not have the
# JS-rendering problem research/07-cost-model hit on the HTML pricing pages.
# ---------------------------------------------------------------------------

# [API] AmazonBedrock/us-east-1 — USE1-TitanEmbeddingV2-Text-input-tokens
TITAN_TEXT_PER_MTOK = 0.02
TITAN_TEXT_BATCH_PER_MTOK = 0.01          # ...-input-tokens-batch
# [API] USE1-TitanEmbeddingsG1-Image-input-image (Nova Multimodal standard-image is the
# same $0.00006). This is the candidate answer to MEMORY-SPEC §10 decision 3 — a metric
# image embedding, rather than the vision model's *judgement* check_likeness.py uses now.
IMAGE_EMBED_PER_IMAGE = 0.00006
# [API] USE1-NovaLite-input-tokens / -output-tokens. The agent runtime for Q4.
NOVA_LITE_IN_PER_MTOK = 0.06
NOVA_LITE_OUT_PER_MTOK = 0.24

# [API] AWSLambda/us-east-1
LAMBDA_GB_S = 0.0000166667
LAMBDA_PER_M_REQ = 0.20
LAMBDA_FREE_REQ = 1_000_000
LAMBDA_FREE_GB_S = 400_000
# [API] AWSQueueService/us-east-1 — Requests-RBP, first 1M/mo free
SQS_PER_M_REQ = 0.40
SQS_FREE_REQ = 1_000_000
# [API] AmazonECS/us-east-1 — USE1-Fargate-vCPU-Hours / -GB-Hours (x86 on-demand)
FARGATE_VCPU_HR = 0.04048
FARGATE_GB_HR = 0.004445
# Fargate Spot is NOT in the Price List API — Spot rates are dynamic by design. 70% off
# on-demand is the commonly-cited steady-state discount, and it is an assumption here.
FARGATE_SPOT_DISCOUNT = 0.70              # UNVERIFIED — no published rate exists
# [API] AmazonECR/us-east-1 — TimedStorage-ByteHrs
ECR_GB_MO = 0.10
# infra/README.md carries CloudWatch as "~cents" at 14-day retention. Not re-derived.
CLOUDWATCH_MO = 0.15                      # UNVERIFIED — order-of-magnitude placeholder

# research/07-cost-model, verified 2026-07-15. B2 is unchanged by this branch.
B2_GB_MO = 0.00695

# cockroachlabs.com/pricing/new, read 2026-07-26: Basic "starts at $0 / month",
# "50 million RUs and 10 GiB storage free per month", "scales to zero", and the
# distributed vector index is listed on all plans including Basic.
CRDB_FREE_RU = 50_000_000
CRDB_FREE_STORAGE_GIB = 10
# Per-unit overage rates past the free allowance are NOT published on the pricing page
# or the costs doc — both defer to a quote. These two are third-party-derived and are
# used only to price the overage case, never the headline.
CRDB_PER_M_RU = 0.20                      # UNVERIFIED — third-party
CRDB_STORAGE_GIB_MO = 0.50                # UNVERIFIED — third-party

# content/bin/generate_stills.py PRICES_USD, dated 2026-07 by the repo itself. This is
# the denominator the whole memory tier is trying to move.
STILL_GEN_PER_IMAGE = 0.04                # gemini-2.5-flash-image
# check_likeness.py calls gemini-2.5-flash once per (still x cast member) with a rubric
# and reference crops. ~2k in + 300 out at Gemini 2.5 Flash $0.30/$2.50 per MTok
# (research/07-cost-model, verified 2026-07-15).
LIKENESS_JUDGE_PER_CALL = 2000 / 1e6 * 0.30 + 300 / 1e6 * 2.50

# ---------------------------------------------------------------------------
# MEASURED — counted out of this repository on 2026-07-26, not assumed.
# ---------------------------------------------------------------------------
M_LIBRARY_CLIPS = 28                      # lib/hooks/* (13) + lib/stock/nocturnal (15)
M_PROSE_TOKENS_MEAN = 47                  # title+logline+audio.quote+beats[].means, /4
M_PROSE_TOKENS_P95 = 76
M_DESCRIPTOR_BYTES = 2310                 # one rtf.clip/v1 as JSON
M_STILLS_PER_EDIT = 22                    # payoff stills; 5 existing edits all 22-25
M_EDITS_EXISTING = 5
M_REF_FRAMES_PER_ARTIST = 5               # MEMORY-SPEC §2: "5 framings/lighting setups"
M_CAST_PER_EDIT = 2                       # anthony + cosima

EMBED_TOKENS = 64                         # budget at ~p95, not the mean
VEC_TEXT_BYTES = 1024 * 4                 # VECTOR(1024), float32
VEC_FACE_BYTES = 512 * 4                  # VECTOR(512)
CLIP_ROW_BYTES = M_DESCRIPTOR_BYTES + VEC_TEXT_BYTES + 200
EPISODE_ROW_BYTES = 500 + 800 + 300 + VEC_FACE_BYTES   # prompt, likeness_table, params

# Ops issued against the tier. Q1+Q2 ANN, one identity/negatives read, one episode
# write, one Q3 distance = 5 per attempt; Q4 catalog work is per-edit.
OPS_PER_ATTEMPT = 5
OPS_PER_EDIT = 20
AGENT_QUERIES_PER_EDIT = 10
AGENT_IN_TOK, AGENT_OUT_TOK = 4000, 500

# Generator container: 2 vCPU / 4 GB, ~15 min per edit. Mostly spent waiting on the
# image endpoint, which bills the container anyway. Engineering estimate, not an SLA.
GEN_VCPU, GEN_GB, GEN_HOURS_PER_EDIT = 2, 4, 0.25


# ---------------------------------------------------------------------------
# TIERS
# ---------------------------------------------------------------------------
class Tier:
    def __init__(self, key, name, blurb, tenants, artists, songs, real_clips,
                 synthetic_clips, edits_per_mo, attempts):
        self.key, self.name, self.blurb = key, name, blurb
        self.tenants, self.artists, self.songs = tenants, artists, songs
        self.real_clips, self.synthetic_clips = real_clips, synthetic_clips
        self.edits_per_mo, self.attempts = edits_per_mo, attempts

    @property
    def clips(self):
        return self.real_clips + self.synthetic_clips


TIERS = [
    Tier("t0", "T0 · Demo",
         "What exists on 2026-08-18, plus the synthetic corpus MEMORY-SPEC §9 commits to "
         "for exercising the index. Every count here is measured out of the repo.",
         tenants=1, artists=2, songs=1, real_clips=M_LIBRARY_CLIPS,
         synthetic_clips=5_000, edits_per_mo=5, attempts=3.0),
    Tier("t1", "T1 · Pilot",
         "Respect the Funk as tenant #1 at the scale PRODUCT.md names as the real "
         "bottleneck: fifty songs, where manual onboarding — not compute — throttles.",
         tenants=1, artists=5, songs=50, real_clips=2_000,
         synthetic_clips=0, edits_per_mo=20, attempts=2.2),
    Tier("t2", "T2 · Platform",
         "Ten labels on one engine. The multi-tenant case BUILD-SPEC §2b rule 6 "
         "partitions for, and the first tier where the free allowance is a real question.",
         tenants=10, artists=50, songs=500, real_clips=50_000,
         synthetic_clips=0, edits_per_mo=200, attempts=1.8),
]


def compute(t: Tier) -> dict:
    """One tier's steady-state month. Ingest is amortised over 12 months where it is a
    one-time backfill, because that is how it actually lands on a bill."""
    r = {}

    # ---- volumes ----------------------------------------------------------
    stills_mo = t.edits_per_mo * M_STILLS_PER_EDIT
    attempts_mo = stills_mo * t.attempts
    r["stills_mo"] = stills_mo
    r["attempts_mo"] = attempts_mo
    r["judgements_mo"] = attempts_mo * M_CAST_PER_EDIT
    r["ops_mo"] = attempts_mo * OPS_PER_ATTEMPT + t.edits_per_mo * OPS_PER_EDIT

    # ---- one-time ingest, amortised over 12 months ------------------------
    ingest_tok = t.clips * EMBED_TOKENS
    ingest_embed = ingest_tok / 1e6 * TITAN_TEXT_BATCH_PER_MTOK      # backfill batches
    ingest_faces = t.artists * M_REF_FRAMES_PER_ARTIST * IMAGE_EMBED_PER_IMAGE
    r["ingest_once"] = ingest_embed + ingest_faces
    r["ingest_mo"] = r["ingest_once"] / 12

    # ---- recurring Bedrock ------------------------------------------------
    # Q1/Q2 issue a query embedding per attempt; Q3 embeds the generated still.
    query_tok = attempts_mo * 2 * EMBED_TOKENS
    r["bedrock_text"] = query_tok / 1e6 * TITAN_TEXT_PER_MTOK
    r["bedrock_image"] = attempts_mo * IMAGE_EMBED_PER_IMAGE
    agent_q = t.edits_per_mo * AGENT_QUERIES_PER_EDIT
    r["bedrock_agent"] = (agent_q * AGENT_IN_TOK / 1e6 * NOVA_LITE_IN_PER_MTOK
                          + agent_q * AGENT_OUT_TOK / 1e6 * NOVA_LITE_OUT_PER_MTOK)
    r["bedrock"] = (r["bedrock_text"] + r["bedrock_image"]
                    + r["bedrock_agent"] + r["ingest_mo"])

    # ---- CockroachDB Basic ------------------------------------------------
    clip_gib = t.clips * CLIP_ROW_BYTES / 1024**3
    ep_gib_yr = attempts_mo * 12 * EPISODE_ROW_BYTES / 1024**3
    ident_gib = t.artists * (1 + M_REF_FRAMES_PER_ARTIST) * VEC_FACE_BYTES / 1024**3
    r["storage_gib"] = clip_gib + ep_gib_yr + ident_gib
    r["storage_pct_free"] = r["storage_gib"] / CRDB_FREE_STORAGE_GIB * 100
    # The honest RU statement is an inversion, not a point estimate: CockroachDB does
    # not publish an RU cost for a vector-index scan, so rather than invent one we state
    # how expensive a single operation would have to be before the free tier breaks.
    r["ru_break_even"] = CRDB_FREE_RU / r["ops_mo"] if r["ops_mo"] else float("inf")
    over_gib = max(0.0, r["storage_gib"] - CRDB_FREE_STORAGE_GIB)
    r["crdb"] = over_gib * CRDB_STORAGE_GIB_MO

    # ---- the rest of the AWS estimate -------------------------------------
    fg_hr = t.edits_per_mo * GEN_HOURS_PER_EDIT
    r["fargate_hours"] = fg_hr
    r["fargate"] = (fg_hr * (GEN_VCPU * FARGATE_VCPU_HR + GEN_GB * FARGATE_GB_HR)
                    * (1 - FARGATE_SPOT_DISCOUNT))
    web_req = t.edits_per_mo * 200 * t.tenants
    r["lambda"] = (max(0, web_req - LAMBDA_FREE_REQ) / 1e6 * LAMBDA_PER_M_REQ
                   + max(0, web_req * 0.4 - LAMBDA_FREE_GB_S) * LAMBDA_GB_S)
    sqs_req = t.edits_per_mo * 10
    r["sqs"] = max(0, sqs_req - SQS_FREE_REQ) / 1e6 * SQS_PER_M_REQ
    r["ecr"] = 2.0 * ECR_GB_MO
    r["cloudwatch"] = CLOUDWATCH_MO
    r["ssm"] = 0.0
    r["aws"] = (r["bedrock"] + r["fargate"] + r["lambda"] + r["sqs"]
                + r["ecr"] + r["cloudwatch"] + r["ssm"])

    # ---- B2, unchanged by this branch -------------------------------------
    b2_gb = t.songs * 0.05 + stills_mo * 12 * 0.002 + t.artists * 0.03
    r["b2"] = b2_gb * B2_GB_MO
    r["b2_gb"] = b2_gb

    # ---- what the tier is trying to move ----------------------------------
    r["generation"] = attempts_mo * STILL_GEN_PER_IMAGE
    r["judging"] = r["judgements_mo"] * LIKENESS_JUDGE_PER_CALL
    r["memory_tier"] = r["bedrock"] + r["crdb"]
    r["total"] = r["aws"] + r["b2"] + r["crdb"] + r["generation"] + r["judging"]

    # ---- unit economics ---------------------------------------------------
    r["mem_per_video"] = r["memory_tier"] / t.edits_per_mo
    r["gen_per_video"] = (r["generation"] + r["judging"]) / t.edits_per_mo
    r["mem_pct_of_video"] = r["mem_per_video"] / r["gen_per_video"] * 100
    r["cost_per_approved_still"] = (r["generation"] + r["judging"]) / stills_mo
    # Breakeven: how much of one attempt the memory tier must remove to pay for itself.
    r["breakeven_attempts"] = r["mem_per_video"] / (M_STILLS_PER_EDIT * STILL_GEN_PER_IMAGE)
    return r


RESULTS = {t.key: compute(t) for t in TIERS}


def idle_cost(t: Tier) -> dict:
    """MEMORY-SPEC §10 decision 2, answered directly: a month in which nobody generates
    anything, with the corpus already ingested and sitting at rest.

    Everything usage-billed goes to zero by construction — Lambda, SQS, Fargate and
    Bedrock are all per-request. What is left is the floor, and the question the branch
    raised was whether CockroachDB adds one. On Basic it does not: the plan scales to
    zero and the storage sits inside the free 10 GiB.
    """
    r = RESULTS[t.key]
    crdb = max(0.0, r["storage_gib"] - CRDB_FREE_STORAGE_GIB) * CRDB_STORAGE_GIB_MO
    return {"ecr": 2.0 * ECR_GB_MO, "cloudwatch": CLOUDWATCH_MO, "b2": r["b2"],
            "crdb": crdb, "compute": 0.0,
            "total": 2.0 * ECR_GB_MO + CLOUDWATCH_MO + r["b2"] + crdb}


IDLE = {t.key: idle_cost(t) for t in TIERS}


# ---------------------------------------------------------------------------
# Sensitivity — the curve the demo metric lives on.
# ---------------------------------------------------------------------------
def attempt_curve(lo=1.0, hi=4.0, steps=13):
    out = []
    for i in range(steps):
        a = lo + (hi - lo) * i / (steps - 1)
        gen = M_STILLS_PER_EDIT * a * STILL_GEN_PER_IMAGE
        judge = M_STILLS_PER_EDIT * a * M_CAST_PER_EDIT * LIKENESS_JUDGE_PER_CALL
        mem = M_STILLS_PER_EDIT * a * (IMAGE_EMBED_PER_IMAGE
                                       + 2 * EMBED_TOKENS / 1e6 * TITAN_TEXT_PER_MTOK)
        out.append((a, gen + judge, mem))
    return out


CURVE = attempt_curve()


# ---------------------------------------------------------------------------
# CSV — AWS Pricing Calculator export schema.
# ---------------------------------------------------------------------------
CSV_COLS = ["Tier", "Service", "Description", "Specs", "Monthly Cost", "12 Months Cost"]


def csv_rows():
    rows = []
    for t in TIERS:
        r = RESULTS[t.key]
        items = [
            ("Amazon Bedrock", "Titan Text Embeddings V2 — clip + query embeddings",
             f"{(t.clips * EMBED_TOKENS / 12 + r['attempts_mo'] * 2 * EMBED_TOKENS) / 1000:,.0f}K tokens/mo @ $0.02/MTok",
             r["bedrock_text"] + r["ingest_mo"]),
            ("Amazon Bedrock", "Titan Image Embeddings — Q3 likeness vectors",
             f"{r['attempts_mo']:,.0f} images/mo @ $0.00006/image", r["bedrock_image"]),
            ("Amazon Bedrock", "Nova Lite — agent runtime for Q4 over MCP",
             f"{t.edits_per_mo * AGENT_QUERIES_PER_EDIT:,} queries/mo @ 4K in / 500 out",
             r["bedrock_agent"]),
            ("AWS Batch / Fargate Spot", "Generator container — Genblaze + ffmpeg",
             f"{r['fargate_hours']:.1f} hr/mo @ {GEN_VCPU} vCPU / {GEN_GB} GB, minvCpus 0",
             r["fargate"]),
            ("AWS Lambda", "web — FastAPI via Web Adapter + Function URL",
             f"{t.edits_per_mo * 200 * t.tenants:,} req/mo — inside 1M free tier",
             r["lambda"]),
            ("Amazon SQS", "job queue + DLQ",
             f"{t.edits_per_mo * 10:,} req/mo — inside 1M free tier", r["sqs"]),
            ("Amazon ECR", "generator image storage", "2 GB @ $0.10/GB-mo", r["ecr"]),
            ("Amazon CloudWatch", "logs, 14-day retention", "UNVERIFIED placeholder",
             r["cloudwatch"]),
            ("AWS Systems Manager", "Parameter Store — B2 + provider keys",
             "Standard tier — free", r["ssm"]),
            ("CockroachDB Cloud (not AWS)", "Basic — vectors and rows, never bytes",
             f"{r['storage_gib']:.2f} GiB of 10 GiB free · {r['ops_mo']:,.0f} ops/mo",
             r["crdb"]),
            ("Backblaze B2 (not AWS)", "masters, stills, manifests — unchanged by branch",
             f"{r['b2_gb']:.1f} GB @ $0.00695/GB-mo", r["b2"]),
        ]
        for svc, desc, spec, cost in items:
            rows.append([t.name, svc, desc, spec, f"{cost:.4f}", f"{cost * 12:.2f}"])
        rows.append([t.name, "TOTAL — infrastructure", "AWS + B2 + CockroachDB",
                     "excludes image generation", f"{r['aws'] + r['b2'] + r['crdb']:.4f}",
                     f"{(r['aws'] + r['b2'] + r['crdb']) * 12:.2f}"])
        rows.append([t.name, "MEMO — image generation", "gemini-2.5-flash-image @ $0.04",
                     f"{r['attempts_mo']:,.0f} attempts/mo", f"{r['generation']:.2f}",
                     f"{r['generation'] * 12:.2f}"])
    return rows


def write_csv(path: Path):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLS)
        w.writerows(csv_rows())
    print(f"-> {path.name}  ({len(csv_rows())} line items)")


# ---------------------------------------------------------------------------
# SVG charts. Hand-rolled: matplotlib is not a dependency of this repo and this
# is three charts, not a plotting library's worth of work.
# ---------------------------------------------------------------------------
MEM, HOT, PLAIN, ASYNC = "#0E7C86", "#D32127", "#4A5568", "#7B61FF"
INK, MUTE, GRID = "#1A202C", "#718096", "#E2E8F0"


def chart_ratio() -> str:
    """Log-scale bars. Linear axes cannot show a 600x ratio; that ratio is the point."""
    import math
    r = RESULTS["t1"]
    bars = [("Image generation", r["gen_per_video"] - r["judging"] / TIERS[1].edits_per_mo, HOT),
            ("Likeness judging", r["judging"] / TIERS[1].edits_per_mo, ASYNC),
            ("Memory tier", r["mem_per_video"], MEM)]
    W, H, L, T, BH, GAP = 620, 186, 132, 26, 34, 20
    lo, hi = 1e-4, 10.0
    def x(v):
        v = max(v, lo)
        return L + (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * (W - L - 60)
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for gv in (1e-4, 1e-3, 1e-2, 1e-1, 1, 10):
        gx = x(gv)
        s.append(f'<line x1="{gx:.1f}" y1="{T-8}" x2="{gx:.1f}" y2="{T+len(bars)*(BH+GAP)-GAP+6}" stroke="{GRID}" stroke-width="1"/>')
        lbl = f"${gv:g}" if gv >= 1 else f"${gv}".replace("0.", ".")
        s.append(f'<text x="{gx:.1f}" y="{T+len(bars)*(BH+GAP)-GAP+20}" font-size="9" fill="{MUTE}" text-anchor="middle">{lbl}</text>')
    for i, (name, val, col) in enumerate(bars):
        y = T + i * (BH + GAP)
        s.append(f'<text x="{L-10}" y="{y+BH/2+4}" font-size="11" fill="{INK}" text-anchor="end">{name}</text>')
        s.append(f'<rect x="{x(lo):.1f}" y="{y}" width="{max(2, x(val)-x(lo)):.1f}" height="{BH}" fill="{col}" rx="2"/>')
        txt = f"${val:,.2f}" if val >= 0.01 else f"${val:.4f}"
        s.append(f'<text x="{x(val)+8:.1f}" y="{y+BH/2+4}" font-size="11" font-weight="700" fill="{col}">{txt}</text>')
    s.append("</svg>")
    return "".join(s)


def chart_curve() -> str:
    """Cost per video against attempts-per-approved-still — the demo metric's axis."""
    W, H, L, R, T, B = 620, 240, 56, 22, 20, 44
    xs = [c[0] for c in CURVE]
    ymax = max(c[1] for c in CURVE) * 1.1
    def px(a): return L + (a - xs[0]) / (xs[-1] - xs[0]) * (W - L - R)
    def py(v): return H - B - v / ymax * (H - T - B)
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i in range(5):
        v = ymax * i / 4
        s.append(f'<line x1="{L}" y1="{py(v):.1f}" x2="{W-R}" y2="{py(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{L-8}" y="{py(v)+4:.1f}" font-size="9" fill="{MUTE}" text-anchor="end">${v:,.2f}</text>')
    pts = " ".join(f"{px(a):.1f},{py(g):.1f}" for a, g, _ in CURVE)
    s.append(f'<polyline points="{pts}" fill="none" stroke="{HOT}" stroke-width="2.5"/>')
    mpts = " ".join(f"{px(a):.1f},{py(m):.1f}" for a, _, m in CURVE)
    s.append(f'<polyline points="{mpts}" fill="none" stroke="{MEM}" stroke-width="2.5"/>')
    for a in (1.0, 2.0, 3.0, 4.0):
        s.append(f'<text x="{px(a):.1f}" y="{H-B+16:.1f}" font-size="9" fill="{MUTE}" text-anchor="middle">{a:.1f}</text>')
    # The claim, drawn: 3.0 -> 2.2 attempts.
    g3 = M_STILLS_PER_EDIT * 3.0 * STILL_GEN_PER_IMAGE
    g22 = M_STILLS_PER_EDIT * 2.2 * STILL_GEN_PER_IMAGE
    s.append(f'<line x1="{px(3.0):.1f}" y1="{py(g3):.1f}" x2="{px(2.2):.1f}" y2="{py(g22):.1f}" stroke="{PLAIN}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    for a, v in ((3.0, g3), (2.2, g22)):
        s.append(f'<circle cx="{px(a):.1f}" cy="{py(v):.1f}" r="4" fill="{PLAIN}"/>')
    # Annotation sits BELOW the dashed segment: the region under the red line is the only
    # empty part of the plot, and anchoring above collides with the curve itself.
    s.append(f'<text x="{px(2.6):.1f}" y="{py(g22)+20:.1f}" font-size="9.5" font-weight="700" fill="{PLAIN}" text-anchor="middle">−${g3-g22:.2f}/video if memory moves 3.0 → 2.2</text>')
    s.append(f'<text x="{px(3.55):.1f}" y="{py(M_STILLS_PER_EDIT*3.55*STILL_GEN_PER_IMAGE)-11:.1f}" font-size="10" font-weight="700" fill="{HOT}" text-anchor="end">generate + judge</text>')
    mem_lo = M_STILLS_PER_EDIT * 1.2 * (IMAGE_EMBED_PER_IMAGE + 2 * EMBED_TOKENS / 1e6 * TITAN_TEXT_PER_MTOK)
    s.append(f'<text x="{px(1.15):.1f}" y="{py(mem_lo)-9:.1f}" font-size="10" font-weight="700" fill="{MEM}" text-anchor="start">memory tier — flat against the axis</text>')
    s.append(f'<text x="{(L+W-R)/2:.1f}" y="{H-6}" font-size="9.5" fill="{MUTE}" text-anchor="middle">attempts per approved still — MEMORY-SPEC §6\'s demo metric</text>')
    s.append("</svg>")
    return "".join(s)


def chart_headroom() -> str:
    """Free-allowance utilisation. The tier is free until one of these bars fills."""
    W, H, L, BH, GAP, T = 620, 190, 152, 26, 16, 24
    rows = []
    for t in TIERS:
        r = RESULTS[t.key]
        rows.append((f"{t.name} storage", r["storage_pct_free"], MEM))
    W2 = W - L - 96
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i, (name, pct, col) in enumerate(rows):
        y = T + i * (BH + GAP)
        s.append(f'<text x="{L-10}" y="{y+BH/2+4}" font-size="11" fill="{INK}" text-anchor="end">{name}</text>')
        s.append(f'<rect x="{L}" y="{y}" width="{W2}" height="{BH}" fill="#F1F5F9" rx="3"/>')
        w = max(2.0, min(pct, 100) / 100 * W2)
        s.append(f'<rect x="{L}" y="{y}" width="{w:.1f}" height="{BH}" fill="{col}" rx="3"/>')
        s.append(f'<text x="{L+W2+10}" y="{y+BH/2+4}" font-size="11" font-weight="700" fill="{col}">{pct:.1f}%</text>')
    s.append(f'<text x="{L}" y="{H-14}" font-size="9.5" fill="{MUTE}">share of CockroachDB Basic\'s 10 GiB free monthly storage, after 12 months of episodes</text>')
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# HTML -> PDF
# ---------------------------------------------------------------------------
CSS = """
@page { size: A4; margin: 15mm 14mm 13mm 14mm;
  @bottom-center { content: "RemixKit · memory-tier workload & unit economics · verified """ + VERIFIED + """ · page " counter(page) " of " counter(pages);
    font-family: Helvetica, sans-serif; font-size: 7.5pt; color: #A0AEC0; } }
* { box-sizing: border-box; }
body { font-family: Helvetica, "Helvetica Neue", Arial, sans-serif; color: #1A202C;
  font-size: 9.2pt; line-height: 1.45; margin: 0; }
h1 { font-size: 19pt; margin: 0 0 2mm; letter-spacing: -0.3pt; }
h2 { font-size: 12pt; margin: 7mm 0 2.5mm; padding-bottom: 1.2mm;
  border-bottom: 1.6pt solid #0E7C86; color: #0E7C86; letter-spacing: -0.2pt; }
h3 { font-size: 9.6pt; margin: 4mm 0 1.5mm; color: #2D3748; }
.sub { color: #4A5568; font-size: 10pt; line-height: 1.45; margin: 0 0 3mm; }
.meta { color: #718096; font-size: 8pt; border-top: 0.6pt solid #E2E8F0;
  padding-top: 1.6mm; margin-bottom: 4mm; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 3mm; font-size: 8.2pt; }
th { text-align: left; font-size: 7.4pt; text-transform: uppercase; letter-spacing: 0.4pt;
  color: #718096; border-bottom: 1pt solid #CBD5E0; padding: 1.4mm 1.8mm; font-weight: 700; }
td { padding: 1.3mm 1.8mm; border-bottom: 0.5pt solid #EDF2F7; vertical-align: top; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tr.tot td { font-weight: 700; border-top: 1pt solid #CBD5E0; border-bottom: none;
  background: #F7FAFC; }
.teal { color: #0E7C86; } .red { color: #D32127; } .mute { color: #718096; }
.kpis { display: flex; gap: 3mm; margin: 3mm 0 1mm; }
.kpi { flex: 1; border: 0.8pt solid #E2E8F0; border-top: 2.4pt solid #0E7C86;
  border-radius: 2pt; padding: 2.4mm 2.6mm; }
.kpi .v { font-size: 15pt; font-weight: 700; color: #0E7C86; letter-spacing: -0.4pt;
  line-height: 1.1; }
.kpi .l { font-size: 7.2pt; color: #718096; text-transform: uppercase;
  letter-spacing: 0.3pt; margin-top: 0.8mm; }
.kpi .d { font-size: 7.6pt; color: #4A5568; margin-top: 1.2mm; line-height: 1.35; }
.kpi.red { border-top-color: #D32127; } .kpi.red .v { color: #D32127; }
.note { border-left: 2.4pt solid #CBD5E0; padding: 1.8mm 0 1.8mm 3mm; margin: 3mm 0;
  font-size: 8.4pt; color: #2D3748; background: #F7FAFC; }
.note.warn { border-left-color: #D69E2E; background: #FFFBEB; }
.note.key { border-left-color: #0E7C86; background: #F0FDFA; }
.note b { color: #1A202C; }
.chart { margin: 2mm 0 1mm; }
.chart svg { width: 100%; height: auto; }
.cap { font-size: 7.8pt; color: #718096; margin: 0 0 3mm; }
.pb { page-break-before: always; }
.tag { display: inline-block; font-size: 6.8pt; font-weight: 700; padding: 0.3mm 1.2mm;
  border-radius: 1.5pt; vertical-align: 1pt; letter-spacing: 0.2pt; }
.tag.v { background: #D1FAE5; color: #065F46; } .tag.u { background: #FEF3C7; color: #92400E; }
.tag.m { background: #DBEAFE; color: #1E40AF; }
ul { margin: 1.5mm 0 3mm; padding-left: 4.5mm; } li { margin-bottom: 1.2mm; }
.tiers { display: flex; gap: 2.6mm; margin: 1mm 0 2mm; }
.tcard { flex: 1; font-size: 7.6pt; color: #4A5568; line-height: 1.35;
  border-left: 1.8pt solid #CBD5E0; padding-left: 2.2mm; }
.tcard b { display: block; color: #0E7C86; font-size: 8.2pt; margin-bottom: 0.6mm; }
"""


def esc(x):
    return html.escape(str(x))


def money(v, dp=2):
    return f"${v:,.{dp}f}"


def build_html() -> str:
    t0, t1, t2 = TIERS
    r0, r1, r2 = RESULTS["t0"], RESULTS["t1"], RESULTS["t2"]
    infra = {k: RESULTS[k]["aws"] + RESULTS[k]["b2"] + RESULTS[k]["crdb"] for k in RESULTS}

    o = [f"<style>{CSS}</style>"]

    # ---------------- page 1 ----------------
    o.append("<h1>The memory tier costs almost nothing to run.<br>"
             "What it has to buy is fewer attempts.</h1>")
    o.append('<p class="sub">A workload model and unit economics for '
             '<b>MEMORY-SPEC.md</b>&rsquo;s CockroachDB branch &mdash; closing the two numbers that '
             'file left open: what the tier costs at idle (§10 decision 2), and whether it '
             'breaks <b>infra/README.md</b>&rsquo;s &ldquo;under $1/month&rdquo; claim (§8).</p>')
    o.append(f'<p class="meta">Rates read from the AWS Price List Bulk API and vendor pricing '
             f'pages on {VERIFIED} · volumes measured out of this repository, not assumed · '
             f'generated by <b>infra/workload.py</b></p>')

    o.append("<h2>The answer, before the arithmetic</h2>")
    o.append('<div class="kpis">')
    o.append(f'<div class="kpi"><div class="v">{money(IDLE["t1"]["total"], 2)}</div>'
             f'<div class="l">idle cost / month, corpus at rest</div>'
             f'<div class="d">The number MEMORY-SPEC §10.2 leaves open. CockroachDB adds '
             f'<b>{money(IDLE["t1"]["crdb"], 2)}</b> of it. &ldquo;Under $1/month&rdquo; '
             f'<b>survives the branch</b>.</div></div>')
    o.append(f'<div class="kpi"><div class="v">{money(infra["t1"], 2)}</div>'
             f'<div class="l">infrastructure / month, T1 active</div>'
             f'<div class="d">Whole stack &mdash; AWS, B2 and CockroachDB &mdash; while actually '
             f'shipping {t1.edits_per_mo} videos a month.</div></div>')
    o.append(f'<div class="kpi"><div class="v">{r1["mem_pct_of_video"]:.2f}%</div>'
             f'<div class="l">memory tier, as share of a video</div>'
             f'<div class="d">{money(r1["mem_per_video"], 4)} of memory against '
             f'{money(r1["gen_per_video"])} of generation and judging.</div></div>')
    o.append(f'<div class="kpi red"><div class="v">{r1["breakeven_attempts"]:.4f}</div>'
             f'<div class="l">attempts it must remove to pay for itself</div>'
             f'<div class="d">Per still, out of {t1.attempts}. Anything the memory tier '
             f'actually learns clears this by three orders of magnitude.</div></div>')
    o.append("</div>")

    o.append('<div class="note key"><b>The finding that matters.</b> This branch was written '
             'expecting a cost argument &mdash; MEMORY-SPEC §8 concedes it &ldquo;reintroduces the tier '
             'that decision removed&rdquo; and declines to restate the idle number. There is no cost '
             'argument to have. CockroachDB Basic scales to zero, publishes a 50M&nbsp;RU + 10&nbsp;GiB '
             'monthly free allowance, and carries the distributed vector index on that plan. '
             'The workload fits inside it at every tier modelled here. <b>The real constraint is '
             'not price &mdash; it is that the vector-index RU cost is unpublished, so the free '
             'allowance has to be measured rather than assumed.</b></div>')

    o.append("<h2>Where the work actually comes from</h2>")
    o.append('<p class="cap">Volumes measured from the repo on ' + VERIFIED + ': '
             f'{M_LIBRARY_CLIPS} library clip descriptors, {M_STILLS_PER_EDIT} payoff stills '
             f'per edit across {M_EDITS_EXISTING} existing edits, {M_REF_FRAMES_PER_ARTIST} reference '
             f'framings per artist, and a mean of {M_PROSE_TOKENS_MEAN} tokens of embeddable prose '
             f'per descriptor (p95 {M_PROSE_TOKENS_P95}).</p>')
    o.append("<table><tr><th>Driver</th>"
             + "".join(f'<th class="n">{esc(t.name)}</th>' for t in TIERS) + "</tr>")
    rows = [
        ("Tenants", [t.tenants for t in TIERS], "{:,}"),
        ("Artists (identities / molds)", [t.artists for t in TIERS], "{:,}"),
        ("Songs", [t.songs for t in TIERS], "{:,}"),
        ("Clip corpus — vector-indexed rows", [t.clips for t in TIERS], "{:,}"),
        ("Edits per month", [t.edits_per_mo for t in TIERS], "{:,}"),
        ("Stills per month", [RESULTS[t.key]["stills_mo"] for t in TIERS], "{:,.0f}"),
        ("Attempts per approved still", [t.attempts for t in TIERS], "{:.1f}"),
        ("Generation attempts per month", [RESULTS[t.key]["attempts_mo"] for t in TIERS], "{:,.0f}"),
        ("Likeness judgements per month", [RESULTS[t.key]["judgements_mo"] for t in TIERS], "{:,.0f}"),
        ("Memory-tier operations per month", [RESULTS[t.key]["ops_mo"] for t in TIERS], "{:,.0f}"),
    ]
    for label, vals, fmt in rows:
        o.append(f"<tr><td>{esc(label)}</td>"
                 + "".join(f'<td class="n">{fmt.format(v)}</td>' for v in vals) + "</tr>")
    o.append("</table>")
    o.append('<div class="tiers">')
    for t in TIERS:
        o.append(f'<div class="tcard"><b>{esc(t.name)}</b>{esc(t.blurb)}</div>')
    o.append('</div>')

    o.append('<div class="pb"></div>')
    o.append("<h2>Free-allowance headroom — the only thing that can make this cost money</h2>")
    o.append(f'<div class="chart">{chart_headroom()}</div>')
    o.append('<div class="note"><b>Storage is a non-event.</b> A clip row is '
             f'~{CLIP_ROW_BYTES/1024:.1f}&nbsp;KB (a {M_DESCRIPTOR_BYTES}-byte descriptor plus a '
             f'{VEC_TEXT_BYTES//1024}&nbsp;KB VECTOR(1024)); an episode row is ~{EPISODE_ROW_BYTES/1024:.1f}&nbsp;KB. '
             f'Even T2 &mdash; fifty thousand clips plus a full year of episodes &mdash; is '
             f'{r2["storage_gib"]:.2f}&nbsp;GiB, or {r2["storage_pct_free"]:.1f}% of the free '
             '10&nbsp;GiB. Bytes stay in B2; the tier holds vectors and rows, exactly as '
             '<b>memory-branch.pdf</b> draws it.</div>')

    o.append('<div class="note warn"><b>Request Units are the open number, and they are '
             'stated as an inversion rather than guessed.</b> CockroachDB publishes no RU cost '
             'for a vector-index scan. So instead of inventing one: an operation would have to '
             f'cost more than <b>{r1["ru_break_even"]:,.0f}&nbsp;RU</b> at T1 '
             f'(<b>{r2["ru_break_even"]:,.0f}&nbsp;RU</b> at T2) before the 50M free allowance '
             'breaks. For scale, CockroachDB documents a small point read at single-digit RUs. '
             'T1 has room to spare; <b>T2 is where this must actually be measured</b> — day 1&ndash;2 '
             'of MEMORY-SPEC §9 is the place to record it.</div>')

    # ---------------- page 2 ----------------
    o.append("<h2>Unit economics — the memory tier is a rounding error on its own bill</h2>")
    o.append(f'<div class="chart">{chart_ratio()}</div>')
    o.append('<p class="cap">Cost of one finished video at T1, on a log axis, because the '
             'ratio is the finding and a linear axis would render the teal bar invisible.</p>')

    o.append("<table><tr><th>Per finished video (22 stills)</th>"
             + "".join(f'<th class="n">{esc(t.name)}</th>' for t in TIERS) + "</tr>")
    ue = [
        ("Image generation + likeness judging",
         [RESULTS[t.key]["gen_per_video"] for t in TIERS], "${:,.2f}"),
        ("Memory tier — embeddings, vectors, rows",
         [RESULTS[t.key]["mem_per_video"] for t in TIERS], "${:,.4f}"),
        ("Memory tier as % of the video",
         [RESULTS[t.key]["mem_pct_of_video"] for t in TIERS], "{:.2f}%"),
        ("Cost per approved still",
         [RESULTS[t.key]["cost_per_approved_still"] for t in TIERS], "${:,.3f}"),
        ("Attempts/still the tier must remove to break even",
         [RESULTS[t.key]["breakeven_attempts"] for t in TIERS], "{:.4f}"),
    ]
    for label, vals, fmt in ue:
        o.append(f"<tr><td>{esc(label)}</td>"
                 + "".join(f'<td class="n">{fmt.format(v)}</td>' for v in vals) + "</tr>")
    o.append("</table>")

    o.append("<h3>The metric the whole case rests on</h3>")
    o.append(f'<div class="chart">{chart_curve()}</div>')
    save_t2 = (M_STILLS_PER_EDIT * (3.0 - 2.2) * STILL_GEN_PER_IMAGE) * t2.edits_per_mo
    o.append(f'<div class="note key"><b>The trade, stated plainly.</b> Memory costs '
             f'{money(r1["mem_per_video"], 4)} per video. One attempt-per-still of improvement is '
             f'worth {money(M_STILLS_PER_EDIT * STILL_GEN_PER_IMAGE)}. The tier therefore pays for '
             f'itself at <b>{r1["breakeven_attempts"]:.4f}</b> attempts removed per still — about '
             f'{r1["breakeven_attempts"]/t1.attempts*100:.2f}% of the current figure. A move from '
             f'3.0 to 2.2 attempts saves {money(M_STILLS_PER_EDIT*0.8*STILL_GEN_PER_IMAGE)} per '
             f'video, or <b>{money(save_t2)}/month at T2</b> — for a tier that bills nothing.</div>')

    o.append('<div class="note warn"><b>And the number this document cannot supply.</b> '
             'Attempts-per-approved-still is <i>not measured anywhere in this repo</i>, because '
             'nothing writes back — that is precisely MEMORY-SPEC §3\'s third failure. The 3.0 / '
             '2.2 / 1.8 figures above are <b>assumptions used to size the workload, not evidence</b>. '
             'The curve is drawn so the sensitivity is visible instead of hidden in a point '
             'estimate: read it as &ldquo;here is what any improvement is worth&rdquo;, never as a claim that '
             'the improvement has been observed. MEMORY-SPEC §6 already commits to labelling the '
             'demo curve with its N; the same rule governs this page.</div>')

    o.append("<h3>What this does and does not change about the bill</h3>")
    o.append("<ul>")
    o.append("<li><b>research/07-cost-model&rsquo;s conclusion is unchanged and is reinforced.</b> "
             "Generation is the bill; everything else rounds to noise. The memory tier does not "
             "add a meaningful line &mdash; it is a lever on the line that already dominates.</li>")
    o.append("<li><b>The tier has no idle floor</b>, which is the property infra/README.md's "
             "no-database decision was actually protecting. Basic scales to zero; the free "
             "allowance is monthly, not a trial.</li>")
    o.append(f"<li><b>Embedding is free in practice.</b> The measured payload is "
             f"~{M_PROSE_TOKENS_MEAN} tokens of prose per clip, so embedding a "
             f"{t2.clips:,}-clip catalogue costs "
             f"{money(t2.clips*EMBED_TOKENS/1e6*TITAN_TEXT_BATCH_PER_MTOK, 2)} &mdash; once.</li>")
    o.append("<li><b>What is genuinely new spend</b> is Bedrock, and it is "
             f"{money(r1['bedrock'], 2)}/month at T1. Batch embedding halves the ingest half of "
             "that.</li>")
    o.append("</ul>")

    # ---------------- page 3 ----------------
    o.append('<div class="pb"></div>')
    o.append("<h2>The AWS workload estimate</h2>")
    o.append('<p class="cap">Line items in AWS Pricing Calculator&rsquo;s own export schema, so this '
             'can be diffed against a real calculator estimate rather than retyped. Also emitted '
             'as <b>memory-workload.csv</b>. <span class="tag v">API</span> read from the AWS Price '
             'List Bulk API · <span class="tag m">MEAS</span> measured from this repo · '
             '<span class="tag u">UNVERIFIED</span> could not be confirmed against a primary source.</p>')

    for t in TIERS:
        r = RESULTS[t.key]
        o.append(f"<h3>{esc(t.name)} &mdash; {t.edits_per_mo} edits/month, "
                 f"{t.clips:,} indexed clips, {t.tenants} tenant(s)</h3>")
        o.append('<table><tr><th>Service</th><th>Line</th><th>Specs</th>'
                 '<th class="n">$/mo</th><th class="n">$/yr</th></tr>')
        items = [
            ("Amazon Bedrock", 'Titan Text Embeddings V2 <span class="tag v">API</span>',
             "$0.02/MTok · clip + query embeddings", r["bedrock_text"] + r["ingest_mo"]),
            ("Amazon Bedrock", 'Titan Image Embeddings <span class="tag v">API</span>',
             f"$0.00006/image · {r['attempts_mo']:,.0f} Q3 vectors/mo", r["bedrock_image"]),
            ("Amazon Bedrock", 'Nova Lite — agent runtime <span class="tag v">API</span>',
             f"$0.06/$0.24 per MTok · {t.edits_per_mo*AGENT_QUERIES_PER_EDIT:,} Q4 queries/mo",
             r["bedrock_agent"]),
            ("AWS Batch / Fargate Spot", 'generator container <span class="tag u">SPOT</span>',
             f"{r['fargate_hours']:.1f} hr/mo · {GEN_VCPU} vCPU / {GEN_GB} GB · minvCpus 0",
             r["fargate"]),
            ("AWS Lambda", 'web — Function URL <span class="tag v">API</span>',
             f"{t.edits_per_mo*200*t.tenants:,} req/mo — inside 1M free", r["lambda"]),
            ("Amazon SQS", 'job queue + DLQ <span class="tag v">API</span>',
             f"{t.edits_per_mo*10:,} req/mo — inside 1M free", r["sqs"]),
            ("Amazon ECR", 'image storage <span class="tag v">API</span>',
             "2 GB @ $0.10/GB-mo", r["ecr"]),
            ("Amazon CloudWatch", 'logs, 14-day <span class="tag u">UNVERIFIED</span>',
             "placeholder — infra/README.md carries this as &ldquo;~cents&rdquo;", r["cloudwatch"]),
            ("AWS Systems Manager", "Parameter Store", "Standard tier — free", r["ssm"]),
        ]
        for svc, line, spec, cost in items:
            o.append(f"<tr><td>{esc(svc)}</td><td>{line}</td><td class='mute'>{spec}</td>"
                     f'<td class="n">{money(cost, 4)}</td><td class="n">{money(cost*12, 2)}</td></tr>')
        o.append(f'<tr class="tot"><td colspan="3">AWS subtotal</td>'
                 f'<td class="n">{money(r["aws"], 4)}</td><td class="n">{money(r["aws"]*12, 2)}</td></tr>')
        o.append(f"<tr><td>CockroachDB Cloud</td><td>Basic — vectors and rows, never bytes</td>"
                 f"<td class='mute'>{r['storage_gib']:.2f} GiB of 10 GiB free · "
                 f"{r['ops_mo']:,.0f} ops/mo vs 50M free RU</td>"
                 f'<td class="n">{money(r["crdb"], 4)}</td><td class="n">{money(r["crdb"]*12, 2)}</td></tr>')
        o.append(f"<tr><td>Backblaze B2</td><td>masters, stills, manifests — unchanged</td>"
                 f"<td class='mute'>{r['b2_gb']:.1f} GB @ $0.00695/GB-mo</td>"
                 f'<td class="n">{money(r["b2"], 4)}</td><td class="n">{money(r["b2"]*12, 2)}</td></tr>')
        o.append(f'<tr class="tot"><td colspan="3">Total infrastructure</td>'
                 f'<td class="n teal">{money(infra[t.key], 4)}</td>'
                 f'<td class="n teal">{money(infra[t.key]*12, 2)}</td></tr>')
        o.append(f'<tr><td class="mute">memo</td><td class="mute">image generation — '
                 f'gemini-2.5-flash-image <span class="tag m">MEAS</span></td>'
                 f'<td class="mute">$0.04/image · {r["attempts_mo"]:,.0f} attempts/mo</td>'
                 f'<td class="n red">{money(r["generation"], 2)}</td>'
                 f'<td class="n red">{money(r["generation"]*12, 2)}</td></tr>')
        o.append("</table>")

    o.append('<div class="note"><b>Read the two totals together.</b> Infrastructure at T2 is '
             f'{money(infra["t2"])}/month while generation is {money(r2["generation"])}/month — '
             f'infrastructure is {infra["t2"]/(infra["t2"]+r2["generation"])*100:.1f}% of the bill. '
             'That ratio is the whole reason the memory tier is worth adding despite the cost '
             'conflict MEMORY-SPEC §8 raises: it is spending in the free column to buy reductions '
             'in the expensive one.</div>')

    o.append('<div class="pb"></div>')
    o.append("<h2>What is not verified, and what would change these numbers</h2>")
    o.append("<ul>")
    o.append("<li><span class='tag u'>UNVERIFIED</span> <b>CockroachDB overage rates.</b> Basic&rsquo;s "
             "free allowance and scale-to-zero are on the vendor pricing page; the per-RU and "
             "per-GiB rates past it are third-party. They only matter if the free tier breaks, "
             "which is why every headline here is stated as headroom rather than as spend.</li>")
    o.append("<li><span class='tag u'>UNVERIFIED</span> <b>RU cost of a filtered vector scan.</b> "
             "The single largest modelling risk. Not published anywhere; must be measured on "
             "days 1&ndash;2 and is the cheapest possible thing to measure.</li>")
    o.append("<li><span class='tag u'>UNVERIFIED</span> <b>Fargate Spot discount.</b> Modelled at "
             "70% off on-demand. Spot rates are dynamic and absent from the Price List API. At "
             f"{r1['fargate_hours']:.1f} hr/month this cannot move the total.</li>")
    o.append("<li><b>The face embedding is still an open design decision</b> (MEMORY-SPEC §10.3), "
             "priced here as Titan Image Embeddings at $0.00006/image — a metric vector for Q3 "
             "rather than the vision model&rsquo;s judgement. A real change to check_likeness.py, "
             "not a refactor.</li>")
    o.append("<li><b>Attempts-per-approved-still is an assumption at every tier.</b> Restated "
             "here because it is the one number that would change the conclusion, and the one "
             "the repo cannot currently produce.</li>")
    o.append("</ul>")
    o.append('<p class="cap">Sources — AWS Price List Bulk API (pricing.us-east-1.amazonaws.com, '
             'AmazonBedrock / AWSLambda / AWSQueueService / AmazonECS / AmazonECR, us-east-1, '
             f'read {VERIFIED}) · cockroachlabs.com/pricing/new and /docs/cockroachcloud/costs '
             f'(read {VERIFIED}) · research/07-cost-model (verified 2026-07-15) for Backblaze B2 '
             'and Gemini token rates · content/bin/generate_stills.py PRICES_USD (dated 2026-07 '
             'by the repo) for the image-generation rate.</p>')
    return "".join(o)


def main():
    html_path = HERE / ".build" / "memory-workload.html"
    html_path.parent.mkdir(exist_ok=True)
    # The charset declaration is load-bearing: without it WeasyPrint decodes the file as
    # latin-1 and every em-dash, middle dot and section sign in the copy turns to mojibake.
    doc = ('<!doctype html><html><head><meta charset="utf-8">'
           '<title>RemixKit — memory-tier workload &amp; unit economics</title></head>'
           f'<body>{build_html()}</body></html>')
    html_path.write_text(doc, encoding="utf-8")
    pdf = HERE / "memory-workload.pdf"
    subprocess.run(["weasyprint", str(html_path), str(pdf)], check=True)
    print(f"-> {pdf.name}")
    write_csv(HERE / "memory-workload.csv")

    print(f"\n{'':<44}" + "".join(f"{t.name:>16}" for t in TIERS))
    show = [
        ("indexed clips", "{:,.0f}", lambda t, r: t.clips),
        ("attempts / month", "{:,.0f}", lambda t, r: r["attempts_mo"]),
        ("memory-tier ops / month", "{:,.0f}", lambda t, r: r["ops_mo"]),
        ("RU/op before free tier breaks", "{:,.0f}", lambda t, r: r["ru_break_even"]),
        ("CockroachDB storage (GiB)", "{:,.2f}", lambda t, r: r["storage_gib"]),
        ("  ...as % of 10 GiB free", "{:,.1f}%", lambda t, r: r["storage_pct_free"]),
        ("Bedrock $/mo", "${:,.2f}", lambda t, r: r["bedrock"]),
        ("AWS subtotal $/mo", "${:,.2f}", lambda t, r: r["aws"]),
        ("TOTAL infrastructure $/mo", "${:,.2f}",
         lambda t, r: r["aws"] + r["b2"] + r["crdb"]),
        ("image generation $/mo", "${:,.2f}", lambda t, r: r["generation"]),
        ("memory tier $/video", "${:,.4f}", lambda t, r: r["mem_per_video"]),
        ("generation $/video", "${:,.2f}", lambda t, r: r["gen_per_video"]),
        ("memory as % of video", "{:,.3f}%", lambda t, r: r["mem_pct_of_video"]),
        ("breakeven attempts removed", "{:,.4f}", lambda t, r: r["breakeven_attempts"]),
    ]
    for label, fmt, fn in show:
        line = f"{label:<44}"
        for t in TIERS:
            line += f"{fmt.format(fn(t, RESULTS[t.key])):>16}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
