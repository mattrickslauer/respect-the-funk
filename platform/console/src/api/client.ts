// =============================================================================
// client.ts — one fetch wrapper, and a taxonomy of failure worth rendering
// =============================================================================
//
// The Python side explains refusals in plain words rather than error codes, and
// this file exists so that character survives the wire. A console that renders
// every non-200 as "Something went wrong" throws away the most useful thing the
// server said.
//
// The five outcomes are genuinely different to an operator and get different
// screens:
//
//   offline   the request never reached anything. Cluster scales to zero, so the
//             first call after an idle period can legitimately be slow.
//   signedOut the session is gone. Not an error — go and sign in.
//   missing   the endpoint is not built. Says so, rather than rendering empty and
//             letting an operator conclude there is no work.
//   refused   the server understood and declined, with a reason meant for a human.
//             "Already queued" is this, and it is the duplicate protection working.
//   broken    an actual fault. Carries the server's text so it can be quoted.
//
// No silent fallbacks anywhere in this file. A caller that cannot get data gets an
// error it must render, never an empty array that looks like a quiet afternoon.

export type FailureKind = "offline" | "signedOut" | "missing" | "refused" | "broken";

export class ApiError extends Error {
  readonly kind: FailureKind;
  readonly status: number | null;
  /** The server's own sentence, when it sent one. Rendered verbatim. */
  readonly detail: string | null;

  constructor(kind: FailureKind, message: string, opts: {
    status?: number | null;
    detail?: string | null;
  } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = opts.status ?? null;
    this.detail = opts.detail ?? null;
  }
}

const BASE = "/api/v1";

async function readDetail(res: Response): Promise<string | null> {
  // FastAPI puts the human sentence in `detail`; some handlers use `message`.
  // Reading the body twice is not possible, so this is the one attempt.
  try {
    const text = await res.text();
    if (!text) return null;
    try {
      const body: unknown = JSON.parse(text);
      if (body && typeof body === "object") {
        const rec = body as Record<string, unknown>;
        for (const key of ["detail", "message", "error"]) {
          const v = rec[key];
          if (typeof v === "string" && v) return v;
        }
      }
    } catch {
      // Not JSON. The raw text is still the most useful thing we have.
    }
    return text.slice(0, 600);
  } catch {
    return null;
  }
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(init.headers ?? {}) },
      ...init,
    });
  } catch (cause) {
    throw new ApiError(
      "offline",
      "The console could not reach the server.",
      { detail: cause instanceof Error ? cause.message : null },
    );
  }

  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  const detail = await readDetail(res);

  // 303 is how the Python side redirects an unauthenticated browser to the landing
  // page. `fetch` follows redirects by default, so this is more likely to arrive as
  // an opaque 200 of HTML — hence the content-type check in `expectJson` below.
  if (res.status === 401 || res.status === 403 || res.status === 303) {
    throw new ApiError("signedOut", "This session is not signed in.", {
      status: res.status, detail,
    });
  }
  if (res.status === 404) {
    throw new ApiError("missing", `No endpoint at ${path}.`, {
      status: 404, detail,
    });
  }
  if (res.status === 409 || res.status === 422 || res.status === 400) {
    throw new ApiError("refused", detail ?? "The server declined that.", {
      status: res.status, detail,
    });
  }
  throw new ApiError("broken", `The server failed on ${path}.`, {
    status: res.status, detail,
  });
}

/**
 * A GET that insists on JSON.
 *
 * Worth its own function because of one specific failure: when the session cookie
 * has expired, the Python side answers a console request with a 303 to `/`, `fetch`
 * follows it, and the console receives 200 OK with the landing page's HTML in the
 * body. `JSON.parse` then fails with "Unexpected token '<'", which is a true
 * statement about the bytes and a useless one about the situation. Checking the
 * content type turns it back into "you are signed out".
 */
export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  }).catch((cause: unknown) => {
    throw new ApiError("offline", "The console could not reach the server.", {
      detail: cause instanceof Error ? cause.message : null,
    });
  });

  const type = res.headers.get("content-type") ?? "";
  if (res.ok && !type.includes("json")) {
    throw new ApiError(
      "signedOut",
      "This session is not signed in.",
      { status: res.status, detail: "The server answered with a page, not data." },
    );
  }
  if (!res.ok) {
    const detail = await readDetail(res);
    if (res.status === 404) {
      throw new ApiError("missing", `No endpoint at ${path}.`, { status: 404, detail });
    }
    if (res.status === 401 || res.status === 403) {
      throw new ApiError("signedOut", "This session is not signed in.", {
        status: res.status, detail,
      });
    }
    throw new ApiError("broken", `The server failed on ${path}.`, {
      status: res.status, detail,
    });
  }
  return (await res.json()) as T;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}
