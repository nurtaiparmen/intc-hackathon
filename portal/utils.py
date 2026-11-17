"""Числитель/знаменатель и сборка расписания с учётом замен."""

from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from .models import ScheduleItem, Submission, Weekday, WeekType


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_week_number(target_date: date | None = None) -> int:
    """Номер учебной недели, считая с 1 от недели, в которую попадает SEMESTER_START."""
    target_date = target_date or timezone.localdate()
    days = (_monday(target_date) - _monday(settings.SEMESTER_START)).days
    return days // 7 + 1


def get_week_type(target_date: date | None = None) -> str:
    """Тип недели для даты: WeekType.NUMERATOR или WeekType.DENOMINATOR.

    Недели чередуются начиная с первой учебной (settings.SEMESTER_START),
    тип которой задаёт settings.FIRST_WEEK_TYPE. Считаем от понедельников,
    поэтому результат одинаков для всех дней одной недели и не ломается
    на стыке годов (в отличие от чётности ISO-номера недели).
    """
    first = WeekType(settings.FIRST_WEEK_TYPE)
    if first == WeekType.EVERY:
        raise ValueError("FIRST_WEEK_TYPE должен быть numerator или denominator")
    second = WeekType.DENOMINATOR if first == WeekType.NUMERATOR else WeekType.NUMERATOR
    return first if get_week_number(target_date) % 2 == 1 else second


def week_dates(any_date: date) -> list[date]:
    """Все семь дат недели (пн-вс), в которую попадает any_date."""
    monday = _monday(any_date)
    return [monday + timedelta(days=i) for i in range(7)]


def get_schedule(group, dates: list[date]) -> dict[date, list[ScheduleItem]]:
    """Итоговое расписание группы на каждую из дат (2 запроса на любой период).

    Для каждого дня берём постоянные пары нужного типа недели и накладываем
    замены на эту дату. У замены появляется атрибут .original — пара,
    которую она перекрыла (или None, если пару добавили в пустой слот).
    """
    items = group.schedule_items.select_related("subject", "teacher")
    regular = list(items.filter(is_replacement=False))
    replacements = list(items.filter(is_replacement=True, date__in=dates))

    result = {}
    for day in dates:
        week_type = get_week_type(day)
        slots = {}
        # Сначала «каждую неделю», затем числитель/знаменатель — он точнее.
        for kind in (WeekType.EVERY, week_type):
            for item in regular:
                if item.weekday == day.isoweekday() and item.week_type == kind:
                    slots[item.lesson_number] = item
        for item in replacements:
            if item.date == day:
                item.original = slots.get(item.lesson_number)
                slots[item.lesson_number] = item
        result[day] = [slots[number] for number in sorted(slots)]
    return result


def get_day_schedule(group, target_date: date | None = None) -> list[ScheduleItem]:
    target_date = target_date or timezone.localdate()
    return get_schedule(group, [target_date])[target_date]


def get_week_schedule(group, any_date: date | None = None) -> list[dict]:
    """Неделя для шаблона: дни с парами. Воскресенье показываем, только если в нём есть пары."""
    today = timezone.localdate()
    dates = week_dates(any_date or today)
    schedule = get_schedule(group, dates)
    return [
        {
            "date": day,
            "weekday": Weekday(day.isoweekday()).label,
            "is_today": day == today,
            "lessons": schedule[day],
        }
        for day in dates
        if day.isoweekday() != 7 or schedule[day]
    ]


def semester_homeworks(group):
    """Задания группы с начала семестра: и будущие, и просроченные."""
    return group.homeworks.filter(deadline__date__gte=settings.SEMESTER_START).select_related(
        "subject", "teacher"
    )


def student_homework_status(user) -> dict:
    """Долги, предстоящие задания по предметам и сданное для одного студента."""
    group = user.study_group
    empty = {"debts": [], "upcoming": [], "by_subject": [], "upcoming_count": 0}
    if group is None:
        return empty
    now = timezone.now()
    done = set(Submission.objects.filter(student=user).values_list("homework_id", flat=True))
    debts, upcoming = [], []
    for homework in semester_homeworks(group).order_by("deadline"):
        homework.is_done = homework.pk in done
        if homework.deadline < now:
            if not homework.is_done:
                debts.append(homework)
        else:
            upcoming.append(homework)

    by_subject = {}
    for homework in upcoming:
        by_subject.setdefault(homework.subject.name, []).append(homework)
    return {
        "debts": debts,
        "upcoming": upcoming,
        "by_subject": [{"subject": name, "items": items} for name, items in sorted(by_subject.items())],
        "upcoming_count": sum(1 for h in upcoming if not h.is_done),
    }


def group_debtors(group) -> list[dict]:
    """Студенты группы с долгами: кто и по каким заданиям."""
    overdue = list(semester_homeworks(group).filter(deadline__lt=timezone.now()))
    if not overdue:
        return []
    done = set(
        Submission.objects.filter(homework__in=overdue).values_list("homework_id", "student_id")
    )
    result = []
    for student in group.students.order_by("last_name", "first_name"):
        debts = [hw for hw in overdue if (hw.pk, student.pk) not in done]
        if debts:
            result.append({"student": student, "debts": debts})
    return result
