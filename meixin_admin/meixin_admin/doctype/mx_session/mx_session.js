async function set_default_end(frm) {
	if (!frm.doc.course || !frm.doc.start_at || frm._mx_end_manually_set) return;
	const result = await frappe.db.get_value("MX Course", frm.doc.course, "default_duration_minutes");
	const duration = Number(result.message?.default_duration_minutes || 0);
	if (duration <= 0 || frm._mx_end_manually_set) return;
	const start_at = frappe.datetime.str_to_obj(frm.doc.start_at);
	const end_at = moment(start_at).add(duration, "minutes").format(frappe.defaultDatetimeFormat);
	frm._mx_setting_default_end = true;
	try {
		await frm.set_value("end_at", end_at);
		frm._mx_auto_end_at = frm.doc.end_at;
	} finally {
		frm._mx_setting_default_end = false;
	}
}

frappe.ui.form.on("MX Session", {
	onload(frm) {
		frm._mx_auto_end_at = null;
		frm._mx_end_manually_set = Boolean(frm.doc.end_at);
	},

	setup(frm) {
		for (const fieldname of ["course", "teacher", "room"]) {
			frm.set_query(fieldname, () => ({ filters: { enabled: 1 } }));
		}
		frm.set_query("student", "students", () => ({ filters: { enabled: 1 } }));
	},

	course(frm) {
		return set_default_end(frm);
	},

	start_at(frm) {
		return set_default_end(frm);
	},

	end_at(frm) {
		if (frm._mx_setting_default_end) return;
		frm._mx_end_manually_set = Boolean(frm.doc.end_at && frm.doc.end_at !== frm._mx_auto_end_at);
	},

	async refresh(frm) {
		const messages = [
			"草稿不占用时段。保存后点击“提交”才确认排课；提交时服务器会重新检查冲突。",
			"本节课已确认并占用时段。需要调整时，请由美心管理员取消后修订，再重新提交。",
			"本节课已取消，时段已释放，记录保留。可点击“修订”生成新的排课草稿。",
		];
		frm.set_intro(messages[frm.doc.docstatus], frm.doc.docstatus === 1 ? "green" : "blue");
		frm.add_custom_button("周课表", () => frappe.set_route("List", "MX Session", "Calendar", "default"));
		if (!frm.is_new() && frm.doc.docstatus === 1) {
			const status_result = await frappe.call("meixin_admin.execution.get_session_execution_status", {
				session: frm.doc.name,
			});
			const execution_status = status_result.message || { status: "待上课" };
			frm.dashboard.set_headline_alert(`实际执行状态：${frappe.utils.escape_html(execution_status.status)}`);
			frm.add_custom_button(execution_status.execution ? "查看执行单" : "记录上课结果", async () => {
				if (execution_status.execution) {
					frappe.set_route("Form", "MX Session Execution", execution_status.execution);
					return;
				}
				const result = await frappe.call("meixin_admin.execution.make_execution", {
					session: frm.doc.name,
				});
				const docs = frappe.model.sync(result.message);
				frappe.set_route("Form", "MX Session Execution", docs[0].name);
			});
		}
		const result = await frappe.call("meixin_admin.api.get_context");
		const context = result.message;
		if (!context) return;
		const site_zone = frappe.utils.escape_html(context.time_zone);
		const user_zone = frappe.utils.escape_html(context.user_time_zone || context.time_zone);
		frm.fields_dict.booking_notice.$wrapper.html(
			`<div class="alert alert-info">站点时区：${site_zone}；表单与日历按当前用户时区 ${user_zone} 显示。` +
			"草稿不占用时段，提交才确认。</div>"
		);
	},
});
