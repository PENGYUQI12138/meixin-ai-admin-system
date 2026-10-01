async function load_execution(frm) {
	if (!frm.doc.session || !frm.is_new() || (frm.doc.attendance || []).length) return;
	const result = await frappe.call("meixin_admin.execution.make_execution", { session: frm.doc.session });
	const execution = result.message;
	if (!execution) return;
	if (!execution.__islocal) {
		frappe.set_route("Form", "MX Session Execution", execution.name);
		return;
	}
	frm.clear_table("attendance");
	for (const row of execution.attendance || []) {
		frm.add_child("attendance", { student: row.student });
	}
	frm.refresh_field("attendance");
}

frappe.ui.form.on("MX Session Execution", {
	setup(frm) {
		frm.set_query("session", () => ({ filters: { docstatus: 1 } }));
	},

	session(frm) {
		return load_execution(frm);
	},

	async refresh(frm) {
		const messages = [
			"填写全部学生考勤后提交。提交时服务器会冻结规则并生成课消决策流水。",
			"执行结果已完成并冻结；纠错须由美心管理员取消后修订。",
			"本执行结果已撤销；历史考勤和课消流水仍保留。",
		];
		frm.set_intro(messages[frm.doc.docstatus], frm.doc.docstatus === 1 ? "green" : "blue");
		if (!frm.is_new() && frm.doc.session) {
			frm.add_custom_button("查看教师课时", () => {
				frappe.set_route("List", "MX Teacher Hour Entry", { execution: frm.doc.name });
			});
			const existing = frm.doc.docstatus === 1 && await frappe.db.get_value(
				"MX Teacher Hour Entry", { execution: frm.doc.name, operation_type: "确认" }, "name"
			);
			if (frm.doc.docstatus === 1 && !existing?.message?.name) frm.add_custom_button("确认实际授课", () => {
				frappe.prompt([
					{ fieldname: "taught", fieldtype: "Check", label: "实际授课", default: 1 },
					{ fieldname: "actual_start", fieldtype: "Datetime", label: "实际开始" },
					{ fieldname: "actual_end", fieldtype: "Datetime", label: "实际结束" },
					{ fieldname: "exception_reason", fieldtype: "Small Text", label: "异常原因（异常时管理员必填）" },
				], async (values) => {
					const result = await frappe.call("meixin_admin.teacher_hours.confirm_teaching", {
						execution: frm.doc.name, ...values,
					});
					if (result.message) frappe.set_route("Form", "MX Teacher Hour Entry", result.message);
				}, "确认教师实际授课", "确认");
			});
			frm.add_custom_button("查看原排课", () => {
				frappe.set_route("Form", "MX Session", frm.doc.session);
			});
			frm.add_custom_button("查看课消流水", () => {
				frappe.set_route("List", "MX Lesson Consumption Entry", {
					execution: frm.doc.name,
				});
			});
		}
	},
});
