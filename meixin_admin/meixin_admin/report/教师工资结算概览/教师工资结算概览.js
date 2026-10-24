frappe.query_reports["教师工资结算概览"] = {
	async onload(report) {
		if (mx_payroll.admin()) report.page.add_inner_button("新建工资单", () => mx_payroll.open_editor());
		const teachers = await mx_payroll.call("list_pay_teachers");
		const field = report.get_filter("teacher");
		field.df.options = [{ value: "", label: "全部教师" }, ...(teachers || []).map((row) => ({
			value: row.name || row.teacher, label: mx_payroll.escape(row.teacher_name || row.teacher_name_snapshot || row.name || row.teacher),
		}))];
		field.refresh();
	},
	filters: [
		{ fieldname: "teacher", label: "教师", fieldtype: "Select", options: "" },
		{ fieldname: "from_date", label: "开始日期", fieldtype: "Date" },
		{ fieldname: "to_date", label: "结束日期", fieldtype: "Date" },
		{ fieldname: "status", label: "结算状态", fieldtype: "Select", options: "\n待核对\n已核对\n待处理调整\n待财务处理\n无需付款\n待付款\n已付款" },
	],
};
