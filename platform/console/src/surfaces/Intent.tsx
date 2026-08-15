// =============================================================================
// Intent.tsx — say the goal, see what it would do, then let it
// =============================================================================
//
// The third surface, and the one most at risk of being a lie, so it is worth being
// precise about what it is.
//
// It is **not** a chatbot and there is no model in it. The goal text is not parsed
// by anything; it is stored and read by the next person to open the campaign,
// exactly as the operator manual says. The stages are the campaign lifecycle that
// manual documents, and — the part that earns the surface its place — each one's
// feasibility is read off `/summary` and `/fleet` rather than assumed.
//
// So what does this add over a form with four fields? It shows the consequence
// before the commitment. Creating a campaign the old way tells you nothing about
// what will run, where it will stop and wait for you, or which stages cannot
// currently do anything at all. A plan that quietly promised "send" on a system
// with no mail provider wired would be the exact dishonesty this product is built
// to refuse.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useArtists, useBudgets, useCommitIntent, useSummary,
} from "../api/queries";
import { Button, Failure, money } from "../components/primitives";
import { STOCKED_CHANNELS, planStages } from "./plan";
import type { Channel, PlannedStage } from "../api/types";

const CHANNELS: { key: Channel; label: string }[] = [
  { key: "radio", label: "Radio" },
  { key: "curator", label: "Curator" },
  { key: "press", label: "Press" },
  { key: "ugc", label: "UGC" },
  { key: "sync", label: "Sync" },
];

function StageRow({ stage }: { stage: PlannedStage }) {
  const chip =
    stage.feasibility === "ready" ? <span className="chip ok">ready</span> :
    stage.feasibility === "blocked" ? <span className="chip warn">blocked</span> :
    <span className="chip">unknown</span>;

  return (
    <li>
      <span className="rank">
        {stage.gate ? <span title="A person decides here.">⏸</span> : null}
      </span>
      <span>
        <span className="who">{stage.name}</span>
        <span className="sub" style={{ display: "block" }}>{stage.detail}</span>
        {stage.note ? (
          <span
            className="sub"
            style={{
              display: "block",
              color: stage.feasibility === "blocked" ? "var(--warn)" : "var(--dim)",
            }}
          >
            {stage.note}
          </span>
        ) : null}
      </span>
      <span />
      <span>{stage.gate ? <span className="chip warn">gate</span> : chip}</span>
    </li>
  );
}

export default function Intent() {
  const artists = useArtists();
  const summary = useSummary();
  const budgets = useBudgets();
  const commit = useCommitIntent();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [artistId, setArtistId] = useState("");
  const [channel, setChannel] = useState<Channel>("radio");
  const [shown, setShown] = useState(false);

  const ready = name.trim().length > 0 && goal.trim().length > 0 && artistId !== "";
  const stages = planStages(channel, summary.data);
  const artistBudget = budgets.data?.rows.find((b) => b.id === artistId);

  return (
    <section className="surface">
      <header>
        <h1>New</h1>
        <p>
          State the goal in your own words. You will see the stages that would run,
          where it stops to wait for you, and which stages cannot currently do
          anything — before the campaign exists. Nothing reads the goal but the next
          person to open the campaign.
        </p>
      </header>

      <div className="grid2">
        <div className="panel">
          <h2>The goal</h2>

          <label className="field">
            <span>Name</span>
            <input
              value={name}
              placeholder="Something you will recognise in a list of thirty"
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="field">
            <span>Artist</span>
            {artists.isPending ? (
              <input disabled value="loading…" />
            ) : artists.isError ? (
              <input disabled value="could not load the roster" />
            ) : (
              <select value={artistId} onChange={(e) => setArtistId(e.target.value)}>
                <option value="">Choose an artist…</option>
                {artists.data.rows.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            )}
          </label>
          {artists.isError ? <Failure error={artists.error} /> : null}

          <label className="field">
            <span>Channel</span>
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value as Channel)}
            >
              {CHANNELS.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                  {STOCKED_CHANNELS.has(c.key) ? "" : " — no contacts yet"}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Goal, in your own words</span>
            <textarea
              value={goal}
              placeholder="Specialist radio for the new single"
              onChange={(e) => setGoal(e.target.value)}
            />
          </label>

          {/* No cap field. Caps are per artist in `party_budget`, not per campaign,
              and the API refused to accept a campaign cap it would have had to
              ignore — a field the server silently drops is worse than an absent one,
              because the operator who typed it believes it took effect. So this
              shows the real budget the campaign will actually run under. */}
          {artistId && artistBudget ? (
            <p className="sub">
              Runs under {artistBudget.name}’s budget:{" "}
              {money(artistBudget.cost_micro_usd_24h)} spent in the last day
              {artistBudget.paused ? ", and it is currently paused" : ""}. Caps are
              set per artist, not per campaign.
            </p>
          ) : artistId && budgets.isSuccess ? (
            <p className="sub" style={{ color: "var(--warn)" }}>
              This artist has no budget row, so paid stages will be refused before
              they cost anything. That is the cap working, not a fault.
            </p>
          ) : null}

          <button
            className="b p"
            disabled={!ready}
            onClick={() => setShown(true)}
          >
            Show me what it would do
          </button>
        </div>

        <aside className="panel">
          <h2>Before you commit</h2>
          <p className="sub" style={{ margin: 0 }}>
            A campaign is one artist, one channel, one goal. Two campaigns on
            different channels run at the same time without interfering.
          </p>
          <p className="sub">
            Creating it opens nothing. Running it is a second, deliberate press on
            the campaign itself.
          </p>
          {summary.isError ? (
            <p className="sub" style={{ color: "var(--warn)" }}>
              The stage feasibility below could not be checked — the summary did not
              answer, so each stage reads “unknown” rather than guessing.
            </p>
          ) : null}
        </aside>
      </div>

      {shown && ready ? (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <h2>What this would do</h2>
          <div className="ranked">
            <ul>
              {stages.map((s) => <StageRow key={s.key} stage={s} />)}
            </ul>
          </div>

          <p className="sub">
            Spending runs against the artist's cap. Nothing exceeds it; a request
            that would is refused before it costs anything.
          </p>

          {/* The label holds at "Create campaign" throughout. This press ends in a
              navigation, so the window between the click and the new screen is the
              only chance to say anything at all — and "creating…" spent that window
              replacing the sentence that names what is being created. The trace
              says the same thing without taking the words away. */}
          <Button
            className="b p"
            busy={commit.isPending}
            onClick={() =>
              commit.mutate(
                { name, goal, artistId, channel, stages },
                { onSuccess: (r) => navigate(`/campaigns/${r.id}`) },
              )
            }
          >
            Create campaign
          </Button>

          {commit.isError ? (
            <div style={{ marginTop: ".7rem" }}>
              <Failure error={commit.error} />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
