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

from .agent import run_agent
from .ai_services import (
    AIServiceError,
    ParsedSchedule,
    apply_parsed_schedule,
    excel_to_text,
    parse_schedule_image,
    parse_schedule_text,
)
from .forms import HomeworkForm, RegularLessonForm, ReplacementForm, ScheduleTextForm
from .models import Subject, Submission, User, Weekday, WeekType
from .utils import (
    get_day_schedule,
    get_week_number,
    get_week_schedule,
    get_week_type,
    group_debtors,
    semester_homeworks,
    student_homework_status,
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
    week = get_week_schedule(group, selected)
    return {
        "week": week,
        "is_current_week": any(day["is_today"] for day in week),
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
        lessons = _mark_current_lesson(get_day_schedule(group, today))
        context["today_lessons"] = lessons
        context["focus_lesson"], context["focus_kind"] = _focus_lesson(lessons)
        context["curators"] = group.teachers.all()
        context["classmates_count"] = group.students.count()
        context.update(student_homework_status(request.user))
    return render(request, "portal/student/dashboard.html", context)


def _focus_lesson(lessons):
    """Пара для главной карточки: идёт сейчас, следующая сегодня или ничего."""
    now = timezone.localtime().time()
    for item in lessons:
        if getattr(item, "is_now", False):
            return item, "now"
    for item in lessons:
        start, _ = item.times
        if start and start > now and not item.is_cancelled:
            return item, "next"
    return None, "done" if lessons else "free"


def about(request):
    """О проекте: группа, куратор, команда и вклад каждого. Открыта без входа — для жюри."""
    from . import team

    return render(request, "portal/about.html", {
        "team": team.TEAM,
        "group_name": team.GROUP_NAME,
        "curator": team.CURATOR,
        "hackathon": team.HACKATHON,
        "ai_model": settings.LLM_MODEL,
    })


def _chat_rate_limited(user_id: int) -> bool:
    """Не больше CHAT_RATE_LIMIT сообщений за CHAT_RATE_WINDOW секунд на пользователя."""
    key = f"chat-rate:{user_id}"
    if cache.add(key, 1, timeout=settings.CHAT_RATE_WINDOW):
        return False
    try:
        return cache.incr(key) > settings.CHAT_RATE_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=settings.CHAT_RATE_WINDOW)
        return False


@login_required
@require_POST
def agent_chat(request):
    """JSON-эндпоинт агента: {"question"} -> {"answer", "actions", "offline", "refused"}."""
    user = request.user
    if not (user.is_teacher or user.study_group_id):
        return JsonResponse({"error": "Агент доступен студентам группы и преподавателям."}, status=403)
    try:
        question = str(json.loads(request.body).get("question", "")).strip()
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Некорректный запрос."}, status=400)
    if not question:
        return JsonResponse({"error": "Напишите вопрос."}, status=400)
    if _chat_rate_limited(user.pk):
        return JsonResponse({"error": "Слишком много вопросов подряд. Подождите несколько минут."}, status=429)

    history = request.session.get("chat_history", [])
    reply = run_agent(user, question, history=history)
    if not reply.is_refusal and not reply.offline:
        history = history + [{"role": "user", "text": question}, {"role": "assistant", "text": reply.text}]
        request.session["chat_history"] = history[-2 * settings.CHAT_HISTORY_TURNS:]
    return JsonResponse({
        "answer": reply.text,
        "actions": reply.actions,
        "offline": reply.offline,
        "refused": reply.is_refusal,
    })


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
        group.debtors_count = len(group_debtors(group))
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
        or ReplacementForm(group=group, initial={"date": today, "teacher": request.user, "lesson_number": 1}),
        "homework_form": homework_form or HomeworkForm(),
        "homeworks": semester_homeworks(group)
        .annotate(done_count=Count("submissions", distinct=True))
        .order_by("-deadline"),
        "students_count": group.students.count(),
        "debtors": group_debtors(group),
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


@teacher_required
def homework_check(request, group_id, homework_id):
    """Отметка, кто из студентов сдал задание. Неотмеченные после срока — должники."""
    group = _teacher_group(request, group_id)
    homework = get_object_or_404(group.homeworks.select_related("subject"), pk=homework_id)
    students = list(group.students.order_by("last_name", "first_name", "username"))
    if request.method == "POST":
        chosen = {int(pk) for pk in request.POST.getlist("done") if pk.isdigit()}
        chosen &= {student.pk for student in students}
        homework.submissions.exclude(student_id__in=chosen).delete()
        existing = set(homework.submissions.values_list("student_id", flat=True))
        Submission.objects.bulk_create(
            Submission(homework=homework, student_id=pk) for pk in chosen - existing
        )
        messages.success(request, f"Сдачу задания «{homework.title}» отметили: {len(chosen)} из {len(students)}.")
        return redirect("teacher_group", group_id=group.pk)

    done = set(homework.submissions.values_list("student_id", flat=True))
    for student in students:
        student.is_done = student.pk in done
    context = {"group": group, "homework": homework, "students": students, "done_count": len(done)}
    return render(request, "portal/teacher/homework_check.html", context)


# --- Постоянное расписание вручную ---------------------------------------------


@teacher_required
def schedule_edit(request, group_id):
    group = _teacher_group(request, group_id)
    form = RegularLessonForm(request.POST or None, group=group)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        messages.success(
            request, f"{item.get_weekday_display()}, {item.lesson_number} пара: {item.subject}. Сохранено."
        )
        return redirect("schedule_edit", group_id=group.pk)

    items = group.schedule_items.filter(is_replacement=False).select_related("subject", "teacher")
    days = [
        {"weekday": label, "lessons": [item for item in items if item.weekday == number]}
        for number, label in Weekday.choices
        if number <= 6 or any(item.weekday == number for item in items)
    ]
    context = {"group": group, "form": form, "days": days, "subjects": Subject.objects.all()}
    return render(request, "portal/teacher/schedule_edit.html", context)


@teacher_required
@require_POST
def schedule_item_delete(request, group_id, item_id):
    group = _teacher_group(request, group_id)
    get_object_or_404(group.schedule_items, pk=item_id, is_replacement=False).delete()
    messages.success(request, "Пара удалена из постоянного расписания.")
    return redirect("schedule_edit", group_id=group.pk)


# --- Расписание по фото: загрузка -> проверка глазами -> запись в базу ---------


def _preview_key(group) -> str:
    return f"parsed_schedule_{group.pk}"


@teacher_required
def schedule_upload(request, group_id):
    group = _teacher_group(request, group_id)
    form = ScheduleTextForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data.get("file")
        try:
            if upload is not None and upload.kind == "image":
                parsed = parse_schedule_image(
                    upload.read(), upload.content_type or "image/jpeg", group_name=group.name
                )
            elif upload is not None:
                parsed = parse_schedule_text(excel_to_text(upload), group_name=group.name)
            else:
                parsed = parse_schedule_text(form.cleaned_data["text"], group_name=group.name)
        except AIServiceError as exc:
            form.add_error(None, str(exc))
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
