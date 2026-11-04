// Copyright (c) 2026, Upande and contributors
// For license information, please see license.txt

frappe.ui.form.on("Production Plan Form", {
    setup(frm) {
        frm.set_query("farm", () => frm.doc.company ? { filters: { company: frm.doc.company } } : {});
        frm.set_query("greenhouse", () => frm.doc.farm ? { filters: { custom_farm: frm.doc.farm } } : {});
    },

    refresh(frm) {
        // Not mandatory in the schema: the planning board and mobile app
        // create plans without them.
        frm.toggle_reqd(["company", "farm", "supervisor"], 1);
    },

    async farm(frm) {
        if (frm.doc.farm && !frm.doc.company) {
            const { message } = await frappe.db.get_value("Farm", frm.doc.farm, "company");
            if (message?.company) frm.set_value("company", message.company);
        }
        if (!frm.doc.greenhouse) return;
        // Picking a greenhouse first fills Farm from it; only clear a
        // greenhouse that belongs to some other farm.
        const { message } = await frappe.db.get_value("Warehouse", frm.doc.greenhouse, "custom_farm");
        if (message?.custom_farm !== frm.doc.farm) frm.set_value("greenhouse", "");
    },
});

frappe.ui.form.on("Production Plan Task", {
    target: set_achievement,
    actual: set_achievement,
});

function set_achievement(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const target = cint(row.target);
    frappe.model.set_value(cdt, cdn, "achievement", target ? flt((cint(row.actual) * 100) / target, 2) : 0);
}
