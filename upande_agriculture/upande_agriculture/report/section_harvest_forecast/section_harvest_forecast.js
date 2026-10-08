frappe.query_reports["Section Harvest Forecast"] = {
	filters: [
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{ fieldname: "greenhouse", label: __("Greenhouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item" },
		{ fieldname: "days", label: __("Days"), fieldtype: "Int", default: 14 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "basis" && data && data.warn) {
			value = `<span style="color: var(--orange-600)">${value}</span>`;
		}
		return value;
	},
};
