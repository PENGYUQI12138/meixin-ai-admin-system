from frappe.model.document import Document

from meixin_admin.permissions import require_manager


class MXSettings(Document):
    def validate(self):
        require_manager()
