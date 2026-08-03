# Respect the Funk — Research Program

**Status:** Research phase complete. No code yet, by design.
**Last updated:** 2026-07-15

> ## 👉 Start here: [`SYNTHESIS.md`](./SYNTHESIS.md) — or the [PDF](./pdf/00-SYNTHESIS.pdf)
>
> Thirteen parallel agents across two phases produced **~6,900 lines across ~2,440 sourced claims**. The synthesis is the only document that sees all thirteen at once — the pillars were researched independently and cannot cross-reference each other. Read it first; the pillars are reference material behind it.
>
> **Rendered PDFs: [`pdf/`](./pdf/)** (16 documents, 273 pages).

## The short version

**Every path to making one song win is closed.** You cannot buy 100k streams profitably (~50x loss). You cannot measure whether ads caused streams (Facebook couldn't, with 663 RCTs). You cannot advertise film-clip creative (a written rule). Bought engagement is EV-negative. Probe accounts need 3–6 months. A 12-position audience clock costs ~$118K to validate. Creator demographics are unaccuracy-rated estimation. And UGC breakout is a **sub-1% lottery** — the top 10% of songs take 96% of creations.

**The conclusion those findings force:**

> **This is a portfolio business, not an optimization business.**
> When outcomes are lottery-shaped, the winning move isn't a better ticket — it's more tickets at lower cost, with the plumbing correct so a win actually pays.
> **More releases beat more spend. Not many accounts — many songs.**

That cuts against *both* strategies proposed so far (scale-the-accounts, and concentrate-on-one-artist). See [the pivot decision record](./STRATEGY-PIVOT.md).

**Do this week — all free:**
1. **Fix the TikTok clip start-time.** The default is wrong. Highest value-per-effort item in the project.
2. **Audit Sound ID / metadata hygiene.** ~20x fragmentation risk — a viral track can earn nothing.
3. **File the TikTok Content Posting API audit.** No SLA; the clock starts when filed.
4. **Run one People↔Things test — $143.** Cheapest real information available.

---

## The two goals, stated plainly

There are two goals here and they are not the same shape. Keeping them distinct is the single most important framing decision in this project.

**Goal 1 — The proof:** Get [*Losing Sleep* by Hallow Youth](https://open.spotify.com/album/50tCwIfgjh25bt1nAc37an) (label: Respect the Funk, 2026) to **100,000 Spotify streams**.

**Goal 2 — The product:** Build a **scalable marketing engine for music labels and artists** on Meta + TikTok, with every cost tracked and every event analytics-ready.

Goal 1 is the forcing function that keeps Goal 2 honest. A marketing engine that has never actually moved a stream counter is a slide deck. The song is the first tenant, not a side quest.

The risk to manage: Goal 1 rewards doing whatever works right now by hand; Goal 2 rewards building reusable machinery. Anything we do for the song that we would not do for a paying client is a detour — worth taking knowingly, not by accident.

## Hard constraints (from the brief)

| Constraint | Implication |
|---|---|
| Every cost tracked | The bill is a product feature, not an afterthought. Per-tenant cost attribution from day one. |
| Everything analytics-ready for ingestion | Events modeled for a warehouse before they're modeled for a UI. |
| **Scale to zero is an absolute necessity** | Rules out always-on infra. Constrains the DB choice hard — this is where "scale to zero" marketing claims usually break down. |
| Leverage cloud for everything, ML if necessary | "If necessary" is doing real work in that sentence. See Pillar 9 on whether custom ML is justified at our scale. |
| Cost-consciousness throughout | At low volume, fixed-cost SaaS subscriptions dominate variable costs. The bill is mostly a subscription-count problem, not a per-unit problem. |

## The two product ideas under evaluation

1. **Collaborative post webapp** — users co-create joint posts via templates + AI.
2. **Movie cutscene generator** — index films (frames, scenes, dialogue), then given a song + parameters, auto-generate a clip cut to the song with dialogue over it.

Idea 2 is the more differentiated of the two and also the one carrying the most unpriced risk. Its viability is a **rights question before it is an engineering question**, which is why Pillar 6 exists and why it asks specifically whether ad spend can be placed behind film-clip creative. A creative format that survives organically but cannot be advertised does not fit an engine whose primary channel is paid.

## Research pillars

| # | Pillar | The answer it came back with |
|---|---|---|
| 01 | [Platform APIs](./01-platform-apis.md) · [pdf](./pdf/01-platform-apis.pdf) | **No App Review needed for accounts you own.** TikTok audit is the long pole. |
| 02 | [Meta Ads](./02-meta-ads.md) · [pdf](./pdf/02-meta-ads.pdf) | **~$20–25K per 100k streams.** Best trackable event is a click, never a stream. |
| 03 | [Audience Models & Math](./03-audience-models.md) · [pdf](./pdf/03-audience-models.pdf) | **Measurement is unwinnable.** Creative testing is affordable — big gaps only. |
| 04 | [Spotify Economics](./04-spotify-economics.md) · [pdf](./pdf/04-spotify-economics.pdf) | **100k streams pays ~$300–500.** It's a distribution problem, not a purchase. |
| 05 | [Account Acquisition](./05-account-acquisition.md) · [pdf](./pdf/05-account-acquisition.pdf) | **Don't** — EV is negative. Ad-account cascade is written Meta policy. |
| 06 | [Rights & Licensing](./06-rights-licensing.md) · [pdf](./pdf/06-rights-licensing.pdf) | **Film clips cannot be advertised.** Pivot to PD/stock/AI footage. |
| 07 | [Cost Model](./07-cost-model.md) · [pdf](./pdf/07-cost-model.pdf) | **Infra is a rounding error** ($55–3,388/mo). AI video is 92% of variable cost. |
| 08 | [TikTok Ads](./08-tiktok-ads.md) · [pdf](./pdf/08-tiktok-ads.pdf) | **CML clearance may gate everything.** ~$17–79K per 100k streams. |
| 09 | [Architecture & Analytics](./09-architecture-analytics.md) · [pdf](./pdf/09-architecture-analytics.pdf) | **$0/mo idle is achievable.** No ML year one. Spotify has no artist API. |
| 10 | [Creator Indexing](./10-creator-indexing.md) · [pdf](./pdf/10-creator-indexing.pdf) | **No endpoint returns a creator's audience demographics at any price.** Buy sound-usage data instead. |
| 11 | [Probe Accounts](./11-probe-accounts.md) · [pdf](./pdf/11-probe-accounts.pdf) | **Legal, but 3–6 months to signal.** 2 accounts, not 8. ⚠️ *Fabricated then verified — see synthesis §9.* |
| 12 | [Audience Geometry](./12-audience-geometry.md) · [pdf](./pdf/12-audience-geometry.pdf) | **The circumplex is real; 12 positions costs ~$118,500.** Start with one axis, $143. |
| 13 | [UGC Adoption](./13-ugc-adoption.md) · [pdf](./pdf/13-ugc-adoption.pdf) | **A sub-1% lottery.** Seeding doesn't cascade. Two free levers are real. |
| — | [Decision record: Strategy Pivot](./STRATEGY-PIVOT.md) · [pdf](./pdf/14-strategy-pivot.pdf) | Why the framing changed after Phase 1 — and why the pivot was also incomplete. |
| — | [Annex: Incremental Attribution](./incremental-attribution-finding.md) · [pdf](./pdf/15-incremental-attribution.pdf) | Meta's tool is a **predictive model, not a holdout test** — and never refuses to answer. |

## Standards every pillar must meet

- **Every number is sourced.** Inline markdown link to a primary source. No numbers from memory.
- **Unverified is a valid answer.** Mark it `UNVERIFIED` rather than guessing. Confident wrong numbers are worse than acknowledged gaps.
- **Credibility is tiered.** Official vendor docs > practitioner blogs. Music-marketing agencies publish benchmark numbers that are partly advertising for themselves; flag them.
- **Pricing decays.** Date every price.
- **Say what won't work.** Each pillar names the techniques that fail at our scale and why. This is the most valuable section in most of these reports, and the easiest to omit.

## The questions that decided this project — and their answers

These were posed as the load-bearing unknowns before the research ran. All six came back. **Five landed harder than expected; one landed softer.**

1. **Can a stream be attributed to an ad at all?** — **No.** Spotify is third-party; the best obtainable event is *"clicked through to Spotify."* Spotify for Artists has no public API, and smart-links expose clicks but never stream events. Ad→stream attribution is a **date heuristic, not a join**. (02, 08, 09)
2. **Does paid traffic help or hurt the algorithm?** — **Unknown, and nobody publishes it.** Mechanistically plausible, universally believed, but the thresholds circulating online are SEO folklore. **Hypothesis to test, not a rule to design around.** (04)
3. **Does the budget support statistical significance?** — **Yes, for big gaps only.** +50% lift ≈ $143; +20% ≈ $791. Test formats, not tweaks. *This is the one that landed softer, and it's where the engine can add real value.* (03)
4. **Can we advertise film-clip creative?** — **No.** TikTok's ad policy has a written rule against it; Meta pre-screens and bans infringing IP. The organic fallback is also closed — Page violations cascade to the Business Manager. (06)
5. **Can we use our own song in TikTok ads?** — **Unresolved, and it's the #1 action.** Conservative read: Spark Ads do *not* launder non-CML music. If right, CML clearance gates the entire TikTok playbook. **Ask your distributor.** (08)
6. **What's the true idle cost?** — **$0/mo is genuinely achievable**, but only by actively dodging traps: seats and plan fees are the floor (not compute), networking sidecars kill the zero, and Cloud Run's `min-instances=0` isn't free under instance-based billing. (09)

## What we still don't know

Flagged rather than papered over. Full table in [the synthesis](./SYNTHESIS.md#8-what-we-still-dont-know).

- **Stock-footage library acquisition cost** — unpriced, and the pivot just made it load-bearing.
- **TTS for AI clips** — unpriced. Generated footage has no dialogue — *and the dialogue was the idea.*
- **Spark Ads × CML interaction** — sources directly contradict; resolved conservatively.
- **LiteLLM PyPI backdoor (March 2026)** — blocks the only real per-tenant hard-cap option.
- **Spark Ads lift figures** (+43% CVR, Nielsen CPA) — trace to no primary source. Direction reliable; percentages are not deck-ready.

## Working notes — how these predictions aged

- **The cutscene generator came back non-viable in its stated form**, exactly as the open verdict allowed for. The pivot (PD/stock/AI footage) preserves the engine and is 50x cheaper — but *does not* preserve the borrowed emotional charge that makes edits work. **Test it for ~$500 before building on it.**
- **The bought-engagement cascade prediction was confirmed, in Meta's own written policy** rather than in folklore. It also arrived from an unexpected second direction: film-clip copyright strikes cascade the same way. The specific thing to watch turned out to be the specific thing that kills the project.
- **We are the label.** Confirmed as the load-bearing fact it looked like. TuneCore: *"you may be liable for Streaming Manipulation perpetrated by a third party on your behalf."* Spotify's penalty is charged to **Respect the Funk's distributor account.** No intermediary absorbs it.

## The pattern worth noticing

**The research says "no" to most of the original plan, and the "no"s are the valuable part.** Buying streams, measuring incrementality, film-clip creative, custom ML, and bought engagement are all dead — each for sourced, specific reasons, and each would have consumed real money before failing.

What's left is real, and smaller than the brief started with: cheap infrastructure, free platform access for owned accounts, affordable creative testing, a compliant footage pipeline cheaper than the alternative, **disclosure as a genuine differentiator in a quietly non-compliant industry**, and an honest accounting layer nobody else offers.

That thesis survives contact with the evidence. The original one didn't.
