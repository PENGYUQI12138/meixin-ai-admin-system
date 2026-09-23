frappe.listview_settings["MX Student Package"] = {
	onload(listview) {
		listview.page.add_inner_button("购买/续费课包", () => frappe.new_doc("MX Student Package"));
	},
};
