frappe.listview_settings["MX Student Package"] = {
	onload(listview) {
		listview.page.add_inner_button("购买/续费课包", () => frappe.new_doc("MX Student Package"));
		if (frappe.session.user === "Administrator" || frappe.user_roles.includes("Meixin Manager")) {
			listview.page.add_inner_button("赠送课包", () => frappe.new_doc("MX Student Package", {
				acquisition_type: "赠送",
			}));
		}
	},
};
