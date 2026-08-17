## Inspiration

We run a small record label. Every release ends the same way. The song goes up on
every service in the world, and then nothing happens.

It took us a long time to understand why, because it looks like a distribution
problem and it isn't. Distribution is solved — anybody can get a song onto every
platform in an afternoon. What isn't solved is that a song sitting on a platform
is a song nobody has heard of. Somebody still has to find the people who might
actually play it — the radio programmer, the curator, the person who hosts a show
about exactly this kind of music — and write to each one of them like a human
being, about this specific record, with a reason they should care.

That's a letter-writing job. It takes weeks per release, and it never ends,
because there's another release next month. Nobody at a label with three people
on it has weeks. So it doesn't get done, and the record disappears, and everyone
blames the algorithm.

We wanted to know whether that job could be handed to something that would
actually do it — every night, for every artist on the roster — without turning us
into the thing everybody in music hates. Because the failure mode here isn't
"nothing happens." The failure mode is that you become spam, and burn a
relationship the label needs for the next ten years, in a business that runs
entirely on people picking up the phone to someone they already know.

## What it does

Spindle is an agentic operating system for a record label, and the first job we
gave it is that letter-writing one.

It builds and keeps one map of everyone in the world who can carry a record —
stations, shows, curators, the people who program them — and what each of them
actually plays. When the label has a new song, Spindle works out who among all of
those people is genuinely right for that particular record, and drafts a real
letter to each one: not a template with a name swapped in, but a pitch that knows
what this person plays and why this song belongs on it.

Then it stops and waits for a person to say yes.

That pause is the product, not a limitation of it. An email to a music director
is the one thing in this business you cannot take back, so a human approves every
send. What the machine removes is the weeks of work in front of that decision,
not the decision.

What makes it worth having is what happens between releases. Every reply, every
polite no, every "send me a shorter edit," every person who said this artist
isn't for them but come back with the next one — all of it stays. So the next
record doesn't start from nothing. It starts from everything the label has
already learned about who says yes. That accumulation is the whole point. Most
labels start every release from zero; the reason they do is that the knowledge
lived in somebody's head or somebody's sent folder, and it left when they did.

It also spends the label's money on its own, within a ceiling the label sets, and
comes back to ask when it wants to spend more.

Which raises the question the entire project is really about. If something is
writing to real people and spending real money on your behalf, you need to be
able to ask it, the next morning, why it did that — and get a true answer.

## How we built it

The pieces are small and boring on purpose.

There is no boss agent directing traffic. Each agent does one narrow job — find
people, learn about them, rank them for a song, write the letter, send it, read
the reply — and none of them call each other. When one finishes, the change it
made is what wakes up the next one. That means you can kill half of them in the
middle of the night and the work still finishes when they come back, which
matters, because they run unattended and nobody is watching at three in the
morning.

Choosing who should hear a record isn't keyword matching. The system compares a
song and a person by what they're *about*, so a station that has never used the
word we'd have searched for still surfaces if it's genuinely the right home. And
the rules that must always hold — this label only ever sees its own people, only
people who can actually be contacted are eligible — aren't checks written in code
that somebody could forget to call. They're part of how the search itself works,
so the wrong answer can't be returned in the first place.

That's the pattern for the whole system: put the guarantees where they can't be
skipped. Nothing can be mailed twice. Nobody who asked us to stop can be
contacted again by anything, ever, including a well-meaning process that
rediscovers their public address next week. Two campaigns can't work the same
person at the same time. Nobody gets a letter at an address we guessed at rather
than found. Each of those is a rule the database enforces rather than a habit the
software has.

And everything an agent does is written down with the exact moment it did it. Not
a summary of what it did — the moment. That turns out to be the key to the last
part.

## Challenges we ran into

The hard one was proving *why*.

The system's memory is alive. It's learning all day: new people, new facts about
them, conversations opening and closing, rankings shifting as it learns. Which
means that by the time you want to ask "why did you write to this person," the
world it decided in is gone. The numbers have been overwritten. The rankings have
moved. Asking today's system what it was thinking last night gets you today's
answer wearing last night's clothes — which is worse than no answer, because it's
convincing.

The usual fix is to build a second system that photographs the first one
constantly, and it's miserable: it slows everything down, it costs a fortune to
keep, and it can only ever answer the questions you were clever enough to
anticipate.

CockroachDB let us skip all of it. The database can be asked what it looked like
at a moment in the past, and answer from the real thing rather than a copy. So
every decision the system makes is stamped with the instant it was made, and
asking why is just asking the same question again, pointed at that instant. It
comes back with the world as it actually stood: the same people, ranked the way
they were ranked, including the things it had learned by then and not the things
it learned since. The system can even re-run its own reasoning against those old
conditions and check that it still reaches the same conclusion it reached last
night.

There is no separate history to build, keep, or trust.

The related challenge was resisting the polite lie. The past isn't kept forever,
and when a decision falls off the far end of what's retained, the honest answer is
that the history is gone. It would have been easy — and it would have looked
better — to quietly answer with today's ranking instead. We made it refuse and
say exactly why it can't answer. Something that can tell you *"I can't justify
this, and here's the reason I can't"* is more trustworthy than something that
always has an explanation ready.

The other real challenge was scope. This started as a much bigger idea and we cut
it down repeatedly to the one job a label would actually miss if you took it
away.

## Accomplishments that we're proud of

That it does the letter-writing job, unattended, overnight — and still hands every
irreversible act to a person before it happens.

That the promises we make about it aren't promises. Nothing sends twice. Nobody
who opted out gets contacted. Nobody gets mail at a guessed address. Those aren't
things we're careful about; they're things the system is unable to do.

That you can ask it why, about any decision it made, and get the truth rather
than a reconstruction — and that when it can't tell you, it says so plainly
instead of improvising.

That it costs essentially nothing to keep alive between releases, which is the
difference between a tool a small label can run and a tool a small label reads
about.

And that the repository argues with itself in public. There's a document in it
whose entire job is to attack our own claims and ask whether the database is
really doing the work or whether we're name-dropping it. We shipped what survived
that, and we're just as explicit about the parts that are written but not
switched on. We'd rather a judge find the caveat already written down by us than
find it themselves.

## What we learned

That the hard part of letting software act in the world isn't making it capable.
It's making it accountable. Capability is the easy half now.

That the right instinct, over and over, was to move a guarantee out of the code
and into the data — from something the software remembers to do into something
the system cannot do wrong.

And that speed was the wrong thing to optimize. We spent real time on how fast
this thing could run before realizing the unit is wrong. It's writing to a person
who opens their inbox on Tuesday. A curator replies when they reply. Programming
meetings happen weekly. Running a thousand times a second doesn't get a record
played any sooner — it just gets you blocked. So the system is paced to the rhythm
of human correspondence, deliberately, and that turned out to be a design
principle rather than a compromise.

## What's next for Spindle

Radio and curators are where we started, because those registers are public and
it was the honest place to begin. The bigger opportunity is the people making
videos with music in them — the creators who break records now. We want them in
the same map, entered by a person who tells us what they actually posted, never
scraped, and treated with the same care as everyone else.

We're also working toward keeping each person's contact details in the country
their own laws say they should live in — physically, not as a promise in a policy
document — so a label can reach out across Europe without inheriting a legal
problem. We've proven out how that works; it isn't switched on yet, and we say so.

After that: letting replies teach the system directly, so that every no makes the
next shortlist better, and giving the label a real seat at the budget — telling
Spindle what a record is worth to them and letting it come back with what it
would do with that.

The goal hasn't changed since the first day. A label with three people on it
should be able to give a record the same shot a label with thirty would.
