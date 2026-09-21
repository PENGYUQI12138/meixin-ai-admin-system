frappe.ui.form.on("MX Lesson Consumption Entry", {
	refresh(frm) {
		frm.set_intro("课消流水不可修改或删除；纠错只会追加撤销流水。", "blue");
		if (
			frm.doc.operation_type === "决定" &&
			Number(frm.doc.effect) === 1 &&
			frappe.user_roles.includes("Meixin Manager")
		) {
			frm.add_custom_button("撤销课消", () => {
				frappe.prompt(
					[{ fieldname: "reason", fieldtype: "Small Text", label: "撤销原因", reqd: 1 }],
					async ({ reason }) => {
						const result = await frappe.call("meixin_admin.consumption.manual_reverse", {
							entry: frm.doc.name,
							reason,
						});
						frappe.show_alert({ message: "已追加撤销流水", indicator: "green" });
						frappe.set_route("Form", "MX Lesson Consumption Entry", result.message);
					},
					"撤销课消",
					"确认撤销",
				);
			});
		}
	},
});
