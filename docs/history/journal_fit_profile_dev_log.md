# Journal Fit Profile Update Log
Date: 2026-01-11

## Goal
Move the "Journal Fit Profile" from single-journal view to an all-journals overview while keeping the baseline comparison and abstract matcher.

## Update
- Split "research abstract reverse matching" into its own tab for clearer separation from the fit overview.

## Changes
- UI: added an overview list showing fit score, confidence, sample size, and Topic/Method mini bars for each journal.
- UI: baseline panel now shows only the reference distributions (favorites or chosen journal).
- Logic: compute fit/confidence/structure scores per journal and sort in the list.
- Interaction: added list sorting; kept baseline selection behavior.

## Metrics
- Fit score: (like rate - dislike rate) * 100
- Confidence: log(total + 1) / log(51)
- Structure match: 0.7 * topic cosine + 0.3 * method cosine

## Follow-ups
- Confirm baseline-empty copy.
- Check readability with very large journal counts.
