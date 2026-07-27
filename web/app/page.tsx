import { CTA, Card, Grid, Section, Stat } from "@/components/ui";

/* Every figure on this page traces to research/_synthesis/SYNTHESIS.md, which carries the
   primary-source link for each. That discipline is not incidental to the pitch — §9 of
   that document records three widely-repeated industry numbers that do not exist where
   they are attributed, one of which would have set the entire budget. A B2B page for
   labels is exactly the place not to repeat them. */

export default function Home() {
  return (
    <main>
      {/* ------------------------------------------------------------------ nav */}
      <header className="sticky top-0 z-50 border-b rule bg-bg/85 backdrop-blur">
        <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4 sm:px-10">
          <span className="text-[15px] font-semibold tracking-tight">
            Remix<span className="text-gold">Kit</span>
          </span>
          <div className="hidden items-center gap-8 text-sm text-ink-2 md:flex">
            <a href="#why" className="hover:text-ink">Why</a>
            <a href="#how" className="hover:text-ink">How it works</a>
            <a href="#compliance" className="hover:text-ink">Disclosure</a>
            <a href="#stack" className="hover:text-ink">Built on</a>
          </div>
          <CTA href="#start">Add your artists</CTA>
        </nav>
      </header>

      {/* ----------------------------------------------------------------- hero */}
      <section className="px-6 pb-24 pt-20 sm:px-10 sm:pb-32 sm:pt-28">
        <div className="mx-auto max-w-6xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-gold">
            For labels and artist managers
          </p>
          <h1 className="mt-6 max-w-4xl text-5xl font-semibold leading-[1.05] tracking-tight sm:text-7xl">
            More releases beat more spend.
          </h1>
          <p className="mt-8 max-w-2xl text-lg leading-relaxed text-ink-2 sm:text-xl">
            Build each artist&rsquo;s on-screen identity once. Then generate
            UGC-inspiring content for every song in their catalogue — provenance
            embedded in every file, disclosure enforced by the funnel, at a cost per
            release that does not move.
          </p>
          <div className="mt-10 flex flex-wrap gap-4">
            <CTA href="#start">Add your artists</CTA>
            <CTA href="#why" variant="ghost">
              Why this and not ads
            </CTA>
          </div>
          <p className="mt-10 max-w-2xl border-l-2 border-s2 pl-5 text-[15px] leading-relaxed text-ink-3">
            <strong className="text-ink-2">We do not claim to make a song go viral.</strong>{" "}
            Nobody can, and the research below is why we say so out loud. We make each
            attempt cheap, fast, and legally clean — so you can afford more of them, and
            so a win actually pays.
          </p>
        </div>
      </section>

      {/* ------------------------------------------------------------------ why */}
      <Section
        id="why"
        bleed
        eyebrow="The evidence"
        title="Breakout is a lottery. Optimising one ticket is the worst move available."
        lede={
          <>
            We commissioned thirteen research pillars — roughly 6,900 lines and 2,440
            sourced claims — to find the mechanism that makes a song win. It found that
            every path is closed, and the closures are not close calls.
          </>
        }
      >
        <Grid cols={3}>
          <Stat
            figure="96%"
            accent="text-s5"
            label={
              <>
                of TikTok creations go to the <strong className="text-ink">top 10%</strong>{" "}
                of songs. The platform&rsquo;s effect concentrates on already-viral tracks;
                the long tail barely moves.
              </>
            }
            source="Quasi-experiment on UMG's TikTok withdrawal — arXiv 2405.14999"
          />
          <Stat
            figure="4%"
            accent="text-s2"
            label={
              <>
                of 600+ TikTok Top-50 songs crossed to the Hot 100. Of ~5,000
                TikTok-popular songs, just <strong className="text-ink">125</strong> were
                emerging artists.
              </>
            }
            source="Pillar 13 — UGC adoption"
          />
          <Stat
            figure="~50×"
            accent="text-s4"
            label={
              <>
                loss buying streams. $20–25K of spend returns roughly{" "}
                <strong className="text-ink">$300–500</strong> in royalties — at any geo
                or bid strategy.
              </>
            }
            source="Pillars 02, 04, 08 — verified against platform ad docs"
          />
          <Stat
            figure="663"
            accent="text-s1"
            label={
              <>
                randomised trials run <em>inside</em> Facebook, with full internal data,
                still &ldquo;unable to reliably estimate an ad campaign&rsquo;s causal
                effect.&rdquo; If they cannot measure it, neither can your agency.
              </>
            }
            source="Gordon et al. — Pillar 03"
          />
          <Stat
            figure="~20×"
            accent="text-s3"
            label={
              <>
                Sound ID fragmentation. A track that goes viral under fragmented IDs{" "}
                <strong className="text-ink">earns nothing</strong>. Metadata hygiene is
                free and almost nobody does it.
              </>
            }
            source="Pex — Pillar 01"
          />
          <Stat
            figure="$143"
            accent="text-gold"
            label={
              <>
                the cost of one creative test with a +50% lift threshold — the
                best-value measurement instrument in the entire stack.
              </>
            }
            source="Pillar 03 — priced 2026-07-15"
          />
        </Grid>

        <div className="mt-14 rounded-xl border rule bg-panel-2 p-8 sm:p-10">
          <h3 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            When outcomes are lottery-shaped, the winning move is not a better ticket.
          </h3>
          <p className="mt-5 max-w-3xl text-lg leading-relaxed text-ink-2">
            It is more tickets, at a lower cost per ticket, with the plumbing correct so
            a win actually pays. Salganik&rsquo;s <em>Science</em> experiment — 14,341
            participants across parallel worlds — showed that social influence, precisely
            what the feed maximises, makes outcomes <strong className="text-ink">more</strong>{" "}
            unpredictable, not less. That unpredictability is the mechanism, not a
            measurement failure to be engineered away. You cannot forecast your way into
            the tail. You can only buy more draws from it.
          </p>
          <p className="mt-5 max-w-3xl text-lg leading-relaxed text-ink-2">
            That makes this a <strong className="text-ink">portfolio business, not an
            optimisation business</strong> — and it is why RemixKit is priced and built
            per release rather than per campaign.
          </p>
        </div>
      </Section>

      {/* ------------------------------------------------------------------ how */}
      <Section
        id="how"
        eyebrow="How it works"
        title="Identity once. Then every song in the catalogue."
        lede="The expensive step runs once per artist. Everything after it is cheap, which is what makes a roster affordable rather than a budget line per act."
      >
        <Grid cols={4}>
          <Card step="Step 01" title="Add an artist">
            Upload reference frames and we build a reusable on-screen identity —
            structural features, wardrobe variants, and the negative prompts that stop an
            image model drifting toward a generic type.
          </Card>
          <Card step="Step 02" title="Attach songs">
            Each master is measured once: BPM, the drop, and the hook window. Those
            numbers are provenance-carrying and reused forever — no re-measuring per
            video.
          </Card>
          <Card step="Step 03" title="Generate">
            Multi-provider generation fans out across video, image and audio models to
            produce a pack of templatable assets, cut to the song&rsquo;s own beat grid.
          </Card>
          <Card step="Step 04" title="Review, then ship">
            Nothing publishes itself. You approve, and every delivered file carries the
            record of which model made it, from which prompt, inside the file.
          </Card>
        </Grid>

        <div className="mt-14 grid gap-5 lg:grid-cols-2">
          <div className="rounded-xl border rule bg-panel p-8">
            <h3 className="text-xl font-semibold tracking-tight">
              Templates, not adverts
            </h3>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              The one UGC lever the evidence supports is{" "}
              <strong className="text-ink">templatability</strong>. The song is the
              substrate; the template is the product. So what we generate is not a
              finished marketing post — it is content designed to be{" "}
              <em>imitated</em>, so a fan who sees it immediately understands what their
              own version would look like.
            </p>
          </div>
          <div className="rounded-xl border rule bg-panel p-8">
            <h3 className="text-xl font-semibold tracking-tight">
              Good creative is literally a media discount
            </h3>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              Meta&rsquo;s own auction documentation:{" "}
              <code className="rounded bg-bg-2 px-1.5 py-0.5 text-[13px] text-ink-2">
                Bid Per Impression = Bid Per Action × Estimated Action Rate
              </code>
              . Better creative does not just convert better — it buys cheaper
              impressions. Volume and quality are the same lever here, not a trade.
            </p>
          </div>
        </div>
      </Section>

      {/* ----------------------------------------------------------- compliance */}
      <Section
        id="compliance"
        bleed
        eyebrow="Disclosure by default"
        title="The majors are non-compliant, and it is being reported."
        lede={
          <>
            NPR, 13 July 2026: labels routinely instruct creators <em>not</em> to disclose
            paid promotion, and were non-responsive to the investigation. So
            &ldquo;do what the majors do&rdquo; is not available to you as a compliance
            strategy.
          </>
        }
      >
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="rounded-xl border border-s2/40 bg-panel p-8 lg:col-span-1">
            <div className="stat-figure text-5xl font-semibold text-s2">$53,088</div>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              FTC penalty, per violation. The exposure is not theoretical and the
              industry is under live investigation.
            </p>
          </div>
          <div className="rounded-xl border rule bg-panel p-8 lg:col-span-2">
            <h3 className="text-xl font-semibold tracking-tight">
              This is the rare case where the honest build is also the commercial one
            </h3>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              Provenance is not a badge we add at the end. Every asset carries a
              cryptographic manifest recording the model, the prompt and the run that
              produced it — embedded <strong className="text-ink">inside the media
              file</strong>, so the disclosure travels with the clip rather than sitting
              in a database we own. Anyone can verify it, including you, including a
              regulator.
            </p>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              We also refuse to launder rights. Footage is AI, stock, public-domain or
              your own masters — enforced at upload, not by convention. Unauthorised
              film-clip creative is barred by TikTok&rsquo;s written ad policy and
              pre-screened by Meta; an engine that lets you use it is selling you a
              takedown.
            </p>
          </div>
        </div>
      </Section>

      {/* ---------------------------------------------------------------- stack */}
      <Section
        id="stack"
        eyebrow="Built on"
        title="Genblaze and Backblaze B2."
        lede="Two choices that do most of the work, and are worth naming because they are why the provenance story is structural rather than bolted on."
      >
        <Grid cols={2}>
          <div className="rounded-xl border rule bg-panel p-8">
            <div className="flex items-baseline gap-3">
              <h3 className="text-xl font-semibold tracking-tight">Genblaze</h3>
              <a
                href="https://github.com/backblaze-labs/genblaze"
                className="text-sm text-s1 hover:text-ink"
                rel="noreferrer noopener"
                target="_blank"
              >
                backblaze-labs/genblaze ↗
              </a>
            </div>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              One pipeline fans out across video, image and audio providers, and returns a{" "}
              <strong className="text-ink">manifest per run</strong> — model, prompt,
              params, and a SHA-256 per asset. Content-addressing means an identical
              request reuses the existing object instead of regenerating it, which cuts
              directly into the dominant cost line. The manifest handlers embed that
              record into the delivered <code className="text-[13px]">.mp4</code>,{" "}
              <code className="text-[13px]">.png</code> or{" "}
              <code className="text-[13px]">.mp3</code>, and{" "}
              <code className="text-[13px]">verify()</code> is what the public checker
              runs.
            </p>
          </div>
          <div className="rounded-xl border rule bg-panel p-8">
            <div className="flex items-baseline gap-3">
              <h3 className="text-xl font-semibold tracking-tight">Backblaze B2</h3>
              <a
                href="https://www.backblaze.com/cloud-storage"
                className="text-sm text-s1 hover:text-ink"
                rel="noreferrer noopener"
                target="_blank"
              >
                backblaze.com ↗
              </a>
            </div>
            <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
              Every master, asset and manifest lives in B2, keyed by content hash. Assets
              move browser-to-storage over presigned URLs and never transit our
              application servers, so a release going viral is a{" "}
              <strong className="text-ink">storage read, not a compute event</strong>. The
              run index exports to Parquet in the same bucket, which means your catalogue
              is analytics-ready for your warehouse without an export step.
            </p>
          </div>
        </Grid>

        <div className="mt-6 rounded-xl border rule bg-panel-2 p-8">
          <p className="text-[15px] leading-relaxed text-ink-2">
            <strong className="text-ink">Idle cost is storage, not servers.</strong> The
            compute scales to zero between runs, so a label with a quiet month pays for
            the bytes it is keeping and essentially nothing else. That is the property
            that makes a portfolio strategy affordable in the first place.
          </p>
        </div>
      </Section>

      {/* ------------------------------------------------------------ guardrail */}
      <Section
        bleed
        eyebrow="What we will not sell you"
        title="A short list, because the omissions are the point."
      >
        <Grid cols={2}>
          <Card title="No virality promise">
            The base rate is under 1% for a cold single. Any engine claiming otherwise is
            selling something the evidence says nobody can deliver — including the
            majors.
          </Card>
          <Card title="No stream attribution">
            Spotify&rsquo;s conversion API has no stream event. We checked the docs
            directly. Anyone showing you stream attribution is showing you a model, not a
            measurement.
          </Card>
          <Card title="No bought engagement">
            EV-negative at any plausible probability, and vendors concede buyers lose
            60–80% of inventory within a fortnight.
          </Card>
          <Card title="No seeding cascade">
            There is no rigorous evidence that paid seeding causes organic pickup.
            Practitioners report momentum ends when spend ends.
          </Card>
        </Grid>
      </Section>

      {/* ------------------------------------------------------------------ cta */}
      <section id="start" className="border-t rule px-6 py-24 sm:px-10 sm:py-32">
        <div className="mx-auto max-w-3xl text-center">
          <h2 className="text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Bring your roster.
          </h2>
          <p className="mt-6 text-lg leading-relaxed text-ink-2">
            Add your artists, attach the catalogue, and start generating content built to
            be copied. Cheaper shots on goal, correctly plumbed, with the paperwork
            already right.
          </p>
          <div className="mt-10 flex flex-wrap justify-center gap-4">
            <CTA href="mailto:hello@respectthefunk.com?subject=RemixKit%20—%20roster%20access">
              Request access
            </CTA>
            <CTA href="#why" variant="ghost">
              Read the evidence again
            </CTA>
          </div>
        </div>
      </section>

      {/* --------------------------------------------------------------- footer */}
      <footer className="border-t rule bg-bg-2 px-6 py-12 sm:px-10">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 text-sm text-ink-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="max-w-2xl">
            <p className="font-semibold text-ink-2">
              Remix<span className="text-gold">Kit</span> — by Respect the Funk
            </p>
            <p className="mt-3 leading-relaxed">
              Every figure on this page traces to a primary source in our research
              synthesis, verified 15 July 2026. Pricing decays quickly. Research, not
              legal or financial advice — have an entertainment lawyer review any rights
              or disclosure position before you act on it.
            </p>
          </div>
          <p className="shrink-0">
            Built on{" "}
            <a
              href="https://github.com/backblaze-labs/genblaze"
              className="text-ink-2 hover:text-ink"
              rel="noreferrer noopener"
              target="_blank"
            >
              Genblaze
            </a>{" "}
            &amp;{" "}
            <a
              href="https://www.backblaze.com/cloud-storage"
              className="text-ink-2 hover:text-ink"
              rel="noreferrer noopener"
              target="_blank"
            >
              Backblaze B2
            </a>
          </p>
        </div>
      </footer>
    </main>
  );
}
