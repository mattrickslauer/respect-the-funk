# Pillar 3 — Audience Modeling, Attribution & the Mathematics of Ad Optimization

**Scope:** the actual math and the actual named models behind Meta ad delivery, measurement, and creative testing — assessed against the constraints of a single-song campaign chasing 100k Spotify streams on a budget in the hundreds-to-low-thousands of dollars.

**Research date:** July 2026. Every factual claim is linked inline. Where a claim is *our derivation* rather than a vendor-published fact, it says so explicitly.

---

## Bottom line / recommendation

1. **Do not try to out-optimize Meta's ranker. Feed it.** Meta's ads stack (GEM → Andromeda → Lattice) is a foundation-model-scale system trained on thousands of GPUs across the entire Facebook/Instagram ecosystem ([Engineering at Meta, Nov 2025](https://engineering.fb.com/2025/11/10/ml-applications/metas-generative-ads-model-gem-the-central-brain-accelerating-ads-recommendation-ai-innovation/)). The levers we actually control are **creative, signal quality, objective, and budget strategy** — not the optimizer. Meta's own auction rewards predicted engagement, so a great creative wins impressions a bigger bid cannot buy ([About ad auctions](https://www.facebook.com/business/help/430291176997542)).
2. **Broad targeting + automatic placements + Lowest Cost bidding is the correct default at our scale.** Narrow manual targeting shrinks the candidate pool Andromeda retrieves over and fragments the data Lattice pools — it fights the architecture's design intent. Meta's own placement math shows that pruning a "more expensive" placement can *raise* blended cost ([About the delivery system: Placements](https://en-gb.facebook.com/business/help/965529646866485)).
3. **Do NOT build a custom bandit layer on top of Meta.** Meta already runs impression-level, posterior-sampling-style exploration/exploitation with user-level features a CTR-only bandit cannot see ([Mobile Dev Memo](https://mobiledevmemo.com/bayesian-bandits-behind-the-scenes-of-facebooks-spend-allocation-decisioning/)). A DIY bandit is a strict subset of what Meta does for free. The *one* legitimate use of bandit logic is one level **up**: allocating our budget across songs/platforms, where no single optimizer has visibility.
4. **We cannot afford a statistically rigorous creative A/B test for small lifts.** Detecting a **20% relative CTR lift** at 1% baseline, 80% power needs **~85,300 impressions ≈ $790 at a $9.27 entertainment CPM** — most of a small budget for one test. Detecting a **50%+ lift** needs only **~15,500 impressions ≈ $143**. **Test for big creative gaps, not fine-tuning.** (Math in §6, verified independently.)
5. **MMM (Robyn/Meridian) and Conversion Lift are out of reach at our scale — do not attempt them.** See §9. This is the single most budget-saving conclusion in this report.
6. **Virality is not predictable in advance.** The rigorous literature converges: content features are *weak* predictors; early observed engagement velocity is what actually predicts cascade size ([Cheng et al., WWW 2014](https://arxiv.org/abs/1403.4608)). Strategy follows: **ship many cheap creatives, read early signal fast, pour budget into whatever moves.** Do not buy a "viral formula."
7. **Measurement in 2026 is worse, not better, and it hurt small advertisers most** ([Aridor et al., *Management Science*](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.06600)) — and Google **retired 10 of 13 Privacy Sandbox APIs in Oct 2025** ([Privacy Sandbox](https://privacysandbox.google.com/blog/update-on-plans-for-privacy-sandbox-technologies)). Plan measurement around **Meta's in-platform numbers + a simple pre/post read on Spotify for Artists**, not a precise attribution graph.

---

## 1. Meta's ad ranking stack, as publicly documented

### 1.1 The auction

Meta states the auction winner is **the ad with the highest total value**, composed of three inputs ([About ad auctions](https://www.facebook.com/business/help/430291176997542)):

| Component | Meta's description |
|---|---|
| **Bid** | "the advertiser's willingness to pay" |
| **Estimated action rate (EAR)** | "an assessment of whether a particular person engages with or converts from a particular ad" |
| **Ad quality** | from "feedback from people viewing or hiding the ad"; penalized for "sensationalized language and engagement bait" |

Commonly rendered as:

$$\text{TotalValue} = \text{Bid} \times \text{EAR} + \text{Quality}$$

> **Honesty note:** the three named components and the relative-value framing are **primary-sourced from Meta**. The precise algebraic form (× vs +) is **not** published by Meta as literal notation — it is a consistent secondary-source rendering. Treat the equation as a faithful mental model, not a quoted formula.

Two direct quotes matter enormously for a small advertiser:

- **"such auction adjustments will not cause us to charge you more than your bid"** — your bid is a ceiling.
- **"an ad that's more relevant to a person could win an auction against ads with higher bids"** — **creative quality is a substitute for money.** This is the entire strategic opening for an indie artist against better-funded competitors.

### 1.2 Why CPM ≠ what you bid

You are not charged your bid; you are charged what it takes to clear the next-best competitor's total value, discounted by your own relative EAR and quality — functionally a **quality-weighted generalized second-price auction**. Meta's documentation confirms the *behavior* (bid is a cap; relevance can beat higher bids) though Meta does not itself use the phrase "second-price" in the auction doc — the GSP framing is a well-supported inference, flagged as **medium confidence**.

The one place Meta does publish real notation is Bid Cap ([About Bid Cap](https://www.facebook.com/business/help/272503946776144), cross-confirmed in [developer docs](https://developers.facebook.com/documentation/ads-commerce/marketing-api/bidding/overview/bid-strategy)):

$$\text{Bid Per Impression} = \text{Bid Per Estimated Action} \times \text{Estimated Action Rate}$$

**Practical read:** raising EAR (better creative → better predicted engagement) lets you buy the same impression for a lower per-impression cost. **Improving creative is mathematically equivalent to getting a discount on media.**

### 1.3 GEM — Generative Ads Recommendation Model

Source: [Meta's Generative Ads Model (GEM), Engineering at Meta, Nov 10 2025](https://engineering.fb.com/2025/11/10/ml-applications/metas-generative-ads-model-gem-the-central-brain-accelerating-ads-recommendation-ai-innovation/).

- **What:** a foundation model for ads ranking, built with LLM-style architecture and training — Meta calls it "the largest foundation model for recommendation systems in the industry, trained at the scale of large language models," across **thousands of GPUs**.
- **Architecture:** attention mechanisms separating **sequence features** (user activity history, up to thousands of events) from **non-sequence features**; cross-feature learning via **Wukong** (stackable factorization machines) and **InterFormer** (alternating sequence/cross-feature layers); multi-domain learning shares knowledge across Facebook, Instagram, and Business Messaging.
- **Scale engineering:** HSDP + 2D parallelism for sparse embedding tables; **23× increase in effective training FLOPS**.
- **Knowledge transfer:** GEM's learnings propagate to smaller production models via hierarchical distillation and a "Student Adapter" — so the foundation model's gains cascade to the whole model fleet.
- **Reported results:** **+5% ad conversions on Instagram, +3% on Facebook Feed** since Q2 launch.

**What it means for us:** GEM extracts signal from *broad, heterogeneous* behavioral data — not from targeting rules we hand-write. Every additional honest conversion/engagement signal we send is raw material GEM converts into a better EAR, which the auction then pays us back for in cheaper impressions.

### 1.4 Andromeda — retrieval

Source: [Meta Andromeda, Engineering at Meta, Dec 2 2024](https://engineering.fb.com/2024/12/02/production-engineering/meta-andromeda-advantage-automation-next-gen-personalized-ads-retrieval-engine/).

- **What:** the **retrieval** stage that narrows tens of millions of eligible ads to a shortlist of a few thousand *before* the auction ranks them. **Andromeda decides which ads the auction even considers.**
- **Why it exists:** retrieval must sift "three orders of magnitude more ads" than later ranking stages — a problem made worse by Advantage+ and generative creative tools multiplying ad variants — in real time.
- **Architecture:** a "highly customized deep neural network with sublinear inference cost," enabling a **10,000× increase in model capacity**; a **jointly-trained hierarchical index** (index and retrieval model train together); **elasticity** that varies inference depth by available compute and segment value.
- **Hardware:** NVIDIA Grace Hopper Superchip; MTIA planned.
- **Results:** **+6% recall**, **+8% ads quality** on selected segments, **>100× feature-extraction speedup** vs CPU, **>3× end-to-end QPS**.
- **Advertiser-facing claim:** advertisers using AI-driven targeting saw a **22% ROAS increase**.

**What it means for us:** Andromeda's whole purpose is searching a *large* candidate space for personalized matches. **Every manual restriction we add shrinks the space it can search.** Narrow targeting is not precision — it is handicapping the retrieval engine.

### 1.5 Lattice — unified multi-domain, multi-objective ranking

Sources: [ai.meta.com — Meta Lattice](https://ai.meta.com/blog/ai-ads-performance-efficiency-meta-lattice/); [arXiv:2512.09200 (KDD 2026)](https://arxiv.org/abs/2512.09200).

- **What:** a unified ranking architecture replacing "hundreds of deep neural network models with trillions of parameters" that were previously siloed by surface (Feed/Story/Reels) and objective (clicks/views/conversions).
- **Problem:** data fragmentation across domains + escalating infra cost — worsened by privacy-driven signal loss.
- **How:** extends **Multi-Domain, Multi-Objective (MDMO)** learning with **horizontal sharing** (learnings from clicks transfer to conversion prediction; Feed transfers to Reels) and **hierarchical sharing** (foundation models feed lightweight vertical models).
- **Results (arXiv, production):** **+10% top-line revenue metrics, +11.5% user satisfaction, +6% conversion rate, 20% infra capacity savings**; the blog separately cites **~8% ad-quality improvement on Instagram**.

> Component names circulating as "Lattice Zipper" / "Lattice Filter" appear in secondary summaries and were **not confirmed** in the primary sources — **medium confidence**, cited here only for completeness.

**What it means for us:** Lattice pools learning *across* surfaces and objectives. Splitting a small campaign into narrow single-placement, single-objective ad sets denies the architecture the pooling it is built to exploit — and splits our already-thin conversion data across more ad sets, each needing its own ~50 events/week (§3).

### 1.6 The foundations — DLRM and DHEN

- **DLRM** ([Naumov et al., arXiv:1906.00091](https://arxiv.org/abs/1906.00091); systems companion [arXiv:1906.03109](https://arxiv.org/pdf/1906.03109)): Facebook's foundational recommendation architecture — **embedding tables** for high-cardinality categorical/sparse features (user ID, ad ID, interactions) + **dense MLPs** for continuous features, joined by explicit feature-interaction layers, with model parallelism on the huge embedding tables and data parallelism on the MLP. The paper calls it "representative of the recommendation models used in production at Facebook" for CTR/ranking. *(Its specific role in **ads** vs organic ranking is well-established in the literature but not verbatim in the abstract — **medium confidence**.)*
- **DHEN** ([arXiv:2203.11014](https://arxiv.org/abs/2203.11014)): a **Deep Hierarchical Ensemble Network** combining multiple heterogeneous feature-interaction modules (DLRM's dot-product interaction being one), because different interaction designs capture non-overlapping information. Reported **+0.27% Normalized Entropy** and **1.2× training throughput** over prior SOTA. A follow-up, [arXiv:2504.08169 (WWW 2025)](https://arxiv.org/abs/2504.08169), confirms DHEN specifically for **ad conversion rate prediction** — the strongest direct evidence this lineage sits in the ads pipeline.
- **Newer still:** Meta published an [Adaptive Ranking Model (Mar 31 2026)](https://engineering.fb.com/2026/03/31/ml-applications/meta-adaptive-ranking-model-bending-the-inference-scaling-curve-to-serve-llm-scale-models-for-ads/) on serving LLM-scale models for ads — evidence the trajectory toward ever-larger foundation models for ads ranking is continuing through 2026.

### 1.7 Why "feed the model" beats manual targeting — Meta's own numbers

- **Advantage+ Audience:** **14.8% / 9.7% / 7.2% lower cost per result** by objective ([About Advantage+ Audience](https://www.facebook.com/business/help/273363992030035)).
- **AI-driven targeting:** **+22% ROAS** ([Andromeda blog](https://engineering.fb.com/2024/12/02/production-engineering/meta-andromeda-advantage-automation-next-gen-personalized-ads-retrieval-engine/)).
- **The placement argument, in Meta's own worked example** ([About the delivery system: Placements](https://en-gb.facebook.com/business/help/965529646866485)): with 11 opportunities and a $27 budget, disabling a placement with a *higher average cost* (Instagram at $5/event) **raised** overall blended cost from **$3.00 → $3.25 per event**, because it removed access to individually cheap opportunities inside that placement. Meta's stated principle: the system optimizes for "the most optimisation events at the lowest average cost **overall**... not the lowest average cost for each placement."

This is the cleanest available refutation of the instinct to prune placements/audiences on surface-level cost comparisons. **Average cost per placement is a confounded statistic; the optimizer is buying at the opportunity level, not the placement level.**

---

## 2. Bid strategies and how they price

All primary-sourced from Meta ([About Meta Bid Strategies](https://www.facebook.com/business/help/1619591734742116); [developer docs](https://developers.facebook.com/documentation/ads-commerce/marketing-api/bidding/overview/bid-strategy)).

| Tier | Strategy | Mechanism | Guarantee |
|---|---|---|---|
| **Spend-based** | Highest Volume / Highest Value | Maximize results from budget, ignoring per-unit cost | Spends full budget; **no cost control** |
| **Goal-based** | Cost per Result Goal (Cost Cap), ROAS Goal | AI adjusts bids to hold an *average* target over campaign lifetime | **Not guaranteed**; "may spend less than your full budget... to protect your performance target" |
| **Manual** | Bid Cap | Hard per-auction ceiling; "never bids above your set amount — even when the market is competitive" | Advanced only; can starve delivery |

- **Lowest Cost / Highest Volume:** no `bid_amount` required; Meta "automatically bids on your behalf and gets you the lowest cost results." **Meta's recommended default for new campaigns and during the learning phase** — it gives the algorithm maximum freedom to explore.
- **Cost Cap** ([About Cost per Result Goal](https://en-gb.facebook.com/business/help/272336376749096)): a target *average* CPA; Meta "dynamically adjust[s] bids — bidding higher or lower per auction." This is **effectively constrained optimization**: maximize volume s.t. average cost ≈ target. Meta says judge on **weekly averages, wait at least 7 days**, and recommends **≥50–100 weekly conversions**.
- **Bid Cap:** caps *Bid Per Estimated Action*. Meta warns plainly: **"Bid cap provides no guarantee on actual outcomes. It is only a mechanism within the auction."**
- **ROAS Goal** (`LOWEST_COST_WITH_MIN_ROAS`) ([About ROAS Goal](https://www.facebook.com/business/help/1113453135474912)): requires Pixel/SDK + value optimization; same ≥50–100 weekly conversions and 7-day evaluation guidance. API `roas_average_floor` is scaled ×10,000 (`12300` = 123%).

**Recommendation for us:** we are chasing **volume** (100k streams), not defending a strict CPA, and we will be nowhere near 50–100 weekly conversions early on. **Use Lowest Cost / Highest Volume.** Cost Cap and ROAS Goal are, by Meta's own stated data requirements, strategies we do not yet qualify for — adopting them on thin data means the constraint is estimated from noise and delivery throttles.

---

## 3. The learning phase and the math behind "~50"

### 3.1 What Meta actually says

[About the Learning Phase](https://www.facebook.com/business/help/112167992830700): ad sets exit learning **"after about 50 results in the week after the ad set's last significant edit."** During learning, "the delivery system is exploring the best way to deliver your ad set," causing "unpredictable performance and elevated costs." Below that pace, Ads Manager flags **"Learning limited"** — explicitly **not a penalty**, but a signal that "your budget isn't being spent effectively because the ad delivery system can't optimise performance with your current setup."

**Resets** ([Significant Edits](https://www.facebook.com/business/help/316478108955072)): *always* — targeting, creative, optimization event, adding ads, pausing 7+ days, bid-strategy changes. *Sometimes* — budget changes by magnitude (Meta's example: "$100 to $101... isn't likely to cause it," but multiplying the budget could).

Notably, the **same ~50–100 weekly threshold** recurs in the Cost Cap and ROAS Goal docs — one consistent "enough signal to trust the model" line running through the delivery system.

### 3.2 Why ~50 — the statistical rationale

> **This derivation is ours, not Meta's.** No Meta primary source explains *why* 50. The number is primary-sourced; the justification below is our own analysis and should be presented as *"a rationale consistent with the threshold,"* never as a Meta-confirmed design decision.

Conversion is Bernoulli: each impression converts with unknown probability $p$. Over $n$ impressions, conversions $k \sim \text{Binomial}(n,p)$, estimator $\hat{p}=k/n$, with

$$SE(\hat{p}) = \sqrt{\frac{p(1-p)}{n}}$$

But Meta's threshold is stated in **conversions $k$**, not impressions $n$ — and that is the tell. Because ad conversion rates are low ($p \approx 0.5\%–5\%$), $k$ is well-approximated by **Poisson** with $\lambda = np$, where $\text{Var}(k) \approx k$, so $SE(k)\approx\sqrt{k}$ and the **relative** standard error of the rate estimate is

$$\text{Relative SE} = \frac{SE(k)}{k} \approx \frac{1}{\sqrt{k}}$$

**For rare events, precision depends on the number of successes observed, not the number of trials.** That is precisely why the rule is "50 conversions," not "50,000 impressions."

| $k$ (conversions) | Relative SE $=1/\sqrt{k}$ | ≈95% CI half-width ($1.96\times$) | Marginal SE gain per extra conversion |
|---|---|---|---|
| 5 | 44.7% | ±87.7% | 4.47 pp |
| 10 | 31.6% | ±62.0% | 1.58 pp |
| 25 | 20.0% | ±39.2% | 0.40 pp |
| **50** | **14.1%** | **±27.7%** | **0.14 pp** |
| 100 | 10.0% | ±19.6% | 0.05 pp |
| 200 | 7.1% | ±13.9% | 0.018 pp |
| 400 | 5.0% | ±9.8% | 0.006 pp |

*(Table computed and independently verified for this report.)*

**Reading the table.** At $k=10$, the true rate could plausibly be **40%–160%** of what you observed. A delivery system that behaves like an exploration/exploitation allocator makes its next-dollar decisions by **comparing** estimated action rates across candidates. With ±30–60% relative noise, those comparisons are dominated by noise — the "gradient" the optimizer would follow is unreliable, and committing budget to an apparently-better configuration is likely chasing sampling error.

By $k \approx 50$, relative SE falls to **~14%** — tight enough for directionally reliable comparisons and for goal-based bidding to constrain around a target.

**Why not 500?** $\sqrt{k}$ convergence. The marginal benefit is

$$\frac{d}{dk}\left(k^{-1/2}\right) = -\frac{1}{2k^{3/2}}$$

At $k=10$ each extra conversion buys **1.58 pp** of relative-SE reduction; at $k=50$, only **0.14 pp** — an **~11× smaller** marginal gain. To halve error from 14.1% to ~7% you must **quadruple** to $k=200$. Past ~50 events, each additional conversion buys progressively less certainty, so the cost — in exploration inefficiency and elapsed time — of holding an ad set in "learning" outweighs the shrinking statistical benefit. **~50 is where the $1/\sqrt{k}$ curve visibly flattens.** That is the honest answer to "why 50": not a magic constant, but the knee of a square-root curve.

### 3.3 The consequence we cannot escape

Every ad set needs its **own** ~50 events/week. **Fragmenting a small budget across many ad sets multiplies the conversions required and guarantees all of them stay Learning Limited.** At our budget this is fatal. **Consolidate: one campaign, few ad sets, several creatives inside them.** This single structural decision matters more than any targeting cleverness.

---

## 4. Attribution & incrementality — the real problem

### 4.1 Why attribution is the hardest part

Attribution asks a **causal** question — *did the ad cause the stream?* — using **correlational** data (who saw what, who converted). The gap between those two is not a rounding error. It is the whole problem, and it has been measured.

### 4.2 Last-click and multi-touch: the models, and their collapse

**Last-click** assigns 100% credit to the final touchpoint. It is trivial to compute, universally implemented, and systematically wrong: it credits the *closing* touch and ignores every touch that created demand — flattering retargeting and search while starving discovery.

**Multi-touch attribution (MTA)** spreads credit across the path:

| Model | Rule |
|---|---|
| **Linear** | equal credit to all $n$ touches: $1/n$ each |
| **Time-decay** | credit rises toward conversion: weight $\propto 2^{-t/h}$ for half-life $h$ (Google's default $h$ = 7 days) |
| **Position-based** | 40% first, 40% last, 20% spread across the middle |
| **Data-driven / Shapley** | credit = each channel's average **marginal contribution** across all orderings: $\phi_i = \sum_{S \subseteq N \setminus \{i\}} \frac{|S|!(|N|-|S|-1)!}{|N|!}\left[v(S \cup \{i\}) - v(S)\right]$ |

The Shapley value is the mathematically principled member of the family — it is the unique credit allocation satisfying efficiency, symmetry, null-player, and additivity. **But even Shapley only distributes observed correlation.** It answers "how do I divide credit for conversions that happened?" — not "would they have happened anyway?" **Every MTA model, Shapley included, is silent on the counterfactual.** That is the question we actually care about.

**MTA didn't just fall from favor — it was withdrawn.** Google **deprecated first-click, linear, time-decay, and position-based** models, fully retiring them from Ads and Analytics by **mid-October 2023** ([Google Ads Developer Blog, Apr 20 2023](https://ads-developers.googleblog.com/2023/04/first-click-linear-time-decay-and.html); [Search Engine Land](https://searchengineland.com/google-confirms-sunset-details-for-4-attribution-models-in-ads-and-analytics-433352)), with the stated rationale that they had dwindled to **fewer than 3% of conversions** ([Google Ads Help](https://support.google.com/google-ads/answer/6259715?hl=en)). **You cannot select these models today even if you want them.**

The structural reasons: cross-device and cross-app journeys are unobservable; ATT/ITP severed the identifiers MTA depends on (§4.6); and **walled gardens don't share logs** — Meta, TikTok, and Spotify will never appear in one deduplicated path. Forrester's summary is the honest one: **"no attribution model is capable of generating a precise value for the return from one individual tactic in a complex system"** ([Forrester, Apr 2021](https://www.forrester.com/blogs/the-perfect-multitouch-attribution-model-doesnt-exist/)).

### 4.3 How wrong is it? Two large studies with RCT ground truth

This is the most important evidence in this section, because it compares attribution-style estimates **against randomized experiments at Facebook** — the ground truth almost nobody gets to see.

**Gordon, Zettelmeyer, Bhargava & Chapsky (2019)** — *Marketing Science* 38(2), 193–225 ([journal](https://pubsonline.informs.org/doi/10.1287/mksc.2018.1135); [free author copy](https://www.kellogg.northwestern.edu/faculty/gordon_b/files/fb_comparison.pdf)). **15 Facebook RCTs, 500 million user-experiment observations, 1.6 billion impressions.**

- **Study 4:** exposed vs unexposed converted at 0.061% vs 0.019% ⇒ a naive **ATT lift of 316%**. The **true RCT lift was 73%** — the observational estimate was **more than 4× too high**. Exact matching on age/gender only pulled it to 222%.
- **Study 9:** true RCT lift **2.4%**; the *closest* observational estimate was **1,306%**.
- Across all: **"the point estimates in 7 of the 14 studies with a checkout-conversion outcome are consistently off by more than a factor of three."**
- Conclusion: **"commonly used observational approaches based on the data usually available in the industry often fail to accurately measure the true effect of advertising."**

**Gordon, Moakler & Zettelmeyer — "Close Enough?" (2023)** — *Marketing Science* 42(4), 768–793 ([journal](https://pubsonline.informs.org/doi/abs/10.1287/mksc.2022.1413); [free full text arXiv:2201.07055](https://arxiv.org/abs/2201.07055)). **663 large-scale Facebook experiments**, with **over 5,000 user-level features** — "richer than what most advertisers or their measurement partners can access."

> **"The median RCT lifts are 29%, 18%, and 5% for the upper, middle, and lower funnel outcomes, respectively. Using DML (SPSM), the median lift by funnel is 83% (173%), 58% (176%), and 24% (64%), respectively, indicating significant relative measurement errors."**

Their conclusion is the one to internalize: **"despite having access to large-scale experiments and rich user-level data, we are unable to reliably estimate an ad campaign's causal effect."**

> **Read that carefully.** Researchers *inside Facebook*, with 5,000 user-level features and state-of-the-art double machine learning, **could not recover the truth from observational data.** The median lower-funnel estimate overstated true lift by roughly **5×**. **We will not do better with a spreadsheet.** This is the empirical foundation for this report's advice to stop chasing precise attribution and settle for directional reads plus honest judgment.

### 4.4 Incrementality testing — the only thing that actually answers the question

**PSA holdouts.** The classic design: the control group is shown a **public service announcement** in place of your ad, so you can compare *exposed* treatment users to *exposed* control users. Two documented flaws ([Johnson, Lewis & Nubbemeyer — NBER version, §2–3](https://conference.nber.org/confer/2016/EoDs16/Johnson_Lewis_Nubbemeyer.pdf)):

1. **Cost.** "The ad platform and the experimental advertiser must pay for the PSA ad inventory or forgo the revenue from other advertisers that the PSAs displace" — and critically, **"the cost of PSAs does not fall with scale."**
2. **Selection bias — the fatal one.** **"Performance-optimized campaigns break PSA experiments."** The platform optimizes PSA delivery toward a *different* audience than your treatment ad, so "the PSA reaches the same number of users but reaches a different distribution of user types. Hence, any measured difference in sales... will conflate the causal ad effect with the user-type selection bias." With two-thirds of display dollars using performance optimization even in 2014, this is the central case, not an edge case.

**Ghost ads — the fix.** Johnson, Lewis & Nubbemeyer (2017), *JMR* 54(6), 867–884 ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2620078); [journal](https://journals.sagepub.com/doi/abs/10.1509/jmr.15.0297); [free NBER version](https://conference.nber.org/confer/2016/EoDs16/Johnson_Lewis_Nubbemeyer.pdf)) — winner of the 2017 Paul E. Green Award.

The idea is elegant: **rather than buying control inventory, the platform runs its normal auction/ranking for control users and simply *records* — "ghosts" — the impression it *would have served***, then shows them whatever they'd have otherwise seen. You get each exposed user's control-group counterpart **identified by the same optimization process**, eliminating the PSA's selection bias **and** the cost of control inventory. Their implementation "records more than 100 million predicted ghost ads per day."

Reported gains: advertisers can **"measure ad lift just as precisely while spending at least an order of magnitude less."** In their retailer case study ($30,500 budget), a PSA control would have raised experiment cost **43%**; a concentrated 6/94 ghost-ads design cut cost **83% to $5,000 — vs $45,000 for the PSA test.** Variance ratios of 5.9–16.4 imply ITT-only experiments must be an order of magnitude larger for equal confidence. (Substantively: retargeting lifted site visits 17%, purchases 11% — a useful reality check on retargeting's true, non-inflated value.)

**Ghost ads are the methodology underneath modern platform lift products** — which is exactly why Conversion Lift is a platform-side feature we can't replicate ourselves (§9.2).

**Geo-lift.** When user-level measurement is impossible, randomize **geographies** instead of people. Meta open-sourced this: [**GeoLift**](https://github.com/facebookincubator/GeoLift) ([docs](https://facebookincubator.github.io/GeoLift/)), "an end-to-end geo-experimental methodology based on Synthetic Control Methods used to measure the true incremental effect (Lift) of ad campaigns." It implements the **Augmented Synthetic Control Method (ASCM)** (augmentation options `None`/`Ridge`/`best`), R ≥ 4.0.0, MIT.

The math: rather than a single control region, build a **synthetic control** — a weighted combination of untreated geos that reproduces the treated geo's pre-treatment outcome trajectory. Choose weights $w_j \ge 0$, $\sum_j w_j = 1$ minimizing pre-period distance $\left\lVert Y^{\text{treat}}_{\text{pre}} - \sum_j w_j Y^{j}_{\text{pre}} \right\rVert$; the lift estimate is the post-period divergence $\hat{\tau}_t = Y^{\text{treat}}_t - \sum_j w_j Y^{j}_t$. **ASCM** adds a ridge-regression correction for residual pre-treatment imbalance. This generalizes difference-in-differences: DiD assumes parallel trends with equal weights; synthetic control *constructs* the counterfactual and relaxes that assumption.

**Meta's own stated minimums** ([GeoLift Best Practices](https://facebookincubator.github.io/GeoLift/docs/Best%20Practices/BestPractices/)):
- **"At minimum we recommend having 25 pre-treatment periods of 20 or more geo-units."**
- **"Under normal circumstances we advise having historical information to be over the last 52 weeks."**
- **"Daily granularity... is strongly recommended over weekly."**
- **"Study should be run for a minimum of 15 days, if daily, and a minimum of 4-6 weeks if weekly."**

It ships real power tooling — `GeoLiftMarketSelection()` (simulation-based test/control selection, outputs MDE and required investment) and `GeoLiftPower()` (power curves) — with default $\alpha = 0.1$.

> **Our verdict on GeoLift:** it is **the most plausible incrementality method at our scale** — it needs no pixel, no user-level identity, and survives ATT entirely, because it measures at the geo level. But be honest about the two blockers: (1) **we'd need ≥20 geo-units with ~52 weeks of clean per-geo daily history of our KPI** — and Spotify for Artists does not give us daily per-city stream counts at that fidelity for a brand-new song with no history; (2) our spend would have to be **large enough per-geo to move a detectable signal above city-level noise** — `GeoLiftMarketSelection()` would very likely return an MDE far above anything a few hundred dollars can produce. **Realistic verdict: not viable for this single, but the right tool to revisit if we ever run sustained six-figure campaigns with a stable streaming baseline.**

### 4.5 Meta's lift products, and a name that misleads

**Conversion Lift** is Meta's ghost-ads-style randomized holdout — the real thing. **Its eligibility gate is $5,000 spend + 500 conversions, i.e. our entire maximum budget just to qualify. Full analysis and numbers in [§9.2](#92-meta-conversion-lift--the-gate-sits-at-our-entire-budget).**

**⚠️ Meta "Incremental Attribution" is NOT an incrementality test.** Meta's Help Center pages for it exist ([About Incremental Attribution](https://www.facebook.com/business/help/2366718460372682); [How to Use](https://www.facebook.com/business/help/644554008179419); [How to View Results](https://www.facebook.com/business/help/1271619100603114)), but per Meta's own quoted language it **"optimizes delivery for incremental conversions using models that predict whether a conversion is caused by an ad"** — i.e. **a predictive ML model, not a randomized holdout.** It belongs in the *modeled attribution* family, closer to §4.2 than to §4.4.

> **Confidence flags, stated plainly:** Meta's Help Center is JavaScript-rendered and **we could not read the body text of these pages** — the URLs and the feature are confirmed; the substance is secondary-sourced. Several blogs describe Incremental Attribution as **"holdout testing with control groups"** — **that conflicts with Meta's own quoted language and we treat it as unverified and likely wrong.** A circulating **"46% lift in incremental conversions"** figure has **no locatable primary source — do not repeat it.** Reported eligibility (Sales/Leads objectives + maximize-conversions bidding, data from Apr 1 2025) is **unconfirmed**. For an explicitly-secondary practitioner test, see [Seer Interactive](https://www.seerinteractive.com/insights/we-tested-metas-new-incremental-attribution-setting-on-1m-in-ad-spend.-heres-how-it-works). **Before relying on this setting, open those Help Center pages in a browser and read them.**

### 4.6 ATT, SKAN, AEM — and who actually got hurt

**ATT.** Since iOS 14.5, apps must request permission via `AppTrackingTransparency` to access the IDFA ([Apple — User Privacy and Data Use](https://developer.apple.com/app-store/user-privacy-and-data-use/); [ATT framework docs](https://developer.apple.com/documentation/apptrackingtransparency)). Opt-in rates are low, so the identifier MTA relied on largely evaporated on iOS.

**SKAdNetwork.** Apple's privacy-preserving replacement: install/conversion **postbacks** attributed at campaign level, with **no user-level data**, deliberate **randomized delays**, and a small **conversion-value** payload ([Apple — SKAdNetwork](https://developer.apple.com/documentation/storekit/skadnetwork)). SKAN 4 added multiple conversion windows and **crowd-anonymity tiers**: when volume is low, Apple **degrades conversion values to coarse (low/medium/high) or omits them entirely** ([Apple — Receiving postbacks in multiple conversion windows](https://developer.apple.com/documentation/storekit/receiving-postbacks-in-multiple-conversion-windows)). Meta's SKAN reporting is aggregated and **delayed up to 3 days** ([Meta](https://www.facebook.com/business/help/188126096313109)).

> **This is the key asymmetry for us: SKAN's privacy floor is a measurement floor, and it binds hardest on small campaigns.** Crowd anonymity is *designed* to withhold detail below volume thresholds — so the advertiser with the least volume gets the least signal. Precisely our case.

**Aggregated Event Measurement (AEM)** is Meta's web-side answer: a **maximum of 8 conversion events per domain**, ranked by priority, with only the highest-priority event counted per user, plus verified domains and **modeled conversions** filling gaps ([Meta — About AEM](https://www.facebook.com/business/help/721422165168355); [Conversion event configuration](https://www.facebook.com/business/help/2151428647238533)). **Practical consequence:** we must *choose and rank* our 8 events — for a music funnel that means deciding, up front, whether e.g. "pre-save" outranks "landing page view."

**The measured damage — and its distribution.** Peer-reviewed, in *Management Science*: post-ATT, conversion-optimized Meta advertising saw a **36.6% relative reduction in click-through rates** (95% CI 18.2–54.5%); revenue fell **8–40%** for Meta-dependent firms. The authors' conclusion is the one that matters to us: **"these declines were primarily borne by smaller e-commerce firms"** ([Aridor, Che, Hollenbeck, Kaiser & McCarthy, 2025](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.06600); [PDF](https://www.anderson.ucla.edu/sites/default/files/document/2025-05/att_privacy.pdf)). Meta itself estimated ATT's 2022 revenue impact at **~$10 billion** ([CNBC, Feb 2 2022](https://www.cnbc.com/2022/02/02/facebook-says-apple-ios-privacy-change-will-result-in-10-billion-revenue-hit-this-year.html)).

**Correcting a live 2026 falsehood: there is no "SKAN 5."** SKAN 5.0 was announced at WWDC 2023 and **abandoned** — per Singular, **"SKAN 5 never happened"** ([Singular, Jun 2023](https://www.singular.net/blog/skan-5-announced/)). Apple pivoted to **AdAttributionKit** (WWDC 2024), "built on the functionality of SKAdNetwork" and interoperable with it ([Apple — AdAttributionKit/SKAdNetwork interoperability](https://developer.apple.com/documentation/adattributionkit/adattributionkit-skadnetwork-interoperability); [AdExchanger](https://www.adexchanger.com/mobile/apple-is-quietly-replacing-skadnetwork-and-pcm-with-a-new-ad-attribution-framework/)). **Production today is still SKAN 4.0.** Blogs dated 2026 claiming a SKAN 5 rollout are recycled or AI-generated — ignore them.

### 4.7 The 2026 state of play: Privacy Sandbox is dead, cookies stayed

The single biggest correction to anyone's pre-2025 mental model:

**On October 17, 2025, Google retired 10 of its 13 Privacy Sandbox APIs** — including **Attribution Reporting, Topics, and Protected Audience, on both Chrome *and* Android** — citing "low levels of adoption." **Only CHIPS, FedCM, and Private State Tokens survive.** Google reaffirmed it **"will maintain our current approach to offering users third-party cookie choice in Chrome"** ([Privacy Sandbox — Update on Plans](https://privacysandbox.google.com/blog/update-on-plans-for-privacy-sandbox-technologies); the earlier April 22 2025 reversal: [Privacy Sandbox next steps](https://privacysandbox.google.com/blog/privacy-sandbox-next-steps); coverage: [AdExchanger](https://www.adexchanger.com/privacy/google-pulls-the-plug-on-topics-paapi-and-other-major-privacy-sandbox-apis-as-the-cma-says-cheerio/), [Search Engine Land](https://searchengineland.com/google-officially-shuts-down-privacy-sandbox-463561), [Adweek](https://www.adweek.com/media/googles-privacy-sandbox-is-officially-dead/)).

**What this means practically:**
- **Third-party cookies are NOT deprecated in Chrome, and have no removal timeline.** The event the ad industry spent 2020–2024 preparing for **did not happen.**
- **Do not build anything on Privacy Sandbox APIs** — the attribution-relevant ones no longer exist.
- **The real measurement friction is Safari/iOS (ATT + ITP), not Chrome.**
- The industry's actual answer to signal loss turned out to be **first-party data + server-side tracking (Conversions API)** — which is also the answer for us, and conveniently the same thing that feeds GEM better signal (§1.3).

### 4.8 So what do we actually do about measurement?

Given: MTA is withdrawn and was never causal; observational estimates are off by **3–5×** even *inside Facebook* with 5,000 features; Conversion Lift needs our entire budget to qualify; GeoLift needs 52 weeks × 20 geos; MMM needs 2+ years (§9.1); and Spotify streams aren't a pixel event.

**The honest answer: accept directional measurement and stop paying for the illusion of precision.**

1. **Meta in-platform numbers** for *relative* decisions (which creative, which audience) — they're biased in level but far more useful for **comparisons under a consistent methodology**.
2. **A pre/post read on Spotify for Artists** as the top-line reality check — crude, confounded, and still the number that matters.
3. **Send clean first-party signal** (Pixel + **Conversions API**, 8 AEM events ranked deliberately) — this improves *delivery* (better EAR ⇒ cheaper impressions, §1.2) even where it can't deliver clean *measurement*. **Signal quality pays for itself through optimization, not reporting.**
4. **Judgment over dashboards.** With a single channel and a single song, the counterfactual is genuinely unknowable at our budget. The intellectually honest posture is: **optimize what we can compare, don't fabricate what we can't measure.**

---

## 5. Multi-armed bandits — and whether to build one

### 5.1 The algorithms

**Epsilon-greedy.** With probability $1-\varepsilon$ exploit the best-observed arm; with probability $\varepsilon$ explore uniformly at random ([Sutton & Barto tradition](https://yashbonde.github.io/blogs/bartosutton/chap2.html)). Weakness: it explores *dumbly* — uniformly, forever, regardless of how much confidence has accumulated — unless $\varepsilon$ is decayed.

**UCB1** ([Auer, Cesa-Bianchi & Fischer, *Machine Learning* 47:235–256, 2002](https://link.springer.com/article/10.1023/A:1013689704352)). Pick the arm maximizing

$$\text{index}_k = \bar{x}_k + \sqrt{\frac{2\ln t}{n_k}}$$

where $\bar{x}_k$ is arm $k$'s empirical mean reward, $t$ total plays, $n_k$ plays of arm $k$. The second term is an **optimism bonus**: it shrinks as an arm gets more data, grows with elapsed time. Auer et al. proved **logarithmic regret $O(\log n)$** uniformly over time, matching the Lai–Robbins lower bound.

**Thompson sampling (Beta-Bernoulli).** The canonical ad-CTR treatment is [Chapelle & Li, NIPS 2011](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/thompson.pdf), with regret analysis in [Agrawal & Goyal (arXiv:1111.1797)](https://arxiv.org/pdf/1111.1797).

- Model creative $i$'s click probability $\theta_i \sim \text{Beta}(\alpha_i,\beta_i)$, prior $\text{Beta}(1,1)$ (uniform).
- Conjugate update per impression: $\alpha_i \mathrel{+}= 1$ on click, $\beta_i \mathrel{+}= 1$ on no-click.
- Each decision: draw $\hat{\theta}_i \sim \text{Beta}(\alpha_i,\beta_i)$ for every creative, serve the argmax.

Exploration is *automatic and intelligent*: wide posteriors (few impressions) occasionally sample high by chance; tight high-mean posteriors win most draws. This is strictly smarter than epsilon-greedy at no extra cost.

### 5.2 Meta already does this

- **Dynamic Creative** "automatically tests combinations of ad components" ([About Dynamic Creative](https://www.facebook.com/business/help/170372403538781)) using an **impression-level optimization strategy**, learning "from the asset's performance across audiences" to select the best assets per impression ([Meta for Developers, asset_feed_spec](https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/asset-feed-spec/dynamic-creative)). Advantage+ Creative absorbed the legacy DCO feature in late 2024.
- **The delivery system itself is the bandit.** A detailed technical analysis by ad-tech analyst Eric Seufert describes Meta maintaining **probability distributions** (not point estimates) over each ad's expected purchases-per-thousand-impressions, starting with random exposure, shifting impressions toward the ad more likely to be better — while still recognizing the laggard has "a small non-zero probability" of being better and continuing a trickle of impressions to it ([Mobile Dev Memo](https://mobiledevmemo.com/bayesian-bandits-behind-the-scenes-of-facebooks-spend-allocation-decisioning/)). **That is posterior sampling in all but name.** *(Seufert is a credible independent analyst, not Meta — Meta does not publish the literal algorithm. Medium-high confidence.)*
- Meta has published bandit-based systems for adjacent delivery problems: [Auto-placement of ad campaigns using multi-armed bandits (Facebook Research, 2021)](https://research.facebook.com/blog/2021/04/auto-placement-of-ad-campaigns-using-multi-armed-bandits/).
- The pattern is now mainstream ad-tech: [Auctions Meet Bandits (arXiv:2508.21162)](https://arxiv.org/abs/2508.21162) documents Thompson sampling inside second-price auctions specifically to solve advertiser cold-start.

### 5.3 Verdict: **do not build a bandit layer on top of Meta**

**Reasoning:**

1. **Feature asymmetry is decisive.** Our DIY bandit would see *CTR, in daily batches*. Meta's EAR model conditions on thousands of signals — user embeddings, history, device, time-of-day, landing-page quality, cross-platform behavior — updated **per impression**, pooled across its entire ecosystem (§1.3–1.5). A Beta-Bernoulli CTR bandit is a **strict subset** of what Meta already runs on our behalf.
2. **The mechanism of a DIY bandit is budget-splitting — which is actively harmful at our scale.** To "bandit" our creatives manually we'd split budget across ad sets. Each then needs its own ~50 events/week (§3). We would convert one possibly-learning ad set into several permanently Learning-Limited ones. **The DIY bandit's own machinery destroys the data it needs.**
3. **The data doesn't exist.** §6 shows that at 1% CTR we need ~85k impressions just to resolve a 20% CTR difference. A bandit reallocating on a handful of daily conversions is reallocating on noise — the same problem Seufert flags (2–3 conversions ⇒ estimates too noisy to allocate on).

**The legitimate exception — a *meta*-level bandit.** Bandit logic is genuinely useful **one level above Meta**, across bets no single optimizer can see: Song A's campaign vs Song B's, or Meta vs TikTok vs playlist pitching. This is exactly where the academic cross-platform budget-bandit work operates ([Stochastic Bandits for Multi-platform Budget Optimization, arXiv:2103.10246](https://arxiv.org/pdf/2103.10246); [Advertising Media and Target Audience Optimization via High-dimensional Bandits, arXiv:2209.08403](https://arxiv.org/pdf/2209.08403)) — because there, no unified ranker is exploring for you; **we are the only agent positioned to compare apples to oranges.** A lightweight rule (track cost-per-stream per song as a posterior, allocate next week's discretionary budget probabilistically toward the better bet, keep ~10–20% exploring the laggard) is cheap, sound, and worth doing.

**Contrast worth noting:** JD.com built a genuine bandit creative-testing layer ("Comparison Lift," [arXiv:2009.07899, AAAI 2021](https://arxiv.org/abs/2009.07899)) reporting +46% CTR vs fixed-sample A/B. But that was **a platform's internal ad-tech team building inside their own auction** — not a solo advertiser bolting logic onto someone else's optimizer. It illustrates the engineering scale required to beat a well-run baseline, and it is not our situation.

---

## 6. Testing math — can our budget even support an A/B test?

### 6.1 The formula

Sample size per group for comparing two proportions (two-sample $z$-test):

$$n = \frac{\left(z_{\alpha/2} + z_{\beta}\right)^{2}\left[p_1(1-p_1) + p_2(1-p_2)\right]}{(p_1-p_2)^{2}}$$

with $z_{\alpha/2}=1.96$ ($\alpha=0.05$, two-sided), $z_\beta=0.84$ (80% power) or $1.28$ (90% power). Source: [Select Statistical Consultants — Sample Size Calculator for Two Proportions](https://select-statistics.co.uk/calculators/sample-size-calculator-two-proportions/); standard treatment also in [Statology](https://www.statology.org/two-proportion-z-test/) and [Statistics LibreTexts §9.3](https://stats.libretexts.org/Bookshelves/Introductory_Statistics/Mostly_Harmless_Statistics_(Webb)/09:_Hypothesis_Tests_and_Confidence_Intervals_for_Two_Populations/9.03:_Two_Proportion_Z-Test_and_Confidence_Interval).

> **Formula-variant note:** [Evan Miller's calculator](https://gist.github.com/mottalrd/7ddfd45d14bc7433dec2) uses a pooled-variance construction for the $\alpha$ term: $n = \left[z_\alpha\sqrt{2\bar{p}\bar{q}} + z_\beta\sqrt{p_1q_1+p_2q_2}\right]^2/(p_1-p_2)^2$. At our effect sizes the two agree within a few percent. Not a discrepancy — a convention choice.

### 6.2 Worked example at music-campaign CTRs

**Baseline $p_1 = 0.01$ (1% CTR).**

**Scenario 1 — +20% relative lift ($p_2=0.012$), 80% power:**

```
p1(1-p1) = 0.01 × 0.99   = 0.0099
p2(1-p2) = 0.012 × 0.988 = 0.011856
sum                       = 0.021756

(p1 - p2)² = (-0.002)²    = 0.000004
(1.96 + 0.84)² = 2.80²    = 7.84

n = 7.84 × 0.021756 / 0.000004
  = 0.17056704 / 0.000004
  = 42,641.8  →  n ≈ 42,642 per variant
```
**Total ≈ 85,284 impressions.**

**Scenario 2 — +20% lift, 90% power:** $(1.96+1.28)^2 = 3.24^2 = 10.4976$
```
n = 10.4976 × 0.021756 / 0.000004 = 57,096.4  →  57,097 per variant
```
**Total ≈ 114,194 impressions.**

**Scenario 3 — +50% relative lift ($p_2=0.015$), 80% power:**
```
p2(1-p2) = 0.015 × 0.985 = 0.014775
sum = 0.0099 + 0.014775   = 0.024675
(p1 - p2)² = (-0.005)²    = 0.000025

n = 7.84 × 0.024675 / 0.000025 = 0.193452 / 0.000025 = 7,738.1  →  7,739 per variant
```
**Total ≈ 15,478 impressions.**

**Scenario 4 — +50% lift, 90% power:**
```
n = 10.4976 × 0.024675 / 0.000025 = 10,361.1  →  10,362 per variant
```
**Total ≈ 20,724 impressions.**

*(All four independently recomputed and verified for this report.)*

| Lift to detect | 80% power | 90% power |
|---|---|---|
| **+20% relative** (1.0% → 1.2%) | **85,284 impressions** | 114,194 impressions |
| **+50% relative** (1.0% → 1.5%) | **15,478 impressions** | 20,724 impressions |

**Note the ~5.5× jump** between the 50% and 20% scenarios. That is the $1/\Delta^2$ law: the absolute gap shrinks 2.5× (0.005 → 0.002), squared ⇒ ~6.25× more sample. **Small lifts are brutally expensive to prove.**

### 6.3 Translating to budget

**CPM benchmarks (2025–2026):**
- **Entertainment/Media CPM $9.27** — aggregation across ~20,000+ accounts ([Sovran Meta Ads CPM by Industry 2026](https://sovran.ai/benchmarks/meta-ads-cpm-by-industry); underlying data via [Triple Whale, Jan–Dec 2025, ~35,000 brands](https://www.triplewhale.com/blog/facebook-ads-benchmarks)).
- **Books & Music (2025):** CTR 2.34%, CPA $30.25 ([Triple Whale](https://www.triplewhale.com/blog/facebook-ads-benchmarks)).
- **Arts & Entertainment (WordStream, 554 US traffic campaigns, Apr 2024–Jun 2025, medians):** **CTR 2.10%, CPC $0.49** — among the lowest CPCs of any industry ([WordStream Facebook Ads Benchmarks 2025](https://www.wordstream.com/blog/facebook-ads-benchmarks-2025)). *(WordStream reports CPC not CPM for this category; the CPM had to be triangulated from Sovran/Triple Whale — flagged.)*
- **Platform-wide CPM:** $13.48 median 2025, rising ~20% YoY to roughly **$14.19–$15.74** into 2026 ([Sovran](https://sovran.ai/benchmarks/meta-ads-cpm-by-industry)).

$\text{Cost} = (\text{impressions}/1000)\times\text{CPM}$:

| Scenario | Impressions | @$5 CPM | **@$9.27 (entertainment)** | @$13.48 (platform avg) | @$15 CPM |
|---|---|---|---|---|---|
| +20% lift, 80% power | 85,284 | $426 | **$791** | $1,150 | $1,279 |
| +20% lift, 90% power | 114,194 | $571 | **$1,059** | $1,539 | $1,713 |
| +50% lift, 80% power | 15,478 | $77 | **$143** | $209 | $232 |
| +50% lift, 90% power | 20,724 | $103 | **$192** | $279 | $311 |

### 6.4 Verdict by budget

- **$500 total:** detects **only a large (~50%+) lift** ($143–$311 — comfortable). A 20% lift needs **$791–$1,713** — **not affordable**; only theoretically reachable at an unrealistic $5 CPM ($426), with zero margin for frequency capping, audience overlap, or wasted impressions. **Test big creative gaps only.**
- **$1,500 total:** **detects a 20% lift at 80% power** at realistic entertainment CPMs ($791–$1,279). 90% power is at the edge ($1,059 at $9.27; over at $13.48–$15). Comfortable for 50% lifts with headroom.
- **$5,000 total:** covers all four scenarios including the worst case ($1,713), with the majority left to **scale the winner** — which is the correct use of most of the budget anyway.

**Strategic conclusion:** at our budget, **do not attempt to detect small creative improvements.** Structure creative testing as a search for **large quality gaps** (a hook that works vs one that doesn't), which is cheap to detect — and let Meta's impression-level optimizer handle the fine-grained allocation it is already better at than any test we can afford to run.

---

## 7. Audience modeling

### 7.1 How lookalikes are built

Meta's own framing: "Lookalike audiences take several sets of people as 'seeds' then Facebook builds an audience of similar people." For conversion-based sources Meta states it **"trains a prediction model, then creates a lookalike audience"** — i.e. not naive nearest-neighbor, but a trained model scoring the broader population on similarity ([Meta for Developers — Lookalike Audiences](https://developers.facebook.com/docs/marketing-api/audiences/guides/lookalike-audiences/)).

**The 1%–10% mechanism.** The API `ratio` parameter spans **1%–20%** in 1-point increments (`starting_ratio` defines sub-ranges), while the Ads Manager UI surfaces the familiar **1%–10%** slider. A **1% lookalike** = the top 1% of a country's population ranked most similar to the seed (smallest, highest similarity — often ~1–3M people in the US); **10%** = a much wider, weaker-similarity net ([Meta for Developers](https://developers.facebook.com/docs/marketing-api/audiences/guides/lookalike-audiences/); consumer-facing [About Lookalike Audiences](https://www.facebook.com/business/help/164749007013531)). Membership **refreshes ~every 3 days** while attached to an active ad set.

**The tradeoff, empirically.** AdEspresso's $1,500 / 14-day head-to-head: **1% → $3.75 CPL, 5% → $4.16, 10% → $6.36** (~70% worse CPA than 1%), with 10% also showing lower CTR (0.66% vs 0.82%) ([AdEspresso](https://adespresso.com/blog/adespresso-experiment-facebook-lookalike-audience/)). **This is a single non-peer-reviewed case study, not a controlled experiment** — directionally consistent with Meta's stated mechanism, but do not treat as precise.

### 7.2 Seed size — and an honest caveat for us

- **Meta's hard minimum: 100 people** from a single country ([Meta for Developers](https://developers.facebook.com/docs/marketing-api/audiences/guides/lookalike-audiences/)).
- **Meta's own quality guidance:** for conversion-based lookalikes, **"200 or more members who converted is recommended"**, and **"more converters result in a better predictive model."** Meta advises making the seed "as large as possible" and keeping it behaviorally coherent (similar objectives).
- **Industry consensus** puts practical quality at 1,000+, with a frequently-cited 5,000–10,000 "sweet spot" ([Lionelz](https://lionelz.com/en/blog/facebook-lookalike-audiences-opportunities/); [Skai](https://skai.io/blog/lookalike-audiences/); [Madgicx](https://madgicx.com/blog/why-facebook-lookalike-audiences-are-worth-your-ad-spend)).

> **Honesty flag:** the 1,000+/5,000–10,000 figures come from **no Meta primary source and no common cited study** — they are agency folklore repeated across blogs. Treat as heuristic, not science.

**What this means for us:** with realistically a few hundred to low-thousands of engaged fans, we clear Meta's 100 floor and probably its 200-converter recommendation — but we sit **well below** the seed size industry associates with high-quality tight lookalikes. **Expect mediocre 1% lookalike quality early on, and do not over-invest in lookalikes before the seed is real.** Broad targeting + strong creative is the better early bet; lookalikes get good *after* we have fans, not before.

### 7.3 Value-based lookalikes

Attach a numeric value per seed member (`LOOKALIKE_VALUE` / `customer_value`), then build with `is_value_based`. Meta: **"Facebook uses the value to determine which users in an audience are worth the most to you, in a quantifiable way."** Any non-negative integer/float works; Meta normalizes internally, so no pre-standardization needed. Meta explicitly recommends **lifetime value, not single-purchase value** ([Meta for Developers — Value-Based Lookalikes](https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/guides/value-based-lookalike-audiences); see also [Create a lifetime value Custom Audience](https://en-gb.facebook.com/business/help/185705781836755), [Best Practices for CLV](https://www.facebook.com/business/help/1249127098589034)). Minimum 100 matched, but **1,000+ matched records** for the weighting to be meaningful rather than noisy.

**Applying it to music (our extrapolation, not a Meta-documented use case):** the natural "value" is **fan depth, not dollars** — weight a seed by full-stream completion, saves, follows, shares, repeat streams. This skews the model toward finding more *superfans* rather than more *clickers*.

> **Operational reality check:** Spotify/Apple streaming data is **not** piped into the Meta pixel. Realistically we must either (a) use **on-platform engagement value** — e.g. Video Engagement Custom Audiences let you seed from people who watched **≥25%, 50%, 75%, or 95%** of a video ([KNOREX](https://knorex.zendesk.com/hc/en-us/articles/360058219091-How-to-Create-a-Video-Engagement-Custom-Audience-with-Meta-Ads)) — a decent proxy for "listened, didn't skip"; or (b) hand-build a value-tagged CSV from first-party data (presave completers, email list, merch buyers). **Option (a) is the realistic one for us.**

### 7.4 Retargeting windows

Website/pixel Custom Audiences retain **1–180 days** for standard events; values above 180 are silently capped. **Purchase events were extended from 180 → 730 days effective May 18, 2026**, with existing 180-day purchase audiences auto-updated ([CommonThreadCo](https://commonthreadco.com/blogs/coachs-corner/meta-purchase-audience-730-days-ecommerce); [Jon Loomer](https://www.jonloomer.com/chatgpt-ads-self-serve/)). *(Practitioner-sourced and consistent across sources; a directly-fetchable Meta primary page was not obtained — **medium-high confidence**. Jon Loomer is a well-regarded independent authority on Meta platform changes.)*

**For a 2–6 week single release** (synthesis from Meta's general mechanics, not a music-specific Meta recommendation):
- **7 days** — high-intent recent engagers (just watched the visualizer / hit the landing page). The workhorse.
- **14–30 days** — reasonable middle for a multi-week release cycle.
- **90–180 days** — **dilutes intent**; someone who watched an ad 120 days ago has weak relevance to this single's release moment. Do not max the window out just because you can.

---

## 8. Virality & engagement prediction — what the evidence actually supports

### 8.1 The classical models

**Bass diffusion** ([Bass 1969, *Management Science* 15(5):215–227](https://doi.org/10.1287/mnsc.15.5.215); [summary](https://en.wikipedia.org/wiki/Bass_diffusion_model)):

$$\frac{dF}{dt} = (1-F)(p + qF)$$

$F(t)$ = cumulative fraction of eventual adopters; **$p$** = coefficient of innovation (external/mass-media influence, typically ~0.01–0.03); **$q$** = coefficient of imitation (word-of-mouth, typically ~0.3–0.5). It is a **macro, aggregate curve-fitting model** — it describes an S-curve for a population. It is fit *after* observing enough of the curve, or by analogy to historical comparables.

**SIR** (Kermack–McKendrick 1927; [equations](https://en.wikipedia.org/wiki/Compartmental_models_in_epidemiology)):

$$\frac{dS}{dt} = -\frac{\beta}{N}IS,\qquad \frac{dI}{dt} = \frac{\beta}{N}IS - \gamma I,\qquad \frac{dR}{dt} = \gamma I$$

with $R_0 = \beta/\gamma$ determining whether a cascade grows or dies.

**The rumor-specific version** is the more apt one: **Daley & Kendall, "Stochastic Rumours"** ([*IMA J. Appl. Math.* 1(1):42–55, 1965](https://doi.org/10.1093/imamat/1.1.42)) — an Ignorant–Spreader–Stifler model where, crucially, "recovery" is **not spontaneous** as in disease: a spreader becomes a stifler upon contacting someone who *already knows*, i.e. **enthusiasm dies from saturation, not time.** (Modern variants remain standard, e.g. [an uncertain SIR rumor model](https://link.springer.com/article/10.1186/s13662-021-03386-w).)

> **The structural limitation, stated plainly:** Bass, SIR, and Daley–Kendall all **describe the shape of a cascade already underway given assumed parameters.** None of them takes pre-release content features and outputs a probability that a specific song takes off. **They model curve *shape*, not curve *existence*.** Using them as forecasting tools for an unreleased single would be misuse.

### 8.2 Can virality be predicted? The literature says: barely, and not from content

**Cheng, Adamic, Dow, Kleinberg, Leskovec — "Can Cascades Be Predicted?" (WWW 2014)** ([arXiv:1403.4608](https://arxiv.org/abs/1403.4608) / [ACM DL](https://dl.acm.org/doi/10.1145/2566486.2567997)). 150,572 Facebook photo reshare cascades, ~9.2M reshares. Task: after observing $k$ reshares, will the cascade double? Results at $k=5$: **all features → 79.5% accuracy, AUC 0.877**; temporal alone ~73%; structural alone ~65%; **content features consistently weakest**. Accuracy rises to 85%+ by $k=25$ — i.e. **the model gets better the longer you wait**, which is close to tautological. The paper's own conclusion:

> "while content features affected the performance of structural and temporal features, we find that they are **weak predictors** of how widely disseminated a piece of content would become."

**Bakshy, Hofman, Mason, Watts — "Everyone's an Influencer" (WSDM 2011)** ([PDF](https://snap.stanford.edu/class/cs224w-readings/bakshy11influencers.pdf)). 1.6M Twitter users, 74M diffusion events. Finding: **"predictions of which particular user or URL will generate large cascades are relatively unreliable"** — and seeding many ordinary users often beats betting on identifiable "influencers."

**Goel, Anderson, Hofman, Watts — "The Structural Virality of Online Diffusion" (*Management Science*, 2016)** ([PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/04/twiral.pdf)). ~1 billion Twitter diffusion events. Finding that should reframe our strategy: **structural virality is typically low and stays low even for the largest cascades** — most things that "go viral" are driven by **one or a few large broadcast moments** (a big account, a press pickup, a platform push), **not** grassroots multi-hop peer contagion. **The folk theory of virality is the exception, not the rule.**

**Recent work (2023–2026), same verdict:**
- [Early Multimodal Prediction of Cross-Lingual Meme Virality (arXiv:2510.05761)](https://arxiv.org/html/2510.05761v1): **content-only plateaus at PR-AUC ≈ 0.13; temporal features alone ≈ 0.54; full multimodal ≈ 0.65.** The paper's framing: prediction shifts "from predicting based on **what the content is** to predicting based on **how it is behaving**."
- [Predicting Post Virality with Temporal Cross-Attention over Trend Signals (arXiv:2605.02358, May 2026)](https://arxiv.org/html/2605.02358): AUC-ROC 0.836 on Reddit, but driven mostly by structural/temporal metadata (posting time, subreddit norms); trend cross-attention added only **+0.003 AUC-PR** over baselines. Content signal remains the weak link even in 2026-era modeling.
- [Botelho — "Predicting Virality: How Soon Can We Tell?"](https://medium.com/cybersecurity-for-democracy/predicting-virality-how-soon-can-we-tell-dc153a98774f): using **only** observed engagement, F1 ≈ 0.8 for top-1% status by **hours 13–17** post-publication; velocity/acceleration features moved that window earlier by only ~3 hours and never exceeded 0.75 standalone.

**Music-specific:** the literature predicts *popularity from audio features + early streaming data*, not pre-release virality. [Beyond the Hook (arXiv:2509.24856)](https://arxiv.org/pdf/2509.24856) and [Spotify Chart Success prediction (arXiv:2508.11632)](https://arxiv.org/html/2508.11632) both **combine early streaming/chart engagement with audio features** — same pattern: observed performance dominates. [CNN on Spotify features/spectrograms (arXiv:2505.07280)](https://arxiv.org/html/2505.07280v1) is correlational — "successful songs share these traits" — **not** validated as a leading indicator for a specific unreleased track. Spotify's own public research in this area is **skip prediction** ([WSDM Cup 2019, arXiv:1901.08203](https://arxiv.org/pdf/1901.08203)) — session retention, a different problem. **No public Spotify research on pre-release virality prediction was found — a genuine gap, stated rather than papered over.**

### 8.3 Where the evidence is weak — call it out

- **"Virality formulas" in marketing content are not backed by the prediction literature.** Frameworks like STEPPS and "6 emotional triggers" rest on real *correlational* findings (high-arousal emotion correlates with sharing) but are routinely oversold as predictive/causal recipes. The rigorous cascade literature (Cheng, Bakshy, Goel) **does not support the claim that these factors let you predict or engineer virality in advance.** [MIT Sloan Management Review — "Is Viral Marketing a Myth?"](https://sloanreview.mit.edu/article/is-viral-marketing-a-myth/) argues the viral-marketing framing itself misrepresents real diffusion mechanics — converging with Goel et al.'s broadcast-dominance finding. Popular explainers like [Neil Patel's "Science of Virality"](https://neilpatel.com/blog/science-of-virality/) synthesize genuine correlational research but **should not be read as predictive formulas.** This is exactly the post-hoc storytelling pattern to avoid buying.
- **The AdEspresso lookalike test** (§7.1) is one uncontrolled case study — directional only.
- **Our learning-phase derivation** (§3.2) is our analysis, not Meta's rationale.

### 8.4 What this means operationally

The literature converges on a deflating but *actionable* conclusion: **you cannot know in advance, from the song alone, whether it will take off.** The strongest signal is **early engagement velocity**, observable only after some exposure. Therefore:

- **Do not** spend money trying to pre-score creative or reverse-engineer a viral formula.
- **Do** ship many cheap creative variants, read early signal within hours-to-days, and **concentrate budget behind whatever actually moves**. This is supported directly by Cheng et al., the 2025–26 meme/post-virality work, and Botelho.
- Given Goel et al.'s broadcast-dominance finding, **a single large placement (a big playlist, a large account, a press pickup) is more likely to produce a step-change than any hoped-for peer-to-peer chain reaction.** Budget accordingly: paid social is for *finding and feeding* the audience, but the step-changes tend to come from broadcast moments.

---

## 9. Techniques that will NOT work at our scale, and why

*The most valuable section in this report. Each item is something a competent-sounding consultant might sell us that our budget mathematically cannot support.*

### 9.1 Media Mix Modeling (Robyn / Meridian) — **off by 13–23×**

**The verdict is delivered by the vendors' own documentation, not by critics.**

- **Robyn** states an MMM "will need a **minimum of two years of historical weekly data**" and a **"recommended ratio of data points is 1 independent variable : 10 observations"** ([Robyn — Analyst's Guide to MMM](https://facebookexperimental.github.io/Robyn/docs/analysts-guide-to-MMM/)).
- **Meridian** is even blunter: **"With two years of weekly data (104 data points), you have four data points per parameter. This sample size is too low to estimate the model reliably."** Google's worked example targets **~15 data points per parameter** (~3 years weekly) ([Meridian — Amount of data needed](https://developers.google.com/meridian/docs/pre-modeling/amount-data-needed)).

**Our situation:** 2 channels × 6–8 weeks ⇒ **6–8 weekly observations**, against **~8–10 parameters** (per channel: adstock θ (1) + Hill saturation α, γ (2) + coefficient (1); plus intercept, trend, seasonality).

| Vendor's own rule | Required | We have | Shortfall |
|---|---|---|---|
| Robyn: "minimum of two years of weekly data" | 104 wks | 6–8 wks | **~13–17× short** |
| Robyn: 1 variable : 10 observations | ≥20 wks (2 vars, ignoring controls) | 6–8 wks | **~2.5–3× short** |
| Meridian: 104 wks is **"too low… to estimate reliably"** | >104 wks | 6–8 wks | **6–8% of an already-insufficient baseline** |
| Meridian: ~15 data points/parameter | ~120–150 wks | 6–8 wks | **~18–23× short** |

**Why this is not merely "less accurate" but meaningless.** Parameters ≥ observations: more unknowns than equations. **Ridge regression will still emit a number** — the L2 penalty makes a rank-deficient matrix invertible — but in that regime the coefficients are determined by **the regularization penalty and the hyperparameter bounds, not by signal in the data**. The model hands back its own priors wearing a lab coat. Nevergrad's evolutionary search compounds it: with 6–8 rows, a vast range of adstock/saturation shapes fit equally well, so the "Pareto-optimal" solution is arbitrary — **a different random seed would likely yield materially different channel attributions.** Google's own theoretical work concedes the point: Bayesian priors exist in MMM *precisely because* media data is typically too sparse and collinear to identify carryover and saturation shape from data alone ([Jin, Wang, Sun, Chan & Koehler, Google 2017](https://research.google/pubs/bayesian-methods-for-media-mix-modeling-with-carryover-and-shape-effects/)).

**And the fatal confound:** over 6–8 weeks, **ad spend and time-trend are near-perfectly collinear**. There is no way to separate "streams rose because of ads" from "streams rose because of the playlist add / press pickup / organic momentum that happened the same fortnight." **That confound is the entire measurement question, and MMM at this scale cannot touch it.**

Even the most small-advertiser-friendly vendor benchmark found — [Recast](https://getrecast.com/data/) *(vendor, secondary)* — which explicitly *rejects* "you must be a big advertiser," still names **"$10 a day" as "pretty much the theoretical limit below which marketing mix modeling just can't work well,"** and suggests 3–6 months for "small advertisers spending just over a thousand dollars a day" ⇒ **~$90,000–$180,000**. That is **18–360× our budget.**

> **Verdict: do not run Robyn or Meridian. Output at our scale would be numerically well-formed and statistically meaningless — worse than no measurement, because a confident-looking chart invites action.**

### 9.2 Meta Conversion Lift — the gate sits at our entire budget

Meta publishes hard eligibility ([About Conversion Lift](https://www.facebook.com/business/help/221353413010930)):

- A campaign in the past year with spend of **$5,000 USD or more**
- **At least 500 conversions** (1-day click, 7-day click, or 1-day view)
- Pegged to a **90-day lookback**, scaling proportionally — Meta's own example: **180 days ⇒ $10,000 and 1,000 conversions**
- **Conversions API with Event Match Quality > 5**
- Setup screen recommends a **minimum $5,000 budget and 28-day duration** ([How to set up a Conversion Lift test](https://www.facebook.com/business/help/1598740610886911)) — and **the "Get started" button is disabled if the account doesn't qualify.** A hard UI gate, not a nudge.
- API access "is currently limited. Please contact your Meta Representative" ([Marketing API — Lift studies](https://developers.facebook.com/docs/marketing-api/guides/lift-studies/)), with a results floor of **≥100 conversions** per breakdown combination to display at all.

**Verdict:** at **$500** we are off by **10× on spend alone** — not close. At the very top of range ($5,000) we could *theoretically* clear the spend bar only by spending the entire budget, while *simultaneously* hitting **500 conversions** and **EMQ > 5**. For a thin-pixel single-song campaign, unrealistic. **Do not plan around Conversion Lift.**

### 9.3 Meta Brand Lift — off by ~24×

**United States minimum: $120,000** per study (max 3 months); Canada $36,500, Germany $75,000, Australia $55,000, France $54,000, UK $44,000, default $10,000 ([Brand Lift minimum requirements](https://www.facebook.com/business/help/417527072254206), page dated June 17 2024). **The cheapest country tier alone is 2× our maximum total budget.** Not a discussion.

> **⚠️ Correct a widespread practitioner error:** many blogs cite **"$30,000" as the Conversion Lift minimum.** That is the **old pre-2024 Brand Lift** number. Secondary sources conflate the two products relentlessly — some even claim Conversion Lift has "no minimum spend." **Use Meta's own two help pages as authoritative, not agency blogs.**

### 9.4 Detecting small creative lifts

From §6: proving a **+20% relative CTR lift** at a 1% baseline needs **85,284 impressions ≈ $791** at entertainment CPM (**$1,150–$1,279** at platform-average CPMs), *at only 80% power*. On a $500 budget that is the whole campaign spent on one inconclusive test. The $1/\Delta^2$ scaling is unforgiving — and no amount of cleverness evades it.

> **Verdict: never budget to detect a small lift. Test only for large (~50%+) creative gaps (~$143).** Accept that fine-grained allocation is Meta's job, and it does it per-impression for free (§5.2).

### 9.5 A custom multi-armed bandit layer

Covered in §5.3. Summary of why it fails *specifically at our scale*: implementing it requires **splitting budget across ad sets**, each then needing its own **~50 conversions/week** (§3) — so the bandit's own machinery guarantees every arm stays **Learning Limited**. It would reallocate on 2–3 daily conversions, i.e. on noise (relative SE at $k=3$ is ~58%). **The intervention destroys the data it depends on.** Meta's optimizer, meanwhile, sees user-level embeddings we will never have.

> **Verdict: no DIY bandit inside Meta.** Bandit logic only *above* Meta, across songs/platforms (§5.3).

### 9.6 Multi-touch attribution

Not just inadvisable — **largely unavailable**. Google **deprecated first-click, linear, time-decay and position-based models**, fully retired from Google Ads and Analytics by **mid-October 2023** ([Google Ads Developer Blog, Apr 20 2023](https://ads-developers.googleblog.com/2023/04/first-click-linear-time-decay-and.html); [Search Engine Land](https://searchengineland.com/google-confirms-sunset-details-for-4-attribution-models-in-ads-and-analytics-433352)), with Google's stated rationale that they had fallen to **fewer than 3% of conversions** ([Google Ads Help](https://support.google.com/google-ads/answer/6259715?hl=en)). **You literally cannot select them today.** And no MTA can span the Meta↔TikTok↔Spotify walled gardens regardless. As Forrester put it: **"no attribution model is capable of generating a precise value for the return from one individual tactic in a complex system"** ([Forrester, Apr 2021](https://www.forrester.com/blogs/the-perfect-multitouch-attribution-model-doesnt-exist/)).

> **Verdict: don't build an attribution graph. It cannot be built, by us or anyone.**

### 9.7 High-quality lookalikes, *early*

Meta's floor is **100 people**, with **"200 or more members who converted… recommended"** ([Meta for Developers](https://developers.facebook.com/docs/marketing-api/audiences/guides/lookalike-audiences/)). We can clear that — but we sit far below the **1,000–10,000** seed size industry associates with genuinely tight 1% lookalikes (*agency heuristic, no primary source — §7.2*). **Lookalikes are a scaling tool, not a cold-start tool.** They get good after we have fans.

> **Verdict: broad targeting + strong creative first. Revisit lookalikes once the seed is real.**

### 9.8 Precise iOS/Spotify attribution

**SKAdNetwork's crowd-anonymity threshold degrades conversion values to coarse (low/medium/high) — or nothing — below an undisclosed volume floor** ([Apple — Receiving postbacks in multiple conversion windows](https://developer.apple.com/documentation/storekit/receiving-postbacks-in-multiple-conversion-windows)). **A small campaign is precisely the case that falls under that floor: SKAN's privacy floor is a measurement floor.** Meta's SKAN reporting is campaign-level aggregated and delayed up to 3 days ([Meta](https://www.facebook.com/business/help/188126096313109)).

And the damage is measured, peer-reviewed, and **concentrated on firms like us**: post-ATT, conversion-optimized Meta advertising saw a **36.6% relative reduction in click-through rates** (95% CI 18.2–54.5%), with firm revenue down **8–40%** for Meta-dependent firms — and the authors conclude **"these declines were primarily borne by smaller e-commerce firms"** ([Aridor, Che, Hollenbeck, Kaiser & McCarthy, *Management Science*, 2025](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.06600); [full PDF](https://www.anderson.ucla.edu/sites/default/files/document/2025-05/att_privacy.pdf)). Meta itself put ATT's 2022 revenue impact at **~$10 billion** ([CNBC, Feb 2 2022](https://www.cnbc.com/2022/02/02/facebook-says-apple-ios-privacy-change-will-result-in-10-billion-revenue-hit-this-year.html)).

Additionally, **Spotify streams are not a pixel event** — there is no conversion signal flowing from Spotify back to Meta. The best available proxies are landing-page/pre-save events and video-watch depth (§7.3).

> **Verdict: accept directional measurement. Use Meta in-platform numbers + a pre/post read on Spotify for Artists, and a holdout-free "did the needle move" judgment. Do not buy tooling promising user-level Spotify attribution — the signal does not exist.**

### 9.9 Anything built on Privacy Sandbox

**Google retired 10 of 13 Privacy Sandbox APIs on October 17, 2025** — including **Attribution Reporting, Topics, and Protected Audience on both Chrome *and* Android** — citing "low levels of adoption." **Only CHIPS, FedCM, and Private State Tokens survive.** Google reaffirmed it **"will maintain our current approach to offering users third-party cookie choice in Chrome"** ([Privacy Sandbox — Update on Plans](https://privacysandbox.google.com/blog/update-on-plans-for-privacy-sandbox-technologies); earlier reversal [Apr 22 2025](https://privacysandbox.google.com/blog/privacy-sandbox-next-steps); coverage: [AdExchanger](https://www.adexchanger.com/privacy/google-pulls-the-plug-on-topics-paapi-and-other-major-privacy-sandbox-apis-as-the-cma-says-cheerio/), [Search Engine Land](https://searchengineland.com/google-officially-shuts-down-privacy-sandbox-463561), [Adweek](https://www.adweek.com/media/googles-privacy-sandbox-is-officially-dead/)).

> **Verdict: ignore Privacy Sandbox entirely. Third-party cookies are NOT deprecated in Chrome and have no removal timeline — the event the industry spent 2020–2024 preparing for did not happen.** The real friction is **Safari/iOS (ATT + ITP)**, not Chrome. The industry's actual answer to signal loss turned out to be **first-party data + server-side tracking (Conversions API)** — that is where our effort belongs.

### 9.10 Two claims circulating in 2026 that are simply false

- **"SKAdNetwork 5 shipped in iOS 19 / Spring 2026."** **False.** SKAN 5.0 was announced at **WWDC 2023 and abandoned** — per Singular, **"SKAN 5 never happened."** Apple pivoted to **AdAttributionKit** (WWDC 2024), "built on the functionality of SKAdNetwork" and interoperable with it. **Production is still SKAN 4.0.** Multiple current-dated blogs claim otherwise; they are recycled/AI-generated content. ([Singular, Jun 2023](https://www.singular.net/blog/skan-5-announced/); [AdExchanger](https://www.adexchanger.com/mobile/apple-is-quietly-replacing-skadnetwork-and-pcm-with-a-new-ad-attribution-framework/); [Apple — AdAttributionKit/SKAdNetwork interoperability](https://developer.apple.com/documentation/adattributionkit/adattributionkit-skadnetwork-interoperability))
- **"A viral formula lets you engineer virality in advance."** Unsupported by the prediction literature — see §8.3. Content features are consistently the *weakest* predictors ([Cheng et al.](https://arxiv.org/abs/1403.4608)).

---

## 10. What to actually do

1. **Structure:** one campaign, **few ad sets** (ideally 1–2), several creatives inside. Never fragment — §3.3.
2. **Bidding:** **Lowest Cost / Highest Volume.** We do not qualify for Cost Cap/ROAS Goal's stated ≥50–100 weekly conversions — §2.
3. **Targeting:** **broad + Advantage+ Audience + automatic placements.** Don't prune placements on average cost — Meta's own math shows it backfires (§1.7).
4. **Creative:** the highest-leverage lever we control. Higher EAR = cheaper impressions, literally (§1.2). Ship many variants; let Meta's impression-level optimizer allocate (§5.2).
5. **Testing:** only test for **large (~50%+) creative gaps** (~$143 at 80% power). Never budget to detect a 20% lift unless we have $1,500+ earmarked for the test alone (§6.4).
6. **Signal:** send clean conversion/engagement signal (Pixel + Conversions API where possible). Use **video-watch depth (≥50%/75%)** as the fan-quality proxy for seeding (§7.3).
7. **Audiences:** broad first. Build lookalikes **after** the seed is real (≥200 converters minimum, more is better) — expect mediocre quality below ~1,000 (§7.2). Retarget at **7 days**, not 180 (§7.4).
8. **Measurement:** Meta in-platform + **pre/post read on Spotify for Artists**. Do not attempt lift tests or MMM (§9). Accept directional, not precise, attribution.
9. **Bandit logic:** only **across** songs/platforms, never inside a Meta campaign (§5.3).
10. **Virality:** don't predict it — **detect it early and pour fuel on it** (§8.4).

---

## Sources

### Meta — auction, bidding, delivery (primary)
- [About ad auctions — Meta Business Help Center](https://www.facebook.com/business/help/430291176997542)
- [About the delivery system: Ad Auctions](https://www.facebook.com/business/help/1000688343301256)
- [About the delivery system: Placements](https://en-gb.facebook.com/business/help/965529646866485) — the blended-cost worked example
- [About Meta Bid Strategies](https://www.facebook.com/business/help/1619591734742116)
- [Bid strategy — Meta for Developers](https://developers.facebook.com/documentation/ads-commerce/marketing-api/bidding/overview/bid-strategy)
- [About Bid Cap](https://www.facebook.com/business/help/272503946776144)
- [About Cost per Result Goal (Cost Cap)](https://en-gb.facebook.com/business/help/272336376749096)
- [About ROAS Goal](https://www.facebook.com/business/help/1113453135474912)
- [About the Learning Phase](https://www.facebook.com/business/help/112167992830700)
- [Significant Edits and the Learning Phase](https://www.facebook.com/business/help/316478108955072)
- [About Advantage+ Audience](https://www.facebook.com/business/help/273363992030035)

### Meta — ML systems (primary)
- [Meta's Generative Ads Model (GEM) — Engineering at Meta, Nov 10 2025](https://engineering.fb.com/2025/11/10/ml-applications/metas-generative-ads-model-gem-the-central-brain-accelerating-ads-recommendation-ai-innovation/)
- [Meta Andromeda — Engineering at Meta, Dec 2 2024](https://engineering.fb.com/2024/12/02/production-engineering/meta-andromeda-advantage-automation-next-gen-personalized-ads-retrieval-engine/)
- [Meta Lattice — ai.meta.com](https://ai.meta.com/blog/ai-ads-performance-efficiency-meta-lattice/)
- [Meta Lattice: Model Space Redesign for Cost-Effective Industry-Scale Ads Ranking (arXiv:2512.09200)](https://arxiv.org/abs/2512.09200)
- [Meta Adaptive Ranking Model — Engineering at Meta, Mar 31 2026](https://engineering.fb.com/2026/03/31/ml-applications/meta-adaptive-ranking-model-bending-the-inference-scaling-curve-to-serve-llm-scale-models-for-ads/)
- [DLRM — Naumov et al., arXiv:1906.00091](https://arxiv.org/abs/1906.00091) · [systems companion, arXiv:1906.03109](https://arxiv.org/pdf/1906.03109)
- [DHEN — arXiv:2203.11014](https://arxiv.org/abs/2203.11014) · [DHEN for ad conversion prediction, arXiv:2504.08169 (WWW 2025)](https://arxiv.org/abs/2504.08169)

### Attribution, incrementality, experiments
- [Gordon, Zettelmeyer, Bhargava & Chapsky (2019), *Marketing Science* 38(2):193–225](https://pubsonline.informs.org/doi/10.1287/mksc.2018.1135) · [free author copy](https://www.kellogg.northwestern.edu/faculty/gordon_b/files/fb_comparison.pdf)
- [Gordon, Moakler & Zettelmeyer, "Close Enough?" (2023), *Marketing Science* 42(4):768–793](https://pubsonline.informs.org/doi/abs/10.1287/mksc.2022.1413) · [arXiv:2201.07055](https://arxiv.org/abs/2201.07055)
- [Johnson, Lewis & Nubbemeyer, "Ghost Ads" (2017), *JMR* 54(6):867–884](https://journals.sagepub.com/doi/abs/10.1509/jmr.15.0297) · [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2620078) · [free NBER version](https://conference.nber.org/confer/2016/EoDs16/Johnson_Lewis_Nubbemeyer.pdf)
- [Meta GeoLift — GitHub](https://github.com/facebookincubator/GeoLift) · [docs](https://facebookincubator.github.io/GeoLift/) · [Best Practices (minimums)](https://facebookincubator.github.io/GeoLift/docs/Best%20Practices/BestPractices/) · [Walkthrough](https://facebookincubator.github.io/GeoLift/docs/GettingStarted/Walkthrough/)
- [About Conversion Lift](https://www.facebook.com/business/help/221353413010930) · [How to set up a Conversion Lift test](https://www.facebook.com/business/help/1598740610886911) · [Marketing API — Lift studies](https://developers.facebook.com/docs/marketing-api/guides/lift-studies/)
- [Brand Lift minimum requirements](https://www.facebook.com/business/help/417527072254206)
- Meta Incremental Attribution *(URLs confirmed; body text unread — see §4.5)*: [About](https://www.facebook.com/business/help/2366718460372682) · [How to Use](https://www.facebook.com/business/help/644554008179419) · [View Results](https://www.facebook.com/business/help/1271619100603114) · [About Attribution Models and Settings](https://www.facebook.com/business/help/460276478298895) · [Seer Interactive test *(secondary)*](https://www.seerinteractive.com/insights/we-tested-metas-new-incremental-attribution-setting-on-1m-in-ad-spend.-heres-how-it-works)
- [Google Ads Developer Blog — MTA model deprecation (Apr 20 2023)](https://ads-developers.googleblog.com/2023/04/first-click-linear-time-decay-and.html) · [Google Ads Help](https://support.google.com/google-ads/answer/6259715?hl=en) · [Search Engine Land](https://searchengineland.com/google-confirms-sunset-details-for-4-attribution-models-in-ads-and-analytics-433352)
- [Forrester — The Perfect Multitouch Attribution Model Doesn't Exist (Apr 2021)](https://www.forrester.com/blogs/the-perfect-multitouch-attribution-model-doesnt-exist/)

### MMM
- [Robyn — Analyst's Guide to MMM (data minimums)](https://facebookexperimental.github.io/Robyn/docs/analysts-guide-to-MMM/)
- [Google Meridian — Amount of data needed](https://developers.google.com/meridian/docs/pre-modeling/amount-data-needed)
- [Jin, Wang, Sun, Chan & Koehler — Bayesian Methods for Media Mix Modeling with Carryover and Shape Effects (Google, 2017)](https://research.google/pubs/bayesian-methods-for-media-mix-modeling-with-carryover-and-shape-effects/)
- [Recast — MMM minimum spend *(vendor, secondary)*](https://getrecast.com/data/)

### Privacy — ATT, SKAN, AEM, Privacy Sandbox
- [Apple — User Privacy and Data Use](https://developer.apple.com/app-store/user-privacy-and-data-use/) · [AppTrackingTransparency](https://developer.apple.com/documentation/apptrackingtransparency)
- [Apple — SKAdNetwork](https://developer.apple.com/documentation/storekit/skadnetwork) · [Receiving postbacks in multiple conversion windows (crowd anonymity)](https://developer.apple.com/documentation/storekit/receiving-postbacks-in-multiple-conversion-windows)
- [Apple — AdAttributionKit/SKAdNetwork interoperability](https://developer.apple.com/documentation/adattributionkit/adattributionkit-skadnetwork-interoperability) · [Singular — "SKAN 5 never happened"](https://www.singular.net/blog/skan-5-announced/) · [AdExchanger — Apple replacing SKAdNetwork](https://www.adexchanger.com/mobile/apple-is-quietly-replacing-skadnetwork-and-pcm-with-a-new-ad-attribution-framework/)
- [Meta — About Aggregated Event Measurement](https://www.facebook.com/business/help/721422165168355) · [Conversion event configuration](https://www.facebook.com/business/help/2151428647238533) · [SKAN reporting delay](https://www.facebook.com/business/help/188126096313109)
- [Aridor, Che, Hollenbeck, Kaiser & McCarthy — *Management Science* (2025)](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.06600) · [PDF](https://www.anderson.ucla.edu/sites/default/files/document/2025-05/att_privacy.pdf)
- [CNBC — Meta's $10B ATT estimate (Feb 2 2022)](https://www.cnbc.com/2022/02/02/facebook-says-apple-ios-privacy-change-will-result-in-10-billion-revenue-hit-this-year.html)
- [Privacy Sandbox — Update on Plans (Oct 17 2025)](https://privacysandbox.google.com/blog/update-on-plans-for-privacy-sandbox-technologies) · [Next steps (Apr 22 2025)](https://privacysandbox.google.com/blog/privacy-sandbox-next-steps) · [AdExchanger](https://www.adexchanger.com/privacy/google-pulls-the-plug-on-topics-paapi-and-other-major-privacy-sandbox-apis-as-the-cma-says-cheerio/) · [Search Engine Land](https://searchengineland.com/google-officially-shuts-down-privacy-sandbox-463561) · [Adweek](https://www.adweek.com/media/googles-privacy-sandbox-is-officially-dead/)

### Bandits & experimentation
- [Auer, Cesa-Bianchi & Fischer — Finite-Time Analysis of the Multi-Armed Bandit Problem (2002)](https://link.springer.com/article/10.1023/A:1013689704352)
- [Chapelle & Li — An Empirical Evaluation of Thompson Sampling (NIPS 2011)](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/thompson.pdf)
- [Agrawal & Goyal — Analysis of Thompson Sampling (arXiv:1111.1797)](https://arxiv.org/pdf/1111.1797)
- [Meta — About Dynamic Creative](https://www.facebook.com/business/help/170372403538781) · [asset_feed_spec / Dynamic Creative (developers)](https://developers.facebook.com/documentation/ads-commerce/marketing-api/ad-creative/asset-feed-spec/dynamic-creative)
- [Mobile Dev Memo — Bayesian Bandits: Facebook's spend allocation *(secondary, credible)*](https://mobiledevmemo.com/bayesian-bandits-behind-the-scenes-of-facebooks-spend-allocation-decisioning/)
- [Facebook Research — Auto-placement of ad campaigns using multi-armed bandits (2021)](https://research.facebook.com/blog/2021/04/auto-placement-of-ad-campaigns-using-multi-armed-bandits/)
- [Auctions Meet Bandits (arXiv:2508.21162)](https://arxiv.org/abs/2508.21162) · [Stochastic Bandits for Multi-platform Budget Optimization (arXiv:2103.10246)](https://arxiv.org/pdf/2103.10246) · [High-dimensional Bandits for Media/Audience Optimization (arXiv:2209.08403)](https://arxiv.org/pdf/2209.08403)
- [JD.com — Comparison Lift (arXiv:2009.07899, AAAI 2021)](https://arxiv.org/abs/2009.07899)

### Testing math & benchmarks
- [Select Statistical Consultants — Sample Size Calculator, Two Proportions](https://select-statistics.co.uk/calculators/sample-size-calculator-two-proportions/)
- [Statology — Two Proportion Z-Test](https://www.statology.org/two-proportion-z-test/) · [Statistics LibreTexts §9.3](https://stats.libretexts.org/Bookshelves/Introductory_Statistics/Mostly_Harmless_Statistics_(Webb)/09:_Hypothesis_Tests_and_Confidence_Intervals_for_Two_Populations/9.03:_Two_Proportion_Z-Test_and_Confidence_Interval)
- [Evan Miller sample-size formula (pooled variant)](https://gist.github.com/mottalrd/7ddfd45d14bc7433dec2)
- [Sovran — Meta Ads CPM by Industry 2026](https://sovran.ai/benchmarks/meta-ads-cpm-by-industry) · [Triple Whale — Facebook Ad Benchmarks](https://www.triplewhale.com/blog/facebook-ads-benchmarks) · [WordStream — Facebook Ads Benchmarks 2025](https://www.wordstream.com/blog/facebook-ads-benchmarks-2025)

### Audiences
- [Meta for Developers — Lookalike Audiences](https://developers.facebook.com/docs/marketing-api/audiences/guides/lookalike-audiences/) · [About Lookalike Audiences](https://www.facebook.com/business/help/164749007013531)
- [Meta for Developers — Value-Based Lookalike Audiences](https://developers.facebook.com/documentation/ads-commerce/marketing-api/audiences/guides/value-based-lookalike-audiences) · [Create a lifetime value Custom Audience](https://en-gb.facebook.com/business/help/185705781836755) · [Best Practices for CLV](https://www.facebook.com/business/help/1249127098589034)
- [AdEspresso — 1% vs 5% vs 10% lookalike experiment *(single case study)*](https://adespresso.com/blog/adespresso-experiment-facebook-lookalike-audience/)
- Seed-size heuristics *(agency folklore, no primary source — see §7.2)*: [Lionelz](https://lionelz.com/en/blog/facebook-lookalike-audiences-opportunities/) · [Skai](https://skai.io/blog/lookalike-audiences/) · [Madgicx](https://madgicx.com/blog/why-facebook-lookalike-audiences-are-worth-your-ad-spend)
- [Video Engagement Custom Audience thresholds *(secondary)*](https://knorex.zendesk.com/hc/en-us/articles/360058219091-How-to-Create-a-Video-Engagement-Custom-Audience-with-Meta-Ads)
- Purchase-audience 730-day retention *(practitioner-sourced)*: [CommonThreadCo](https://commonthreadco.com/blogs/coachs-corner/meta-purchase-audience-730-days-ecommerce) · [Jon Loomer](https://www.jonloomer.com/chatgpt-ads-self-serve/)

### Diffusion & virality
- [Bass (1969), *Management Science* 15(5):215–227 — DOI 10.1287/mnsc.15.5.215](https://doi.org/10.1287/mnsc.15.5.215) · [summary](https://en.wikipedia.org/wiki/Bass_diffusion_model)
- [Daley & Kendall, "Stochastic Rumours" (1965), *IMA J. Appl. Math.* 1(1):42–55 — DOI 10.1093/imamat/1.1.42](https://doi.org/10.1093/imamat/1.1.42)
- [Compartmental models in epidemiology (SIR equations)](https://en.wikipedia.org/wiki/Compartmental_models_in_epidemiology) · [Uncertain SIR rumor model (Springer 2021)](https://link.springer.com/article/10.1186/s13662-021-03386-w)
- [Cheng, Adamic, Dow, Kleinberg & Leskovec — Can Cascades Be Predicted? (WWW 2014, arXiv:1403.4608)](https://arxiv.org/abs/1403.4608) · [ACM DL](https://dl.acm.org/doi/10.1145/2566486.2567997)
- [Bakshy, Hofman, Mason & Watts — Everyone's an Influencer (WSDM 2011)](https://snap.stanford.edu/class/cs224w-readings/bakshy11influencers.pdf)
- [Goel, Anderson, Hofman & Watts — The Structural Virality of Online Diffusion (*Management Science*, 2016)](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/04/twiral.pdf)
- [Early Multimodal Prediction of Cross-Lingual Meme Virality (arXiv:2510.05761)](https://arxiv.org/html/2510.05761v1) · [Predicting Post Virality with Temporal Cross-Attention (arXiv:2605.02358)](https://arxiv.org/html/2605.02358) · [Botelho — Predicting Virality: How Soon Can We Tell?](https://medium.com/cybersecurity-for-democracy/predicting-virality-how-soon-can-we-tell-dc153a98774f)
- Music-specific: [Beyond the Hook (arXiv:2509.24856)](https://arxiv.org/pdf/2509.24856) · [Spotify Chart Success (arXiv:2508.11632)](https://arxiv.org/html/2508.11632) · [CNN on Spotify features/spectrograms (arXiv:2505.07280)](https://arxiv.org/html/2505.07280v1) · [Spotify WSDM Cup 2019 skip prediction (arXiv:1901.08203)](https://arxiv.org/pdf/1901.08203)
- Skeptical counterweight: [MIT Sloan Management Review — Is Viral Marketing a Myth?](https://sloanreview.mit.edu/article/is-viral-marketing-a-myth/) · [Neil Patel — The Science of Virality *(popular, non-rigorous — cited as an example of the genre)*](https://neilpatel.com/blog/science-of-virality/)

---

## Confidence notes

| Claim | Status |
|---|---|
| Auction components (bid × EAR + quality) | **Primary (Meta).** Exact algebraic form is a secondary rendering, not Meta notation. |
| "Second-price"/GSP framing | **Medium** — behavior is Meta-documented; the GSP label is our inference. |
| GEM / Andromeda / Lattice architecture & results | **Primary** (Meta engineering blogs + arXiv). |
| "Lattice Zipper" / "Lattice Filter" components | **Medium** — secondary only; not in primary sources. |
| DLRM's specific role in *ads* ranking | **Medium** — well-established in literature, not verbatim in abstract. DHEN-for-ads is primary ([arXiv:2504.08169](https://arxiv.org/abs/2504.08169)). |
| ~50 conversions/week threshold | **Primary (Meta).** |
| *Why* ~50 (the $1/\sqrt{k}$ derivation, §3.2) | **Our analysis, not Meta's.** Meta publishes no rationale. Present as "consistent with," never "Meta-confirmed." |
| Meta runs Bayesian-bandit spend allocation | **Medium-high** — credible independent analyst (Seufert); Meta doesn't publish the algorithm. |
| Power-analysis figures (§6) | **Independently recomputed and verified** for this report. |
| CPM benchmarks | **Secondary** (vendor aggregations). Arts & Entertainment CPM is triangulated, not directly reported. |
| Conversion Lift / Brand Lift minimums | **Primary (Meta).** Note the widespread "$30k Conversion Lift" claim is a **conflation with old Brand Lift** — §9.3. |
| Meta "Incremental Attribution" details | **Low-medium** — URLs confirmed, body text unreadable (JS-rendered). "46% lift" figure has **no primary source — do not repeat.** "Holdout-based" framing **contradicts Meta's own quoted language.** |
| Robyn/Meridian data minimums | **Primary** (vendor docs). |
| Recast "$10/day" MMM floor | **Secondary (vendor).** |
| GeoLift minimums | **Primary (Meta docs).** |
| Ghost Ads / Gordon et al. figures | **Primary, DOI-verified via Crossref.** |
| ATT impact (36.6% CTR reduction; harm concentrated on small firms) | **Primary, peer-reviewed** (*Management Science*). |
| Privacy Sandbox retirement (Oct 17 2025) | **Primary (Google), multiply corroborated.** |
| "SKAN 5 in 2026" | **False.** SKAN 5 abandoned; production is SKAN 4.0; successor is AdAttributionKit. |
| Lookalike seed 100 min / 200 converters recommended | **Primary (Meta).** |
| Lookalike seed "1,000+/5,000–10,000 sweet spot" | **Agency folklore** — no primary source, no common cited study. |
| Purchase audience 730-day retention (May 18 2026) | **Medium-high** — practitioner sources consistent; Meta primary page not directly fetched. |
| Virality: content features are weak predictors | **High** — convergent across Facebook/Twitter/Reddit datasets, 2011–2026. |
| "Viral formulas" (STEPPS etc.) as predictive tools | **Unsupported** by the cascade-prediction literature. |
| No public Spotify research on pre-release virality prediction | **Not found** — stated as a gap, not a negative proof. |
