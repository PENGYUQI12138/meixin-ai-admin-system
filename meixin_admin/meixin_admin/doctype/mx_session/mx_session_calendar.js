// Use the installed Frappe 16 Calendar, including its permission-aware native filters.
frappe.views.calendar["MX Session"] = {
	field_map: {
		start: "start_at",
		end: "end_at",
		id: "name",
		title: "title",
		allDay: "allDay",
		status: "docstatus",
		color: "color",
	},
	get_events_method: "meixin_admin.api.get_events",
	get_args(start, end) {
		return {
			start: this.get_system_datetime(start),
			end: this.get_system_datetime(end),
			filters: this.list_view.filter_area.get(),
		};
	},
	options: {
		initialView: "timeGridWeek",
		firstDay: 1,
		weekends: true,
		slotMinTime: "00:00:00",
		slotMaxTime: "24:00:00",
		scrollTime: "08:00:00",
		allDaySlot: false,
		eventStartEditable: false,
		eventDurationEditable: false,
		eventTimeFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
		slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
		headerToolbar: {
			left: "prev,title,next",
			center: "mx_filters",
			right: "today,timeGridWeek,timeGridDay,dayGridMonth",
		},
		customButtons: {
			mx_filters: {
				text: "日期 / 教师 / 教室",
				click() {
					const listview = cur_list;
					const calendar = listview.calendar;
					const current = listview.filter_area.get();
					const selected = (field) => current.find((f) => f[1] === field && f[2] === "=")?.[3] || "";
					frappe.prompt([
						{ fieldname: "date", fieldtype: "Date", label: "查看哪一天所在的周", reqd: 1,
							default: moment(calendar.fullCalendar.getDate()).format("YYYY-MM-DD") },
						{ fieldname: "teacher", fieldtype: "Link", label: "教师（留空显示全部）", options: "MX Teacher", default: selected("teacher") },
						{ fieldname: "room", fieldtype: "Link", label: "教室（留空显示全部）", options: "MX Room", default: selected("room") },
					], async (values) => {
						const filters = current.filter((f) => !["teacher", "room"].includes(f[1]));
						for (const field of ["teacher", "room"]) {
							if (values[field]) filters.push(["MX Session", field, "=", values[field]]);
						}
						await listview.filter_area.clear(false);
						await listview.filter_area.set(filters);
						calendar.fullCalendar.changeView("timeGridWeek", values.date);
						calendar.refresh();
					}, "筛选周课表", "查看");
				},
			},
		},
		viewDidMount(info) {
			const zone = frappe.boot.time_zone?.user || frappe.boot.time_zone?.system || "";
			const banner = document.createElement("div");
			banner.className = "text-muted small mb-3 mx-calendar-guide";
			banner.textContent = `橙色：草稿（不占时段）；绿色：已确认；已取消默认隐藏。显示时区：${zone}。点击课节查看详情，调整排课请进入表单。`;
			info.el.prepend(banner);
		},
	},
};
