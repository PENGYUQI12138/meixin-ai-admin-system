frappe.query_reports["学生课包概览"] = {
	onload(report) {
		report.page.add_inner_button("购买/续费课包", () => frappe.new_doc("MX Student Package"));
	},
	filters: [
		{ fieldname: "student", label: "学生", fieldtype: "Link", options: "MX Student" },
		{ fieldname: "course", label: "课程", fieldtype: "Link", options: "MX Course" },
	],
};
