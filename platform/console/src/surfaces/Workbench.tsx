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
  Button, Empty, Failure, Meter, Trace, scaleDistances,
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
              // Every stage in the chain shares one mutation hook, so `isPending`
              // locks the whole chain. Without the `variables` comparison the
              // operator gets six identically-inert buttons and no way to tell
              // which stage they just restarted — on the one surface whose job is
              // showing where work currently is.
              <Button
                className="b q"
                style={{ alignSelf: "start" }}
                busy={rerun.isPending && rerun.variables === s.key}
                disabled={rerun.isPending}
                onClick={() => rerun.mutate(s.key)}
              >
                re-run from here
              </Button>
            ) : null}
          </div>
        ))}
      </div>
      {rerun.isError ? (
        <p className="sub" role="status" style={{ color: "var(--err)", marginTop: ".5rem" }}>
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

  // One hook serves three controls on every row in the list, so `isPending` alone
  // cannot say which press is in flight. This can, because the shortlist is not
  // optimistic: `r.pinned` still reads its pre-press value while the write is out,
  // so the action string computed here is the same one that was sent.
  const busyOn = (contactId: string, action: string) =>
    act.isPending
    && act.variables?.contactId === contactId
    && act.variables.action === action;

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
              <Button
                className="b q"
                busy={busyOn(r.id, r.pinned ? "unpin" : "pin")}
                disabled={act.isPending}
                onClick={() => act.mutate({
                  contactId: r.id, action: r.pinned ? "unpin" : "pin",
                })}
              >
                {r.pinned ? "unpin" : "pin"}
              </Button>
              <Button
                className="b q"
                busy={busyOn(r.id, r.vetoed ? "unveto" : "veto")}
                disabled={act.isPending}
                onClick={() => act.mutate({
                  contactId: r.id, action: r.vetoed ? "unveto" : "veto",
                })}
              >
                {r.vetoed ? "unveto" : "veto"}
              </Button>
              <Button
                className="b"
                busy={busyOn(r.id, "open")}
                disabled={act.isPending || r.vetoed || r.contactState !== "contactable"}
                title={
                  r.contactState !== "contactable"
                    ? `This contact reads ${r.contactState.replace("_", " ")}.`
                    : "Opens a conversation and reserves this contact label-wide."
                }
                onClick={() => act.mutate({ contactId: r.id, action: "open" })}
              >
                open
              </Button>
            </span>
          </li>
        ))}
      </ol>
      {/* A press that ends in nothing is worse than one that ends in a refusal:
          the trace stops, the list re-fetches unchanged, and the operator is left
          to work out whether the write failed or the server simply agreed with
          what was already there. `open` is refused by a unique index whenever
          another campaign holds the contact, so this is a routine outcome and it
          needs a sentence. */}
      {act.isError ? (
        <p className="sub" role="status" style={{ color: "var(--err)", margin: ".5rem .7rem" }}>
          {act.error instanceof Error ? act.error.message : "That was declined."}
        </p>
      ) : null}
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
          {/* `.eyebrow` is the same declaration `.panel > h2` already carried; this
              heading sits over a list rather than inside a panel, so it could not use
              that selector and was inlining the shorthand instead. An inlined 9.5px
              is a 9.5px no phone breakpoint can reach, which is the whole reason the
              rule got a name of its own. */}
          <h2 className="eyebrow">Shortlist</h2>
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
            {c.funnel.open_threads} open {c.funnel.open_threads === 1 ? "thread" : "threads"} ·{" "}
            {c.funnel.awaiting} awaiting approval · {c.funnel.sent} sent
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
  if (q.data.rows.length === 0) {
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
        {q.data.rows.map((c) => (
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
              {c.funnel.open_threads} open · {c.funnel.sent} sent
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
