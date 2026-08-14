// =============================================================================
// Intent.tsx — say the goal, see what it would do, then let it
// =============================================================================
//
// The third surface, and the one most at risk of being a lie, so it is worth being
// precise about what it is.
//
// It is **not** a chatbot and it is not a model inventing a plan. The stages it
// shows are the fleet's real stages, the estimates are what those stages actually
// cost, and the cap is `spend.py`'s real cap — the one that refuses a request
// before it is charged rather than reporting an overspend afterwards. The goal text
// is not parsed by anything; it is stored and read by the next person to open the
// campaign, exactly as the operator manual says.
//
// So what does this surface add over a form with four fields? It shows the
// consequence before the commitment. Creating a campaign the old way tells you
// nothing about what will run, what it will cost, or where it will stop and wait
// for you. This shows all three, lets you switch stages off, and marks the human
// gate as the one thing you cannot switch off. That is the whole feature, and it is
// honest because every number in it comes from the server.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useArtists, useCommitIntent, usePlanIntent } from "../api/queries";
import { Failure, money } from "../components/primitives";
import type { Channel, IntentPlan, PlannedStage } from "../api/types";

const CHANNELS: { key: Channel; label: string; stocked: boolean }[] = [
  { key: "radio", label: "Radio", stocked: true },
  { key: "curator", label: "Curator", stocked: false },
  { key: "press", label: "Press", stocked: false },
  { key: "ugc", label: "UGC", stocked: false },
  { key: "sync", label: "Sync", stocked: false },
];

function StageRow({
  stage, onToggle,
}: {
  stage: PlannedStage;
  onToggle: (key: string, enabled: boolean) => void;
}) {
  return (
    <li className={stage.enabled ? "" : "vetoed"}>
      <span className="rank">
        {stage.gate ? (
          <span title="A person decides here. This cannot be switched off.">⏸</span>
        ) : null}
      </span>
      <span>
        <span className="who">{stage.name}</span>
        <span className="sub" style={{ display: "block" }}>{stage.detail}</span>
      </span>
      <span className="d">
        {stage.estimateMicroUsd === null ? "—" : money(stage.estimateMicroUsd)}
      </span>
      <span>
        {stage.gate ? (
          <span className="chip warn">gate</span>
        ) : (
          <button
            className="b q"
            onClick={() => onToggle(stage.key, !stage.enabled)}
          >
            {stage.enabled ? "skip" : "include"}
          </button>
        )}
      </span>
    </li>
  );
}

function PlanView({
  plan, onChange, onCommit, committing,
}: {
  plan: IntentPlan;
  onChange: (p: IntentPlan) => void;
  onCommit: () => void;
  committing: boolean;
}) {
  const estimated = plan.stages
    .filter((s) => s.enabled && s.estimateMicroUsd !== null)
    .reduce((n, s) => n + (s.estimateMicroUsd ?? 0), 0);
  const unknown = plan.stages.some((s) => s.enabled && s.estimateMicroUsd === null);

  return (
    <div className="panel" style={{ marginTop: "1rem" }}>
      <h2>What this would do</h2>
      <div className="ranked">
        <ul>
          {plan.stages.map((s) => (
            <StageRow
              key={s.key}
              stage={s}
              onToggle={(key, enabled) =>
                onChange({
                  ...plan,
                  stages: plan.stages.map((x) =>
                    x.key === key ? { ...x, enabled } : x),
                })
              }
            />
          ))}
        </ul>
      </div>

      <p className="sub">
        Estimated {money(estimated)}
        {unknown ? " plus stages whose cost cannot be known until they run" : ""} ·
        capped at {money(plan.capMicroUsd)}. Nothing exceeds a cap; a request that
        would is refused before it costs anything.
      </p>

      <label className="field" style={{ marginTop: ".6rem" }}>
        <span>Cap for this campaign (US$)</span>
        <input
          type="number"
          min={0}
          step="0.01"
          value={(plan.capMicroUsd / 1_000_000).toFixed(2)}
          onChange={(e) =>
            onChange({
              ...plan,
              capMicroUsd: Math.max(
                0, Math.round(Number(e.target.value || 0) * 1_000_000)),
            })
          }
        />
      </label>

      <button className="b p" disabled={committing} onClick={onCommit}>
        {committing ? "creating…" : "Create campaign"}
      </button>
      <p className="sub" style={{ marginTop: ".5rem" }}>
        Creating it opens nothing. Running it is a second, deliberate press on the
        campaign itself.
      </p>
    </div>
  );
}

export default function Intent() {
  const artists = useArtists();
  const planner = usePlanIntent();
  const commit = useCommitIntent();
  const navigate = useNavigate();

  const [goal, setGoal] = useState("");
  const [artistId, setArtistId] = useState("");
  const [channel, setChannel] = useState<Channel>("radio");
  const [plan, setPlan] = useState<IntentPlan | null>(null);

  const ready = goal.trim().length > 0 && artistId !== "";

  return (
    <section className="surface">
      <header>
        <h1>New</h1>
        <p>
          State the goal in your own words. You will see the stages that would run,
          what each is allowed to spend, and where it stops to wait for you — before
          anything is created. Nothing reads the goal but the next person to open the
          campaign.
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

          <label className="field">
            <span>Channel</span>
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value as Channel)}
            >
              {CHANNELS.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}{c.stocked ? "" : " — no contacts yet"}
                </option>
              ))}
            </select>
          </label>
          {/* An honest warning rather than a disabled option: the channel is real,
              the campaign would be valid, and there is simply nobody in the index to
              rank yet. Hiding it would misrepresent what is built. */}
          {!CHANNELS.find((c) => c.key === channel)?.stocked ? (
            <p className="sub" style={{ marginTop: "-.2rem" }}>
              The counterparty index holds radio today. A campaign on this channel
              will rank nobody until that channel is stocked.
            </p>
          ) : null}

          <label className="field">
            <span>Goal, in your own words</span>
            <textarea
              value={goal}
              placeholder="Specialist radio for the new single"
              onChange={(e) => setGoal(e.target.value)}
            />
          </label>

          <button
            className="b p"
            disabled={!ready || planner.isPending}
            onClick={() =>
              planner.mutate(
                { goal, artistId, channel },
                { onSuccess: setPlan },
              )
            }
          >
            {planner.isPending ? "working out what that means…" : "Show me what it would do"}
          </button>

          {planner.isError ? (
            <div style={{ marginTop: ".7rem" }}>
              <Failure error={planner.error} />
            </div>
          ) : null}
        </div>

        <aside className="panel">
          <h2>Before you commit</h2>
          <p className="sub" style={{ margin: 0 }}>
            A campaign is one artist, one channel, one goal. Two campaigns on
            different channels run at the same time without interfering.
          </p>
          <p className="sub">
            Whatever you plan here, no message leaves without a person approving it
            in full. That stage is marked as a gate below and cannot be switched off
            from inside the console.
          </p>
        </aside>
      </div>

      {plan ? (
        <PlanView
          plan={plan}
          onChange={setPlan}
          committing={commit.isPending}
          onCommit={() =>
            commit.mutate(plan, {
              onSuccess: (r) => navigate(`/campaigns/${r.campaignId}`),
            })
          }
        />
      ) : null}

      {commit.isError ? <Failure error={commit.error} /> : null}
    </section>
  );
}
