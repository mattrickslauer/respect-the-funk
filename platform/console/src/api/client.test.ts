// The wire, and the five ways it fails.
//
// The taxonomy in `client.ts` is a claim about product behaviour — that an operator
// is told which of five different things went wrong — and a claim like that is
// worth exactly as much as its tests.

import { describe, expect, it, vi, afterEach } from "vitest";
import { ApiError, get, post } from "./client";

function reply(body: unknown, init: ResponseInit & { json?: boolean } = {}) {
  const { json = true, ...rest } = init;
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    headers: { "content-type": json ? "application/json" : "text/html" },
    ...rest,
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("get", () => {
  it("returns the parsed body on 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ proposals: [] })));
    await expect(get("/today")).resolves.toEqual({ proposals: [] });
  });

  it("reports a 404 as `missing`, not as empty data", async () => {
    // The endpoint not existing and the endpoint returning nothing are different
    // facts. Collapsing them would let an unbuilt screen read as a quiet afternoon.
    vi.stubGlobal("fetch", vi.fn(async () => reply({ detail: "no route" }, { status: 404 })));
    await expect(get("/today")).rejects.toMatchObject({ kind: "missing" });
  });

  it("treats an HTML 200 as signed out rather than a parse error", async () => {
    // The specific failure this guards: the session cookie expires, the Python side
    // answers with a 303 to `/`, fetch follows it, and the console receives 200 OK
    // full of landing-page HTML. Without the content-type check the operator gets
    // "Unexpected token '<'", which is true about the bytes and useless about the
    // situation.
    vi.stubGlobal("fetch", vi.fn(async () =>
      reply("<!doctype html><title>Respect the Funk</title>", { json: false })));

    const err = await get("/today").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("signedOut");
  });

  it("reports an unreachable server as `offline`", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("failed to fetch"); }));
    await expect(get("/today")).rejects.toMatchObject({ kind: "offline" });
  });

  it("carries the server's own sentence through verbatim", async () => {
    // The Python side explains refusals in plain words. That text is what an
    // operator quotes when they ask for help, so it must survive the wire unedited.
    vi.stubGlobal("fetch", vi.fn(async () =>
      reply({ detail: "Already queued." }, { status: 409 })));
    const err = await post("/proposals/x/approve").catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("refused");
    expect((err as ApiError).detail).toBe("Already queued.");
  });
});
