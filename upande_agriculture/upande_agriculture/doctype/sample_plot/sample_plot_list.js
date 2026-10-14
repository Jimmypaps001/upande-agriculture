frappe.listview_settings["Sample Plot"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Print plot tags"), () => {
			frappe.prompt(
				[
					{ fieldname: "greenhouse", label: __("Greenhouse"), fieldtype: "Link", options: "Warehouse", reqd: 1,
						get_query: () => ({ filters: { warehouse_type: "Greenhouse" } }) },
					{ fieldname: "section", label: __("Section (blank = all)"), fieldtype: "Data" },
				],
				(v) => {
					const q = new URLSearchParams({ greenhouse: v.greenhouse });
					if (v.section) q.set("section", v.section);
					window.open(`/api/method/upande_agriculture.forecast.api.plot_tags_pdf?${q}`);
				},
				__("Print plot tags"),
				__("Open PDF")
			);
		});
	},
};
