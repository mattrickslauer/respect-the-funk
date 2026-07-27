# Pillar 10: Creator Indexing & Audience Demographic Data

Research for the "Losing Sleep" (Hallow Youth / Respect the Funk) marketing engine — how to find, index, and evaluate creators by **the demographics of their audience**, not their own follower count. Researched July 2026.

---

## Bottom line / recommendation

**The cheapest viable path is to stop trying to buy audience demographics, and instead buy *sound-usage data* + use free platform-native tools.** Here's the reasoning:

1. **Audience demographics for creators you haven't onboarded are estimates, not measurements — and no vendor claims otherwise.** Every platform (TikTok, Meta, YouTube) gates true audience demographic data behind the *creator's own OAuth grant*. There is no endpoint on any platform that returns an arbitrary creator's follower demographics. Third-party tools therefore infer age/gender/country by running image recognition and NLP over a **sample of the publicly visible subset** of a creator's followers, then extrapolating. Modash says this plainly: it's *"based on a statistical sample of the audience, so treat it as a strong indicator rather than exact numbers"* ([Modash Help Center](https://help.modash.io/en/articles/13715083-understanding-audience-demographics-and-insights)). **Not a single vendor in this market publishes an accuracy figure for their audience age/gender/country estimates.** See §4 — this is the most important finding in this report.

2. **The tools that *do* have first-party platform data are all enterprise-priced and out of range.** CreatorIQ (~$25–60K/yr) has official TikTok Marketplace API and YouTube Creator Partnership API access; Kolsquare (€500/mo, annual-only) is a Meta Business Partner. Everything affordable — Modash, HypeAuditor, Heepsy — is scraped-and-estimated. And critically: **even the enterprise tools' first-party data only covers creators who have connected their accounts.** Their discovery indexes are still estimated. See §3.

3. **Sound-usage data is higher-signal than demographic data and 10x cheaper.** If a creator already posted with an adjacent artist's sound and it performed, that is *measured behavioral evidence* their audience likes this music — strictly better than a 60%-confidence guess that their audience is 62% female aged 18–24. **Soundcharts at $10–49/mo** or **Chartmetric at $40–117/mo** gets you this ([Soundcharts pricing](https://soundcharts.com/en/pricing); [Chartmetric pricing](https://chartmetric.com/pricing)).

### Recommended stack for Respect the Funk (~$150–350/mo)

| Step | Tool | Cost | What it gives |
|---|---|---|---|
| 1. Find adjacent artists & who's using their sounds | **Soundcharts** (10-artist tier) or **Chartmetric Manager** | $49/mo / $40/mo | TikTok sound velocity, video counts per song, artist audience demos |
| 2. Manually pull creator lists off TikTok sound pages | TikTok native (logged-out browsable) | $0 | Actual creators who already used adjacent sounds — measured, not estimated |
| 3. Verify creator audience demos — for shortlisted creators only | **TikTok One** (free) + **ask the creator for a screenshot of their own analytics** | $0 | *Ground truth* from the creator's own dashboard |
| 4. Only if step 3 doesn't scale | **Modash Essentials** | $199/mo annual | 380M index, honest methodology, has an API path |

**Steps 1–3 cost under $50/month and produce better data than a $299/mo estimation tool**, because step 3 gets you the creator's *actual* Instagram/TikTok Insights numbers — the same first-party data CreatorIQ pays TikTok for — simply by asking. At our roster size (one artist, dozens not thousands of creators), **asking beats inferring**. Add Modash only when manual sourcing becomes the bottleneck.

**Do not scrape.** See §5 — the legal picture is more favorable to scrapers than commonly believed (Meta *lost* to Bright Data in 2024), but every platform's ToS prohibits it, enforcement against small players is technical (bans/blocks) rather than legal, and hiQ won the CFAA question twice and still went out of business. The asymmetry isn't worth it when sound-page data is free to browse.

**Budget for creator payments, not tools.** Music sound-use rates run **20–40% below** equivalent brand-sponsorship rates ([InfluencerFee](https://influencerfee.com/blog/music-influencer-rates/)) — nano creators at $20–150/video, micro at $100–1,000. See §7.

---

## 1. The core data problem

The thing we need — *who follows this creator* — is the thing every platform guards hardest, and for a coherent reason: it's their users' personal data, and exposing it to arbitrary third parties is a privacy liability.

**The universal rule across all three platforms: audience demographics flow only with the creator's explicit consent.** There is no exception, no partner tier, and no price that unlocks arbitrary lookup.

| Platform | Marketplace (2026 name) | Audience demos to brands? | Mechanism | Creator threshold | 3rd-party API | Cost to brand |
|---|---|---|---|---|---|---|
| TikTok | **TikTok One** (rebrand of Creator Marketplace, Mar 2025) | Yes, in-platform | First-party data in marketplace UI | 10k followers, 1k views/30d, 3 posts/30d, 18+ | Creator Marketplace API — approved partners only | No published fee (likely free) |
| Meta/IG | **Brand Collabs Manager** | Yes + "audience match" score | In-platform UI only | 25k followers (2018 doc); 1k claimed for 2026 — UNVERIFIED | Graph API — per-creator OAuth + Advanced Access; **no arbitrary lookup** | Free; Meta takes no cut |
| YouTube | **Creator Partnerships** (ex-BrandConnect, renamed NewFronts 2026) | Only if creator opts into "channel insight sharing" | In-platform + invite-only API | 18+, in YPP, no strikes | Creator Partnerships API — ~17–24 approved partners, no self-serve | Bundled with Google Ads account |

### 1a. TikTok One (formerly TikTok Creator Marketplace / TTCM)

**The rebrand matters for anyone reading older docs.** TTCM was rebranded to **TikTok One** in early 2025; the legacy Creator Marketplace and Creative Challenge platforms stopped accepting new projects after **March 10, 2025** ([TikTok: How creators can upgrade to TikTok One](https://ads.tiktok.com/help/article/how-creators-can-upgrade-to-tiktok-one)). TikTok One consolidates Creator Marketplace, Creative Center, and Partner Exchange under `ads.tiktok.com`; a May 13, 2026 announcement describes a new "Creator AI Search" for shortlisting creators ([TikTok Business Blog](https://ads.tiktok.com/business/en/blog/tiktok-one-creative-platform)).

**What it exposes:** Brands browsing the marketplace can see and filter creators by **audience demographics (age, gender, location)** alongside niche, engagement rate, and past performance — this is TikTok's *first-party* data, i.e., real, not estimated ([TikTok One Support](https://support.tiktok.com/en/business-and-creator/tiktok-one)). **The exact fields shown (age bands, gender split, country list) are UNVERIFIED** — TikTok's support article doesn't enumerate them, and secondary guides ([StackMatix](https://www.stackmatix.com/blog/tiktok-creator-marketplace-guide)) may be stale.

**Creator eligibility to be listed** ([TikTok One Support](https://support.tiktok.com/en/business-and-creator/tiktok-one)):
- Account in good standing, no repeated policy violations
- Age 18+ (regional variations)
- **10,000+ followers** (regional variations)
- **1,000+ post views in last 30 days**
- **3 posts published in last 30 days**

> **This threshold is a strategic problem for us.** The 10k-follower minimum means **nano creators (1k–10k) — the cheapest and highest-acceptance-rate tier for music seeding (§7) — are entirely invisible in TikTok One.** For the nano tier, sound pages and manual sourcing are the only route.

**Cost:** No published subscription fee. Brands appear to access it free and pay creators directly. **UNVERIFIED** — no primary source states "free" explicitly; inferred from absence of pricing language.

**API:** TikTok launched a **Creator Marketplace API** in 2021 giving "partnered marketing companies" access to first-party audience demographics, growth trends, and campaign reporting ([TechCrunch, 2021](https://techcrunch.com/2021/08/31/tiktoks-new-creator-marketplace-api-lets-influencer-marketing-companies-tap-into-first-party-data/)). Access is restricted to **approved partner organizations, not self-serve developers**. Whether it survives unchanged under the TikTok One rebrand is **UNVERIFIED** — no primary 2026 doc found. CreatorIQ is a confirmed current partner (§3).

### 1b. Meta / Instagram — the `instagram_manage_insights` question, answered precisely

This is the most misunderstood part of the stack, and the answer is more nuanced than "own account only."

**Brand Collabs Manager** is the official product name (no "Meta Creator Marketplace" exists in Meta's own materials — that term appears only in third-party blogs). Per Meta's own creator-facing page, a creator's portfolio *"displays niche categories, audience demographics, and recent post performance to any brand running a search,"* and brands get an **"audience match"** score showing what percentage of a creator's audience overlaps their target ([Meta: Introducing Brand Collabs Manager](https://creators.facebook.com/introducing-brand-collabs-manager/?locale=en_US)).

**Creator eligibility:** The only dated primary source found (Meta Business Help Center, content dated July 30, 2018) states US creators, 25,000+ followers ([Meta Business Help](https://www.facebook.com/business/help/1225872907555801)). Secondary sources claim the bar has since dropped to **1,000+ Page followers** plus engagement thresholds (15,000 post engagements OR 180,000 minutes viewed OR 30,000 one-minute views) and expanded beyond the US. **UNVERIFIED** — could not be traced to a primary Meta page; Meta's help pages render via JS and returned title-only stubs to automated fetching. Worth a manual browser check.

**Cost:** No access fee for brands. Meta states it does not take a cut of brand-partnership payments (2018-era language, with a "this may change" caveat) ([Meta](https://creators.facebook.com/introducing-brand-collabs-manager/?locale=en_US)).

#### The Graph API distinction — exactly right

**Permissions** ([Meta: Insights — Instagram Platform](https://developers.facebook.com/docs/instagram-platform/insights/)):
- Instagram Login flow: `instagram_business_basic` + `instagram_business_manage_insights`
- Facebook Login flow: `instagram_basic` + `instagram_manage_insights` + `pages_read_engagement`

**Endpoint:** `GET /{ig-user-id}/insights` ([Instagram User Insights API reference](https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-user/insights/)).

**The demographic metrics that exist:**
- `follower_demographics` — follower age/gender/city/country breakdown. **Requires the account to have 100+ followers.**
- `engaged_audience_demographics` — demographic breakdown (country, city, gender) of accounts that engaged with content.
- Both carry Meta's own caveat: *"Only viewers for whom we have demographic data are used in demographic metric calculations."* — **even Meta's own first-party numbers are partial.**

**The three rules that define what's possible:**
1. *"You can only get insights for a single user at a time."* ([Meta docs](https://developers.facebook.com/docs/instagram-platform/insights/)) — no bulk creator queries.
2. An app needs **Advanced Access** *"if your app serves Instagram professional accounts you don't own or manage"* ([same](https://developers.facebook.com/docs/instagram-platform/insights/)).
3. **There is no endpoint that returns another creator's audience demographics from a handle or search.** None. Meta does not expose a public creator-demographics lookup.

**So the precise answer:** `instagram_manage_insights` is **not** restricted to your own account — it's restricted to **accounts that have explicitly authorized your specific app**, one at a time, and serving accounts you don't own additionally requires Meta's **Advanced Access** app-review tier. A third-party tool *can* read a creator's `follower_demographics` — but **only** if (a) that individual creator OAuth'd into that specific app, **and** (b) the app passed App Review for Advanced Access. Never unilaterally. This is exactly the flow influencer platforms use ([Phyllo explainer](https://www.getphyllo.com/post/instagram-audience-demographics-for-influencer-marketing-platforms)).

> **Implication for us:** we could build this ourselves — an app that creators OAuth into, giving us their true `follower_demographics`. But at our scale, asking a creator to screenshot their own Insights tab achieves the identical result for $0 and no App Review. **Build the OAuth app only if we're onboarding hundreds of creators.**

### 1c. YouTube

**YouTube Analytics API is strictly owner-gated.** All requests require **OAuth 2.0** and the API explicitly **does not support the service account flow** — access must come from an individual's OAuth grant ([Google: YouTube Reporting API authorization](https://developers.google.com/youtube/reporting/guides/authorization/installed-apps)). Scopes: `yt-analytics.readonly`, `yt-analytics-monetary.readonly` ([YouTube Analytics API](https://developers.google.com/youtube/analytics)). Available dimensions include age group, gender, country, device, traffic source — **but only for channels whose owner authorized your app**. **Content Owner Reports** extend this only to channels formally linked under the same Content Owner (e.g., a label/MCN with rights) — still not arbitrary channels ([Content Owner Reports](https://developers.google.com/youtube/analytics/content_owner_reports)).

**YouTube Creator Partnerships** (renamed from BrandConnect, announced NewFronts, page dated **March 23, 2026**) rolled out first to 7 markets — US, India, Indonesia, UK, Brazil, Australia, Canada ([YouTube Blog](https://blog.youtube/news-and-events/youtube-creator-partnerships-newfronts-2026/); [ppc.land](https://ppc.land/youtube-creator-partnerships-replaces-brandconnect-in-7-markets/)). It's built into YouTube Studio (creator side) and Google Ads / DV360 (advertiser side).

Audience data is **opt-in by the creator** — YouTube's own language: *"We also recommend that creators opt-in to channel insights sharing to provide advertisers more holistic data about your channel"* ([YouTube Help](https://support.google.com/youtube/answer/9385307?hl=en)). Exact fields shared are **UNVERIFIED** (help page doesn't itemize). Creator eligibility: 18+, in YPP, ad-revenue-share eligible, available country, no active strikes ([same](https://support.google.com/youtube/answer/9385307)).

**The Creator Partnerships API is invite-only** — roughly 17–24 approved platforms as of NewFronts 2026 (CreatorIQ, TRIBE, impact.com, Meltwater, Sprout Social, Viral Nation, and others), providing "audience demographics, geographic distribution, and interest-based insights" ([netinfluencer](https://www.netinfluencer.com/youtube-lists-17-creator-partnerships-api-partners-as-advertiser-access-remains-controlled/); [streamer.guide](https://streamer.guide/blog/youtube-creator-partnerships-api-newfronts-2026)). Basic Creator Search/Overview/Lists is available to any active Google Ads account in a YPP country ([netinfluencer](https://www.netinfluencer.com/youtube-opens-creator-partnerships-api-to-third-party-influencer-marketing-platforms/)) — **UNVERIFIED** for a formal price, appears bundled/free.

**YouTube is the lowest-priority channel for this campaign** anyway — sound-driven discovery is a TikTok/Reels dynamic.

---

## 2. Vendor comparison — creator databases

### Price and access

| Vendor | Cheapest real price | API? | API cost | Contract | Verdict for a small label |
|---|---|---|---|---|---|
| **Heepsy** | **$89/mo** (but audience filters need **$249/mo** tier) | **None** | — | Monthly | Cheapest, but no API = dead end for an engine |
| **Modash** | **$199/mo** annual / $299 monthly | Yes | ~$10–16.2K/yr (**UNVERIFIED**) | Monthly or annual, 14-day free trial | ✅ **Best fit** — honest, cheap seat, API path exists |
| **HypeAuditor** | **$299/mo** annual | Yes | **Enterprise only** | Annual | Usable UI; API tier disqualifying |
| **Grin** | **$399/mo** Lite (self-serve, new ~Jan 2026) | Yes (CRM only, not discovery) | Complete tier $1,799/mo | Month-to-month on self-serve | CRM-first; enterprise track median **$27,150/yr** |
| **Kolsquare** | **€500/mo**, annual only | Enterprise only, extra cost | Custom | **Annual only, no trial** | 30% startup discount may apply → ~€350/mo |
| **Upfluence** | Sticker "$478/mo" — **real min ~$1,276/mo** | Yes | Sales-gated | **12-mo lock-in** + $695 onboarding | ❌ Misleading pricing, smallest DB |
| **Aspire** | ~$2,499/mo (Capterra estimate) | **None found** | — | Annual, no trial | ❌ Out of range |
| **CreatorIQ** | ~$25–30K/yr floor; median **$39,250/yr** | Yes | By request | Annual only | ❌ Out of range (best data, though) |

### Data and provenance — the column that actually matters

| Vendor | Creators indexed | Audience data provided | **How obtained** | Accuracy claim |
|---|---|---|---|---|
| **Modash** | 380M+ ([source](https://www.modash.io/influencer-database)) | Age, gender, country, language, interests, credibility score (0–1), fake-follower rate | **Scraping + ML estimation.** Self-described as *"like Google Search crawling"*; image recognition + statistical sampling ([Modash Data](https://www.modash.io/data)) | **None.** *"Sampling model, not a complete census"* — most honest in the set |
| **HypeAuditor** | 227.8M+ ([source](https://hypeauditor.com/discovery/)) | Location, age, gender, **ethnicity**, interests, AQS | **Public scraping only, explicitly disclosed:** *"We collect publicly available information from open sources... We do not access private data such as DMs or insights from influencer accounts"* ([methodology](https://hypeauditor.com/collect-analyze-influencer-data/)) | **95.5% fraud detection, 0.73% mean error — for FRAUD, not demographics** (see §4) |
| **CreatorIQ** | ~15M ([source](https://www.creatoriq.com/lp/icp-surface-any-creator-profile)) | Age, gender, location, interests (creator + audience); YouTube real *viewer* demos | **Genuine first-party APIs:** official TikTok Marketing Partner w/ Marketplace API ([source](https://www.creatoriq.com/blog/tiktok-first-party-audience-insights)); YouTube Creator Partnership API since Mar 26, 2026 ([press](https://www.creatoriq.com/press/releases/creatoriq-deepens-partnership-with-youtube-with-integration-unlocking-audience-insights-for-smarter-creator-campaigns)). Meta equivalent UNVERIFIED | No numeric claim |
| **Kolsquare** | "Millions," 5K+ followers, 180+ countries (exact # UNVERIFIED) | Age, gender, country/city, interests, language, Credibility Score (0–100) | **Hybrid:** official **Meta Business Partner** — *"verified data directly from Instagram"* via creator OAuth ([FAQ](https://www.kolsquare.com/en/frequently-asked-questions)). Everything else "aggregated public data." No TikTok/YouTube partnership found | *"Real reach and impressions (verified, not estimated)"* — **connected accounts only** |
| **Upfluence** | 12M+ ([source](https://www.upfluence.com/influencer-search)) — **~1/30th of Modash** | ER, fake-follower %; IG audience age/gender/location *"if the influencer authorizes it"* ([IMH](https://influencermarketinghub.com/upfluence/)) | Mixed, poorly disclosed. OAuth for the IG slice. Differentiator is Shopify/e-comm first-party data. **No platform partnership found** | None. Own disclaimer: percentages "not absolutes and are estimated" |
| **Heepsy** | 4M+ ([source](https://www.heepsy.com/influencer-database)) — conflicting 7M/9M/11M figures | ER, AQS, fake/bot detection; age/gender/location filters (**UNVERIFIED**, 403-blocked) | **No methodology page, no API partnership, no OAuth model found.** Accuracy "based on sampling" | ~85% fake-follower detection — **third-party paraphrase, UNVERIFIED** |
| **Grin** | 190M+ ([source](https://grin.co/product/influencer-discovery-platform/)) | Audience age, gender, "other key demographics"; credibility 0–100. Country/interest breakdowns **UNVERIFIED** | **Public scraping** for free tools (*"examines publicly available signals"* — [source](https://grin.co/influencer-marketing-tools/fake-influencer-tool/)). "Gia" AI trained on *$1B in verified brand-creator transactions* — real conversion data, **but only for creators who transacted through Grin** | None. Hedge: *"no automated tool is 100% definitive"* |
| **Aspire** | 1M+ ([source](https://www.aspire.io/platform/creator-marketplace)) — **opt-in marketplace, not an index** | Age, gender, geography; Audience Authenticity 0–100 | **Opt-in + heuristic.** Creators self-register and connect socials. Authenticity uses *"the follower account's avatar and bio description, number of posts, followers vs following ratio..."* ([help](https://help.aspireiq.com/en/articles/6027302-what-is-audience-authenticity)) — heuristic over follower list, **not** API data | **None.** Says manual vetting "is still important" |

### Three traps in this market

1. **"Official API partnership" ≠ "your search results are first-party."** CreatorIQ has real TikTok and YouTube API access — but that covers *opted-in/connected creators*. Its 15M discovery graph still blends public data. Same for Kolsquare and Grin. **The question to ask any vendor: *"Is the audience demographic data on a creator I haven't onboarded first-party or estimated?"* Expect "estimated."**
2. **Database sizes are not comparable.** Thresholds differ (Kolsquare 5K+, Heepsy 1K vs 3K, others undisclosed) and "profiles" vs "accounts" vs "registered creators" mean different things. Modash's 380M and Upfluence's 12M do not measure the same thing — though a 30x gap still signals real coverage differences for niche/emerging music creators.
3. **Sticker prices mislead.** Upfluence's G2-listed "$478/mo" becomes **~$1,276/mo** once you buy the three modules you actually need, plus $79/mo per extra seat, $695 onboarding, and a mandatory 12-month contract ([creator-hero](https://www.creator-hero.com/blog/upfluence-pricing-and-review)). Heepsy's $89 tier **excludes audience filters** — the only reason we'd use it.

### The central constraint of this whole market

**Cheap seat vs. expensive API.** Modash is the only vendor with a genuinely small-team price ($199–299/mo), a documented API, and honest methodology — but its API reportedly starts at **$10–16.2K/yr** (**UNVERIFIED**, from aggregators). Heepsy is cheapest and has **no API at all**. HypeAuditor's API is **Enterprise-only**. So: the affordable plans are all UI-only. **A programmatic creator-indexing engine, built on purchased data, does not have a sub-$10K/yr path.** This is why the recommendation is to index manually from sound pages instead.

---

## 3. Data provenance and accuracy — be skeptical

**This section is the deliverable the rest of the report depends on.**

### What the vendors actually do

Absent platform API access (which none of the affordable tools have), the method is:

1. Crawl public creator profiles repeatedly (Modash: *"several times a month... about sections, captions & descriptions of posts, images, videos, and other public info"* — [Modash Data](https://www.modash.io/data)).
2. Look at the **subset of followers/commenters whose profiles are public**.
3. Run **image recognition on profile photos** and **NLP on names/bios/captions** to guess each one's age, gender, and location.
4. **Extrapolate that sample to the entire audience.**

Location specifically is inferred from *"location tags, languages, captions, bios, and other signals from followers"* ([Modash Help](https://help.modash.io/en/articles/13715083-understanding-audience-demographics-and-insights)).

### The four structural limitations — all vendor-admitted

1. **Private follower lists = no data at all.** Modash: if a creator's follower list is private, *"audience demographics can't be calculated"* ([Modash blog](https://www.modash.io/blog/how-to-check-influencer-audience-demographics)). This is direct proof the method is scrape-dependent, not API-privileged.
2. **The sample is not random — it's the *public* subset.** Private and inactive followers are structurally invisible. Whether public followers demographically resemble private ones is **an untested assumption**, and there's a plausible reason to think not (privacy behavior correlates with age and gender).
3. **Accuracy degrades exactly where we want to operate.** Modash: fake-follower analysis is *"solid for established creators with 10K+ followers,"* and *"thinner"* for nano-influencers, because there are *"limited behavioral patterns to analyze"* under ~10,000 followers ([Modash Data](https://www.modash.io/data)). **Our cheapest, highest-conversion tier (nano, 1k–10k) is the tier where this data is weakest.**
4. **For very large accounts it gets more speculative, not less.** Modash says it *"sometimes infer[s] audience data by analysing signals from millions of other public profiles — but only when confidence is high enough"* — with **no disclosed confidence threshold** ([Modash Data](https://www.modash.io/data)).
5. **Even Meta's own first-party data is partial:** *"Only viewers for whom we have demographic data are used in demographic metric calculations"* ([Meta docs](https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-user/insights/)). The ground truth is itself a subset.

### How wrong are they? Honest answer: nobody publicly knows.

**No independent, peer-reviewed study was found that audits any of these platforms' demographic estimates against ground truth** (e.g., a creator's actual Instagram Insights). This is a genuine gap in public research, not an oversight in this review — targeted searching found no Digiday/Business Insider/academic audit of demographic-estimate accuracy. *(Negative finding limited by search — absence of evidence, not evidence of absence.)*

**What we do have:**

| Claim | Number | What it actually measures | Source |
|---|---|---|---|
| HypeAuditor's accuracy claim | **95.5% of known fraud detected, 0.73% mean error** | **Fraud-detection recall against *known* fraud. NOT demographic accuracy.** | [HypeAuditor methodology](https://hypeauditor.com/collect-analyze-influencer-data/) |
| Heepsy's accuracy | ~85% fake-follower detection | Fake followers, not demographics. **Third-party paraphrase, not Heepsy's wording — UNVERIFIED** | [Hyred](https://hyred.com/heepsy-comparison) |
| Academic bot classifiers | 91.4% acc / 90.8% precision / 92.1% recall; some claim 99.94% | **Bot detection on 2015 Twitter data, in-sample/lab metrics.** Not demographic inference | Cresci et al., *Decision Support Systems*, [arXiv:1509.04098](https://arxiv.org/abs/1509.04098) |

> **⚠️ The single most important skeptical point in this report:** HypeAuditor's **95.5%** is the only hard number any vendor publishes, and a salesperson will let you believe it means "our data is 95.5% accurate." **It does not.** It is fraud-detection recall against known fraud. **Nobody — not Modash, not HypeAuditor, not CreatorIQ, not Kolsquare, not Grin, not Aspire — publishes any accuracy figure for audience age/gender/country estimates.** In a market this competitive, if anyone could credibly claim "our age estimates are 90% accurate," they would. **The silence is the finding.**

### The proxy evidence that the underlying audience is often fake anyway

Points North Group (independent ad-fraud analytics) studied real 2018 Instagram campaigns and found **72% of Ritz-Carlton's influencer-program reach was fake**; Aquaphor ~50% of budget wasted; L'Occitane 39%; Pampers 32%; Olay 19%. **Micro-influencers (50K–100K) averaged ~20% fake followers.** Estimated $102M of $744M in 2018 influencer spend wasted on fake followers ([Ad Age](https://adage.com/article/digital/study-influencer-spenders-finds-big-names-fake-followers/313223/); [MediaPost](https://www.mediapost.com/publications/article/318104/is-influencer-marketing-complicit-in-fraud-of-up-t.html)).

**Why this compounds:** if 20–70% of a creator's followers are bots, then the demographic breakdown computed *on top of* that follower base is profiling a population that partly doesn't exist. The errors multiply. *(Caveat: this study uses 2018 data and measures authenticity, not demographic accuracy — treat as directional.)*

Secondary/self-reported figures, **UNVERIFIED**: HypeAuditor's own 2024 claim that ~22% of Instagram accounts show inauthentic activity; a 2025 Influencer Marketing Hub survey claiming 63% of brands experienced influencer fraud in the prior 18 months ([both via InfluencerDB](https://influencerdb.net/blog/influencer-marketing-metric-audience-quality/)).

### Error bars to actually assume

Since no published error rates exist, these are **reasoned engineering assumptions, not sourced figures** — flagged as such:

| Data type | Source | Trust | Working assumption |
|---|---|---|---|
| Creator's own screenshot of Insights | Platform first-party | **High** | Ground truth (mod. Meta's own "viewers we have data for" caveat) |
| TikTok One / Brand Collabs / opted-in CreatorIQ | Platform first-party | **High** | Ground truth for connected creators |
| Gender split, est. from 100k+ follower account | Sampled + ML | **Medium** | ±10–15pp — binary classification on photos is the easiest task here |
| Country/top-market, est. | Sampled + ML | **Medium-low** | Directionally right for the #1 market; long tail unreliable |
| Age bands, est. | Sampled + ML | **Low** | ±1 full age band. Photo-based age estimation is genuinely hard |
| Interests, est. | Inferred from bios/captions | **Low** | Treat as tags, not percentages |
| **Anything on a <10k-follower creator** | Sampled + ML | **Very low** | Vendor-admitted "thinner." **Do not make spend decisions on this** |
| Any creator with a private follower list | — | **None** | No data exists |

**The rule this implies:** *use estimated demographics to **rank and shortlist**, never to **justify spend**.* A tool saying "68% female, 18–24, US" is a hypothesis worth 10 minutes of manual verification — not a fact worth $500.

---

## 4. Scraping — the legal and practical reality

### 4a. hiQ v. LinkedIn — the full timeline, corrected

The famous version of this story is wrong, and getting it wrong leads teams to bad decisions.

1. **2017** — LinkedIn sends hiQ a cease-and-desist; hiQ sues for declaratory relief.
2. **Aug 14, 2017** — N.D. Cal. grants hiQ a preliminary injunction (273 F. Supp. 3d 1099).
3. **Sept 9, 2019** — **Ninth Circuit affirms**: **hiQ Labs, Inc. v. LinkedIn Corp., 938 F.3d 985 (9th Cir. 2019)**. Where a network generally permits public access, accessing public data isn't "without authorization" under the CFAA. *This is the ruling everyone cites.* ([9th Cir. opinion PDF](https://cdn.ca9.uscourts.gov/datastore/opinions/2019/09/09/17-16783.pdf))
4. **June 14, 2021** — SCOTUS **GVRs** (141 S. Ct. 2752) in light of *Van Buren*.
5. **Apr 18, 2022** — **Ninth Circuit reaffirms** on remand: **31 F.4th 1180 (9th Cir. 2022)**. Public scraping still doesn't violate CFAA even post-*Van Buren*. ([opinion PDF](https://cdn.ca9.uscourts.gov/datastore/opinions/2022/04/18/17-16783.pdf))
6. **Aug 2022** — Injunction **dissolved** — hiQ no longer had an ongoing business. AWS had suspended its account ~April 2020; hiQ's CEO told its litigation funder in March 2020 it was **$895,000 in debt** ([Staffing Industry Analysts](https://www.staffingindustry.com/news/global-daily-news/linkedin-ends-legal-battle-hiq-labs-data-scraping-case)).
7. **⚠️ Nov 4, 2022 — the step everyone misses.** On the *merits* (not the CFAA injunction question), Judge Edward Chen (N.D. Cal., No. 3:17-cv-03301-EMC) grants summary judgment finding **hiQ breached LinkedIn's User Agreement** — via its own scraping *and* via crowdsourced "Turker" workers who created **fake LinkedIn profiles**. Mixed ruling, but LinkedIn won the core contract theory. ([Proskauer](https://newmedialaw.proskauer.com/2022/11/11/court-finds-hiq-breached-linkedins-terms-prohibiting-scraping-but-in-mixed-ruling-declines-to-grant-summary-judgment-to-either-party-as-to-certain-key-issues/); [National Law Review](https://natlawreview.com/article/court-finds-hiq-breached-linkedin-s-terms-prohibiting-scraping-mixed-ruling-declines))
8. **Dec 6–7, 2022 — final resolution.** Stipulated consent judgment: **hiQ found liable**, pays LinkedIn **$500,000**, permanently ceases all LinkedIn scraping, deletes all LinkedIn-derived code/data/algorithms. hiQ stipulates to liability for breach of contract, CFAA (as to the *fake-account* access specifically), California computer-crime law, trespass to chattels, and misappropriation — **plus discovery sanctions for spoliation.** ([Privacy World](https://www.privacyworld.blog/2022/12/linkedins-data-scraping-battle-with-hiq-labs-ends-with-proposed-judgment/); [Morgan Lewis](https://www.morganlewis.com/blogs/sourcingatmorganlewis/2022/12/linkedin-v-hiq-landmark-data-scraping-suit-provides-guidance-to-data-scrapers-and-web-operators))
9. **hiQ Labs is defunct.**

> **The correct takeaway is not "scraping public data is legal." It is: "scraping public data may not violate the CFAA, but it can still be a breach of contract that destroys your company." hiQ won the CFAA question decisively — twice, at the circuit level — and still went out of business.**

### 4b. Where CFAA law landed (2021–2026)

**Van Buren v. United States, 593 U.S. 374 (2021)** (June 3, 2021, 6-3, Barrett, J.): the CFAA's "exceeds authorized access" clause reaches only someone who accesses a computer with authorization but obtains information in areas **off-limits to them** — not someone who misuses information they were entitled to access. Resolved a circuit split in favor of the narrow reading ([SCOTUS opinion PDF](https://www.supremecourt.gov/opinions/20pdf/19-783_k53l.pdf); [CRS summary](https://www.congress.gov/crs-product/LSB10616)).

Net: **the CFAA is now a weak tool against scraping of public-facing sites.** Platforms rely on contract (ToS), trespass to chattels, copyright, and state computer-crime statutes instead ([Jackson Lewis](https://www.jacksonlewis.com/insights/supreme-court-adopts-narrow-interpretation-computer-fraud-and-abuse-act)).

### 4c. The 2024 rulings that cut *against* the platforms

**Meta Platforms, Inc. v. Bright Data Ltd.** (N.D. Cal.) — **Judge Edward Chen** (the same judge who ruled against hiQ) granted **summary judgment for Bright Data on Jan 23, 2024**, holding Meta's ToS bind only users who are **logged in** — **scraping public data while logged out is not a ToS breach**, because Meta's terms don't reach non-account-holders viewing public pages. Meta then voluntarily dismissed its remaining claim (Feb 23, 2024) and **waived its right to appeal** ([TechCrunch](https://techcrunch.com/2024/01/24/court-rules-in-favor-of-a-web-scraper-bright-data-which-meta-had-used-and-then-sued/); [Farella Braun + Martel](https://www.fbm.com/publications/major-decision-affects-law-of-scraping-and-online-data-collection-meta-platforms-v-bright-data/); [Eric Goldman's blog](https://blog.ericgoldman.org/archives/2024/01/game-on-bright-data-scores-major-victory-in-web-scraping-dispute-with-meta-guest-blog-post.htm)).

**X Corp. v. Bright Data Ltd.** (N.D. Cal., May 10, 2024) — court dismissed X's claims, holding **copyright law preempts** contract/trespass/tortious-interference claims aimed at stopping scraping of public data, since X doesn't own the underlying content (its users do) and can't use ToS to manufacture a copyright-like monopoly over public data ([MoFo](https://www.mofo.com/resources/insights/240604-california-federal-court-holds-x-s-claims); [Skadden](https://www.skadden.com/insights/publications/2024/05/district-court-adopts-broad-view)).

**Both are district-court rulings** (not binding precedent outside N.D. Cal.) and both involved a well-resourced defendant (Bright Data) able to fight for years. But together they meaningfully **strengthen** the scraper's legal position for logged-out public scraping — a real correction to the "hiQ lost, therefore scraping is illegal" narrative.

### 4d. Platform ToS — uniformly prohibitive regardless of case law

- **TikTok**: prohibits *"scraping, crawling, exporting or otherwise extracting any data or content in any form from the platform using any automated system or software, including automated 'bots,' except as approved in writing by TikTok"* ([TikTok ToS](https://www.tiktok.com/legal/page/us/terms-of-service/en)).
- **Meta/Instagram**: *"You may not access or collect data from our Products using automated means (without our prior permission)."* Meta maintains separate **[Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms)** requiring **express written permission first** — accepting the terms is not itself permission. Enforcement reserved *"at any time, including while we investigate you, with or without notice."* **But** per *Meta v. Bright Data*, this has been judicially held **not to apply to logged-out public access** — a live tension between Meta's stated policy and controlling N.D. Cal. law.
- **YouTube/Google**: prohibits *"accessing the Service using any automated means such as robots, botnets or scrapers,"* except public search engines per robots.txt or with prior written permission ([YouTube ToS](https://www.youtube.com/static?template=terms); [API Developer Policies](https://developers.google.com/youtube/terms/developer-policies)).

### 4e. Detection and enforcement in practice

**Technical** (the layer that will actually stop us): rate limiting per IP/session, CAPTCHAs, device/browser fingerprinting (screen resolution, fonts, TLS fingerprint), ML behavioral bot-detection, IP reputation scoring. TikTok publicly documents using CAPTCHAs, device/network/interaction monitoring, and rate limiting ([TikTok: How We Combat Unauthorized Data Scraping](https://www.tiktok.com/privacy/blog/how-we-combat-scraping/en)).

**Legal** (reserved for commercial-scale operators): Meta's pattern is C&D letter → account bans → litigation. It disabled ~38,000 fake accounts run by Voyager Labs and sued ([Meta: Taking Action Against Scraping for Hire](https://about.fb.com/news/2022/07/actions-against-scraping-for-hire/)); settled with BrandTotal/Unimania in Oct 2022 with a **permanent injunction** plus payment. LinkedIn remains the most consistently successful litigant (hiQ $500K; Mantheos 2022 settlement forcing data deletion + software destruction — though that was a **fraudulent-access** case involving fake accounts and stolen payment methods, not pure public scraping ([Bloomberg Law](https://news.bloomberglaw.com/privacy-and-data-security/linkedin-settles-data-scraping-lawsuit-against-mantheos))).

### Honest verdict

**Scraping is a trap for us — but not primarily for legal reasons.**

- **The legal risk is genuinely lower than the folklore suggests.** Post-*Van Buren* and post-*Bright Data*, logged-out scraping of public pages in the Ninth Circuit has real defenses. A well-funded defendant can win.
- **But every platform's ToS prohibits it**, so we'd be violating platform rules regardless of how a lawsuit would resolve — exposing us to the enforcement toolkit that actually matters at our size: **technical blocking and account bans**. We need our TikTok and Instagram accounts to *run the campaign*. Losing them to a scraping ban is a self-inflicted wound orders of magnitude worse than a $299/mo tool subscription.
- **Litigation is asymmetric and ruinous even when you'd win.** hiQ won the CFAA question twice at the circuit level and went bankrupt from 5+ years of cost and uncertainty before losing on contract. Bright Data beat Meta and X — Bright Data is a data company with a litigation budget. We are a label with one artist.
- **And the economics don't justify it anyway.** Scraping would get us *estimated* demographics — the same low-confidence data (§3) we could buy for $199/mo, or beat entirely by asking a creator for a screenshot. **We'd take on real risk to obtain data we've just established we can't trust.**

**Verdict: no.** Browsing TikTok sound pages manually — which is just *using the product as intended* — gets us the highest-signal data (§6) with zero risk.

---

## 5. Music-specific creator discovery — the highest-signal path

**This is the strategic core of the pillar.** If a creator already posted with an adjacent artist's sound and it performed, that is **measured behavioral evidence** their audience responds to this music — categorically better than an inferred demographic guess. It skips the entire estimation problem.

### 5a. TikTok sound pages

**Publicly browsable, no login required.** TikTok's public music API serves sound pages without authentication ([tokportal](https://www.tokportal.com/post/tiktok-without-account-what-you-can-and-cant-do)). Tapping a sound on any video opens a feed of every video using that audio; the `user_count` field = total videos that have used the sound ([socialcrawl](https://www.socialcrawl.dev/platforms/tiktok/song)). Logged-out guests can't see comment threads.

**No compliant commercial API for "who used this sound":**
- **TikTok Research API** — restricted to verified academic/non-profit institutions, ~4-week approval, 1,000 requests/day, and **explicitly prohibits commercial use**. It does expose a `music_id` query field — which is exactly what we'd want, and exactly what we can't have ([TikTok Research API](https://developers.tiktok.com/doc/research-api-get-started)).
- **TikTok Commercial Music Library** — 879,000–1,000,000+ royalty-free tracks for business accounts. This is a **licensing library, not an analytics tool** ([TikTok CML](https://ads.tiktok.com/help/article/commercial-music-library?lang=en)).
- **TikTok Creative Center** — trending songs/hashtags/creators with region filters and interest-over-time. **Aggregate trend data, not per-sound creator lists.** Free ([Creative Center](https://ads.tiktok.com/business/creativecenter/music/pc/en)).
- ⚠️ Following a **Universal Music Group dispute**, many audio metadata fields (esp. original-artist attribution for UMG catalog) now return empty/limited data ([sociavault](https://sociavault.com/blog/tiktok-music-api)).
- **No official Content Posting API endpoint lets a third party attach an existing trending native sound to a post** ([sociavault](https://sociavault.com/blog/tiktok-music-api)) — relevant if we ever automate posting.

**Unofficial scrapers** (Apify-hosted "TikTok Sound Scraper" etc.) pull per-sound creator handles, follower counts, bios, verified status, plus per-video stats, up to ~500 videos/sound ([apify](https://apify.com/burbn/tiktok-sound-library)). **ToS-violating and UNVERIFIED for reliability — see §4. Not recommended.**

**Practical conclusion: manual sound-page browsing is free, compliant, and gives us exactly the list we need.** At one artist and dozens of creators, this is a person-afternoon, not an engineering project.

### 5b. Music analytics platforms

| Tool | Base price | What it gives us | API |
|---|---|---|---|
| **Soundcharts** | **$10/mo** (1 artist), **$49/mo** (10 artists), **$129/mo** PRO | TikTok video-count-per-song over time, audience demos (age/gender/location/language), geographic reach, spike alerts. All from public metrics — **no credentials needed to track any artist** ([Soundcharts TikTok Analytics](https://soundcharts.com/en/tiktok-analytics)) | Starter **$50/mo** (10k calls) → Enterprise $4,500/mo ([dev pricing](https://developers.soundcharts.com/pricing)) |
| **Chartmetric** | **$40/mo** Manager (10 artists), **$117/mo** Premium, **$150/mo** Ultra | "Artist Sounds on TikTok," "Popular TikTok Sounds," **7-day velocity** on rising sounds, UGC usage frequency ([Chartmetric TikTok Analytics](https://chartmetric.com/use-cases/tiktok-analytics)). Premium adds "Creator channel insight" + "Brand & sponsorship insight" | **From $350/mo** ([pricing](https://chartmetric.com/pricing)) |
| **Songstats** | ~**$12/mo** Artist, ~**$20–27/mo** Label, ~**$99–130/mo** Pro — **all UNVERIFIED** (JS pricing page unfetchable; figures from aggregators and mutually inconsistent) | Cross-platform (Spotify/Apple/TikTok/IG/YouTube/Shazam). TikTok page claims *"uncover top influencers, track engagement metrics, gain detailed insights into audience demographic and locations"* ([songstats.com/platforms/tiktok](https://songstats.com/platforms/tiktok)) | Enterprise/custom quote, **not publicly priced** ([docs](https://docs.songstats.com/)) |

**Key unverified item:** Chartmetric reportedly has a **"Top Influencers Using Artist's Sound"** module explicitly framed as useful *"for assessing the value of influencer campaigns"* — which would be exactly the feature we want. **UNVERIFIED** — Chartmetric's help center pages returned empty to automated fetching. **Confirm this in a trial account before subscribing; if real, it alone justifies Chartmetric over Soundcharts.**

A **"Top Influencers by Country"** feature surfaced in searches attributed to Soundcharts appears to actually belong to a separate tool, **Tokchart** (tokchart.com) — **flagged as likely misattribution, UNVERIFIED.**

### 5c. The play

1. Identify 5–10 **adjacent artists** to Hallow Youth (similar genre/sound/tempo/mood).
2. In Soundcharts/Chartmetric, find which of their tracks have **TikTok sound traction and rising 7-day velocity**.
3. Open those sound pages on TikTok. **Manually list the creators who used them** and whose videos performed.
4. That list *is* our index. These creators have **demonstrated** — not estimated — that their audience engages with this music.
5. Verify demographics for the shortlist by **asking creators for their analytics screenshot** during outreach (they'll share it; it's how rate negotiation works anyway — see §7's note on rate cards).

---

## 6. What it costs to engage creators

### Music/sound-use rates (the relevant numbers)

| Tier | Followers | TikTok song-push rate/video |
|---|---|---|
| **Nano** | 1K–10K | **$20–$150** |
| **Micro** | 10K–100K | **$100–$1,000** |
| Mid-tier | 100K–500K | $500–$4,000 |
| Macro | 500K–1M | $2,000–$12,000 |
| Mega | 1M+ | $8,000–$80,000+ |

Source: [InfluencerFee: Music Influencer Rates](https://influencerfee.com/blog/music-influencer-rates/), fetched July 2026.

> **Key finding: music campaigns are cheaper than generic UGC.** The same source states music-promotion rates run **20–40% lower than equivalent brand product sponsorship rates**, because sound-use content has looser creative requirements — creators use the song however fits their content, unlike rigid brand-messaging sponsorships. **This confirms the thesis: creators want to use trending sounds anyway, so we're paying a discount to a willing party.**

### Generic sponsored rates, for comparison

| Tier | TikTok video | Instagram Reel |
|---|---|---|
| Nano (1K–10K) | $25–$200 (also cited $20–$100, $100–$300) | $50–$300 (also cited $100–$500) |
| Micro (10K–100K) | $200–$1,500 (also cited $300–$2,000) | $300–$800 (also cited $500–$5,000) |

Sources: [Influee](https://influee.co/blog/tiktok-influencer-rates); [Influencer Marketing Hub: Nano Rates](https://influencermarketinghub.com/influencer-rates/nano-influencer-rates/); [Nowadays Media](https://nowadays.media/influencer-marketing/instagram-influencer-rates-2026/). **Ranges vary widely across sources — treat as order-of-magnitude, not precision.** Collabstr 2026 data cites a **$345 average** per sponsored TikTok post across all tiers; Reels run ~32% higher than equivalent-follower TikTok rates (via aggregators citing Collabstr — **UNVERIFIED primary**).

**Spark Ads / usage rights add 20–50% on top of base content rate** ([Influee](https://influee.co/blog/tiktok-influencer-rates)) — budget for this if we want to run creator content as paid media (see Pillar 8).

### Gifting and seeding economics

- Gifting-only acceptance is **3–5x higher with nano creators**; drops to **10–15% acceptance at the micro tier** — gifting stops working past ~10K followers ([Gigapay via aggregator, **UNVERIFIED primary**](https://www.gigapay.com/blog/tiktok-influencer-payments-report)).
- Volume seeding: seed 100+ nano creators, expect 30–50 to post; **cost per content piece $5–$20** (COGS only). **UNVERIFIED.**
- Hybrid: **$100–$500 flat fee + product** once gifting-only conversion drops.

### Real label campaign structures

- **Typical phased campaign:** Phase 1 (Seed) — 100–500 nano creators at gifting or $20–$100 each. Phase 2 (Amplify) — 20–50 micro creators at $100–$1,000 each to build the trend template. Mid-size label single-launch total: **$15,000–$80,000** ([InfluencerFee](https://influencerfee.com/blog/music-influencer-rates/)).
- **RCA Records** works with an influencer agency to identify **10–30 lower-follower-count creators per single**, lets them post varied creative concepts, then doubles down on whichever trend works. Those 10–30 creators together cost **$8,000–$50,000** ([Billboard](https://www.billboard.com/pro/song-viral-tiktok-organically-or-paid-for/) — **original publication date unclear, treat as directional**).
- Two digital marketing agencies cited **$5,000** as the low end for an effective push; **$80,000**+ for major artists ([Billboard, same](https://www.billboard.com/pro/song-viral-tiktok-organically-or-paid-for/)).
- **Obsolete benchmark:** 2020 pandemic-era, individual creators commanded $10,000+/video, one reportedly **"$50,000 just to play the sound."** Big single-creator buys have declined in effectiveness since 2021–22 in favor of spreading budget across many smaller creators for a grassroots look ([Billboard, same](https://www.billboard.com/pro/song-viral-tiktok-organically-or-paid-for/)).
- Intermediaries: **Sound.Me** (automation platform connecting creators with artists/labels for paid sound placement); TikTok's in-app **"Work With Artists"** feature (creators with 50,000+ followers) ([Billboard, same](https://www.billboard.com/pro/song-viral-tiktok-organically-or-paid-for/)).

**Realistic Respect the Funk budget:** RCA's model — 10–30 nano/micro creators, varied concepts, double down on what works — is directly copyable at our scale. At nano/micro music rates, **30 creators × $50–200 = $1,500–6,000 per single.** That's the right order of magnitude, and it dwarfs any tool subscription — which is precisely why the tooling recommendation is "spend $50/mo, not $500/mo, and put the difference into creator payments."

### ⚠️ Disclosure — an active regulatory risk as of *this month*

An **NPR investigation published July 13, 2026** — two days before this research — found five music influencers who **routinely accept undisclosed payments** to post about songs. The documented workflow: label/agency contacts creator or manager → requests a **rate card** (TikTok post / IG feed / IG Stories pricing) → creator submits a draft video for label approval → payment after posting ([NPR](https://www.npr.org/2026/07/13/nx-s1-5849926/influencers-paid-music-promotion)).

FTC guidance requires disclosure of any *"financial, employment, personal, or family relationship with a brand"* being promoted. Asked whether undisclosed song promos violate this, the **FTC said it evaluates "case-by-case" and declined to comment on specifics**. NPR contacted Interscope, Republic, Atlantic, RCA, Arista, Epic, Columbia, Sub Pop, and Sony/UMG/WMG — **none responded by publication** ([NPR](https://www.npr.org/2026/07/13/nx-s1-5849926/influencers-paid-music-promotion)).

> **Action item: build FTC-compliant disclosure (#ad / #sponsored / Paid Partnership tag) into every creator contract and brief from day one, and store `disclosure_status` as a first-class field in the index (§7).** This is a live press-scrutiny area *right now*, the industry norm is non-compliance, and an indie label is a cheaper target than a major. It is also, usefully, free to get right.
>
> *(Useful side effect: the rate-card workflow NPR documents is the same conversation where we ask for the analytics screenshot — §3's ground-truth path. Compliance and data quality point the same direction.)*

---

## 7. The practical index — proposed schema

Design principles:
1. **Every demographic field carries its provenance and confidence.** A field without `source` is unusable.
2. **Measured beats estimated, always.** Structure makes it impossible to confuse them.
3. **Sound usage is a first-class entity** — it's our highest-signal data (§5).
4. **Disclosure status is not optional** (§6).

```sql
-- ============ CREATOR ============
creator
  id                    uuid PK
  handle                text
  platform              enum(tiktok, instagram, youtube)
  platform_user_id      text          -- stable ID; handles change
  display_name          text
  profile_url           text
  bio                   text
  country_self_reported text          -- from bio; MEASURED-ish (self-declared)
  follower_count        int           -- MEASURED (public)
  follower_count_at     timestamptz   -- decays fast; always timestamp it
  follower_tier         enum(nano, micro, mid, macro, mega)  -- derived
  is_verified           bool
  first_seen_at         timestamptz
  last_refreshed_at     timestamptz

-- ============ AUDIENCE DEMOGRAPHICS — provenance-tagged ============
-- One row per (creator, dimension, bucket, source). NEVER overwrite an
-- estimate with another estimate; append and let source rank decide.
audience_demographic
  id                uuid PK
  creator_id        uuid FK
  dimension         enum(age, gender, country, city, language, interest)
  bucket            text          -- '18-24' | 'female' | 'US' | 'indie-music'
  share             numeric(5,4)  -- 0.0-1.0

  -- THE PROVENANCE BLOCK — the whole point of this table
  source            enum(
                      'creator_screenshot',    -- ground truth, creator's own Insights
                      'creator_oauth',         -- ground truth, via our app w/ their token
                      'platform_marketplace',  -- ground truth (TikTok One / Brand Collabs)
                      'vendor_estimate',       -- Modash/HypeAuditor/etc — ESTIMATED
                      'our_inference'          -- our own guess — ESTIMATED
                    )
  is_measured       bool          -- GENERATED: source IN (first three)
  vendor            text          -- 'modash' | 'hypeauditor' | null
  confidence        enum(high, medium, low, very_low)
  error_bar_pp      numeric       -- assumed ± percentage points (§3 table)
  sample_size       int           -- if vendor discloses (they mostly don't)
  collected_at      timestamptz   -- >3mo old = stale, re-verify
  notes             text          -- 'follower list private, no data'

-- ============ SOUND USAGE — our highest-signal table ============
sound_usage
  id                uuid PK
  creator_id        uuid FK
  sound_id          text          -- TikTok music_id
  sound_name        text
  artist_name       text
  is_adjacent_artist bool         -- one of our 5-10 reference artists?
  video_url         text
  posted_at         timestamptz
  views             int           -- MEASURED
  likes             int           -- MEASURED
  comments          int           -- MEASURED
  shares            int           -- MEASURED
  engagement_rate   numeric       -- derived, MEASURED
  performed_well    bool          -- vs. this creator's own median
  discovered_via    enum(sound_page_manual, chartmetric, soundcharts, referral)

-- ============ ENGAGEMENT BASELINE ============
engagement_metric
  creator_id        uuid FK
  measured_at       timestamptz
  median_views      int           -- MEASURED — trust over follower_count
  median_engagement_rate numeric  -- MEASURED
  post_frequency_30d int
  view_to_follower_ratio numeric  -- best cheap authenticity signal we control
  vendor_credibility_score numeric -- ESTIMATED (Modash 0-1 / HypeAuditor AQS)
  vendor_fake_follower_pct numeric -- ESTIMATED — unreliable <10k followers (§3)

-- ============ OUTREACH / DEALS ============
contact
  creator_id        uuid FK
  email             text
  manager_email     text
  preferred_channel enum(email, dm, manager)
  rate_card_on_file bool          -- NPR: this is the standard ask (§6)

deal
  id                uuid PK
  creator_id        uuid FK
  campaign_id       uuid FK
  quoted_rate_usd   numeric
  agreed_rate_usd   numeric
  deliverable       enum(tiktok_video, ig_reel, ig_story, yt_short)
  usage_rights      enum(none, spark_ads, whitelisting)  -- +20-50% cost (§6)
  usage_rights_expires_at date
  status            enum(prospect, contacted, negotiating, agreed, posted, paid, declined)

  -- COMPLIANCE — not optional (§6, NPR 2026-07-13)
  disclosure_required bool DEFAULT true
  disclosure_method   enum(paid_partnership_tag, ad_hashtag, verbal, none)
  disclosure_verified bool         -- did we actually CHECK the live post?
  disclosure_checked_at timestamptz

-- ============ PERFORMANCE (closes the loop) ============
campaign_result
  deal_id           uuid FK
  video_url         text
  views_24h         int
  views_7d          int
  views_30d         int
  saves             int
  sound_uses_driven int           -- did others use OUR sound after this post?
  streams_attributed int          -- best-effort, join to Pillar 4/9
  cost_per_1k_views numeric       -- derived — the real efficiency metric
  would_rehire      bool          -- the single most valuable field over time
```

### Notes on the schema

- **`audience_demographic.source` is the most important column in the database.** Never render an estimated share next to a measured one without visual distinction. If we forget this, we will make $5,000 decisions on ±1-age-band guesses.
- **`is_measured` should drive the UI.** Estimated = grey/italic with an error bar. Measured = solid. This is a *design* requirement, not just a data one.
- **`view_to_follower_ratio` is our best free authenticity signal.** It needs no vendor. A creator with 50k followers and 800 median views has a problem no demographic breakdown will reveal.
- **`sound_usage.performed_well` is the shortlisting key** — not demographics. Rank by "used an adjacent artist's sound and beat their own median," then verify demographics only for that shortlist.
- **`would_rehire` compounds.** After 2–3 campaigns, our own first-party performance data on 30 creators is worth more than any vendor's 380M-row estimated index. **That is the actual long-term asset here** — the same insight behind Grin's "Gia" model trained on $1B of verified transactions ([Grin](https://grin.co/product/influencer-discovery-platform/)), just at our scale.
- **Timestamp everything.** Follower counts and demographics both decay; vendor guidance treats >3 months as unreliable.

### What to *not* build yet

No OAuth app (§1b) until we're onboarding 100+ creators — a screenshot request achieves the same for $0 and no App Review. No vendor API integration until the Modash UI's 300-profile-opens/mo cap actually binds. No scraper, ever (§4).

---

## Open questions / verification debt

1. **Chartmetric's "Top Influencers Using Artist's Sound"** — confirm in a trial account. If real, it's the single highest-value feature in this report and changes the tool recommendation.
2. **Meta Brand Collabs Manager 2026 follower threshold** — the 1,000-follower figure is secondary-sourced; Meta's help pages are JS-rendered and resisted automated fetching. Check in a browser.
3. **TikTok Creator Marketplace API status under TikTok One** — whether the 2021 partner API survives the rebrand, and whether partner access is remotely attainable.
4. **Modash's actual API minimums** — the $10–16.2K/yr figures are aggregator-sourced. Ask sales directly.
5. **Songstats pricing** — page is JS-only; figures inconsistent across aggregators ($99 vs $130/mo for Pro). Check at signup.
6. **Kolsquare's 30% startup discount** — (<3yrs, <€500K raised, <10 employees). Respect the Funk likely qualifies; would bring Discovery to ~€350/mo. Probably still not worth it vs. Soundcharts, but worth knowing.
7. **Grin's $399 Lite tier** — unclear whether it includes full 190M discovery-database access or just CRM.
8. **Heepsy's actual tiers/AQS claims** — heepsy.com returned 403 to all automated fetches; everything in §2 for Heepsy is aggregator-sourced.

---

## Sources

**Platform primary docs**
- [TikTok One Support — eligibility](https://support.tiktok.com/en/business-and-creator/tiktok-one)
- [TikTok — How creators can upgrade to TikTok One](https://ads.tiktok.com/help/article/how-creators-can-upgrade-to-tiktok-one)
- [TikTok Business Blog — TikTok One Creative Platform (May 13, 2026)](https://ads.tiktok.com/business/en/blog/tiktok-one-creative-platform)
- [TikTok Research API — Get Started](https://developers.tiktok.com/doc/research-api-get-started)
- [TikTok Commercial Music Library](https://ads.tiktok.com/help/article/commercial-music-library?lang=en)
- [TikTok Creative Center — Music](https://ads.tiktok.com/business/creativecenter/music/pc/en)
- [TikTok Terms of Service](https://www.tiktok.com/legal/page/us/terms-of-service/en)
- [TikTok — How We Combat Unauthorized Data Scraping](https://www.tiktok.com/privacy/blog/how-we-combat-scraping/en)
- [Meta — Insights, Instagram Platform](https://developers.facebook.com/docs/instagram-platform/insights/)
- [Meta — Instagram User Insights API reference](https://developers.facebook.com/docs/instagram-platform/api-reference/instagram-user/insights/)
- [Meta — Introducing Brand Collabs Manager](https://creators.facebook.com/introducing-brand-collabs-manager/?locale=en_US)
- [Meta Business Help — About Brand Collabs Manager](https://www.facebook.com/business/help/1225872907555801)
- [Meta — Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms)
- [Google — YouTube Analytics API](https://developers.google.com/youtube/analytics)
- [Google — YouTube Reporting API authorization](https://developers.google.com/youtube/reporting/guides/authorization/installed-apps)
- [Google — YouTube Analytics Content Owner Reports](https://developers.google.com/youtube/analytics/content_owner_reports)
- [YouTube Blog — Introducing YouTube Creator Partnerships, NewFronts 2026](https://blog.youtube/news-and-events/youtube-creator-partnerships-newfronts-2026/)
- [YouTube Help — Get started with Creator Partnerships](https://support.google.com/youtube/answer/9385307?hl=en)
- [YouTube Terms of Service](https://www.youtube.com/static?template=terms)
- [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies)

**Vendor pricing & methodology**
- [Modash — pricing](https://www.modash.io/pricing) · [influencer database](https://www.modash.io/influencer-database) · [Our Data (methodology)](https://www.modash.io/data) · [Help: audience demographics](https://help.modash.io/en/articles/13715083-understanding-audience-demographics-and-insights) · [Help: fake followers 101](https://help.modash.io/en/articles/5649607-fake-followers-101-understanding-audience-quality) · [blog: how to check audience demographics](https://www.modash.io/blog/how-to-check-influencer-audience-demographics)
- [HypeAuditor — pricing](https://hypeauditor.com/pricing/) · [discovery](https://hypeauditor.com/discovery/) · [How we collect & analyze data (methodology)](https://hypeauditor.com/collect-analyze-influencer-data/) · [how we rank influencers](https://hypeauditor.com/how-calculate-influencer-rankings/) · [API docs](https://hypeauditor.readme.io/reference/basic)
- [CreatorIQ — creator search](https://www.creatoriq.com/influencer-marketing-solution/creator-search) · [TikTok first-party audience insights](https://www.creatoriq.com/blog/tiktok-first-party-audience-insights) · [named official TikTok Marketing Partner](https://www.creatoriq.com/blog/creatoriq-named-official-tiktok-marketing-partner) · [YouTube partnership press release (Mar 26, 2026)](https://www.creatoriq.com/press/releases/creatoriq-deepens-partnership-with-youtube-with-integration-unlocking-audience-insights-for-smarter-creator-campaigns) · [API docs](https://apidocs.creatoriq.com/docs/ciq-api-documentation/o5yqwvpp1lbnb-overview)
- [Kolsquare — pricing](https://www.kolsquare.com/en/products/pricing) · [FAQ (methodology)](https://www.kolsquare.com/en/frequently-asked-questions) · [Instagram Connection](https://www.kolsquare.com/en/blog/instagram-connection-make-every-influencer-campaign-data-driven) · [API article](https://kolsquare.zendesk.com/hc/en-gb/articles/22158411985564--KOLSQUARE-API-Connect-your-own-tools-to-our-API-to-amplify-your-strategies-and-improve-your-data-work)
- [Upfluence — influencer search](https://www.upfluence.com/influencer-search) · [fake follower check disclaimer](https://www.upfluence.com/instagram-fake-follower-check) · [API](https://www.upfluence.com/influencer-marketing-api) · [Influencer Marketing Hub review](https://influencermarketinghub.com/upfluence/) · [Creator Hero pricing analysis](https://www.creator-hero.com/blog/upfluence-pricing-and-review)
- [Heepsy — influencer database](https://www.heepsy.com/influencer-database) · [TikTok fake follower checker](https://www.heepsy.com/free-tools/tiktok-fake-follower-check) · [Capterra pricing](https://www.capterra.com/p/202552/Heepsy/pricing/) · [Hyred comparison](https://hyred.com/heepsy-comparison)
- [Grin — pricing](https://grin.co/pricing/) · [influencer discovery](https://grin.co/product/influencer-discovery-platform/) · [fake influencer tool (methodology)](https://grin.co/influencer-marketing-tools/fake-influencer-tool/) · [API docs](https://help.grin.co/docs/integrating-with-the-grin-api) · [Vendr procurement data](https://www.vendr.com/marketplace/grin)
- [Aspire — creator marketplace](https://www.aspire.io/platform/creator-marketplace) · [for influencers](https://www.aspire.io/influencers) · [Audience Authenticity methodology](https://help.aspireiq.com/en/articles/6027302-what-is-audience-authenticity) · [Capterra pricing estimate](https://www.capterra.com/p/187445/AspireIQ/)
- [Modash Creator Hero review](https://www.creator-hero.com/blog/modash-pricing-and-review) · [CreatorIQ pricing analysis](https://www.creator-hero.com/blog/creatoriq-pricing-and-review) · [Kolsquare pricing analysis](https://www.influencer-hero.com/blogs/kolsquare-pricing)

**Music analytics**
- [Chartmetric — pricing](https://chartmetric.com/pricing) · [TikTok analytics use case](https://chartmetric.com/use-cases/tiktok-analytics)
- [Soundcharts — pricing](https://soundcharts.com/en/pricing) · [TikTok analytics](https://soundcharts.com/en/tiktok-analytics) · [developer API pricing](https://developers.soundcharts.com/pricing)
- [Songstats](https://songstats.com/) · [TikTok platform page](https://songstats.com/platforms/tiktok) · [API docs](https://docs.songstats.com/)
- [SociaVault — TikTok Music API analysis](https://sociavault.com/blog/tiktok-music-api) · [SocialCrawl — TikTok song data](https://www.socialcrawl.dev/platforms/tiktok/song) · [TokPortal — TikTok without an account](https://www.tokportal.com/post/tiktok-without-account-what-you-can-and-cant-do) · [Apify TikTok Sound Library scraper](https://apify.com/burbn/tiktok-sound-library)

**Rates & campaign economics**
- [InfluencerFee — Music Influencer Rates](https://influencerfee.com/blog/music-influencer-rates/)
- [Billboard — Does a song go viral on TikTok organically or is it paid for?](https://www.billboard.com/pro/song-viral-tiktok-organically-or-paid-for/)
- [Influee — TikTok influencer rates](https://influee.co/blog/tiktok-influencer-rates)
- [Influencer Marketing Hub — Nano influencer rates](https://influencermarketinghub.com/influencer-rates/nano-influencer-rates/)
- [Nowadays Media — Instagram influencer rates 2026](https://nowadays.media/influencer-marketing/instagram-influencer-rates-2026/)
- [NPR — Influencers paid for music promotion (July 13, 2026)](https://www.npr.org/2026/07/13/nx-s1-5849926/influencers-paid-music-promotion)

**Accuracy & fraud research**
- [Ad Age — Study of influencer spenders finds big names, fake followers (Points North Group)](https://adage.com/article/digital/study-influencer-spenders-finds-big-names-fake-followers/313223/)
- [MediaPost — Is Influencer Marketing Complicit In Fraud Of Up To 72%?](https://www.mediapost.com/publications/article/318104/is-influencer-marketing-complicit-in-fraud-of-up-t.html)
- [Cresci et al., "Fame for sale: Efficient detection of fake Twitter followers," arXiv:1509.04098](https://arxiv.org/abs/1509.04098)
- [Phyllo — Instagram Audience Demographics API for influencer platforms](https://www.getphyllo.com/post/instagram-audience-demographics-for-influencer-marketing-platforms)

**Legal — scraping**
- [hiQ v. LinkedIn, 938 F.3d 985 (9th Cir. 2019) — opinion PDF](https://cdn.ca9.uscourts.gov/datastore/opinions/2019/09/09/17-16783.pdf)
- [hiQ v. LinkedIn, 31 F.4th 1180 (9th Cir. 2022) — opinion PDF](https://cdn.ca9.uscourts.gov/datastore/opinions/2022/04/18/17-16783.pdf)
- [Proskauer — Court Finds hiQ Breached LinkedIn's Terms (Nov 2022)](https://newmedialaw.proskauer.com/2022/11/11/court-finds-hiq-breached-linkedins-terms-prohibiting-scraping-but-in-mixed-ruling-declines-to-grant-summary-judgment-to-either-party-as-to-certain-key-issues/)
- [Privacy World — hiQ/LinkedIn ends with proposed judgment (Dec 2022)](https://www.privacyworld.blog/2022/12/linkedins-data-scraping-battle-with-hiq-labs-ends-with-proposed-judgment/)
- [Morgan Lewis — LinkedIn v. hiQ landmark suit guidance](https://www.morganlewis.com/blogs/sourcingatmorganlewis/2022/12/linkedin-v-hiq-landmark-data-scraping-suit-provides-guidance-to-data-scrapers-and-web-operators)
- [Staffing Industry Analysts — LinkedIn ends legal battle with hiQ Labs](https://www.staffingindustry.com/news/global-daily-news/linkedin-ends-legal-battle-hiq-labs-data-scraping-case)
- [Van Buren v. United States, 593 U.S. 374 (2021) — opinion PDF](https://www.supremecourt.gov/opinions/20pdf/19-783_k53l.pdf)
- [Congressional Research Service — Van Buren summary](https://www.congress.gov/crs-product/LSB10616)
- [Jackson Lewis — SCOTUS adopts narrow CFAA interpretation](https://www.jacksonlewis.com/insights/supreme-court-adopts-narrow-interpretation-computer-fraud-and-abuse-act)
- [TechCrunch — Court rules in favor of Bright Data against Meta (Jan 2024)](https://techcrunch.com/2024/01/24/court-rules-in-favor-of-a-web-scraper-bright-data-which-meta-had-used-and-then-sued/)
- [TechCrunch — Meta drops lawsuit against Bright Data (Feb 2024)](https://techcrunch.com/2024/02/26/meta-drops-lawsuit-against-web-scraping-firm-bright-data-that-sold-millions-of-instagram-records/)
- [Farella Braun + Martel — Meta Platforms v. Bright Data analysis](https://www.fbm.com/publications/major-decision-affects-law-of-scraping-and-online-data-collection-meta-platforms-v-bright-data/)
- [Eric Goldman — Bright Data scores major victory over Meta](https://blog.ericgoldman.org/archives/2024/01/game-on-bright-data-scores-major-victory-in-web-scraping-dispute-with-meta-guest-blog-post.htm)
- [Morrison Foerster — X Corp. v. Bright Data ruling (May 2024)](https://www.mofo.com/resources/insights/240604-california-federal-court-holds-x-s-claims)
- [Skadden — District court adopts broad view (X v. Bright Data)](https://www.skadden.com/insights/publications/2024/05/district-court-adopts-broad-view)
- [Meta — Taking Action Against Scraping for Hire (July 2022)](https://about.fb.com/news/2022/07/actions-against-scraping-for-hire/)
- [Bloomberg Law — LinkedIn settles data scraping suit against Mantheos](https://news.bloomberglaw.com/privacy-and-data-security/linkedin-settles-data-scraping-lawsuit-against-mantheos)
