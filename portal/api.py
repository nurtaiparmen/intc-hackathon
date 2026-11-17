"""JSON API для мобильного приложения (React / React Native).

Вход по токену, без cookies и CSRF:
    POST /api/login/  {"username", "password"}  ->  {"token", "user"}
    дальше в каждом запросе заголовок  Authorization: Token <token>

Токен — подписанный id пользователя (django.core.signing), живёт 30 дней.
Отдельная таблица не нужна; смена SECRET_KEY делает все токены недействительными.
Описание всех адресов — в API.md.
"""

import json
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate
from django.core import signing
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .agent import run_agent
from .models import User, Weekday, WeekType
from .utils import (
    get_schedule,
    get_week_number,
    get_week_type,
    group_debtors,
    semester_homeworks,
    student_homework_status,
)

TOKEN_SALT = "smart-college-api"
TOKEN_MAX_AGE = 30 * 24 * 3600  # 30 дней


# --- Служебное ------------------------------------------------------------------


def _json(data, status=200):
    response = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})
    # Для React в браузере (Expo Web и т. п.). Токен в заголовке — cookies не нужны.
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def _error(message, status):
    return _json({"error": message}, status)


def make_token(user) -> str:
    return signing.dumps({"uid": user.pk}, salt=TOKEN_SALT)


def _user_from_token(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Token "):
        return None
    try:
        data = signing.loads(header[6:].strip(), salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data.get("uid"), is_active=True).select_related("study_group").first()


def api_view(methods=("GET",), auth=True):
    """CORS, проверка метода и токена. request.user подменяется пользователем из токена."""

    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == "OPTIONS":
                return _json({})
            if request.method not in methods:
                return _error("Метод не поддерживается.", 405)
            if auth:
                user = _user_from_token(request)
                if user is None:
                    return _error("Нужен вход: заголовок Authorization: Token <токен>.", 401)
                request.user = user
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _date_param(request) -> date:
    try:
        return date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return timezone.localdate()


def _group_for(request):
    """Студент — своя группа; преподаватель — ?group=<id> или первая из его групп."""
    user = request.user
    if user.is_teacher:
        groups = user.teaching_groups.all()
        group_id = request.GET.get("group")
        if group_id:
            return groups.filter(pk=group_id).first()
        return groups.first()
    return user.study_group


def _side(week_type) -> str:
    return "left" if week_type == WeekType.NUMERATOR else "right"


def _user_info(user) -> dict:
    info = {
        "id": user.pk,
        "username": user.username,
        "name": str(user),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
    }
    if user.is_teacher:
        info["groups"] = [{"id": g.pk, "name": g.name, "course": g.course} for g in user.teaching_groups.all()]
    elif user.study_group:
        g = user.study_group
        info["group"] = {
            "id": g.pk, "name": g.name, "course": g.course,
            "curators": [str(t) for t in g.teachers.all()],
        }
    return info


def _lesson(item) -> dict:
    start, end = item.times
    data = {
        "number": item.lesson_number,
        "subject": item.subject.name,
        "start": f"{start:%H:%M}" if start else None,
        "end": f"{end:%H:%M}" if end else None,
        "room": item.room or None,
        "teacher": str(item.teacher) if item.teacher else None,
        "status": "cancelled" if item.is_cancelled else "replacement" if item.is_replacement else "regular",
        "note": item.note or None,
    }
    if item.is_replacement and getattr(item, "original", None):
        data["instead_of"] = item.original.subject.name
    return data


def _homework(hw, done=None) -> dict:
    data = {
        "id": hw.pk,
        "subject": hw.subject.name,
        "title": hw.title,
        "description": hw.description,
        "deadline": timezone.localtime(hw.deadline).isoformat(),
        "teacher": str(hw.teacher) if hw.teacher else None,
    }
    if done is not None:
        data["submitted"] = done
    return data


def _days(group, start: date, count: int) -> list[dict]:
    dates = [start + timedelta(days=i) for i in range(count)]
    schedule = get_schedule(group, dates)
    today = timezone.localdate()
    return [
        {
            "date": day.isoformat(),
            "weekday": Weekday(day.isoweekday()).label,
            "is_today": day == today,
            "week": _side(get_week_type(day)),
            "lessons": [_lesson(item) for item in schedule[day]],
        }
        for day in dates
    ]


# --- Адреса ---------------------------------------------------------------------


@api_view(methods=("GET",), auth=False)
def index(request):
    return _json({
        "name": "Smart College API",
        "college": settings.COLLEGE_NAME,
        "docs": "см. API.md в репозитории",
        "endpoints": [
            "POST /api/login/", "GET /api/me/", "GET /api/today/", "GET /api/schedule/",
            "GET /api/week/", "GET /api/homework/", "GET /api/groups/", "POST /api/chat/",
        ],
    })


@api_view(methods=("POST",), auth=False)
def login(request):
    data = _body(request)
    user = authenticate(request, username=str(data.get("username", "")), password=str(data.get("password", "")))
    if user is None:
        return _error("Неверный логин или пароль.", 400)
    return _json({"token": make_token(user), "expires_in_days": TOKEN_MAX_AGE // 86400, "user": _user_info(user)})


@api_view()
def me(request):
    return _json(_user_info(request.user))


@api_view()
def today(request):
    """Всё для главного экрана: сегодняшние пары, тип недели, счётчики."""
    group = _group_for(request)
    day = timezone.localdate()
    data = {
        "date": day.isoformat(),
        "weekday": Weekday(day.isoweekday()).label,
        "week_number": get_week_number(day),
        "week": _side(get_week_type(day)),
        "group": group.name if group else None,
        "lessons": _days(group, day, 1)[0]["lessons"] if group else [],
    }
    if request.user.is_student and group:
        status = student_homework_status(request.user)
        data["debts_count"] = len(status["debts"])
        data["upcoming_count"] = status["upcoming_count"]
    return _json(data)


@api_view()
def schedule(request):
    """?date=ГГГГ-ММ-ДД (по умолчанию сегодня) &days=1..14 &group=<id> (для преподавателя)."""
    group = _group_for(request)
    if group is None:
        return _error("Группа не найдена.", 404)
    try:
        count = max(1, min(int(request.GET.get("days", 1)), 14))
    except ValueError:
        count = 1
    return _json({"group": group.name, "days": _days(group, _date_param(request), count)})


@api_view()
def week(request):
    """Неделя (пн–сб, воскресенье — если есть пары), в которую попадает ?date=."""
    group = _group_for(request)
    if group is None:
        return _error("Группа не найдена.", 404)
    any_day = _date_param(request)
    monday = any_day - timedelta(days=any_day.weekday())
    days = [d for d in _days(group, monday, 7) if d["weekday"] != Weekday.SUNDAY.label or d["lessons"]]
    return _json({
        "group": group.name,
        "week_number": get_week_number(monday),
        "week": _side(get_week_type(monday)),
        "days": days,
    })


@api_view()
def homework(request):
    user = request.user
    if user.is_student:
        status = student_homework_status(user)
        return _json({
            "upcoming": [_homework(h, h.is_done) for h in status["upcoming"]],
            "debts": [_homework(h, False) for h in status["debts"]],
        })
    group = _group_for(request)
    if group is None:
        return _error("Группа не найдена.", 404)
    total = group.students.count()
    now = timezone.now()
    return _json({
        "group": group.name,
        "homework": [
            {**_homework(h), "overdue": h.deadline < now, "submitted_count": h.submissions.count(), "students": total}
            for h in semester_homeworks(group).order_by("deadline")
        ],
        "debtors": [
            {"student": str(e["student"]), "debts": [_homework(h) for h in e["debts"]]}
            for e in group_debtors(group)
        ],
    })


@api_view()
def groups(request):
    if not request.user.is_teacher:
        return _error("Только для преподавателя.", 403)
    return _json({"groups": [
        {"id": g.pk, "name": g.name, "course": g.course, "students": g.students.count()}
        for g in request.user.teaching_groups.all()
    ]})


@api_view(methods=("POST",))
def chat(request):
    """{"question": "...", "history": [{"role": "user"|"assistant", "text": "..."}]} -> ответ агента.

    История хранится в приложении и присылается с каждым вопросом (последние 6 сообщений).
    """
    user = request.user
    if not (user.is_teacher or user.study_group_id):
        return _error("Агент доступен студентам группы и преподавателям.", 403)
    data = _body(request)
    question = str(data.get("question", "")).strip()
    if not question:
        return _error("Напишите вопрос.", 400)
    from .views import _chat_rate_limited  # общий лимит с сайтом

    if _chat_rate_limited(user.pk):
        return _error("Слишком много вопросов подряд. Подождите несколько минут.", 429)
    history = [
        {"role": str(t.get("role", "")), "text": str(t.get("text", ""))[:2000]}
        for t in (data.get("history") or [])[-2 * settings.CHAT_HISTORY_TURNS:]
        if isinstance(t, dict)
    ]
    reply = run_agent(user, question, history=history)
    return _json({
        "answer": reply.text,
        "actions": reply.actions,
        "offline": reply.offline,
        "refused": reply.is_refusal,
    })
