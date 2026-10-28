/* Native Desk controls; server APIs recheck roles, sources and amounts. */
window.mx_payroll = {
	admin: () => frappe.session.user === "Administrator" ||
		frappe.user_roles.some((role) => ["Meixin Manager", "Meixin Scheduler"].includes(role)),
	finance: () => frappe.session.user === "Administrator" || frappe.user_roles.includes("Meixin Finance"),
	escape: (value) => frappe.utils.escape_html(String(value ?? "")),
	async call(method, args = {}) {
		return (await frappe.call({ method: "meixin_admin.teacher_payroll." + method, args })).message;
	},
	source_id: (source) => source.hour_entry || source.name,
	source_label(source) {
		return [source.actual_start || source.scheduled_start || source.pay_date || "",
			source.course_name_snapshot || source.course_name || "",
			"名单 " + (source.roster_size ?? "?") + " 人", this.source_id(source)].filter(Boolean).join(" · ");
	},
	show_summary(view, title = "授课来源摘要") {
		const labels = {
			name: "授课确认", hour_entry: "授课确认", teacher: "教师标识",
			teacher_name_snapshot: "教师", teacher_name: "教师",
			course_name_snapshot: "课程", course_name: "课程",
			source_root: "原始课次修订链", session: "排课标识",
			pay_date: "计薪日期", scheduled_start: "计划开始", scheduled_end: "计划结束",
			actual_start: "实际开始", actual_end: "实际结束", roster_size: "冻结名单人数",
			has_attendance: "是否有人到课", has_present: "是否有人到课", effect_minutes: "确认分钟",
			actual_minutes: "原始时长（分钟）", review_issue: "待核对事项", anomalies: "异常类别",
			active: "来源有效", valid: "来源有效", exception: "异常类别", normal_90: "正常 90 分钟",
			present_any: "是否有人到课", issues: "待核对事项", is_valid: "来源有效", reversal: "撤销记录",
		};
		const rows = Object.entries(view || {}).map(([key, value]) => {
			if (value === null || value === undefined) return "";
			const text = typeof value === "boolean" ? (value ? "是" : "否") : Array.isArray(value) ? value.join("、") :
				typeof value === "object" ? Object.values(value).join("、") : value;
			return "<tr><th>" + this.escape(labels[key] || key) + "</th><td>" + this.escape(text) + "</td></tr>";
		}).join("");
		frappe.msgprint({ title, message: '<table class="table table-bordered table-sm">' + rows + "</table>", wide: true });
	},
	async source_dialog() {
		frappe.prompt([{ fieldtype: "Data", fieldname: "hour_entry", label: "授课确认标识", reqd: 1 }],
			async (values) => this.show_summary(await this.call("get_source_summary", values)), "查看履职所需来源摘要", "查看");
	},
	async open_editor(sheet = null) {
		if (!this.admin()) return;
		const teachers = await this.call("list_pay_teachers");
		const teacher_options = (teachers || []).map((row) => ({
			value: row.name || row.teacher, label: this.escape(row.teacher_name || row.teacher_name_snapshot || row.name || row.teacher),
		}));
		let input = [];
		if (sheet?.input_json) input = JSON.parse(sheet.input_json);
		const data = input.map((line) => ({ ...line, hours_text: (line.hours || []).join("\n") }));
		let sources = [], loading = false, pending = false;
		const request_id = crypto.randomUUID();
		const dialog = new frappe.ui.Dialog({
			title: sheet ? "编辑待核对工资明细" : "新建教师工资结算单",
			size: "extra-large",
			fields: [
				{ fieldname: "teacher", fieldtype: "Select", label: "教师", options: [{ value: "", label: "请选择教师" }, ...teacher_options],
					reqd: 1, default: sheet?.teacher, read_only: !!sheet },
				{ fieldname: "from_date", fieldtype: "Date", label: "开始日期", reqd: 1,
					default: sheet?.from_date || frappe.datetime.get_today().slice(0, 8) + "01", read_only: !!sheet },
				{ fieldname: "to_date", fieldtype: "Date", label: "结束日期", reqd: 1,
					default: sheet?.to_date || frappe.datetime.get_today(), read_only: !!sheet },
				{ fieldname: "load_sources", fieldtype: "Button", label: "加载该教师日期范围内授课" },
				{ fieldname: "source_notice", fieldtype: "HTML", options:
					'<p class="text-muted">先选教师与日期并加载授课，再添加明细。人数仅按冻结名单预填，必须核对请假、缺勤是否完整。固定费用须明确全天或指定课程/工作事项；本期仅支持原排课单教师。</p>' },
				{ fieldname: "lines", fieldtype: "Table", label: "工资明细（勾选一行后可修改）",
					cannot_add_rows: true, in_place_edit: false, data,
					fields: [
						{ fieldname: "kind", fieldtype: "Data", label: "计费类型", read_only: 1, in_list_view: 1, columns: 1 },
						{ fieldname: "hour_entry", fieldtype: "Data", label: "授课确认", read_only: 1, in_list_view: 1, columns: 2 },
						{ fieldname: "pay_date", fieldtype: "Date", label: "日期", read_only: 1, in_list_view: 1, columns: 2 },
						{ fieldname: "class_size", fieldtype: "Int", label: "计薪人数", read_only: 1, in_list_view: 1, columns: 1 },
						{ fieldname: "scope", fieldtype: "Data", label: "固定范围", read_only: 1, in_list_view: 1, columns: 1 },
						{ fieldname: "amount", fieldtype: "Float", precision: 2, label: "特殊/固定金额（CNY）", read_only: 1, in_list_view: 1, columns: 1 },
						{ fieldname: "description", fieldtype: "Small Text", label: "内容与覆盖约定", read_only: 1, in_list_view: 1, columns: 2 },
						{ fieldname: "hours_text", fieldtype: "Small Text", label: "指定覆盖授课", read_only: 1 },
						{ fieldname: "headcount_basis", fieldtype: "Small Text", label: "人数与归属核对依据", read_only: 1 },
						{ fieldname: "exception_reason", fieldtype: "Small Text", label: "特殊金额原因", read_only: 1 },
						{ fieldname: "work_key", fieldtype: "Data", label: "工作事项标识", read_only: 1 },
					] },
				{ fieldname: "add_line", fieldtype: "Button", label: "添加课程或固定费用" },
				{ fieldname: "edit_line", fieldtype: "Button", label: "修改勾选的明细" },
			],
			primary_action_label: sheet ? "保存待核对明细" : "创建待核对单",
			primary_action: async (values) => {
				if (pending) return;
				const lines = dialog.fields_dict.lines.df.data.map((row) => {
					const line = {};
					for (const key of ["kind", "hour_entry", "class_size", "headcount_basis", "exception_reason",
						"amount", "pay_date", "scope", "work_key", "description"]) {
						if (row[key] !== undefined && row[key] !== null && row[key] !== "") line[key] = row[key];
					}
					if (row.hours_text) line.hours = row.hours_text.split("\n").filter(Boolean);
					return line;
				});
				if (!lines.length) return frappe.msgprint("请至少添加一行工资明细。");
				pending = true;
				try {
					const result = sheet ? await this.call("update_sheet", { sheet: sheet.name, lines }) :
						await this.call("create_sheet", { teacher: values.teacher, from_date: values.from_date,
							to_date: values.to_date, lines, request_id });
					dialog.hide();
					const name = typeof result === "string" ? result : result.name || sheet?.name;
					if (cur_frm?.doc?.name === name) await cur_frm.reload_doc();
					else frappe.set_route("Form", "MX Teacher Pay Sheet", name);
				} finally { pending = false; }
			},
		});
		const load_sources = async () => {
			const teacher = dialog.get_value("teacher"), from_date = dialog.get_value("from_date"), to_date = dialog.get_value("to_date");
			if (!teacher || !from_date || !to_date) return frappe.msgprint("请先选择教师和起止日期。");
			if (loading) return;
			loading = true;
			try {
				sources = await this.call("list_pay_sources", { teacher, from_date, to_date });
				dialog.fields_dict.source_notice.$wrapper.html('<p class="text-muted">已加载 ' +
					this.escape(sources.length) + ' 条授课来源。预填人数仍须行政确认；来源和规则以服务器核对时为准。</p>');
			} finally { loading = false; }
		};
		const edit_line = (row = null) => {
			const fixed = 'eval:doc.kind=="固定"';
			let line_pending = false;
			const line_dialog = new frappe.ui.Dialog({
				title: row ? "修改工资明细" : "添加工资明细", size: "large",
				fields: [
					{ fieldname: "kind", fieldtype: "Select", label: "计费类型", options: "课程\n特殊\n固定", default: "课程", reqd: 1 },
					{ fieldname: "hour_entry", fieldtype: "Select", label: "已确认授课",
						options: [{ value: "", label: "请选择来源" }, ...sources.map((source) => ({ value: this.source_id(source), label: this.escape(this.source_label(source)) }))],
						depends_on: 'eval:doc.kind!="固定"', mandatory_depends_on: 'eval:doc.kind!="固定"' },
					{ fieldname: "source_preview", fieldtype: "Button", label: "查看所选来源摘要", depends_on: 'eval:doc.kind!="固定"' },
					{ fieldname: "class_size", fieldtype: "Int", label: "当日计薪班级人数（含请假、缺勤）", depends_on: 'eval:doc.kind!="固定"' },
					{ fieldname: "headcount_basis", fieldtype: "Small Text", label: "人数与教师归属核对依据",
						description: "例如：已核对当日完整名单含请假、缺勤，原排课教师实际授课无争议；补人数须说明依据。",
						depends_on: 'eval:doc.kind!="固定"' },
					{ fieldname: "exception_reason", fieldtype: "Small Text", label: "特殊金额或异常核对原因",
						depends_on: 'eval:doc.kind!="课程"', mandatory_depends_on: 'eval:doc.kind!="课程"' },
					{ fieldname: "scope", fieldtype: "Select", label: "固定费用覆盖范围", options: "全天\n指定", default: "全天", depends_on: fixed, mandatory_depends_on: fixed },
					{ fieldname: "pay_date", fieldtype: "Date", label: "费用归属日（全天覆盖用当天）", default: dialog.get_value("from_date"), depends_on: fixed, mandatory_depends_on: fixed },
					{ fieldname: "hours", fieldtype: "MultiCheck", label: "指定覆盖课程（可多选；独立工作事项可不选）",
						options: sources.map((source) => ({ value: this.source_id(source), label: this.escape(this.source_label(source)) })),
						description: "指定课程可跨所选结算日期，固定费用金额不按课程拆分。",
						depends_on: 'eval:doc.kind=="固定"&&doc.scope=="指定"', columns: 1 },
					{ fieldname: "work_key", fieldtype: "Data", label: "明确工作事项标识",
						description: "同一工作事项跨结算使用同一标识，避免重复计费。", depends_on: 'eval:doc.kind=="固定"&&doc.scope=="指定"' },
					{ fieldname: "amount", fieldtype: "Float", precision: 2, label: "特殊或固定金额（CNY）", depends_on: 'eval:doc.kind!="课程"', mandatory_depends_on: 'eval:doc.kind!="课程"' },
					{ fieldname: "description", fieldtype: "Small Text", label: "内容与覆盖约定（固定费必填）", mandatory_depends_on: fixed },
				],
				primary_action_label: row ? "更新此行" : "添加此行",
				primary_action: (values) => {
					if (line_pending) return;
					line_pending = true;
					if (values.kind === "固定") {
						delete values.hour_entry; delete values.class_size; delete values.headcount_basis;
						if (values.scope === "全天") { values.hours = []; delete values.work_key; }
					} else {
						for (const key of ["scope", "pay_date", "hours", "work_key"]) delete values[key];
						if (values.kind === "课程") { delete values.amount; delete values.exception_reason; }
					}
					const record = { ...values, hours_text: (values.hours || []).join("\n") };
					if (row) {
						for (const key of ["kind", "hour_entry", "class_size", "headcount_basis", "exception_reason",
							"amount", "pay_date", "scope", "work_key", "description", "hours", "hours_text"]) delete row[key];
						Object.assign(row, record);
					}
					else dialog.fields_dict.lines.df.data.push(record);
					dialog.fields_dict.lines.grid.refresh();
					line_dialog.hide();
				},
			});
			line_dialog.fields_dict.hour_entry.df.onchange = () => {
				const source = sources.find((item) => this.source_id(item) === line_dialog.get_value("hour_entry"));
				if (source) line_dialog.set_value("class_size", source.roster_size);
			};
			line_dialog.fields_dict.source_preview.df.click = async () => {
				const hour_entry = line_dialog.get_value("hour_entry");
				if (hour_entry) this.show_summary(await this.call("get_source_summary", { hour_entry }));
			};
			line_dialog.show();
			if (row) {
				line_dialog.set_values({ ...row, hours: (row.hours_text || "").split("\n").filter(Boolean) });
			}
		};
		dialog.fields_dict.load_sources.df.click = load_sources;
		dialog.fields_dict.add_line.df.click = () => edit_line();
		dialog.fields_dict.edit_line.df.click = () => {
			const selected = dialog.fields_dict.lines.grid.get_selected_children();
			if (selected.length !== 1) return frappe.msgprint("请勾选且只勾选一行。");
			edit_line(selected[0]);
		};
		dialog.show();
		if (sheet) await load_sources();
	},
	async sheet_action(frm, method, message) {
		frappe.confirm(message, async () => {
			await this.call(method, { sheet: frm.doc.name });
			await frm.reload_doc();
		});
	},
	async payment_dialog(frm, difference = false) {
		const balance = await this.call("get_pay_balance", { sheet: frm.doc.name });
		const balance_version = balance.balance_version;
		const request_id = crypto.randomUUID();
		let pending = false;
		const dialog = new frappe.ui.Dialog({
			title: difference ? "登记已发生付款差异" : "登记实际线下付款",
			fields: [
				{ fieldtype: "HTML", fieldname: "notice", options:
					difference ? '<div class="alert alert-warning">仅用于整单付款已实际发生后，与当前核算不一致的事实登记。请按凭据填写真实金额、原因与依据；差额保留待财务处理，不自动退款、扣款或生成分次付款。</div>' :
						'<div class="alert alert-info">请按线下付款凭据填写实际金额。金额和核算版本必须与本次确认一致；提交时变化会拒绝登记，不会替换实际金额。本操作不会转账。</div>' },
				{ fieldname: "expected_amount", fieldtype: "Float", precision: 2, label: "本次核算净应付（CNY）", read_only: 1, default: balance.net_payable },
				{ fieldname: "actual_amount", fieldtype: "Float", precision: 2, label: "凭据上的实际付款金额（CNY）", reqd: 1 },
				{ fieldname: "paid_at", fieldtype: "Datetime", label: "实际付款时间", reqd: 1, default: frappe.datetime.now_datetime() },
				{ fieldname: "payment_method", fieldtype: "Select", label: "付款方式", reqd: 1, options: "\n现金\n微信\n支付宝\n银行\n其他" },
				{ fieldname: "reference_no", fieldtype: "Data", label: "线下付款参考号或凭据标识", reqd: 1 },
				...(difference ? [
					{ fieldname: "reason", fieldtype: "Small Text", label: "已发生付款与当前核算不一致的原因", reqd: 1 },
					{ fieldname: "basis", fieldtype: "Small Text", label: "实际付款及差异核对依据", reqd: 1 },
				] : []),
				{ fieldname: "confirmed", fieldtype: "Check", label: "我已核对凭据，以上金额确已实际支付，且本单整单付款只登记一次", reqd: 1 },
			],
			primary_action_label: difference ? "保留真实付款并登记差异" : "确认已付款并登记",
			primary_action: async (values) => {
				if (pending) return;
				if (!values.confirmed) return frappe.msgprint("请先确认凭据及实际付款事实。");
				pending = true;
				try {
					const { actual_amount, paid_at, payment_method, reference_no, reason, basis } = values;
					await this.call(difference ? "record_pay_difference" : "record_pay_payment", {
						sheet: frm.doc.name, actual_amount, paid_at, payment_method, reference_no,
						balance_version, request_id, ...(difference ? { reason, basis } : {}),
					});
					dialog.hide(); await frm.reload_doc();
				} catch (error) {
					await dialog.set_values(values);
					throw error;
				} finally { pending = false; }
			},
		});
		dialog.show();
	},
	async correction_dialog(frm, related_adjustment = null) {
		const sheet_name = related_adjustment ? frm.doc.sheet : frm.doc.name;
		const sheet = related_adjustment ? null : frm.doc;
		const request_id = crypto.randomUUID();
		let pending = false;
		const targets = (sheet?.lines || []).map((line) => ({ value: String(line.idx),
			label: this.escape(["第" + line.idx + "行", line.kind, line.pay_date, line.description || line.hour_entry || line.work_key, "原额 CNY " + line.amount].filter(Boolean).join(" · ")) }));
		const dialog = new frappe.ui.Dialog({
			title: related_adjustment ? "申请更正已批准调整" : "申请工资明细更正",
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "notice", options: '<div class="alert alert-info">更正只追加差额，原账、付款与课次占用保留。请填写差额而非更正后的总额，例如原100应为120填+20。申请须由财务另行批准；已付款差额留待处理，不自动扣款或退款。</div>' },
				{ fieldname: "target", fieldtype: "Data", label: "更正原调整", default: related_adjustment, read_only: 1, hidden: !related_adjustment },
				{ fieldname: "line_index", fieldtype: "Select", label: "原结算明细", options: [{ value: "", label: "请选择原结算明细" }, ...targets], reqd: !related_adjustment, hidden: !!related_adjustment },
				{ fieldname: "amount", fieldtype: "Float", precision: 2, label: "建议追加差额（CNY，负数减少应付）", reqd: 1 },
				{ fieldname: "reason", fieldtype: "Small Text", label: "更正原因", reqd: 1 },
				{ fieldname: "basis", fieldtype: "Small Text", label: "事实、金额及核对依据", reqd: 1 },
			],
			primary_action_label: "提交更正申请",
			primary_action: async (values) => {
				if (pending) return;
				pending = true;
				try {
					const name = await this.call("request_pay_correction", {
						sheet: sheet_name, amount: values.amount, reason: values.reason, basis: values.basis, request_id,
						...(related_adjustment ? { related_adjustment } : { line_index: values.line_index }),
					});
					dialog.hide();
					frappe.set_route("Form", "MX Teacher Pay Adjustment", name);
				} finally { pending = false; }
			},
		});
		dialog.show();
	},
	reject_correction_dialog(frm) {
		let pending = false;
		frappe.prompt([{ fieldname: "reason", fieldtype: "Small Text", label: "驳回原因（申请保留，不改原账）", reqd: 1 }],
			async (values) => {
				if (pending) return;
				pending = true;
				try {
					await this.call("reject_pay_correction", { adjustment: frm.doc.name, reason: values.reason });
					await frm.reload_doc();
				} finally { pending = false; }
			}, "财务驳回更正申请", "驳回并保留申请");
	},
	reversal_dialog(frm, payment) {
		const request_id = crypto.randomUUID();
		let pending = false;
		const dialog = new frappe.ui.Dialog({
			title: "冲正错误付款登记",
			fields: [
				{ fieldtype: "HTML", fieldname: "notice", options: '<div class="alert alert-warning">原付款记录保留，追加唯一反向登记。冲正仅更正系统记录，不表示银行撤回或教师已退款。完成后可按当时净应付重新登记。</div>' },
				{ fieldname: "reason", fieldtype: "Small Text", label: "错误登记及冲正原因", reqd: 1 },
			],
			primary_action_label: "确认冲正登记",
			primary_action: async (values) => {
				if (pending) return;
				pending = true;
				try {
					await this.call("reverse_pay_payment", { payment, reason: values.reason, request_id });
					dialog.hide(); await frm.reload_doc();
				} finally { pending = false; }
			},
		});
		dialog.show();
	},
	adjustment_dialog(frm) {
		let pending = false;
		frappe.prompt([
			{ fieldname: "amount", fieldtype: "Float", label: "批准调整额（CNY，负数表示减少应付）", precision: 2, default: frm.doc.suggested_amount },
			{ fieldname: "reason", fieldtype: "Small Text", label: "财务决定原因（金额为零也必填）", reqd: 1 },
		], async (values) => {
			if (pending) return;
			pending = true;
			try {
				await this.call("approve_adjustment", { adjustment: frm.doc.name, ...values });
				await frm.reload_doc();
			} finally { pending = false; }
		}, "财务批准工资调整", "批准");
	},
	async show_balance(frm) {
		const view = await this.call("get_pay_balance", { sheet: frm.doc.name });
		const labels = {
			total_amount: "原核算额", adjustment_amount: "已批准调整额",
			net_payable: "净应付", net_paid: "有效付款", outstanding: "差额", status: "派生状态",
		};
		const metrics = Object.entries(view || {}).filter(([key, value]) =>
			labels[key] && value !== null && typeof value !== "object").map(([key, value]) =>
			"<div><strong>" + this.escape(labels[key]) + "：</strong>" + this.escape(value) + "</div>").join("");
		const payments = view.payments || [];
		const adjustments = view.adjustments || [];
		const reversed = new Set(payments.map((row) => row.reversal_of).filter(Boolean));
		const payment_rows = payments.map((row) =>
			"<tr><td>" + this.escape(row.name) + "</td><td>" + this.escape(row.operation_type) + "</td><td>" +
			this.escape(row.amount) + "</td><td>" + this.escape(row.paid_at) + "</td><td>" +
			this.escape(row.payment_method) + "</td><td>" + this.escape(row.reversal_of || row.reason || "") + "</td><td>" +
			(this.finance() && row.operation_type === "付款" && !reversed.has(row.name) ?
				'<button class="btn btn-xs btn-default mx-pay-reverse" data-name="' + this.escape(row.name) + '">冲正错误登记</button>' : "") + "</td></tr>").join("");
		const adjustment_rows = adjustments.map((row) =>
			'<tr><td><a class="mx-pay-adjustment" href="#" data-name="' + this.escape(row.name) + '">' +
			this.escape(row.name) + "</a></td><td>" + this.escape(row.event_type) + "</td><td>" +
			this.escape(row.reason) + "</td><td>" + this.escape(row.docstatus === 1 ? (row.decision === "驳回" ? "已驳回" : "已批准") : "待财务处理") +
			"</td><td>" + this.escape(row.docstatus === 1 ? row.amount : row.suggested_amount) + "</td></tr>").join("");
		const wrapper = frm.fields_dict.balance_overview.$wrapper;
		wrapper.html('<div class="mb-3">' + metrics + '</div><h5>付款与冲正</h5><table class="table table-bordered table-sm">' +
			"<thead><tr><th>记录</th><th>操作</th><th>金额</th><th>时间</th><th>方式</th><th>原记录或原因</th><th>操作</th></tr></thead><tbody>" +
			(payment_rows || '<tr><td colspan="7">暂无付款登记</td></tr>') + "</tbody></table>" +
			'<h5>来源变更及工资更正事项</h5><table class="table table-bordered table-sm"><thead><tr><th>事项</th><th>事件</th><th>原因</th><th>状态</th><th>建议/批准金额</th></tr></thead><tbody>' +
			(adjustment_rows || '<tr><td colspan="5">暂无调整事项</td></tr>') + "</tbody></table>");
		wrapper.find(".mx-pay-reverse").on("click", (event) => this.reversal_dialog(frm, event.currentTarget.dataset.name));
		wrapper.find(".mx-pay-adjustment").on("click", (event) => {
			event.preventDefault();
			frappe.set_route("Form", "MX Teacher Pay Adjustment", event.currentTarget.dataset.name);
		});
		return view;
	},
};
