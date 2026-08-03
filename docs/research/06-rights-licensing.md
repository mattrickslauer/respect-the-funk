# Pillar 6: Rights, Licensing & Content ID
### Can we build a "movie cut-scene generator" (indexed film footage + dialogue, auto-cut to a song, posted commercially to market "Losing Sleep" by Hallow Youth)?

*Research compiled July 2026. Not legal advice — see disclaimer at bottom.*

---

## Bottom line

**The organic "edit"/fancam format is real and dominant, but it is not legal — it survives on selective non-enforcement, not on a fair-use defense that would hold up in court.** Posting one clip-cut-to-a-song video to a personal account is extremely low-risk in practice (millions do it, enforcement is patchy and mostly limited to mute/block, not strikes). But the product as specced — an *automated pipeline* that indexes whole films and mass-produces branded promotional cut-scenes, which are then **run as paid ads** to sell a specific commercial product (a song) — is a different animal, and it fails on essentially every axis that matters:

1. **Fair use is a weak-to-losing defense.** Commercial, non-transformative, low-alteration reuse of substantial, recognizable film/dialogue content to promote an unrelated commercial product cuts against you on 3 of 4 factors, and post-*Warhol v. Goldsmith* (2023) the "it feels artistically different" argument that used to carry these cases is significantly narrowed when the use is commercial. See [Q1](#1-the-core-legal-question-fair-use-walked-through) and [Q2](#2-the-commercial-use-aggravator--warhol-v-goldsmith-2023).
2. **The ads layer is the actual kill switch, not the copyright law.** You don't even get to litigate fair use — **TikTok's ad policy explicitly and literally prohibits "use of clips from any unauthorized media sources to promote your product or service,"** and Meta's ad policy bans any ad containing content that infringes third-party IP, reviewed by ~15,000 human reviewers plus automated hash-matching. This isn't a gray-area risk you're weighing — it's a written rule that describes this exact use case and forbids it. See [Q4](#4-the-ads-layer--this-is-the-decisive-finding).
3. **Organic posting is not "safe," it's "not yet caught," and it does not scale safely.** Content ID / Rights Manager / TikTok's matching systems already fingerprint film video and dialogue, not just music. An automated pipeline producing many clips increases the surface area for automated detection (mute, block, strike) far beyond what one person editing fancams by hand generates. And copyright strikes and Page/account-level violations **can cascade into Business Manager and ad-account restrictions** — so even the "organic-only, no ads" fallback carries risk to your paid infrastructure if the same brand identity runs both. See [Q3](#3-content-id-and-platform-enforcement--the-practical-layer).
4. **There is no realistic licensing path at this budget/scale.** Studios don't clip-license for indie music marketing; when they do, per-second rates and minimums (roughly $500–$3,000+ per clip/project) make an automated multi-clip-per-song pipeline economically absurd. See [Q5](#5-licensing-the-legitimate-way).
5. **There is a real, legal path to the same aesthetic** — via public-domain film (1930 and earlier, now fully clear as of Jan 1, 2026), licensed stock/archival footage, and AI-generated cinematic video — that gets you most of the way to the "edit" look without the legal exposure. See [Q6](#6-the-public-domain--legal-substitute-path) and the [recommendation](#recommendation-a-path-forward).

**Verdict: build the indexing/auto-cut engine — it's genuinely good technology — but do not point it at copyrighted studio film. Point it at public-domain film, licensed stock footage, and/or AI-generated footage. That version is legally sound, ad-platform-compliant, and still delivers the cinematic "edit" aesthetic.**

---

## Disclaimer

This document is research, not legal advice. It was compiled from public sources (statutes, case law, platform policy pages, and law-firm commentary) using AI-assisted web research and reflects information available as of July 2026. Copyright and platform-policy details change frequently and fair-use outcomes are famously fact-specific and unpredictable. **Before launching any version of this product — especially before spending ad dollars — have an actual entertainment/IP attorney review the specific creative approach, footage sources, and licensing chain.**

---

## 1. The core legal question: fair use, walked through

Fair use in the US is governed by 17 U.S.C. § 107 and evaluated on four factors ([U.S. Copyright Office Fair Use Index](https://www.copyright.gov/fair-use/)). Applying them honestly to "auto-generated movie clip, cut to a commercial song, dialogue retained, posted to market that song":

### Factor 1 — Purpose and character of the use (commercial vs. transformative)
This is the most damaging factor for this use case. The purpose of the clip in the source film is dramatic/entertainment value; the purpose in our video is... also entertainment/emotional value, repackaged to soundtrack a different song, for a directly commercial purpose (streams/revenue for "Losing Sleep"). Courts ask whether the new use has a "further purpose or different character" ([Copyright Office summary of *Campbell v. Acuff-Rose*](https://www.copyright.gov/fair-use/)) — not merely a new context. A cut-scene edit largely repurposes the *same* aesthetic and emotional payload the film clip already had; it doesn't comment on, critique, or recontextualize the film. Compare this to **[SOFA Entertainment v. Dodger Productions](https://www.copyright.gov/fair-use/summaries/sofa-dodger-9thcir2013.pdf)** (9th Cir. 2013), where a 7-second Ed Sullivan clip in the musical *Jersey Boys* was fair use specifically because it served as a "biographical anchor" — a *different* purpose (historical documentation of a real event in the Four Seasons' career) than the original broadcast's purpose (entertainment). A cut-scene edit generally has no such distinguishing purpose — it's raw entertainment content moved to a new commercial context, which is what **[Elvis Presley Enterprises v. Passport Video](https://copyrightalliance.org/movie-copyright-cases-part-2/)** found *against* fair use (commercial documentary reusing 30-60 second Elvis performance clips with no added commentary).

### Factor 2 — Nature of the copyrighted work
Feature films are highly creative, expressive works (as opposed to factual works), which weighs against fair use — creative works get more protection.

### Factor 3 — Amount and substantiality
"The scene," "the dialogue" — i.e., using the most recognizable, emotionally load-bearing portion of the film — is exactly the kind of "heart of the work" usage courts penalize, even when the absolute clip length is short. Short duration alone does not save you ([this is a listed misconception in fair-use guidance](https://firemark.com/2025/10/14/fair-use-explained-for-podcasters-youtubers-and-filmmakers/)) — SOFA won partly because the clip was incidental to a larger biographical narrative, not because it was short.

### Factor 4 — Effect on the market
This factor increasingly dominates post-Warhol (see below). Does the cut-scene edit substitute for or harm the market for the film, or for the film's own promotional/licensing market (studios sell branded clip licenses, e.g. to trailers, ads, and — critically — to *other artists making exactly this kind of promotional content*)? Using film content to promote a *different* commercial product (a song) plausibly competes directly with the market where studios would otherwise license "vibe"/promotional clips to marketers. This is analogous to the market-substitution logic in **[Bill Graham Archives v. Dorling Kindersley](https://www.copyright.gov/fair-use/)** and was central to the Court's reasoning in Warhol.

### The crux: legality vs. non-enforcement
**These are different things and the difference matters enormously for a business.** The "everyone does edits and nothing happens" reality reflects:
- **Selective, complaint-driven enforcement.** Studios and their agents (via YouTube Content ID / Meta Rights Manager) generally choose to **monetize or track** fan edits rather than block them, because fan edits drive engagement/promotion of their catalog for free — this is a business choice, not a legal concession. [YouTube Content ID lets a rights holder choose "monetize, track, or block"](https://support.google.com/youtube/answer/2797370?hl=en) per video, and the overwhelmingly common choice for fan content is monetize/track.
- **Enforcement is documented to be inconsistent and non-uniform** — one investigated real case found an Instagram account permanently banned for a clip while an identical, higher-view-count post from a verified account faced zero action ([JustAnswer case writeup](https://www.justanswer.com/intellectual-property-law/vava8-instagram-copyright-strike-bviral-video.html)). That's the signature of discretionary/complaint-driven enforcement, not a legal safe harbor.
- **Nobody has actually litigated a "fancam/edit for commercial marketing" fair-use case to a published opinion** that we could find — the genre thrives in a legal gray zone that has never been tested, precisely because the economics of suing a small account/small label rarely justify the studio's litigation cost. That absence of case law is not the same as a favorable holding.

**Conclusion on Q1: a rigorous four-factor analysis does not support fair use for this specific commercial use case. The "safety" the genre enjoys today is prosecutorial/enforcement discretion by rights holders and platforms, which can change per rights-holder, per platform policy update, or the moment paid spend and scale make you worth going after.**

---

## 2. The commercial-use aggravator — *Warhol v. Goldsmith* (2023)

This is the single most important recent legal development for this question, and it cuts hard against the product idea.

**[Andy Warhol Foundation for the Visual Arts, Inc. v. Goldsmith, 598 U.S. 508 (2023)](https://www.law.cornell.edu/supremecourt/text/21-869)** — the Supreme Court held 7-2 that AWF's licensing of Warhol's "Orange Prince" image to a magazine was *not* fair use, even though the Warhol image had obvious new aesthetic meaning relative to Goldsmith's original photograph. The key holding, per the [Copyright Office's own case summary](https://www.copyright.gov/fair-use/summaries/Andy-Warhol-Found-for-the-Visual-Arts-Inc-v-Goldsmith-143-S-Ct-1258-2023.pdf):

> Adding "new expression" is **not sufficient** to be transformative if the new use serves substantially the **same purpose** as the original and is **commercial**. Both works were portraits of Prince used to illustrate magazine stories about Prince — same purpose — and the commercial licensing counted heavily against fair use.

Multiple law-firm analyses describe this as narrowing transformativeness specifically for commercial, substitutional reuse — see [Akerman: "Does Transformative Matter? No, At Least Where Use Is Commercial"](https://www.akerman.com/en/perspectives/ip-does-transformative-matter-no-at-least-where-use-is-commercial.html) and [Crowell & Moring's analysis](https://www.crowell.com/en/insights/client-alerts/lessemgreaterandy-warhollessemgreater-commercialism-and-the-doctrine-of-fair-use). The Court explicitly weighed **whether the secondary use directly competes with a licensing market the original rights holder could have exploited** — which is precisely the situation here: film studios have a genuine, growing "clip licensing for marketing/promotional" business (see Q5), and an unlicensed cut-scene edit promoting a commercial song **substitutes for, rather than supplements, that market.**

**Applied to this product:**
- Purpose: the film clip's purpose in a cut-scene edit (emotional, cinematic entertainment) is substantially the *same* as its purpose in the film. This is not a parody, critique, or documentary use — the closest a fan edit gets to "transformative" is combining it with different music, and courts have not treated a music swap alone as sufficient transformation of purpose.
- Commercial: unambiguous — this is being built explicitly to drive streams/revenue for a specific commercial release.

**Yes — using film content to market a product materially worsens the analysis versus a fan making an edit "for fun."** A non-commercial fan edit at minimum has a plausible (if still risky) argument on factor 1. A brand/label doing this at scale, systematically, to sell a specific commercial product, loses that argument almost entirely under Warhol's framework. This is not a nuance — it is the central holding of the most relevant recent Supreme Court case on point.

---

## 3. Content ID and platform enforcement — the practical layer

### YouTube Content ID
Rights holders (including major studios) submit **audio and video reference files** into Content ID; every upload is automatically scanned against >100M reference files ([YouTube Help: How Content ID works](https://support.google.com/youtube/answer/2797370?hl=en)). On a match, the rights holder's chosen policy applies automatically: **monetize** (ad revenue redirected to them), **track** (analytics only, video stays up), or **block** (removed/blocked, sometimes only in specific territories) — [official docs](https://support.google.com/youtube/answer/2797370?hl=en). A Content ID **claim is not a strike**; strikes come from formal DMCA takedown notices or from mishandling disputes, and **3 strikes = channel termination** ([Third Chair: YouTube Copyright Rules 2026](https://usethirdchair.com/blog/youtube-copyright-rules-in-strikes-claims-and-appeals)).

### Meta Rights Manager (Instagram/Facebook)
Works the same way for video as YouTube Content ID for audio: rights holders upload reference files, every uploaded video is fingerprinted and checked, and on a match the rights holder can **block, claim ad earnings, monitor, or report as IP violation** ([Meta Transparency Center](https://transparency.meta.com/reports/intellectual-property/protecting-intellectual-property-rights/), [Meta 2023 announcement](https://about.fb.com/news/2023/01/helping-creators-and-publishers-manage-intellectual-property/)). As of 2026 Meta has expanded fingerprinting with AI-assisted detection covering even short/background audio ([Foxi Music 2026 guide](https://www.foximusic.com/blog/instagram-reels-music-copyright-legal-guide/)). Studios are increasingly using Rights Manager for film/TV video, not just labels for music.

### TikTok
TikTok does **not** have a fully open, YouTube-style Content ID marketplace, but it does run **video-level content matching** — waveform/audio fingerprinting, visual/scene fingerprinting, and derivative-work detection ([Red Points DMCA guide](https://www.redpoints.com/blog/tiktok-dmca-takedown/)) — plus a **repeat-infringer policy**: accounts accumulate strikes that expire after 90 days, but TikTok "may exercise discretion to immediately ban any account in cases of severe copyright violations" ([TikTok Intellectual Property Policy](https://www.tiktok.com/legal/page/global/copyright-policy/en)). Its enforcement, per commentary, is real but less systematic/scaled than YouTube's ([Third Chair: "Content ID for TikTok and Instagram: What's Missing Today"](https://usethirdchair.com/blog/content-id-for-tiktok-and-instagram-what-s-missing-today)).

### What actually happens in practice
For a hand-made, personal-account film-clip edit: usually nothing, occasionally a mute/track claim, rarely a takedown, very rarely a strike. This matches the "selective enforcement" pattern above. For an **automated pipeline producing many such videos under one brand identity**, the risk profile changes:
- More uploads → more matches → more claims, faster accumulation toward strike thresholds.
- A single documented real-world example: an Instagram account was **permanently deactivated** under a "pattern violation" repeat-infringer policy after one strike, while a near-identical, more-viewed post from a different (verified) account faced no action — illustrating how *arbitrary and account-specific* enforcement risk is, not something you can model as a stable, low background rate ([case writeup](https://www.justanswer.com/intellectual-property-law/vava8-instagram-copyright-strike-bviral-video.html)).

### Critical: cascading risk to the ad account / Business Manager
This is a genuine and underappreciated risk for a commercial operation running both organic content and ads from the same brand assets:
- **Meta explicitly documents that Page-level policy violations can cascade to associated ad accounts**, and that **a Business Manager-level restriction can cascade to every connected ad account and shared asset** ([Meta Transparency Center: Counting Strikes](https://transparency.meta.com/enforcement/taking-action/counting-strikes/); commentary summarized in [HyperFX's 2026 guide](https://www.hyperfx.ai/blog/meta-ad-account-disabled-causes-2026)). Meta's own framing: *"Depending on the severity and frequency of violations, Meta may: Disapprove your ad, Temporarily restrict your ad account, Disable your business manager, Permanently ban your account."*
- This means **the "we'll just post organically and eat the occasional strike" strategy is not actually insulated from the ad business** if the Page, Instagram account, and Business Manager are the same connected asset graph used to run "Losing Sleep" ads — a repeat-infringer flag on the organic Page can jeopardize the ad account and Business Manager that fund the whole campaign.

---

## 4. The ads layer — this is the decisive finding

This is the single most important finding in this research, because it determines whether the strategy can be a *paid* growth engine at all — and the answer, on the current written policy of both major platforms, is **no.**

### TikTok Ads: explicit, direct prohibition
Per TikTok's own advertising policy page, TikTok's Intellectual Property Infringement policy explicitly prohibits **"use of clips from any unauthorized media sources to promote your product or service"** ([TikTok Ads: Intellectual Property Infringement policy](https://ads.tiktok.com/help/article/tiktok-ads-policy-intellectual-property-infringement)). This is not an inference from a general rule — it is a specific, named prohibition that describes exactly what this product would do: take clips from a source we don't control the rights to (a film) and use them to promote a product (a song). Enforcement includes **advertiser account suspension, temporary or permanent**, for detected violations.

### Meta Ads: general but strongly enforced
Meta's Advertising Standards state plainly: **"Ads may not contain content that violates the intellectual property rights of any third party, including copyright, trademark or other legal rights"** ([Meta Transparency Center: Third-Party IP Infringement](https://transparency.meta.com/policies/ad-standards/intellectual-property-infringement/third-party-infringement/)). Ads are reviewed both by **automated systems (image hashing, keyword detection, Rights Manager cross-checks)** and by **a global review team Meta describes as 15,000+ people** ([AdAmigo: Meta's Copyright Review Process](https://www.adamigo.ai/blog/meta-copyright-review-process); [Transparency Center IP page](https://transparency.meta.com/policies/ad-standards/intellectual-property-infringement/copyright-and-trademarks/)). Consequences scale from ad disapproval → ad account restriction → Business Manager disablement → permanent ban.

### Why paid spend changes the risk calculus fundamentally
1. **Ads get reviewed pre-publication** — organic posts generally do not undergo human/automated pre-screening at anywhere near the same rate; ads do, by policy design. You are actively inviting scrutiny you'd otherwise mostly avoid.
2. **Ads get scale and reach on purpose** — the entire point of ad spend is to buy distribution, i.e., to make the content maximally visible to exactly the kind of automated and human monitoring systems (including third-party brand-protection/anti-piracy vendors studios contract with) that are actively looking for this.
3. **Ads carry a money trail and an advertiser identity** — unlike an anonymous fan edit account, a "Respect the Funk" or "Hallow Youth" branded ad account is trivially identifiable, attributable, and a much more attractive enforcement target (there's a real business to send a cease-and-desist to, and real revenue to potentially claim damages against).
4. **TikTok's policy doesn't require a rights-holder complaint at all** — this is a *pre-emptive, written, self-enforcing rule*. Even absent any studio ever noticing, TikTok's own ad review can and should reject this creative on submission.

**Conclusion: even if you conclude the organic fair-use risk is tolerable, you cannot build the paid-acquisition engine on top of copyrighted film footage. The written ad policies of both major platforms forbid it outright, and paid spend is precisely the vector that most increases detection probability if you try anyway.** A creative strategy that "works" only organically, with no legal path to amplification via paid spend, breaks the core growth thesis of a music-marketing product built around distribution.

---

## 5. Licensing the legitimate way

### Studio clip licensing — cost and practical reality
Clip-licensing houses that broker studio footage (e.g., stock/archival libraries acting as agents for studio libraries) show rates roughly in this range, per aggregated industry pricing data:
- **General/non-theatrical media use:** ~$49–$79 per second, with a **~$539 minimum** per project ([Producers Library pricing](https://producerslibrary.com/pricing-details)).
- **Theatrical/broader rights, in perpetuity:** ~$59–$79 per second, **~$1,003 minimum**.
- **Network/cable licensing:** reported around **$800/clip**; PBS around **$500/clip**; theatrical-only rights $2,000–$3,500/clip depending on scope ([AM Stock-Cameo Film Library fee schedule](http://www.amstockcameo.com/license_fees.html)).

**Practical reality:** studios overwhelmingly license clips for documentaries, news, retrospectives, and licensed brand campaigns with real budgets and legal teams — not for indie artist marketing content. Even at the low end, a single 5–10 second clip could run **$500–$1,500+**, and a pipeline that wants to auto-generate many cut-scenes per song across many films is not economically compatible with per-clip licensing at any indie-label budget. This path essentially doesn't exist for this use case at this scale.

### Stock/archival footage alternatives (commercially licensed, no infringement risk)
These are legitimate substitutes that already carry commercial-use rights:
- **[Artgrid](https://artgrid.io/)** — subscription, **$25–$50/month** for HD-RAW/LOG cinematic footage, royalty-free for commercial projects including music videos.
- **[Filmsupply](https://www.filmsupply.com/pricing)** — higher-end, higher cost, licensing terms scale with company size/distribution — genuinely cinematic quality, good aesthetic fit for this use case.
- **[Pond5](https://www.pond5.com/pricing)** — huge library (30M+ clips), per-clip pricing from under $5 up to subscription tiers; commercial use including music videos supported.
- **[Getty Images](https://www.gettyimages.com/)** — most expensive, complex licensing, priced per download (~$199 low-res, ~$499 4K/HD, discounted packs).

None of these carry the *recognizable Hollywood film* aesthetic that makes the "edit" genre emotionally resonant (the appeal is specifically "I know this scene, this actor, this moment"), but they can approximate cinematic mood/texture at low legal risk.

### AI-generated video as a footage source
This is the most interesting current option and worth taking seriously:
- **Pricing (July 2026):** roughly **$0.10–$0.30/sec for Kling 3.0**, **$0.10–$0.30/sec base for Sora 2** (Pro tier ~$0.30–$0.50/sec), **~$0.15/sec for Runway Gen-4.5**, **~$0.15–$0.75/sec for Veo 3.1** depending on tier ([aggregated pricing comparison, July 2026](https://www.buildmvpfast.com/api-costs/ai-video)). Note: **OpenAI shut down the Sora consumer app on April 26, 2026**, though the API reportedly continues through September 2026 ([CineD](https://www.cined.com/sora-2-vs-hollywood-the-copyright-reckoning-of-generative-video/)) — verify current availability before committing to it as infrastructure.
- **Commercial use rights:** Runway, Kling, and Veo generally grant commercial use rights on paid tiers ([pricing comparison source above](https://www.buildmvpfast.com/api-costs/ai-video)).
- **Copyrightability (does WE own the output?):** The **[U.S. Copyright Office's Part 2 report on AI and Copyrightability (Jan 2025)](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf)** confirms human authorship remains required; **prompts alone are not sufficient** to claim authorship of AI output, but AI-assisted work with meaningful human creative selection/arrangement/modification can be protected ([Copyright Office AI hub](https://www.copyright.gov/ai/); [Skadden summary](https://www.skadden.com/insights/publications/2025/02/copyright-office-publishes-report)). Practically: the *raw* AI clip may not be exclusively ownable by you, but that mostly matters for defending against others copying you — it does **not** create infringement liability for you using it.
- **Important caveat — AI models trained on copyrighted film can reproduce copyrighted characters/scenes.** Sora 2's 2025 launch generated recognizable characters from *Bob's Burgers*, *SpongeBob*, *Pokémon*, etc., triggering major studio/talent-agency backlash (WME telling OpenAI all its clients would opt out) and a court order barring OpenAI from using the "Cameo" name/feature after litigation ([Hollywood Reporter](https://www.hollywoodreporter.com/business/business-news/openai-movies-tv-shows-lawsuits-legal-risk-1236391327/); [CNBC](https://www.cnbc.com/2025/10/04/sora-openai-video-app.html); [TechCrunch on the Cameo ruling](https://techcrunch.com/2026/02/17/u-s-court-bars-openai-from-using-cameo/)). **If you use AI video tools for this, prompt generically (moods, settings, generic characters) — do not prompt for specific movies, characters, or actor likenesses, or you reintroduce the same infringement/likeness risk you're trying to avoid**, now with an added layer of uncertainty about whether the AI vendor's output license actually protects you from a claim by the underlying IP owner.

---

## 6. The public-domain / legal substitute path

### Current public-domain cutoff (verified for 2026)
US copyright term for works published before 1978 is **life+70 / 95 years from publication** for corporate works. **As of January 1, 2026, all US works published in 1930 (and earlier) are now in the public domain** ([Library of Congress: Lifecycle of Copyright — 1930 Works](https://blogs.loc.gov/copyright/2025/12/lifecycle-of-copyright-1930-works-in-the-public-domain/); [Copyright Lately: Public Domain Day 2026](https://copyrightlately.com/public-domain-2026/); [Duke Law CSPD 2026](https://web.law.duke.edu/cspd/publicdomainday/2026/)).

Notable 1930-and-earlier films now fully clear: **Hell's Angels**, **The Big Trail** (John Wayne's first starring role), **Anna Christie** (Garbo's first talkie), **All Quiet on the Western Front** (1930 Best Picture), Marx Brothers' **Animal Crackers**, Laurel & Hardy shorts, plus the entire deep catalog of pre-1930 silent/early-sound film ([CBS News summary](https://www.cbsnews.com/news/new-public-domain-works-2026/); [Wikipedia: List of films in the public domain](https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States)). **Caveat: character/trademark rights can outlive the underlying film's copyright** — e.g., the original 1930 Betty Boop cartoon is PD but the modern Betty Boop character/trademark is not, and reuse must avoid later-added distinctive elements ([CBS News](https://www.cbsnews.com/news/new-public-domain-works-2026/)).

### Archives
- **[Internet Archive / Prelinger Collection](https://archive.org/details/prelinger)** — ~65% of holdings are public domain (expired copyright or no proper notice); **8,500+ PD films** freely downloadable and reusable, many under CC0/PD dedication ([Internet Archive Help](https://help.archive.org/help/prelinger-archive/)). Strong caveat: **PD films can still embed copyrighted elements (music score, licensed stock footage within them)** that aren't automatically PD just because the film shell is — verify per-film before use ([panix.com Prelinger licensing notes](https://www.panix.com/~footage/prelarch.html)).
- **Library of Congress moving image collections** — additional PD and public-access archival footage ([LOC Moving Image Research guide](https://guides.loc.gov/moving-image-research/related-resources)).

### Honest assessment: can PD footage deliver the "edit" aesthetic?
**Partially, and with a real trade-off.** The emotional power of the "edits" genre comes substantially from *recognition* — viewers respond because they know the actor, the character, the exact emotional beat from a movie they've seen recently (2020s-2010s blockbusters, prestige TV). Pre-1930 and archival/ephemeral PD film is stylistically and culturally distant from that (black-and-white, different acting/pacing conventions, no modern stars) — it will not replicate the "I recognize this scene from a movie I watched last month" hook that drives the format's virality. It *can*, however, deliver a genuinely striking, differentiated **cinematic/moody aesthetic** (grainy vintage footage, dramatic silent-era close-ups, classic Hollywood glamour) that could become its own distinctive visual signature for the brand — arguably a better long-term IP position than chasing a legally compromised trend. Combined with AI-generated modern-feeling cinematic footage and licensed stock footage, a hybrid library can get much closer to the contemporary "edit" look while staying fully clear.

---

## 7. Our own rights (Respect the Funk / Hallow Youth)

Owning the master recording and composition of "Losing Sleep" gives Respect the Funk/Hallow Youth:
- The exclusive right to reproduce, distribute, publicly perform, and synchronize *the song* with video content of our own choosing.
- The ability to license or use the song freely in any video we create or license footage for.

**It gives zero rights to any third party's film, footage, characters, or performers.** Music rights and audiovisual/film rights are completely separate copyright bundles held by different owners (the studio owns the film; a music publisher/label owns the underlying composition/master of anything used *in* the film — irrelevant here since we're not using their music). Owning "Losing Sleep" does not create any synchronization right, license, or fair-use privilege over someone else's movie — this is a common and important misconception to rule out explicitly, since it's tempting to think "it's my song, I can put it over anything." You cannot.

---

## Risk register

| Approach | Likelihood of enforcement | Severity if enforced | Mitigation |
|---|---|---|---|
| **Organic post, one-off, personal/brand account, copyrighted film clip + our song** | Low-moderate (Content ID/Rights Manager auto-mute or track is common; takedown/strike less common but documented) | Low-moderate: mute, regional block, or claim on that video; repeated incidents risk account-level repeat-infringer action ([case example](https://www.justanswer.com/intellectual-property-law/vava8-instagram-copyright-strike-bviral-video.html)) | Diversify footage sources; keep organic and ad-funded brand assets on separate Business Manager/Page identities to prevent cascade; monitor claim rate and stop if pattern-violation flags appear |
| **Automated pipeline, many copyrighted-film cut-scenes, organic only, single brand identity** | Moderate-high (volume increases match rate; consistent branding makes pattern-detection and repeat-infringer flags easier to trigger) | Moderate-high: accumulating strikes risk full account termination on TikTok (3 strikes) or YouTube (3 strikes); Instagram repeat-infringer account disable documented | Same as above, at greater urgency; consider this approach non-viable at scale |
| **Same pipeline output run as PAID ads (Meta or TikTok)** | High — TikTok's ad policy is a written, explicit, self-enforcing prohibition of exactly this ("clips from unauthorized media sources to promote your product"); Meta ad review (automated + ~15,000 human reviewers) actively screens for third-party IP | High: ad rejection at minimum; repeated attempts risk ad account restriction, Business Manager disablement, or permanent advertiser ban ([Meta Transparency Center](https://transparency.meta.com/enforcement/taking-action/counting-strikes/)) | Do not attempt. This is not a "risk to manage," it's a policy violation on its face. |
| **Cascading risk: organic copyright issues → paid ad infrastructure** | Moderate (documented mechanism; depends on shared Business Manager/Page/asset structure) | High: can disable the Business Manager and every connected ad account, not just the offending post ([Meta Transparency Center: Counting Strikes](https://transparency.meta.com/enforcement/taking-action/counting-strikes/)) | Structurally separate any experimental/gray-area organic content from the Business Manager used to run paid "Losing Sleep" campaigns |
| **Public-domain film (pre-1930) footage, organic + paid** | Very low (no underlying copyright; verify no embedded copyrighted elements) | Low (worst case: mistaken automated claim, easily disputed with PD provenance) | Source only from vetted PD archives (Internet Archive/Prelinger, LOC); keep provenance records per clip; watch for trademark issues on characters |
| **Licensed stock/archival footage (Artgrid, Filmsupply, Pond5), organic + paid** | Very low | Low | Confirm license explicitly covers commercial/paid-ad use (most do); retain license records |
| **AI-generated cinematic footage (Kling/Runway/Veo), generic prompts, organic + paid** | Low, if prompts avoid specific copyrighted characters/films/actor likeness | Low-moderate if prompts drift into recognizable IP (see Sora 2 backlash) | Prompt for generic scenes/moods only; avoid named films, characters, or real-person likeness; keep output license terms on file |
| **Studio clip licensing (paid license)** | None (licensed use) | None | Cost-prohibitive at indie scale (~$500–$3,000+ per clip/project minimum) — not practically viable for automated multi-clip pipeline |

---

## Recommendation: a path forward

**Build the product. Change the footage source, not the concept.** The valuable IP here is the indexing + auto-cut-to-song engine, not the specific choice of using copyrighted Hollywood film. Concretely:

1. **Keep the pipeline architecture** (index frames/scenes/dialogue, cut to song parameters, auto-generate) — this is genuinely differentiated technology regardless of footage source.
2. **Build the footage library from three legal sources, blended for aesthetic range:**
   - Public-domain film (1930-and-earlier, sourced and provenance-tracked via Internet Archive/Prelinger/LOC) for authentic grainy/vintage cinematic texture.
   - Licensed cinematic stock footage (Artgrid/Filmsupply-tier) for modern-feeling, high-production-value b-roll and mood shots, explicitly licensed for commercial/paid use.
   - AI-generated cinematic footage (Kling/Runway/Veo) prompted generically (moods, settings, archetypal characters — never named films/characters/real people) for maximum stylistic flexibility, including the ability to generate footage that thematically matches the song's lyrics/mood in a way stock footage can't.
3. **This combination is ad-platform-compliant** — nothing here trips TikTok's "unauthorized media sources" ad prohibition or Meta's third-party-IP ad policy, so the paid-acquisition engine (the actual growth lever) stays fully available.
4. **If the team wants to test the "real movie clip" aesthetic anyway for organic-only exploration:** treat it as a high-risk, non-scalable, non-paid experiment on an isolated brand identity (separate Page/IG/Business Manager, not connected to the ad account funding "Losing Sleep" spend), monitor claim/strike rate closely, and have counsel review before any escalation — but do not build the core product roadmap around it, and never run it as a paid ad.
5. **Get an actual entertainment lawyer to review** the final footage-sourcing plan, the PD provenance chain, and the stock/AI license terms before launch — this report identifies the shape of the risk, not a substitute for that review.

---

## Sources

**Statute & Copyright Office**
- [U.S. Copyright Office Fair Use Index](https://www.copyright.gov/fair-use/)
- [U.S. Copyright Office: Andy Warhol Foundation v. Goldsmith case summary (PDF)](https://www.copyright.gov/fair-use/summaries/Andy-Warhol-Found-for-the-Visual-Arts-Inc-v-Goldsmith-143-S-Ct-1258-2023.pdf)
- [U.S. Copyright Office: SOFA Entertainment v. Dodger Productions case summary (PDF)](https://www.copyright.gov/fair-use/summaries/sofa-dodger-9thcir2013.pdf)
- [U.S. Copyright Office: Copyright and Artificial Intelligence hub](https://www.copyright.gov/ai/)
- [U.S. Copyright Office: Copyright and AI Part 2 — Copyrightability report (PDF)](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf)

**Case law / primary legal documents**
- [Andy Warhol Foundation for Visual Arts, Inc. v. Goldsmith — Cornell LII / full text](https://www.law.cornell.edu/supremecourt/text/21-869)
- [SOFA Entertainment, Inc. v. Dodger Productions, Inc. — 9th Circuit opinion (PDF)](https://cdn.ca9.uscourts.gov/datastore/opinions/2013/03/11/10-56535.pdf)
- [SOFA Entertainment v. Dodger Productions — Stanford Fair Use Project case page](https://fairuse.stanford.edu/case/sofa-entertainment-inc-v-dodger-productions-inc-et-al/)
- [Copyright Alliance: Movie Copyright Cases Part 2 — Fair Use Cases (incl. Elvis Presley Enterprises v. Passport Video)](https://copyrightalliance.org/movie-copyright-cases-part-2/)

**Law firm / commentary analysis of Warhol**
- [Akerman LLP: "Does Transformative Matter? No, At Least Where Use Is Commercial"](https://www.akerman.com/en/perspectives/ip-does-transformative-matter-no-at-least-where-use-is-commercial.html)
- [Crowell & Moring: Andy Warhol, Commercialism, and the Doctrine of Fair Use](https://www.crowell.com/en/insights/client-alerts/lessemgreaterandy-warhollessemgreater-commercialism-and-the-doctrine-of-fair-use)
- [Ice Miller: Supreme Court Transforms Transformative Copyright Use](https://www.icemiller.com/thought-leadership/supreme-court-transforms-transformative-copyright-use-andy-warhol-foundation-for-the-visual-arts-inc-v-goldsmith-598-u-s-_-2023)
- [WIPO Magazine: In the Courts — the Warhol decision revisits the boundaries of fair use](https://www.wipo.int/en/web/wipo-magazine/articles/in-the-courts-the-us-supreme-courts-warhol-decision-revisits-the-boundaries-of-fair-use-56582)
- [Yale Law Journal: Copyright, Meet Antitrust — the Warhol decision](https://yalelawjournal.org/essay/copyright-meet-antitrust-the-supreme-courts-warhol-decision-and-the-rise-of-competition-analysis-in-fair-use)

**Platform policy — Content ID / Rights Manager / enforcement**
- [YouTube Help: How Content ID works](https://support.google.com/youtube/answer/2797370?hl=en)
- [YouTube Help: Using Content ID](https://support.google.com/youtube/answer/3244015?hl=en)
- [Third Chair: YouTube Copyright Rules in 2026 — Strikes, Claims, and Appeals](https://usethirdchair.com/blog/youtube-copyright-rules-in-strikes-claims-and-appeals)
- [Meta Transparency Center: How we protect intellectual property rights](https://transparency.meta.com/reports/intellectual-property/protecting-intellectual-property-rights/)
- [Meta (2023): Helping Creators and Publishers Manage IP with Rights Manager](https://about.fb.com/news/2023/01/helping-creators-and-publishers-manage-intellectual-property/)
- [Foxi Music: Instagram Reels Music Copyright — 2026 Legal Guide](https://www.foximusic.com/blog/instagram-reels-music-copyright-legal-guide/)
- [TikTok: Intellectual Property Policy](https://www.tiktok.com/legal/page/global/copyright-policy/en)
- [Red Points: TikTok DMCA takedown guide (2026)](https://www.redpoints.com/blog/tiktok-dmca-takedown/)
- [Third Chair: Content ID for TikTok and Instagram — What's Missing Today](https://usethirdchair.com/blog/content-id-for-tiktok-and-instagram-what-s-missing-today)
- [JustAnswer: real-world Instagram copyright strike / account termination case](https://www.justanswer.com/intellectual-property-law/vava8-instagram-copyright-strike-bviral-video.html)

**Ad policy — the decisive layer**
- [TikTok Ads: Intellectual Property Infringement policy (explicit prohibition on unauthorized media clips in ads)](https://ads.tiktok.com/help/article/tiktok-ads-policy-intellectual-property-infringement)
- [Meta Transparency Center: Third-Party IP Infringement ad policy](https://transparency.meta.com/policies/ad-standards/intellectual-property-infringement/third-party-infringement/)
- [Meta Transparency Center: Copyrights and Trademarks ad policy](https://transparency.meta.com/policies/ad-standards/intellectual-property-infringement/copyright-and-trademarks/)
- [Meta Transparency Center: Counting Strikes / enforcement cascade](https://transparency.meta.com/enforcement/taking-action/counting-strikes/)
- [AdAmigo.ai: How Meta's Copyright Review Process Works](https://www.adamigo.ai/blog/meta-copyright-review-process)
- [HyperFX: Meta Ad Account Disabled — Causes 2026](https://www.hyperfx.ai/blog/meta-ad-account-disabled-causes-2026)

**Licensing costs**
- [Producers Library: Pricing Details](https://producerslibrary.com/pricing-details)
- [AM Stock-Cameo Film Library: License Fees](http://www.amstockcameo.com/license_fees.html)
- [Artgrid](https://artgrid.io/)
- [Filmsupply: Pricing](https://www.filmsupply.com/pricing)
- [Pond5: Pricing](https://www.pond5.com/pricing)

**AI video generation**
- [Aggregated AI Video API Pricing, July 2026 (buildmvpfast.com)](https://www.buildmvpfast.com/api-costs/ai-video)
- [CineD: Sora 2 vs Hollywood — The Copyright Reckoning of Generative Video](https://www.cined.com/sora-2-vs-hollywood-the-copyright-reckoning-of-generative-video/)
- [Hollywood Reporter: OpenAI's video tool features user-generated South Park, Dune scenes — will studios sue?](https://www.hollywoodreporter.com/business/business-news/openai-movies-tv-shows-lawsuits-legal-risk-1236391327/)
- [CNBC: Sora is full of copyrighted characters — it's a gamble for OpenAI](https://www.cnbc.com/2025/10/04/sora-openai-video-app.html)
- [TechCrunch: US court bars OpenAI from using "Cameo"](https://techcrunch.com/2026/02/17/u-s-court-bars-openai-from-using-cameo/)
- [Skadden: Copyright Office Publishes Report on Copyrightability of AI-Generated Materials](https://www.skadden.com/insights/publications/2025/02/copyright-office-publishes-report)

**Public domain**
- [Library of Congress: Lifecycle of Copyright — 1930 Works in the Public Domain](https://blogs.loc.gov/copyright/2025/12/lifecycle-of-copyright-1930-works-in-the-public-domain/)
- [Copyright Lately: Public Domain Day 2026 Is Coming](https://copyrightlately.com/public-domain-2026/)
- [Duke University School of Law CSPD: Public Domain Day 2026](https://web.law.duke.edu/cspd/publicdomainday/2026/)
- [CBS News: Notable works officially in the public domain as 2026 arrives](https://www.cbsnews.com/news/new-public-domain-works-2026/)
- [Wikipedia: List of films in the public domain in the United States](https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States)
- [Internet Archive: Prelinger Collection](https://archive.org/details/prelinger)
- [Internet Archive Help Center: Prelinger Archive](https://help.archive.org/help/prelinger-archive/)
- [Prelinger Archives: Licensing, Access, Collections (panix.com)](https://www.panix.com/~footage/prelarch.html)
- [Library of Congress: Moving Image Research — Related Online Resources](https://guides.loc.gov/moving-image-research/related-resources)
