# Country backfill — filling the world map, without taking the site down with it

The public map on `/` is a choropleth: countries are heat-coloured by how many
counterparties the index holds in each. It reads `party_fact.dimension = 'country_code'`,
and every counterparty that has no such fact is counted in the total and left off the map.

This runbook is how to write the missing ones, and — the part that matters — how fast you
are allowed to do it.

**Where it stood on 2026-08-15**, after the first run of everything below:

| | |
|---|---|
| counterparties | 43,191 |
| with a country | 19,963 |
| countries reached | 90 |
| still to backfill | 23,400 |

Before that run the number was **3,180, all of them `US`** — one country on a world map.

---

## 1. FCC stations: one statement, no network

```bash
cd apps/spindle/web
python -m spindle.ingest --backfill-fcc-country
```

Writes `country_code = 'US'` for every party carrying an `fcc_facility_id` and no country.
8,184 rows the first time it ran; instant, idempotent, and it makes no HTTP request.

It is a transcription rather than a guess, and `ingest.backfill_fcc_country`'s docstring
carries the argument: an FCC broadcast licence is a US licence, `fcc.query` only ever asks
the Commission for US jurisdictions, and the FM Query row carries `US` in field 11 next to
the city and state `index_stations` already stored. Re-fetching would return the same
string for all 8,184 rows.

It never touches a party that already has a country, so a station Radio Browser also knows
keeps the country Radio Browser asserted.

---

## 2. Radio Browser stations: `refresh_stream`, and it is slow

```bash
cd apps/spindle/web
python -m spindle.ingest --refresh-streams --kinds refresh_stream
```

`--refresh-streams` queues one lead per UUID bucket that still holds stations with no
country — sixteen buckets, keyed on the first hex character — and then drains them.

`--kinds refresh_stream` is **not optional**. Without it, `drain` works every kind in
`agents.REGISTRY`, which includes 33,489 pending `embed_party` leads. Those cost money.

`streams.REFRESH_CAP` bounds one lead to 200 stations, so a full pass moves ~3,200 rows and
the frontier needs roughly a dozen passes. One lead takes about 2.3 minutes — 200
sequential transactions against a remote cluster, so it is round-trip-bound, not
API-bound (four Radio Browser calls per 200 stations).

Repeat until it reports `queued 0`:

```bash
for i in $(seq 1 15); do
  python -m spindle.ingest --refresh-streams --kinds refresh_stream || break
done
```

Re-running is what finishes it. Until 2026-08-15 it could not: the queue used
`ON CONFLICT DO NOTHING`, so once the sixteen leads were `done` the second pass queued
nothing and the remaining 91% of the index was never looked at again. The conflict now
re-opens a bucket, which is safe because the frontier query only returns buckets that
still have stations with no country.

---

## 3. Two workers. Not four.

The buckets are independent and `fleet.claim` hands each lead to exactly one worker
(`FOR UPDATE SKIP LOCKED`, plus a lease token), so running several drainers is safe by
construction — a second worker that races on a claim gets an empty list and moves on.

Safe is not the same as free. Measured against the production Basic cluster on
2026-08-15:

| workers | `/public/search` | `/` |
|---|---|---|
| 0 | 1.6 s | 1.5 s |
| 2 | ~2 s | ~3.5 s |
| 4 | **> 200 s** | **> 120 s** |

Four workers exhaust the cluster's request-unit burst budget, and everything else is
throttled behind them — including the public map the backfill exists to fill in. The
site was effectively down for as long as the backfill was fast.

Recovery is not instant either: after stopping the workers the first query still took
39 seconds, then settled to 1.6.

**Run two.** This is a property of a Basic cluster's RU budget, so re-measure rather than
inherit the number if the tier changes.

To run a second worker alongside the first, give it its own name so the two are
distinguishable in `agent_run`:

```bash
python -m spindle.ingest --refresh-streams --kinds refresh_stream --worker w2
```

---

## 4. Checking progress

```sql
SELECT count(*)                        AS with_country,
       count(DISTINCT value_text)      AS countries
  FROM party_fact
 WHERE dimension = 'country_code' AND status = 'live';
```

Or just reload `/` — the readout above the map is the same numbers, and `/public/index`
recomputes them at most once a minute.

---

## 5. What will still be missing when it finishes

**Countries with no outline.** `world.json` is Natural Earth 1:110m, which has no polygon
for a city-state or a small island — 21 of them held counterparties as of 2026-08-15
(Hong Kong, Barbados, American Samoa …). They are counted, listed under the map, and
reachable through the country picker; they are simply not drawable at that scale.

**Counterparties with no country at all.** 26 parties carry neither an `fcc_facility_id`
nor a `radiobrowser_uuid`, so neither stage above can place them. The readout reports
`total` and `placed on the map` separately precisely so this gap is visible rather than
implied.
