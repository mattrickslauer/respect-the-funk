// =============================================================================
// Now.tsx — the proposal stream
// =============================================================================
//
// The first surface, and the argument for the whole console: it opens on what the
// system wants to do next rather than on a table of what exists.
//
// Two things make it agentic rather than a prettier Today list. Each item carries
// its own reasoning, one keystroke away, so "why is this here" never costs a
// navigation. And each item carries its own controls, so deciding is a press rather
// than a press plus a form plus a save.
//
// An empty stream is the correct and common state. It means every decision that
// could be made without a person already was, and it says so — the alternative,
// filling the screen with recent activity so it looks busy, would make the one
// screen that is supposed to demand attention stop demanding it.

import { useAct, useToday } from "../api/queries";
import { Empty, Failure, Trace } from "../components/primitives";
import type { Proposal, ProposalAction } from "../api/types";

function Action({
  action, pending, onAct,
}: {
  action: ProposalAction;
  pending: boolean;
  onAct: (key: string) => void;
}) {
  const cls =
    action.style === "primary" ? "b p" :
    action.style === "danger" ? "b d" :
    action.style === "quiet" ? "b q" : "b";

  // A refusal the server already knows about is shown on the control itself
  // rather than discovered by pressing it. The button stays visible — hiding it
  // would leave an operator wondering why an action they expected is absent.
  return (
    <button
      className={cls}
      disabled={pending || Boolean(action.refusedBecause)}
      title={action.refusedBecause ?? undefined}
      onClick={() => onAct(action.key)}
    >
      {pending ? "working…" : action.label}
      {action.refusedBecause ? " ·  refused" : ""}
    </button>
  );
}

function ProposalCard({ proposal }: { proposal: Proposal }) {
  const act = useAct();
  const pending = act.isPending && act.variables?.id === proposal.id;
  const settled = proposal.settled;

  return (
    <article className={`proposal ${settled ? "done" : proposal.tone}`}>
      <div className="kind">{proposal.kind.replace(/_/g, " ")}</div>
      <h2 className="head">{proposal.head}</h2>
      {proposal.sub ? <p className="sub">{proposal.sub}</p> : null}

      <Trace steps={proposal.why} />

      {settled ? (
        <p className="sub" style={{ marginTop: ".55rem" }}>
          {settled.outcome} · {settled.at}
        </p>
      ) : (
        <div className="acts">
          {proposal.actions.map((a) => (
            <Action
              key={a.key}
              action={a}
              pending={pending}
              onAct={(key) => act.mutate({ id: proposal.id, action: key })}
            />
          ))}
        </div>
      )}

      {/* A refusal that arrives from the server is rendered where the decision was
          made, not as a toast that disappears before it is read. */}
      {act.isError && act.variables?.id === proposal.id ? (
        <p className="sub" style={{ marginTop: ".5rem", color: "var(--err)" }}>
          {act.error instanceof Error ? act.error.message : "That was declined."}
        </p>
      ) : null}
    </article>
  );
}

export default function Now() {
  const today = useToday();

  return (
    <section className="surface">
      <header>
        <h1>Now</h1>
        <p>
          Everything asking something of you, and nothing else. Each item carries why
          it is here and what you can do about it. An empty list is the correct
          state — it means everything that could be decided without you already was.
        </p>
      </header>

      {today.isPending ? (
        <div className="state">Asking the cluster…</div>
      ) : today.isError ? (
        <Failure error={today.error} />
      ) : today.data.proposals.length === 0 ? (
        <Empty title="Nothing needs you.">
          The fleet decided everything it was allowed to decide. Work that needs a
          person lands here on its own; there is nothing to check.
        </Empty>
      ) : (
        today.data.proposals.map((p) => <ProposalCard key={p.id} proposal={p} />)
      )}
    </section>
  );
}
