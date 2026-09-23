const mx_safe = (value) => frappe.utils.escape_html(String(value ?? ""));

async function mx_show_plan_preview(frm) {
	const wrapper = frm.fields_dict.package_overview?.$wrapper;
	if (!wrapper || !frm.is_new()) return;
	const selected = frm.doc.package_plan;
	if (!selected) {
		wrapper.text("选择启用的课包产品后，这里会显示课程、标准课时和价格。购买快照以服务器创建时的数据为准。");
		return;
	}
	const { message: plan } = await frappe.db.get_value("MX Package Plan", selected,
		["plan_name", "course", "standard_credits", "standard_price", "enabled"]);
	if (frm.doc.package_plan !== selected) return;
	if (!plan?.plan_name || !plan.enabled) {
		wrapper.text("该课包产品不可用，请选择启用中的产品。");
		return;
	}
	const { message: course } = await frappe.db.get_value("MX Course", plan.course, "course_name");
	if (frm.doc.package_plan !== selected) return;
	const price = frappe.format(plan.standard_price, { fieldtype: "Currency", options: "CNY", precision: 2 }, { only_value: true });
	wrapper.html(`<div class="alert alert-info">产品：${mx_safe(plan.plan_name)}；课程：${mx_safe(course?.course_name)}；` +
		`标准课时：${mx_safe(plan.standard_credits)}；标准价格：${mx_safe(price)}。` +
		"保存时服务器会重新核对产品并冻结成交快照。</div>");
}

async function mx_create_purchase(frm) {
	if (frm._mx_purchase_pending) return;
	if (!frm.doc.student || !frm.doc.package_plan || !frm.doc.effective_from) {
		frappe.msgprint("请选择学生、课包产品和生效日期。");
		return;
	}
	frm._mx_purchase_pending = true;
	try {
		const { message: name } = await frappe.call({
			method: "meixin_admin.entitlements.create_student_package",
			args: {
				student: frm.doc.student, package_plan: frm.doc.package_plan,
				acquisition_type: "购买", effective_from: frm.doc.effective_from,
				expires_on: frm.doc.expires_on, source_reference: frm.doc.source_reference,
				request_id: frm._mx_purchase_request_id,
			},
		});
		frappe.set_route("Form", "MX Student Package", name);
	} finally {
		frm._mx_purchase_pending = false;
	}
}

function mx_open_receipt(frm, view) {
	const request_id = crypto.randomUUID();
	let pending = false;
	const dialog = new frappe.ui.Dialog({
		title: "录入收款",
		fields: [
			{ fieldtype: "HTML", fieldname: "summary", options:
				`<div class="alert alert-info">状态：${mx_safe(view.status)}；应收：${mx_safe(view.deal_amount)}；` +
				`已付净额：${mx_safe(view.paid_amount)}；尚需付款：${mx_safe(view.due_amount)}。</div>` },
			{ fieldtype: "Currency", fieldname: "amount", label: "本次实收（CNY）", options: "CNY", precision: 2, reqd: 1 },
			{ fieldtype: "Select", fieldname: "payment_method", label: "支付方式", options: "现金\n银行转账\n其他", reqd: 1 },
			{ fieldtype: "Datetime", fieldname: "paid_at", label: "支付时间", default: frappe.datetime.now_datetime(), reqd: 1 },
			{ fieldtype: "Small Text", fieldname: "note", label: "备注" },
		],
		primary_action_label: "确认收款",
		primary_action: async (values) => {
			if (pending) return;
			pending = true;
			try {
				await frappe.call({
					method: "meixin_admin.payments.record_payment",
					args: { student_package: frm.doc.name, ...values, request_id },
				});
				dialog.hide();
				await frm.reload_doc();
			} finally {
				pending = false;
			}
		},
	});
	dialog.show();
}

frappe.ui.form.on("MX Student Package", {
	setup(frm) {
		frm.set_query("student", () => ({ filters: { enabled: 1 } }));
		frm.set_query("package_plan", () => ({ filters: { enabled: 1 } }));
	},

	onload(frm) {
		if (!frm.is_new()) return;
		frm._mx_purchase_request_id = crypto.randomUUID();
		frm.set_value("acquisition_type", "购买");
		if (!frm.doc.effective_from) frm.set_value("effective_from", frappe.datetime.get_today());
	},

	package_plan(frm) {
		return mx_show_plan_preview(frm);
	},

	async refresh(frm) {
		const wrapper = frm.fields_dict.package_overview?.$wrapper;
		if (!wrapper) return;
		if (frm.is_new()) {
			frm.disable_save();
			frm.set_df_property("acquisition_type", "read_only", 1);
			frm.set_intro("购买/续费始终新建课包；请核对产品预览后创建草稿，再点击原生“提交”。", "blue");
			frm.add_custom_button("创建购买草稿", () => mx_create_purchase(frm));
			await mx_show_plan_preview(frm);
			return;
		}
		frm.add_custom_button("续费/新购课包", () => frappe.new_doc("MX Student Package", {
			student: frm.doc.student, package_plan: frm.doc.package_plan,
		}));
		const name = frm.doc.name;
		try {
			const { message: view } = await frappe.call({
				method: "meixin_admin.entitlements.package_overview",
				args: { student_package: name, include_entries: 1 },
			});
			if (frm.doc.name !== name) return;
			const credits = (view.credits || []).map((row) => `<tr><td>${mx_safe(row.creation)}</td>` +
				`<td>${mx_safe(row.operation_type)}</td><td>${mx_safe(row.effect)}</td>` +
				`<td>${mx_safe(row.source_doctype)} ${mx_safe(row.source_name)}</td></tr>`).join("");
			const payments = (view.payments || []).map((row) => `<tr><td>${mx_safe(row.paid_at)}</td>` +
				`<td>${mx_safe(row.operation_type)}</td><td>${mx_safe(row.cash_effect)}</td>` +
				`<td>${mx_safe(row.payment_method)}</td><td>${mx_safe(row.name)}</td></tr>`).join("");
			wrapper.html(`<div class="mb-3"><strong>派生状态：</strong>${mx_safe(view.status)}　` +
				`<strong>应收：</strong>${mx_safe(view.deal_amount)}　` +
				`<strong>已付净额：</strong>${mx_safe(view.paid_amount)}　` +
				`<strong>尚需付款：</strong>${mx_safe(view.due_amount)}　` +
				`<strong>剩余课时：</strong>${view.remaining_credits === null ? "待核查" : mx_safe(view.remaining_credits)}</div>` +
				`<div class="text-muted mb-2">余额和状态仅供展示；业务写入时会重新锁定并计算。</div>` +
				`<h5>付款记录</h5><table class="table table-bordered table-sm"><thead><tr>` +
				`<th>支付时间</th><th>操作</th><th>现金效果</th><th>方式</th><th>单号</th></tr></thead>` +
				`<tbody>${payments || '<tr><td colspan="5">暂无付款记录</td></tr>'}</tbody></table>` +
				`<h5>课时权益流水</h5><table class="table table-bordered table-sm"><thead><tr>` +
				`<th>创建时间</th><th>权益操作</th><th>课时变化</th><th>来源单据</th></tr></thead>` +
				`<tbody>${credits || '<tr><td colspan="4">暂无权益流水</td></tr>'}</tbody></table>`);
			if (frm.doc.docstatus === 1 && frm.doc.acquisition_type === "购买" &&
				view.status !== "已退款关闭" && view.due_amount_value !== "0.00") {
				frm.add_custom_button("录入收款", () => mx_open_receipt(frm, view));
			}
			if (view.credits?.length) frm.add_custom_button("查看权益流水", () => {
				frappe.route_options = { student_package: name };
				frappe.set_route("List", "MX Lesson Credit Entry");
			});
		} catch (error) {
			wrapper.text("课包派生信息暂不可用，请检查查看权限或联系管理员。");
		}
	},
});
