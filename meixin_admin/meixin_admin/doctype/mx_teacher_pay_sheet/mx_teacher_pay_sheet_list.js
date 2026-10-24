frappe.listview_settings["MX Teacher Pay Sheet"] = {
	add_fields: ["status", "teacher_name_snapshot", "total_amount"],
	onload(listview) {
		if (mx_payroll.admin()) listview.page.add_inner_button("新建工资单", () => mx_payroll.open_editor());
		listview.page.add_inner_button("工资结算报表", () => frappe.set_route("query-report", "教师工资结算概览"));
	},
	get_indicator(doc) {
		return [doc.status, { "待核对": "orange", "已核对": "blue", "已批准": "green" }[doc.status], "status,=," + doc.status];
	},
};
