"""ИИ-агент портала: понимает вопрос, сам вызывает функции сайта и отвечает.

Студенту доступно чтение: пары, тип недели, задания, долги.
Преподавателю ещё и действия: замены, отмены, задания, отметки о сдаче,
постоянное расписание — только в своих группах.

Если модель не подключена или не отвечает, работает запасной режим
offline_answer(): простые вопросы разбираются по ключевым словам.
"""

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import llm
from .forms import HomeworkForm, RegularLessonForm, ReplacementForm
from .models import Homework, Subject, Submission, User, Weekday, WeekType
from .utils import (
    get_schedule,
    get_week_number,
    get_week_type,
    group_debtors,
    semester_homeworks,
    student_homework_status,
)

logger = logging.getLogger(__name__)

REFUSAL_TEXT = "Я отвечаю только на вопросы, связанные с нашим колледжем."

WEEKDAY_WORDS = {
    "понедельник": 1, "вторник": 2, "сред": 3, "четверг": 4, "пятниц": 5, "суббот": 6, "воскресень": 7,
}
WEEK_TYPES = {
    "every": WeekType.EVERY, "каждую": WeekType.EVERY, "обе": WeekType.EVERY,
    "left": WeekType.NUMERATOR, "левая": WeekType.NUMERATOR, "numerator": WeekType.NUMERATOR,
    "числитель": WeekType.NUMERATOR,
    "right": WeekType.DENOMINATOR, "правая": WeekType.DENOMINATOR, "denominator": WeekType.DENOMINATOR,
    "знаменатель": WeekType.DENOMINATOR,
}


class ToolError(Exception):
    """Понятная ошибка инструмента: уходит модели, а она объясняет её человеку."""


@dataclass
class AgentReply:
    text: str
    actions: list[str] = field(default_factory=list)
    offline: bool = False
    is_refusal: bool = False


# --- Разбор аргументов ---------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_.«»\"']", "", text or "").casefold()


def _side(week_type) -> str:
    return "левая (числитель)" if week_type == WeekType.NUMERATOR else "правая (знаменатель)"


def parse_day(value) -> date:
    """'2026-11-18', '18.11', 'завтра', 'пятница' -> дата."""
    today = timezone.localdate()
    text = str(value or "").strip().casefold()
    if not text or text in {"today", "сегодня"}:
        return today
    if text in {"tomorrow", "завтра"}:
        return today + timedelta(days=1)
    if text in {"послезавтра", "day after tomorrow"}:
        return today + timedelta(days=2)
    if text in {"вчера", "yesterday"}:
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text)
    if match:
        day, month, year = int(match[1]), int(match[2]), match[3]
        year = int(year) + (2000 if year and len(year) == 2 else 0) if year else today.year
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise ToolError(f"Такой даты нет: {value}") from exc
    for word, number in WEEKDAY_WORDS.items():
        if word in text:
            return today + timedelta(days=(number - today.isoweekday()) % 7)
    raise ToolError(f"Не понял дату «{value}». Нужен формат ГГГГ-ММ-ДД.")


def parse_deadline(value) -> datetime:
    text = str(value or "").strip().replace("T", " ")
    match = re.search(r"(\d{1,2}):(\d{2})\s*$", text)
    hours, minutes = (int(match[1]), int(match[2])) if match else (18, 0)
    day = parse_day(text[: match.start()].strip() if match else text)
    return timezone.make_aware(datetime.combine(day, datetime.min.time()).replace(hour=hours, minute=minutes))


def parse_week_type(value) -> str:
    key = str(value or "every").strip().casefold()
    if key not in WEEK_TYPES:
        raise ToolError("Неделя должна быть: every (каждую), left (левая) или right (правая).")
    return WEEK_TYPES[key]


def _form_error(form) -> str:
    return " ".join(str(error) for errors in form.errors.values() for error in errors)


def _best_match(query: str, options: dict, what: str):
    """Ищет по точному совпадению, по вхождению, затем похожее написание."""
    key = _norm(query)
    if not key:
        raise ToolError(f"Не указан {what}.")
    if key in options:
        return options[key]
    if len(key) >= 4:
        partial = [value for name, value in options.items() if key in name or name.startswith(key)]
        if len(partial) == 1:
            return partial[0]
    close = difflib.get_close_matches(key, options.keys(), n=1, cutoff=0.7)
    return options[close[0]] if close else None


# --- Контекст одного запроса ---------------------------------------------------


class AgentContext:
    def __init__(self, user: User):
        self.user = user
        self.actions: list[str] = []

    @property
    def is_teacher(self) -> bool:
        return self.user.is_teacher

    def group(self, name=None):
        if not self.is_teacher:
            if self.user.study_group is None:
                raise ToolError("Студент не привязан к группе.")
            return self.user.study_group
        groups = list(self.user.teaching_groups.all())
        if not groups:
            raise ToolError("За преподавателем не закреплено ни одной группы.")
        if not name:
            if len(groups) == 1:
                return groups[0]
            raise ToolError("Уточните группу: " + ", ".join(g.name for g in groups))
        found = _best_match(name, {_norm(g.name): g for g in groups}, "группа")
        if found is None:
            raise ToolError(f"Группа «{name}» не закреплена за вами. Ваши группы: " + ", ".join(g.name for g in groups))
        return found

    def subject(self, name: str) -> Subject:
        found = _best_match(name, {_norm(s.name): s for s in Subject.objects.all()}, "предмет")
        return found or Subject.objects.create(name=" ".join(str(name).split())[:150])

    def teacher(self, name):
        if not name:
            return None
        if _norm(name) in {"я", "me", "мне", "меня", _norm(self.user.last_name)}:
            return self.user
        teachers = User.objects.filter(role=User.Role.TEACHER)
        options = {}
        for teacher in teachers:
            options.setdefault(_norm(teacher.last_name), teacher)
            options.setdefault(_norm(teacher.get_full_name()), teacher)
        found = _best_match(name, {k: v for k, v in options.items() if k}, "преподаватель")
        if found is None:
            raise ToolError(f"Преподаватель «{name}» не найден на портале.")
        return found

    def homework(self, group, value) -> Homework:
        items = list(semester_homeworks(group))
        if str(value).isdigit():
            for item in items:
                if item.pk == int(value):
                    return item
        found = _best_match(str(value), {_norm(h.title): h for h in items}, "задание")
        if found is None:
            raise ToolError("Задание не найдено. Сначала посмотрите список: get_group_homework.")
        return found

    def run(self, name: str, raw_arguments: str) -> dict:
        tool = TOOLS_BY_NAME.get(name)
        if tool is None or (tool["teacher_only"] and not self.is_teacher):
            return {"error": f"Инструмент {name} недоступен."}
        try:
            arguments = json.loads(raw_arguments or "{}") or {}
        except ValueError:
            return {"error": "Аргументы должны быть JSON-объектом."}
        try:
            with transaction.atomic():
                return tool["func"](self, **arguments)
        except ToolError as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            logger.info("Неверные аргументы %s: %s", name, exc)
            return {"error": f"Неверные параметры для {name}."}
        except Exception:
            logger.exception("Инструмент %s упал", name)
            return {"error": "Внутренняя ошибка сайта при выполнении действия."}


# --- Инструменты: чтение -------------------------------------------------------


def _lesson_info(item) -> dict:
    start, end = item.times
    info = {"pair": item.lesson_number, "subject": item.subject.name}
    if start and end:
        info["time"] = f"{start:%H:%M}-{end:%H:%M}"
    if item.room:
        info["room"] = item.room
    if item.teacher:
        info["teacher"] = str(item.teacher)
    if item.is_cancelled:
        info["status"] = "ОТМЕНЕНА"
    elif item.is_replacement:
        info["status"] = "ЗАМЕНА"
        if getattr(item, "original", None):
            info["instead_of"] = item.original.subject.name
    if item.note:
        info["note"] = item.note
    return info


def tool_get_schedule(ctx, date=None, days=1, group=None):
    start = parse_day(date)
    days = max(1, min(int(days or 1), 7))
    target = ctx.group(group)
    dates = [start + timedelta(days=i) for i in range(days)]
    schedule = get_schedule(target, dates)
    return {
        "group": target.name,
        "days": [
            {
                "date": day.isoformat(),
                "weekday": Weekday(day.isoweekday()).label,
                "week": _side(get_week_type(day)),
                "lessons": [_lesson_info(item) for item in schedule[day]] or "пар нет",
            }
            for day in dates
        ],
    }


def tool_get_week(ctx, date=None):
    day = parse_day(date)
    return {"date": day.isoformat(), "week_number": get_week_number(day), "week": _side(get_week_type(day))}


def _homework_info(homework) -> dict:
    return {
        "id": homework.pk,
        "subject": homework.subject.name,
        "title": homework.title,
        "deadline": f"{timezone.localtime(homework.deadline):%Y-%m-%d %H:%M}",
    }


def tool_get_my_homework(ctx):
    status = student_homework_status(ctx.user)
    return {
        "upcoming": [{**_homework_info(h), "submitted": h.is_done} for h in status["upcoming"]] or "заданий нет",
        "debts_count": len(status["debts"]),
    }


def tool_get_my_debts(ctx):
    debts = student_homework_status(ctx.user)["debts"]
    return {"debts": [_homework_info(h) for h in debts] or "долгов нет"}


def tool_list_my_groups(ctx):
    return {"groups": [{"name": g.name, "students": g.students.count()} for g in ctx.user.teaching_groups.all()]}


def tool_get_group_homework(ctx, group=None):
    target = ctx.group(group)
    total = target.students.count()
    now = timezone.now()
    return {
        "group": target.name,
        "homework": [
            {**_homework_info(h), "overdue": h.deadline < now, "submitted": f"{h.submissions.count()} из {total}"}
            for h in semester_homeworks(target).order_by("deadline")
        ] or "заданий нет",
    }


def tool_get_debtors(ctx, group=None):
    target = ctx.group(group)
    return {
        "group": target.name,
        "debtors": [
            {"student": str(entry["student"]), "debts": [f"{h.subject.name}: {h.title}" for h in entry["debts"]]}
            for entry in group_debtors(target)
        ] or "должников нет",
    }


# --- Инструменты: действия преподавателя ---------------------------------------


def _replacement(ctx, group, date, lesson_number, *, cancel, subject="", room="", teacher="", note=""):
    target = ctx.group(group)
    day = parse_day(date)
    data = {
        "date": day.isoformat(),
        "lesson_number": lesson_number,
        "is_cancelled": cancel,
        "room": room or "",
        "note": (note or "")[:200],
    }
    if not cancel:
        data["subject"] = ctx.subject(subject).pk
        found = ctx.teacher(teacher)
        data["teacher"] = found.pk if found else ""
    form = ReplacementForm(data=data, group=target)
    if not form.is_valid():
        raise ToolError(_form_error(form))
    item = form.save()
    when = f"{target.name}, {day:%d.%m} ({Weekday(day.isoweekday()).label.lower()}), {item.lesson_number} пара"
    if cancel:
        ctx.actions.append(f"Отменена пара: {when} — {item.subject}")
    else:
        details = f"{item.subject}" + (f", ауд. {item.room}" if item.room else "")
        ctx.actions.append(f"Замена: {when} → {details}")
    return {"ok": True, "saved": _lesson_info(item), "date": day.isoformat()}


def tool_make_replacement(ctx, date, lesson_number, subject, group=None, room="", teacher="", note=""):
    return _replacement(ctx, group, date, lesson_number, cancel=False, subject=subject, room=room,
                        teacher=teacher, note=note)


def tool_cancel_lesson(ctx, date, lesson_number, group=None, note=""):
    return _replacement(ctx, group, date, lesson_number, cancel=True, note=note)


def tool_remove_replacement(ctx, date, lesson_number, group=None):
    target = ctx.group(group)
    day = parse_day(date)
    deleted, _ = target.schedule_items.filter(is_replacement=True, date=day, lesson_number=lesson_number).delete()
    if not deleted:
        raise ToolError(f"На {day:%d.%m} у {lesson_number} пары нет замены или отмены.")
    ctx.actions.append(f"Убрана замена: {target.name}, {day:%d.%m}, {lesson_number} пара — снова по расписанию")
    return {"ok": True}


def tool_add_homework(ctx, subject, title, deadline, group=None, description=""):
    target = ctx.group(group)
    when = parse_deadline(deadline)
    form = HomeworkForm(data={
        "subject": ctx.subject(subject).pk,
        "title": title,
        "description": description or "",
        "deadline": f"{timezone.localtime(when):%Y-%m-%d %H:%M}",
    })
    if not form.is_valid():
        raise ToolError(_form_error(form))
    homework = form.save(commit=False)
    homework.group, homework.teacher = target, ctx.user
    homework.save()
    ctx.actions.append(
        f"Задание для {target.name}: {homework.subject} — «{homework.title}», до {timezone.localtime(homework.deadline):%d.%m %H:%M}"
    )
    return {"ok": True, "homework": _homework_info(homework)}


def tool_mark_submissions(ctx, homework, students, group=None, submitted=True):
    target = ctx.group(group)
    item = ctx.homework(target, homework)
    roster = list(target.students.all())
    names = students if isinstance(students, list) else [students]
    if any(_norm(n) in {"все", "всех", "all", "everyone"} for n in names):
        chosen = roster
    else:
        options = {}
        for student in roster:
            for key in (student.last_name, student.get_full_name(), f"{student.last_name} {student.first_name}",
                        student.username):
                if key:
                    options.setdefault(_norm(key), student)
        chosen, missing = [], []
        for name in names:
            found = _best_match(name, options, "студент")
            (chosen.append(found) if found else missing.append(name))
        if missing:
            raise ToolError("Не нашёл в группе: " + ", ".join(missing))
    if submitted:
        Submission.objects.bulk_create(
            [Submission(homework=item, student=s) for s in chosen], ignore_conflicts=True
        )
    else:
        item.submissions.filter(student__in=chosen).delete()
    who = ", ".join(str(s) for s in chosen)
    verb = "сдали" if submitted else "сняли отметку о сдаче"
    ctx.actions.append(f"«{item.title}»: {verb} — {who}")
    return {"ok": True, "homework": item.title, "students": [str(s) for s in chosen],
            "submitted_now": f"{item.submissions.count()} из {len(roster)}"}


def tool_set_regular_lesson(ctx, weekday, lesson_number, subject, group=None, week="every", room="", teacher=""):
    target = ctx.group(group)
    found = ctx.teacher(teacher)
    form = RegularLessonForm(data={
        "weekday": weekday,
        "lesson_number": lesson_number,
        "week_type": parse_week_type(week),
        "subject": ctx.subject(subject).name,
        "room": room or "",
        "teacher": found.pk if found else "",
    }, group=target)
    if not form.is_valid():
        raise ToolError(_form_error(form))
    item = form.save()
    week_label = {"every": "каждую неделю", "numerator": "левая неделя", "denominator": "правая неделя"}[item.week_type]
    ctx.actions.append(
        f"Постоянное расписание {target.name}: {item.get_weekday_display()}, {item.lesson_number} пара ({week_label}) — {item.subject}"
    )
    return {"ok": True, "saved": _lesson_info(item)}


def tool_delete_regular_lesson(ctx, weekday, lesson_number, group=None, week="every"):
    target = ctx.group(group)
    deleted, _ = target.schedule_items.filter(
        is_replacement=False, weekday=weekday, lesson_number=lesson_number, week_type=parse_week_type(week)
    ).delete()
    if not deleted:
        raise ToolError("Такой пары в постоянном расписании нет.")
    ctx.actions.append(f"Удалена из постоянного расписания {target.name}: {Weekday(int(weekday)).label}, {lesson_number} пара")
    return {"ok": True}


# --- Описания инструментов для модели ------------------------------------------

_GROUP = {"type": "string", "description": "Название группы. Можно не указывать, если у преподавателя одна группа"}
_DATE = {"type": "string", "description": "Дата ГГГГ-ММ-ДД"}
_PAIR = {"type": "integer", "minimum": 1, "maximum": 6, "description": "Номер пары"}
_WEEK = {"type": "string", "enum": ["every", "left", "right"],
         "description": "every — каждую неделю, left — только левая (числитель), right — только правая (знаменатель)"}
_WEEKDAY = {"type": "integer", "minimum": 1, "maximum": 6, "description": "1 — понедельник … 6 — суббота"}


def _tool(func, description, properties=None, required=(), teacher_only=False):
    return {
        "func": func,
        "teacher_only": teacher_only,
        "schema": {
            "type": "function",
            "function": {
                "name": func.__name__.removeprefix("tool_"),
                "description": description,
                "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
            },
        },
    }


TOOLS = [
    _tool(tool_get_schedule, "Пары группы на дату (с заменами и отменами). days — сколько дней подряд, до 7.",
          {"date": _DATE, "days": {"type": "integer", "minimum": 1, "maximum": 7}, "group": _GROUP}),
    _tool(tool_get_week, "Номер учебной недели и какая она: левая или правая.", {"date": _DATE}),
    _tool(tool_get_my_homework, "Предстоящие задания студента и что уже сдано."),
    _tool(tool_get_my_debts, "Долги студента: просроченные задания без отметки о сдаче."),
    _tool(tool_list_my_groups, "Группы преподавателя.", teacher_only=True),
    _tool(tool_get_group_homework, "Задания группы за семестр: id, срок, сколько студентов сдали.",
          {"group": _GROUP}, teacher_only=True),
    _tool(tool_get_debtors, "Должники группы и их долги.", {"group": _GROUP}, teacher_only=True),
    _tool(tool_make_replacement, "Заменить пару на конкретную дату. Студенты увидят сразу.",
          {"date": _DATE, "lesson_number": _PAIR, "subject": {"type": "string"}, "group": _GROUP,
           "room": {"type": "string"},
           "teacher": {"type": "string", "description": "Фамилия того, кто ведёт замену"},
           "note": {"type": "string", "description": "Комментарий для студентов"}},
          ["date", "lesson_number", "subject"], teacher_only=True),
    _tool(tool_cancel_lesson, "Отменить пару на конкретную дату.",
          {"date": _DATE, "lesson_number": _PAIR, "group": _GROUP, "note": {"type": "string"}},
          ["date", "lesson_number"], teacher_only=True),
    _tool(tool_remove_replacement, "Убрать замену или отмену: пара снова идёт по расписанию.",
          {"date": _DATE, "lesson_number": _PAIR, "group": _GROUP}, ["date", "lesson_number"], teacher_only=True),
    _tool(tool_add_homework, "Задать домашнее задание группе.",
          {"subject": {"type": "string"}, "title": {"type": "string"},
           "deadline": {"type": "string", "description": "ГГГГ-ММ-ДД ЧЧ:ММ; без времени — 18:00"},
           "group": _GROUP, "description": {"type": "string"}},
          ["subject", "title", "deadline"], teacher_only=True),
    _tool(tool_mark_submissions, "Отметить, кто сдал задание (или снять отметку).",
          {"homework": {"type": "string", "description": "id или название задания"},
           "students": {"type": "array", "items": {"type": "string"},
                        "description": "Фамилии или имена; [\"все\"] — вся группа"},
           "group": _GROUP, "submitted": {"type": "boolean", "description": "false — снять отметку"}},
          ["homework", "students"], teacher_only=True),
    _tool(tool_set_regular_lesson, "Добавить или изменить пару в постоянном расписании (на весь семестр).",
          {"weekday": _WEEKDAY, "lesson_number": _PAIR, "subject": {"type": "string"}, "group": _GROUP,
           "week": _WEEK, "room": {"type": "string"}, "teacher": {"type": "string"}},
          ["weekday", "lesson_number", "subject"], teacher_only=True),
    _tool(tool_delete_regular_lesson, "Удалить пару из постоянного расписания.",
          {"weekday": _WEEKDAY, "lesson_number": _PAIR, "group": _GROUP, "week": _WEEK},
          ["weekday", "lesson_number"], teacher_only=True),
]
TOOLS_BY_NAME = {tool["schema"]["function"]["name"]: tool for tool in TOOLS}
STUDENT_ONLY = {"get_my_homework", "get_my_debts"}


def tools_for(user) -> list[dict]:
    return [
        tool["schema"] for name, tool in TOOLS_BY_NAME.items()
        if (user.is_teacher or not tool["teacher_only"]) and not (user.is_teacher and name in STUDENT_ONLY)
    ]


# --- Промпт ---------------------------------------------------------------------

PROMPT = """\
Ты — ИИ-агент портала колледжа «{college}». С тобой говорит {who}.
Сегодня {today}. Идёт {week_number}-я учебная неделя, сейчас {side} неделя.
Ближайшие дни (для перевода «завтра», «в пятницу» в даты):
{calendar}

КАК РАБОТАТЬ
1. Пары, замены, задания, долги и тип недели узнавай ТОЛЬКО через инструменты. \
Не выдумывай пары, кабинеты, даты и имена.
2. В инструменты передавай даты в формате ГГГГ-ММ-ДД.
3. Левая неделя — это числитель, правая — знаменатель. Студенты говорят по-разному, понимай оба варианта.
4. Правила колледжа (сессия, пересдачи, справки, контакты, звонки) бери из БАЗЫ ЗНАНИЙ. \
Если там нет ответа — скажи: «В моей базе знаний этого нет. Уточните у куратора или в учебной части.»
{role_rules}
ОБЩЕНИЕ
- Понимай сленг и шутки («чё по парам завтра», «распиши по-братски», «оцени сложность дня»). \
Если вопрос про учёбу и колледж — отвечай по делу и в нужном формате.
- Отказывай фразой «{refusal}» только если вопрос никак не связан с колледжем, учёбой и данными портала \
(писать код, решать домашку за студента, анекдоты, политика).
- Отвечай на языке вопроса, коротко: 1–6 предложений или список. Без markdown-таблиц и без звёздочек.

БАЗА ЗНАНИЙ
<knowledge>
{knowledge}
</knowledge>
"""

STUDENT_RULES = """\
5. Ты говоришь со студентом. Менять расписание и задания он не может: если просит — \
объясни, что это делает преподаватель.
"""

TEACHER_RULES = """\
5. Ты говоришь с преподавателем и можешь менять данные: замены, отмены, задания, \
отметки о сдаче, постоянное расписание. Если в запросе есть всё нужное (группа, дата, \
номер пары, предмет) — выполняй сразу. Если чего-то не хватает или запрос двусмысленный — \
задай один короткий вопрос и ничего не меняй.
6. «Я проведу», «поставь меня» — значит teacher = «{last_name}».
7. Меняй ровно то, что просили. После действия коротко подтверди, что изменено. \
Если инструмент вернул error — объясни ошибку простыми словами.
"""


def load_college_knowledge() -> str:
    try:
        return settings.COLLEGE_KNOWLEDGE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "(база знаний пуста)"


def build_prompt(user) -> str:
    today = timezone.localdate()
    calendar = []
    for offset in range(8):
        day = today + timedelta(days=offset)
        mark = {0: " — сегодня", 1: " — завтра", 2: " — послезавтра"}.get(offset, "")
        calendar.append(f"- {day.isoformat()}, {Weekday(day.isoweekday()).label.lower()}{mark}")
    if user.is_teacher:
        groups = ", ".join(g.name for g in user.teaching_groups.all()) or "нет"
        who = f"преподаватель {user} (группы: {groups})"
        role_rules = TEACHER_RULES.format(last_name=user.last_name or user.username)
    else:
        group = user.study_group.name if user.study_group else "не указана"
        who = f"студент {user}, группа {group}"
        role_rules = STUDENT_RULES
    return PROMPT.format(
        college=settings.COLLEGE_NAME,
        who=who,
        today=f"{today.isoformat()}, {Weekday(today.isoweekday()).label.lower()}",
        week_number=get_week_number(today),
        side=_side(get_week_type(today)),
        calendar="\n".join(calendar),
        role_rules=role_rules,
        refusal=REFUSAL_TEXT,
        knowledge=load_college_knowledge(),
    )


# --- Цикл агента ----------------------------------------------------------------


def run_agent(user, question: str, history: list[dict] | None = None) -> AgentReply:
    question = " ".join(str(question).split())
    if len(question) > settings.CHAT_MAX_QUESTION_LENGTH:
        return AgentReply(
            f"Вопрос слишком длинный. Сформулируйте короче — до {settings.CHAT_MAX_QUESTION_LENGTH} символов.",
            is_refusal=True,
        )
    if not llm.is_configured():
        return offline_answer(user, question)

    ctx = AgentContext(user)
    messages = [{"role": "system", "content": build_prompt(user)}]
    for turn in (history or [])[-2 * settings.CHAT_HISTORY_TURNS:]:
        role = "assistant" if turn.get("role") in {"model", "assistant"} else "user"
        messages.append({"role": role, "content": str(turn.get("text", ""))})
    messages.append({"role": "user", "content": question})
    tools = tools_for(user)

    try:
        for _ in range(settings.AGENT_MAX_STEPS):
            message = llm.complete(messages, tools=tools, max_tokens=settings.CHAT_MAX_OUTPUT_TOKENS)
            calls = message.tool_calls or []
            if not calls:
                text = llm.clean_text(message.content) or "Готово."
                return AgentReply(text, ctx.actions, is_refusal=REFUSAL_TEXT.rstrip(".") in text)
            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                    for c in calls
                ],
            })
            for call in calls:
                result = ctx.run(call.function.name, call.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })
    except llm.LLMError as exc:
        if ctx.actions:
            return AgentReply(f"Сделано, но ИИ не успел дописать ответ ({exc})", ctx.actions)
        reply = offline_answer(user, question)
        reply.text = f"{exc}\n\n{reply.text}"
        return reply
    return AgentReply("Не получилось уложиться в несколько шагов. Разбейте запрос на части.", ctx.actions)


# --- Запасной режим без модели --------------------------------------------------

OFFLINE_HELP = (
    "Сейчас ИИ не подключён, поэтому я понимаю только простые вопросы: "
    "«пары сегодня / завтра / в пятницу», «какая неделя», «задания», «долги»."
)


def _find_day_in_text(text: str) -> date | None:
    for word in ("послезавтра", "завтра", "сегодня"):
        if word in text:
            return parse_day(word)
    for word in WEEKDAY_WORDS:
        if word in text:
            return parse_day(word)
    match = re.search(r"\b(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b", text)
    return parse_day(match[1]) if match else None


def _format_day(group, day) -> str:
    lessons = get_schedule(group, [day])[day]
    head = f"{group.name}, {Weekday(day.isoweekday()).label.lower()} {day:%d.%m} ({_side(get_week_type(day))} неделя):"
    if not lessons:
        return f"{head}\nпар нет"
    lines = [head]
    for item in lessons:
        info = _lesson_info(item)
        line = f"{item.lesson_number}. {info.get('time', '')} {item.subject}".replace("  ", " ")
        if item.is_cancelled:
            line += " — ОТМЕНЕНА"
        else:
            if item.room:
                line += f", ауд. {item.room}"
            if item.is_replacement:
                line += " (замена)"
        lines.append(line)
    return "\n".join(lines)


def offline_answer(user, question: str) -> AgentReply:
    text = question.casefold()
    ctx = AgentContext(user)
    groups = list(user.teaching_groups.all()) if user.is_teacher else [user.study_group] if user.study_group else []

    if "долг" in text or "должник" in text:
        if user.is_teacher:
            parts = []
            for group in groups:
                debtors = group_debtors(group)
                names = ", ".join(f"{e['student']} ({len(e['debts'])})" for e in debtors)
                parts.append(f"{group.name}: {names or 'должников нет'}")
            answer = "Должники:\n" + "\n".join(parts)
        else:
            debts = student_homework_status(user)["debts"]
            answer = "Долгов нет." if not debts else "Ваши долги:\n" + "\n".join(
                f"- {h.subject}: {h.title} (срок был {timezone.localtime(h.deadline):%d.%m})" for h in debts
            )
    elif any(word in text for word in ("задани", "домашк", "дз")) and not user.is_teacher:
        upcoming = student_homework_status(user)["upcoming"]
        answer = "Предстоящих заданий нет." if not upcoming else "Задания:\n" + "\n".join(
            f"- {h.subject}: {h.title}, до {timezone.localtime(h.deadline):%d.%m %H:%M}"
            + (" (сдано)" if h.is_done else "") for h in upcoming
        )
    elif "недел" in text and not any(word in text for word in ("пар", "расписан")):
        info = tool_get_week(ctx)
        answer = f"Сейчас {info['week_number']}-я учебная неделя, {info['week']}."
    else:
        try:
            day = _find_day_in_text(text)
        except ToolError:
            day = None
        if day is None and not any(word in text for word in ("пар", "расписан", "урок")):
            return AgentReply(OFFLINE_HELP, offline=True)
        day = day or timezone.localdate()
        answer = "\n\n".join(_format_day(group, day) for group in groups) or "Группа не указана."
    return AgentReply(answer, offline=True)
