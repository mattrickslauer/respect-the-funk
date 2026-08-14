"""Turning a station we can describe into a station we can reach.

`018_contact_route.sql` built the table and stated the gap it exists to close: the system
has known *who* a counterparty is since 009 and *where they are* since 005, and has never
been able to say **how to reach them**. Measured against the live cluster on 2026-08-13,
that was still exactly true — `contact_route` held **one** row, the delivery self-test
address a human typed, against 14,170 counterparties. A ranked station nobody can reach is
a dead row, and 14,169 of them were dead.

This module is the harvest path for the one channel where a defensible answer exists.


## Where a route may come from, and where it may not

`SCOPE-RESET §2a` and Pillar 10 §4/§5 between them leave a narrow door, and it is the
right door:

  * **The FCC register does not carry one.** `fcc.py` says so in its own header: the
    Commission knows the licensee, the frequency and the city of licence, and does not
    know the music director's name or address. The FM Query `list=4` output has no contact
    column at all. Nothing to read.
  * **Radio Browser does not carry one.** It carries `homepage`, which `index_streams`
    correctly writes as a `presence` and not as a route — "a homepage is a surface you can
    read, a contact route is an endpoint you may send to, and conflating the two is how a
    crawler's URL ends up in a mail merge."
  * **Wikipedia does not carry one.** The infobox has `website`, which is another
    presence, and no address.
  * **Podcast Index does not carry one — but the feed does.** `podcastindex.py`'s header
    is right that the *API* omits `<itunes:owner><itunes:email>`, and the API returns the
    feed URL. `docs/research/14-counterparty-sources.md` §4 measured the difference: 40 of
    45 music feeds declare an owner address, only 2 of those 40 being hosting-platform
    boilerplate. That is `harvest_feed_contacts` below, and the research ranks it first
    among every source surveyed. It is first here too, and the module is ordered the other
    way round only because the website stage is the one that can run today.


## Two stages, ranked, and the ranking is not close

`harvest_feed_contacts` reads an element whose *only* purpose is to say where to write.
Apple requires it in a submitted feed so the directory can reach the owner. There is no
ambiguity about whose address it is or what it is for, which is why it needs no filtering
beyond dropping the hosting platforms that template their own address into every feed.

`harvest_contacts` reads a website, which is a weaker thing, and the difference is
enforced by how much argument the second one needs. §11 of the same research measured what
a naive homepage scan returns over 54 US stations: of eleven addresses, one was
`email@example.com` from an unedited template, one was `team@latofonts.com` lifted out of
a **CSS attribution comment**, and one was a podcast advertisement that appeared on two
unrelated broadcasters. Its conclusion — that a homepage stage, if built at all, must read
*"structured declarations only … and abstain otherwise"* — is the rule `plan` and
`declared` implement, and the free-text body scan this module originally had is gone
because that measurement killed it.

That narrowing has a measured cost and it is worth stating. Run against six real station
sites on 2026-08-13, the narrowed stage returned `music@wprb.com` from WPRB's own
submissions page, `submissions@wnxp.org`, seven addresses from KEXP's contact page
including `md@kexp.org`, four named staff addresses from KTXK, and **nothing at all** for
KFCF — whose addresses sit on pages its own navigation does not label as contact. Nothing
junk was returned in that run. Abstaining on KFCF is the stage working, not failing.

What is left is the station's **own website** — the page the station publishes *for the
purpose of being contacted*. That is not the thing Pillar 10 §5 forbids. The prohibition
there is on scraping *platforms*: places with terms of service, an account to ban and an
anti-bot team, where the enforcement risk is losing the accounts the campaign runs on.
Fetching a broadcaster's own contact page over the open web, once, with a truthful
User-Agent, after asking its `robots.txt`, is the thing every mail client does when it
renders a link. No account, nothing to ban, no terms accepted.

The distinction is not a loophole and it has a hard edge: this module reads **only** hosts
a counterparty already has a `presence` row for, follows **only** same-site links, and
stops at one hop. It is not a crawler and must not become one.


## The rule that decides every remaining question: only what they published

`018` names the failure mode in the provenance section — *"`inferred` is a guess, above all
a pattern guess like `music@<domain>`, and a guessed address is the single fastest way to
earn a spam complaint"*. `podcastindex.py` refuses the same construction. This module does
not merely decline to synthesise addresses; **it cannot**, and that is enforced rather than
promised:

`plan()` checks every `email` and `phone` value it is about to return against the bytes it
was read from, and raises `NotPublished` if the value does not occur there verbatim. There
is no path through this module that emits an address the station did not publish, because
the only way to produce one is to find it in a page, and the check re-proves that at the
exit. A future edit that adds a clever `info@` fallback fails a test rather than mailing a
stranger.

Every route this module writes is therefore `measured` — the source published it, which is
the class `018` reserves for exactly this: *"a station's published submissions address"*.
No route it writes is `inferred`, because it emits none.


## What it will not read, and why each one is a refusal rather than an oversight

  * **Bare text that looks like a phone number.** Only `tel:` hrefs become `phone` routes.
    A regex for digits over page text returns fax numbers, street numbers, licence
    numbers, transmitter power and the year the station was founded, and every one of them
    would be written as a number to call. `tel:` is markup whose entire meaning is "call
    this", authored by the station.
  * **Addresses from a page on another host.** A station's site links to its network, its
    syndicators and its sponsors. An address found there reaches somebody else.
  * **A form we merely found.** A `form` route is written only for a page carrying a
    `<textarea>`, because a contact form has a message box and a site search does not.
    Sending an operator to a search box labelled as a contact route is a wasted campaign
    slot and looks exactly like a working one.
  * **A postal address.** The licence register carries the licensee's city, not a mailing
    address, and parsing one out of a page footer is precisely the confident-looking guess
    this codebase keeps refusing. `contact_route` has a `postal` channel and this module
    leaves it empty.


## Personal data, and why the evidence document has no `party_id`

`024_regional_by_row.sql` is unambiguous that `contact_route` is the only table in this
schema holding Article 4(1) personal data — an address, a phone number, and an `addressee`
naming an identifiable person's job. Two consequences are wired in here rather than left
to a reviewer.

**Every route carries a `contact_country`, or it is not written.** 024's whole argument is
that a row whose country nobody established must not be silently domiciled in Virginia.
So `country_for` reads the country off evidence we already hold — an FCC facility ID is a
US broadcast licence and there is no such thing as a non-US one, or a `country_code` fact
written from Radio Browser's own `countrycode` — and **raises** when neither exists. A
station whose country we cannot establish is a station whose contact details we do not
harvest yet. That is the no-fallback rule applied where it costs something.

**The evidence document is written with `party_id = NULL`.** `018` wants the page a route
was read from kept, so the route can be argued with later. `agents._fetch_embed_party`
embeds *a party's most recent `party_document`*, so attaching a contact page to the party
would make a page of email addresses the text R1 ranks that station on — destroying
`profiles.py` rules 1 and 2 for every party this stage touches, and putting personal data
into a vector index at the same time. The route points at its evidence through
`contact_route.document_id`, which is the link 018 designed; the party does not point at
it. `ingest.EVIDENCE_PLATFORMS` likewise does not list `contact_page`, so the evidence is
not chunked into `party_chunk` either. Neither omission is incidental.

Stdlib only, like `fcc.py` and `radiobrowser.py`, so this runs in the console Lambda's
bundle.
"""

from __future__ import annotations

import hashlib
import html as html_module
import re
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable

import psycopg

from rtf_platform import fleet, podcastindex, spend

#: Truthful, with a contact address, as every polite-crawler convention asks. Same
#: reasoning as `fcc.fetch`: if a host blocks this, ask the host, do not claim to be a
#: browser. Measured 2026-08-13 — `kexp.org` answers 403 to it, `wrek.org` answers 200 —
#: and a 403 is reported as a permanent failure naming the host rather than retried behind
#: a different string.
USER_AGENT = ("respect-the-funk/1.0 (radio counterparty index; "
              "contact anthonybtedesco@gmail.com)")

#: How many pages one lead may fetch beyond the homepage. Three, because a station site
#: that hides its address deeper than "Contact" plus two siblings is a station whose
#: address a human should find. This is the number that keeps this a reader and not a
#: crawler, and it is the number to argue with first if that ever stops being true.
CONTACT_PAGES = 3

#: Bytes read per page. A radio station's contact page is a few tens of kilobytes; half a
#: megabyte is a bound on a misbehaving host, not a sampling policy. Truncation is
#: reported by `plan` finding fewer routes, never by a silent partial parse claiming
#: success — the addresses live in the markup, and markup that did not arrive is markup
#: this module never saw rather than markup it misread.
MAX_BYTES = 512_000

#: Words that mark a link as leading somewhere a contact is published, in two tiers.
#: Matched against the link's path and its anchor text.
#:
#: The tiers exist because `CONTACT_PAGES` is three and the budget is spent in page order
#: otherwise. Measured against `kfcf.org` on 2026-08-13, an untiered pass spent all three
#: fetches on "public service announcements", "financial info" and "programming funds" —
#: every one of them a `WEAK` match that happened to appear early in the navigation — and
#: never reached the page the site labels Contact. Ranking by the site's *own* label, not
#: by position, is the fix, and it is the site telling us where its addresses are.
#:
#: `WEAK` is kept rather than dropped: plenty of small stations put the music director's
#: address on an About or a DJs page and have no page called Contact at all. A weak match
#: costs one fetch of a page with no addresses on it.
STRONG_WORDS = (
    "contact", "contactus", "get-in-touch", "getintouch", "reach-us",
    "submission", "submissions", "submit", "staff", "music-director", "musicdirector",
    "program-director", "programdirector",
)
WEAK_WORDS = (
    "about", "people", "team", "music", "programming", "dj", "djs", "connect", "info",
)
CONTACT_WORDS = STRONG_WORDS + WEAK_WORDS

#: An email address in markup or in prose. Deliberately narrower than RFC 5322: the
#: grammar that standard actually permits matches things no station has ever printed, and
#: a wider pattern here means more junk, not more contacts.
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24}")

#: Last labels that mean the match is a filename or an asset reference and not a domain —
#: `logo@2x.png`, `sprite@3x.svg`. This list can go stale and is allowed to: an extension
#: nobody listed costs one junk row that a human deletes, whereas the alternative (accept
#: everything shaped like an address) puts `hero@2x.png` in a mail merge.
NOT_A_DOMAIN = frozenset({
    "png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "json", "woff", "woff2",
    "ttf", "eot", "ico", "mp3", "mp4", "m4a", "aac", "pdf", "zip", "xml",
})

#: Domains that appear on real pages and reach nobody: template placeholders a CMS shipped
#: and error-reporting endpoints a script embedded. Writing one of these as a route would
#: be writing an address that is published and still worthless, which the "only what they
#: published" rule does not by itself exclude.
NOT_A_CONTACT = frozenset({
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
    "email.com", "sentry.io", "sentry.wixpress.com", "mysite.com", "yoursite.com",
})

#: Longest `addressee` this module will transcribe. The column is free text by design —
#: 018 refused a closed taxonomy because "the taxonomy differs per source and inventing a
#: closed set here would force adapters to lie" — but a whole paragraph of anchor text is
#: not a job title, it is a parse that went wrong, and truncating it silently would hide
#: that. Over the limit, the addressee is dropped and the route keeps `''`.
ADDRESSEE_CHARS = 60


class NotPublished(ValueError):
    """A route value does not appear in the page it was supposedly read from.

    This is the guard that makes "no guessed addresses" a property rather than a promise.
    It should be unreachable in normal operation: every value `plan` returns was taken out
    of a page. Reaching it means either a normalisation step altered a value after
    extraction, or somebody added a synthesiser. Both are bugs and both are loud.
    """


class Blocked(RuntimeError):
    """`robots.txt` says this path is not ours to fetch. Terminal for this host."""


class PageUnavailable(RuntimeError):
    """The page could not be read. Carries whether asking again could ever help."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


# ------------------------------------------------------------------------ the route

@dataclass(frozen=True)
class Route:
    """One row of `contact_route`, with the evidence still attached to it.

    `evidence_url` and `evidence_text` are not decoration. `018` keeps `document_id` on
    the table so "a route can be argued with later on the same terms as a fact", and a
    route that arrives at the writer without the page it came from cannot honour that.
    Carrying them on the dataclass means the writer cannot forget to.
    """

    channel: str
    value: str
    addressee: str
    evidence_url: str
    evidence_text: str

    #: `018`'s `route_channel_known` CHECK, restated so a bad channel fails in this
    #: module's tests rather than as a constraint violation at 3am. Only three of the five
    #: are produced here; `postal` and `social` are left to a human, see the header.
    CHANNELS = ("email", "phone", "form")

    def __post_init__(self) -> None:
        if self.channel not in self.CHANNELS:
            raise ValueError(
                f"{self.channel!r} is not a channel this module produces; "
                f"known: {', '.join(self.CHANNELS)}")
        if not self.value:
            raise ValueError("a route with no value is not a route")


# ------------------------------------------------------------------------ parsing

class _Anchors(HTMLParser):
    """Every `<a>` in a page as `(href, text)`, plus whether the page has a `<textarea>`.

    A parser rather than a regex over `href="..."` because anchor *text* is where a page
    says who an address reaches — "Music Director" wrapping a `mailto:` is the station
    telling us the `addressee`, and a regex over attributes cannot see it.

    `convert_charrefs` is left at its default, so `&#109;ailto:` — the oldest email
    obfuscation on the web — is decoded before this sees it. That is not cleverness aimed
    at defeating obfuscation; it is the parser doing what a browser does, and the result
    is an address the station published in a form its own visitors can read.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.has_textarea = False
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "textarea":
            self.has_textarea = True
        if tag != "a":
            return
        href = dict(attrs).get("href")
        # A second `<a>` before the first closed is malformed markup, which is most of the
        # web. The open anchor is flushed with whatever text it had rather than discarded.
        if self._href is not None:
            self._flush()
        self._href = (href or "").strip()
        self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._text).split())
        self.links.append((self._href or "", text))
        self._href, self._text = None, []

    def close(self) -> None:          # pragma: no cover - trivial
        super().close()
        if self._href is not None:
            self._flush()


def anchors(html: str) -> tuple[list[tuple[str, str]], bool]:
    """`(links, has_textarea)` for one page. Malformed markup is normal, not fatal."""
    parser = _Anchors()
    parser.feed(html)
    parser.close()
    return parser.links, parser.has_textarea


def is_address(value: str) -> bool:
    """Is this a string worth writing into `contact_route.value` as an email?

    Three refusals, each for a different reason: it has to match the grammar at all; its
    last label has to be a domain suffix rather than a file extension; and its domain has
    to be one that reaches somebody. Each is checked separately so a rejection can be
    explained, which matters because the alternative to explaining it is loosening it.
    """
    if not EMAIL.fullmatch(value):
        return False
    domain = value.rsplit("@", 1)[-1].lower()
    if domain.rsplit(".", 1)[-1] in NOT_A_DOMAIN:
        return False
    return domain not in NOT_A_CONTACT


def normalise_email(raw: str) -> str:
    """A `mailto:` payload or a bare address into the value to store, or `''`.

    The domain is lowercased and the local part is **not**. Domains are
    case-insensitive by RFC 1035 and lowercasing them is what makes
    `route_one_per_value` actually deduplicate `Music@WREK.org` against
    `music@wrek.org`. Local parts are case-*sensitive* by RFC 5321 §2.4; almost no
    provider treats them that way, and "almost" is not a licence to rewrite an address
    somebody published. The uniqueness index then admits two rows for two spellings of
    what is probably one mailbox, which is a duplicate an operator can see and delete —
    strictly better than a route that silently does not resolve.

    `?subject=` and friends are dropped: they are the page's suggestion about what to say,
    not part of the address, and storing them would make one mailbox look like several.
    """
    value = raw.strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    value = value.split("?", 1)[0]
    value = urllib.parse.unquote(value).strip().strip("<>,;")
    # A `mailto:` may carry several comma-separated recipients. The first is the one the
    # link is about; the rest are copies, and writing a cc list as a route would mail
    # people whose only relationship to us is being on somebody else's cc line.
    value = value.split(",")[0].strip()
    if "@" not in value:
        return ""
    local, _, domain = value.rpartition("@")
    value = f"{local}@{domain.lower()}"
    return value if is_address(value) else ""


def normalise_phone(raw: str) -> str:
    """A `tel:` payload into the value to store, or `''`.

    Whitespace is removed and **nothing else is**. The punctuation in `+1-404-894-2468` is
    how the station wrote it, a normaliser that strips it produces a string that no longer
    appears on the page, and `plan`'s published-verbatim check would then have to be
    weakened to accommodate this function — which is the check earning its keep.

    Seven digits is the floor. Below that the `tel:` is an extension, a short code or a
    template the CMS left behind, none of which is reachable on its own.
    """
    value = "".join(raw.strip().split())
    if value.lower().startswith("tel:"):
        value = value[4:]
    value = urllib.parse.unquote(value).strip()
    if sum(character.isdigit() for character in value) < 7:
        return ""
    return value


def addressee_from(text: str, value: str) -> str:
    """What the page says this route reaches, or `''` when it says nothing.

    `018` puts `addressee` on the route rather than on the party precisely so a station's
    general enquiries address and its music director's address can both exist, and says
    the field records who the route reaches *"when the source says"*. So this transcribes
    the anchor's own words and never invents them: an anchor whose text is the address
    itself has told us nothing, and gets `''` rather than a cheerful "general".

    Trailing punctuation goes because "Music Director:" and "Music Director" are the same
    job. Nothing else is normalised — not the case, not the wording — because this is a
    transcription and the taxonomy is the source's.
    """
    said = " ".join(text.split()).strip(" :–—-")
    if not said or len(said) > ADDRESSEE_CHARS:
        return ""
    if "@" in said or said.casefold() == value.casefold():
        return ""
    if not any(character.isalpha() for character in said):
        return ""
    return said


def same_site(link: str, base: str) -> bool:
    """Is `link` on the same site as `base`?

    Host equality, or `link`'s host being a subdomain of `base`'s — `music.wrek.org` under
    `wrek.org`. Not the reverse: a station hosted at `kexp.org` does not thereby own
    everything under `org`, and the suffix test has to be anchored on a label boundary or
    it says `notwrek.org` is inside `wrek.org`.

    Scheme is not compared. A site that links from `https` to its own `http` pages is
    common and is still the same site; what matters here is whose inbox is at the end.
    """
    try:
        here = urllib.parse.urlsplit(link).hostname or ""
        there = urllib.parse.urlsplit(base).hostname or ""
    except ValueError:
        return False
    if not here or not there:
        return False
    here, there = here.lower().removeprefix("www."), there.lower().removeprefix("www.")
    return here == there or here.endswith(f".{there}")


def resolve(href: str, base: str) -> str:
    """One `href` into an absolute, fetchable URL, or `''`.

    The percent-encoding is not tidiness. `ktxk.org` links to `/management and staff` with
    the spaces unencoded — measured 2026-08-13 — and `urllib.request` raises `InvalidURL`
    on a control or space character in a path, which is a `ValueError` and would have
    escaped a handler catching only `URLError`. Browsers encode such a path before
    requesting it; so does this. Only the path is touched: re-quoting a query string
    would double-encode any `%` a CMS already wrote.
    """
    if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
        return ""
    try:
        absolute = urllib.parse.urldefrag(urllib.parse.urljoin(base, href))[0]
        split = urllib.parse.urlsplit(absolute)
    except ValueError:
        return ""
    if split.scheme not in ("http", "https") or not split.netloc:
        return ""
    return urllib.parse.urlunsplit(
        (split.scheme, split.netloc, urllib.parse.quote(split.path, safe="/%:@!$&'()*+,;=~"),
         split.query, ""))


def contact_pageish(url: str) -> bool:
    """Does this URL's own path say it is a page *for* being contacted?

    **`STRONG_WORDS` only, and the tier split is load-bearing here.** The weak tier decides
    where to *look* — an About or a DJs page is worth a fetch, because small stations put
    the music director's address there. This function decides something else: whether an
    address on a page counts as that counterparty *declaring* it. `/about` and `/music` are
    editorial pages that carry advertisements, syndicated show promos and third-party
    credits; `/contact`, `/submissions` and `/staff` are pages whose subject is who to
    write to. `14-counterparty-sources.md` §11 found `betrayalpod@gmail.com` — a podcast
    advertisement — in the body of two unrelated iHeart station pages, and admitting weak
    pages here is the door that failure comes through.

    Used for two things and nothing else: the `form` rule, and `declared`. A `<textarea>`
    on a page the site routes as `/contact` is a message box; a `<textarea>` on a homepage
    is a WordPress comment field, a newsletter widget or an oversized search box —
    measured on `kfcf.org`, whose homepage carries one and is not a contact route.
    """
    return any(word in urllib.parse.urlsplit(url).path.lower()
               for word in STRONG_WORDS)


def contact_links(html: str, base: str) -> list[str]:
    """Same-site URLs on this page that look like they lead to a published contact.

    Strong matches first, then weak, and within each tier the page's own order, so the run
    is deterministic and a re-fetch reads the same pages. Fragments are dropped —
    `#contact` is the page we are already on — and query strings are kept, because a CMS
    that routes contact pages through `?page=contact` is not unusual.
    """
    links, _ = anchors(html)
    strong: list[str] = []
    weak: list[str] = []
    for href, text in links:
        absolute = resolve(href, base)
        if not absolute or not same_site(absolute, base):
            continue
        if absolute.rstrip("/") == base.rstrip("/"):
            continue
        haystack = f"{urllib.parse.urlsplit(absolute).path} {text}".lower()
        into = (strong if any(word in haystack for word in STRONG_WORDS)
                else weak if any(word in haystack for word in WEAK_WORDS)
                else None)
        if into is not None and absolute not in strong and absolute not in weak:
            into.append(absolute)
    return strong + weak


def _published(value: str, page: str) -> bool:
    """Does this value occur in the page it was read from, verbatim?

    Two relaxations and no more. **Case**, because `normalise_email` lowercases the domain
    and a page may have printed it in capitals. **Character references**, because
    `_Anchors` decodes them — that is what a browser does, and `&#109;ailto:` is the oldest
    email obfuscation on the web — so a page carrying `&#109;d@kexp.org` really does
    publish `md@kexp.org` and the raw bytes are the wrong text to compare against. Without
    this, a station using entity obfuscation would fail the guard and park its lead
    permanently, which is the check firing on a correct read.

    Nothing else is relaxed, and in particular nothing is normalised out of the *value*
    side. See `NotPublished` for what this is guarding.
    """
    return value.casefold() in html_module.unescape(page).casefold()


def declared(url: str, href: str, site: str) -> bool:
    """Is this anchor a declaration by *this* counterparty, rather than a string on a page?

    The narrowing `14-counterparty-sources.md` §11 asks for, stated as a predicate so it
    can be tested rather than described. Two ways an anchor qualifies, and an anchor that
    qualifies by neither is refused:

      * **the address is on the counterparty's own domain.** `music@wprb.com` linked from
        anywhere on `wprb.com` is that station's mailbox by construction.
      * **the anchor sits on a page the site itself routes as contact/submissions/staff.**
        A gmail address under a named person on `ktxk.org/management and staff` is that
        station listing its own staff, and its being on gmail is a fact about a small
        college station rather than a reason to doubt it.

    What this refuses is the failure §11 measured: `betrayalpod@gmail.com`, a podcast
    advertisement appearing in the body of two *unrelated* iHeart station pages. Off-site
    domain, not on a contact page, and under the old rule it would have been written
    against both broadcasters — durably, because `018` makes routes hard to remove on
    purpose.

    A `tel:` has no domain, so it qualifies only by the second test. That costs the
    homepage phone number in a header, which is usually the station's own — measured on
    `wrek.org`, which publishes `tel:4048942468` on its homepage and nowhere this stage
    now reads. The loss is accepted rather than argued around: a `tel:` in a homepage
    banner can equally be an advertiser's, and there is nothing in the markup that tells
    the two apart.
    """
    if contact_pageish(url):
        return True
    if href.lower().startswith("mailto:"):
        value = normalise_email(href)
        return bool(value) and same_site(f"https://{value.rsplit('@', 1)[-1]}/", site)
    return False


def plan(pages: Iterable[tuple[str, str]], *, site: str) -> list[Route]:
    """Every route these pages *declare*, deduplicated on `(channel, value)`.

    **Only structured declarations.** `mailto:` and `tel:` anchors, and a contact page's
    own form — never a string in the page body that happens to match an address pattern.
    `docs/research/14-counterparty-sources.md` §11 measured what the body scan actually
    returns, over 54 US station homepages: of eleven addresses found, one was
    `email@example.com` from an unedited template, one was `team@latofonts.com` lifted out
    of a **CSS attribution comment**, and one was a podcast advertisement that appeared on
    two unrelated stations. Its conclusion is the rule this function implements — *"read
    structured declarations only … and abstain otherwise, in the same way `wikipedia.py`
    writes nothing when no `format =` line is found."*

    The first version of this module did scan the body, and it would have written the font
    vendor. That is not a hypothetical avoided by care; it is a measurement, and the
    scan is gone.

    `declared` is the second half of the narrowing and is documented on its own.

    The pages arrive in fetch order — homepage first, then contact pages — and the first
    occurrence of a value wins its `addressee`. That ordering is doing real work: an
    address that appears in a footer on every page and again under "Music Director" on the
    contact page keeps the footer's blank addressee. The alternative, preferring whichever
    occurrence carries a label, sounds better and is worse: it promotes a heading that
    happened to sit above a general enquiries address into a claim about who reads it.

    Deduplication is **case-insensitive on the value, while the stored spelling is the
    page's own**. `normalise_email` lowercases the domain and deliberately leaves the
    local part alone, because RFC 5321 §2.4 makes local parts case-sensitive and rewriting
    somebody's published address is not this module's business. That is the right call for
    the stored value and the wrong one for the key: `kfcf.org` prints both
    `Calendar@kfcf.org` and `calendar@kfcf.org` on one page — measured 2026-08-13 — and
    emitting two routes for one mailbox because a designer capitalised a heading is noise
    an operator has to clean by hand. The key is folded, the value is not, and the first
    spelling encountered is the one written.

    Raises `NotPublished` if any `email` or `phone` value it is about to return does not
    occur verbatim in the page it was read from. A `form` route is exempt and that is not
    an exception being carved out: its value is the URL of a page this module fetched, so
    the evidence is the fetch, not a string inside the body.
    """
    routes: dict[tuple[str, str], Route] = {}

    for url, html in pages:
        links, has_textarea = anchors(html)

        for href, text in links:
            lowered = href.lower()
            if lowered.startswith("mailto:"):
                value, channel = normalise_email(href), "email"
            elif lowered.startswith("tel:"):
                value, channel = normalise_phone(href), "phone"
            else:
                continue
            if not value or not declared(url, href, site):
                continue
            if not _published(value, html):
                raise NotPublished(
                    f"{value!r} was not found in {url} — it was normalised into "
                    "something the page does not contain, or it was synthesised. "
                    "Neither may be written as a contact route.")
            routes.setdefault((channel, value.casefold()),
                              Route(channel=channel, value=value,
                                    addressee=addressee_from(text, value),
                                    evidence_url=url,
                                    evidence_text=f"<a href={href!r}>{text}</a>"))

        # A message box on a page the *site* labels as contact. Both halves are required:
        # see `contact_pageish` for why a textarea alone is not evidence of anything.
        if has_textarea and contact_pageish(url):
            routes.setdefault(("form", url.casefold()),
                              Route(channel="form", value=url, addressee="",
                                    evidence_url=url,
                                    evidence_text="page carries a <textarea>, so it is a "
                                                  "message form rather than a search box"))

    return list(routes.values())


def evidence_body(host: str, routes: list[Route]) -> str:
    """The document `018`'s `document_id` points at: what was found, and where.

    Not the raw page. A station homepage is a quarter of a megabyte of navigation and
    scripts, and storing it would put the whole site in a table whose rows are personal
    data — more of a retention problem than the route itself, for no gain. What a human
    arguing with a route needs is the address, the page it was on, and the words around
    it, and that is what this keeps.
    """
    lines = [f"Contact routes published on {host}.", ""]
    for route in routes:
        who = f" ({route.addressee})" if route.addressee else ""
        lines.append(f"{route.channel}: {route.value}{who}")
        lines.append(f"  found at: {route.evidence_url}")
        lines.append(f"  evidence: {route.evidence_text[:300]}")
    return "\n".join(lines)


# ------------------------------------------------------------------------- fetching

def _decode(raw: bytes, content_type: str) -> str:
    """Bytes into text, using the charset the server declared and `utf-8` when it did not.

    `errors='replace'` rather than a strict decode: a mis-declared charset in one byte of
    a footer must not cost the whole page, and a replacement character cannot become part
    of an address — `EMAIL` does not match it, so a corrupted region drops out on its own
    instead of producing a plausible-looking wrong address.
    """
    charset = ""
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip().strip('"')
    return raw.decode(charset or "utf-8", "replace")


def fetch(url: str, *, timeout: int = 20) -> str:
    """One page, as text. Raises `PageUnavailable`, never returns an empty page silently.

    A 403 is `permanent`. It is what a bot filter returns to a truthful, unfamiliar
    User-Agent — measured against `kexp.org` on 2026-08-13 — and the two ways past it are
    to claim to be a browser or to ask the station. `fcc.fetch` faced the identical choice
    and made the same call: send the truth, and if that is refused, say so out loud rather
    than inventing a plausible string.
    """
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES)
            return _decode(raw, response.headers.get("content-type", ""))
    except urllib.error.HTTPError as exc:
        raise PageUnavailable(
            f"{url} -> {exc.code}",
            permanent=exc.code in (400, 401, 403, 404, 410)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PageUnavailable(f"{url} unreachable: {exc}") from exc
    except ValueError as exc:
        # `urllib.error.InvalidURL` is a `ValueError`, not a `URLError`, and a site that
        # links to an unencoded path raises it from inside `urlopen` rather than from the
        # parse. `resolve` encodes what it can; this is the net for what it cannot, and it
        # is permanent because a malformed URL is malformed on every retry.
        raise PageUnavailable(f"{url} is not fetchable: {exc}", permanent=True) from exc


def robots_for(base: str, *, timeout: int = 15) -> urllib.robotparser.RobotFileParser:
    """This origin's `robots.txt`, parsed. Raises when it cannot be established.

    **A 404 means "no rules" and is honoured as such; an error does not.** That asymmetry
    is the protocol's, not a default this module invented: the standard says an absent
    `robots.txt` grants full access, so treating a 404 as permission is reading the
    answer. A 500 or a timeout is not an answer, and proceeding on one would be assuming
    permission we failed to check — the shape of silent default `MEMORY`'s no-fallbacks
    rule exists to forbid. So the first is allowed and the second raises.
    """
    split = urllib.parse.urlsplit(base)
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{split.scheme}://{split.netloc}/robots.txt")
    try:
        text = fetch(f"{split.scheme}://{split.netloc}/robots.txt", timeout=timeout)
    except PageUnavailable as exc:
        if exc.permanent:
            parser.parse([])
            return parser
        raise
    parser.parse(text.splitlines())
    return parser


def allowed(parser: urllib.robotparser.RobotFileParser, url: str) -> bool:
    """May we fetch this URL? Asked with our real User-Agent, not with `*`."""
    return parser.can_fetch(USER_AGENT, url)


# ------------------------------------------------------------------- country of record

def country_for(conn: psycopg.Connection, tenant_id: str, party_id: str) -> str:
    """The ISO-3166-1 alpha-2 country to domicile this party's routes in, or raise.

    Two sources of evidence, both already on the row, neither of them a guess:

      * **an `fcc_facility_id` identifier.** `024` makes the argument and it is airtight:
        a broadcast licence is issued under 47 U.S.C. to a licensee in the United States
        or its territories, and there is no such thing as a non-US FCC broadcast licence.
        `US` for such a party is a measured fact about the source.
      * **a `country_code` fact.** Radio Browser publishes `countrycode` per station;
        `streams.refresh_stream` writes it. This is the source's own field, transcribed.

    And **no third branch.** Not the TLD of the homepage — `.fm` is Micronesia and is used
    by radio stations on four continents, and `.co` is Colombia and is used by everybody.
    Not the FCC city of licence, whose two-letter state code is the exact trap `024`
    devotes a section to: ISO `AL` is Albania and FCC `AL` is Alabama.

    Raising rather than defaulting is `024`'s central argument transplanted into Python.
    The country decides where a person's contact details physically live; a party we
    cannot place is a party whose details we do not collect yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM party_identifier
                WHERE tenant_id = %s AND party_id = %s AND kind = 'fcc_facility_id'
                LIMIT 1""",
            (tenant_id, party_id),
        )
        if cur.fetchone() is not None:
            return "US"

        cur.execute(
            """SELECT value_text FROM party_fact
                WHERE tenant_id = %s AND party_id = %s AND dimension = 'country_code'
                  AND status = 'live'
                ORDER BY first_seen_at DESC LIMIT 1""",
            (tenant_id, party_id),
        )
        row = cur.fetchone()

    code = (row["value_text"] if row else "").strip().upper()
    if len(code) == 2 and code.isalpha():
        return code
    raise fleet.LeadFailed(
        f"party {party_id} has no country evidence — no `fcc_facility_id` identifier and "
        "no live `country_code` fact — so there is nowhere lawful to domicile a contact "
        "route for it (024_regional_by_row.sql). Run `refresh_stream` for this party "
        "first, which writes `country_code` from Radio Browser's own field.",
        permanent=True)


# ------------------------------------------------------------- the harvest_contacts stage

def _content_hash(text: str) -> str:
    """SHA-256 hex, matching `agents.content_hash`.

    Duplicated rather than imported because `agents` imports this module to register the
    stage, and importing back would be a cycle. `harvested.PRESENCE_MODES` duplicates
    `domain.ProfileMode` for the same reason and says so in the same place.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_harvest_contacts(conn: psycopg.Connection, lead: dict[str, Any],
                            gate: spend.Gate) -> dict[str, Any]:
    """Read one counterparty's own website. Network only, no writes.

    The gate is not consulted: fetching a public web page costs nothing this system
    meters, and `spend.estimate` raises on an unpriced key by design. The cost this stage
    causes is zero — unlike every other discovery stage it queues no embedding, because a
    contact route is not part of what a party is ranked on.
    """
    tenant_id = lead["tenant_id"]
    party_id = lead.get("party_id") or lead.get("target")
    if not party_id:
        raise fleet.LeadFailed("harvest_contacts needs a party", permanent=True)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.name, pr.url
                 FROM party p
                 JOIN presence pr ON pr.tenant_id = p.tenant_id
                                 AND pr.subject_kind = 'party' AND pr.subject_id = p.id
                                 AND pr.platform = 'web' AND pr.state = 'present'
                WHERE p.tenant_id = %s AND p.id = %s AND p.party_class = 'counterparty'""",
            (tenant_id, party_id),
        )
        row = cur.fetchone()
    if row is None:
        raise fleet.LeadFailed(
            f"party {party_id} has no `web` presence, so there is no site of theirs to "
            "read. A contact route is only harvested from a page the counterparty "
            "publishes; nothing here guesses one from a domain.", permanent=True)

    base = (row["url"] or "").strip()
    if not base.lower().startswith(("http://", "https://")):
        raise fleet.LeadFailed(
            f"{base!r} is not an http(s) URL", permanent=True)

    country = country_for(conn, tenant_id, str(party_id))

    try:
        robots = robots_for(base)
        if not allowed(robots, base):
            raise Blocked(f"robots.txt on {base} disallows {USER_AGENT}")
        pages = [(base, fetch(base))]
    except Blocked as exc:
        # Terminal, and terminal is the correct reading. A host that says no does not say
        # a different thing tomorrow, and a stage that retried it four times on backoff
        # would be ignoring the answer slowly rather than quickly.
        raise fleet.LeadFailed(str(exc), permanent=True) from exc
    except PageUnavailable as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc

    for link in contact_links(pages[0][1], base)[:CONTACT_PAGES]:
        if not allowed(robots, link):
            continue
        try:
            pages.append((link, fetch(link)))
        except PageUnavailable:
            # One contact page failing is not the station failing. The homepage's routes
            # still stand, and the lead completes with what it read rather than losing
            # them to a 404 on a stale nav link.
            continue

    return {"party_id": str(party_id), "name": row["name"], "base": base,
            "country": country, "pages": pages}


def _write_harvest_contacts(conn: psycopg.Connection, lead: dict[str, Any],
                            gate: spend.Gate, prepared: dict[str, Any]) -> fleet.Outcome:
    """Write the routes the station published, with the evidence they were read from.

    `ON CONFLICT DO NOTHING` on `route_one_per_value`, which is `018`'s whole mechanism
    for making `opted_out` terminal: *"a harvester that re-finds a public address must not
    be able to resurrect a route somebody asked us to stop using"*. This is that
    harvester, and it is the call site that rule was written about. `DO UPDATE` here —
    even to refresh `checked_at`, which looks harmless — would undo it, so nothing is
    updated and a re-harvest of a known address is a no-op that reports zero.
    """
    tenant_id, party_id = lead["tenant_id"], prepared["party_id"]
    routes = plan(prepared["pages"], site=prepared["base"])
    host = urllib.parse.urlsplit(prepared["base"]).netloc

    if not routes:
        # Not a failure and not marked as one. A station that publishes no address on the
        # pages it links from its homepage is a real answer about that station, and
        # `party.contact_state` is deliberately left `contactable`: 009's `unusable` means
        # "this counterparty cannot be reached", which is a stronger claim than "we did
        # not find an address on their website" and would silently shrink every shortlist.
        return fleet.Outcome(
            summary=f"{host}: no published contact route on "
                    f"{len(prepared['pages'])} page(s)",
            calls=len(prepared["pages"]))

    body = evidence_body(host, routes)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO party_document (tenant_id, party_id, lead_id, platform, url,
                                           title, body, content_hash, mime, lang,
                                           http_status)
               VALUES (%s, NULL, %s, 'contact_page', %s, %s, %s, %s, 'text/plain',
                       'en', 200)
               ON CONFLICT (tenant_id, content_hash) DO UPDATE SET fetched_at = now()
            RETURNING id""",
            (tenant_id, lead["id"], prepared["base"],
             f"{prepared['name']} — published contact routes", body,
             # Hashed with the party id for the same reason `ingest.recompose_profiles`
             # does: two network stations sharing a syndicated contact page would
             # otherwise collide on `(tenant_id, content_hash)` and the second would
             # silently get no evidence row at all.
             _content_hash(f"{party_id}:{body}")),
        )
        document_id = cur.fetchone()["id"]

        written = 0
        for route in routes:
            cur.execute(
                """INSERT INTO contact_route (tenant_id, party_id, channel, value,
                                              addressee, provenance, state, source,
                                              document_id, written_by, contact_country)
                   VALUES (%s, %s, %s, %s, %s, 'measured', 'unverified', %s, %s,
                           'harvest_contacts', %s)
                   ON CONFLICT (tenant_id, party_id, channel, value) DO NOTHING""",
                (tenant_id, party_id, route.channel, route.value, route.addressee,
                 route.evidence_url[:500], document_id, prepared["country"]),
            )
            written += cur.rowcount

    kinds = ", ".join(sorted({r.channel for r in routes}))
    return fleet.Outcome(
        summary=(f"{host}: {written} new route(s) of {len(routes)} published ({kinds}), "
                 f"from {len(prepared['pages'])} page(s)"),
        documents=1, calls=len(prepared["pages"]),
    )


#: Read a counterparty's own website and write the contact routes it publishes — and only
#: those. Nothing here synthesises an address; see this module's header.
harvest_contacts = fleet.NetworkAgent(fetch=_fetch_harvest_contacts,
                                      write=_write_harvest_contacts)


# ------------------------------------------------------- the harvest_feed_contacts stage
#
# The strongest route class this system can obtain, and the reason it is a separate stage
# from `harvest_contacts` rather than a branch inside it.
#
# `docs/research/14-counterparty-sources.md` ranks podcast feed enrichment first among
# every source surveyed, and the ranking is not close. Measured against 45 music-category
# feeds on 2026-08-13: 40 carry `<itunes:owner><itunes:email>`, and only 2 of those 40 are
# hosting-platform boilerplate. The element exists because Apple requires it in a
# submitted feed so the directory can reach the owner — its *only* purpose is to say where
# to write, which is precisely `018`'s definition of a `measured` route and is a stronger
# claim than anything a page yields.
#
# `podcastindex.py`'s header says the index does not publish the address, and it is right
# and it is about the API. The API returns the feed URL; the feed is one GET away and is
# the publisher's own document on the publisher's own host.
#
# **What this stage does not do, and why it is not folded into `index_podcasts`.** The
# natural home for this is `_write_index_podcasts`, which already holds the feed record
# with its `url` field in hand. That function lives in `agents.py`, which this workstream
# does not own, so the work is done here as a second stage that re-asks the index for the
# feed URL by ID — one extra call per show. If `index_podcasts` ever stores `feed_url` as
# an identifier or a `presence`, this stage should read it from there and drop the call;
# `_fetch_harvest_feed_contacts` is written so that change is a deletion.
#
# **Nothing it writes exists yet.** As of 2026-08-13 the live cluster holds zero podcast
# counterparties: `023_podcast_source.sql` and `027_enable_podcasts.sql` are both
# unapplied, `agent_manifest` has no `index_podcasts` row, and `PODCASTINDEX_API_KEY` is
# not set in this environment. This stage is therefore complete, tested offline, and
# unexercised against real data — which is stated here rather than discovered later.


def _fetch_harvest_feed_contacts(conn: psycopg.Connection, lead: dict[str, Any],
                                 gate: spend.Gate) -> dict[str, Any]:
    """Read one show's feed and take the owner address out of it. Network only.

    Two calls: the index, for the feed URL it holds and `index_podcasts` did not store,
    and the feed itself. Both raise rather than returning empty — `podcastindex._get`'s
    standing rule, and the reason is the same one: a stage that turns an outage into "this
    show has no owner" writes that non-answer into the database as though it were one.
    """
    tenant_id = lead["tenant_id"]
    party_id = lead.get("party_id") or lead.get("target")
    if not party_id:
        raise fleet.LeadFailed("harvest_feed_contacts needs a party", permanent=True)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.name, i.value AS feed_id
                 FROM party p
                 JOIN party_identifier i ON i.tenant_id = p.tenant_id
                                        AND i.party_id = p.id
                                        AND i.kind = 'podcastindex_feed_id'
                WHERE p.tenant_id = %s AND p.id = %s AND p.party_class = 'counterparty'""",
            (tenant_id, party_id),
        )
        row = cur.fetchone()
    if row is None:
        raise fleet.LeadFailed(
            f"party {party_id} carries no `podcastindex_feed_id`, so there is no feed to "
            "read. This stage reads a publisher's own RSS and nothing else.",
            permanent=True)

    try:
        feed_id = int(str(row["feed_id"]).strip())
    except ValueError:
        raise fleet.LeadFailed(
            f"{row['feed_id']!r} is not a Podcast Index feed ID", permanent=True) from None

    try:
        record = podcastindex.feed_by_id(feed_id)
        url = str(record.get("url") or "").strip()
        if not url:
            raise fleet.LeadFailed(
                f"Podcast Index holds feed {feed_id} with no `url`, so the document that "
                "carries the owner address cannot be located", permanent=True)
        document = podcastindex.fetch_feed(url)
    except podcastindex.NotConfigured as exc:
        raise fleet.LeadFailed(str(exc), permanent=True) from exc
    except podcastindex.Refused as exc:
        raise fleet.LeadFailed(str(exc), permanent=exc.permanent) from exc

    email, owner_name = podcastindex.owner(document)
    return {"party_id": str(party_id), "name": row["name"], "feed_url": url,
            "email": email, "owner_name": owner_name,
            # The country is the open question `024` leaves and this stage cannot answer:
            # a feed says nothing about where its owner is established. See the writer.
            "excerpt": document[:4000]}


def _write_harvest_feed_contacts(conn: psycopg.Connection, lead: dict[str, Any],
                                 gate: spend.Gate,
                                 prepared: dict[str, Any]) -> fleet.Outcome:
    """Write the owner address as a `measured` route, with the feed header as its evidence.

    **`contact_country` is written as NULL and that is the honest value.** A feed declares
    an address and a name and says nothing about where its owner is established; there is
    no `countrycode` here as there is for a Radio Browser station and no licence
    jurisdiction as there is for an FCC station. `024`'s rule is that a country nobody
    established must not be *invented* — its objection is to `ELSE 'aws-us-east-1'`, not to
    an honest NULL — and 029 deliberately leaves the column nullable so this can be said.
    The consequence is stated plainly: **these routes block 024's `SET NOT NULL` gate**
    until a human decides what to do with them, which is exactly the human decision
    `14-counterparty-sources.md` §10 item 3 raises about independent shows and individual
    subscribers. A route that stops a residency migration until somebody looks at it is
    behaving correctly.

    `addressee` is `<itunes:name>` when the feed carries one — the publisher's own word for
    who this reaches, transcribed, which §10 notes is the field a PECR corporate-versus-
    individual policy would filter on.
    """
    tenant_id, party_id = lead["tenant_id"], prepared["party_id"]
    email = prepared["email"]

    if not email:
        return fleet.Outcome(
            summary=f"{prepared['name'][:50]}: feed carries no usable owner address",
            calls=2)

    body = (f"Owner declared in the RSS feed at {prepared['feed_url']}.\n\n"
            f"email: {email}\n"
            f"name: {prepared['owner_name'] or '(not stated)'}\n\n"
            f"Feed header as fetched:\n{prepared['excerpt']}")

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO party_document (tenant_id, party_id, lead_id, platform, url,
                                           title, body, content_hash, mime, lang,
                                           http_status)
               VALUES (%s, NULL, %s, 'contact_page', %s, %s, %s, %s, 'text/plain',
                       'en', 200)
               ON CONFLICT (tenant_id, content_hash) DO UPDATE SET fetched_at = now()
            RETURNING id""",
            (tenant_id, lead["id"], prepared["feed_url"][:500],
             f"{prepared['name']} — declared feed owner", body,
             _content_hash(f"{party_id}:{body}")),
        )
        document_id = cur.fetchone()["id"]

        cur.execute(
            """INSERT INTO contact_route (tenant_id, party_id, channel, value, addressee,
                                          provenance, state, source, document_id,
                                          written_by, contact_country)
               VALUES (%s, %s, 'email', %s, %s, 'measured', 'unverified', %s, %s,
                       'harvest_feed_contacts', NULL)
               ON CONFLICT (tenant_id, party_id, channel, value) DO NOTHING""",
            (tenant_id, party_id, email, prepared["owner_name"][:ADDRESSEE_CHARS],
             prepared["feed_url"][:500], document_id),
        )
        written = cur.rowcount

    return fleet.Outcome(
        summary=(f"{prepared['name'][:50]}: {written} route(s) from the feed's declared "
                 f"owner"),
        documents=1, calls=2,
    )


#: Read `<itunes:owner><itunes:email>` out of a show's own RSS feed. The only route in this
#: codebase whose source element exists solely to say where to write.
harvest_feed_contacts = fleet.NetworkAgent(fetch=_fetch_harvest_feed_contacts,
                                           write=_write_harvest_feed_contacts)
