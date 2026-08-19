# Graph Report - upande_agriculture  (2026-07-26)

## Corpus Check
- 76 files · ~57,625 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 396 nodes · 646 edges · 57 communities (35 shown, 22 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 60 edges (avg confidence: 0.55)
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
- tests/__init__.py

## God Nodes (most connected - your core abstractions)
1. `v()` - 20 edges
2. `renderGrid()` - 19 edges
3. `e()` - 16 edges
4. `TestApi` - 15 edges
5. `calculate_weekly_projection()` - 14 edges
6. `setStatus()` - 12 edges
7. `a()` - 11 edges
8. `TestGreenhouseController` - 11 edges
9. `upsert_todo()` - 11 edges
10. `make_warehouse()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `createItemElement()` --indirect_call--> `e()`  [INFERRED]
  upande_agriculture/public/lib/jspreadsheet/jsuites.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `evalFormula()` --indirect_call--> `n()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `evalFormula()` --indirect_call--> `p()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `ensureAssetsLoaded()` --indirect_call--> `s()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js
- `flushSave()` --indirect_call--> `u()`  [INFERRED]
  upande_agriculture/upande_agriculture/page/production_budget/production_budget.js → upande_agriculture/public/lib/jspreadsheet/jspreadsheet.js

## Import Cycles
- None detected.

## Communities (57 total, 22 thin omitted)

### Community 0 - "jspreadsheet.js"
Cohesion: 0.07
Nodes (60): a(), ae(), at(), be(), c(), Ce(), ct(), d() (+52 more)

### Community 1 - "api.py"
Cohesion: 0.05
Nodes (53): _actual_week_array(), bulk_apply_formula(), bulk_set_aggregated_source(), bulk_update_aggregated_weeks(), bulk_update_projection_weeks(), copy_aggregated_row(), _cycle_for_projection(), _forecast_week_array() (+45 more)

### Community 2 - "production_budget.js"
Cohesion: 0.12
Nodes (41): k(), m(), te(), x(), applyBulkFormula(), applyFormulaToSelection(), applyHeatmapClasses(), bindShortcuts() (+33 more)

### Community 3 - "TestApi"
Cohesion: 0.10
Nodes (14): Nightly job: read Actual Harvest, fill actual_stems on Projection Week., Returns count of Projection Week rows updated.      Walks every Production Proje, rollup_actuals(), FrappeTestCase, Creates an Item for the variety if it does not exist, returns its name., Recalculates a Hybrid projection and reports how many weeks changed., All returned rows must have cycle_status == 'Active'., Greenhouse filter narrows the result set. (+6 more)

### Community 4 - "controllers.py"
Cohesion: 0.19
Nodes (6): FrappeTestCase, ToDo validates its dynamic link, so the reference must really exist., TestTodoHelpers, date, Idempotent ToDo upsert keyed on (reference_type, reference_name, tag).  The tag, upsert_todo()

### Community 5 - "calculate_weekly_projection"
Cohesion: 0.10
Nodes (20): _autoseed_milestone_tasks(), _due_date_for_plan(), _ensure_milestone_todos(), _ensure_projection(), greenhouse_on_update(), production_plan_form_before_save(), production_plan_form_on_update(), _protocol_dict() (+12 more)

### Community 6 - "jsuites.js"
Cohesion: 0.14
Nodes (7): createItemElement(), gamma0(), gamma1(), rotr(), shr(), sigma0(), sigma1()

### Community 7 - "import_mona_2026_data.py"
Cohesion: 0.24
Nodes (12): length_suffix(), link_greenhouses(), map_gh(), normalize_variety(), parse_forecast_sheet(), _projection_budget_lookup(), Import real Mona data into mona2.local so the Production Budget spreadsheet show, Return {full_variety_name: {week_n: stems}} from a forecast sheet.      The shee (+4 more)

### Community 8 - "TestCropCycleController"
Cohesion: 0.16
Nodes (14): default_company(), default_uom(), ensure_supervisor_field(), make_warehouse(), Shared test fixtures.  Warehouse mandatory fields vary by site (upande_core adds, Not every site ships the ERPNext default 'Nos'., Milestone ToDos need Warehouse.custom_supervisor (shipped by upande_core)., Return the name of a non-group Warehouse, creating it if needed. (+6 more)

### Community 9 - "README.md"
Cohesion: 0.40
Nodes (4): Contributing, Installation, License, Upande Agriculture

## Knowledge Gaps
- **5 isolated node(s):** `upande_agriculture`, `Upande Agriculture`, `Installation`, `Contributing`, `License`
  These have ≤1 connection - possible missing edges or undocumented components.
- **22 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TestApi` connect `TestApi` to `TestCropCycleController`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Why does `e()` connect `jspreadsheet.js` to `jsuites.js`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `createItemElement()` connect `jsuites.js` to `jspreadsheet.js`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `renderGrid()` (e.g. with `m()` and `x()`) actually correct?**
  _`renderGrid()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `e()` (e.g. with `a()` and `c()`) actually correct?**
  _`e()` has 12 INFERRED edges - model-reasoned connections that need verification._
- **What connects `upande_agriculture`, `Upande Agriculture`, `Installation` to the rest of the system?**
  _5 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `jspreadsheet.js` be split into smaller, more focused modules?**
  _Cohesion score 0.06924882629107981 - nodes in this community are weakly interconnected._