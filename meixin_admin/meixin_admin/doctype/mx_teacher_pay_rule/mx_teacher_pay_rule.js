frappe.ui.form.on("MX Teacher Pay Rule", {
	onload(frm) {
		if (!frm.is_new()) return;
		if (!frm.doc.effective_from) frm.set_value("effective_from", frappe.datetime.get_today());
		if (!(frm.doc.rates || []).length) {
			frm.add_child("rates", { min_students: 1, max_students: 4, amount: 100 });
			frm.add_child("rates", { min_students: 5, max_students: 0, amount: 120 });
			frm.refresh_field("rates");
		}
	},
	refresh(frm) {
		frm.set_intro(frm.doc.docstatus === 1 ?
			"此生效规则已冻结。调价请新增规则版本，旧批准账单保留原快照。" :
			"按授课日期选择已提交的生效规则。人数档位须连续且无重叠；金额单位人民币，每节固定 90 分钟。", "blue");
	},
});
