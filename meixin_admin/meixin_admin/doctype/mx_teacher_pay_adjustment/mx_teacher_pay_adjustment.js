frappe.ui.form.on("MX Teacher Pay Adjustment", {
	refresh(frm) {
		frm.disable_save();
		frm.set_intro(frm.doc.docstatus === 1 ?
			"财务调整决定已冻结，原结算与付款记录保留。" :
			"来源变更尚待财务处理。固定费用部分课程取消时，须依据覆盖约定决定金额；不得机械扣除整笔固定费。", "blue");
		if (frm.doc.sheet) frm.add_custom_button("查看原结算单", () =>
			frappe.set_route("Form", "MX Teacher Pay Sheet", frm.doc.sheet));
		if (frm.doc.hour_entry) frm.add_custom_button("查看来源摘要", async () =>
			mx_payroll.show_summary(await mx_payroll.call("get_source_summary", { hour_entry: frm.doc.hour_entry })));
		if (!frm.is_new() && frm.doc.docstatus === 0 && mx_payroll.finance()) {
			frm.add_custom_button("财务批准调整", () => mx_payroll.adjustment_dialog(frm));
		}
	},
});
