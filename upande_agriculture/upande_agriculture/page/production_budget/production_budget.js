/**
 * Production Budget — annual spreadsheet view.
 *
 * Each row is ONE Production Projection (variety × greenhouse). 52 week
 * columns plus identifier columns on the left and a total on the right.
 * Click to edit, arrow nav, Cmd+C/V copy-paste, drag-fill from corner,
 * Cmd+Z undo. Manual / Auto toggle per row.
 *
 * Skin: matches the Mona Flowers design system — ink shades on a cream
 * surface, Poppins/Fraunces/JetBrains Mono, signal teal #228883.
 */

frappe.pages["production_budget"].on_page_load = async function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("Production Budget"),
        single_column: true,
    });

    // Strip default page chrome — we render our own header.
    wrapper.classList.add("uagri-budget-page");
    $(wrapper).find(".page-head").hide();

    const state = {
        year: new Date().getFullYear(),
        mode: "compact",   // "compact" | "compare"
        primary: "budget", // "budget" | "forecast" | "plan" | "actual" — compare mode only
        greenhouseFilter: "",
        varietyFilter: "",
        grid: null,
        loading: false,
        // pending edits buffered for debounced save
        pendingEdits: new Map(),  // key: `${projection}::${week}` -> {projection, week, value}
        saveTimer: null,
    };

    await ensureAssetsLoaded();
    renderShell(page);
    await loadGrid();
    bindShortcuts();

    // --- asset loader --------------------------------------------------------

    async function ensureAssetsLoaded() {
        const base = "/assets/upande_agriculture/lib/jspreadsheet";
        const cssFiles = [
            "/assets/upande_agriculture/css/budget.css",
            `${base}/jspreadsheet.css`,
            `${base}/jsuites.css`,
        ];
        const jsFiles = [
            `${base}/jsuites.js`,
            `${base}/jspreadsheet.js`,
        ];
        for (const href of cssFiles) {
            if (!document.querySelector(`link[href="${href}"]`)) {
                const l = document.createElement("link");
                l.rel = "stylesheet";
                l.href = href;
                document.head.appendChild(l);
            }
        }
        for (const src of jsFiles) {
            if (document.querySelector(`script[src="${src}"]`)) continue;
            await new Promise((resolve, reject) => {
                const s = document.createElement("script");
                s.src = src;
                s.onload = resolve;
                s.onerror = reject;
                document.head.appendChild(s);
            });
        }
    }

    // --- shell ----------------------------------------------------------------

    function renderShell(page) {
        const $body = $(page.body).empty();
        $body.html(`
            <div class="uagri-shell">
                <header class="uagri-head">
                    <div class="uagri-head__left">
                        <div class="uagri-head__eyebrow">Mona Flowers · Annual Plan</div>
                        <h1 class="uagri-head__title">Production Budget · <span class="uagri-year">${state.year}</span></h1>
                        <p class="uagri-head__sub">Each row is one variety in one greenhouse. Edit cells directly — drag the corner to fill, ⌘C/⌘V to copy ranges.</p>
                    </div>
                    <div class="uagri-head__tools">
                        <div class="uagri-pillgroup" data-target="mode">
                            <button data-val="compact" class="on">Compact</button>
                            <button data-val="compare">Compare 4 layers</button>
                        </div>
                        <div class="uagri-pillgroup uagri-primary-pill" data-target="primary" style="display:none">
                            <button data-val="budget" class="on">Budget</button>
                            <button data-val="forecast">Forecast</button>
                            <button data-val="plan">Plan</button>
                            <button data-val="actual">Actual</button>
                        </div>
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
                    <span class="uagri-foot__hint">⌘C / ⌘V copy-paste · drag corner to fill · ⌘Z undo · auto-saves</span>
                    <span class="uagri-foot__legend" data-legend></span>
                </footer>
            </div>
        `);

        // mode toggle
        $body.on("click", ".uagri-pillgroup button", (ev) => {
            const $btn = $(ev.currentTarget);
            const target = $btn.parent().data("target");
            $btn.siblings().removeClass("on");
            $btn.addClass("on");
            state[target] = $btn.data("val");
            if (target === "mode") {
                $(".uagri-primary-pill", $body).toggle(state.mode === "compare");
                loadGrid();
            } else if (target === "primary") {
                rerenderGridOnly();
            }
        });

        // year
        $body.on("change", ".uagri-yearpill", (ev) => {
            state.year = parseInt($(ev.currentTarget).val()) || new Date().getFullYear();
            $(".uagri-year", $body).text(state.year);
            loadGrid();
        });

        // filters (re-render only)
        $body.on("change", ".uagri-filter", (ev) => {
            const t = $(ev.currentTarget).data("target");
            if (t === "greenhouse") state.greenhouseFilter = ev.currentTarget.value;
            if (t === "variety") state.varietyFilter = ev.currentTarget.value;
            rerenderGridOnly();
        });
    }

    function setStatus(msg) {
        $('[data-status]').text(msg || "");
    }

    function renderLegend() {
        if (state.mode !== "compare") {
            $('[data-legend]').empty();
            return;
        }
        $('[data-legend]').html(`
            <span><i style="background:#0a0a0a"></i>Budget</span>
            <span><i style="background:#0ea5e9"></i>Forecast</span>
            <span><i style="background:#f59e0b"></i>Plan</span>
            <span><i style="background:#10b981"></i>Actual</span>
        `);
    }

    // --- data loading --------------------------------------------------------

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
            populateFilterOptions();
            renderGrid();
            renderLegend();
            setStatus(`${state.grid.rows.length} projections loaded`);
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

    // --- spreadsheet render --------------------------------------------------

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

    function renderGrid() {
        const host = document.getElementById("uagri-grid");
        if (!host) return;
        host.innerHTML = "";

        const rows = visibleRows();
        if (!rows.length) {
            host.innerHTML = `<div class="uagri-empty">No projections for ${state.year}. Create a Crop Cycle for this year — its Projection will appear here.</div>`;
            return;
        }

        // Build the data matrix
        const layer = (r) => {
            if (state.mode === "compact") return r.weeks;
            const arr = { budget: r.weeks, forecast: r.forecast, plan: r.plan, actual: r.actual }[state.primary] || r.weeks;
            return arr || new Array(52).fill(0);
        };
        const data = rows.map(r => {
            const cells = [r.greenhouse || "—", r.variety || "—"];
            const weeks = layer(r);
            for (let i = 0; i < 52; i++) cells.push(weeks[i] || 0);
            cells.push(r.total);
            cells.push(r.source);
            return cells;
        });

        // Column defs — 2 identifier + 52 week + total + source
        const columns = [
            { type: "text", title: "Greenhouse", width: 130, readOnly: true },
            { type: "text", title: "Variety", width: 170, readOnly: true },
        ];
        for (let w = 1; w <= 52; w++) {
            columns.push({ type: "numeric", title: `W${w}`, width: 56, mask: "#,##" });
        }
        columns.push({ type: "numeric", title: "Total", width: 88, readOnly: true, mask: "#,##" });
        columns.push({ type: "dropdown", title: "Mode", width: 100,
                       source: ["Manual", "Hybrid", "Calculated from Protocol"] });

        // Nested headers (months above weeks)
        const monthLabels = state.grid.month_labels;
        const monthOffsets = state.grid.month_offsets;  // start week of each month
        const nestedHeaders = [[
            { title: "", colspan: 2 },
            ...monthLabels.map((m, i) => {
                const start = monthOffsets[i];
                const end = i < 11 ? monthOffsets[i + 1] - 1 : 52;
                return { title: m, colspan: end - start + 1 };
            }),
            { title: "", colspan: 2 },
        ]];

        // Instantiate jspreadsheet
        if (window.uagriSheet) {
            try { window.uagriSheet.destroy(); } catch (e) { /* noop */ }
        }
        window.uagriSheet = jspreadsheet(host, {
            data,
            columns,
            nestedHeaders,
            tableOverflow: true,
            tableHeight: "calc(100vh - 320px)",
            tableWidth: "100%",
            freezeColumns: 2,
            columnSorting: false,
            columnDrag: false,
            allowInsertColumn: false,
            allowDeleteColumn: false,
            allowInsertRow: false,
            allowDeleteRow: false,
            allowExport: true,
            csvFileName: `mona-budget-${state.year}`,
            contextMenu: function (obj, x, y) {
                return [
                    { title: "Copy", onclick: () => obj.copy(true) },
                    { title: "Paste", onclick: () => obj.paste(x, y, "") },
                    { type: "line" },
                    { title: "Set row to Manual",
                      onclick: () => bulkSetSource(x, y, "Manual") },
                    { title: "Set row to Hybrid (auto-recalc)",
                      onclick: () => bulkSetSource(x, y, "Hybrid") },
                ];
            },
            onchange: handleCellChange,
            onpaste: () => { scheduleSave(); },
        });

        // Save the projection name keyed by row index for handlers
        window.uagriRowProjections = rows.map(r => r.projection);
        decorateHeader();
    }

    function decorateHeader() {
        // Re-style the nested header band based on month
        const head = document.querySelector("#uagri-grid table thead");
        if (!head) return;
        head.classList.add("uagri-thead");
    }

    function handleCellChange(instance, cell, x, y, value) {
        const col = parseInt(x);
        const row = parseInt(y);
        const projection = window.uagriRowProjections?.[row];
        if (!projection) return;

        // columns 0,1 = identifiers; 2..53 = weeks; 54 = total; 55 = source
        if (col >= 2 && col <= 53) {
            const week = col - 1;  // col 2 -> week 1
            const numeric = parseInt(String(value).replace(/[, ]/g, "")) || 0;
            queueEdit(projection, week, numeric);
        } else if (col === 55) {
            changeSource(projection, value);
        }
    }

    // --- buffered save -------------------------------------------------------

    function queueEdit(projection, week, value) {
        state.pendingEdits.set(`${projection}::${week}`,
                                { projection, week, value });
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
                method: "upande_agriculture.api.bulk_update_projection_weeks",
                args: { updates },
            });
            setStatus(`Saved ${updates.length} cells · ${new Date().toLocaleTimeString()}`);
        } catch (e) {
            setStatus("Save failed: " + (e?.message || e));
            // requeue so user doesn't lose data
            updates.forEach(u => state.pendingEdits.set(`${u.projection}::${u.week}`, u));
        }
    }

    async function changeSource(projection, source) {
        await flushSave();
        setStatus(`Switching ${projection} to ${source}…`);
        try {
            await frappe.call({
                method: "upande_agriculture.api.set_projection_source",
                args: { projection, source },
            });
            await loadGrid();
        } catch (e) {
            setStatus("Source change failed: " + (e?.message || e));
        }
    }

    async function bulkSetSource(x, y, source) {
        const sel = window.uagriSheet?.getSelectedRows?.() || [];
        const projections = (sel.length ? sel : [parseInt(y)])
            .map(i => window.uagriRowProjections[i])
            .filter(Boolean);
        for (const p of projections) {
            await frappe.call({
                method: "upande_agriculture.api.set_projection_source",
                args: { projection: p, source },
            });
        }
        await loadGrid();
    }

    // --- keyboard shortcuts --------------------------------------------------

    function bindShortcuts() {
        document.addEventListener("keydown", async (ev) => {
            if ((ev.metaKey || ev.ctrlKey) && ev.key === "s") {
                ev.preventDefault();
                await flushSave();
            }
        });
        // Flush on page hide too — operators close tabs without warning.
        window.addEventListener("beforeunload", () => {
            if (state.pendingEdits.size) flushSave();
        });
    }
};
