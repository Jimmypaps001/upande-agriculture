/**
 * Production Budget — annual spreadsheet view.
 *
 * Rows are aggregated by (greenhouse, base variety). Edits to a cell
 * redistribute proportionally across the underlying length variants
 * (e.g. editing "Athena W23" splits across Athena-50cm / -60cm / -70cm
 * by their current proportions). Compact mode shows budget numbers
 * with heatmap shading. Compare mode shows all four layers stacked
 * (Budget / Forecast / Plan / Actual) in every cell, read-only.
 *
 * Skin: Mona Flowers design system — ink shades on a cream surface,
 * Poppins / Fraunces / JetBrains Mono, signal teal #228883.
 */

frappe.pages["production_budget"].on_page_load = async function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("Production Budget"),
        single_column: true,
    });
    wrapper.classList.add("uagri-budget-page");
    $(wrapper).find(".page-head").hide();

    const state = {
        year: new Date().getFullYear(),
        mode: "compact",        // "compact" | "compare"
        greenhouseFilter: "",
        varietyFilter: "",
        showPriorYear: false,
        grid: null,
        prior: {},              // {key: [52 ints]} prior-year actuals
        loading: false,
        pendingEdits: new Map(),    // key: `${row_key}::${week}` -> {row_key, week, value}
        saveTimer: null,
    };

    await ensureAssetsLoaded();
    renderShell(page);
    await loadGrid();
    bindShortcuts();

    // -------------------------------------------------------------------------
    // assets
    // -------------------------------------------------------------------------

    async function ensureAssetsLoaded() {
        const base = "/assets/upande_agriculture/lib/jspreadsheet";
        const cssFiles = [
            "/assets/upande_agriculture/css/budget.css",
            `${base}/jspreadsheet.css`,
            `${base}/jsuites.css`,
        ];
        const jsFiles = [`${base}/jsuites.js`, `${base}/jspreadsheet.js`];
        for (const href of cssFiles) {
            if (document.querySelector(`link[href="${href}"]`)) continue;
            const l = document.createElement("link");
            l.rel = "stylesheet"; l.href = href;
            document.head.appendChild(l);
        }
        for (const src of jsFiles) {
            if (document.querySelector(`script[src="${src}"]`)) continue;
            await new Promise((resolve, reject) => {
                const s = document.createElement("script");
                s.src = src; s.onload = resolve; s.onerror = reject;
                document.head.appendChild(s);
            });
        }
    }

    // -------------------------------------------------------------------------
    // shell
    // -------------------------------------------------------------------------

    function renderShell(page) {
        const $body = $(page.body).empty();
        $body.html(`
            <div class="uagri-shell">
                <header class="uagri-head">
                    <div class="uagri-head__left">
                        <div class="uagri-head__eyebrow">Mona Flowers · Annual Plan</div>
                        <h1 class="uagri-head__title">Production Budget · <span class="uagri-year">${state.year}</span></h1>
                        <p class="uagri-head__sub">Each row is one variety in one greenhouse — summed across stem lengths. Edits redistribute proportionally to the underlying length variants. Drag the corner to fill, ⌘C/⌘V to copy ranges.</p>
                    </div>
                    <div class="uagri-head__tools">
                        <div class="uagri-pillgroup" data-target="mode">
                            <button data-val="compact" class="on">Compact</button>
                            <button data-val="compare">Compare 4 layers</button>
                        </div>
                        <label class="uagri-checkpill">
                            <input type="checkbox" data-target="priorYear" />
                            <span>Overlay ${state.year - 1} actuals</span>
                        </label>
                        <input class="uagri-yearpill" type="number" value="${state.year}" />
                    </div>
                </header>

                <div class="uagri-filters">
                    <select class="uagri-filter" data-target="greenhouse"><option value="">All greenhouses</option></select>
                    <select class="uagri-filter" data-target="variety"><option value="">All varieties</option></select>
                    <span class="uagri-status" data-status></span>
                </div>

                <div class="uagri-grid-wrap">
                    <div class="uagri-grid" id="uagri-grid"></div>
                </div>

                <footer class="uagri-foot">
                    <span class="uagri-foot__hint">⌘C / ⌘V copy-paste · drag the corner to fill · ⌘Z undo · auto-saves</span>
                    <span class="uagri-foot__legend" data-legend></span>
                </footer>

                <div class="uagri-bulk" data-bulk style="display:none">
                    <span class="uagri-bulk__count" data-bulk-count>0 cells</span>
                    <span class="uagri-bulk__sep"></span>
                    <button data-bulk-op="percent_add">+ %</button>
                    <button data-bulk-op="percent_sub">− %</button>
                    <button data-bulk-op="add">+ N</button>
                    <button data-bulk-op="subtract">− N</button>
                    <button data-bulk-op="set">Set all to…</button>
                </div>
            </div>
        `);

        $body.on("click", ".uagri-pillgroup button", (ev) => {
            const $btn = $(ev.currentTarget);
            const target = $btn.parent().data("target");
            $btn.siblings().removeClass("on");
            $btn.addClass("on");
            state[target] = $btn.data("val");
            loadGrid();
        });
        $body.on("change", 'input[data-target="priorYear"]', (ev) => {
            state.showPriorYear = ev.currentTarget.checked;
            loadGrid();
        });
        $body.on("change", ".uagri-yearpill", (ev) => {
            state.year = parseInt($(ev.currentTarget).val()) || new Date().getFullYear();
            $(".uagri-year", $body).text(state.year);
            loadGrid();
        });
        $body.on("change", ".uagri-filter", (ev) => {
            const t = $(ev.currentTarget).data("target");
            if (t === "greenhouse") state.greenhouseFilter = ev.currentTarget.value;
            if (t === "variety") state.varietyFilter = ev.currentTarget.value;
            rerenderGridOnly();
        });

        $body.on("click", ".uagri-bulk [data-bulk-op]", async (ev) => {
            const op = ev.currentTarget.dataset.bulkOp;
            await applyBulkFormula(op);
        });
    }

    function setStatus(msg) { $('[data-status]').text(msg || ""); }

    function renderLegend() {
        if (state.mode !== "compare") {
            $('[data-legend]').html(`
                <span><i style="background:rgba(34,136,131,0.45)"></i>cell heat = actual ÷ budget</span>
            `);
            return;
        }
        $('[data-legend]').html(`
            <span><i style="background:#0a0a0a"></i>Budget</span>
            <span><i style="background:#0ea5e9"></i>Forecast</span>
            <span><i style="background:#f59e0b"></i>Plan</span>
            <span><i style="background:#10b981"></i>Actual</span>
        `);
    }

    // -------------------------------------------------------------------------
    // data
    // -------------------------------------------------------------------------

    async function loadGrid() {
        if (state.loading) return;
        state.loading = true;
        setStatus("Loading…");
        try {
            const r = await frappe.call({
                method: "upande_agriculture.api.get_budget_grid",
                args: { year: state.year, mode: state.mode },
            });
            state.grid = r.message;
            if (state.showPriorYear) {
                try {
                    const pr = await frappe.call({
                        method: "upande_agriculture.api.get_prior_year_actuals",
                        args: { year: state.year },
                    });
                    state.prior = pr.message?.rows || {};
                } catch (e) { state.prior = {}; }
            } else {
                state.prior = {};
            }
            populateFilterOptions();
            renderGrid();
            renderLegend();
            setStatus(`${state.grid.rows.length} variety×greenhouse rows`);
        } catch (e) {
            setStatus("Load failed: " + (e?.message || e));
        } finally {
            state.loading = false;
        }
    }

    function populateFilterOptions() {
        if (!state.grid) return;
        const ghSet = new Set(), varSet = new Set();
        state.grid.rows.forEach(r => { ghSet.add(r.greenhouse); varSet.add(r.variety); });
        const fillSel = (sel, values, current) => {
            const $s = $(sel);
            const placeholder = $s.find("option:first").text();
            $s.empty().append(`<option value="">${placeholder}</option>`);
            [...values].filter(Boolean).sort().forEach(v => {
                $s.append(`<option value="${frappe.utils.escape_html(v)}" ${v === current ? "selected" : ""}>${frappe.utils.escape_html(v)}</option>`);
            });
        };
        fillSel('[data-target="greenhouse"]', ghSet, state.greenhouseFilter);
        fillSel('[data-target="variety"]', varSet, state.varietyFilter);
    }

    function visibleRows() {
        if (!state.grid) return [];
        return state.grid.rows.filter(r =>
            (!state.greenhouseFilter || r.greenhouse === state.greenhouseFilter) &&
            (!state.varietyFilter || r.variety === state.varietyFilter)
        );
    }

    function rerenderGridOnly() {
        renderGrid();
        renderLegend();
        setStatus(`${visibleRows().length} of ${state.grid?.rows.length || 0} shown`);
    }

    // -------------------------------------------------------------------------
    // sparkline & cell renderers
    // -------------------------------------------------------------------------

    function sparklineSVG(weeks, priorWeeks) {
        const max = Math.max(1, ...weeks, ...(priorWeeks || []));
        const W = 92, H = 26, P = 2;
        const pts = (arr) => arr.map((v, i) => {
            const x = P + (i / 51) * (W - P * 2);
            const y = H - P - (v / max) * (H - P * 2);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(" ");
        const prior = priorWeeks && priorWeeks.length
            ? `<polyline points="${pts(priorWeeks)}" fill="none" stroke="#b8b6ae" stroke-width="1" stroke-dasharray="2,2"/>`
            : "";
        return `<svg class="uagri-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${prior}
            <polyline points="${pts(weeks)}" fill="none" stroke="#0a0a0a" stroke-width="1.5"/>
        </svg>`;
    }

    function compareCell(b, f, p, a) {
        // 4 horizontal sparkbars, each colored layer + abbreviated number.
        // Bar lengths are proportional within the cell so the comparison
        // reads at a glance — equal bars = on plan, mismatched = off.
        const max = Math.max(1, b, f, p, a);
        const pct = (v) => Math.max(0, Math.min(100, (v / max) * 100));
        const bar = (cls, color, label, value) => {
            const w = pct(value);
            const visible = value > 0 ? "" : "uagri-cbar--zero";
            return `<div class="uagri-cbar ${cls} ${visible}">
                <span class="uagri-cbar__label" style="color:${color}">${label}</span>
                <span class="uagri-cbar__track">
                    <span class="uagri-cbar__fill" style="width:${w.toFixed(1)}%;background:${color}"></span>
                </span>
                <span class="uagri-cbar__value" style="color:${color}">${fmtFull(value)}</span>
            </div>`;
        };
        return `<div class="uagri-cstack">
            ${bar("uagri-cbar--b", "#0a0a0a", "B", b)}
            ${bar("uagri-cbar--f", "#0ea5e9", "F", f)}
            ${bar("uagri-cbar--p", "#f59e0b", "P", p)}
            ${bar("uagri-cbar--a", "#10b981", "A", a)}
        </div>`;
    }

    function fmtFull(n) {
        if (!n) return "·";
        return Number(n).toLocaleString();
    }

    function fmt(n) {
        if (!n) return "·";
        return Number(n).toLocaleString();
    }

    function varianceHTML(budget, actual) {
        const b = Number(budget) || 0;
        const a = Number(actual) || 0;
        if (!b && !a) return `<span class="uagri-var uagri-var--flat">—</span>`;
        if (!b) return `<span class="uagri-var uagri-var--up">+∞</span>`;
        const pct = ((a - b) / b) * 100;
        const sign = pct >= 0 ? "+" : "";
        const cls = pct >= -2 ? "up" : (pct >= -15 ? "warn" : "down");
        return `<span class="uagri-var uagri-var--${cls}">${sign}${pct.toFixed(0)}%</span>`;
    }

    function heatClass(actual, budget) {
        // Returns a heat band 0..4 for budget cell shading when in compact mode.
        // ratio = actual/budget; 0 if no actual.
        if (!budget) return "";
        if (!actual) return "";
        const r = actual / budget;
        if (r >= 1.10) return "uagri-heat-best";
        if (r >= 0.95) return "uagri-heat-good";
        if (r >= 0.80) return "uagri-heat-warn";
        return "uagri-heat-bad";
    }

    // -------------------------------------------------------------------------
    // grid render
    // -------------------------------------------------------------------------

    function renderGrid() {
        const host = document.getElementById("uagri-grid");
        if (!host) return;
        host.innerHTML = "";

        const rows = visibleRows();
        if (!rows.length) {
            host.innerHTML = `<div class="uagri-empty">No projections for ${state.year}. Create a Crop Cycle for this year — its Projection will appear here.</div>`;
            return;
        }

        const isCompare = state.mode === "compare";
        // Store cell HTML for compare cells / sparkline / variance pill so we
        // can DOM-inject after render (jspreadsheet's "html" type renders
        // inconsistently across versions; post-render injection is reliable).
        const htmlOverrides = [];   // [{x, y, html}]
        const data = rows.map((r, rIdx) => {
            const priorArr = state.prior[r.key];
            const actualTotal = (r.actual || []).reduce((s, v) => s + v, 0);
            // Numeric placeholders kept in the data; HTML applied post-render.
            const cells = [
                r.greenhouse || "—",
                r.variety || "—",
                "",   // sparkline cell — placeholder, populated below
            ];
            htmlOverrides.push({ x: 2, y: rIdx, html: sparklineSVG(r.weeks, priorArr) });

            for (let i = 0; i < 52; i++) {
                if (isCompare) {
                    cells.push("");
                    htmlOverrides.push({
                        x: i + 3, y: rIdx,
                        html: compareCell(r.weeks[i], r.forecast?.[i] || 0,
                                          r.plan?.[i] || 0, r.actual?.[i] || 0),
                    });
                } else {
                    cells.push(r.weeks[i] || 0);
                }
            }
            cells.push(r.total);
            cells.push(actualTotal);
            cells.push("");
            htmlOverrides.push({ x: 57, y: rIdx, html: varianceHTML(r.total, actualTotal) });
            cells.push(r.source);
            return cells;
        });

        // Columns: 0=GH 1=Variety 2=Pattern 3..54=Weeks 55=Total 56=Actual 57=Var% 58=Mode
        const columns = [
            { type: "text", title: "Greenhouse", width: 140, readOnly: true },
            { type: "text", title: "Variety",    width: 130, readOnly: true },
            { type: "text", title: "Pattern",    width: 104, readOnly: true, align: "left" },
        ];
        for (let w = 1; w <= 52; w++) {
            columns.push({
                type: isCompare ? "text" : "numeric",
                title: `W${w}`,
                width: isCompare ? 132 : 56,
                readOnly: isCompare,
                mask: isCompare ? undefined : "#,##",
                align: "right",
            });
        }
        columns.push({ type: "numeric", title: "Budget",  width: 92, readOnly: true, mask: "#,##" });
        columns.push({ type: "numeric", title: "Actual",  width: 92, readOnly: true, mask: "#,##" });
        columns.push({ type: "text",    title: "Var %",   width: 80, readOnly: true, align: "center" });
        columns.push({ type: "dropdown", title: "Mode", width: 110,
                       source: ["Manual", "Hybrid", "Calculated from Protocol", "Mixed"] });

        const monthLabels = state.grid.month_labels;
        const monthOffsets = state.grid.month_offsets;
        const nestedHeaders = [[
            { title: "", colspan: 3 },
            ...monthLabels.map((m, i) => {
                const start = monthOffsets[i];
                const end = i < 11 ? monthOffsets[i + 1] - 1 : 52;
                return { title: m, colspan: end - start + 1 };
            }),
            { title: "", colspan: 4 },
        ]];

        if (window.uagriSheet) {
            try { window.uagriSheet.destroy(); } catch (e) { /* noop */ }
        }
        window.uagriSheet = jspreadsheet(host, {
            data,
            columns,
            nestedHeaders,
            tableOverflow: true,
            tableHeight: "calc(100vh - 360px)",
            tableWidth: "100%",
            freezeColumns: 3,
            columnSorting: false,
            columnDrag: false,
            allowInsertColumn: false,
            allowDeleteColumn: false,
            allowInsertRow: false,
            allowDeleteRow: false,
            allowExport: true,
            csvFileName: `mona-budget-${state.year}`,
            contextMenu: (obj, x, y) => buildContextMenu(obj, x, y, rows),
            onchange: handleCellChange,
            onpaste: () => { scheduleSave(); updateBulkBarVisibility(); },
            onselection: (instance, x1, y1, x2, y2) => updateBulkBarVisibility(x1, y1, x2, y2),
        });

        window.uagriRowsRef = rows;
        injectHTMLOverrides(htmlOverrides);
        applyHeatmapClasses();
        decorateBulkBar();
        wrapper.classList.toggle("uagri-compare", isCompare);
    }

    function injectHTMLOverrides(overrides) {
        // jspreadsheet escapes HTML in 'text' type cells; we punch the HTML
        // back in via innerHTML after render so cells reliably display
        // sparklines, compare-stacks, and variance pills.
        const tbody = document.querySelector("#uagri-grid table tbody");
        if (!tbody) return;
        overrides.forEach(({ x, y, html }) => {
            const td = tbody.querySelector(`tr:nth-child(${y + 1}) td[data-x="${x}"]`);
            if (td) td.innerHTML = html;
        });
    }

    function applyHeatmapClasses() {
        if (state.mode !== "compact") return;
        const rows = window.uagriRowsRef || [];
        const tbody = document.querySelector("#uagri-grid table tbody");
        if (!tbody) return;
        rows.forEach((r, rowIdx) => {
            const actuals = r.actual || [];
            for (let i = 0; i < 52; i++) {
                const td = tbody.querySelector(`tr:nth-child(${rowIdx + 1}) td[data-x="${i + 3}"]`);
                if (!td) continue;
                td.classList.remove("uagri-heat-best", "uagri-heat-good", "uagri-heat-warn", "uagri-heat-bad", "uagri-zero");
                if (!r.weeks[i]) td.classList.add("uagri-zero");
                const hc = heatClass(actuals[i] || 0, r.weeks[i] || 0);
                if (hc) td.classList.add(hc);
            }
        });
    }

    // -------------------------------------------------------------------------
    // edit handling
    // -------------------------------------------------------------------------

    function handleCellChange(instance, cell, x, y, value) {
        const col = parseInt(x);
        const row = parseInt(y);
        const r = window.uagriRowsRef?.[row];
        if (!r) return;
        // x: 0=GH 1=Variety 2=Pattern 3..54=Weeks 55=Budget 56=Actual 57=Var% 58=Mode
        if (col >= 3 && col <= 54) {
            const week = col - 2;  // x=3 -> week 1
            const numeric = parseInt(String(value).replace(/[, ]/g, "")) || 0;
            queueEdit(r, week, numeric);
        } else if (col === 58) {
            changeSource(r, value);
        }
    }

    function queueEdit(r, week, value) {
        const key = `${r.key}::${week}`;
        state.pendingEdits.set(key, {
            greenhouse: r.greenhouse,
            variety_base: r.variety,
            year: state.year,
            week,
            value,
        });
        setStatus(`Pending ${state.pendingEdits.size} edits…`);
        scheduleSave();
    }

    function scheduleSave() {
        if (state.saveTimer) clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(flushSave, 700);
    }

    async function flushSave() {
        if (!state.pendingEdits.size) return;
        const updates = [...state.pendingEdits.values()];
        state.pendingEdits.clear();
        setStatus("Saving…");
        try {
            await frappe.call({
                method: "upande_agriculture.api.bulk_update_aggregated_weeks",
                args: { updates },
            });
            setStatus(`Saved ${updates.length} cells · ${new Date().toLocaleTimeString()}`);
            refreshTotalsLocally(updates);
        } catch (e) {
            setStatus("Save failed: " + (e?.message || e));
            updates.forEach(u => state.pendingEdits.set(`${u.greenhouse}||${u.variety_base}::${u.week}`, u));
        }
    }

    function refreshTotalsLocally(updates) {
        // Update in-memory rows and just the derived columns (Total,
        // Actual, Var%, Pattern). DO NOT destroy/recreate the sheet —
        // that's the full-screen flicker the user reported.
        const rowsRef = window.uagriRowsRef || [];
        const touchedRowIdx = new Set();
        // Apply each update to the in-memory row's weeks array.
        for (const u of updates) {
            const idx = rowsRef.findIndex(r =>
                r.greenhouse === u.greenhouse && r.variety === u.variety_base);
            if (idx < 0) continue;
            const r = rowsRef[idx];
            const w = parseInt(u.week);
            if (w >= 1 && w <= 52) {
                r.weeks[w - 1] = parseInt(u.value) || 0;
                touchedRowIdx.add(idx);
            }
        }
        // Recompute totals + repaint trailing columns + sparkline.
        const tbody = document.querySelector("#uagri-grid table tbody");
        if (!tbody) return;
        for (const idx of touchedRowIdx) {
            const r = rowsRef[idx];
            const newTotal = r.weeks.reduce((s, v) => s + (v || 0), 0);
            r.total = newTotal;
            // Total column (x=55)
            const tdTotal = tbody.querySelector(`tr:nth-child(${idx + 1}) td[data-x="55"]`);
            if (tdTotal) tdTotal.textContent = fmtFull(newTotal);
            // Var% column (x=57)
            const tdVar = tbody.querySelector(`tr:nth-child(${idx + 1}) td[data-x="57"]`);
            const actualTotal = (r.actual || []).reduce((s, v) => s + v, 0);
            if (tdVar) tdVar.innerHTML = varianceHTML(newTotal, actualTotal);
            // Sparkline (x=2)
            const tdSpark = tbody.querySelector(`tr:nth-child(${idx + 1}) td[data-x="2"]`);
            if (tdSpark) tdSpark.innerHTML = sparklineSVG(r.weeks, state.prior[r.key]);
            // Heatmap reshade for this row
            if (state.mode === "compact") {
                for (let i = 0; i < 52; i++) {
                    const td = tbody.querySelector(`tr:nth-child(${idx + 1}) td[data-x="${i + 3}"]`);
                    if (!td) continue;
                    td.classList.remove("uagri-heat-best", "uagri-heat-good",
                                         "uagri-heat-warn", "uagri-heat-bad", "uagri-zero");
                    if (!r.weeks[i]) td.classList.add("uagri-zero");
                    const hc = heatClass((r.actual || [])[i] || 0, r.weeks[i] || 0);
                    if (hc) td.classList.add(hc);
                }
            }
        }
    }

    async function changeSource(r, source) {
        await flushSave();
        setStatus(`Setting ${r.variety} @ ${r.greenhouse} → ${source}…`);
        try {
            await frappe.call({
                method: "upande_agriculture.api.bulk_set_aggregated_source",
                args: { rows: [{ greenhouse: r.greenhouse, variety_base: r.variety, year: state.year }],
                        source },
            });
            await loadGrid();
        } catch (e) {
            setStatus("Source change failed: " + (e?.message || e));
        }
    }

    // -------------------------------------------------------------------------
    // bulk operations
    // -------------------------------------------------------------------------

    function getSelection() {
        // Returns {x1, y1, x2, y2} of the currently-selected range or null.
        const sheet = window.uagriSheet;
        if (!sheet) return null;
        const sel = sheet.getSelection ? sheet.getSelection() : null;
        if (!sel || sel.length < 4) return null;
        const [x1, y1, x2, y2] = sel;
        return { x1, y1, x2, y2 };
    }

    function updateBulkBarVisibility(x1, y1, x2, y2) {
        const bar = document.querySelector("[data-bulk]");
        if (!bar) return;
        let cells = 0;
        if (x1 !== undefined) {
            const lo = Math.min(x1, x2), hi = Math.max(x1, x2);
            const ylo = Math.min(y1, y2), yhi = Math.max(y1, y2);
            // Only count week cells (x 3..54)
            const xspan = Math.max(0, Math.min(hi, 54) - Math.max(lo, 3) + 1);
            const yspan = yhi - ylo + 1;
            if (xspan > 0 && yspan > 0) cells = xspan * yspan;
        }
        if (cells > 1 && state.mode === "compact") {
            bar.style.display = "flex";
            bar.querySelector("[data-bulk-count]").textContent = `${cells} cells`;
            bar.dataset.selX1 = x1; bar.dataset.selY1 = y1;
            bar.dataset.selX2 = x2; bar.dataset.selY2 = y2;
        } else {
            bar.style.display = "none";
        }
    }

    function decorateBulkBar() {
        document.querySelector("[data-bulk]")?.style.setProperty("display", "none");
    }

    async function applyBulkFormula(op) {
        const bar = document.querySelector("[data-bulk]");
        if (!bar) return;
        const x1 = parseInt(bar.dataset.selX1), y1 = parseInt(bar.dataset.selY1);
        const x2 = parseInt(bar.dataset.selX2), y2 = parseInt(bar.dataset.selY2);
        if (isNaN(x1)) return;

        const promptMap = {
            percent_add: "Add what percent? (e.g. 10 → +10%)",
            percent_sub: "Subtract what percent? (e.g. 10 → −10%)",
            add: "Add how many stems?",
            subtract: "Subtract how many stems?",
            set: "Set every selected cell to which value?",
        };
        const input = window.prompt(promptMap[op] || "Value?", "0");
        if (input == null) return;
        const operand = parseFloat(String(input).replace(/[, ]/g, ""));
        if (!isFinite(operand)) {
            frappe.show_alert({ message: "Not a number.", indicator: "red" });
            return;
        }

        const xlo = Math.max(3, Math.min(x1, x2));
        const xhi = Math.min(54, Math.max(x1, x2));
        const ylo = Math.min(y1, y2);
        const yhi = Math.max(y1, y2);
        const rowsRef = window.uagriRowsRef || [];
        const updates = [];
        for (let y = ylo; y <= yhi; y++) {
            const r = rowsRef[y];
            if (!r) continue;
            for (let x = xlo; x <= xhi; x++) {
                const w = x - 2;
                updates.push({
                    greenhouse: r.greenhouse,
                    variety_base: r.variety,
                    year: state.year,
                    week: w,
                    current: r.weeks[w - 1] || 0,
                });
            }
        }
        if (!updates.length) return;
        await flushSave();
        setStatus(`Applying ${op} (${operand}) to ${updates.length} cells…`);
        try {
            await frappe.call({
                method: "upande_agriculture.api.bulk_apply_formula",
                args: { updates, operation: op, operand },
            });
            await loadGrid();
            setStatus(`Applied ${op} (${operand}) to ${updates.length} cells.`);
        } catch (e) {
            setStatus("Bulk apply failed: " + (e?.message || e));
        }
    }

    // -------------------------------------------------------------------------
    // context menu
    // -------------------------------------------------------------------------

    function buildContextMenu(obj, x, y, rows) {
        const r = rows[y];
        const items = [
            { title: "Copy", onclick: () => obj.copy(true) },
            { title: "Paste", onclick: () => obj.paste(x, y, "") },
            { type: "line" },
            { title: "Recalc from Crop Protocol",
              onclick: () => bulkSetSourceForSelection("Hybrid") },
            { title: "Set selected rows to Manual",
              onclick: () => bulkSetSourceForSelection("Manual") },
        ];
        if (r) {
            items.push({ type: "line" });
            items.push({
                title: `Copy this row to another greenhouse…`,
                onclick: () => copyRowToGreenhouse(r),
            });
        }
        return items;
    }

    async function bulkSetSourceForSelection(source) {
        const sheet = window.uagriSheet;
        const sel = sheet?.getSelectedRows?.() || [];
        const rowsRef = window.uagriRowsRef || [];
        const targets = (sel.length ? sel : []).map(i => rowsRef[i]).filter(Boolean);
        if (!targets.length) return frappe.show_alert("Select rows first.");
        await flushSave();
        setStatus(`Updating ${targets.length} rows → ${source}…`);
        try {
            await frappe.call({
                method: "upande_agriculture.api.bulk_set_aggregated_source",
                args: {
                    rows: targets.map(r => ({
                        greenhouse: r.greenhouse, variety_base: r.variety, year: state.year,
                    })),
                    source,
                },
            });
            await loadGrid();
        } catch (e) {
            setStatus("Failed: " + (e?.message || e));
        }
    }

    async function copyRowToGreenhouse(r) {
        const ghs = [...new Set((state.grid?.rows || []).map(x => x.greenhouse).filter(Boolean))]
            .filter(g => g !== r.greenhouse);
        if (!ghs.length) return frappe.show_alert("No other greenhouses to copy to.");
        const target = await new Promise(resolve => {
            const d = new frappe.ui.Dialog({
                title: __("Copy '{0}' to another greenhouse", [r.variety]),
                fields: [
                    { fieldname: "target_greenhouse", label: __("Target Greenhouse"),
                      fieldtype: "Select", options: ghs.join("\n"), reqd: 1 },
                ],
                primary_action_label: __("Copy 52-week pattern"),
                primary_action: (v) => { d.hide(); resolve(v.target_greenhouse); },
            });
            d.show();
        });
        if (!target) return;
        setStatus(`Copying ${r.variety} pattern to ${target}…`);
        try {
            await frappe.call({
                method: "upande_agriculture.api.copy_aggregated_row",
                args: {
                    source_greenhouse: r.greenhouse,
                    source_variety_base: r.variety,
                    target_greenhouse: target,
                    year: state.year,
                },
            });
            await loadGrid();
            setStatus(`Copied to ${target}.`);
        } catch (e) {
            setStatus("Copy failed: " + (e?.message || e));
        }
    }

    // -------------------------------------------------------------------------
    // keyboard
    // -------------------------------------------------------------------------

    function bindShortcuts() {
        document.addEventListener("keydown", async (ev) => {
            if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") {
                ev.preventDefault();
                await flushSave();
            }
        });
        window.addEventListener("beforeunload", () => {
            if (state.pendingEdits.size) flushSave();
        });
    }
};
