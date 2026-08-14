import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApiError } from "../api/client";
import { Failure, Gate, money, scaleDistances } from "./primitives";

describe("money", () => {
  it("rounds down, never up", () => {
    // A budget display that rounds up shows a cap as breached when it is not.
    expect(money(1_999_999)).toBe("$1.9999");
    expect(money(999_999)).toBe("$0.9999");
  });

  it("does not render real spend as zero", () => {
    // Two decimal places would show $0.00 for money that was genuinely spent.
    expect(money(1_234)).toBe("$0.0012");
    expect(money(0)).toBe("$0.0000");
  });
});

describe("scaleDistances", () => {
  it("spreads a tight cluster across the bar", () => {
    // Cosine distances cluster: 0.0912 and 0.0925 are meaningfully different and a
    // 0–1 scale draws them as identical full bars. Scaling across the returned
    // range is what makes the meter informative rather than decorative.
    const scale = scaleDistances([0.0912, 0.0918, 0.0925]);
    const [near, mid, far] = [scale(0.0912), scale(0.0918), scale(0.0925)];
    expect(near).toBeGreaterThan(mid!);
    expect(mid).toBeGreaterThan(far!);
    expect(near! - far!).toBeGreaterThan(50); // visibly different, not a rounding
  });

  it("does not divide by zero when every distance is identical", () => {
    const scale = scaleDistances([0.5, 0.5, 0.5]);
    expect(Number.isFinite(scale(0.5))).toBe(true);
  });

  it("returns zero rather than NaN for an empty list", () => {
    expect(scaleDistances([])(0.5)).toBe(0);
  });
});

describe("Gate", () => {
  it("says the sender is not connected rather than reading as healthy", () => {
    // "0 sent" is ambiguous between "nothing was ready" and "the wire is not
    // connected". An operator who reads the first when the second is true will wait
    // for replies that cannot arrive.
    render(<Gate humanApprovalRequired senderConnected={false} queued={4} />);
    expect(screen.getByText(/no sender connected/)).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("names the person holding the gate when sending is live", () => {
    render(<Gate humanApprovalRequired senderConnected queued={0} />);
    expect(screen.getByText(/held by a person/)).toBeTruthy();
  });
});

describe("Failure", () => {
  it("says an unbuilt endpoint is unbuilt, not that there is no work", () => {
    render(<Failure error={new ApiError("missing", "no endpoint")} />);
    expect(screen.getByText(/not built yet/)).toBeTruthy();
    expect(screen.getByText(/not because there is no work/)).toBeTruthy();
  });

  it("shows the server's sentence so it can be quoted", () => {
    render(<Failure error={new ApiError("broken", "boom", { detail: "psycopg: relation \"party\" does not exist" })} />);
    expect(screen.getByText(/relation "party" does not exist/)).toBeTruthy();
  });

  it("explains scale-to-zero when the server did not answer", () => {
    render(<Failure error={new ApiError("offline", "no answer")} />);
    expect(screen.getByText(/scales to zero/)).toBeTruthy();
  });

  it("renders an unrecognised throw rather than swallowing it", () => {
    render(<Failure error={new Error("something odd")} />);
    expect(screen.getByText(/something odd/)).toBeTruthy();
  });
});
