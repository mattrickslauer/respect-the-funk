# Finding: Meta Incremental Attribution is not measurement

**Status:** Verified against Meta primary sources. Banked here during research; belongs in Pillar 03 (Audience Models) and cross-referenced from Pillar 02 (Meta Ads).
**Date:** 2026-07-15

## Primary sources

- [About Incremental Attribution](https://www.facebook.com/business/help/2366718460372682)
- [How to Use Incremental Attribution in Meta Ads Manager](https://www.facebook.com/business/help/644554008179419)
- [How to View Results for Incremental Attribution](https://www.facebook.com/business/help/1271619100603114)

## What it actually is

Per Meta's own docs, Incremental Attribution is **an attribution-model setting driven by a predictive ML model — not a randomized experiment.** Meta describes it as using "machine learning models that predict whether a conversion is caused by an ad." That is the entire methodological disclosure. No holdouts, no control groups, no lift studies are mentioned on any of the three pages.

It reports a filtered subset of conversions you already had. Meta's own example: a campaign with 100 total conversions, 70 of which Meta considers incremental, shows 70 in the Results column.

Meta's careful wording on the results page is the tell: it is "designed to help you better understand the **relative** incrementality between Meta campaigns." That is a materially weaker claim than measuring true incremental lift. There is no control group for your account, therefore no account-specific counterfactual. **Meta is grading its own homework.** Treat it as an unfalsifiable vendor estimate, not as evidence.

### Correction to industry folklore

The claim that it is "built on Meta's historical Lift study data / holdout testing" is widespread in agency content (Jonathan Snow, workmagic.io, threechaptermedia.com, bir.ch, easyinsights.ai) but **could not be traced to any Meta URL**. Treat as UNVERIFIED. [Social Media Today's coverage](https://www.socialmediatoday.com/news/meta-updates-information-incremental-attribution-conversion-tracking/759205/) is the most credible secondary source but is still trade press.

Vendor-circulated performance stats — Meta Q1 2025 earnings "average 46% lift", Q4 2025 "24% increase in incremental conversions" — come via secondary summaries and are **UNVERIFIED** against Meta investor relations. Do not cite without checking.

## Why this is the most important small-budget finding so far

**There is no published minimum spend, no minimum conversion volume, and no data-sufficiency threshold for results to display.** This is a genuine absence, not a failed search — the eligibility constraints Meta does state are purely structural (objective, performance goal, bid control), exactly where a spend minimum would sit if one existed.

Contrast with [Conversion Lift](https://www.facebook.com/business/help/399784471609174), which states its $5,000 / 500-conversion gate plainly, and which documents a "at least 100 conversions from test and control groups combined" threshold before results display.

**The consequence: at $500 of spend, Incremental Attribution will still show you a number.** A real experiment refuses to report when it lacks power. This one never refuses. That is a silent-failure mode — the number looks identical whether it means something or nothing, and there is no in-product signal telling you which.

This is the canonical example for the "techniques that will not work at our scale" section. The danger isn't that the tool is unavailable; it's that it is available, free, and confidently wrong.

## Usable constraints (verified)

| Constraint | Detail |
|---|---|
| Campaign objectives | Sales, Engagement, or Leads |
| Conversion location | Website, or Website and app |
| Performance goals | Maximize number of conversions, or Maximize value of conversions |
| Bid controls | **Cannot be used** — Meta: "You cannot use bid controls when using incremental attribution" |
| Reversibility | **None.** "You cannot change your attribution model after you publish your campaign." Publish-time, irreversible — matters for a short campaign. |
| Data floor | Results unavailable for date ranges before **April 1, 2025** |
| Where | Ads Manager → Show more options → Attribution model → "Incremental" |

Special ad category and pharmaceutical campaigns "may not have access to all Advantage+ features, or may see a different experience."

## Recommendation

Switch it on — it's free, it works with the objectives we'd use anyway (Sales + Maximize conversions), and it's the only incrementality-flavored option available below Conversion Lift's $5,000 gate.

But frame it in our own product and in anything we show a label as a **directional, Meta-graded estimate of relative incrementality — never as measurement.** If our engine surfaces this number to a client as "incremental streams," we would be laundering a vendor's self-assessment into an analytics product. That's a credibility risk for the engine that outweighs the metric's value.

Note the bid-control exclusion conflicts with cost-cap bidding strategies, and the irreversibility means it's a decision made once per campaign at publish time.
