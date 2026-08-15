// =============================================================================
// Now.tsx — the proposal stream
// =============================================================================
//
// The first surface, and the argument for the whole console: it opens on what the
// system wants to do next rather than on a table of what exists.
//
// Two things make it agentic rather than a prettier Today list. Each item carries
// its own reasoning, one keystroke away, so "why is this here" never costs a
// navigation. And each item carries its own controls — including the address to
// post them to, which the server sends — so deciding is a press rather than a press
// plus a form plus a save.
//
// An empty stream is the correct and common state. It means every decision that
// could be made without a person already was, and it says so, with the quiet
// counters underneath for context: "nothing needs you" reads very differently next
// to "2,626 leads pending" than it does next to nothing at all.

import { useAct, useToday } from "../api/queries";
import { ApiError } from "../api/client";
import { Empty, Failure, Trace } from "../components/primitives";
import type { TodayAction, TodayRow } from "../api/types";

function Action({
  action, row, pending, onAct,
}: {
  action: TodayAction;
  row: TodayRow;
  pending: boolean;
  onAct: () => void;
}) {
  const cls =
    action.style === "primary" ? "b p" :
    action.style === "danger" ? "b d" :
    action.style === "quiet" ? "b q" : "b";

  const count = action.per === "candidate" ? (row.candidates?.length ?? 0) : 1;

  // A refusal the server already knows about is shown on the control itself rather
  // than discovered by pressing it. The button stays visible — hiding it would leave
  // an operator wondering why an action they expected is absent.
  return (
    <button
      className={cls}
      disabled={pending || Boolean(action.refusedBecause) || count === 0}
      title={
        action.refusedBecause ??
        (action.per === "candidate"
          ? `Applies to all ${count} candidates, in one go.`
          : undefined)
      }
      onClick={onAct}
    >
      {pending ? "working…" : action.label}
      {action.per === "candidate" && count > 1 ? ` all ${count}` : ""}
      {action.refusedBecause ? " · refused" : ""}
    </button>
  );
}

function RowCard({ row }: { row: TodayRow }) {
  const act = useAct();
  const pending = act.isPending && act.variables?.row.id === row.id;
  const mine = act.variables?.row.id === row.id;

  return (
    <article className={`proposal ${row.tone}`}>
      <div className="kind">{row.kind.replace(/_/g, " ")}</div>
      <h2 className="head">{row.head}</h2>
      {row.sub ? <p className="sub">{row.sub}</p> : null}

      <Trace steps={row.why ?? []} />

      {/* The candidates behind a group, named. The manual says this arrives as one
          decision per artist, and an operator making that decision should be able to
          see what is in the lot before taking it. */}
      {row.candidates && row.candidates.length > 0 ? (
        <details className="trace">
          <summary>the {row.candidates.length} candidates</summary>
          <ol>
            {row.candidates.map((c) => (
              <li key={c.id}>
                <span>{c.kind}</span>
                <span>
                  {String(c.payload?.["label"] ?? c.party_name)}
                  {c.payload?.["confidence"] != null
                    ? ` · ${Number(c.payload["confidence"]).toFixed(2)}`
                    : ""}
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <div className="acts">
        {(row.actions ?? []).map((a) => (
          <Action
            key={a.key}
            action={a}
            row={row}
            pending={pending}
            onAct={() => act.mutate({ row, action: a })}
          />
        ))}
      </div>

      {/* A refusal that arrives from the server is rendered where the decision was
          made, not as a toast that disappears before it is read. */}
      {mine && act.isError ? (
        <p className="sub" style={{ marginTop: ".5rem", color: "var(--err)" }}>
          {act.error instanceof Error ? act.error.message : "That was declined."}
        </p>
      ) : null}
      {mine && act.isSuccess ? (
        <p className="sub" style={{ marginTop: ".5rem", color: "var(--ok)" }}>
          {act.data.applied} applied.
        </p>
      ) : null}
    </article>
  );
}

// The strip was written as a wall of inline styles, and inline styles are the one
// place a stylesheet cannot follow. That mattered more than tidiness: the labels were
// set at 10px, which is unreadable on a phone, and no media query in app.css could
// raise them because a `style` attribute outranks every one of them. It is a `.quiet`
// class now, so the touch block owns its size along with everything else. Same
// rendering at desktop, to the pixel — the declarations moved, they did not change.
function Quiet({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="quiet">
      {entries.map(([k, v]) => (
        <span key={k}>
          {k.replace(/_/g, " ")}{" "}
          <b>{typeof v === "number" ? v.toLocaleString() : String(v)}</b>
        </span>
      ))}
    </div>
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
      ) : !Array.isArray(today.data?.rows) ? (
        // A 200 whose body is not the shape this screen speaks. Rendering the empty
        // state here would be the same lie as rendering it for a 404: it would tell
        // an operator their queue is clear when it is unreadable. This screen exists
        // so they do not have to check the others, so it has to be trustworthy about
        // not knowing.
        <Failure
          title="The server answered with a shape this console does not understand."
          error={new ApiError(
            "broken",
            "unrecognised /today body",
            { detail: `expected an object with a "rows" array, got: ${
              JSON.stringify(today.data)?.slice(0, 300)}` },
          )}
        />
      ) : today.data.rows.length === 0 ? (
        <>
          <Empty title="Nothing needs you.">
            The fleet decided everything it was allowed to decide. Work that needs a
            person lands here on its own; there is nothing to check.
          </Empty>
          <Quiet counts={today.data.quiet} />
        </>
      ) : (
        <>
          {today.data.rows.map((r) => <RowCard key={r.id} row={r} />)}
          <Quiet counts={today.data.quiet} />
        </>
      )}
    </section>
  );
}
