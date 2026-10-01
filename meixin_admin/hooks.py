app_name = "meixin_admin"
app_title = "美心行政"
app_publisher = "Meixin"
app_description = "学生、教师、课程、教室与单节排课"
app_email = ""
app_license = "MIT"
required_apps = ["frappe"]
calendars = ["MX Session"]

before_install = "meixin_admin.install.before_install"
after_install = "meixin_admin.install.after_install"
after_migrate = "meixin_admin.install.after_migrate"

fixtures = [{"dt": "Role", "filters": [["name", "in", ["Meixin Manager", "Meixin Scheduler"]]]}]

_doctypes = ["MX Student", "MX Teacher", "MX Course", "MX Room", "MX Session",
             "MX Session Execution", "MX Lesson Consumption Entry", "MX Settings",
             "MX Package Plan", "MX Student Package", "MX Payment", "MX Lesson Credit Entry",
             "MX Teacher Hour Entry"]
has_permission = {dt: "meixin_admin.permissions.has_permission" for dt in _doctypes}
permission_query_conditions = {dt: "meixin_admin.permissions.query_conditions" for dt in _doctypes}
doc_events = {"DocShare": {"validate": "meixin_admin.permissions.prevent_mx_share"}}
