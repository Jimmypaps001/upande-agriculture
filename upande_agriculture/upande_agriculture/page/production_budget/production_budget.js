// Production Budget — desk host for the full Budget & Forecast planner.
// ──────────────────────────────────────────────────────────────────────
// The planner itself lives at /budget-forecast (www): a self-contained
// single-page app with its own sidebar (Dashboard, Budget, Forecast,
// Lifetime, Weekly Plan, Farm Map, Calibration), fixed-position layout,
// Leaflet maps and design system. Reimplementing 4k lines inside the desk
// DOM would fork it; hosting it keeps one codebase and every view working,
// while planners stay inside the desk. Same session, same permissions —
// the frame is served by this very site.

frappe.pages['production_budget'].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({ parent: wrapper, title: 'Production Budget', single_column: true });

	const $content = $(wrapper).find('.page-content');
	// The planner brings its own header and sidebar — give it the whole
	// viewport below the desk navbar instead of desk paddings and titles.
	$(wrapper).find('.page-head').hide();
	$content.css({ padding: 0, margin: 0 });

	const frame = document.createElement('iframe');
	frame.src = '/budget-forecast';
	frame.title = 'Budget & Forecast';
	frame.style.cssText = [
		'width:100%',
		'border:0',
		'display:block',
		// Desk navbar is the only chrome left above the frame.
		'height:calc(100vh - var(--navbar-height, 60px))',
		'background:#f5f4f0',
	].join(';');
	$content.append(frame);
};
