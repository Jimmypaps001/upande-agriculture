// Production Budget & Forecast
// ─────────────────────────────
// Budget is monthly on a Sep–Aug crop year (area × stems/m²/yr, market-weighted).
// Forecast is weekly per variety × grade, which is the shape of the sheets this
// page replaces. Past weeks are read-only; the current revision is the only
// editable layer.

frappe.pages['production_budget'].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: 'Production Budget', single_column: true });
	new BudgetForecast(wrapper);
};

const FONTS = 'https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700' +
	'&family=JetBrains+Mono:wght@400;500;600&display=swap';

class BudgetForecast {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.$page = $(wrapper).find('.page-content');
		this.grain = 'week';
		this.year = new Date().getFullYear();
		this.state = null;
		this.pop = null;
		this.load_fonts();
		this.render_shell();
		this.refresh();
	}

	load_fonts() {
		if (!document.getElementById('bf-fonts')) {
			$('<link id="bf-fonts" rel="stylesheet">').attr('href', FONTS).appendTo('head');
		}
	}

	// Desk chrome fights a full-bleed planning surface, so it is hidden while
	// this page is mounted and restored on the way out.
	render_shell() {
		$(this.wrapper).find('.page-head').hide();
		this.$root = $('<div class="bf"></div>').appendTo('body');
		this.$root.html(`
			<aside class="bf__rail">
				<div class="bf__top">
					<div class="bf__mark">U</div>
					<div class="bf__brand"><b>UPANDE</b><small>Agriculture</small></div>
					<button class="bf__collapse" data-act="collapse" title="Collapse">
						<svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg></button>
				</div>
				<div class="bf__scroll">
					<div class="bf__nav">
						<button class="bf__n" data-route="budget"><span class="bf__ni">B</span><span class="bf__nt">Budget</span></button>
						<button class="bf__n on"><span class="bf__ni">F</span><span class="bf__nt">Forecast</span></button>
						<button class="bf__n" data-route="plan"><span class="bf__ni">P</span><span class="bf__nt">Weekly Plan</span></button>
						<button class="bf__n" data-route="Crop Protocol"><span class="bf__ni">C</span><span class="bf__nt">Crop Protocols</span></button>
						<button class="bf__n" data-route="Greenhouse"><span class="bf__ni">G</span><span class="bf__nt">Greenhouses</span></button>
						<button class="bf__n" data-route="calibrate"><span class="bf__ni">K</span><span class="bf__nt">Calibration</span></button>
					</div>
					<div class="bf__eye">Crop year</div>
					<div class="bf__year" data-act="year"><b></b><small>Sep&ndash;Aug</small></div>
					<div class="bf__eye">Greenhouse › Variety</div>
					<div class="bf__tree"></div>
				</div>
				<div class="bf__foot">
					<div class="bf__sel"><small>Selected</small><b>—</b><span></span></div>
					<button class="bf__desk" data-act="desk">
						<svg viewBox="0 0 24 24"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
						<span class="bf__nt">Back to Desk</span></button>
					<div class="bf__who">
						<div class="bf__av"></div>
						<div class="bf__who-m"><b></b><small></small></div>
					</div>
				</div>
			</aside>
			<main class="bf__shell">
				<div class="bf__head">
					<div class="bf__hl">
						<div class="bf__eyebrow"></div>
						<h1>Budget &amp; Forecast</h1>
						<div class="bf__sub">Loading…</div>
					</div>
					<div class="bf__hr">
						<div class="bf__pg">
							<button data-grain="month">Monthly budget</button>
							<button data-grain="week" class="on">Weekly forecast</button>
						</div>
						<button class="bf__pill" data-act="window"></button>
						<button class="bf__btn" data-act="rebuild">Rebuild</button>
						<button class="bf__btn bf__btn--ink" data-act="revise">New revision</button>
					</div>
				</div>
				<div class="bf__kpis"></div>
				<div class="bf__card bf__climate"></div>
				<div class="bf__card bf__gridcard"></div>
			</main>`);

		const u = frappe.session.user_fullname || frappe.session.user;
		this.$root.find('.bf__av').text(u.split(/\s+/).map(s => s[0]).slice(0, 2).join('').toUpperCase());
		this.$root.find('.bf__who-m b').text(u);
		this.$root.find('.bf__who-m small').text(frappe.session.user);

		this.bind();
	}

	bind() {
		const self = this;
		this.$root.on('click', '[data-act="collapse"]', () => this.$root.toggleClass('is-collapsed'));
		this.$root.on('click', '[data-act="desk"]', () => { this.destroy(); frappe.set_route('/app'); });
		this.$root.on('click', '.bf__n[data-route]', function () {
			const r = $(this).data('route');
			if (r === 'Crop Protocol' || r === 'Greenhouse') { self.destroy(); frappe.set_route('List', r); }
		});
		this.$root.on('click', '[data-grain]', function () {
			self.grain = $(this).data('grain');
			self.$root.find('[data-grain]').removeClass('on');
			$(this).addClass('on');
			self.draw();
		});
		this.$root.on('click', '[data-act="rebuild"]', () => this.rebuild());
		this.$root.on('click', '.bf__tw', function () { $(this).find('.bf__cb').toggleClass('on'); self.recount(); });
		this.$root.on('click', 'td.edit', function (e) { e.stopPropagation(); self.open_cell($(this)); });
		this.$root.on('click', e => { if (!$(e.target).closest('.bf__pop').length) this.close_pop(); });
		$(document).on('keydown.bf', e => { if (e.key === 'Escape') this.close_pop(); });

		// Frappe keeps the page in the DOM when routing away, so tear down on hide.
		$(this.wrapper).on('hide', () => this.destroy());
	}

	destroy() {
		if (this.$root) { this.$root.remove(); this.$root = null; }
		$(document).off('keydown.bf');
		$(this.wrapper).find('.page-head').show();
	}

	refresh() {
		frappe.call({
			method: 'upande_agriculture.budget.grid_payload',
			args: { year: this.year },
			callback: r => { this.state = r.message; this.draw(); },
		});
	}

	rebuild() {
		frappe.confirm(
			__('Rebuild every budget for {0} from the crop cycles?<br><br>Locked and hand-edited weeks are kept.', [this.year]),
			() => frappe.call({
				method: 'upande_agriculture.budget.generate_all_budgets',
				args: { year: this.year },
				freeze: true,
				freeze_message: __('Rebuilding budgets…'),
				callback: r => {
					frappe.show_alert({ message: __('{0} budgets rebuilt', [r.message.generated]), indicator: 'green' });
					this.refresh();
				},
			}));
	}

	// ── drawing ────────────────────────────────────────────────
	draw() {
		const s = this.state;
		if (!s) return;
		this.$root.find('.bf__eyebrow').text(`Crop year ${s.crop_year} · Sep – Aug`);
		this.$root.find('.bf__year b').text(s.crop_year);
		this.$root.find('.bf__sub').text(
			`${s.blocks.length} blocks across ${s.house_count} greenhouses · ` +
			`${fmt_m(s.budget_total)} stems budgeted · ${s.weeks.length ? `weeks W${s.weeks[0]}–W${s.weeks.at(-1)}` : 'no weeks'}`);
		this.$root.find('[data-act="window"]').text(
			s.weeks.length ? `W${s.weeks[0]} \u2013 W${s.weeks.at(-1)}` : 'No window');
		this.draw_tree();
		this.draw_kpis();
		this.draw_climate();
		this.draw_grid();
		this.recount();
	}

	draw_tree() {
		const byHouse = {};
		for (const b of this.state.blocks) (byHouse[b.greenhouse] ||= []).push(b);
		this.$root.find('.bf__tree').html(Object.entries(byHouse).map(([gh, list]) => `
			<div class="bf__tw bf__tw--gh"><i class="bf__cb on"></i>
				<span class="bf__tn">${frappe.utils.escape_html(short_house(gh))}</span>
				<span class="bf__tm">${list[0].area ? Math.round(list[0].area).toLocaleString() + ' m²' : ''}</span></div>
			${list.map(b => `<div class="bf__tw bf__tw--v" data-block="${b.key}">
				<i class="bf__cb on"></i>
				<span class="bf__tn">${frappe.utils.escape_html(b.variety)}</span>
				<span class="bf__tm">${fmt_m(b.budget_total)}</span></div>`).join('')}
		`).join('') || '<div class="bf__eye">No crop cycles yet</div>');
	}

	recount() {
		const on = this.$root.find('.bf__tw--v .bf__cb.on').closest('.bf__tw');
		let stems = 0, area = 0;
		on.each((_, el) => {
			const b = this.state.blocks.find(x => x.key === $(el).data('block'));
			if (b) { stems += b.budget_total || 0; area += b.area || 0; }
		});
		this.$root.find('.bf__sel b').text(fmt_m(stems) + ' stems');
		this.$root.find('.bf__sel span').text(`${on.length} blocks · ${Math.round(area).toLocaleString()} m²`);
	}

	draw_kpis() {
		const s = this.state;
		const K = [
			['Budget · crop year', fmt_m(s.budget_total), 'stems', `Sep ${s.crop_year.slice(0, 4)} – Aug ${s.crop_year.slice(-2)}`, 'var(--s-budget)'],
			['Forecast · window', fmt_m(s.forecast_total), 'stems', s.revision_note || 'no revision yet', 'var(--s-forecast)'],
			['Actual vs budget', signed(s.actual_vs_budget), '%', `${s.actual_weeks} weeks to date`, s.actual_vs_budget >= 0 ? 'var(--pos)' : 'var(--neg)'],
			['Model accuracy', signed(s.model_error), '%', s.model_note || 'not yet calibrated', 'var(--signal)'],
		];
		this.$root.find('.bf__kpis').html(K.map(([l, v, u, m, c]) => `
			<div class="bf__kpi"><div class="bf__kl">${l}</div>
				<div class="bf__kv">${v}<i>${u}</i></div>
				<div class="bf__kt"><i style="background:${c}"></i>${frappe.utils.escape_html(m)}</div></div>`).join(''));
	}

	draw_climate() {
		const s = this.state;
		if (!s.climate || !Object.keys(s.climate).length) {
			this.$root.find('.bf__climate').hide();
			return;
		}
		this.$root.find('.bf__climate').show().html(`
			<div class="bf__ch">
				<div><h3>Climate — ${frappe.utils.escape_html(s.site || 'Farm')}</h3>
					<p>${frappe.utils.escape_html(s.climate_note || '')}</p></div>
				<div class="bf__sp"></div>
				${s.climate_alert ? `<div class="bf__note"><i style="background:var(--warn)"></i>${frappe.utils.escape_html(s.climate_alert)}</div>` : ''}
			</div>
			<div class="bf__scroller" data-sync="clim"><div class="bf__clim">
				<div class="bf__cpad"></div>
				${s.weeks.map(w => {
					const c = s.climate[w];
					if (!c) return `<div class="bf__cw"><b>W${w}</b></div>`;
					const kind = w === s.now_week ? 'now' : w < s.now_week ? 'past' : '';
					const pct = Math.max(10, Math.min(100, Math.round((c.light - 11) / 9 * 100)));
					const col = c.light >= 17 ? 'var(--pos)' : c.light >= 14 ? 'var(--warn)' : 'var(--neg)';
					const ic = c.icon === 'Bright' ? 'var(--warn)' : c.icon === 'Wet' ? 'var(--s-forecast)' : 'var(--ink-mute)';
					return `<div class="bf__cw ${kind}"><b>W${w}</b>
						<div class="ico" style="color:${ic}">${c.icon}</div>
						<div class="tmp">${c.temp}°<s>${c.light}</s></div>
						<div class="bar"><i style="width:${pct}%;background:${col}"></i></div></div>`;
				}).join('')}
			</div></div>`);
	}

	draw_grid() {
		const s = this.state;
		if (!s.blocks.length) {
			this.$root.find('.bf__gridcard').html(
				`<div class="bf__empty"><b>Nothing to show yet</b>
				 Record a Crop Cycle with a Crop Protocol, then press Rebuild.</div>`);
			return;
		}
		const cols = this.grain === 'week' ? s.weeks : s.months;
		const lbl = this.grain === 'week' ? w => 'W' + w : m => m;
		const now = this.grain === 'week' ? s.now_week : s.now_month;
		const isPast = c => this.grain === 'week' ? c < now : s.months.indexOf(c) < s.months.indexOf(now);

		const head = `<thead><tr><th class="lbl">VARIETY</th><th class="grd">LEN</th>
			${cols.map(c => `<th class="c${c === now ? ' now' : ''}">${lbl(c)}</th>`).join('')}</tr></thead>`;

		const body = s.blocks.map(b => {
			const series = this.grain === 'week' ? b.weekly : b.monthly;
			const cls = (c, k) => {
				const out = [k];
				if (c === now) out.push('now');
				else if (isPast(c)) { out.push('past'); if (k !== 'act') out.push('locked'); }
				return out.filter(Boolean).join(' ');
			};
			const grp = `<tr class="grp"><td class="lbl"><b>${frappe.utils.escape_html(short_house(b.greenhouse))} › ${frappe.utils.escape_html(b.variety)}</b></td>
				<td class="grd"></td>
				<td class="c" colspan="${cols.length}" style="text-align:left;padding-left:2px">
					<em>${b.area ? Math.round(b.area).toLocaleString() + ' m²' : ''} ${b.rate ? '· ' + b.rate + ' stems/m²/yr' : ''}</em></td></tr>`;

			const grades = (series.grades || []).map(g => `<tr class="g">
				<td class="lbl"></td><td class="grd">${g.grade}</td>
				${cols.map((c, i) => {
					const v = g.revised && g.revised[i] != null ? g.revised[i] : g.values[i];
					const rev = g.revised && g.revised[i] != null;
					const k = v == null ? 'nil' : rev ? 'rev' : isPast(c) ? 'bud' : 'edit';
					const attrs = (!isPast(c) && v != null)
						? ` data-block="${b.key}" data-grade="${frappe.utils.escape_html(g.grade)}" data-col="${c}" data-val="${v}"` : '';
					return `<td class="c ${cls(c, k)}"${attrs}>${fmt(v)}</td>`;
				}).join('')}</tr>`).join('');

			const totals = cols.map((c, i) =>
				(series.grades || []).reduce((t, g) => {
					const v = g.revised && g.revised[i] != null ? g.revised[i] : g.values[i];
					return t + (v || 0);
				}, 0) || null);

			const sums = `
				<tr class="sum"><td class="lbl"></td><td class="grd">${this.grain === 'week' ? 'Weekly' : 'Monthly'}</td>
					${totals.map((v, i) => `<td class="c ${cls(cols[i], '')}">${fmt(v)}</td>`).join('')}</tr>
				<tr class="sum daily"><td class="lbl"></td><td class="grd">Daily</td>
					${totals.map((v, i) => `<td class="c ${cls(cols[i], '')}">${fmt(v ? Math.round(v / (this.grain === 'week' ? 7 : 30)) : null)}</td>`).join('')}</tr>`;

			const refs = `
				<tr class="ref"><td class="lbl" style="color:var(--s-budget)">Budget<em>${this.grain === 'week' ? 'monthly ÷ weeks' : 'area × rate'}</em></td>
					<td class="grd"></td>
					${(series.budget || []).map((v, i) => `<td class="c bud ${cls(cols[i], '')}">${fmt(v)}</td>`).join('')}</tr>
				<tr class="ref"><td class="lbl" style="color:var(--s-actual)">Actual<em>harvested</em></td>
					<td class="grd"></td>
					${(series.actual || []).map((v, i) => `<td class="c ${v == null ? 'nil' : 'act'} ${cls(cols[i], 'act')}">${fmt(v)}</td>`).join('')}</tr>
				<tr class="ref"><td class="lbl" style="color:var(--ink-faint)">Variance<em>actual vs forecast</em></td>
					<td class="grd"></td>
					${(series.actual || []).map((v, i) => {
						const f = totals[i];
						if (v == null || !f) return `<td class="c nil ${cls(cols[i], '')}">—</td>`;
						const d = Math.round((v - f) / f * 100);
						return `<td class="c ${d >= 0 ? 'pos' : 'neg'} ${cls(cols[i], '')}">${d >= 0 ? '+' : ''}${d}%</td>`;
					}).join('')}</tr>`;
			return grp + grades + sums + refs;
		}).join('');

		this.$root.find('.bf__gridcard').html(`
			<div class="bf__ch">
				<div><h3>${this.grain === 'week' ? 'Weekly forecast by grade' : 'Monthly budget by grade'}</h3>
					<p>Click a cell to type, or use the form for a weather-adjusted suggestion. Past periods are locked.</p></div>
				<div class="bf__sp"></div>
				<div class="bf__leg">
					<span><i style="background:var(--s-budget)"></i>Budget</span>
					<span><i style="background:var(--s-forecast)"></i>Forecast</span>
					<span><i style="background:var(--s-revised)"></i>Revised</span>
					<span><i style="background:var(--s-actual)"></i>Actual</span></div>
			</div>
			<div class="bf__scroller" data-sync="grid"><table class="bf__grid">${head}<tbody>${body}</tbody></table></div>`);
		this.sync_scroll();
	}

	// The climate strip only means anything if its columns stay over the grid's.
	sync_scroll() {
		const g = this.$root.find('[data-sync="grid"]')[0];
		const c = this.$root.find('[data-sync="clim"]')[0];
		if (!g || !c) return;
		let lock = false;
		const tie = (a, b) => a.addEventListener('scroll', () => {
			if (lock) return; lock = true; b.scrollLeft = a.scrollLeft; lock = false;
		});
		tie(g, c); tie(c, g);
	}

	// ── cell form ──────────────────────────────────────────────
	close_pop() { if (this.pop) { this.pop.remove(); this.pop = null; } }

	open_cell($td) {
		this.close_pop();
		const s = this.state;
		const col = +$td.data('col'), grade = $td.data('grade'), cur = +$td.data('val');
		const block = s.blocks.find(b => b.key === $td.data('block'));
		const clim = s.climate && s.climate[col];
		const norm = s.light_norm || 16.5;
		// Light sum against the seasonal norm shifts the suggestion — brighter
		// weeks bring the flush forward and heavier, duller weeks push it back.
		const factor = clim ? 1 + (clim.light - norm) / norm * 0.45 : 1;
		const sugg = Math.max(0, Math.round(cur * factor / 10) * 10);
		const delta = cur ? Math.round((sugg - cur) / cur * 100) : 0;

		this.pop = $(`
			<div class="bf__pop">
				<h4>${this.grain === 'week' ? 'W' + col : col} · ${frappe.utils.escape_html(grade)}
					<small>${frappe.utils.escape_html(short_house(block.greenhouse))} › ${frappe.utils.escape_html(block.variety)}</small></h4>
				<div class="psub">Revised forecast · revision ${s.revision || 1}</div>
				<div class="bf__sugg">
					<div class="r"><b>${fmt(sugg)}</b>
						<span class="tag">${delta >= 0 ? '+' : ''}${delta}% vs forecast</span></div>
					<div class="why">${clim
						? `${clim.icon} <u>${clim.temp}°C</u>, light sum <u>${clim.light} MJ</u> — ${clim.light >= norm ? 'above' : 'below'} the ${norm} norm, so the flush should arrive ${clim.light >= norm ? 'earlier and heavier' : 'later and lighter'}.`
						: 'No climate data for this period — suggestion equals the current forecast.'}</div>
				</div>
				<div class="bf__pin"><input type="text" value="${fmt(sugg)}"><span class="u">stems</span></div>
				<div class="bf__pr">
					<button data-set="${cur}">Forecast ${fmt(cur)}</button>
					<button class="on" data-set="${sugg}">Suggested</button>
					<button data-set="${Math.round(cur * 0.9)}">−10%</button>
					<button data-set="${Math.round(cur * 1.1)}">+10%</button>
				</div>
				<div class="bf__pf">
					<button class="cancel">Cancel</button>
					<button class="save">Save to revision ${s.revision || 1}</button>
				</div>
				<div class="bf__prev"><span>${frappe.utils.escape_html(s.revision_note || 'No edits yet')}</span>
					<a data-act="history">History ›</a></div>
			</div>`).appendTo('body');

		const $in = this.pop.find('input');
		this.pop.on('click', '[data-set]', e => $in.val(fmt(+$(e.currentTarget).data('set'))));
		this.pop.on('click', '.cancel', () => this.close_pop());
		this.pop.on('click', '.save', () => this.save_cell($td, unfmt($in.val())));

		const r = $td[0].getBoundingClientRect();
		const h = this.pop.outerHeight();
		const left = Math.max(12, Math.min(r.left - 100, window.innerWidth - 308));
		const below = r.bottom + 8 + h < window.innerHeight;
		this.pop.css({ left: left + 'px', top: (below ? r.bottom + 8 : r.top - h - 8) + window.scrollY + 'px' });
		$in.focus().select();
	}

	save_cell($td, value) {
		if (!(value >= 0)) { frappe.show_alert({ message: __('Enter a number'), indicator: 'red' }); return; }
		frappe.call({
			method: 'upande_agriculture.budget.set_forecast_cell',
			args: {
				block: $td.data('block'), grade: $td.data('grade'),
				week: +$td.data('col'), value: value, year: this.year,
			},
			callback: () => {
				this.close_pop();
				frappe.show_alert({ message: __('Saved'), indicator: 'green' });
				this.refresh();
			},
		});
	}
}

// ── helpers ────────────────────────────────────────────────────
const fmt = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString('en-US');
const unfmt = s => parseInt(String(s).replace(/[^\d-]/g, ''), 10);
const fmt_m = n => !n ? '0' : n >= 1e6 ? (n / 1e6).toFixed(2) + ' M' : Math.round(n).toLocaleString();
const signed = n => n == null ? '—' : (n >= 0 ? '+' : '') + Number(n).toFixed(1);
// "Main GH 05 - MFK" reads as "GH 05" once you are already inside one farm.
const short_house = h => String(h || '').replace(/^Main\s+/i, '').replace(/\s*-\s*[A-Z]{2,4}$/, '');
