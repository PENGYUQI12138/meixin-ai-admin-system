frappe.query_reports["学生课包概览"] = {
	filters: [
		{ fieldname: "student", label: "学生", fieldtype: "Link", options: "MX Student" },
		{ fieldname: "course", label: "课程", fieldtype: "Link", options: "MX Course" },
	],
};
