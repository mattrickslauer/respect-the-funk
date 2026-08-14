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
import { Failure, money } from "../components/primitives";
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

  const [goal, setGoal] = useState("");
  const [artistId, setArtistId] = useState("");
  const [channel, setChannel] = useState<Channel>("radio");
  const [capDollars, setCapDollars] = useState("5.00");
  const [shown, setShown] = useState(false);

  const ready = goal.trim().length > 0 && artistId !== "";
  const stages = planStages(channel, summary.data);
  const artistBudget = budgets.data?.budgets.find((b) => b.id === artistId);

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
            <span>Artist</span>
            {artists.isPending ? (
              <input disabled value="loading…" />
            ) : artists.isError ? (
              <input disabled value="could not load the roster" />
            ) : (
              <select value={artistId} onChange={(e) => setArtistId(e.target.value)}>
                <option value="">Choose an artist…</option>
                {artists.data.artists.map((a) => (
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

          <label className="field">
            <span>Cap for this campaign (US$)</span>
            <input
              type="number"
              min={0}
              step="0.01"
              value={capDollars}
              onChange={(e) => setCapDollars(e.target.value)}
            />
          </label>
          {artistBudget ? (
            <p className="sub" style={{ marginTop: "-.3rem" }}>
              This artist has spent {money(artistBudget.cost_micro_usd_24h)} in the
              last day{artistBudget.paused ? " and is currently paused" : ""}.
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
            Capped at {money(Math.round(Number(capDollars || 0) * 1_000_000))}.
            Nothing exceeds a cap; a request that would is refused before it costs
            anything.
          </p>

          <button
            className="b p"
            disabled={commit.isPending}
            onClick={() =>
              commit.mutate(
                {
                  goal,
                  artistId,
                  channel,
                  capMicroUsd: Math.round(Number(capDollars || 0) * 1_000_000),
                  stages,
                },
                { onSuccess: (r) => navigate(`/campaigns/${r.id}`) },
              )
            }
          >
            {commit.isPending ? "creating…" : "Create campaign"}
          </button>

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
