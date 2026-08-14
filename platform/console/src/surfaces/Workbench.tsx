// =============================================================================
// Workbench.tsx — a campaign as the signal chain it is
// =============================================================================
//
// The second surface. A campaign is not a record with fields; it is work moving
// through stages, and this draws it that way — discover, rank, open, draft,
// approve, send — with each stage's real count under it and a pip saying whether
// it is running, waiting on a person, or done.
//
// The fine control lives here. You can reach into the ranking, pin a contact you
// know is right, veto one you know is wrong, and re-run the stage from there. What
// you cannot do is open conversations in bulk: there is one button per contact and
// no button for the batch, because opening a conversation takes that contact off
// the market for every other campaign in the label, and a bulk control would hide
// that cost at exactly the moment you incur it. That is a product rule, not a
// missing feature, and the note under the list says so.

import { Link, useParams } from "react-router-dom";
import {
  useCampaign, useCampaigns, useRerunStage, useShortlistAct,
} from "../api/queries";
import {
  Empty, Failure, Meter, Trace, scaleDistances,
} from "../components/primitives";
import type { Campaign, RankedContact, Stage } from "../api/types";

function StageChain({ campaign }: { campaign: Campaign }) {
  const rerun = useRerunStage(campaign.id);
  return (
    <>
      <div className="chain">
        {campaign.stages.map((s: Stage) => (
          <div key={s.key} className={`stage ${s.status}`}>
            <div className="name">
              <span className="pip" />
              {s.name}
            </div>
            {/* Null is not zero. "Never ranked" and "ranked, found nobody" are
                different facts and an operator acts differently on each. */}
            <div className="n">{s.count === null ? "—" : s.count.toLocaleString()}</div>
            {s.note ? <div className="note">{s.note}</div> : null}
            {s.status !== "running" ? (
              <button
                className="b q"
                style={{ alignSelf: "start" }}
                disabled={rerun.isPending}
                onClick={() => rerun.mutate(s.key)}
              >
                re-run from here
              </button>
            ) : null}
          </div>
        ))}
      </div>
      {rerun.isError ? (
        <p className="sub" style={{ color: "var(--err)", marginTop: ".5rem" }}>
          {rerun.error instanceof Error ? rerun.error.message : "That was declined."}
        </p>
      ) : null}
    </>
  );
}

function Shortlist({ campaign }: { campaign: Campaign }) {
  const act = useShortlistAct(campaign.id);
  const list = campaign.shortlist;

  if (list.length === 0) {
    return (
      <Empty title="Nothing ranked yet.">
        {campaign.rankedAt === null
          ? "This campaign has never been ranked. Run the rank stage above."
          : "The ranking returned nobody. Everyone suitable may already be in " +
            "conversation with another campaign — they are absent here rather than " +
            "greyed out, so this list is people you can genuinely approach now."}
      </Empty>
    );
  }

  const scale = scaleDistances(list.map((r) => r.distance));

  return (
    <div className="ranked">
      <ol>
        {list.map((r: RankedContact) => (
          <li
            key={r.id}
            className={r.vetoed ? "vetoed" : r.pinned ? "pinned" : ""}
          >
            <span className="rank">{String(r.rank).padStart(2, "0")}</span>
            <span>
              <span className="who">{r.name}</span>
              <Trace steps={r.why} />
            </span>
            <Meter
              fill={scale(r.distance)}
              label={`distance ${r.distance.toFixed(4)}`}
            />
            <span className="row">
              <span className="d">{r.distance.toFixed(4)}</span>
              <button
                className="b q"
                disabled={act.isPending}
                onClick={() => act.mutate({
                  contactId: r.id, action: r.pinned ? "unpin" : "pin",
                })}
              >
                {r.pinned ? "unpin" : "pin"}
              </button>
              <button
                className="b q"
                disabled={act.isPending}
                onClick={() => act.mutate({
                  contactId: r.id, action: r.vetoed ? "unveto" : "veto",
                })}
              >
                {r.vetoed ? "unveto" : "veto"}
              </button>
              <button
                className="b"
                disabled={act.isPending || r.vetoed || r.contactState !== "contactable"}
                title={
                  r.contactState !== "contactable"
                    ? `This contact reads ${r.contactState.replace("_", " ")}.`
                    : "Opens a conversation and reserves this contact label-wide."
                }
                onClick={() => act.mutate({ contactId: r.id, action: "open" })}
              >
                open
              </button>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function CampaignView({ id }: { id: string }) {
  const q = useCampaign(id);

  if (q.isPending) return <div className="state">Asking the cluster…</div>;
  if (q.isError) return <Failure error={q.error} />;
  const c = q.data;

  return (
    <>
      <header>
        <h1>{c.name}</h1>
        <p>
          {c.artist} · {c.channel} · {c.state}
          {c.goal ? <> — “{c.goal}”</> : null}
        </p>
      </header>

      <StageChain campaign={c} />

      <div className="grid2" style={{ marginTop: "1.1rem" }}>
        <div>
          <h2
            style={{
              font: "600 9.5px/1 var(--face-display)",
              letterSpacing: ".12em",
              textTransform: "uppercase",
              color: "var(--faint)",
              margin: "0 0 .6rem",
            }}
          >
            Shortlist
          </h2>
          <Shortlist campaign={c} />
          <p className="sub" style={{ maxWidth: "76ch" }}>
            One button per contact, and none for the batch. Opening a conversation
            reserves that contact for the whole label until it is closed, so it is a
            decision made one at a time on purpose.
          </p>
        </div>

        <aside className="panel">
          <h2>This campaign</h2>
          <p className="sub" style={{ margin: 0 }}>
            {c.openThreads} open {c.openThreads === 1 ? "thread" : "threads"} ·{" "}
            {c.awaitingApproval} awaiting approval · {c.sent} sent
          </p>
          <p className="sub">
            {c.rankedAt === null
              ? "Never ranked."
              : `Ranked ${c.rankedAt}. The ranking as it stood then is still queryable, which is how "why did we write to them" has an answer weeks later.`}
          </p>
        </aside>
      </div>
    </>
  );
}

function CampaignList() {
  const q = useCampaigns();
  if (q.isPending) return <div className="state">Asking the cluster…</div>;
  if (q.isError) return <Failure error={q.error} />;
  if (q.data.campaigns.length === 0) {
    return (
      <Empty title="No campaigns yet.">
        A campaign is one artist, one channel, one goal. Start one on{" "}
        <Link to="/new">New</Link>, where you state the goal and see what it would
        do before it does it.
      </Empty>
    );
  }
  return (
    <div className="ranked">
      <ul>
        {q.data.campaigns.map((c) => (
          <li key={c.id}>
            <span className="rank" />
            <span>
              <Link className="who" to={`/campaigns/${c.id}`}>{c.name}</Link>
              <span className="sub" style={{ display: "block" }}>
                {c.artist} · {c.channel}
              </span>
            </span>
            <span className="chip">{c.state}</span>
            <span className="d">
              {c.openThreads} open · {c.sent} sent
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function Workbench() {
  const { id } = useParams<{ id: string }>();
  return (
    <section className="surface">
      {id ? (
        <CampaignView id={id} />
      ) : (
        <>
          <header>
            <h1>Campaigns</h1>
            <p>
              One artist, one channel, one goal. Open one to watch its stages and
              reach into the ranking.
            </p>
          </header>
          <CampaignList />
        </>
      )}
    </section>
  );
}
