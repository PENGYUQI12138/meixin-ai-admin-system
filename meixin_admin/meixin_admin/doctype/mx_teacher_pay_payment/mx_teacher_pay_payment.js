frappe.ui.form.on("MX Teacher Pay Payment", {
	refresh(frm) {
		frm.disable_save();
		frm.set_intro("本记录不可修改。错误付款登记通过受控冲正保留原记录；冲正不表示银行撤回或已退款。", "blue");
		if (frm.doc.sheet) frm.add_custom_button("查看结算与冲正操作", () =>
			frappe.set_route("Form", "MX Teacher Pay Sheet", frm.doc.sheet));
	},
});
