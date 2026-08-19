# Graph Report - upande_agriculture  (2026-07-25)

## Corpus Check
- 73 files · ~57,478 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 371 nodes · 597 edges · 56 communities (35 shown, 21 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 60 edges (avg confidence: 0.55)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9e3d3e98`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- jspreadsheet.js
- api.py
- production_budget.js
- TestApi
- controllers.py
- calculate_weekly_projection
- jsuites.js
- import_mona_2026_data.py
- TestCropCycleController
- README.md
- migrate_projections_to_templates.py
- execute
- seed_agriculture_workspace.py
- execute
- execute
- execute
- CropCycle
- CropProtocol
- CropProtocolFlush
- CropProtocolGrowthStage
- FlowerTrial
- ProductionForecast
- ProductionForecastWeek
- ProductionPlanForm
- ProductionPlanTask
- ProductionPlanVariety
- ProductionProjection
- ProjectionWeek
- production_budget.py
- backfill_plan_planned_stems.py
- upande_agriculture

## God Nodes (most connected - your core abstractions)
1. `v()` - 20 edges
2. `renderGrid()` - 19 edges
3. `e()` - 16 edges
4. `TestApi` - 15 edges
5. `calculate_weekly_projection()` - 14 edges
6. `setStatus()` - 12 edges
7. `a()` - 11 edges
8. `upsert_todo()` - 11 edges
9. `TestCropCycleController` - 10 edges
10. `loadGrid()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `createItemElement()` --indirect_call--> `e()`  [INFERRED]
  upande_agriculture/public/lib/jspreadsheet/jsuites.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `renderGrid()` --indirect_call--> `m()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `renderGrid()` --indirect_call--> `x()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `evalFormula()` --indirect_call--> `n()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `evalFormula()` --indirect_call--> `p()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js

## Import Cycles
- None detected.

## Communities (56 total, 21 thin omitted)

### Community 0 - "jspreadsheet.js"
Cohesion: 0.06
Nodes (64): a(), ae(), at(), be(), c(), Ce(), ct(), d() (+56 more)

### Community 1 - "api.py"
Cohesion: 0.06
Nodes (43): _actual_week_array(), bulk_apply_formula(), bulk_update_aggregated_weeks(), bulk_update_projection_weeks(), copy_aggregated_row(), _forecast_week_array(), get_budget_grid(), get_prior_year_actuals() (+35 more)

### Community 2 - "production_budget.js"
Cohesion: 0.14
Nodes (37): applyBulkFormula(), applyFormulaToSelection(), applyHeatmapClasses(), bindShortcuts(), buildContextMenu(), bulkSetSourceForSelection(), bumpZoom(), changeSource() (+29 more)

### Community 3 - "TestApi"
Cohesion: 0.10
Nodes (14): Nightly job: read Actual Harvest, fill actual_stems on Projection Week., Returns count of Projection Week rows updated.      Walks every Production Proje, rollup_actuals(), FrappeTestCase, Tests for upande_agriculture.api — six whitelisted endpoints.  Run with:     ben, Creates an Item for the variety if it does not exist, returns its name., Recalculates a Hybrid projection and reports how many weeks changed., All returned rows must have cycle_status == 'Active'. (+6 more)

### Community 4 - "controllers.py"
Cohesion: 0.11
Nodes (17): _autoseed_milestone_tasks(), crop_cycle_on_update(), _due_date_for_plan(), _ensure_milestone_todos(), _ensure_projection(), production_plan_form_before_save(), production_plan_form_on_update(), date (+9 more)

### Community 5 - "calculate_weekly_projection"
Cohesion: 0.17
Nodes (11): bulk_set_aggregated_source(), Recalculate unlocked weeks for a Hybrid projection.      Schema note: Projection, Toggle a single Projection's source (Manual / Hybrid / Calculated)., Apply a source change to every projection under N aggregated rows.      rows: [{, regenerate_projection(), set_projection_source(), calculate_weekly_projection(), date (+3 more)

### Community 6 - "jsuites.js"
Cohesion: 0.14
Nodes (7): createItemElement(), gamma0(), gamma1(), rotr(), shr(), sigma0(), sigma1()

### Community 7 - "import_mona_2026_data.py"
Cohesion: 0.24
Nodes (12): length_suffix(), link_greenhouses(), map_gh(), normalize_variety(), parse_forecast_sheet(), _projection_budget_lookup(), Import real Mona data into mona2.local so the Production Budget spreadsheet show, Return {full_variety_name: {week_n: stems}} from a forecast sheet.      The shee (+4 more)

### Community 8 - "TestCropCycleController"
Cohesion: 0.36
Nodes (3): FrappeTestCase, A Plan Form saved for the week a cycle's pinch falls in should         auto-rece, TestCropCycleController

### Community 9 - "README.md"
Cohesion: 0.40
Nodes (4): Contributing, Installation, License, Upande Agriculture

## Knowledge Gaps
- **5 isolated node(s):** `upande_agriculture`, `Upande Agriculture`, `Installation`, `Contributing`, `License`
  These have ≤1 connection - possible missing edges or undocumented components.
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `e()` connect `jspreadsheet.js` to `jsuites.js`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **Why does `createItemElement()` connect `jsuites.js` to `jspreadsheet.js`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `renderGrid()` (e.g. with `m()` and `x()`) actually correct?**
  _`renderGrid()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `e()` (e.g. with `a()` and `c()`) actually correct?**
  _`e()` has 12 INFERRED edges - model-reasoned connections that need verification._
- **What connects `upande_agriculture`, `Upande Agriculture`, `Installation` to the rest of the system?**
  _5 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `jspreadsheet.js` be split into smaller, more focused modules?**
  _Cohesion score 0.06456140350877193 - nodes in this community are weakly interconnected._
- **Should `api.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06342494714587738 - nodes in this community are weakly interconnected._