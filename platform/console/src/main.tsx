import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";

// Generated from the design system. See scripts/sync-tokens.mjs — editing it is how
// the palette forks.
import "./styles/design.generated.css";
import "./styles/app.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A failed request is rendered as itself rather than retried into a spinner
      // that never resolves. One retry covers the genuine case — the cluster scales
      // to zero and the first request after idle can time out — and no more, because
      // beyond that the honest answer is "the server did not answer" and the
      // operator should see it.
      retry: 1,
      refetchOnWindowFocus: true,
      staleTime: 1_000,
    },
    mutations: { retry: 0 },
  },
});

const root = document.getElementById("root");
if (!root) {
  // Loudly. A console that silently fails to mount looks like a slow network.
  throw new Error("index.html has no #root element — the console cannot mount.");
}

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename="/console">
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
