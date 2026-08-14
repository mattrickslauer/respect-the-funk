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

// ------------------------------------------------------------------ summary ---

/**
 * The eleven numbers `/summary` returns — one round trip, and the endpoint the API
 * recommends polling instead of holding a changefeed open.
 *
 * `sender_wired` and `inbound_adapter_wired` are stated rather than implied, which
 * is what lets the console say *why* nothing has left: "3 queued" and "0 sent" is
 * ambiguous until you know whether a provider exists at all.
 *
 * Snake case, because it is the wire's own shape and translating it would create a
 * second vocabulary for the same eleven facts.
 */
export interface Summary {
  awaiting_human: number;
  open_threads: number;
  inbound: number;
  queued_unsent: number;
  running_campaigns: number;
  leads_pending: number;
  leads_failed: number;
  leads_due: number;
  suggestions_pending: number;
  sender_wired: boolean;
  inbound_adapter_wired: boolean;
}

// ---------------------------------------------------------------- campaigns ---

/** The funnel, grouped on the wire because it is one thing and wants an order. */
export interface Funnel {
  threads: number;
  open_threads: number;
  awaiting: number;
  queued: number;
  sent: number;
  replied: number;
  agreed: number;
  delivered: number;
}

export interface CampaignSummary {
  id: string;
  name: string;
  artist: string | null;
  track: string | null;
  channel: Channel;
  state: string;
  goal: string;
  started_at: string | null;
  funnel: Funnel;
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

// ------------------------------------------------------------------- fleet ---

/**
 * One worker, as `/fleet` reports it.
 *
 * `manifest` being null is the answer rather than a missing value: it means a
 * worker with runs and no declaration — one nobody declared.
 */
export interface FleetAgent {
  kind: string;
  state: "working" | "idle" | "off" | "declared" | string;
  has_implementation: boolean;
  leases_held: number;
  work_waiting: number;
  manifest: {
    work_table: string;
    writes: string[];
    requires_human: boolean;
    enabled: boolean;
    per_artist_cap: number | null;
    batch_size: number | null;
  } | null;
  runs: {
    total: number;
    last_hour: number;
    failed: number;
    refused: number;
    last_run: string | null;
    cost_micro_usd: number;
  };
}

export interface Budget {
  id: string;
  name: string;
  cap: number;
  paused: boolean;
  spent: number;
  pending: number;
  cost_micro_usd_24h: number;
}

// ------------------------------------------------------------------ intent ---

/**
 * A campaign before it exists: a stated goal, and the stages that would actually
 * run against it.
 *
 * Nothing here is invented, and that is the whole point of the surface. The stages
 * are the fleet's declared agents read from `/fleet`; `gate` is the manifest's own
 * `requires_human` rather than an assumption this client makes; and the per-run cost
 * is measured — total spend divided by total runs — with an em-dash where a stage has
 * never run, because a stage with no history has no honest estimate.
 *
 * Composed on the client rather than asked for as `/intent/plan`, because every
 * input already has an endpoint and a server-side planner would be a second place
 * that knows what a campaign does.
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
  /** What the stage does, in the operator's language. */
  detail: string;
  /**
   * Whether this stage can actually run right now, and why not when it cannot.
   *
   * `blocked` is the honest one and the reason the surface earns its place: the
   * send stage is blocked today because no mail provider is wired, and a plan that
   * did not say so would promise something the system cannot do. Derived from
   * `/summary` and `/fleet`, never asserted by the client.
   */
  feasibility: "ready" | "blocked" | "unknown";
  note: string | null;
  /** A stage that waits for a person. Cannot be switched off. */
  gate: boolean;
}
