frappe.query_reports["Weekly Harvest Forecast"] = {
	filters: [
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item", get_query: () => ({ filters: { has_variants: 1 } }) },
		{ fieldname: "weeks", label: __("Weeks"), fieldtype: "Int", default: 3 },
		{ fieldname: "group_by", label: __("Show"), fieldtype: "Select", options: "Variety\nGreenhouse", default: "Variety" },
	],
};
