frappe.listview_settings["MX Session"] = {
	add_fields: ["docstatus", "course", "teacher", "room", "start_at", "end_at"],
	has_indicator_for_draft: true,
	has_indicator_for_cancelled: true,
	get_indicator(doc) {
		return [
			["草稿 · 不占时段", "orange", "docstatus,=,0"],
			["已确认", "green", "docstatus,=,1"],
			["已取消", "gray", "docstatus,=,2"],
		][doc.docstatus];
	},
	onload(listview) {
		listview.page.add_inner_button("周课表", () => {
			frappe.set_route("List", "MX Session", "Calendar", "default");
		});
	},
};
