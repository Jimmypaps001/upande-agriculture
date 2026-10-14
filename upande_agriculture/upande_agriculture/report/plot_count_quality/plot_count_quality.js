frappe.query_reports["Plot Count Quality"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -13) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "greenhouse", label: __("Greenhouse"), fieldtype: "Link", options: "Warehouse" },
	],
};
