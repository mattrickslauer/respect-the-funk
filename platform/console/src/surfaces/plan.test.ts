// The intent surface's only real claim: that it says what a campaign would do
// without promising anything the system cannot currently do.

import { describe, expect, it } from "vitest";
import { planStages } from "./plan";
import type { Summary } from "../api/types";

const SUMMARY: Summary = {
  awaiting_human: 0, open_threads: 0, inbound: 0, queued_unsent: 3,
  running_campaigns: 0, leads_pending: 0, leads_failed: 0, leads_due: 0,
  suggestions_pending: 0, sender_wired: false, inbound_adapter_wired: false,
};

const stage = (channel: Parameters<typeof planStages>[0], s: Summary | undefined, key: string) =>
  planStages(channel, s).find((x) => x.key === key)!;

describe("planStages", () => {
  it("marks send as blocked while no mail provider is wired, and says why", () => {
    // The load-bearing case. A plan that showed "send · ready" on a system with no
    // sender would promise something that cannot happen, and the operator would be
    // left to infer it from a `sent` column that stays at zero.
    const send = stage("radio", SUMMARY, "send");
    expect(send.feasibility).toBe("blocked");
    expect(send.note).toMatch(/No mail provider is wired/);
    expect(send.note).toMatch(/still prepares and records/);
  });

  it("marks send ready once a sender exists", () => {
    const send = stage("radio", { ...SUMMARY, sender_wired: true }, "send");
    expect(send.feasibility).toBe("ready");
    expect(send.note).toBeNull();
  });

  it("says unknown rather than ready when the summary has not arrived", () => {
    // `undefined` is not `false`. An unchecked stage must not read as healthy.
    for (const key of ["shortlist", "open", "send"]) {
      expect(stage("radio", undefined, key).feasibility).toBe("unknown");
    }
  });

  it("blocks the shortlist on a channel with no counterparties", () => {
    const short = stage("curator", SUMMARY, "shortlist");
    expect(short.feasibility).toBe("blocked");
    expect(short.note).toMatch(/holds radio today/);
  });

  it("leaves the shortlist ready on radio", () => {
    expect(stage("radio", SUMMARY, "shortlist").feasibility).toBe("ready");
  });

  it("always marks approve as a gate, on every channel", () => {
    // The manual says this cannot be switched off from inside the console. If a
    // channel ever renders without it, the console is misrepresenting the product's
    // central guarantee.
    for (const c of ["radio", "curator", "press", "ugc", "sync"] as const) {
      const gates = planStages(c, SUMMARY).filter((s) => s.gate);
      expect(gates.map((g) => g.key)).toEqual(["approve"]);
    }
  });

  it("keeps the lifecycle in order", () => {
    expect(planStages("radio", SUMMARY).map((s) => s.key))
      .toEqual(["shortlist", "open", "draft", "approve", "send"]);
  });
});
