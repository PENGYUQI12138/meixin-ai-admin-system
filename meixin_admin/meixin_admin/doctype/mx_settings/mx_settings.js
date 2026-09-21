frappe.ui.form.on("MX Settings", {
	async refresh(frm) {
		const result = await frappe.call("meixin_admin.api.get_context");
		const context = result.message;
		if (!context) return;
		const zone = frappe.utils.escape_html(context.time_zone);
		const user_zone = frappe.utils.escape_html(context.user_time_zone || context.time_zone);
		const offset = moment.tz(context.time_zone).utcOffset();
		const warning = offset !== 480
			? '<p class="text-danger">当前站点时区与中国时间（UTC+8）不同。请先确认排课时间的换算和影响，再由系统管理员决定是否更改全站时区。</p>'
			: "<p>当前站点时区对应中国时间（UTC+8）。</p>";
		frm.fields_dict.site_time_zone.$wrapper.html(
			`<div class="alert alert-info"><p>实际站点时区：<strong>${zone}</strong></p>` +
			`<p>当前用户显示时区：${user_zone}</p>${warning}` +
			"<p>此处只读展示实际配置，不会更改全站时区。原生日期时间表单和日历按当前用户时区显示。</p></div>"
		);
	},
});
