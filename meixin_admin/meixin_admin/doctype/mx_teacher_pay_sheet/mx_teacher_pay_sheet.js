frappe.ui.form.on("MX Teacher Pay Sheet", {
	async refresh(frm) {
		frm.disable_save();
		if (frm.is_new()) {
			frm.set_intro("请使用工资结算列表或报表中的“新建工资单”，由受控操作创建。", "blue");
			if (mx_payroll.admin()) frm.add_custom_button("新建工资单", () => mx_payroll.open_editor());
			return;
		}
		frm.set_intro(frm.doc.status === "已批准" ?
			"原批准账已冻结。来源撤销、后补等变更通过调整事项处理；付款仅登记实际线下全额付款。" :
			"行政核对人数与来源后，交财务重新检查并批准。正常课程一节 90 分钟，不按分钟折算。", "blue");
		if (mx_payroll.admin() && frm.doc.status === "待核对") {
			frm.add_custom_button("编辑明细", () => mx_payroll.open_editor(frm.doc));
			frm.add_custom_button("行政核对", () => mx_payroll.sheet_action(frm, "check_sheet",
				"确认已核对当日班级人数含请假、缺勤，原排课教师归属无争议，并核对固定费用覆盖约定？"));
		}
		if (mx_payroll.admin() && frm.doc.status === "已核对") {
			frm.add_custom_button("撤回重新核对", () => mx_payroll.sheet_action(frm, "withdraw_sheet", "撤回核对并释放尚未批准的来源占用？"));
		}
		if (mx_payroll.finance() && frm.doc.status === "已核对") {
			frm.add_custom_button("财务批准", () => mx_payroll.sheet_action(frm, "approve_sheet",
				"确认批准本单金额？服务器会重新检查来源、规则、人数依据和覆盖关系；批准后原账冻结。"));
		}
		frm.add_custom_button("查看来源摘要", () => {
			const names = [...new Set([...(frm.doc.lines || []), ...(frm.doc.coverage || [])]
				.map((row) => row.hour_entry).filter(Boolean))];
			if (!names.length) return frappe.msgprint("本单无授课来源，固定工作事项详见工资明细。");
			frappe.prompt([{ fieldname: "hour_entry", fieldtype: "Select", label: "授课确认", options: names.join("\n"), reqd: 1 }],
				async (values) => mx_payroll.show_summary(await mx_payroll.call("get_source_summary", values)),
				"选择履职所需来源摘要", "查看");
		});
		const balance = await mx_payroll.show_balance(frm);
		if (mx_payroll.finance() && balance.status === "待付款") {
			frm.add_custom_button("登记实际付款", () => mx_payroll.payment_dialog(frm));
		}
	},
});
