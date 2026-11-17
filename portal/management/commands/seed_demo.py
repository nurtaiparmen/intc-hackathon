"""python manage.py seed_demo — демо-данные группы РПО6-24Р для показа прототипа.

Расписание перенесено с реального скриншота расписания группы. Команду можно
запускать повторно: расписание и задания группы пересоздаются, пользователи
и предметы не дублируются.
"""

from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from portal.models import Group, Homework, ScheduleItem, Subject, Submission, User, WeekType

DEMO_PASSWORD = "demo12345"
GROUP = "РПО6-24Р"

# Команда хакатона — студенты группы. (логин, имя, фамилия)
STUDENTS = [
    ("student", "Самандар", "Айтматов"),
    ("asanali", "Асанали", "Исмаилов"),
    ("sultan", "Султан", "Алтынбек"),
    ("nurtay", "Нуртай", "Пармен"),
]

# Преподаватели из расписания. (фамилия, инициалы). Входа у них нет.
TEACHERS = {
    "Туменов": "Б.Б.", "Рахматула": "А.Г.", "Маратов": "Е.М.", "Манатбай": "А.А.",
    "Юсупова": "Н.И.", "Дюсенбаев": "А.А.", "Бычкова": "С.С.", "Аламанова": "А.А.", "Самат": "А.",
}

DS = "Структуры данных"
FLOW = "Создание блок-схем и визуализация алгоритмов"
PROG = "Основы программирования и проектирование модулей"
PROTO = "Прототипирование программных модулей"
PE = "Физическая культура"
DOCS = "Разработка технической документации и спецификаций"
ARCH = "Архитектура ПО"
ALGO = "Алгоритмы и структура данных"
REQ = "Анализ требований и управление проектами"
CURATOR_HOUR = "Кураторский час"

E, L, R = WeekType.EVERY, WeekType.NUMERATOR, WeekType.DENOMINATOR
TUE = {0: ("08:00", "08:25"), 1: ("08:30", "09:50"), 2: ("10:00", "11:20"), 3: ("11:30", "12:50"), 4: ("13:00", "14:20")}

# (день, пара, предмет, аудитория, преподаватель, неделя, пометка)
SCHEDULE = [
    (1, 1, DS, "602", "Туменов", E, ""),
    (1, 2, FLOW, "502", "Рахматула", L, ""),
    (1, 2, PROG, "603", "Маратов", R, ""),
    (1, 3, PROTO, "606", "Манатбай", E, ""),
    (1, 4, PE, "спортзал", "Юсупова", E, ""),
    (2, 0, CURATOR_HOUR, "410", "Болатов", E, ""),
    (2, 1, DOCS, "410", "Дюсенбаев", E, ""),
    (2, 2, ARCH, "709", "Бычкова", E, "10.11 и 17.11 ведёт Рахматула А.Г."),
    (2, 3, ALGO, "604", "Аламанова", E, ""),
    (2, 4, PROG, "716", "Маратов", E, ""),
    (3, 1, DOCS, "410", "Дюсенбаев", E, ""),
    (3, 2, REQ, "404", "Самат", E, ""),
    (3, 3, FLOW, "604", "Рахматула", E, ""),
    (3, 4, DS, "602", "Туменов", L, ""),
    (3, 4, DOCS, "410", "Дюсенбаев", R, ""),
    (4, 1, PROTO, "606", "Манатбай", E, ""),
    (4, 2, FLOW, "603", "Рахматула", E, ""),
    (4, 3, DS, "602", "Туменов", E, ""),
    (5, 1, PROG, "709", "Маратов", E, ""),
    (5, 2, ALGO, "405", "Аламанова", E, ""),
    (5, 3, DOCS, "410", "Дюсенбаев", E, ""),
]


def _t(value):
    return time.fromisoformat(value) if value else None


class Command(BaseCommand):
    help = "Создаёт группу РПО6-24Р: куратор, команда, реальное расписание, замену и задания."

    @transaction.atomic
    def handle(self, *args, **options):
        # Старые демо-группы прошлых версий проекта больше не нужны.
        Group.objects.filter(name__in=["ПО-21", "ИС-11"]).delete()
        group, _ = Group.objects.update_or_create(name=GROUP, defaults={"course": 3})

        curator = self._user("teacher", "Айкын", "Болатов", role=User.Role.TEACHER)
        curator.teaching_groups.set([group])
        teachers = {"Болатов": curator}
        for last, initials in TEACHERS.items():
            teachers[last] = self._user(
                f"t_{len(teachers)}", initials, last, role=User.Role.TEACHER, can_login=False
            )
        students = [
            self._user(login, first, last, role=User.Role.STUDENT, study_group=group)
            for login, first, last in STUDENTS
        ]
        me = students[0]
        self._user("admin", "Админ", "Портала", is_staff=True, is_superuser=True)

        subjects = {name: Subject.objects.get_or_create(name=name)[0] for name in {row[2] for row in SCHEDULE}}

        group.schedule_items.all().delete()
        items = []
        for weekday, number, name, room, teacher, week, note in SCHEDULE:
            start, end = TUE[number] if weekday == 2 else (None, None)
            items.append(ScheduleItem(
                group=group, weekday=weekday, lesson_number=number, subject=subjects[name], room=room,
                teacher=teachers[teacher], week_type=week, note=note, start_time=_t(start), end_time=_t(end),
            ))
        ScheduleItem.objects.bulk_create(items)

        # Замена на ближайший учебный день (пн-пт), чтобы её было видно сразу.
        day = timezone.localdate()
        while day.isoweekday() > 5:
            day += timedelta(days=1)
        ScheduleItem.objects.create(
            group=group, is_replacement=True, date=day, lesson_number=2,
            subject=subjects[ARCH], teacher=teachers["Бычкова"], room="709",
            note="Перенос: преподаватель на курсах повышения квалификации.",
        )

        group.homeworks.all().delete()
        now = timezone.now()

        def evening(days, hour=18):
            target = timezone.localdate() + timedelta(days=days)
            return timezone.make_aware(datetime.combine(target, time(hour, 0)))

        hw = Homework.objects.bulk_create([
            Homework(group=group, subject=subjects[DOCS], teacher=teachers["Дюсенбаев"],
                     title="Техническое задание на свой проект по ГОСТ 34",
                     description="Разделы: назначение, требования, этапы. Сдать в печатном виде.",
                     deadline=(now + timedelta(hours=20)).replace(minute=0, second=0, microsecond=0)),
            Homework(group=group, subject=subjects[DS], teacher=teachers["Туменов"],
                     title="Лабораторная №5: стек и очередь",
                     description="Реализовать на Python без встроенных deque, с тестами.",
                     deadline=evening(3)),
            Homework(group=group, subject=subjects[ARCH], teacher=teachers["Бычкова"],
                     title="Диаграмма компонентов для своего проекта", deadline=evening(6)),
            Homework(group=group, subject=subjects[ALGO], teacher=teachers["Аламанова"],
                     title="Задачи 1–10: сортировки", deadline=evening(-4)),
            Homework(group=group, subject=subjects[FLOW], teacher=teachers["Рахматула"],
                     title="Блок-схема бинарного поиска", deadline=evening(-7)),
            Homework(group=group, subject=subjects[PROG], teacher=teachers["Маратов"],
                     title="Модуль «Калькулятор» с тестами", deadline=evening(-2)),
        ])
        ds_lab, sorting, flowchart, calc = hw[1], hw[3], hw[4], hw[5]
        submitted = {
            ds_lab: [me],
            sorting: students[1:],         # у Самандара долг
            flowchart: students[:3],       # у Нуртая долг
            calc: students[1:3],           # долг у Самандара и Нуртая
        }
        Submission.objects.bulk_create(
            Submission(homework=h, student=s) for h, who in submitted.items() for s in who
        )

        self.stdout.write(self.style.SUCCESS(f"Демо-данные группы {GROUP} готовы."))
        self.stdout.write(f"Входы: student (Самандар), teacher (куратор Болатов), admin. Пароль: {DEMO_PASSWORD}")
        self.stdout.write("Ещё студенты: asanali, sultan, nurtay — пароль тот же.")

    def _user(self, username, first_name, last_name, can_login=True, **fields):
        user, created = User.objects.update_or_create(
            username=username, defaults={"first_name": first_name, "last_name": last_name, **fields}
        )
        if created:
            if can_login:
                user.set_password(DEMO_PASSWORD)
            else:
                user.set_unusable_password()
            user.save()
        return user
