from meixin_admin.scheduling import SchedulingDocument, validate_master


class MXCourse(SchedulingDocument):
    def validate(self):
        validate_master(self)

