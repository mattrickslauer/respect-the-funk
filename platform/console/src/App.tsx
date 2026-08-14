// =============================================================================
// App.tsx — the shell
// =============================================================================
//
// Three surfaces, and a link back to the classic console for everything else.
//
// This app is deliberately **additive**. The thirteen Jinja views — Facts, Queue,
// Statements, Fleet, Budgets and the rest — keep working and are not ported here;
// they are dense reference tables that a server-rendered page does well and that a
// rewrite would only put at risk. What this app adds is the part those views cannot
// be: a surface that leads with what the system wants to do next, a campaign drawn
// as the work moving through it, and a way to see what a goal would cost before
// committing to it.
//
// The rail says so out loud rather than pretending this is the whole console.

import { NavLink, Route, Routes } from "react-router-dom";
import { useGate, useToday } from "./api/queries";
import { Gate } from "./components/primitives";
import Now from "./surfaces/Now";
import Workbench from "./surfaces/Workbench";
import Intent from "./surfaces/Intent";

function TopBar() {
  const gate = useGate();
  return (
    <div className="topbar">
      <span className="spacer" />
      {/* Chrome must not assert what it does not know. While the gate is loading or
          unreachable it says nothing at all, rather than showing a reassuring
          default — a gate indicator that reads "held" when it has not been checked
          is worse than no indicator. */}
      {gate.data ? (
        <Gate
          humanApprovalRequired={gate.data.humanApprovalRequired}
          senderConnected={gate.data.senderConnected}
          queued={gate.data.queued}
        />
      ) : null}
    </div>
  );
}

function Rail() {
  const today = useToday();
  const waiting = today.data?.proposals.filter((p) => !p.settled).length;

  return (
    <nav className="rail">
      <NavLink to="/" end>
        <span className="label">Now</span>
        {waiting ? <span className="count">{waiting}</span> : null}
      </NavLink>
      <NavLink to="/campaigns">
        <span className="label">Campaigns</span>
      </NavLink>
      <NavLink to="/new">
        <span className="label">New</span>
      </NavLink>

      <div className="railgap" />
      <div className="railnote">
        <a href="/artists" style={{ color: "var(--dim)" }}>Classic console →</a>
        <br />
        Facts, Queue, Statements, Fleet and Budgets live there.
      </div>
    </nav>
  );
}

export default function App() {
  return (
    <div className="shell">
      <div className="brand">
        <span className="spark" />
        <span className="wordmark">Respect the Funk</span>
      </div>
      <TopBar />
      <Rail />
      <Routes>
        <Route path="/" element={<Now />} />
        <Route path="/campaigns" element={<Workbench />} />
        <Route path="/campaigns/:id" element={<Workbench />} />
        <Route path="/new" element={<Intent />} />
        <Route
          path="*"
          element={
            <section className="surface">
              <div className="state">
                <b>No such screen.</b>
                The three surfaces are Now, Campaigns and New.
              </div>
            </section>
          }
        />
      </Routes>
    </div>
  );
}
