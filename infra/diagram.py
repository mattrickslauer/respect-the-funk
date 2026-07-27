#!/usr/bin/env python3
"""Render the RemixKit AWS architectures to PDF.

    python3 make_icons.py && python3 diagram.py

Three outputs, because PRODUCT.md narrowed the scope to one user role and the difference
is most of the architecture:

  architecture.pdf              THE ONE TO BUILD, and the one that ships on Aug 3.
                                Label-only scope: five services, one durable store,
                                no database tier.
  memory-branch.pdf             MEMORY-SPEC.md's branch, taken after Aug 3. Adds the
                                CockroachDB memory tier the file above deliberately
                                removed. Page 1 the system, page 2 the loop + economics.
  deferred-marketplace.pdf      The three-sided design, kept for when roles 2 and 3
                                come back. Page 1 the system, page 2 the economics.

`architecture.pdf` is not modified by the memory branch and must not be — infra/README.md
derives "under $1/month idle" from having no database tier, and MEMORY-SPEC §8 commits to
that architecture shipping unchanged. The branch is a third document, not an edit.

The diagrams are generated rather than drawn so they cannot drift from the writeup: node
labels carry the actual configuration (`minvCpus: 0`, `Function URL`, `Fargate Spot`,
`VECTOR(1024)`), and changing the architecture means changing this file.

Requires: graphviz on PATH (`brew install graphviz`), plus requirements.txt.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import Athena
from diagrams.aws.compute import Batch, Lambda
from diagrams.aws.database import Aurora
from diagrams.aws.integration import SQS, EventbridgeScheduler
from diagrams.aws.management import Cloudwatch, ParameterStore
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import CloudFront
from diagrams.custom import Custom

HERE = Path(__file__).parent
A = HERE / "assets"

B2 = str(A / "backblaze-b2.png")
GENBLAZE = str(A / "genblaze.png")
GMI = str(A / "gmicloud.png")
ELEVEN = str(A / "elevenlabs.png")
WWW = str(A / "browser.png")
CRDB = str(A / "cockroachdb.png")

# Palette. Hot path is the one a viral release actually exercises; it is drawn heaviest
# because the whole design is about keeping compute off it.
HOT = "#D32127"      # storage/CDN read path — thick, red
ASYNC = "#7B61FF"    # queued work — dashed, purple
DIRECT = "#0B7A4B"   # browser <-> B2 presigned, never through compute — dashed, green
PLAIN = "#4A5568"

# Memory branch only. Same logic as HOT above, applied to the thing THAT design is about:
# the loop is drawn heaviest because a memory tier that is only ever read is a catalog.
# Solid = retrieve (memory -> generation). Dashed = write back (result -> memory). Both
# teal, because the point is that they are two halves of one cycle rather than two paths.
MEM = "#0E7C86"

# nodesep is the load-bearing number here. `diagrams` hangs the label *under* a
# fixed-size icon, so a two-word label is far wider than the node graphviz is spacing,
# and anything under ~1.4 silently overlaps the neighbouring caption. Labels are kept to
# two short lines for the same reason — the detail belongs in README.md, not on the node.
GRAPH = {
    "fontsize": "26",
    "fontname": "Helvetica-Bold",
    "labelloc": "t",
    "bgcolor": "white",
    "pad": "0.9",
    "nodesep": "1.5",
    "ranksep": "1.15",
    "splines": "spline",
    "compound": "true",
}
NODE = {"fontsize": "13", "fontname": "Helvetica"}
EDGE = {"fontsize": "11", "fontname": "Helvetica"}
CLUSTER = {"fontsize": "16", "fontname": "Helvetica-Bold", "style": "rounded", "penwidth": "2"}


def page_label_scope(out: Path) -> Path:
    """The architecture for PRODUCT.md's scope: one user role, no marketplace.

    The point of this picture is what is NOT in it. Deferring the fan side removes the
    only things that needed SQL — attribution joins, leaderboard views, reward ledgers —
    and the database tier goes with them. What is left is a queue and a bucket.
    """
    title = ("RemixKit on AWS — label scope (PRODUCT.md)\n"
             "five services · one durable store · no database tier · $0 idle compute")
    with Diagram(title, filename=str(out), outformat="pdf", show=False,
                 direction="TB", graph_attr={**GRAPH, "nodesep": "1.8", "ranksep": "1.3"},
                 node_attr=NODE, edge_attr=EDGE):

        label = Custom("Label\nthe only user", WWW)

        with Cluster("AWS — scale-to-zero, $0 idle",
                     graph_attr={**CLUSTER, "bgcolor": "#F0F7FF"}):
            web = Lambda("① web · FastAPI\nWeb Adapter + Function URL")
            q = SQS("② SQS jobs\nidempotent, keyed")
            worker = Batch("③ generator · Fargate Spot\nGenblaze + ffmpeg, minvCpus: 0")
            ssm = ParameterStore("④ SSM Parameters\nB2 + provider keys")

        with Cluster("Backblaze B2 — the only durable store",
                     graph_attr={**CLUSTER, "bgcolor": "#FFF5F5", "penwidth": "3"}):
            artists = Custom("⑤ artists/{artist}/\nidentity + songs + rights", B2)
            kits = Custom("kits/{artist}/{song}/\nassets + embedded manifests", B2)
            parquet = Athena("manifests.parquet\nthe catalog index — no DB needed")

        with Cluster("AI providers — via Genblaze",
                     graph_attr={**CLUSTER, "bgcolor": "#FFFAF0"}):
            gb = Custom("Genblaze Pipeline", GENBLAZE)
            p1 = Custom("GMI Cloud\nvideo + image", GMI)
            p2 = Custom("ElevenLabs\naudio", ELEVEN)

        label >> Edge(color=PLAIN, label="HTTPS") >> web
        web >> Edge(color=ASYNC, style="dashed", label="enqueue") >> q
        q >> Edge(color=ASYNC, style="dashed") >> worker
        worker >> Edge(color=PLAIN, style="dotted") >> ssm
        artists >> Edge(color=PLAIN, style="dotted", label="identity + hook window") >> worker
        worker >> Edge(color=PLAIN) >> gb
        gb >> Edge(color=PLAIN) >> [p1, p2]
        worker >> Edge(color=HOT, penwidth="2.5", label="ObjectStorageSink") >> kits
        worker >> Edge(color=PLAIN, style="dotted", label="genblaze index") >> parquet
        label >> Edge(color=DIRECT, style="dashed", penwidth="2.5",
                      label="presigned PUT / GET\nnever transits compute",
                      constraint="false") >> kits

    return out.with_suffix(".pdf")


def page_memory_branch(out: Path) -> Path:
    """MEMORY-SPEC.md's architecture: the same five services plus a memory tier.

    Two things this picture has to say, or it is just the label-scope diagram with a
    database bolted to the side:

      1. CockroachDB holds *vectors and rows*, never bytes. B2 keeps every master,
         reference frame and delivered asset exactly as before — so the presigned
         browser<->B2 path survives untouched, and the new tier is not on the hot path.
      2. The teal edges form a cycle. Solid retrieves memory before generating; dashed
         writes the result back. A tier with only solid edges would be a catalog, which
         is what infra/README.md correctly said a bucket could already do.
    """
    title = ("RemixKit on AWS — memory branch (MEMORY-SPEC.md)\n"
             "CockroachDB is the memory tier · B2 still holds the bytes · the teal cycle is what is new")
    with Diagram(title, filename=str(out), outformat="pdf", show=False,
                 direction="TB", graph_attr={**GRAPH, "nodesep": "1.7", "ranksep": "1.25"},
                 node_attr=NODE, edge_attr=EDGE):

        label = Custom("Label\nthe only user", WWW)

        with Cluster("AWS — scale-to-zero compute",
                     graph_attr={**CLUSTER, "bgcolor": "#F0F7FF"}):
            web = Lambda("① web · FastAPI\nWeb Adapter + Function URL")
            q = SQS("② SQS jobs\nidempotent, keyed")
            worker = Batch("③ generator · Fargate Spot\nGenblaze + ffmpeg, minvCpus: 0")
            agent = Bedrock("④ Bedrock\nembeddings + agent runtime")
            ssm = ParameterStore("⑤ SSM Parameters\nkeys")

        with Cluster("CockroachDB — the memory tier (vectors + rows, never bytes)",
                     graph_attr={**CLUSTER, "bgcolor": "#F5F0FF", "penwidth": "3"}):
            ident = Custom("identity + negatives\nface VECTOR(512)", CRDB)
            corpus = Custom("clip corpus\nmeaning VECTOR(1024)", CRDB)
            eps = Custom("episodes + lessons\nevery attempt, kept", CRDB)
            cat = Custom("artist · approval\nplain SQL joins — Q4", CRDB)

        # The presigned browser<->B2 path is deliberately NOT drawn here, though it still
        # exists unchanged. As an edge it spans the full height of the page, routes around
        # every cluster, and out-shouts the teal cycle this page exists to show. The claim
        # is load-bearing, so it moves into the cluster label; page_label_scope draws it.
        with Cluster("Backblaze B2 — still the only durable store\n"
                     "presigned browser ↔ B2, still never transits compute",
                     graph_attr={**CLUSTER, "bgcolor": "#FFF5F5", "penwidth": "3"}):
            artists = Custom("artists/{artist}/\nmasters + reference frames", B2)
            kits = Custom("kits/{artist}/{song}/\nassets + embedded manifests", B2)

        with Cluster("AI providers — via Genblaze",
                     graph_attr={**CLUSTER, "bgcolor": "#FFFAF0"}):
            gb = Custom("Genblaze Pipeline", GENBLAZE)
            p1 = Custom("GMI Cloud\nvideo + image", GMI)
            p2 = Custom("ElevenLabs\naudio", ELEVEN)

        # ---- unchanged from the label-scope architecture --------------------
        label >> Edge(color=PLAIN, label="HTTPS") >> web
        web >> Edge(color=ASYNC, style="dashed", label="enqueue") >> q
        q >> Edge(color=ASYNC, style="dashed") >> worker
        worker >> Edge(color=PLAIN, style="dotted") >> ssm
        artists >> Edge(color=PLAIN, style="dotted", label="masters + frames") >> worker
        worker >> Edge(color=PLAIN) >> gb
        gb >> Edge(color=PLAIN) >> [p1, p2]
        worker >> Edge(color=HOT, penwidth="2.5", label="ObjectStorageSink") >> kits

        # ---- retrieve: memory read BEFORE the model is called ---------------
        # rights.source travels inside the ANN query rather than filtering its output.
        # CLIP-SPEC rule 3 is a legal gate; a neighbour you may not publish is a trap,
        # not a ranking problem.
        corpus >> Edge(color=MEM, penwidth="3",
                       label="Q1 / Q2 filtered ANN\nrights.source in the predicate") >> worker
        ident >> Edge(color=MEM, penwidth="3",
                      label="the mold\n+ learned negatives") >> worker

        # ---- write back: the half that makes it memory ----------------------
        worker >> Edge(color=MEM, style="dashed", penwidth="3",
                       label="episode row\n+ Q3 likeness score") >> eps
        label >> Edge(color=MEM, style="dashed", penwidth="2.5",
                      label="human verdict", constraint="false") >> eps
        eps >> Edge(color=MEM, style="dashed", penwidth="2.5",
                    label="distil: repeated rejections\n→ identity_negative",
                    constraint="false") >> ident

        # ---- the agent ------------------------------------------------------
        agent >> Edge(color=MEM, style="dotted", label="embeddings") >> corpus
        label >> Edge(color=PLAIN, style="dotted", label="ask in English") >> agent
        agent >> Edge(color=PLAIN, style="dotted", label="MCP server") >> cat

    return out.with_suffix(".pdf")


def page_memory_loop(out: Path) -> Path:
    """The economics page, one layer up from page_economics.

    That page amortises a generation bill across every fan. This one amortises an
    onboarding cost across every video — and adds the claim that page could not make,
    which is that the per-video cost *falls* rather than merely being spread.
    """
    title = ("The mold is the fixed cost — memory is what makes each pour cheaper\n"
             "retrieve → generate → measure → judge → distil, and the cycle closes")
    with Diagram(title, filename=str(out), outformat="pdf", show=False,
                 direction="LR", graph_attr={**GRAPH, "ranksep": "2.2", "nodesep": "1.0"},
                 node_attr=NODE, edge_attr=EDGE):

        with Cluster("Once per ARTIST — the expensive step, paid at onboarding",
                     graph_attr={**CLUSTER, "bgcolor": "#FFF5F5", "penwidth": "3"}):
            shoot = Custom("reference frames\n5 setups + signed consent", B2)
            mold = Custom("identity — the mold\nface VECTOR(512)", CRDB)
            shoot >> Edge(color=PLAIN) >> mold

        with Cluster("Once per VIDEO — and it gets cheaper each time",
                     graph_attr={**CLUSTER, "bgcolor": "#F0FFF4", "penwidth": "3"}):
            retrieve = Custom("retrieve\nmold + negatives + clips", CRDB)
            gen = Batch("generate\nGenblaze · Fargate Spot")
            measure = Bedrock("measure\ncheck_likeness — Q3")
            judge = Custom("human verdict\napprove / reject", WWW)
            retrieve >> Edge(color=MEM, penwidth="3") >> gen
            gen >> Edge(color=PLAIN) >> measure
            measure >> Edge(color=PLAIN) >> judge

        with Cluster("What closes the cycle", graph_attr={**CLUSTER, "bgcolor": "#F5F0FF"}):
            ep = Custom("episode\nprompt · score · verdict", CRDB)
            distil = Custom("distil\n→ negatives + lessons", CRDB)
            metric = Cloudwatch("attempts per approved still\nthe falsifiable metric")
            ep >> Edge(color=MEM, style="dashed", penwidth="2.5") >> distil
            ep >> Edge(color=PLAIN, style="dotted") >> metric

        mold >> Edge(color=MEM, penwidth="3",
                     label="amortised across\nevery video") >> retrieve
        judge >> Edge(color=MEM, style="dashed", penwidth="3") >> ep

        # The back-edge is the entire argument, so it is drawn even though it fights the
        # left-to-right reading. constraint=false keeps it from dragging the ranks.
        distil >> Edge(color=MEM, style="dashed", penwidth="3", constraint="false",
                       label="next generation starts\nknowing what failed") >> retrieve

    return out.with_suffix(".pdf")


def page_system(out: Path) -> Path:
    title = ("RemixKit on AWS — scale-to-zero control plane, Backblaze B2 data plane\n"
             "every compute box bills only while it runs · idle cost is storage only")
    with Diagram(title, filename=str(out), outformat="pdf", show=False,
                 direction="TB", graph_attr=GRAPH, node_attr=NODE, edge_attr=EDGE):

        with Cluster("Clients", graph_attr={**CLUSTER, "bgcolor": "#F7FAFC"}):
            label = Custom("Label / Artist", WWW)
            creator = Custom("Creator / Fan", WWW)
            judge = Custom("Judge / Public", WWW)

        with Cluster("Edge — CloudFront", graph_attr={**CLUSTER, "bgcolor": "#FFF5F5"}):
            cdn = CloudFront("CloudFront\nimmutable · long TTL")
            edgefn = CloudFront("CF Function + KVS\n/r/{code} 302 at edge")

        with Cluster("AWS control plane — all scale-to-zero",
                     graph_attr={**CLUSTER, "bgcolor": "#F0F7FF"}):
            web = Lambda("web · FastAPI\nWeb Adapter + Function URL")

            # kit-worker is Batch, NOT Lambda. BUILD-SPEC §4 calls Genblaze with
            # timeout=900, which is exactly Lambda's 15-minute ceiling — no headroom for
            # cold start, provider retries or the upload afterwards. The most expensive
            # path in the system is the wrong place to sit on a hard cliff.
            with Cluster("Queued — bills only while running",
                         graph_attr={**CLUSTER, "bgcolor": "#FAF5FF"}):
                q = SQS("kit-jobs + DLQ\nidempotent, keyed")
                kitw = Batch("kit-worker\nGenblaze · Fargate Spot")
                compw = Batch("composite-worker\nffmpeg · Fargate Spot")

            with Cluster("Scheduled", graph_attr={**CLUSTER, "bgcolor": "#FAF5FF"}):
                sched = EventbridgeScheduler("EventBridge\nnightly")
                indexer = Lambda("indexer\ngenblaze index")

        with Cluster("AWS state", graph_attr={**CLUSTER, "bgcolor": "#F0FFF4"}):
            db = Aurora("Aurora Serverless v2\nmin 0 ACU · auto-pause")
            ssm = ParameterStore("SSM Parameter Store\nkeys")
            logs = Cloudwatch("CloudWatch\nlogs · metrics")

        with Cluster("Backblaze B2 — data plane, provenance, analytics",
                     graph_attr={**CLUSTER, "bgcolor": "#FFF5F5", "penwidth": "3"}):
            masters = Custom("masters/\nprivate", B2)
            runs = Custom("runs/\nmanifests + assets", B2)
            kits = Custom("kits/\npublic · immutable", B2)
            parquet = Athena("analytics/\nmanifests.parquet")

        with Cluster("AI providers — orchestrated by Genblaze",
                     graph_attr={**CLUSTER, "bgcolor": "#FFFAF0"}):
            gb = Custom("Genblaze Pipeline\nSHA-256 addressed", GENBLAZE)
            p1 = Custom("GMI Cloud\nvideo", GMI)
            p2 = Custom("GMI Cloud\nimage", GMI)
            p3 = Custom("ElevenLabs\naudio", ELEVEN)

        # ---- request path -------------------------------------------------
        [label, creator, judge] >> Edge(color=PLAIN) >> cdn
        creator >> Edge(color=PLAIN, style="dotted", label="share link") >> edgefn
        cdn >> Edge(color=PLAIN, label="API (JSON)") >> web

        # ---- the hot path: virality is a storage read, not a compute event --
        # constraint=false on both: these jump the full height of the graph, and letting
        # them influence rank assignment drags the whole layout right and leaves a third
        # of the page empty. They are drawn, but they do not get a vote on placement.
        cdn >> Edge(color=HOT, penwidth="3.5", label="cached reads",
                    constraint="false") >> kits
        creator >> Edge(color=DIRECT, style="dashed", penwidth="2.5",
                        label="presigned PUT/GET\nnever transits compute",
                        constraint="false") >> kits

        # ---- async work ----------------------------------------------------
        web >> Edge(color=ASYNC, style="dashed", label="enqueue") >> q
        q >> Edge(color=ASYNC, style="dashed") >> kitw
        q >> Edge(color=ASYNC, style="dashed") >> compw
        sched >> Edge(color=ASYNC, style="dashed") >> indexer

        # ---- generation ----------------------------------------------------
        kitw >> Edge(color=PLAIN) >> gb
        gb >> Edge(color=PLAIN) >> [p1, p2, p3]
        kitw >> Edge(color=HOT, penwidth="2", label="ObjectStorageSink") >> runs
        compw >> Edge(color=HOT, penwidth="2") >> kits
        masters >> Edge(color=PLAIN, style="dotted", label="hook window") >> kitw
        indexer >> Edge(color=PLAIN) >> parquet

        # ---- state ---------------------------------------------------------
        web >> Edge(color=PLAIN, style="dotted") >> db
        kitw >> Edge(color=PLAIN, style="dotted", label="cost ledger") >> db
        kitw >> Edge(color=PLAIN, style="dotted") >> ssm
        web >> Edge(color=PLAIN, style="dotted") >> logs

    return out.with_suffix(".pdf")


def page_economics(out: Path) -> Path:
    title = ("Generate once, remix infinitely — why cost per release is flat\n"
             "and provenance survives all the way to the finished fan clip")
    # LR flips which knob does the work: ranksep is now the HORIZONTAL gap, and that is
    # the axis the wide two-line captions collide along.
    with Diagram(title, filename=str(out), outformat="pdf", show=False,
                 direction="LR", graph_attr={**GRAPH, "ranksep": "2.4", "nodesep": "1.0"},
                 node_attr=NODE, edge_attr=EDGE):

        with Cluster("Once per RELEASE — the expensive step",
                     graph_attr={**CLUSTER, "bgcolor": "#FFF5F5", "penwidth": "3"}):
            master = Custom("owned master\n+ hook window", B2)
            run = Custom("Genblaze Run\nvideo+image+audio", GENBLAZE)
            kit = Custom("kit assets\n+ manifest.json", B2)
            master >> Edge(color=PLAIN) >> run >> Edge(color=HOT, penwidth="2.5") >> kit

        with Cluster("Once per FAN — the cheap step",
                     graph_attr={**CLUSTER, "bgcolor": "#F0FFF4", "penwidth": "3"}):
            fans = Custom("fan segments\npresigned PUT", WWW)
            comp = Batch("ffmpeg composite\nCPU-only · cents")
            outclip = Custom("finished clip\nmanifest in the mp4", B2)
            fans >> Edge(color=DIRECT, style="dashed", penwidth="2") >> comp
            comp >> Edge(color=HOT, penwidth="2.5") >> outclip

        with Cluster("What that buys", graph_attr={**CLUSTER, "bgcolor": "#F0F7FF"}):
            verify = Custom("public /verify\nmanifest.verify()", WWW)
            ledger = Aurora("cost ledger\nper run · per tenant")
            warehouse = Athena("manifests.parquet\nanalytics-ready")

        kit >> Edge(color=PLAIN, penwidth="2.5",
                    label="amortised across\nevery fan") >> fans
        outclip >> Edge(color=PLAIN) >> verify
        kit >> Edge(color=PLAIN, style="dotted") >> ledger
        kit >> Edge(color=PLAIN, style="dotted") >> warehouse

    return out.with_suffix(".pdf")


def bundle(pages: list[Path], final: Path) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    for p in pages:
        w.append(str(p))
    with open(final, "wb") as fh:
        w.write(fh)
    print(f"-> {final}  ({len(pages)} page{'s' if len(pages) > 1 else ''})")


def main() -> None:
    tmp = HERE / ".build"
    tmp.mkdir(exist_ok=True)

    # The one to build, and the one that ships Aug 3. Not touched by the memory branch.
    bundle([page_label_scope(tmp / "00-label-scope")], HERE / "architecture.pdf")

    # The branch taken after Aug 3 (MEMORY-SPEC.md). Adds the tier the file above drops.
    bundle([page_memory_branch(tmp / "10-memory-branch"),
            page_memory_loop(tmp / "11-memory-loop")],
           HERE / "memory-branch.pdf")

    # Kept, not deleted — role 2 is sequenced, not cancelled (PRODUCT.md).
    bundle([page_system(tmp / "01-system"), page_economics(tmp / "02-economics")],
           HERE / "deferred-marketplace.pdf")


if __name__ == "__main__":
    main()
