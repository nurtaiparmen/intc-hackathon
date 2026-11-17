from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Group, Homework, ScheduleItem, Subject, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "last_name", "first_name", "role", "study_group", "is_staff")
    list_filter = ("role", "study_group", "is_staff")
    filter_horizontal = (*BaseUserAdmin.filter_horizontal, "teaching_groups")
    fieldsets = (
        *BaseUserAdmin.fieldsets,
        ("Колледж", {"fields": ("role", "study_group", "teaching_groups")}),
    )
    add_fieldsets = (
        *BaseUserAdmin.add_fieldsets,
        ("Колледж", {"fields": ("first_name", "last_name", "role", "study_group")}),
    )


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "course")
    search_fields = ("name",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(ScheduleItem)
class ScheduleItemAdmin(admin.ModelAdmin):
    list_display = (
        "group", "weekday", "lesson_number", "subject", "room",
        "week_type", "is_replacement", "date", "is_cancelled",
    )
    list_filter = ("group", "is_replacement", "week_type", "weekday")
    autocomplete_fields = ("subject",)


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = ("title", "group", "subject", "deadline", "teacher")
    list_filter = ("group", "subject")
    autocomplete_fields = ("subject",)
