from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
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
    # Студент
    path("student/", views.student_dashboard, name="student_dashboard"),
    path("student/chat/", views.student_chat, name="student_chat"),
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
