frappe.query_reports["Bed Sample Forecast"] = {
	filters: [
		{ fieldname: "weeks_ahead", label: __("Weeks Ahead"), fieldtype: "Int", default: 8 },
		{ fieldname: "group_by", label: __("Show"), fieldtype: "Select", options: "Variety\nGreenhouse", default: "Variety" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item", get_query: () => ({ filters: { has_variants: 1 } }) },
		{ fieldname: "greenhouse", label: __("Greenhouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "as_of", label: __("As Of"), fieldtype: "Date", default: frappe.datetime.get_today(),
		  description: __("Pick a past date to see what the forecast said then.") },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "last_sampled" && data && data.stale) {
			value = `<span style="color: var(--red-600)" title="${__("Over 2 weeks old: sample again")}">${value}</span>`;
		}
		return value;
	},
};
