// =============================================================================
// primitives.tsx — the shared vocabulary, as components
// =============================================================================
//
// Every one of these renders a class the design system already defines. None of
// them declares a colour or a size. That is the rule that keeps this console, the
// landing page and the operator manual looking like one product: the manual teaches
// `● measured` and this file is what draws it.

import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Provenance, TraceStep } from "../api/types";
import { ApiError } from "../api/client";

// ----------------------------------------------------------------- button ---

/**
 * A control that writes, and can say so while it is waiting.
 *
 * `busy` is deliberately per-*button*, not per-mutation. A react-query mutation
 * hook is shared by every control it serves — one `useShortlistAct` backs pin,
 * veto and open on every row — so `isPending` is true on all of them at once.
 * Wiring the visual state to that is what produced the old behaviour: press pin,
 * and all three controls on the row went equally inert, which reported that
 * *something* was in flight but not which thing. Callers pass `busy` by
 * comparing `mutation.variables` against the press this button represents.
 *
 * Busy implies disabled, because the alternative is a second POST for a write
 * the server may already have applied. It does not imply the disabled *look*:
 * the stylesheet keeps this one at full strength while its neighbours fade, and
 * that contrast is the answer to "which one did I press".
 */
export function Button({
  busy = false,
  className = "b",
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      {...rest}
      className={busy ? `${className} busy` : className}
      disabled={disabled || busy}
      aria-busy={busy}
    />
  );
}

// ------------------------------------------------------------------ meter ---

/**
 * A bounded quantity, drawn as signal strength.
 *
 * `fill` is a percentage the *caller* has already decided is meaningful, and that
 * responsibility does not belong here for a reason worth stating: cosine distances
 * cluster tightly, so a bar scaled 0–1 is always nearly full and tells you nothing.
 * Scale across the range you actually returned. An honest bar that is all-full is
 * worse than no bar.
 */
export function Meter({ fill, label }: { fill: number; label?: string }) {
  const pct = Math.max(0, Math.min(100, fill));
  return (
    <span
      className="meter"
      role="img"
      aria-label={label ?? `${Math.round(pct)} per cent`}
      style={{ ["--fill" as string]: `${pct}%` }}
    >
      <i />
    </span>
  );
}

/** Scale a set of distances across their own range. Nearer reads longer. */
export function scaleDistances(distances: number[]): (d: number) => number {
  if (distances.length === 0) return () => 0;
  const lo = Math.min(...distances);
  const hi = Math.max(...distances);
  const span = hi - lo || 1;
  return (d) => 96 - ((d - lo) / span) * 74;
}

// ------------------------------------------------------------- provenance ---

export function Prov({ kind, children }: { kind: Provenance; children?: ReactNode }) {
  return <span className={`prov ${kind}`}>{children ?? kind}</span>;
}

// ------------------------------------------------------------------ trace ---

/**
 * Why the system is proposing this.
 *
 * A disclosure rather than a tooltip, because an operator reads it while deciding
 * and a tooltip cannot be read and compared at the same time. Closed by default:
 * the point is that it is one keystroke away, not that it is always on screen.
 */
export function Trace({ steps, label = "why" }: { steps: TraceStep[]; label?: string }) {
  if (steps.length === 0) return null;
  return (
    <details className="trace">
      <summary>{label}</summary>
      <ol>
        {steps.map((s, i) => (
          <li key={`${s.label}-${i}`}>
            <span>{s.label}</span>
            <span>
              {s.value}
              {s.provenance ? <> <Prov kind={s.provenance} /></> : null}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

// ------------------------------------------------------------------- gate ---

/**
 * Whether anything can physically leave the building.
 *
 * Stated persistently in the top bar, because "0 sent" is ambiguous between
 * "nothing was ready to go" and "the wire is not connected", and an operator who
 * reads the first when the second is true will wait for replies that cannot come.
 */
export function Gate({
  humanApprovalRequired,
  senderConnected,
  queued,
}: {
  humanApprovalRequired: boolean;
  senderConnected: boolean;
  queued: number;
}) {
  if (!senderConnected) {
    return (
      <span className="gate dark" title="Approving prepares and records a send. It leaves once a mail provider is connected.">
        <span className="dot" />
        no sender connected · <b>{queued}</b> queued
      </span>
    );
  }
  return (
    <span className="gate held" title="Nothing leaves without a person pressing a button.">
      <span className="dot" />
      {humanApprovalRequired ? <>gate <b>held by a person</b></> : <b>gate open</b>}
      {queued > 0 ? <> · <b>{queued}</b> queued</> : null}
    </span>
  );
}

// ------------------------------------------------------------ empty/error ---

/**
 * An empty screen names what is missing. That sentence is usually the most useful
 * text on the page, so it is a required prop rather than a default.
 */
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <b>{title}</b>
      {children}
    </div>
  );
}

/**
 * A failure, rendered as itself.
 *
 * Each kind gets its own sentence and its own next step. None of them apologises,
 * and none of them is vague about what happened — the server's own words are shown
 * verbatim underneath, because that text is what an operator quotes when they ask
 * for help.
 */
export function Failure({ error, title }: { error: unknown; title?: string }) {
  if (!(error instanceof ApiError)) {
    return (
      <div className="state bad">
        <b>The console hit a fault it does not recognise.</b>
        <code>{error instanceof Error ? error.message : String(error)}</code>
      </div>
    );
  }

  const say: Record<string, { title: string; next: string }> = {
    offline: {
      title: "The server did not answer.",
      next: "The cluster scales to zero, so the first request after an idle period can take a moment. Try again.",
    },
    signedOut: {
      title: "This session is not signed in.",
      next: "Open /signin and paste your token. Your work is not lost.",
    },
    missing: {
      title: "That part of the API is not built yet.",
      next: "This screen is showing you nothing because there is nothing to ask, not because there is no work.",
    },
    refused: {
      title: "The server declined that.",
      next: "The reason is below, and it is usually a rule working rather than a fault.",
    },
    broken: {
      title: "The server failed.",
      next: "Quote the text below on Runs & errors when you ask for help.",
    },
  };
  const s = say[error.kind] ?? say["broken"]!;

  // A caller may override the headline when it knows something the kind does not.
  // The shape-mismatch case is the reason: it arrives as `broken`, but "the server
  // failed" is false — the server answered perfectly well, in a dialect this console
  // does not speak, and telling an operator the server is down would send them to
  // look at the wrong thing.
  return (
    <div className={`state ${error.kind === "missing" ? "" : "bad"}`}>
      <b>{title ?? s.title}</b>
      {s.next}
      {error.detail ? <code>{error.detail}</code> : null}
    </div>
  );
}

// ------------------------------------------------------------------ money ---

/**
 * Micro-USD as dollars.
 *
 * Always rounded *down*. A budget display that rounds up shows a cap as breached
 * when it is not, and one that rounds to the nearest can show $0.00 for money that
 * was genuinely spent. Four decimal places because the amounts here are small
 * enough that two would read as zero.
 */
export function money(microUsd: number): string {
  const dollars = Math.floor(microUsd / 100) / 10000;
  return `$${dollars.toFixed(4)}`;
}
