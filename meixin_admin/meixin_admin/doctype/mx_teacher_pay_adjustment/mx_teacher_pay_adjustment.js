frappe.ui.form.on("MX Teacher Pay Adjustment", {
	refresh(frm) {
		frm.disable_save();
		frm.set_intro(frm.doc.docstatus === 1 ?
			(frm.doc.decision === "驳回" ? "财务已驳回，更正申请及原因保留。需要更正时重新提交申请。" : "财务调整决定已冻结。填错须申请更正本调整，原结算与付款记录保留。") :
			(frm.doc.event_type === "更正" ? "金额更正尚待财务批准。请核对关联原明细或原调整、申请原因及依据；批准只追加差额，不释放课次占用。" :
				"来源变更尚待财务处理。固定费用部分课程取消时，须依据覆盖约定决定金额；不得机械扣除整笔固定费。"), "blue");
		if (frm.doc.sheet) frm.add_custom_button("查看原结算单", () =>
			frappe.set_route("Form", "MX Teacher Pay Sheet", frm.doc.sheet));
		if (frm.doc.hour_entry) frm.add_custom_button("查看来源摘要", async () =>
			mx_payroll.show_summary(await mx_payroll.call("get_source_summary", { hour_entry: frm.doc.hour_entry })));
		if (!frm.is_new() && frm.doc.docstatus === 0 && mx_payroll.finance()) {
			frm.add_custom_button("财务批准调整", () => mx_payroll.adjustment_dialog(frm));
			if (frm.doc.event_type === "更正") frm.add_custom_button("财务驳回更正", () => mx_payroll.reject_correction_dialog(frm));
		}
		if (!frm.is_new() && frm.doc.docstatus === 1 && frm.doc.decision !== "驳回" && (mx_payroll.admin() || mx_payroll.finance())) {
			frm.add_custom_button("申请更正本调整", () => mx_payroll.correction_dialog(frm, frm.doc.name));
		}
	},
});
