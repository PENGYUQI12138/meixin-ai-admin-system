frappe.ui.form.on("MX Teacher Pay Payment", {
	refresh(frm) {
		frm.disable_save();
		frm.set_intro(frm.doc.is_difference ?
			"这是已发生付款的差异登记，金额按实际付款凭据保留；核算差额待财务处理，不自动扣款或退款。记录不可改，错误登记须受控冲正。" :
			"本记录不可修改。实际付款金额与登记时核算版本均保留；错误登记通过受控冲正，冲正不表示银行撤回或已退款。", "blue");
		if (frm.doc.sheet) frm.add_custom_button("查看结算与冲正操作", () =>
			frappe.set_route("Form", "MX Teacher Pay Sheet", frm.doc.sheet));
	},
});
