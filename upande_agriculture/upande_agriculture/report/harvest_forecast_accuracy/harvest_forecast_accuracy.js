frappe.query_reports["Harvest Forecast Accuracy"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -28) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item" },
	],
};
