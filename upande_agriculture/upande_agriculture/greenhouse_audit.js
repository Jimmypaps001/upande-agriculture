// Shared by Greenhouse 5S Audit and Greenhouse DMS Audit (included from each
// doctype's form script): one checklist table per section, loaded from the
// template and scored per section.

function setup_greenhouse_audit(doctype, audit_type) {
	const api = "upande_agriculture.greenhouse_audit";

	frappe.ui.form.on(doctype, {
		setup(frm) {
			frm.set_query("greenhouse", () => ({
				filters: {
					warehouse_type: "Greenhouse",
					is_group: 0,
					disabled: 0,
					...(frm.doc.farm ? { custom_farm: frm.doc.farm } : {}),
				},
			}));
		},

		refresh(frm) {
			if (!frm.is_new() || frm.doc.audit_template) return;
			frappe.call(`${api}.get_default_template`, { audit_type }).then((r) => {
				if (r.message) frm.set_value("audit_template", r.message);
			});
		},

		audit_template(frm) {
			if (!frm.doc.audit_template) return;
			frappe
				.call(`${api}.get_checklist`, { audit_template: frm.doc.audit_template })
				.then((r) => {
					Object.entries(r.message).forEach(([table, rows]) => {
						frm.clear_table(table);
						rows.forEach((row) => frm.add_child(table, row));
						frm.refresh_field(table);
					});
					update_scores(frm);
				});
		},
	});

	frappe.ui.form.on("Greenhouse Audit Item", {
		score: update_scores,
	});
}

function update_scores(frm) {
	const tables = frm.meta.fields.filter(
		(df) => df.fieldtype === "Table" && df.options === "Greenhouse Audit Item"
	);
	frm.clear_table("section_scores");
	let total = 0,
		max = 0;
	tables.forEach((df) => {
		const rows = frm.doc[df.fieldname] || [];
		if (!rows.length) return;
		const score = rows.reduce((a, r) => a + cint(r.score), 0);
		const max_score = rows.reduce((a, r) => a + cint(r.max_score), 0);
		frm.add_child("section_scores", {
			section: df.label,
			score,
			max_score,
			percentage: max_score ? flt((score * 100) / max_score, 2) : 0,
		});
		total += score;
		max += max_score;
	});
	frm.refresh_field("section_scores");
	frm.set_value({
		total_score: total,
		max_score: max,
		percentage: max ? flt((total * 100) / max, 2) : 0,
	});
}
