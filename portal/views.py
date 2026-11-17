import json
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .ai_services import (
    AIServiceError,
    ParsedSchedule,
    apply_parsed_schedule,
    ask_college_bot,
    parse_schedule_image,
)
from .forms import HomeworkForm, ReplacementForm, ScheduleImageForm
from .models import Homework, User, Weekday, WeekType
from .utils import (
    build_student_context,
    get_day_schedule,
    get_week_number,
    get_week_schedule,
    get_week_type,
)

# --- Роли ----------------------------------------------------------------------


def role_required(role):
    """Пускает только вошедших пользователей с нужной ролью, остальных — на home."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.role != role:
                return redirect("home")
            return view(request, *args, **kwargs)

        return login_required(wrapper)

    return decorator


student_required = role_required(User.Role.STUDENT)
teacher_required = role_required(User.Role.TEACHER)


@login_required
def home(request):
    """Единая точка входа: после логина каждый попадает в свой кабинет."""
    user = request.user
    if user.is_teacher:
        return redirect("teacher_dashboard")
    if user.is_staff and user.study_group_id is None:
        return redirect("admin:index")  # администратор без группы
    return redirect("student_dashboard")


# --- Общие помощники -----------------------------------------------------------


def _selected_date(request) -> date:
    """Дата из ?date=ГГГГ-ММ-ДД для листания недель; по умолчанию сегодня."""
    try:
        return date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return timezone.localdate()


def _week_context(group, selected: date) -> dict:
    return {
        "week": get_week_schedule(group, selected),
        "week_type": WeekType(get_week_type(selected)),
        "week_number": get_week_number(selected),
        "prev_week": selected - timedelta(days=7),
        "next_week": selected + timedelta(days=7),
    }


def _mark_current_lesson(lessons):
    """Помечает пару, которая идёт прямо сейчас (для подсветки в расписании)."""
    now = timezone.localtime().time()
    for item in lessons:
        start, end = item.times
        item.is_now = bool(start and end and start <= now < end and not item.is_cancelled)
    return lessons


# --- Студент -------------------------------------------------------------------


@student_required
def student_dashboard(request):
    group = request.user.study_group
    today = timezone.localdate()
    context = {
        "group": group,
        "today": today,
        "today_weekday": Weekday(today.isoweekday()).label,
        "today_week_type": WeekType(get_week_type(today)),
        "today_week_number": get_week_number(today),
    }
    if group:
        context.update(_week_context(group, _selected_date(request)))
        context["today_lessons"] = _mark_current_lesson(get_day_schedule(group, today))
        context["homeworks"] = Homework.objects.filter(
            group=group, deadline__gte=timezone.now()
        ).select_related("subject", "teacher")
    return render(request, "portal/student/dashboard.html", context)


def _chat_rate_limited(user_id: int) -> bool:
    """Не больше CHAT_RATE_LIMIT сообщений за CHAT_RATE_WINDOW секунд на студента."""
    key = f"chat-rate:{user_id}"
    if cache.add(key, 1, timeout=settings.CHAT_RATE_WINDOW):
        return False
    try:
        return cache.incr(key) > settings.CHAT_RATE_LIMIT
    except ValueError:  # ключ истёк между add и incr
        cache.set(key, 1, timeout=settings.CHAT_RATE_WINDOW)
        return False


@student_required
@require_POST
def student_chat(request):
    """JSON-эндпоинт виджета: {"question": "..."} -> {"answer": "...", "refused": bool}."""
    try:
        question = str(json.loads(request.body).get("question", "")).strip()
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Некорректный запрос."}, status=400)
    if not question:
        return JsonResponse({"error": "Напишите вопрос."}, status=400)
    if _chat_rate_limited(request.user.pk):
        return JsonResponse(
            {"error": "Слишком много вопросов подряд. Подождите несколько минут."}, status=429
        )

    history = request.session.get("chat_history", [])
    try:
        answer = ask_college_bot(
            question,
            history=history,
            student_context=build_student_context(request.user),
        )
    except AIServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=503)

    if not answer.is_refusal:
        # Отказы в историю не кладём: они не нужны для контекста и не тратят токены.
        history = history + [
            {"role": "user", "text": question},
            {"role": "model", "text": answer.text},
        ]
        request.session["chat_history"] = history[-2 * settings.CHAT_HISTORY_TURNS :]
    return JsonResponse({"answer": answer.text, "refused": answer.is_refusal})


# --- Преподаватель -------------------------------------------------------------


def _teacher_group(request, group_id):
    """Группа, привязанная к преподавателю в админке. Чужая группа -> 404."""
    return get_object_or_404(request.user.teaching_groups, pk=group_id)


@teacher_required
def teacher_dashboard(request):
    today = timezone.localdate()
    groups = request.user.teaching_groups.annotate(
        students_count=Count("students", distinct=True),
        homework_count=Count(
            "homeworks", filter=Q(homeworks__deadline__gte=timezone.now()), distinct=True
        ),
    )
    for group in groups:
        group.today_lessons = get_day_schedule(group, today)
    context = {
        "groups": groups,
        "today": today,
        "today_weekday": Weekday(today.isoweekday()).label,
        "today_week_type": WeekType(get_week_type(today)),
        "today_week_number": get_week_number(today),
    }
    return render(request, "portal/teacher/dashboard.html", context)


def _render_group(request, group, *, replacement_form=None, homework_form=None):
    today = timezone.localdate()
    context = {
        "group": group,
        "today": today,
        "replacement_form": replacement_form
        or ReplacementForm(group=group, initial={"date": today, "teacher": request.user}),
        "homework_form": homework_form or HomeworkForm(),
        "homeworks": group.homeworks.filter(deadline__gte=timezone.now()).select_related("subject"),
        "replacements": group.schedule_items.filter(
            is_replacement=True, date__gte=today
        ).select_related("subject").order_by("date", "lesson_number"),
        **_week_context(group, _selected_date(request)),
    }
    return render(request, "portal/teacher/group.html", context)


@teacher_required
def teacher_group(request, group_id):
    return _render_group(request, _teacher_group(request, group_id))


@teacher_required
@require_POST
def replacement_create(request, group_id):
    group = _teacher_group(request, group_id)
    form = ReplacementForm(request.POST, group=group)
    if not form.is_valid():
        return _render_group(request, group, replacement_form=form)
    item = form.save()
    action = "отменена" if item.is_cancelled else "заменена"
    messages.success(request, f"{item.lesson_number} пара {item.date:%d.%m} {action}. Студенты уже видят это.")
    response = redirect("teacher_group", group_id=group.pk)
    response["Location"] += f"?date={item.date.isoformat()}"
    return response


@teacher_required
@require_POST
def replacement_delete(request, group_id, item_id):
    group = _teacher_group(request, group_id)
    item = get_object_or_404(group.schedule_items, pk=item_id, is_replacement=True)
    item.delete()
    messages.success(request, "Замена убрана, пара идёт по обычному расписанию.")
    return redirect("teacher_group", group_id=group.pk)


@teacher_required
@require_POST
def homework_create(request, group_id):
    group = _teacher_group(request, group_id)
    form = HomeworkForm(request.POST)
    if not form.is_valid():
        return _render_group(request, group, homework_form=form)
    homework = form.save(commit=False)
    homework.group = group
    homework.teacher = request.user
    homework.save()
    messages.success(request, "Задание добавлено.")
    return redirect("teacher_group", group_id=group.pk)


@teacher_required
@require_POST
def homework_delete(request, group_id, homework_id):
    group = _teacher_group(request, group_id)
    get_object_or_404(group.homeworks, pk=homework_id).delete()
    messages.success(request, "Задание удалено.")
    return redirect("teacher_group", group_id=group.pk)


# --- Расписание по фото: загрузка -> проверка глазами -> запись в базу ---------


def _preview_key(group) -> str:
    return f"parsed_schedule_{group.pk}"


@teacher_required
def schedule_upload(request, group_id):
    group = _teacher_group(request, group_id)
    form = ScheduleImageForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        image = form.cleaned_data["image"]
        try:
            parsed = parse_schedule_image(image.read(), image.content_type, group_name=group.name)
        except AIServiceError as exc:
            form.add_error("image", str(exc))
        else:
            # До подтверждения результат лежит в сессии, база не меняется.
            request.session[_preview_key(group)] = parsed.model_dump()
            return redirect("schedule_preview", group_id=group.pk)
    return render(request, "portal/teacher/schedule_upload.html", {"group": group, "form": form})


@teacher_required
def schedule_preview(request, group_id):
    group = _teacher_group(request, group_id)
    data = request.session.get(_preview_key(group))
    if not data:
        return redirect("schedule_upload", group_id=group.pk)
    parsed = ParsedSchedule.model_validate(data)

    if request.method == "POST":
        count = apply_parsed_schedule(group, parsed)
        del request.session[_preview_key(group)]
        messages.success(request, f"Расписание группы {group} обновлено, пар в нём: {count}.")
        return redirect("teacher_group", group_id=group.pk)

    lessons = sorted(parsed.lessons, key=lambda l: (l.weekday, l.lesson_number, l.week_type))
    days = [
        {"weekday": Weekday(number).label, "lessons": [l for l in lessons if l.weekday == number]}
        for number in sorted({l.weekday for l in lessons})
    ]
    context = {
        "group": group,
        "parsed": parsed,
        "days": days,
        "current_count": group.schedule_items.filter(is_replacement=False).count(),
    }
    return render(request, "portal/teacher/schedule_preview.html", context)
