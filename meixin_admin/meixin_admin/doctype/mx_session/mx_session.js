frappe.ui.form.on("MX Session", {
	setup(frm) {
		for (const fieldname of ["course", "teacher", "room"]) {
			frm.set_query(fieldname, () => ({ filters: { enabled: 1 } }));
		}
		frm.set_query("student", "students", () => ({ filters: { enabled: 1 } }));
	},

	async refresh(frm) {
		const messages = [
			"草稿不占用时段。保存后点击“提交”才确认排课；提交时服务器会重新检查冲突。",
			"本节课已确认并占用时段。需要调整时，请由美心管理员取消后修订，再重新提交。",
			"本节课已取消，时段已释放，记录保留。可点击“修订”生成新的排课草稿。",
		];
		frm.set_intro(messages[frm.doc.docstatus], frm.doc.docstatus === 1 ? "green" : "blue");
		frm.add_custom_button("周课表", () => frappe.set_route("List", "MX Session", "Calendar", "default"));
		const result = await frappe.call("meixin_admin.api.get_context");
		const context = result.message;
		if (!context) return;
		const site_zone = frappe.utils.escape_html(context.time_zone);
		const user_zone = frappe.utils.escape_html(frappe.boot.time_zone?.user || context.time_zone);
		frm.fields_dict.booking_notice.$wrapper.html(
			`<div class="alert alert-info">站点时区：${site_zone}；表单与日历按当前用户时区 ${user_zone} 显示。` +
			"草稿不占用时段，提交才确认。</div>"
		);
	},
});
