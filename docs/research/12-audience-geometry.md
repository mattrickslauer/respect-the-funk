# Pillar 12: Audience Geometry — Can Listener Types Be Modeled on a Circle?

*Testing a specific proposal: chart listener archetypes on a modular/circular taxonomy, ~12 equidistant "clock" positions, each defined by parameters (gender, hobbies, interests), probed with themed content accounts (a math page, a baby page), measured against song response.*

---

## Bottom line

**The instinct is real and has 70 years of psychometric precedent — but the number is wrong by roughly an order of magnitude, and the geometry claim (circular wraparound) is the part least likely to be true.** The canonical circular models in the literature — Holland's RIASEC hexagon, Russell's affect circumplex, the interpersonal circumplex — all top out at **6–8 discrete positions**, not 12, and in every case the circle was *discovered* by fitting a statistical model to hundreds of subjects, never *assumed* and drawn first. At our scale (one artist, 6–10 probe accounts, a few hundred to low-thousand follower counts), we cannot fit or validate a 12-position circumplex — not by a small margin, but by 10–100x on cost, and by 100–1000x on the subject count real psychometric circumplex-fitting requires. **What *is* fittable: one theory-anchored bipolar axis now (the People↔Things axis — which is, not coincidentally, almost exactly the "math page vs. baby page" contrast the user already proposed, and which happens to be the single most-replicated axis in this literature), extending to two orthogonal axes / four quadrants once budget clears ~$1,500–$5,000, and only reconsidering more resolution after that — with 6 positions (not 12) as the realistic ceiling even at multi-artist scale.** Meta's ad stack already solves audience *targeting* better than anything we could build; the honest, durable value of an explicit audience-geometry model isn't targeting — it's a shared vocabulary for **what content to make and which creators/niches to seed**, a decision layer upstream of anything the ad auction touches. Section 4 shows the full cost math distinguishing 12 positions costs; Section 7 gives the concrete, budget-matched v1.

---

## 1. Circumplex models — the real precedent

### 1.1 What "circular" actually claims

A circumplex is not just "a chart drawn as a circle." [Guttman (1954)](https://www.researchgate.net/publication/263932865_Guttmans_Radex_Theory_of_Intelligence) formally defined it as **a circular ordering of variables representing a difference in *kind*, not degree** — a qualitative wheel, as opposed to a *simplex* (a linear ordering of degree, like "amount of difficulty"). Guttman called the combination of the two a **radex** — the seed concept behind every circumplex model that followed ([Facet Theory, Wikipedia](https://en.wikipedia.org/wiki/Facet_theory); [Guttman 1954, "A New Approach to Factor Analysis: The Radex"](https://www.researchgate.net/publication/263932865_Guttmans_Radex_Theory_of_Intelligence)).

Concretely, circular topology makes three falsifiable claims about a proximity/correlation structure:
1. **Adjacent points correlate highest** (neighbors on the wheel are most similar).
2. **Points 180° apart are maximally dissimilar/negatively correlated** — true *opposition*, not just "different."
3. **Traveling far enough around returns you to the start** — the space *wraps*. This is the load-bearing, and most commonly wrong, assumption: most human trait spaces are **bipolar continua** (two opposite poles, nothing beyond either end), not wheels. Wraparound is a strong, additional claim beyond "there are two independent axes."

Whether audience/listener space plausibly wraps is addressed honestly in §1.6 below — short answer: **plausible for one axis (affect/energy), implausible for the specific example the user gave (math vs. babies)**, which is better modeled as two poles of a *linear* axis, not two stations on a wheel.

### 1.2 Holland's RIASEC hexagon — the canonical vocational-interest circle

Holland's model sorts vocational interest into six types — **R**ealistic, **I**nvestigative, **A**rtistic, **S**ocial, **E**nterprising, **C**onventional — arranged so adjacent types (e.g., Realistic–Investigative) correlate more than opposite types (Realistic–Social) ([Holland's hexagon model, ResearchGate figure](https://www.researchgate.net/figure/Hollands-hexagon-model-and-degrees-of-congruence-R-realistic-interests-I_fig3_233747919)).

**Critically, this structure was not assumed — it was tested.** The dominant test is [Hubert & Arabie's (1987) Randomization Test of Hypothesized Order Relations (Psychological Bulletin)](https://www.researchgate.net/publication/247728195_RANDALL_A_microsoft_FORTRAN_program_for_a_randomization_test_of_hypothesized_order_relations), operationalized in the **RANDALL/RTHOR** program: it computes the exact probability that the number of correctly-ordered correlation pairs in the hypothesized circular order could arise by chance relabeling of the proximity matrix, yielding a **Correspondence Index** (proportion of predictions met minus proportion violated) ([RANDALL description](https://www.researchgate.net/publication/247728195_RANDALL_A_microsoft_FORTRAN_program_for_a_randomization_test_of_hypothesized_order_relations)). Studies applying RTHOR to RIASEC — across gender, time, and countries including Finland and Bolivia — do generally confirm a circular (though not always perfectly hexagonal/equidistant) order ([Circumplex Structure of RIASEC Across Gender and Time](https://www.researchgate.net/publication/232431684_Circumplex_Structure_of_Holland%27s_RIASEC_Interests_Across_Gender_and_Time); [A Simple, Exact Test for the Holland Hexagon, ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0001879196900287)).

The alternative confirmatory route is [**Browne's (1992) circular stochastic process model**, published in *Psychometrika* 57:469–497](https://link.springer.com/article/10.3758/BRM.42.1.55) — implemented in the **CIRCUM** program and later the R package **CircE** — which fits each variable's *angular position* around 360° (either freely or constrained to be equally spaced) via a Fourier-series covariance model, giving a formal goodness-of-fit chi-square for "is this genuinely circular" as opposed to just "is this roughly circular by eyeball" ([CircE: An R implementation of Browne's model, *Behavior Research Methods* 42(1):55](https://link.springer.com/article/10.3758/BRM.42.1.55); [PubMed record](https://pubmed.ncbi.nlm.nih.gov/20160286/)).

A crucial underlying finding: the RIASEC hexagon **reduces to two orthogonal bipolar dimensions** — **People↔Things** and **Data↔Ideas** — first shown by [Prediger (1982)](https://www.researchgate.net/publication/232579136_Prediger's_Dimensional_Representation_of_Holland's_RIASEC_Circumplex) and reconfirmed as recently as 2023 across large modern datasets: [**"We've come full circle: The universality of People-Things and Data-Ideas as core dimensions of vocational interests," *Journal of Vocational Behavior*, 2023](https://www.sciencedirect.com/science/article/pii/S000187912300057X). This matters directly for §7 — **the field's own most-replicated axis is a two-pole continuum a lot like the one the user's "math page / baby page" example gestures at.**

### 1.3 Russell's circumplex model of affect

[Russell (1980), "A Circumplex Model of Affect," *Journal of Personality and Social Psychology* 39(6):1161–1178](https://www.researchgate.net/publication/235361517_A_circumplex_model_of_affect) — one of the most-cited papers in psychology — asked subjects to sort 28 emotion words by similarity and recovered a two-dimensional circular arrangement along **valence** (pleasant↔unpleasant) and **arousal** (activated↔deactivated) ([summary via Psychology of Human Emotion open textbook](https://psu.pb.unizin.org/psych425/chapter/circumplex-models/)). States 180° apart (e.g., "excited" vs. "bored/depressed") are near-opposite; states 90° apart are roughly uncorrelated ([ScienceDirect Topics overview](https://www.sciencedirect.com/topics/psychology/circumplex-model)). This one *does* plausibly wrap — arousal-plus-valence genuinely behaves like a wheel because both dimensions are graded intensities, not disjoint topics — and it is the closest real analogue to the "energy/mood" axis that would matter for **which cut of a song** (acoustic/mellow vs. hyped/intense edit) resonates where.

### 1.4 The interpersonal circumplex

[Wiggins (1979), "A Psychological Taxonomy of Trait-Descriptive Terms: The Interpersonal Domain," *JPSP* 37:395–412](https://scispace.com/papers/a-psychological-taxonomy-of-trait-descriptive-terms-the-32ogk8teeo) formalized interpersonal behavior around two orthogonal axes — **Agency/Dominance** and **Communion/Warmth** — with the same structural prediction as the others: adjacent octants correlate positively, opposite octants correlate negatively, orthogonal octants are uncorrelated ([Interpersonal Circumplex, Wikipedia](https://en.wikipedia.org/wiki/Interpersonal_circumplex)). This structure has been replicated across dozens of studies and languages and is usually reported at **8 octants**, occasionally 16 finer segments for large-N clinical samples — never a bare 12 ([Circumplex Scales of Interpersonal Problems, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6042649/)).

### 1.5 How you'd actually test whether a structure is circular

Three converging tools, all requiring real proximity/correlation data, none assumable from theory alone:

1. **RTHOR / RANDALL** ([Hubert & Arabie 1987](https://www.researchgate.net/publication/247728195_RANDALL_A_microsoft_FORTRAN_program_for_a_randomization_test_of_hypothesized_order_relations)) — nonparametric, tests a specific hypothesized *order* against random relabeling of the correlation matrix. Cheap on assumptions, needs a full k×k proximity matrix per subject group.
2. **Browne's CIRCUM / CircE** ([Browne 1992](https://link.springer.com/article/10.3758/BRM.42.1.55)) — confirmatory, model-based, gives an actual fit statistic (does circular beat non-circular) and estimated angular positions with standard errors.
3. **Circular statistics on the fitted angles** — once you have positions on a circle, the correct descriptive/inferential toolkit is **von Mises** distributions and **circular mean/variance**, not ordinary linear statistics (a mean of 359° and 1° is *not* 180°). The **Rayleigh test** is the standard test of whether angular data is non-uniform (i.e., whether there's a real, non-random preferred direction) — it is literally the likelihood-ratio test for uniformity within the von Mises family ([Rayleigh Test of Uniformity, StatisticsHowTo](https://www.statisticshowto.com/rayleigh-test-of-uniformity/); [circular R package docs](https://rdrr.io/cran/circular/man/rayleigh.test.html)).

**None of these can be run at N=6–10 probe accounts.** They require a full pairwise proximity matrix (§4 works this out in dollars).

### 1.6 Honest assessment: is *audience space* plausibly circular?

Partially. Affect/energy (Russell) plausibly wraps because both poles are intensities of the same underlying thing. **The user's own worked example — "a math page to attract men, a baby page to attract women" — does not plausibly wrap.** Math-content and baby-content are not opposite intensities of one variable; they're two different *topics* that happen to skew differently by gender today. There's no principled reason "extremely math" should be 180° from "extremely baby," nor any reason traveling far enough past "baby content" should loop back around to "math content." The closest real, validated structure that captures this specific contrast is Prediger's **People↔Things** axis (§1.2, §7) — a straight bipolar line, not a wheel. **Recommendation: don't force wraparound where a straight axis already fits and is already validated in the literature.** Reserve circular framing for where it's earned (affect/energy), and only claim a genuine circle once two validated orthogonal axes exist and their combination is tested (§1.5, §7).

---

## 2. Music preference taxonomies — the real empirical structure

### 2.1 STOMP → the MUSIC model

[Rentfrow & Gosling (2003), "The Do Re Mi's of Everyday Life: The Structure and Personality Correlates of Music Preferences," *JPSP* 84(6):1236–1256](https://www.researchgate.net/publication/10718841_The_Do_Re_Mi's_of_Everyday_Life_The_Structure_and_Personality_Correlates_of_Music_Preferences) introduced the **Short Test Of Music Preferences (STOMP)** and an original four-factor structure. Refined with larger, genre-free item sets, [Rentfrow, Goldberg & Levitin (2011), "The Structure of Musical Preferences: A Five-Factor Model," *JPSP* 100:1139–1157](https://pmc.ncbi.nlm.nih.gov/articles/PMC3138530/) converged on **five** factors across three independent studies — the **MUSIC** model: **M**ellow, **U**npretentious, **S**ophisticated, **I**ntense, **C**ontemporary — described as **genre-free and reflecting primarily affective/emotional responses to music** rather than raw genre labels. It was independently replicated in [Rentfrow et al. (2012), "Replication and Extension of the Five-Factor Model of Musical Preferences" (*Journal of Research in Personality*)](https://projects.ori.org/lrg/PDFs_papers/RentfrowEtal2012MUSICReplicationMP.pdf).

### 2.2 The shape of the space: factorial, not circular

**This is the key finding for the pillar's core question.** Neither the original STOMP work nor the MUSIC model paper claims or tests a *circular* structure. It's a standard **factor-analytic (oblique/orthogonal factor) model** — five correlated-but-distinct dimensions, not points on a wheel. No published work located in this research proposes or validates a circular/circumplex geometry specifically for music-preference space (searched directly; the [Wikipedia synthesis of the area](https://en.wikipedia.org/wiki/Psychology_of_music_preference) likewise describes preference clusters — "reflective/complex" vs. "intense/rebellious" — as a factorial/dimensional split, not a circumplex). **The honest answer to "what shape is music-preference space": empirically it is a low-dimensional factor space (5 correlated factors, sometimes compressed to 2 higher-order super-factors resembling arousal/valence), not demonstrated to be circular.** Calling it a "clock" borrows circumplex *language* without circumplex *evidence* — the evidence that exists (Rentfrow et al.) is standard EFA/CFA, the same statistical family used for RIASEC before its circularity was separately tested and confirmed. Nobody has run that second step for music preference.

### 2.3 Music preference × demographics/personality — the effect-size crux

This is where the plan's economics live or die, and the literature is blunt about it.

**[Schäfer & Mehlhorn (2017), "Can Personality Traits Predict Musical Style Preferences? A Meta-Analysis," *Personality and Individual Differences* 116:265–273](https://www.sciencedirect.com/science/article/abs/pii/S0191886917303215)** — synthesizing ~30 studies — found personality–genre correlations that were **inconsistent across studies and, where significant, small**: |r| mostly in the **.10–.21** range, and **only 6 of 30 weighted average coefficients even exceeded |r|=0.10** ([confirmed via Wikipedia's summary of the same meta-analysis](https://en.wikipedia.org/wiki/Psychology_of_music_preference); openness showed the largest, still-modest effects). Their stated conclusion: **the personality–music-preference relationship is not as strong as generally assumed.**

**[Nave, Minxha, Greenberg, Kosinski, Stillwell & Rentfrow (2018), "Musical Preferences Predict Personality: Evidence From Active Listening and Facebook Likes," *Psychological Science*](https://journals.sagepub.com/doi/abs/10.1177/0956797618761659)** used two large behavioral samples (Study 1: **N = 22,252**) and found music preference *did* predict personality — most notably openness and extraversion — above chance and above demographics alone. Note the scale required to detect this reliably: **tens of thousands of subjects**, not a handful of accounts.

**What this means at our scale, in plain terms**: |r| ≈ 0.1–0.2 corresponds to **1–4% of variance explained** (r²). Correlations that small are real academic findings but are exactly the kind of effect that "will not survive contact with a marketing budget" — you'd need the Nave-et-al-scale sample (tens of thousands) to even *detect* them reliably, let alone use them to route content decisions for one artist with a handful of probe accounts. **Do not build the pillar's targeting logic on personality-genre correlation coefficients this small. They are statistically real and practically unusable for us.**

**Gender**, by contrast, shows the more reliably usable — if still modest — signal: multiple reviews note women report a stronger emotional response to music and skew toward mainstream/popular genres more than men on average ([Psychology of Music Preference, Wikipedia, synthesizing multiple primary studies](https://en.wikipedia.org/wiki/Psychology_of_music_preference)). This is consistent with (not proof of) the intuition behind "math page for men, baby page for women" — a plausible, weak prior, not a validated targeting mechanism.

---

## 3. The dimensionality-reduction reality

A "clock face" read as an angle is a **2D embedding interpreted in polar coordinates.** That's a specific, strong methodological choice with real failure modes:

- **PCA** gives you the two directions of maximum linear variance — meaningful angles only if the *underlying* structure is genuinely two-dimensional and roughly circular; otherwise the "angle" of a point is an artifact of whichever two components happened to come out first.
- **MDS** (classical or non-metric) explicitly tries to preserve pairwise distances in low-D — closer to the actual circumplex-fitting tradition (§1.5) — but still needs a real, well-populated proximity matrix, not a hand-assigned one.
- **t-SNE / UMAP** are the most dangerous tools to reach for here. [Chari & Pachter (2023), "The Specious Art of Single-Cell Genomics," *PLOS Computational Biology*](https://journals.plos.org/ploscompbiol/article?id=10.1371%2Fjournal.pcbi.1012403) built an autoencoder ("Picasso") that forces arbitrary high-dimensional data into **any predefined 2D shape — including a literal elephant** — while preserving roughly the same degree of "local structure" that t-SNE/UMAP claim to preserve. Their point, stated for genomics but structurally identical to the risk here: **t-SNE/UMAP layouts can look meaningfully clustered/circular and be substantially arbitrary** — global distances and shapes are not reliable ([summary thread](https://x.com/lpachter/status/1431325969411821572)). *(Their strongest claims have since been contested by other groups showing UMAP/t-SNE do preserve local neighborhoods reasonably well under better metrics — cited for balance — but the core warning holds: [The art of seeing the elephant in the room, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11446450/).)* **The practical warning for us: if you set out to find a circle, an embedding algorithm can hand you one whether or not it's really there. A clock face drawn by inspection is exactly this failure mode — indistinguishable, without a formal circularity test (§1.5), from Picasso's elephant.**
- **If** a circular structure is eventually validated, the correct descriptive toolkit switches from ordinary (linear) statistics to **circular statistics** — von Mises distribution for the angular analogue of a normal distribution, **circular mean/variance** (not arithmetic mean of degrees), and the **Rayleigh test** for whether angles cluster non-uniformly around the circle rather than spreading uniformly ([circular R package](https://rdrr.io/cran/circular/man/rayleigh.test.html); [Statistics How To](https://www.statisticshowto.com/rayleigh-test-of-uniformity/)). This machinery is real, well-built, and completely appropriate — **once** there's a validated circle to apply it to (see §8 for what it tells us about *density*, which is the more immediately useful piece).

---

## 4. The sample-size question — the single most important section

### 4.1 How many parameters does a 12-position circumplex have?

Twelve positions on a circle imply a **k=12** proximity/correlation matrix with **C(12,2) = 66** unique pairwise relationships to estimate and mutually order. A confirmatory circular model (Browne's CIRCUM, §1.5) parametrizes this with roughly one **angular position** per variable (11 free angles after fixing rotation/reflection) plus one **communality** per variable (12) plus a small number of global Fourier coefficients — **on the order of 24–30 free parameters**, comparable in information demand to a standard 2-factor exploratory factor analysis on 12 manifest variables *(this is a back-of-envelope structural estimate from the general CIRCUM parametrization, not a number stated verbatim in the primary source — flagged as an approximation, not verified precisely)*. That parameter count is what has to be estimated *before* you can even ask whether the shape is circular.

### 4.2 Standard rules for how much data that needs

- **[Comrey & Lee (1992)](https://www.encorewiki.org/display/~nzhao/The+Minimum+Sample+Size+in+Factor+Analysis)** benchmark for stable factor solutions: **N=50 very poor, 100 poor, 200 fair, 300 good, 500 very good, 1,000+ excellent.**
- **Subjects-per-variable rules of thumb**: "rule of 10" (≥10 subjects per item) → for 12 positions, **≥120**; "rule of 5" → **≥60** ([summary via researchgate/theanalysisfactor](https://www.theanalysisfactor.com/sample-size-needed-for-factor-analysis/)).
- **[MacCallum, Widaman, Zhang & Hong (1999), "Sample Size in Factor Analysis," *Psychological Methods*](https://quantpsy.org/pubs/maccallum_widaman_preacher_hong_2001.pdf)** — the field's most careful treatment — showed flat rules of thumb are themselves wrong: what matters is **communality**. With high communalities (~0.6–0.8, i.e., each variable strongly reflects the underlying factors), N=100–200 can be enough; with the **low, noisy communalities typical of behavioral/marketing data** (~0.2–0.4), stable recovery needs **several hundred to 1,000+**.
- **MDS specifically**: proximity-matrix estimates show **relative stability from ~25 individuals, materially better stability at 100+** ([empirical metric-recovery study, ResearchGate](https://www.researchgate.net/publication/247742587_Matrix_and_Stimulus_Sample_Sizes_in_the_Weighted_MDS_Model_Empirical_Metric_Recovery_Functions)) — but note this N is *people rating similarity*, a data-collection mode (panel/survey) we don't have access to at this scale (see §7).

**Every one of these benchmarks is denominated in real human subjects (or independent behavioral events), not in "accounts."** We have 6–10 accounts. Even the most permissive rule of thumb here (60, "rule of 5") is **6–10x** the number of *accounts* we have — and accounts aren't subjects; each account would need to independently generate dozens to hundreds of scoreable events to function as one unit of "N."

### 4.3 Translating to the project's own CTR-lift benchmark — the worked math

The project has already rigorously derived, and this report reuses without re-deriving, the Meta CTR-detection benchmark from [`03-audience-models.md` §6](03-audience-models.md#6-detecting-a-ctr-lift-the-real-cost-of-knowing):

| Lift to detect | 80% power (impressions) | Cost @ $9.27 CPM (entertainment) |
|---|---|---|
| **+50% relative** (1.0%→1.5% CTR) | **15,478** | **$143** |
| **+20% relative** (1.0%→1.2% CTR) | **85,284** | **$791** |

That's the cost of **one** pairwise comparison — "does audience A respond differently than audience B." A 12-position circumplex requires **C(12,2) = 66** such pairwise comparisons to establish the full ordering (the minimum needed even before applying the formal circularity tests of §1.5):

| Resolution | Positions | Pairwise comparisons (C(k,2)) | Cost, 50% lift only (naive) | Cost, 20% lift (naive) |
|---|---|---|---|---|
| **1 axis, 2 poles** | 2 | 1 | **$143** | **$791** |
| **2 axes, 4 quadrants** | 4 | 6 | $858 | $4,746 |
| **Hexagon (RIASEC-scale)** | 6 | 15 | $2,145 | $11,865 |
| **12-position clock** | 12 | **66** | **$9,438** | **$52,206** |

And that's *before* correcting for running 66 (or 15, or 6) simultaneous statistical tests. Doing this properly requires a **Bonferroni correction**: testing at family-wise α=0.05 across m=66 comparisons means each individual test must run at α'=0.05/66≈0.00076, which raises the required critical value from z=1.96 to **z≈3.38**. Re-deriving the project's own sample-size formula $n=(z_{\alpha'/2}+z_\beta)^2 \cdot [p_1(1-p_1)+p_2(1-p_2)]/(p_1-p_2)^2$ with that inflated z (holding 80% power, $z_\beta=0.84$) multiplies every required n by a factor of $(3.38+0.84)^2/(1.96+0.84)^2 ≈ 2.27$. Applied to the 12-position row:

| Resolution | Naive cost (20% lift) | Bonferroni-corrected cost (20% lift) |
|---|---|---|
| 2 quadrants (1 pair) | $791 | $1,796 |
| 4 quadrants (6 pairs) | $4,746 | $10,774 |
| Hexagon (15 pairs) | $11,865 | $26,935 |
| **12-position clock (66 pairs)** | **$52,206** | **≈$118,500** |

*(These are illustrative order-of-magnitude figures from applying the project's own already-verified pairwise formula 66 times with a standard multiple-comparisons correction — not a formal CIRCUM/CFA fit statistic, which would use the proximity-matrix machinery of §1.5 rather than brute-force pairwise testing. The true cost of a properly-fit confirmatory circumplex model is not identical to this, but this is the right order of magnitude and the right *direction* of the correction.)*

Compare this to the project's own stated budget tiers ([`03-audience-models.md` §6.4](03-audience-models.md#64-verdict-by-budget)): **$500 total detects only large (50%+) lifts; $1,500 detects a 20% lift at 80% power for one pairwise test; $5,000 covers all four lift scenarios for one pairwise test with room to scale the winner.** The 12-position clock's *properly-corrected* cost (**≈$118,500** for 20%-lift resolution) is **~24x the project's largest stated budget tier**, and even the most optimistic naive/uncorrected reading (**$9,438**, 50%-lift-only, no multiple-comparisons correction at all) is still **~6x the largest tier and ~19x the "affordable" $500 tier.**

**A second, independent constraint points the same direction.** The project separately established (same document, §9.5) that each Meta ad set needs **~50 conversions/week** to escape "Learning Limited" status. Splitting into 12 position-specific ad sets to test them requires **12 × 50 = 600 conversions/week** just to keep every arm out of the statistically-starved regime — before counting the impressions needed to *generate* those conversions. That is a scale of campaign this project is not budgeted or staffed to run for a single-artist, early-stage roster.

**A third constraint: do the probe accounts even have the reach to generate the impressions?** Both Instagram and TikTok gate demographic audience insights behind a **100-follower minimum**, and meaningful reach ("Accounts Reached") thresholds sit above that ([Instagram Insights requirements](https://help.instagram.com/788388387972460/); [TikTok analytics demographic threshold](https://influenceflow.io/resources/tiktok-analytics-and-audience-insights-the-complete-guide-for-2026/)). Newly-created probe accounts start at zero and grow slowly (see [`05-account-acquisition.md`](05-account-acquisition.md) on the risks and realistic pace of account growth). Organic reach from 6–10 small, fresh accounts is nowhere near 15,478 impressions, let alone 2.3M+ — **even the cheapest single pairwise test effectively requires paid amplification, not organic probe-account posting alone.**

### 4.4 The verdict

**A 12-position circumplex is not fittable or validatable at this project's current scale — not marginally, but by roughly one to two orders of magnitude on both cost and required subject/event count.** This isn't just a budget artifact, either: §1.2–§1.4 showed that even the best-funded, most mature circumplex literatures (built on samples in the hundreds to tens of thousands, run over decades) converge on **6–8 discrete categories**, never 12. **The field itself never needed 12 positions — that's independent evidence the target resolution was wrong from the start, not only a constraint imposed by our budget.**

**What is fittable, with the same math:**
- **1 axis / 2 poles now** — the project's own $143–$791 single-pairwise-test benchmark applies directly.
- **2 axes / 4 quadrants** at **$858–$4,746** naive (**$1,141–$6,312** Bonferroni-corrected for a 4-way comparison, using the same inflation logic at m=6) — inside or near the project's existing $1,500 and $5,000 budget tiers.
- **A hexagon (6 positions, matching RIASEC exactly)** is a plausible *long-run, multi-artist-roster* target once cumulative ad spend and follower counts across the whole label's catalog clear roughly **$12,000–$27,000** in cumulative properly-powered pairwise testing — not a near-term target for one song.
- **12 positions: park it.** Revisit only if the roster scales to the point where cumulative spend across many songs/artists organically accumulates the impressions this table requires as a side effect of normal marketing, not as a dedicated research budget.

---

## 5. The confounding problem

Every probe account differs simultaneously in **niche** (what topic/archetype it represents) and **content quality/execution** (how good the specific post is, who made it, what time it went up). Follower growth or engagement on Account A vs. Account B conflates both — a "math page" that grows faster than a "baby page" could mean men respond better to math content, *or* it could mean whoever runs the math page is simply better at making content, or posted at a better time, or benefited from a lucky algorithmic push. Structural virality research is unambiguous that idiosyncratic, early, largely unpredictable factors dominate individual post performance ([Goel, Anderson, Hofman & Watts, *Management Science* 2016, cited in `03-audience-models.md` §8](03-audience-models.md)) — so this confound is not a small nuisance, it's the dominant source of variance you'd otherwise misattribute to "niche."

**The fix is standard experimental design, and it's practical at 6 accounts:**

- **Rotate identical content across accounts.** Take the *same* piece of content (same song clip, same edit) and post it, at matched times, across all 6–10 probe accounts. Now the "niche" of the *account* (its follower base, its framing/caption context) is the only thing varying — content quality is held constant by construction. This isolates the audience-fit effect from the creative-quality effect.
- **Within-account A/B.** Where an account has enough reach, test two content variants (e.g., a "mellow" edit vs. an "intense" edit of the same clip) *within* the same account/audience, so niche is held constant and only content varies — the mirror-image design, isolating creative effect from audience effect.
- **Block/counterbalance by time.** Stagger posting order across accounts and days so that "week 1 got the good creative" doesn't become confounded with "week 1 was also when the algorithm was favorable" — a basic blocking control.
- **Latin square as the formal version of the above.** A Latin square design controls for **two** nuisance factors simultaneously (e.g., account/niche as rows, time slot as columns) while testing one treatment factor (content variant), such that each treatment appears exactly once per row and once per column — this needs far fewer cells than full factorial crossing (a 6×6 Latin square needs 36 cells vs. 216 for full 6-niche×6-slot×6-content crossing) ([Latin Square Design, Statistics By Jim](https://statisticsbyjim.com/basics/latin-square/); caution on over-applying it in marketing generally — [Latin Square in marketing: a cautionary tale, ResearchGate](https://www.researchgate.net/publication/241239470_The_rise_and_fall_of_the_Latin_Square_in_marketing_A_cautionary_tale)).

**Minimum viable design for 6 accounts, concretely:** pick **6 pieces of content** (e.g., 6 different clip/caption combinations of the same song). Build a 6×6 Latin square where rows = probe accounts, columns = posting week, cells = which of the 6 content pieces runs that week — each content piece appears exactly once per account and once per week. After 6 weeks you have a fully crossed, confound-controlled dataset: **niche effect** (compare rows, content held balanced) is now separable from **content effect** (compare columns/content IDs, account held balanced). This is the smallest design that actually separates the two effects rather than conflating them — a simpler "post the same 1 piece everywhere" design (§ first bullet) is the cheap version that only isolates niche effect and forgoes measuring content effect at all, which is a reasonable v1 simplification if the priority is testing niche fit, not creative optimization.

---

## 6. Does the platform already do this better?

**For targeting: yes, decisively.** Meta's ad stack — **GEM** (foundation-model-scale ads recommendation model, trained on thousands of GPUs across the entire Facebook/Instagram behavioral graph), **Andromeda** (retrieval, narrowing tens of millions of eligible ads to a shortlist using learned embeddings), and **Lattice** (unified multi-domain, multi-objective ranking) — already builds and continuously updates an audience representation from data no small advertiser will ever have access to: cross-platform behavioral graphs at the scale of the entire user base ([Meta's Generative Ads Model (GEM), Engineering at Meta, Nov 10 2025](https://engineering.fb.com/2025/11/10/ml-applications/metas-generative-ads-model-gem-the-central-brain-accelerating-ads-recommendation-ai-innovation/); [Meta Andromeda, Engineering at Meta, Dec 2 2024](https://engineering.fb.com/2024/12/02/production-engineering/meta-andromeda-advantage-automation-next-gen-personalized-ads-retrieval-engine/); [Meta Lattice, ai.meta.com](https://ai.meta.com/blog/ai-ads-performance-efficiency-meta-lattice/); reported **+22% ROAS** from Andromeda's learned retrieval — [same source](https://engineering.fb.com/2024/12/02/production-engineering/meta-andromeda-advantage-automation-next-gen-personalized-ads-retrieval-engine/)). This is documented at length in [`03-audience-models.md` §1](03-audience-models.md#1-metas-ad-stack). A hand-built 12-point audience wheel, fit on a few thousand data points, cannot out-target a system fit on billions of user-days of behavior. **Building our own targeting geometry is redundant, and worse, actively harmful — narrow manual segmentation fights Andromeda's retrieval and fragments the data Lattice needs to pool ([same doc, principle #2](03-audience-models.md)).**

**But targeting is not the only thing an audience model is for, and this is the pillar's real strategic value.** Meta's stack answers "given this piece of content and this objective, who should see it" — it cannot answer:
- **What content to make in the first place.** GEM optimizes delivery of creative that already exists; it has no opinion on whether the next piece of content should be a "wholesome parenting" reel or a "hyper-analytical breakdown" reel for a funk track. That's a creative-strategy decision made *before* anything touches the auction.
- **Which creators/niches to seed or partner with.** Deciding whether to approach a math-TikToker or a parenting-influencer for a collab is a decision about **content and relationship strategy**, not ad delivery — Meta's black box has no interface for "tell me what kind of creator resonates with this song," only "given creative X, who clicks it."
- **Why** something is working, in language a human team can act on. An explicit, interpretable two-axis model ("this content leans Things-coded and Intense-coded") gives the team a shared vocabulary for creative briefs, follow-on content, and pitch decisions — Andromeda's embeddings are opaque by construction and were never meant to be read by a person.

**The honest reframe: an audience-geometry model at our scale is not a targeting tool (Meta already wins there) — it is a lightweight, interpretable *content-ideation and creator-outreach taxonomy*, sitting one layer upstream of anything the ad auction touches.** That's a real, defensible use of the instinct — just not the use case ("chart listeners to attract followers via ads") the original proposal framed it as.

---

## 7. What a defensible v1 looks like

**Axis 1 (now): People ↔ Things**, borrowed directly and explicitly from Prediger's validated, twice-replicated RIASEC axis (§1.2). This is not a new invention — it's the single most-replicated bipolar dimension in this literature, reconfirmed as recently as 2023 across large modern samples ([full-circle 2023 replication](https://www.sciencedirect.com/science/article/pii/S000187912300057X)). It also happens to closely match the user's own worked example: "math page" content skews toward the **Things/Data** pole (systems, analysis, abstraction), "baby page" content skews toward the **People** pole (relationships, nurturing, social connection). **The user's instinct correctly identified a real, validated axis — the correction is calling it one axis of a straight line, not one hour on a 12-position clock.**

- **Instrument**: don't try to *fit* this from data we don't have (no listener survey panel exists at this scale). Instead, **assign** each probe account's position by rubric — a simple 1–5 analyst rating of how People-coded vs. Things-coded its content niche is, decided in advance, not fit post hoc. This sidesteps the N=100–500 subject requirement of §4.2 entirely, at the honest cost of not yet being empirically validated — it's a theory-anchored prior, not a fitted model.
- **Measurement**: run the same song content across 2–3 accounts near each pole (People-leaning vs. Things-leaning), using the **rotate-identical-content** design of §5, and compare **on-platform engagement (video-watch-depth ≥50%/75%, per [`03-audience-models.md` §7.3](03-audience-models.md))** and, where paid, CTR — exactly the metric and cost structure already validated in §4.3's "1 axis, 2 poles" row: **$143–$791** for one properly-powered pairwise test.
- **Validation gate before adding an axis**: only add a second axis once the People↔Things pairwise test above shows a real, statistically distinguishable difference (not assumed) — i.e., earn the second dimension with data, don't draw it up front.

**Axis 2 (after Axis 1 shows signal): Mellow ↔ Intense**, borrowed from the validated ends of Rentfrow et al.'s MUSIC model (§2.1) and Russell's arousal dimension (§1.3) — operationalized concretely as **two edits of the same song** (an acoustic/mellow cut vs. a hyped/high-energy cut), tested within-account per the §5 A/B design. Combining both axes gives **4 quadrants**, at the **$858–$4,746** (naive) / **$1,141–$6,312** (corrected) cost band from §4.4 — inside the project's own existing budget tiers.

**How to assign a real account/audience a position, concretely, with data we actually have**: once both axes exist, a probe account's (or a listener segment's) position is a simple 2D score, not a fitted angle — $(\text{score}_{\text{People-Things}}, \text{score}_{\text{Mellow-Intense}})$ — computed from (a) the analyst rubric rating of its content niche, cross-checked against (b) its native platform demographic split (age/gender, available once an account clears the **100-follower threshold** on both Instagram and TikTok — [Instagram Insights](https://help.instagram.com/788388387972460/); [TikTok analytics](https://influenceflow.io/resources/tiktok-analytics-and-audience-insights-the-complete-guide-for-2026/)) as a weak, directional sanity check (§2.3's gender-skew finding), never as the primary signal given how small personality/genre correlations are (§2.3). **Read the position as Cartesian coordinates on a plane, not an angle on a wheel, until circularity is actually tested (§1.5) — which requires the multi-artist, multi-hundred-subject scale of §4.2, not this project's current stage.**

**Resolution ladder, tied to evidence, not aspiration:**

| Stage | Structure | Positions | Data needed | Cost | Status |
|---|---|---|---|---|---|
| v0 (now) | 1 axis, theory-assigned | 2 poles | Rubric only + 1 pairwise test | $143–$791 | **Do this now** |
| v1 | 2 orthogonal axes, theory-assigned | 4 quadrants | Rubric + 6 pairwise tests | $1,141–$6,312 | Next, once v0 shows signal |
| v2 | Empirically fit 2D plane | ~6 (hexagon-scale) | N≈100–300 subjects/events per §4.2, cumulative multi-artist scale | ~$12k–$27k cumulative | Later, multi-artist roster |
| Full circumplex | Formally validated circle | 8 max in the mature literature | N≈300–1,000+, RTHOR/CIRCUM fit test | Not currently in scope | **Never at single-artist scale; revisit only at label-portfolio scale** |

---

## 8. Non-uniform density — the "equidistant clock" mismatch

"Equidistant like a clock" is a strong, specific claim: it assumes listener taste is spread **uniformly** around whatever circle exists — every hour equally populated. Real audience distributions are never like this. Every empirical circumplex study cited above shows the same pattern: certain octants/types dominate any given sample (e.g., psychology-subject-pool studies over-represent Social/Artistic RIASEC types; general-population music-preference samples skew heavily toward Unpretentious/Contemporary categories relative to niche Sophisticated ones). In the language of §3's circular-statistics toolkit: real listener-taste angular distributions almost always have a **von Mises concentration parameter κ > 0** (mass bunched around one or a few preferred directions) — **κ = 0 (perfectly uniform around the circle) is the special case the "equidistant clock" design implicitly assumes, and it is the least realistic case in practice.** A Rayleigh test run on real audience data would, in virtually every case in this literature, reject uniformity ([Rayleigh Test of Uniformity](https://www.statisticshowto.com/rayleigh-test-of-uniformity/)) — meaning the "equally spaced" assumption is very likely false the moment real data exists to check it.

**Practical consequence: don't allocate probe accounts one-per-hour.** Allocate them proportional to *expected* density — informed by whatever prior exists (the artist's actual existing Spotify/IG audience skew, if any; genre-adjacent lookalike audiences for funk/indie-adjacent listeners) — putting **more probe accounts near the plausible dominant mode** (e.g., if early signal suggests the funk/indie audience skews Intense+Things-coded — energetic, analytical/production-focused listeners) and **fewer or none in sectors with no plausible listener mass** (a literal "12th hour" niche with zero connection to the genre is probe-account budget spent proving a near-certain null). This also directly supports the §4 economics: concentrating the limited pairwise-test budget on the 2–4 positions with real expected mass, rather than spreading it thin and equal across 12 mostly-empty sectors, is both cheaper and more likely to find a real, actionable signal.

---

## Sources

**Circumplex theory & tests**
- [Guttman (1954), "A New Approach to Factor Analysis: The Radex" — foundational radex/circumplex definition](https://www.researchgate.net/publication/263932865_Guttmans_Radex_Theory_of_Intelligence)
- [Facet Theory, Wikipedia](https://en.wikipedia.org/wiki/Facet_theory)
- [Hubert & Arabie (1987), Randomization Test of Hypothesized Order Relations — RANDALL program](https://www.researchgate.net/publication/247728195_RANDALL_A_microsoft_FORTRAN_program_for_a_randomization_test_of_hypothesized_order_relations)
- [Browne (1992), Circumplex models for correlation matrices, *Psychometrika* 57:469–497 — via CircE implementation, *Behavior Research Methods* 42(1):55](https://link.springer.com/article/10.3758/BRM.42.1.55) · [PubMed](https://pubmed.ncbi.nlm.nih.gov/20160286/)
- [Rayleigh Test of Uniformity — Statistics How To](https://www.statisticshowto.com/rayleigh-test-of-uniformity/) · [circular R package docs](https://rdrr.io/cran/circular/man/rayleigh.test.html)

**RIASEC / vocational interests**
- [Holland's hexagon model diagram, ResearchGate](https://www.researchgate.net/figure/Hollands-hexagon-model-and-degrees-of-congruence-R-realistic-interests-I_fig3_233747919)
- [Circumplex Structure of Holland's RIASEC Interests Across Gender and Time](https://www.researchgate.net/publication/232431684_Circumplex_Structure_of_Holland%27s_RIASEC_Interests_Across_Gender_and_Time)
- [A Simple, Exact Test for the Holland Hexagon, ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0001879196900287)
- [Prediger's Dimensional Representation of Holland's RIASEC Circumplex](https://www.researchgate.net/publication/232579136_Prediger's_Dimensional_Representation_of_Holland's_RIASEC_Circumplex)
- [We've come full circle: The universality of People-Things and Data-Ideas as core dimensions of vocational interests, *Journal of Vocational Behavior* (2023)](https://www.sciencedirect.com/science/article/pii/S000187912300057X)

**Affect and interpersonal circumplex**
- [Russell (1980), A Circumplex Model of Affect, *JPSP* 39(6):1161–1178](https://www.researchgate.net/publication/235361517_A_circumplex_model_of_affect)
- [Russell's Circumplex Models, Psychology of Human Emotion open textbook](https://psu.pb.unizin.org/psych425/chapter/circumplex-models/)
- [Wiggins (1979), A Psychological Taxonomy of Trait-Descriptive Terms: The Interpersonal Domain, *JPSP* 37:395–412](https://scispace.com/papers/a-psychological-taxonomy-of-trait-descriptive-terms-the-32ogk8teeo)
- [Interpersonal Circumplex, Wikipedia](https://en.wikipedia.org/wiki/Interpersonal_circumplex)
- [Development and Validation of the Circumplex Scales of Interpersonal Problems, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6042649/)

**Music preference structure & effect sizes**
- [Rentfrow & Gosling (2003), The Do Re Mi's of Everyday Life, *JPSP* 84(6):1236–1256](https://www.researchgate.net/publication/10718841_The_Do_Re_Mi's_of_Everyday_Life_The_Structure_and_Personality_Correlates_of_Music_Preferences)
- [Rentfrow, Goldberg & Levitin (2011), The Structure of Musical Preferences: A Five-Factor Model, *JPSP* 100:1139–1157](https://pmc.ncbi.nlm.nih.gov/articles/PMC3138530/)
- [Rentfrow et al. (2012), Replication and Extension of the MUSIC Model](https://projects.ori.org/lrg/PDFs_papers/RentfrowEtal2012MUSICReplicationMP.pdf)
- [Psychology of Music Preference, Wikipedia (synthesis of the area, including Schäfer & Mehlhorn 2017)](https://en.wikipedia.org/wiki/Psychology_of_music_preference)
- [Schäfer & Mehlhorn (2017), Can Personality Traits Predict Musical Style Preferences? A Meta-Analysis, *Personality and Individual Differences* 116:265–273](https://www.sciencedirect.com/science/article/abs/pii/S0191886917303215)
- [Nave, Minxha, Greenberg, Kosinski, Stillwell & Rentfrow (2018), Musical Preferences Predict Personality, *Psychological Science*](https://journals.sagepub.com/doi/abs/10.1177/0956797618761659)

**Dimensionality reduction risk**
- [Chari & Pachter (2023), The Specious Art of Single-Cell Genomics, *PLOS Computational Biology*](https://journals.plos.org/ploscompbiol/article?id=10.1371%2Fjournal.pcbi.1012403)
- [Thread summary, Lior Pachter](https://x.com/lpachter/status/1431325969411821572)
- [Counterpoint: The art of seeing the elephant in the room, PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11446450/)

**Sample-size / factor-analysis methodology**
- [Comrey & Lee (1992) benchmarks, summarized via The Analysis Factor](https://www.theanalysisfactor.com/sample-size-needed-for-factor-analysis/) · [Minimum Sample Size in Factor Analysis](https://www.encorewiki.org/display/~nzhao/The+Minimum+Sample+Size+in+Factor+Analysis)
- [MacCallum, Widaman, Zhang & Hong (1999), Sample Size in Factor Analysis: The Role of Model Error, *Psychological Methods*](https://quantpsy.org/pubs/maccallum_widaman_preacher_hong_2001.pdf)
- [MDS proximity-matrix stability: Matrix and Stimulus Sample Sizes in the Weighted MDS Model](https://www.researchgate.net/publication/247742587_Matrix_and_Stimulus_Sample_Sizes_in_the_Weighted_MDS_Model_Empirical_Metric_Recovery_Functions)

**Experimental design**
- [Latin Square Design in Experiments Explained, Statistics By Jim](https://statisticsbyjim.com/basics/latin-square/)
- [The rise and fall of the Latin Square in marketing: A cautionary tale](https://www.researchgate.net/publication/241239470_The_rise_and_fall_of_the_Latin_Square_in_marketing_A_cautionary_tale)

**Platform data availability**
- [About Instagram Insights — 100-follower demographic threshold](https://help.instagram.com/788388387972460/)
- [TikTok Analytics and Audience Insights Guide 2026](https://influenceflow.io/resources/tiktok-analytics-and-audience-insights-the-complete-guide-for-2026/)

**Internal project sources (this repo)**
- [`03-audience-models.md` §6 — CTR-lift detection cost math (15,478 / 85,284 impressions; $143 / $791 at entertainment CPM)](03-audience-models.md)
- [`03-audience-models.md` §1 — Meta's GEM / Andromeda / Lattice ad stack](03-audience-models.md)
- [`03-audience-models.md` §9.5 — "Learning Limited" / ~50 conversions/week per ad set](03-audience-models.md)
- [`05-account-acquisition.md` — probe/niche account growth and risk realities](05-account-acquisition.md)
