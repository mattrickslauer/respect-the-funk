// The proposal stream's honest states.
//
// These are the tests that matter for this surface, because several of these states
// look identical if you get them wrong — a blank screen — and mean opposite things.

import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Now from "./Now";

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Now />
    </QueryClientProvider>,
  );
}

function reply(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
    ...init,
  });
}

const EMPTY = { rows: [], returned: 0, quiet: { leads_pending: 2626 } };

const GROUP = {
  rows: [{
    id: "sug-1",
    kind: "suggestion_group",
    tone: "act",
    head: "3 candidate surfaces for Hallow Youth",
    sub: "deezer · best match 0.70 · found by search, not asserted",
    why: [{ label: "how it was found", value: "an agent searched a source by name",
            provenance: "inferred" }],
    candidates: [
      { id: "c1", party_id: "p", party_name: "Hallow Youth", party_slug: "hy",
        kind: "presence", payload: { label: "Hallow Youth", confidence: 0.7 } },
      { id: "c2", party_id: "p", party_name: "Hallow Youth", party_slug: "hy",
        kind: "presence", payload: { label: "Hallow Yth", confidence: 0.4 } },
    ],
    actions: [
      { key: "accept", label: "Accept", style: "primary",
        endpoint: "/api/v1/suggestions/{id}/accept", per: "candidate" },
      { key: "reject", label: "Reject", style: "danger",
        endpoint: "/api/v1/suggestions/{id}/reject", per: "candidate" },
    ],
  }],
  returned: 1,
  quiet: {},
};

afterEach(() => vi.unstubAllGlobals());

describe("Now", () => {
  it("says nothing needs you when the stream is genuinely empty", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(EMPTY)));
    mount();
    await waitFor(() =>
      expect(screen.getByText(/Nothing needs you/)).toBeTruthy());
    expect(screen.getByText(/decided everything it was allowed to decide/)).toBeTruthy();
  });

  it("shows the quiet counters alongside an empty list", async () => {
    // "Nothing needs you" reads very differently next to "2,626 leads pending".
    vi.stubGlobal("fetch", vi.fn(async () => reply(EMPTY)));
    mount();
    await waitFor(() => expect(screen.getByText("2,626")).toBeTruthy());
  });

  it("does NOT say nothing needs you when the endpoint is missing", async () => {
    // An unbuilt or broken endpoint rendering as "nothing needs you" would tell an
    // operator their queue is clear when it is unknown — and this screen exists
    // precisely so they do not have to check the others.
    vi.stubGlobal("fetch", vi.fn(async () => reply({ detail: "no route" }, { status: 404 })));
    mount();
    await waitFor(() => expect(screen.getByText(/not built yet/)).toBeTruthy());
    expect(screen.queryByText(/Nothing needs you/)).toBeNull();
  });

  it("does NOT say nothing needs you when the body is the wrong shape", async () => {
    // The regression for the outage that produced this boundary: the API returned
    // its listing envelope, the client read `.proposals`, and one undefined property
    // unmounted the whole application. Now an unrecognised shape is reported as one.
    vi.stubGlobal("fetch", vi.fn(async () => reply({ proposals: [] })));
    mount();
    await waitFor(() =>
      expect(screen.getByText(/shape this console does not understand/)).toBeTruthy());
    expect(screen.queryByText(/Nothing needs you/)).toBeNull();
  });

  it("renders a group with its reasoning, its candidates and its controls", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(GROUP)));
    mount();
    await waitFor(() =>
      expect(screen.getByText(/3 candidate surfaces/)).toBeTruthy());

    expect(screen.getByText("why")).toBeTruthy();
    expect(screen.getByText(/an agent searched a source by name/)).toBeTruthy();
    expect(screen.getByText("the 2 candidates")).toBeTruthy();
  });

  it("says how many a group action applies to, because one press means the lot", async () => {
    // The operator manual is explicit that a suggestion group is one decision per
    // artist. A button reading plain "Accept" next to five candidates invites the
    // reading that it accepts one of them.
    vi.stubGlobal("fetch", vi.fn(async () => reply(GROUP)));
    mount();
    const accept = await waitFor(() =>
      screen.getByRole("button", { name: /Accept all 2/ }));
    expect(accept.getAttribute("title")).toMatch(/all 2 candidates, in one go/);
  });

  it("disables an action the server has already refused, and says why", async () => {
    const refused = {
      ...GROUP,
      rows: [{
        ...GROUP.rows[0],
        actions: [{
          key: "accept", label: "Accept", style: "primary",
          endpoint: "/api/v1/suggestions/{id}/accept", per: "row",
          refusedBecause: "Already queued.",
        }],
      }],
    };
    vi.stubGlobal("fetch", vi.fn(async () => reply(refused)));
    mount();
    const btn = await waitFor(() => screen.getByRole("button", { name: /Accept/ }));
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("title")).toBe("Already queued.");
  });

  it("puts the busy state on the control that was pressed, not the whole row", async () => {
    // The row locks while a decision is in flight — a proposal is one decision, and
    // accepting it while a reject is out would be two. But only one of these controls
    // was pressed, and an operator watching five candidates fan out needs to know
    // which. Before this, both went equally inert and the row reported only that
    // *something* was happening.
    let release: (r: Response) => void = () => {};
    const pending = new Promise<Response>((res) => { release = res; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      String(url).includes("/accept") ? pending : reply(GROUP)));

    mount();
    const accept = await waitFor(() =>
      screen.getByRole("button", { name: /Accept all 2/ }));
    fireEvent.click(accept);

    await waitFor(() => expect(accept.getAttribute("aria-busy")).toBe("true"));
    expect(accept.className).toContain("busy");

    const reject = screen.getByRole("button", { name: /Reject all 2/ });
    expect((reject as HTMLButtonElement).disabled).toBe(true); // locked…
    expect(reject.className).not.toContain("busy");            // …but not working
    expect(reject.getAttribute("aria-busy")).toBe("false");

    release(reply({ ok: true }));
  });

  it("keeps an action's label steady across a press", async () => {
    // The label is the only record of what was pressed while the write is out.
    let release: (r: Response) => void = () => {};
    const pending = new Promise<Response>((res) => { release = res; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      String(url).includes("/accept") ? pending : reply(GROUP)));

    mount();
    const accept = await waitFor(() =>
      screen.getByRole("button", { name: /Accept all 2/ }));
    fireEvent.click(accept);

    await waitFor(() => expect(accept.getAttribute("aria-busy")).toBe("true"));
    expect(accept.textContent).toBe("Accept all 2");
    expect(screen.queryByText(/working/i)).toBeNull();

    release(reply({ ok: true }));
  });
});
