from django.contrib.auth import views as auth_views
from django.urls import path

from . import api, views

urlpatterns = [
    # JSON API для мобильного приложения (см. API.md)
    path("api/", api.index, name="api_index"),
    path("api/login/", api.login, name="api_login"),
    path("api/me/", api.me, name="api_me"),
    path("api/today/", api.today, name="api_today"),
    path("api/schedule/", api.schedule, name="api_schedule"),
    path("api/week/", api.week, name="api_week"),
    path("api/homework/", api.homework, name="api_homework"),
    path("api/groups/", api.groups, name="api_groups"),
    path("api/chat/", api.chat, name="api_chat"),
    # Вход и развилка по ролям
    path("", views.home, name="home"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="portal/login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("about/", views.about, name="about"),
    # Студент
    path("student/", views.student_dashboard, name="student_dashboard"),
    path("chat/", views.agent_chat, name="agent_chat"),
    # Преподаватель
    path("teacher/", views.teacher_dashboard, name="teacher_dashboard"),
    path("teacher/groups/<int:group_id>/", views.teacher_group, name="teacher_group"),
    path(
        "teacher/groups/<int:group_id>/replacements/add/",
        views.replacement_create,
        name="replacement_create",
    ),
    path(
        "teacher/groups/<int:group_id>/replacements/<int:item_id>/delete/",
        views.replacement_delete,
        name="replacement_delete",
    ),
    path(
        "teacher/groups/<int:group_id>/homework/add/",
        views.homework_create,
        name="homework_create",
    ),
    path(
        "teacher/groups/<int:group_id>/homework/<int:homework_id>/delete/",
        views.homework_delete,
        name="homework_delete",
    ),
    path(
        "teacher/groups/<int:group_id>/homework/<int:homework_id>/check/",
        views.homework_check,
        name="homework_check",
    ),
    path(
        "teacher/groups/<int:group_id>/schedule/",
        views.schedule_edit,
        name="schedule_edit",
    ),
    path(
        "teacher/groups/<int:group_id>/schedule/<int:item_id>/delete/",
        views.schedule_item_delete,
        name="schedule_item_delete",
    ),
    path(
        "teacher/groups/<int:group_id>/schedule/upload/",
        views.schedule_upload,
        name="schedule_upload",
    ),
    path(
        "teacher/groups/<int:group_id>/schedule/preview/",
        views.schedule_preview,
        name="schedule_preview",
    ),
]
