// =============================================================================
// types.ts — the contract this client assumes
// =============================================================================
//
// **PROVISIONAL.** These shapes were written alongside `/api/v1` rather than after
// it, so that the console and the API could be built at the same time. They are an
// expectation, not a transcript. Reconcile against `docs/reference/api-v1.md` and
// change *this file* when they disagree — every assumption the client makes about
// the wire is deliberately confined here so that reconciliation is one file and not
// a search.
//
// Where a field is optional below it is because the API may honestly not know it,
// not because it is convenient to leave out. `null` means "asked, and there is no
// answer"; `undefined` means "this endpoint does not carry that". The console
// renders those two differently and the difference matters — see `State`.

/** How a fact was obtained. The spine of the product; see the operator manual. */
export type Provenance = "measured" | "inferred" | "asserted";

/** The five outreach channels. Only `radio` is stocked today. */
export type Channel = "curator" | "ugc" | "press" | "radio" | "sync";

export type ContactState = "contactable" | "in_thread" | "declined" | "stale";

// ---------------------------------------------------------------- proposals ---

/**
 * A thing the system wants to do, or a decision it cannot make alone.
 *
 * This is the unit the console is built around. It deliberately carries its own
 * reasoning (`why`) and its own controls (`actions`) rather than leaving the
 * operator to reconstruct either — the whole difference between this console and a
 * table of rows is that a row makes you go and find out why it is there.
 */
export interface Proposal {
  id: string;
  /** `suggestion` — a match to confirm. `parked` — work stopped for a person. */
  kind: string;
  /** One line, in the operator's language, not the system's. */
  head: string;
  sub?: string;
  /** Drives the left border: how much this is asking of you. */
  tone: "act" | "warn" | "info";
  why: TraceStep[];
  actions: ProposalAction[];
  /** Set once acted on, so the row can settle rather than vanish. */
  settled?: { outcome: string; at: string } | null;
}

export interface TraceStep {
  /** Short uppercase label: `source`, `distance`, `lesson`, `rule`. */
  label: string;
  value: string;
  provenance?: Provenance;
}

export interface ProposalAction {
  /** Stable key the client posts back. */
  key: string;
  /** What the button says. Active voice, and the same word the result will use. */
  label: string;
  style?: "primary" | "quiet" | "danger";
  /** Present when the action is refused before it is attempted, with the reason. */
  refusedBecause?: string;
}

// ---------------------------------------------------------------- campaigns ---

export interface CampaignSummary {
  id: string;
  name: string;
  artist: string;
  channel: Channel;
  state: string;
  openThreads: number;
  awaitingApproval: number;
  sent: number;
}

/** A real fleet stage, not a metaphor. Counts come from the tables. */
export interface Stage {
  key: string;
  name: string;
  status: "idle" | "running" | "blocked" | "done";
  count: number | null;
  note?: string;
}

export interface Campaign extends CampaignSummary {
  goal: string;
  stages: Stage[];
  shortlist: RankedContact[];
  /** Null when the campaign has never been ranked, not zero. */
  rankedAt: string | null;
}

export interface RankedContact {
  id: string;
  rank: number;
  name: string;
  /** Cosine distance. Lower is nearer; the meter is scaled across the range. */
  distance: number;
  contactState: ContactState;
  why: TraceStep[];
  pinned?: boolean;
  vetoed?: boolean;
}

// -------------------------------------------------------------------- gate ---

/**
 * Whether anything can physically leave. The console states this persistently
 * because it is the product's central claim, and because "0 sent" is ambiguous
 * between "nothing was ready" and "the wire is not connected".
 */
export interface GateState {
  /** Always true today, and not switchable from inside the console. */
  humanApprovalRequired: boolean;
  /** False until a mail provider is wired. */
  senderConnected: boolean;
  queued: number;
  sentEver: number;
}

// ------------------------------------------------------------------ intent ---

/**
 * A campaign before it exists: a stated goal, decomposed into the stages that
 * would run and what each is allowed to spend.
 *
 * This is not a plan the model invents. The stages are the fleet's real stages and
 * the caps are `spend.py`'s real caps; the surface's job is to show them before you
 * commit rather than after, and to let you change them.
 */
export interface IntentPlan {
  goal: string;
  artistId: string;
  channel: Channel;
  recordingId?: string | null;
  stages: PlannedStage[];
  /** Micro-USD, matching the server. Rendered as dollars, never rounded up. */
  capMicroUsd: number;
}

export interface PlannedStage {
  key: string;
  name: string;
  /** What it would do, in the operator's language. */
  detail: string;
  /** Null when the cost genuinely cannot be estimated before the stage runs. */
  estimateMicroUsd: number | null;
  /** A stage that waits for a person. Cannot be switched off. */
  gate?: boolean;
  enabled: boolean;
}
