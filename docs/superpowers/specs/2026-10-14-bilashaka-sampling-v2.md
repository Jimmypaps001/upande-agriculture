# Bilashaka forecast: sampling v2 (2026-10-14)

Agreed with Teddy 2026-10-14. Builds on `2026-10-08-bilashaka-harvest-forecast.md` (Part 1, shipped).
Research: `.superpowers/sdd/2026-10-08-bilashaka-harvest-forecast/sampling-research.md` (copied from the session scratchpad).

## Why
- One or two plots of 10 plants per section are far too few: plot-to-plot CV ~35–50% gives ±50–100% per section. Many small spots spread across the section beat one big plot.
- The farm manager counts the whole regrowth cycle, not only buds, so the forecast can see ~8 weeks ahead.
- Biggest single accuracy gain in practice: correct each section by its own actual ÷ forecast ratio.

## Decisions
1. **Stages (farm's own words; defaults, still overridable per variety on the Crop Protocol):**

   | Stage | Days to harvest | Default survival |
   |---|---|---|
   | Eye pop-up | 55 | 0.80 |
   | Maroon shoot | 36 (wide: spans ~22–50) | 0.85 |
   | Pea | 21 | 0.93 |
   | Bud | 14 | 0.97 |
   | Colour break | 7 | 0.99 |

   Pea→bud 7 d, bud→colour break 7 d, colour break→harvest 7 d (confirmed). Fresh cuts (day 0–8 after cut) are not counted: harvest records + the regrowth term cover them. Old names (Rice, Chickpea, Showing colour, Opening) stay valid if a protocol names them.
2. **Sampling: 8 beds per section, 5 plants per bed** ("sample spot"). Beds placed systematically across the section: usable beds = from_bed+1 … to_bed−1 (never the first/last bed); step = usable/8; a start in [0, step) seeded from the section (deterministic); bed_i = start + i·step. Position along the bed rotates ¼ → ½ → ¾ by i. Never the first/last ~2 m (a field instruction on the tag). Spots are permanent for a flush; a quarter move monthly (later; not in this build).
3. **Count every spot every 3 days**, plant by plant (plant 1…5), one tap per shoot/bud on its stage, plus two extra counts per spot: **blind shoots** and **missing/dead plants**. plants_counted = 5 − missing.
4. **Plot tags with QR** (content = the Sample Plot name, e.g. `PLOT-00042`), tied to plant 1 of the spot. Scanning a tag opens that spot. A supervisor's first scan of a new spot ("set up") confirms the real bed number.
5. **Per-section calibration ratio R** = actual stems ÷ forecast stems over the last 21 days, from snapshots made 0–2 days ahead. Applied (clamped 0.5–2.0) once ≥10 days and ≥300 forecast stems exist. A section with R outside 0.8–1.25 is flagged "spots unrepresentative".
6. **Horizon 56 days** (was 21) for the section forecast and snapshots; error-band buckets add 21–34 and 35–55.
7. **Quality checks** stored per count (`quality_flags`) and shown live in the app:
   - jump: a stage changed by more than max(3, 50%) vs the spot's previous count;
   - flow: a later stage now exceeds (its previous + the previous stage's previous + 2) — buds move at most one stage in 3 days;
   - fast: under 15 s for the spot (5 plants);
   - missing plants > 0.
   A second person counting the same spot the same day is a **recount**; the quality report compares them.

## API contract (upande_agriculture.forecast.api) — server and app build against this

`get_plot_plan(farm=None)` → `{count_interval_days, plots:[…]}`, each plot adds to today's fields:
```
position: "¼" | "½" | "¾" | null     # along the bed
set_up: 0|1                          # bed confirmed on the ground
plants: 5
last: null | {date, counts:{stage_name: n}, blind_shoots, missing_plants}
route_no: int                        # 1..n order within the section (by bed)
```
Stages come from `data.stages(variety)` as today (`stages:[{stage_name, days_to_harvest}]`), now the farm's names by default.

`submit_plot_count(client_uuid, sample_plot, counts, plants_counted=None, notes=None, captured_at=None, photos=None, plant_counts=None, blind_shoots=0, missing_plants=0, duration_seconds=None)`
- `plant_counts`: JSON `[{"plant": 1, "counts": {stage_name: n}}, …]` (optional; stored as JSON on Bed Sample).
- Server recomputes and stores `quality_flags` (newline list of short codes+text, e.g. `jump: Pea 4→40`). Returns `{name, flags:[…], photo_notes?}`.

`confirm_plot_bed(sample_plot, bed)` → sets `bed`, `set_up=1`. Idempotent.

`plot_tags_pdf(greenhouse, section=None)` → PDF download of the section's active spots, 10 per A4 (86×54 mm tags, like bucket tags): QR, "B2 GH3 A", "Bed 60 · ½ along", "Spot 3 of 8 · plants 1–5", "Start at this plant. Skip the first 2 m of the bed."

`get_bay_forecast(…, days=7)` unchanged shape; may add `ratio` (R) and `flags.unrepresentative`.

## Doctype changes
- **Sample Plot:** `position` (Select ¼/½/¾), `set_up` (Check), `started_on` (Date); `plants` default 5.
- **Bed Sample:** `plant_counts` (Long Text JSON), `blind_shoots` (Int), `missing_plants` (Int), `duration_seconds` (Int), `quality_flags` (Small Text), `is_recount` (Check), `counted_by` (Link User, default session user).

## Reports
- **Plot Count Quality** (Script Report): per section × day — spots due vs counted, flags, counter, seconds per spot, recount differences, current R.

## Constraints
- Mona never changes: upande_agriculture `bilashaka` branch and bilashaka-harvest only.
- Tests via `.superpowers/sdd/2026-10-08-bilashaka-harvest-forecast/runtests.sh` on bhcloud.local; never mona sites.
- App UI: the app's monochrome theme; no colour accents; interaction modelled on a tally counter (see memory feedback-app-ui-restraint).
