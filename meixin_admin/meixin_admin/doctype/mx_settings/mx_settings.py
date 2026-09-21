from meixin_admin.permissions import require_manager
from meixin_admin.scheduling import SchedulingDocument


class MXSettings(SchedulingDocument):
    def validate(self):
        require_manager()
