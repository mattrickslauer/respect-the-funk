// =============================================================================
// ErrorBoundary.tsx — a render fault must not be a blank page
// =============================================================================
//
// Written after one. `App.tsx` read `today.data?.proposals.filter(...)`; the API
// returned its listing envelope, `proposals` was undefined, and the optional chain
// guarded `data` but not the property after it. React unmounted the entire tree and
// the console served a black rectangle — no rail, no error, no clue. The server was
// fine, the request was a 200, and nothing anywhere said what had happened.
//
// That is the worst failure this console can have, and it is worse than any of the
// five the client already handles, because those at least render a sentence. A blank
// page is indistinguishable from a broken deployment, a CSS mistake, or a hung
// request, and an operator has no way to tell which.
//
// So: a boundary, and it says what threw.

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Kept, because the stack is the useful half and the boundary below shows only
    // the message. A console that swallows this entirely is harder to debug than one
    // that never had a boundary.
    console.error("console: render failed", error, info.componentStack);
  }

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="surface">
        <div className="state bad" style={{ margin: "2rem auto" }}>
          <b>The console failed to draw this screen.</b>
          This is a fault in the console itself, not in the cluster — your data is
          untouched and the server-rendered console is unaffected. Reload to try
          again; if it recurs, the text below is what to quote.
          <code>{error.message}</code>
          <p style={{ marginTop: ".9rem" }}>
            <a href="/">The classic console</a> has every screen this one does not.
          </p>
        </div>
      </div>
    );
  }
}
