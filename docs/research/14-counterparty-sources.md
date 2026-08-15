---
title: "Where the other four channels come from — and the one that has no source"
subtitle: "The counterparty index holds 14,173 rows and cannot address a single one of them, because no harvest stage has ever written a contact route. This surveys the public sources that could stock the four empty channels and non-US radio, ranks them by what they actually deliver, and concludes that playlist curators — the most commercially valuable channel — have no legitimate bulk source at all, for a structural reason rather than an accidental one."
status: "FINDINGS — 2026-08-13. Every number marked *measured* in this document was taken from the live source on the date given, by the command quoted beside it. Podcasts, playlist curators and non-US radio were researched to depth; press and sync were not, and §7 and §8 say so rather than padding. Nothing here has been built."
date: "2026-08-13"
---

## 0. The sentence this whole document is about

**`contact_route` has zero harvest-written rows. All 14,173 counterparties are describable and unaddressable.**

That is not a bug and it is not an oversight. It is the deliberate consequence of a rule the codebase has held consistently, and it is worth seeing how consistently, because the discipline is the reason the gap is clean rather than poisoned:

- `fcc.py`'s header: the register *"does not know the music director's name or address, and no public dataset does"*, so the loader produces the spine and leaves the route to a later step.
- `agents.py:1941`, on Radio Browser: *"The website is a `presence`, not a `contact_route`. A homepage is a surface…"*
- `agents.py:2307`, on Podcast Index: *"`contact_route` is deliberately not written"*, which `023_podcast_source.sql` *"names as the point rather than"* an omission.

Grep the tree for `INSERT INTO contact_route` and you get two hits. One is `ingest.py:574`, the send self-test. The other is a test fixture. Nothing else in the system has ever written a way to reach anybody.

So the work programme is not "find more counterparties." There are already fourteen thousand of them and the shortlist ranks them fine. The work programme is **find sources that carry a route the publisher themselves declared**, because `018_contact_route.sql` will accept those as `measured` and refuses, by design, to accept anything guessed:

> `inferred` is a guess, above all a pattern guess like `music@<domain>`, and a guessed address is the single fastest way to earn a spam complaint, so it is labelled and the sender is expected to refuse it.

Every source below is judged against that one question first. A source that yields fifty thousand names and no declared route scores below one that yields two hundred names each carrying an address its owner published.

---

## 1. The bottom line

| # | Opportunity | Yield | Route? | Effort | Verdict |
|---|---|---|---|---|---|
| **1** | **Podcast feed enrichment** — fetch the RSS the index already gives us, read `<itunes:owner><itunes:email>` | **~89% of music feeds** carry a publisher-declared address (measured, n=45) | **Yes — `measured`** | **Smallest in the list.** Source, seeder, agent and migration all already exist | **Do this first** |
| **2** | **Non-US radio via Radio Browser** | **53,297 non-US stations** (measured); 95–99% carry a homepage | Homepage only — a `presence`, not a route | **Zero new code.** `--index-streams DE,GB,FR` works today | **Do this second** |
| 3 | Press / music writers | Unquantified — not researched to depth | Mostly guessed | One loader, target unclear | Defer; see §7 |
| 4 | Sync supervisors | Small, and gated | **No** | — | **Dead end for bulk harvest** (§8) |
| 5 | **Playlist curators** | 12M names visible via vendors; **zero addresses** | **No — structurally** | — | **Dead end. No legitimate source exists** (§5) |
| — | UGC creators | — | — | — | Already answered by Pillar 10 §4 (§9) |

**The single best opportunity is podcast feed enrichment,** and its dominance is not close. It is the only item on the list that produces a `measured` contact route, and it is simultaneously the cheapest, because `023_podcast_source.sql` and `027_enable_podcasts.sql` already shipped the source, the manifest and the agent. The remaining work is one HTTP GET and one XML field.

**The most surprising finding is that playlist curators have no source at all** — not an expensive one, not a legally awkward one, none. §5 argues this is structural: the contact route is the entire product of the intermediaries that sit in this market, so nobody gives it away.

---

## 2. What "one loader" costs here, measured against the two that exist

Every effort estimate below is calibrated against `fcc.py` and `radiobrowser.py` rather than guessed, because the shape of a loader in this codebase is unusually fixed:

| Piece | Where | Size |
|---|---|---|
| Adapter module, stdlib only | `spindle/<source>.py` | `fcc.py` 312 lines, `radiobrowser.py` 184, `wikipedia.py` 152 |
| Seeder — one lead per unit of work | `ingest.py` | ~30 lines (`index_streams` is 35) |
| `_fetch_` / `_write_` agent pair | `agents.py` | ~150–200 lines |
| Registry entry | `agents.REGISTRY` | 1 line |
| Migration — `source_manifest` + `agent_manifest` rows | `platform/schema/0NN_*.sql` | ~40 lines of SQL under ~90 lines of argument |

Stdlib only is a hard constraint, not a preference — these modules run inside the console Lambda's bundle. A source needing `lxml`, `boto3` or a Spark job is a different and much larger proposition, which is the main reason Common Crawl does not appear in §1.

So **"one loader" is roughly 400–600 lines including its migration**, and the dominant cost is not the code. It is the argument in the header about what class each field's provenance belongs to, which is the part `fcc.py` spends thirty-six lines on before importing anything.

---

## 3. The test a source has to pass

Four questions, in order. A source that fails the third is worth far less than its row count suggests, and most of the sources that look attractive fail exactly there.

1. **Is it public and do its terms permit this use?** Not "would anyone notice" — Pillar 10 §4's standing rule is *no scraper, ever*, and its reasoning is enforcement rather than law: a ban costs us the accounts the campaign runs on.
2. **Does it carry evidence of what the entity is,** or only a name? A name is what produced *"Halloween Radio"* when shortlisting *"Hallow Youth"*, the failure `profiles.py` rule 2 exists about.
3. **Does it carry a route the publisher declared?** This is the question that reorders the whole list.
4. **Can the loader be honest about provenance** — is it clear, field by field, which of `measured` / `inferred` / `asserted` applies?

---

## 4. Podcasts — the finding, and it is already three-quarters built

### 4a. What was measured

The claim in `podcastindex.py`'s header is correct and incomplete, and the gap between those two things is the opportunity:

> **What is deliberately absent: an email address.** Podcast Index does not publish `<itunes:owner><itunes:email>`, and this module does not manufacture one from the show's domain.

Both halves are true of the *API*. But the API returns the **feed URL** — `023_podcast_source.sql` lists `feed_url` and `url` in the source's `emits` array — and the feed itself is a public XML document that Apple's own directory requires to carry an owner email for submission.

Measured 2026-08-13, against 45 music-category podcast feeds taken from the iTunes Search API and fetched directly:

```
feeds fetched            45   (0 errors)
carrying <itunes:owner><itunes:email>   40   (89%)
distinct email domains   25
```

The distribution matters more than the headline, because the obvious objection is that these are all hosting-platform boilerplate. They are not:

| Kind of address | Count | Examples |
|---|---|---|
| Independent / personal | 9 | `donshor@gmail.com`, `anapbm@gmail.com` |
| The show's own domain | ~21 | `contact@switchedonpop.com`, `info@yfbspod.com`, `andrew@thenationalpep.co.uk`, `ben@impressions.fm` |
| Broadcaster / network | 8 | `podcasts@npr.org`, `Maggie.Gourville@prx.org`, `ringerfeed@spotify.com` |
| **Hosting-platform boilerplate** | **2** | `feeds@spreaker.com`, `anchor.fm` |

Two of forty. The gmail tier is the interesting one: those are exactly the independent, single-presenter music shows where — per `023`'s own argument — *"there is no programming department between a pitch and the ear that hears it."*

### 4b. Why this provenance is `measured` and not a stretch

`018` accepts `measured` for *"a route the source published."* An `<itunes:owner><itunes:email>` element is a route the *publisher* wrote into their own feed for the express purpose of being contacted about the show — Apple requires it precisely so the directory can reach the owner. It is stronger evidence than an address scraped off a page, because there is no ambiguity about whose address it is or what it is for.

And the feed document is retained as the evidence: `contact_route.document_id` already points at `party_document`, so the route can be argued with later on the same terms as a fact.

### 4c. What is left to build

Genuinely small, and this is the whole case for ranking it first:

- **`source_manifest` row** — exists (`023`), `auth = 'key'`, `cost_class = 'free'`.
- **`agent_manifest` row** — exists, and `027_enable_podcasts.sql` already flipped it to `enabled = true`.
- **`agents.REGISTRY` entry** — exists; `index_podcasts` is wired.
- **The adapter** — exists, 550 lines, with the provenance argument already written.
- **Remaining:** fetch `feed_url`, parse one XML element, write one `contact_route` row with `provenance = 'measured'`, `addressee` from `<itunes:name>`, and the feed as the `party_document`. Add `contact_route` to the stage's `writes` array in a new migration.

That is an afternoon, not a week, and it produces **the first contactable counterparties the system has ever had.**

### 4d. Two honest caveats

- **The ~4.4M feed count in `podcastindex.py`'s header is unverified against the live API today.** `api.podcastindex.org/api/1.0/stats/current` requires the key I do not hold. The count is carried forward from the adapter's own header, not re-measured.
- **The n=45 sample is chart-ordered and biased toward larger shows.** The iTunes Search API returns results by relevance/popularity, so a long-tail sample would likely show a lower rate. Directionally the finding is strong; treat 89% as an upper estimate for the tail.
- **The iTunes Search API caps at 100 results per query** regardless of the `limit` parameter, with no offset (measured 2026-08-13: `limit=200` and `limit=250` both returned `resultCount: 100`). It is a term-fanout source, not an enumerable one — which is why Podcast Index remains the right enumeration path, and `ingest.feed_id_range` already implements that pagination.

---

## 5. Playlist curators — no source, for a structural reason

This is the channel a label would most want stocked, and the answer is that no legitimate bulk source exists. That conclusion is worth more than a padded list, so here is the evidence for it.

### 5a. Spotify is closed three separate ways

The Spotify Developer Terms forbid this use in three independent clauses, any one of which would be sufficient (fetched 2026-08-13 from [developer.spotify.com/terms](https://developer.spotify.com/terms)):

| Clause | Wording | What it kills |
|---|---|---|
| **IV.3.1.a** | *"you may not store, aggregate or create compilations or databases of Spotify Content, other than as strictly necessary to operate your SDA"* | Building a curator index at all |
| **V.6** | *"You will not email Spotify users unless you obtain their explicit consent or obtain their email address and permission through means other than Spotify"* | Contacting anyone found this way |
| **IV.2.4.a** | *"Do not…use any robot, spider, site search/retrieval application…to retrieve, duplicate, or index any portion of the Spotify Service or Spotify Content"* | The fallback of scraping instead |

Clause V.6 is the decisive one and it is unusually explicit. It does not merely restrict the data — it restricts *the act we want to perform*, and it does so even if the address came from elsewhere, unless permission also came from elsewhere.

Separately, the November 2024 deprecation removed `audio-features`, `audio-analysis`, `recommendations`, related-artists, featured-playlists and category-playlists for all new applications, and [as of 2026 no replacement has shipped](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api). Only apps that already held extended quota are unaffected. So even the evidence-of-what-this-playlist-is half of the problem has got harder, independent of the contact question.

### 5b. The vendors have the names and say plainly that they do not have the addresses

Chartmetric indexes on the order of **12 million curators** across Spotify, Apple Music, Tidal and Deezer, filterable by genre and follower count — an excellent answer to question 2 of §3 and a complete non-answer to question 3. Its own help centre states it does not hold curator email addresses and cannot make introductions, offering publicly-known social accounts instead. *(Read from search indexing on 2026-08-13; the help page renders via JavaScript and returned a title-only stub to direct fetching, so the wording is reported rather than quoted — treat the exact phrasing as **unverified**, the substance as consistent across every source found.)*

That is the largest vendor in the category, at $140/mo, saying the thing we need is the thing it does not have.

### 5c. Why nobody has it: the route *is* the product

SubmitHub hosts roughly **20,000 curators** and Groover a comparable European network at about €2 per submission. Neither publishes a directory or a developer API for extracting one, and this is not an oversight. **Their entire business is holding the contact route and renting access to it one submission at a time.** A public curator directory with working addresses would destroy the market it was extracted from, which is why the equilibrium is stable and why no free source has appeared to break it.

This is the difference between "we have not found the source yet" and "the source cannot exist in this shape," and it is the reason to stop looking rather than to look harder.

### 5d. What is left, and it is not a loader

The compliant path to curators is the one that was always compliant: a curator who publishes a submission form on their own site has declared a route, and reading it is legitimate. But there is **no registry of such curators** — finding them is the hard part, and the only index of them is owned by the intermediaries in §5c.

**Recommendation: do not build a curator loader.** If the channel must run, it runs through SubmitHub/Groover as a *paid channel* — which is a `contact_route` with `channel = 'form'` and an `addressee` naming the platform, not a harvest — or through human curation at a volume that does not need a pipeline. Both are decisions for a person, not a stage.

---

## 6. Non-US radio — a config change, not a loader

The cheapest genuine expansion available, because the loader is already written and already global.

### 6a. The yield, measured

Against `de1.api.radio-browser.info`, 2026-08-13:

```
countries with an ISO code   241
total stations            61,024
United States              7,727
non-US                    53,297      ← 7.9x the current stream index
```

Top of the tail, with coverage of the two fields that decide whether a row is useful:

| Country | Stations | With tags | With homepage |
|---|---|---|---|
| US | 7,724 | 69% | 98% |
| Germany | 6,132 | 68% | 95% |
| Great Britain | 2,357 | 58% | 98% |
| France | 2,785 | 52% | 97% |
| Australia | 2,068 | **87%** | 99% |
| Canada | 1,545 | 78% | 98% |
| Italy | 1,773 | 69% | 98% |
| Netherlands | 1,456 | 57% | 98% |
| Spain | 1,375 | 63% | 96% |
| Brazil | 1,621 | 62% | 99% |
| Ireland | 282 | 59% | 98% |
| New Zealand | 240 | 68% | 99% |
| South Africa | 226 | 36% | 99% |

**Non-US rows are not worse rows.** Homepage coverage is 95–99% everywhere, matching or beating the US; tag coverage is comparable and Australia is markedly better. The quality objection to expanding does not survive the measurement.

As corroboration that the source is live and the existing measurement was sound: `radiobrowser.py`'s header records 7,709 US stations, 5,335 with tags and 7,544 with a homepage on 2026-08-11. Two days later the same query returns 7,724 / 5,343 / 7,558 — a live, slowly-growing index, and a numbers match close enough to trust both readings.

### 6b. The effort is zero

`ingest.index_streams` already takes a country list, derives its page count from Radio Browser's own `/json/countries` response rather than a hardcoded number, and is already exposed on the CLI:

```
python -m spindle.ingest --index-streams DE,GB,FR,AU,CA,NL,IT,ES,IE,NZ
```

That command works today. There is **no new adapter, no new agent, no new migration** — the seeder writes one lead per 100-station page and `index_streams` claims them exactly as it does for the US.

The real costs are the ones the cluster imposes: `radiobrowser.PAGE` is 100 *because that was measured*, not chosen, against a CockroachDB BASIC deployment whose request-unit budget is the binding constraint. 53,297 stations is ~533 leads. That is a budget question and a wall-clock question, not an engineering one.

### 6c. Terms

Radio Browser requires no key, asks only for *"a speaking http agent string"* and for click-throughs to be reported — both of which `radiobrowser.py` already honours. **The database licence is not stated on the API landing page**; the project describes itself as free and open source and grants use in free and non-free software, but I could not find an explicit CC0/ODbL declaration. Marked **unverified** — worth resolving before the index becomes a commercial asset, though nothing found suggests a restriction.

### 6d. The part that is not free

Non-US expansion is cheap in code and **not cheap in jurisdiction**. See §10. Every one of Germany, GB, France, Italy, Netherlands, Spain and Ireland is a GDPR/PECR jurisdiction, and that is 16,000 of the 53,297 rows before counting the rest of the EU. The rows can be indexed freely; whether they can be *mailed* is a separate decision that needs a human.

---

## 7. Press and writers — not researched to depth

**Stated plainly rather than padded:** the sub-agents commissioned for this channel did not survive a process restart, and the re-run was budgeted toward podcasts, curators and non-US radio. What follows is thin and should be read as a starting point, not a finding.

What is known:

- **The podcast result in §4 is partly a press result.** `023`'s categorisation already separates shows that play records from shows that talk about them, and `podcastindex.py` is explicit that Apple's `Music` category collects both — *"A talk show about the music industry and a show that plays records both land in `Music`."* The music-interview and music-review shows in that bucket are press counterparties with a declared email, obtained by the same GET as §4. **This may be the entire press channel, obtained for free.** It deserves the next hour of research, not a new loader.
- **Muck Rack, Cision, Prowly and Roxhill** sell journalist contact databases. Not investigated: pricing, API availability, redistribution terms, and — the question that matters most for a GDPR-exposed channel — the *provenance* of their contact data. **Unverified in all respects.**
- **Common Crawl** could in principle yield music-blog domains and their published contact pages. Ruled out on effort rather than terms: it is a Spark-scale job with S3 egress, and every loader in this codebase is stdlib-only and runs in a Lambda bundle (§2). Wrong shape for this system today.

**Recommendation: do not open a press workstream yet.** Do §4 first and then measure how many of the resulting shows are press rather than radio-adjacent. That number decides whether press needs a source at all.

---

## 8. Sync — a dead end for bulk harvest, and confidently so

Also researched shallowly, but the two decisive facts were both confirmed directly and both point the same way.

**The credits data is licence-blocked.** IMDb's non-commercial datasets are the largest openly-downloadable film/TV credits corpus, and [IMDb's own developer page](https://developer.imdb.com/non-commercial-datasets/) states the subsets are available *"for access to customers for personal and non-commercial use."* This is a commercial product. That disqualifies the source outright — not on a technicality we could argue about, but on the licence's plain purpose.

**The directories are gated.** Both music-supervisor guild directories were checked and both refuse public access:

- The US [Guild of Music Supervisors](https://www.guildofmusicsupervisors.com/) — directory is members-only, or by application as a client seeking to hire.
- The [UK & European Guild](https://www.guildofmusicsupervisors.co.uk/directory/) — identical gate; fetching the directory URL returns a client-registration form, not listings.

**And the structural point is the same as §5c, in a stronger form.** Sync is a relationship business in which the supervisor's inbox is their scarcest asset and a cold pitch is close to worthless. Even a perfect harvest of names and addresses would produce a channel with a near-zero response rate. TMDB's crew data would give names for free; names without routes are §3 question 3 failing, and here the route is withheld deliberately by people whose job depends on withholding it.

**Recommendation: sync is human-curated by nature.** Do not build a loader. If the channel runs, it runs on `asserted` routes typed in by a person who already knows the supervisor — which `018` supports today, and which is the correct shape.

---

## 9. UGC creators — already answered, and the answer has not changed

Not re-researched, because Pillar 10 settled it and nothing found this week disturbs it:

- **[Pillar 10 §1](./10-creator-indexing.md):** *"There is no endpoint on any platform that returns an arbitrary creator's follower demographics."* Audience data flows only with the creator's own OAuth grant — no exception, no partner tier, no price.
- **[Pillar 10 §4](./10-creator-indexing.md):** *no scraper, ever* — on enforcement grounds rather than legal ones, which is the reasoning `fcc.py`'s header carries forward as the standing rule.
- The affordable vendor tier (Modash, HypeAuditor, Heepsy) is scraped-and-estimated, which makes its contact routes `inferred` at best and of unknown provenance for GDPR purposes.

The one thing worth adding: **`018`'s design means the UGC channel is not blocked on a source, it is blocked on a route class.** A creator who publishes a business email in their bio has declared a route; a creator who has not, has not. There is no bulk path to the first group and no acceptable path to the second.

---

## 10. Jurisdiction, and the column that already exists

Not legal advice. What follows is what the regulator publishes, and the flag for where a human has to decide.

The UK's PECR draws a line the US CAN-SPAM regime does not: [the ICO's B2B guidance](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/) states that the electronic-mail marketing rule does not apply to **corporate subscribers** — a registered company, an LLP, a government body — so consent under PECR is not required to mail a company's general business address. But:

- **Sole traders and non-LLP partnerships are individual subscribers** and get the full protection.
- **A named individual at a corporate address may themselves be the subscriber**, which changes the analysis for `jane.smith@station.fm` versus `info@station.fm`.
- **PECR is not the whole question.** UK GDPR still applies to the personal data regardless, which means a lawful basis and a notice obligation independent of whether consent was needed.

**The consequence for this schema is unusually neat: `contact_route.addressee` is already the column that carries this distinction.** `018` defined it as free text — *"'music_director', 'general', 'submissions', 'program_director'"* — for taxonomy reasons, and it turns out to be exactly the field a jurisdiction rule would filter on. A route with `addressee = 'general'` and a route naming a person are different legal objects, and the table can already tell them apart.

**What needs a human decision, stated as decisions rather than as drift:**

1. Whether non-US rows may be *mailed* at all, or only indexed, pending advice.
2. Whether a policy of "role addresses only outside the US" is adopted — which the schema can enforce and which would sidestep most of the individual-subscriber question.
3. Whether §4's podcast routes change the analysis, since a gmail address on a one-person show is plainly an individual subscriber even when the show is a business.

Item 3 is worth flagging as a live tension: the highest-value routes in §4 are the independent shows, and those are also the ones with the least corporate cover. It does not block the US portion.

---

## 11. Why generic homepage scraping is the wrong shape of solution

The obvious alternative to all of the above is a `harvest_contact` stage that visits the 7,558 US station homepages already in `presence`, finds a `mailto:`, and writes it. It is tempting because the homepages are already there. It should not be built, and the evidence against it is a sample I ran on the way to something else.

**Measured 2026-08-13**, 60 US station homepages sampled at random from Radio Browser's click-ranked list, 54 fetched successfully, single page each, no `/contact` follow:

```
homepages fetched                 54
yielding any email address        11    (~20%)
```

Twenty per cent is a poor but not fatal yield. **The yield is not the problem. The contents are.** Among those eleven:

| Address found | What it actually is |
|---|---|
| `email@example.com` | Placeholder text in an unedited template |
| `team@latofonts.com` | **A font vendor's address, lifted from a CSS attribution comment** |
| `betrayalpod@gmail.com` | A podcast advertisement, appearing on two *unrelated* iHeart station pages |
| `donate@bassdrive.com` | Real, but a donations address, not a music submission route |
| `board@wkcr.org` | Real and genuinely useful — a college station's board |

So of eleven hits, at least three are not addresses of the station at all, and one of those three appears on multiple stations — meaning a naive stage would confidently attach the same stranger's podcast to several different broadcasters.

**Why that is disqualifying rather than merely untidy.** `018` makes routes deliberately hard to remove:

- The uniqueness constraint is on `(tenant_id, party_id, channel, value)` and the loaders use `ON CONFLICT DO NOTHING` rather than `DO UPDATE`, *"so a harvester that re-finds a public address must not be able to resurrect a route somebody asked us to stop using."*
- `opted_out` is terminal and no discovery stage may overwrite it.

Those properties are exactly right for their purpose and they cut both ways: a table designed so that routes cannot be quietly overwritten is a table where **writing the wrong route is durable.** A stage that puts a font vendor's address into `contact_route` has not made a mess that a re-run cleans up; it has made a permanent claim that the system will later treat as evidence.

And the provenance class would have to be honest about it. An address found somewhere on a page is not *"a route the source published"* — it is a route *this module inferred was the right one*, which makes it `inferred`, which `018` says the sender is expected to refuse. **A stage whose output the sender refuses by policy is a stage that costs request units and delivers nothing.**

**The general principle, which is the reason to rank §4 first:** the difference between a publisher-declared route and a page-scraped one is not a difference of degree. `<itunes:owner><itunes:email>` is an element whose *only purpose* is to say "contact the owner here." An address in the page body is a string that happens to match a pattern. The first is evidence; the second is a guess wearing evidence's clothes, which is the precise thing `SCOPE-RESET §2a` rule 1 exists to prevent.

If a homepage stage is ever built, it should read **structured declarations only** — `mailto:` links inside a page marked up as a contact page, `schema.org/Organization` `email` properties, `<link rel="me">` — and abstain otherwise, in the same way `wikipedia.py` writes nothing when no `format =` line is found.

---

## 12. Recommended order of work

**1. Podcast feed contact enrichment.** (§4) The only item that produces a `measured` route. Cheapest, because four of the five pieces already exist. Produces the first contactable counterparties in the system's history. Do it first and do it this week.

**2. Non-US radio seeding, US-mailable only.** (§6) Zero code. `--index-streams DE,GB,FR,AU,CA,NL,IT,ES,IE,NZ` adds **20,016 stations** at equal or better field coverage — and the full 53,297 is available whenever the request-unit budget allows. Seed the index now; hold the mailing decision (§10) separately, because indexing is not sending and coupling them delays a free win behind a legal question.

**3. Measure how much of §4's output is press.** (§7) An hour of counting against the podcast categories already stored, which decides whether the press channel needs a source at all. Cheaper than researching press directories and strictly more informative.

**4. Nothing for curators, sync, or UGC.** (§5, §8, §9) Three channels, three different reasons, one shared conclusion: the missing thing is a contact route that no public source holds, and in all three cases the reason it is missing is that somebody's business model depends on it being missing. Build nothing. If these channels run, they run on `asserted` routes from a human, which the schema already supports.

The honest summary of that ordering: **two of the five gaps close cheaply and three do not close at all.** Reporting the three as closeable would have been the easier document to write and would have cost a week each to discover otherwise.

---

## 13. What is unverified

Listed rather than buried, per the house rule.

| Claim | Status |
|---|---|
| Podcast Index holds ~4.4M feeds | **Unverified today.** Carried from `podcastindex.py`'s header; `stats/current` requires a key not held |
| Podcast Index Music-category feed count | **Not measured.** Decides §4's true yield and should be measured before the work starts |
| Chartmetric's exact wording on curator emails | **Unverified.** Help page is JS-rendered; substance corroborated across sources, phrasing is not quoted |
| Radio Browser database licence | **Unverified.** No explicit licence found on the API landing page |
| 89% owner-email rate on long-tail feeds | **Upper estimate.** n=45, chart-ordered, biased toward larger shows |
| Muck Rack / Cision / Roxhill terms, pricing, data provenance | **Not researched** (§7) |
| TMDB crew data as a sync source | **Not researched.** Ruled out on the route question (§8), not on the data question |
| Whether non-US rows may be mailed | **Open decision for a human** (§10) |

---

## 14. Sources

- [Spotify Developer Terms](https://developer.spotify.com/terms) — §IV.2.4.a, §IV.3.1.a, §V.6, fetched 2026-08-13
- [Introducing some changes to our Web API](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) — Spotify, 2024-11-27
- [Chartmetric — Curator Analytics](https://chartmetric.com/features/curator-analytics) and [Pricing](https://chartmetric.com/pricing)
- [IMDb Non-Commercial Datasets](https://developer.imdb.com/non-commercial-datasets/) — licence, fetched 2026-08-13
- [Guild of Music Supervisors](https://www.guildofmusicsupervisors.com/) · [UK & European Guild directory](https://www.guildofmusicsupervisors.co.uk/directory/) — both gated, checked 2026-08-13
- [ICO — Business to business marketing](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/) — PECR corporate vs individual subscriber
- [Radio Browser API](https://api.radio-browser.info/) — terms and UA convention, fetched 2026-08-13
- Radio Browser `/json/countries` and `/json/stations/search` — measured directly, 2026-08-13
- iTunes Search API — `https://itunes.apple.com/search?media=podcast` — measured directly, 2026-08-13
- In-repo: [`SCOPE-RESET.md`](../SCOPE-RESET.md) §2a · [`10-creator-indexing.md`](./10-creator-indexing.md) §1, §4 · `platform/schema/018_contact_route.sql` · `023_podcast_source.sql` · `026_role_backfill.sql` · `027_enable_podcasts.sql` · `spindle/fcc.py`, `radiobrowser.py`, `podcastindex.py`, `ingest.py`
