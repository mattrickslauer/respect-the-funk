---
title: "The Label's Mind (rtf.skill/v1)"
subtitle: "MEMORY-SPEC closes the loop but leaves the judge in a terminal. A Mind moves that judge to email, where the label already is — and the write-back that was the hard part becomes a reply. This is the third hackathon track, and the only one that requires no new infrastructure."
status: "DRAFT — adds a third track to the sequence in MEMORY-SPEC §0. Depends on MEMORY-SPEC §4–§6 shipping first; nothing here is buildable before Aug 18. ⚠️ Written against unverified rules — see §9."
date: "2026-07-27"
deadline: "2026-08-28 (time of day unconfirmed)"
---

## 0. The decision

**Three hackathons, sequenced.** MEMORY-SPEC §0 committed to two; this adds a third that consumes what the first two produce.

| | Backblaze | CockroachDB × AWS | **Creative Minds Jam #1** |
|---|---|---|---|
| Deadline | **2026-08-03** 17:00 EDT | **2026-08-18** 17:00 EDT | **2026-08-28** |
| Pool | — | — | **US$10,000**, split ~8–10 ways |
| Requires | B2 + Genblaze | ≥2 CockroachDB tools + ≥1 AWS service | A persistent agent on **Minds by Animoca Brands** |
| Must demo | Generative pipeline | Agentic memory design | **Memory + autonomous follow-up across sessions** |
| Depends on | — | — | **MEMORY-SPEC §4–§6** |
| This repo today | shipping | in plan | needs one auth provider (§5) |

The build order is forced rather than chosen: this track's demo *is* MEMORY-SPEC's loop with a different judge surface, so it cannot be built before that loop exists. **Aug 3 → Aug 18 → Aug 28.** MEMORY-SPEC §0's rule extends unchanged: nothing here touches the Aug 18 submission path before Aug 18.

**Why it is worth a third submission at all.** 22 registrants at time of writing, against a prize structure that pays out roughly 8–10 times — per-track winner and runner-up, plus a grand prize and a student prize. "Hong Kong" is branding; the rules reportedly state that being Hong Kong based grants no advantage. ⚠️ Every number in this paragraph is from a secondary summary, not the rules page — see §9.

---

## 1. Why this is the honest fit and not a bolt-on

The same test MEMORY-SPEC §1 applied to itself: does the product need this, or does the hackathon need it?

[MEMORY-SPEC §6](./MEMORY-SPEC.md) draws the loop — retrieve → generate → measure → judge → distil — and then concedes where it breaks:

> Every arrow already exists as a script except the last one. `generate_stills.py` generates, `check_likeness.py` measures, **a human judges in the terminal** — and then the loop is cut, because nothing writes back.

Read that sentence as a product problem rather than a plumbing problem. The judge is a label — an A&R or marketing lead who does not have a terminal open, is not going to run `check_likeness.py`, and whose verdict is the single input the memory layer cannot synthesise. **The write-back is not blocked on a database. It is blocked on the judge having somewhere to answer.**

A Mind is that somewhere, and it arrives with the three properties the loop needs:

| The loop needs | Minds provides, natively |
|---|---|
| A judge reachable where they already are | Email (primary) and Telegram. No app to install. |
| Verdicts that survive between sessions weeks apart | Long-term + short-term memory in the Soul; persists across sessions by construction |
| Someone to *ask* without being prompted | Autonomous action — monitors, acts on conditions, follows up while the user is offline |

So the deliverable is not "RemixKit, plus an agent." It is **the missing judge step in a loop this repo already committed to** — which is exactly the argument MEMORY-SPEC §1 made about memory itself, one layer further out.

**The correspondence is close enough to be worth stating plainly.** The jam requires an agent that demonstrates memory and autonomous follow-up across sessions. MEMORY-SPEC §6 requires a judge whose verdicts accumulate across generations. These are the same requirement seen from two directions, and only one build satisfies both.

---

## 2. What the platform actually is — checked, because it decides the cost

The assumption worth killing early: **Minds is not a deploy target.** RemixKit is not ported to it, does not run on it, and does not leave AWS. Minds is a hosted agent platform that is *extended* by publishing capabilities, and the extension path for an existing HTTP API is explicit in the builder docs:

> **Q: Can I build a Skill around my own internal tool?**
> A: Yes. Paste its API docs into the chat and describe what you want. Your Mind builds the Skill around that API.

Structure, as the docs define it:

| Term | Is | Developer term |
|---|---|---|
| **Tool** | One capability — typically a single API endpoint | Tool Schema |
| **Skill** | A playbook — the routine the Mind follows over time | Skill Playbook |
| **App** | A bundle of Tools under one identity | App Manifest |
| **Bazaar** | The marketplace others equip from, with a leaderboard | Registry Offering |
| **Connection** | Where the API key lives — platform-held, never in the Mind | — |

A Skill is authored by describing the outcome in plain language; the Mind assembles all four artifacts. There is also a JSON-first CLI (`@animocabrands/minds-cli`) and a Node client library (`@animocabrands/minds-client-lib`, `MINDS_BUILDER_API_KEY`, Node 22+) for the parts that want to be scripted or embedded.

**Consequence for the plan: the integration surface is one auth provider and a set of endpoint descriptions.** That is the entire reason this track is cheap.

### 2a. ✅ The pass-through mechanism, confirmed against the live Bazaar

This was §9's open technical risk and it is now closed, from evidence rather than from a FAQ. The public Bazaar catalog is queryable with no API key (`minds bazaar search`), and the pattern that wraps an arbitrary external REST API has a name — **`HTTP_Execute`** — and real adoption:

| Skill | Equipped | Wraps |
|---|---|---|
| Hi-Fidelity Minds Video Production | **106** | Minds Video via the Video Bridge API |
| GitHub_Sovereign_Bridge_v1 | 31 | The GitHub REST API |
| The_Connector_MCP_Client_v1 | 27 | Any MCP server, JSON-RPC 2.0 over HTTP/S |
| Nansen_Intelligence_v1 | 13 | Nansen Labs API v1 + v2 |
| Classical_Knowledge_Engine | 2 | Open Opus + MusicBrainz, normalised to one JSON envelope |

The last one is close to a reference implementation for §3: it routes operations across namespaces, executes HTTP via `HTTP_Execute`, and normalises every response into a stable envelope with a consistent `items` array. That is precisely the shape a RemixKit Skill wants, and it means the Tool table in §3 is a description exercise rather than an engineering one.

**Two consequences that were not in the plan before:**

- **A Mind can speak MCP.** `The_Connector_MCP_Client_v1` is a stateless MCP JSON-RPC client. [MEMORY-SPEC §7](./MEMORY-SPEC.md) already commits to CockroachDB's **Cloud Managed MCP Server** as one of its two required tools — so the Aug 18 deliverable is directly reachable by the Aug 28 agent, and Q4 in natural language becomes a path the Mind can take without any new adapter. The two tracks share a component rather than merely a repo.
- **Media goes back over Telegram natively.** `Telegram Native Media Bridge (Video, Audio, Images)` — 31 equips — resolves the human's `chat_id` and sends photo/video/audio back. §3's judge loop needs the label to *see* the stills before rejecting one; this is how, without building a review UI.

⚠️ **Minds generates video natively, and the most-equipped skill on the board does exactly that.** "Minds Video" via the Video Bridge API sits at 106 equips. This cuts both ways and should be decided rather than discovered: it is the obvious way to make Minds *a core product layer* rather than a notification pipe (§10 #2, the investment criterion) — and it is also the thing a judge might see as overlapping RemixKit's Genblaze pipeline. Recorded as §10 #7.

### 2b. Credentials — where a Mind ID comes from

Three steps, in order, because each needs the one before it:

1. ✅ **Create a Mind** at [hellominds.ai](https://www.hellominds.ai/) — free, email sign-up, no wallet. The Concierge onboards it and emails when it is awake; a One-Click template is the fast path. The docs are explicit that this is the prerequisite: *"You need at least one Mind before Builder Tools can route messages."* **Done 2026-07-27.**
2. **Create a Builder API key** in the Builder console at [build.hellominds.ai/en/console](https://build.hellominds.ai/en/console) → **Credentials**. Name it, set an expiry (90 / 180 / 270 days / 1 year), and copy the token — **it is shown only once.** Store as `MINDS_BUILDER_API_KEY`. The key is a JWT carrying `humanId`, which the CLI parses rather than asking for.
3. **Read the Mind ID** — it is not issued separately, it is the identifier of the Mind from step 1, and `list`'s own help says so: *"List Minds on your builder account (mindId + name for chat create)."*

   ```sh
   npx @animocabrands/minds-cli@latest list | jq '.items[] | {mindId, name}'
   ```

⚠️ **The CLI declares `node >=22`; this repo's dev machine is on v20.19.6.** `npx` runs it anyway with an `EBADENGINE` warning and the read-only commands work, but do not assume the authenticated write paths are as forgiving. Node 22 before day 1.

⚠️ **Builder access may be gated.** The "Unlock Builder Access" control renders `disabled` in the docs page's server HTML, and the site carries a builder-access form asking about current activity on Minds and which primitives you have personally used. That is consistent with a sign-in gate that resolves on login — but also with an approval queue. If it is the latter, it has lead time and therefore belongs in the same "do it now" bucket as registration (§8), not on day 1.

⚠️ **The Builder console's "Skills & Apps" tab reads *Coming soon*.** Skills are authored by chatting with the Mind (§2), so this does not block §3 — but it does mean there may be no programmatic publish path for the Bazaar step in §8. Confirm before relying on it.

### 2c. Minds has its own memory primitives — and they overlap ours

Discovered while reading the builder docs, and it changes a design question rather than a detail. A Mind's Soul already stores:

| Minds primitive | Is | Nearest thing in [MEMORY-SPEC §4](./MEMORY-SPEC.md) |
|---|---|---|
| **Episodes** | Memories of specific past conversations | `episode` — near-identical name, narrower scope |
| **Tenets — Invariants** | Rules the Mind will never break; set by the Steward | Guardrails; closest to the rights gate in §6 |
| **Tenets — Priors** | Flexible preferences learned from real interaction, written by the Mind itself | `lesson`, and arguably `identity_negative` |

Tenets are readable and writable in plain language — *"Show me your current Tenets"*, *"Add a new Tenet: …"*.

**This is a decision, not a freebie.** Two stores can now hold the same learned fact, and letting that happen by accident is how the demo ends up unable to say where memory lives. The default position: **CockroachDB remains the system of record** — it holds the artist-scoped, tenant-partitioned, vector-indexed memory that MEMORY-SPEC §5's queries run over, and it survives the Mind being deleted. Tenets hold only what is genuinely about *this Mind's* conduct: tone, cadence of follow-up, and the §6 rights Invariants. A `lesson` about an artist's jaw drifting belongs in the database, not in a Soul.

⚠️ Stated because it cuts the other way too: a judge could reasonably ask why the memory is not in the Mind, on a platform whose headline feature is memory. §10 #6 records that as an open decision rather than pretending it is settled.

---

## 3. What gets built — the RemixKit A&R Mind

One Mind, equipped with one Skill, wrapping endpoints that already exist in [`app/remixkit/api/v1.py`](./app/remixkit/api/v1.py).

**Tools** — a thin map over the existing API, no new business logic:

| Tool | Endpoint | Exists today |
|---|---|---|
| List the roster | `GET /artists` | ✅ |
| Request a kit | `POST /kits` | ✅ |
| Check a kit | `GET /kits/{id}` | ✅ |
| Approve / reject a kit | `PUT /kits/{id}/approval` | ✅ |
| Verify provenance on an asset | `POST /verify` | ✅ |
| Record a rejection reason as memory | *new — writes `identity_negative`* | ❌ MEMORY-SPEC §4 |

**The Skill playbook** — the loop, expressed as a routine that runs over weeks:

```
  A kit finishes
        │
        ▼
  Mind emails the label: the stills, the likeness scores, what it retrieved
        │                 and applied from memory before generating
        ▼
  Label replies in plain language: "reject #3, the jaw is wrong"
        │
        ▼
  Mind writes that back as an identity_negative, scoped to this artist
        │
        ▼
  Next kit, days or weeks later: the Mind retrieves it before prompting,
  says so in the email, and the attempt count falls
```

**The autonomous half** — not follow-up on a request, but action without one. MEMORY-SPEC §5's Q4 already asks *"which songs have no approved edit?"*. A Mind that runs that query on a schedule and proposes the next release unprompted is attacking what [PRODUCT.md](./PRODUCT.md) names as the real bottleneck: *"at fifty songs that manual step, not compute, is what limits throughput."*

**The demo is the one MEMORY-SPEC already specified.** *Attempts per approved still, per artist, over time* — with the verdicts now arriving by email instead of by keyboard. ⚠️ MEMORY-SPEC §6's warning carries over intact and is not softened here: there is one artist, one song, and 22 stills. Whatever N exists on Aug 28 gets shown **labelled with its N**. A flattering curve is not a deliverable.

---

## 4. What this does to PRODUCT.md's role scope

Stated because it is a genuine tension, not a detail.

[PRODUCT.md](./PRODUCT.md) narrowed the product to **one role — the label** — and deferred role 2 (creator / fan) in full. The jam's brief is *"Build What Creators Need Next,"* aimed at content creators and the creator economy: discoverability, engagement, workflow efficiency.

**The fit is real on workflow efficiency and needs no un-deferring.** A label *is* a creator-services business, and its bottleneck is a workflow bottleneck that PRODUCT.md already measured. The A&R Mind in §3 addresses exactly that and nothing else.

⚠️ **The temptation to resist:** reading "creator economy" as an invitation to un-defer role 2 for the submission. PRODUCT.md removed 8 of ~14 tables and nearly all the architectural complexity by deferring it, ten days before this deadline. **Role 2 stays deferred.** If the jam's rules turn out to require a fan-facing surface — unverified, see §9 — that is a reason to reconsider the submission, not to reverse a scoping decision under deadline pressure.

---

## 5. The one piece of genuinely new work

RemixKit authenticates by email OTP only. A Minds Connection stores an API key and the platform injects it on the Mind's behalf, so the API needs to accept a static key for a service principal.

This is small, and the codebase already anticipated its shape. [`app/remixkit/auth/provider.py`](./app/remixkit/auth/provider.py) defines `AuthProvider` as a Protocol with `AnonymousAuth` and `OtpAuth` behind it, and [`app/remixkit/deps.py`](./app/remixkit/deps.py) says so in its own docstring:

> Auth followed exactly that shape: `auth/otp.py` plus a branch in `_build_auth`. **No route changed**, because routes already depend on `current_principal` rather than assuming who is calling.

An `auth/apikey.py` is the third instance of a pattern with two instances already. The `Principal` it returns carries `tenant_id` and a scope set — which is where the rights gate in §6 is enforced, so this file is load-bearing beyond convenience.

---

## 6. Rights — the surface gets larger, not smaller

[MEMORY-SPEC §7](./MEMORY-SPEC.md)'s caveat applies here with more force, and this section exists so that is not discovered late.

All 12 `dialogue` hooks carry `rights.source: youtube` and `people_release: false`. MEMORY-SPEC scoped the risk to a public demo URL — a passive surface a person must visit. **A Mind is an active one.** It sends email unprompted, acts while nobody is watching, and can be introduced to other Minds in a Circle. The blast radius of an over-scoped Skill is categorically worse than that of an over-shared link.

Three controls, all cheap:

1. **The scope check is the existing one.** `Principal.can(scope)` in `auth/provider.py` is where the service key's permissions are bounded. The Mind's key gets read + propose + record-verdict. It does not get publish.
2. **Nothing built from a `people_release: false` clip may leave the system.** [CLIP-SPEC](./content/CLIP-SPEC.md) rule 3, applied to the agent exactly as MEMORY-SPEC §7 applied it to the demo URL. Retrieval over those clips is fine; outbound is not.
3. **Audit the Skill on camera.** The docs supply the prompt — *"Show me what this Skill can do, what it reads, and what it can change. Flag anything it should not touch."* Running that in the demo video turns a compliance step into a scoring one, and it is the house style: `screen_clips.py` abstains rather than guesses, so the agent declares its scope rather than assuming it.

---

## 7. Cost

Unlike [MEMORY-SPEC §8](./MEMORY-SPEC.md)'s $0.00 finding, this one is not free — though it is close enough not to matter.

Minds is free to launch. Usage burns **Cognition Credits**: US$10 / 1,000, with monthly plans from Standard ($10) to Ultra ($50 / 5,000). Every reasoning step and every tool call consumes them, and an always-on monitoring agent consumes them without being asked.

**Budget US$25–50 for the track.** ⚠️ The unmodelled risk is the same shape as MEMORY-SPEC §8's: the credit cost of one *autonomous monitoring cycle* is not published, and a Skill that polls the catalog hourly for ten days could plausibly exhaust a Standard plan. Measure one cycle on day 1 before setting any schedule. Credits are tracked per Mind, and a Mind warns before it runs dry.

---

## 8. Plan — Aug 18 → Aug 28

Ten days, and the same discipline as MEMORY-SPEC §9: the dependency ships first.

**Aug 18 — Ship CockroachDB. Tag `cockroachdb-submission`.** Branch after, never before.

**Now (pre-Aug 3, ~20 minutes, the only things that cannot wait) — register, and claim credentials.** Applications open **2026-07-28**; registration is free and reserves nothing else. Pull the official rules the same day and close §9. In the same sitting, do §2b steps 1–2: create a Mind and request Builder access. Both are free, neither touches the Aug 3 path, and if builder access turns out to sit behind an approval queue, ten days is not enough runway to discover that on Aug 19.

**Days 1–2 — Prove the integration, and measure a credit.** `minds doctor`, `minds list` for the Mind ID. `auth/apikey.py` and a service principal. One round trip: a Mind calls `GET /artists` against the deployed API and reads the roster back over email. Record the credit cost of one monitoring cycle (§7). *Nothing else counts until a Mind has touched the real API.*

**Days 3–5 — The Skill and the judge loop.** Tools per §3. The playbook: kit finishes → email with stills, scores, and retrieved memory → reply parsed to a verdict → `identity_negative` written. This is the core; if everything after it slips, the submission still has its headline criterion.

**Days 5–7 — Autonomous follow-up.** Scheduled Q4 sweep, unprompted proposal of the next release, approval routed to the label. The across-sessions proof: a verdict given on day 3 must visibly change a generation on day 7.
⚠️ *Risk: "across sessions" is only convincing with real elapsed time between them. Start the clock on day 3 — the first verdict must land as early as possible so the gap is genuine rather than simulated.*

**Days 7–8 — Publish to the Bazaar.** The Skill listed and equippable. This is the distribution story and the investment story; it is also the only part of the submission that outlives it.

**Days 8–9 — Package.** Demo video, repo, documentation, the §6 scope audit on camera. Attempts-per-approved-still with its N stated.

**Day 10 — Buffer. Submit Aug 27 EOD.**

---

## 9. ⚠️ Unverified — read the rules on Jul 28

Stated as a block rather than scattered, because more of this document rests on secondary sources than is comfortable.

**The DoraHacks rules page could not be read.** It sits behind an AWS WAF human-verification wall; automated fetches return 405. Everything below is from a secondary summary or the Animoca announcement, and **none of it is confirmed**:

1. **The track list and the prize split.** ~$1,200 winner + $600 runner-up per track, a ~$2,300 grand prize, a ~$1,300 student prize. The count of tracks determines the count of payouts, which is the entire expected-value case in §0.
2. **The student prize.** Reportedly ~$1,300. **Does anyone on this team qualify?** If yes it is the highest-value-per-effort item on the board and should shape which prize the submission targets.
3. **Any "newly created during the Submission Period" clause.** CockroachDB had one. This repo's initial commit is 2026-07-26. If the same clause exists here, [MEMORY-SPEC §2](./MEMORY-SPEC.md) is already written to double as the pre-existing-work disclosure and the same table serves.
4. **Whether submitting a project entered in other hackathons is permitted.** Assumed yes; unconfirmed. This one can invalidate the track outright.
5. **Whether a fan-facing surface is required** — see §4. If it is, reconsider the submission rather than the scope.
6. **The submission deadline's time of day**, and what the deliverables are (repo? video? live demo URL?).

~~**Also unverified, and technical rather than legal:** §2's claim that a Skill can wrap an arbitrary external HTTPS API.~~ — **closed 2026-07-27.** Not by re-reading the FAQ but by querying the live Bazaar: `HTTP_Execute` is the named pass-through pattern, in production across skills with 2 to 106 equips, wrapping GitHub, Nansen, MusicBrainz, and MCP servers. See §2a. **The remaining risk on this axis is authentication shape, not capability** — whether a Connection can hold a plain bearer token for a self-hosted API, which day 1 answers.

**Status of the legal block: still open.** DoraHacks registration is complete (2026-07-27), which may make the rules page readable while signed in. It was not readable otherwise, so items 1–6 above stand until someone reads them in a browser.

---

## 10. Open decisions

1. **Licence.** Unchanged from [MEMORY-SPEC §10](./MEMORY-SPEC.md) #1 and now blocking three submissions instead of one. Apache-2.0 recommended. This needs a call before anything is public.
2. **Does the Mind get the investment pitch, or just the prize?** The programme reportedly requires Minds as *a core product layer*. A Skill wrapping an API wins prize money; it does not make that claim true. Deciding which is being aimed at changes §3's ambition and should be decided before day 3, not after.
3. **Who is the demo's label?** Respect the Funk is tenant #1 and dogfoods it — but "the label replies to an email" needs a person to actually reply, on camera, on specific days.
4. **Bazaar publication timing** — before or after judging, and under whose identity.
5. **Which prize is targeted.** Grand, a track, or student (§9 #2). They imply different demo emphases and the choice is currently unmade.
6. **Where memory lives — the database or the Soul.** §2c states the default (CockroachDB is the system of record; Tenets hold only the Mind's own conduct rules) and states the counter-argument. Needs a call before day 3, because the demo narrative depends on answering it in one sentence.
7. **Whether the demo generates through Minds Video.** §2a. It is the strongest available answer to "is Minds a core product layer or a notification pipe" (#2), and the clearest overlap with the Genblaze pipeline the Aug 3 track is judged on. Both readings are defensible; drifting into one by accident is not.

---

## 11. What this deliberately is not

- **Not a fourth product.** It is [MEMORY-SPEC](./MEMORY-SPEC.md)'s judge step, moved to where the judge is.
- **Not a reason to un-defer role 2.** See §4. The scoping decision predates this deadline and outranks it.
- **Not a replacement for the human verdict** — it is the opposite. MEMORY-SPEC §11 warns that a memory layer learning from its own unaudited scores would industrialise `check_likeness.py`'s 5/5 false-match error. This track makes the human verdict *easier to give*, which is the only safe direction to move it.
- **Not a claim that agents make a song go viral.** [BUILD-SPEC §0](./BUILD-SPEC.md)'s guardrail is unchanged: sub-1% lottery, no virality claim, in an event whose field will not be short of them.
