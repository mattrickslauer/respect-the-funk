#!/usr/bin/env python3
"""Render the RemixKit AWS architecture to a two-page PDF.

    python3 make_icons.py && python3 diagram.py

Page 1 is the system: who calls what, and which boxes cost money when nobody is using
them (none of them). Page 2 is the argument the system exists to make — generate once,
remix infinitely — drawn as a flow so the unit economics and the provenance lineage are
the same picture.

The diagram is generated rather than drawn so it cannot drift from the writeup: the node
labels carry the actual configuration (`min 0 ACU`, `Function URL`, `Fargate Spot`), and
changing the architecture means changing this file.

Requires: graphviz on PATH (`brew install graphviz`), plus requirements.txt.
"""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import Athena
from diagrams.aws.compute import Batch, Lambda
from diagrams.aws.database import Aurora
from diagrams.aws.integration import SQS, EventbridgeScheduler
from diagrams.aws.management import Cloudwatch, ParameterStore
from diagrams.aws.network import CloudFront
from diagrams.custom import Custom

HERE = Path(__file__).parent
A = HERE / "assets"

B2 = str(A / "backblaze-b2.png")
GENBLAZE = str(A / "genblaze.png")
GMI = str(A / "gmicloud.png")
ELEVEN = str(A / "elevenlabs.png")
WWW = str(A / "browser.png")

# Palette. Hot path is the one a viral release actually exercises; it is drawn heaviest
# because the whole design is about keeping compute off it.
HOT = "#D32127"      # storage/CDN read path — thick, red
ASYNC = "#7B61FF"    # queued work — dashed, purple
DIRECT = "#0B7A4B"   # browser <-> B2 presigned, never through compute — dashed, green
PLAIN = "#4A5568"

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


def main() -> None:
    from pypdf import PdfWriter

    tmp = HERE / ".build"
    tmp.mkdir(exist_ok=True)
    pages = [page_system(tmp / "01-system"), page_economics(tmp / "02-economics")]

    w = PdfWriter()
    for p in pages:
        w.append(str(p))
    final = HERE / "architecture.pdf"
    with open(final, "wb") as fh:
        w.write(fh)
    print(f"-> {final}  ({len(pages)} pages)")


if __name__ == "__main__":
    main()
