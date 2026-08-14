// =============================================================================
// plan.ts — what a campaign would actually do, composed from what is true
// =============================================================================
//
// The stages below are the campaign lifecycle the operator manual documents:
// shortlist, open, draft, approve, send. They are not the discovery fleet —
// `index_stations` and `harvest_contacts` stock the counterparty index and run
// whether or not any campaign exists — and conflating the two would tell an
// operator that creating a campaign triggers a harvest, which it does not.
//
// Every `feasibility` below is read off `/summary` or `/fleet`. None is asserted
// here. That is the whole reason this surface is worth having: it says "approving
// will not send anything today, because no mail provider is wired" *before* the
// campaign exists, rather than leaving an operator to infer it from a `sent` column
// that stays at zero.

import type { Channel, PlannedStage, Summary } from "../api/types";

/** Radio is the only channel with counterparties in the index today. */
export const STOCKED_CHANNELS: ReadonlySet<Channel> = new Set<Channel>(["radio"]);

export function planStages(
  channel: Channel,
  summary: Summary | undefined,
): PlannedStage[] {
  const stocked = STOCKED_CHANNELS.has(channel);

  // `undefined` is not `false`. When the summary has not arrived the answer is
  // "unknown", and the surface says so rather than showing a reassuring default.
  const unknown = summary === undefined;

  return [
    {
      key: "shortlist",
      name: "Shortlist",
      detail:
        "Rank counterparties on this channel by how well they suit this artist, " +
        "excluding anyone already in conversation with another campaign.",
      feasibility: unknown ? "unknown" : stocked ? "ready" : "blocked",
      note: unknown
        ? null
        : stocked
          ? null
          : "The counterparty index holds radio today. This channel will rank nobody.",
      gate: false,
    },
    {
      key: "open",
      name: "Open conversations",
      detail:
        "One at a time, one button per contact. Opening a conversation reserves " +
        "that contact for the whole label until it is closed.",
      feasibility: unknown ? "unknown" : stocked ? "ready" : "blocked",
      note: null,
      gate: false,
    },
    {
      key: "draft",
      name: "Draft the pitch",
      detail:
        "Write the subject and body, with the track's measured character and any " +
        "lessons carried over from earlier campaigns for this artist alongside.",
      feasibility: "ready",
      note: null,
      gate: false,
    },
    {
      key: "approve",
      name: "Approve",
      detail:
        "A person reads the message in full and approves or rejects it. This stage " +
        "cannot be switched off from inside the console.",
      feasibility: "ready",
      note: "Nothing leaves without this.",
      gate: true,
    },
    {
      key: "send",
      name: "Send",
      detail:
        "The approved message is recorded and queued, once and only once.",
      feasibility: unknown ? "unknown" : summary.sender_wired ? "ready" : "blocked",
      note: unknown
        ? null
        : summary.sender_wired
          ? null
          : "No mail provider is wired. Approving still prepares and records the " +
            "send; the message will not physically leave until one is connected.",
      gate: false,
    },
  ];
}
