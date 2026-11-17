"""Числитель/знаменатель и сборка расписания с учётом замен."""

from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Homework, ScheduleItem, Weekday, WeekType


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


def build_student_context(user) -> str:
    """Короткая текстовая сводка для чат-бота: кто спрашивает, пары и ДЗ."""
    today = timezone.localdate()
    week_type = WeekType(get_week_type(today)).label.lower()
    lines = [
        f"Сегодня {today:%d.%m.%Y}, {Weekday(today.isoweekday()).label.lower()}.",
        f"Идёт {get_week_number(today)}-я учебная неделя, тип недели: {week_type}.",
    ]
    group = user.study_group
    if group is None:
        return "\n".join(lines)

    lines.append(f"Студент учится в группе {group.name}. Расписание группы на эту неделю:")
    for day in get_week_schedule(group, today):
        if not day["lessons"]:
            continue
        lines.append(f"{day['weekday']} ({day['date']:%d.%m}):")
        for item in day["lessons"]:
            start, end = item.times
            time_range = f"{start:%H:%M}-{end:%H:%M}" if start and end else "время не указано"
            if item.is_cancelled:
                lines.append(f"  {item.lesson_number} пара ({time_range}): ОТМЕНЕНА")
                continue
            parts = [item.subject.name]
            if item.room:
                parts.append(f"ауд. {item.room}")
            if item.teacher:
                parts.append(str(item.teacher))
            if item.is_replacement:
                parts.append("ЗАМЕНА")
            lines.append(f"  {item.lesson_number} пара ({time_range}): {', '.join(parts)}")

    homeworks = Homework.objects.filter(group=group, deadline__gte=timezone.now()).select_related(
        "subject"
    )[:10]
    if homeworks:
        lines.append("Актуальные домашние задания:")
        for hw in homeworks:
            deadline = timezone.localtime(hw.deadline)
            lines.append(f"  {hw.subject.name}: {hw.title} (сдать до {deadline:%d.%m %H:%M})")
    return "\n".join(lines)
