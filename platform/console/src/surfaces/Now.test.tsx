// The proposal stream's two honest states.
//
// These are the tests that matter for this surface, because the two states look
// identical if you get them wrong — a blank screen — and mean opposite things.

import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

afterEach(() => vi.unstubAllGlobals());

describe("Now", () => {
  it("says nothing needs you when the stream is genuinely empty", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ proposals: [] })));
    mount();
    await waitFor(() =>
      expect(screen.getByText(/Nothing needs you/)).toBeTruthy());
    expect(screen.getByText(/decided everything it was allowed to decide/)).toBeTruthy();
  });

  it("does NOT say nothing needs you when the endpoint is missing", async () => {
    // The load-bearing one. An unbuilt or broken endpoint rendering as "nothing
    // needs you" would tell an operator their queue is clear when it is unknown —
    // and this screen exists precisely so they do not have to check the others.
    vi.stubGlobal("fetch", vi.fn(async () => reply({ detail: "no route" }, { status: 404 })));
    mount();
    await waitFor(() =>
      expect(screen.getByText(/not built yet/)).toBeTruthy());
    expect(screen.queryByText(/Nothing needs you/)).toBeNull();
  });

  it("renders a proposal with its reasoning and its controls", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({
      proposals: [{
        id: "p1",
        kind: "profile_suggestion",
        head: "Three candidate pages for Hallow Youth",
        sub: "Accept or reject the lot.",
        tone: "act",
        why: [
          { label: "source", value: "spotify search", provenance: "inferred" },
          { label: "distance", value: "0.0912" },
        ],
        actions: [
          { key: "accept", label: "Accept", style: "primary" },
          { key: "reject", label: "Reject", style: "danger" },
        ],
      }],
    })));
    mount();
    await waitFor(() =>
      expect(screen.getByText(/Three candidate pages/)).toBeTruthy());

    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
    // The reasoning is present and one keystroke away, not on another page.
    expect(screen.getByText("why")).toBeTruthy();
    expect(screen.getByText("spotify search")).toBeTruthy();
  });

  it("disables an action the server has already refused, and says why", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({
      proposals: [{
        id: "p2", kind: "draft", head: "Pitch to KEXP", tone: "act", why: [],
        actions: [{
          key: "approve", label: "Approve & queue", style: "primary",
          refusedBecause: "Already queued.",
        }],
      }],
    })));
    mount();
    const btn = await waitFor(() =>
      screen.getByRole("button", { name: /Approve & queue/ }));
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("title")).toBe("Already queued.");
  });
});
