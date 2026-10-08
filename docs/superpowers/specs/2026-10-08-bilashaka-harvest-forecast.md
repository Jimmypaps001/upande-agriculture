# Bilashaka harvest forecast: spec

Agreed with Teddy on 2026-10-08. Based on "Phenology-Driven Yield Forecasting System v2.0 (staggered harvest)", adapted to Bilashaka.

## Farm reality
- A **bay is a greenhouse section** (upande_core `Warehouse Section`), with one variety and one person who harvests it for weeks.
- Roses are harvested continuously. A section holds buds at every stage, and the person cuts only what is ready, daily or every other day.
- A stem cut today regrows a new flower about 8 weeks later.

## What the system does
1. **Permanent sample plots.** Each section has 1–2 fixed plots of 10 plants (`Sample Plot`), created automatically and marked with a tag in the field. Counting the same plants repeatedly gives a running count of buds at each stage.
2. **Plot counts on the phone.** The section's own worker counts their plots about every 3 days during rounds:
   - taps + per bud stage;
   - takes at least one photo;
   - works offline.
3. **Stages.** The default stages are Rice, Pea, Chickpea, Showing colour and Opening, overridable per variety on the Crop Protocol. Opening exists because buds due in the next few days are already past colour stage; without it the latest count would miss them. Each stage has:
   - days to harvest;
   - a spread (± days, default 20% of the days);
   - survival %.

   Defaults at ~18 °C come from Monroy, Pérez & Cure (2003), as cited in La Salle (2008): Rice 28 days, Pea 22, Chickpea 15, Colour 8, Opening 3. Survival defaults are 85 / 88 / 92 / 97 / 99%.
4. **Daily forecast per section, 21 days ahead.**
   - **Counted buds:** buds per plant × section plants (`Warehouse Section.custom_plants`) × stage survival × learned survival, spread across days by each stage's curve (a normal distribution cut into whole days, mean and spread × the learned timing scale).
   - **Regrowth from cuts:** stems already cut in the section × learned regrowth yield, landing about regrowth-days later (learned, default 56 days or the protocol's weeks between cuts). A cut whose new bud was already visible at the last count is skipped, because it is already in the bud count.
   - **Temperature:** if `Greenhouse Temperature` logs exist, timing scales by degree-days (base 5.3 °C) relative to the calibration period.
   - **Prediction band:** 10–90% for each day, from the learned error by horizon. The defaults widen with distance.
5. **Self-calibration, weekly per variety.** Using the last 10 weeks of plot counts and actual picks per section per day, the model fits the timing scale, bud survival, regrowth days and regrowth yield, and records the error bands and MAPE by horizon. With too little data it keeps the defaults and says why.
6. **Daily snapshots** of every section's 21-day forecast, kept as an audit trail and scored against actual picks in an accuracy report.
7. **Outputs.**
   - **Section Harvest Forecast** report: daily per section.
   - **Weekly Harvest Forecast** report: per variety per week for sales, including actual-so-far for the current week, split by length (measured mix, else the protocol's).
   - **"Your bay" card** on the phone: today, tomorrow and the next 7 days.
8. **Camera training data.** Every plot count carries photos plus its stage counts, exportable as a dataset (images + labels.csv). On-device stage recognition is trained once enough labelled photos exist; the export is the hand-off.

## Data sources
- **Actual picks / cuts per section per day:** submitted `Harvesting` Stock Entries with `custom_greenhouse` + `custom_section`. `createHarvestEntry` must stamp `custom_section`.
- **Plants per section:** `Warehouse Section.custom_plants`. If it's blank, only the regrowth part is forecast and the section is flagged.

## Part 2: camera, bay imagery and learned models (in scope, Teddy 2026-10-08)

Frappe Cloud runs no torch, so detection runs on the phone (TFLite), training on Kaggle, and the learned forecast in numpy.

1. **Bud-stage detector.**
   - OWLv2 finds the buds on plot photos, and each photo's worker counts assign them stages (ranked by petal colour and size).
   - It then trains YOLO11n with 5 stage classes on Kaggle.
   - **Gate on held-out photos:** stage-share MAE ≤ 0.15 and Spearman ρ ≥ 0.6. Training waits for ≥150 photos.
2. **Camera-guided counts.**
   - The phone pre-fills a plot's counters from its photos, and the worker corrects them.
   - What the camera said is kept (`camera_counts`) to measure and improve it.
3. **Bay imagery.**
   - **Phone walk scan:** an auto photo every 2 s along the path, detected on the phone.
   - **Drone photos:** attached in Desk and processed off-cloud by `process_bay_scans.py`.
   - Both give buds per frame by stage. Scan ÷ plot-photo detections (same camera, 14 days, clipped 0.5–1.5) corrects how representative a section's plots are, applied to the counted-bud part only.
4. **Learned correction.**
   - Ridge regression on log(actual / physical forecast), using weekday, count age, temperature and recent picking.
   - Used only if walk-forward validation cuts the error by ≥5%.
5. **Sequence model.**
   - A GRU over each section's last 28 days predicts the next 14. It is trained offline (torch) once ≥300 windows exist, and runs as a numpy forward pass.
   - It is used only in the horizon buckets where it validated better than the physical forecast.

## Constraints
- Mona never changes. Everything goes on the `bilashaka` branches of upande_agriculture and upande_harvest, plus the separate bilashaka-harvest app.
- No new Python dependencies: use `math`, and `numpy` only if needed (it's already a dependency).
