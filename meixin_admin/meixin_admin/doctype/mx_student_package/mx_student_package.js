frappe.ui.form.on("MX Student Package", {
	async refresh(frm) {
		const wrapper = frm.fields_dict.package_overview?.$wrapper;
		if (!wrapper) return;
		if (frm.is_new()) {
			wrapper.text("保存课包后显示服务端派生的付款、状态和课时权益。");
			return;
		}
		const name = frm.doc.name;
		try {
			const { message: view } = await frappe.call({
				method: "meixin_admin.entitlements.package_overview",
				args: { student_package: name, include_entries: 1 },
			});
			if (frm.doc.name !== name) return;
			const safe = (value) => frappe.utils.escape_html(String(value ?? ""));
			const rows = (view.credits || []).map((row) => `<tr>
				<td>${safe(row.creation)}</td><td>${safe(row.operation_type)}</td>
				<td>${safe(row.effect)}</td><td>${safe(row.source_doctype)} ${safe(row.source_name)}</td>
			</tr>`).join("");
			wrapper.html(`<div class="mb-3">
				<strong>派生状态：</strong>${safe(view.status)}　
				<strong>应收：</strong>${safe(view.deal_amount)}　
				<strong>已付净额：</strong>${safe(view.paid_amount)}　
				<strong>剩余课时：</strong>${view.remaining_credits === null ? "待核查" : safe(view.remaining_credits)}
			</div><div class="text-muted mb-2">余额和状态仅供展示；业务写入时会重新锁定并计算。</div>
			<table class="table table-bordered table-sm"><thead><tr>
				<th>创建时间</th><th>权益操作</th><th>课时变化</th><th>来源单据</th>
			</tr></thead><tbody>${rows || '<tr><td colspan="4">暂无权益流水</td></tr>'}</tbody></table>`);
		} catch (error) {
			wrapper.text("课包派生信息暂不可用，请检查查看权限或联系管理员。");
		}
	},
});
