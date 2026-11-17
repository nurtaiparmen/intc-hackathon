"""python manage.py seed_demo — демо-данные для показа прототипа.

Команду можно запускать повторно: расписание и задания демо-группы
пересоздаются, пользователи и предметы не дублируются.
"""

from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from portal.models import Group, Homework, ScheduleItem, Subject, User, WeekType

DEMO_PASSWORD = "demo12345"

# (день недели, номер пары, предмет, аудитория, тип недели)
SCHEDULE = [
    (1, 1, "Математика", "204", WeekType.EVERY),
    (1, 2, "Программирование на Python", "301", WeekType.EVERY),
    (1, 3, "Английский язык", "115", WeekType.NUMERATOR),
    (1, 3, "История", "118", WeekType.DENOMINATOR),
    (2, 1, "Базы данных", "302", WeekType.EVERY),
    (2, 2, "Физика", "210", WeekType.EVERY),
    (2, 3, "Веб-разработка", "301", WeekType.DENOMINATOR),
    (3, 2, "Математика", "204", WeekType.EVERY),
    (3, 3, "Программирование на Python", "301", WeekType.NUMERATOR),
    (3, 3, "Базы данных", "302", WeekType.DENOMINATOR),
    (3, 4, "Физическая культура", "спортзал", WeekType.EVERY),
    (4, 1, "Английский язык", "115", WeekType.EVERY),
    (4, 2, "Веб-разработка", "301", WeekType.EVERY),
    (4, 3, "Физика", "210", WeekType.NUMERATOR),
    (5, 1, "История", "118", WeekType.EVERY),
    (5, 2, "Базы данных", "302", WeekType.EVERY),
    (5, 3, "Математика", "204", WeekType.DENOMINATOR),
]


class Command(BaseCommand):
    help = "Создаёт демо-группу, пользователей, расписание, замену и домашние задания."

    @transaction.atomic
    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name="ПО-21", defaults={"course": 2})
        Group.objects.get_or_create(name="ИС-11", defaults={"course": 1})

        teacher = self._user("teacher", "Анна", "Иванова", role=User.Role.TEACHER)
        teacher.teaching_groups.add(group)
        self._user("student", "Алексей", "Смирнов", role=User.Role.STUDENT, study_group=group)
        self._user("admin", "Админ", "Портала", is_staff=True, is_superuser=True)

        subjects = {
            name: Subject.objects.get_or_create(name=name)[0]
            for name in {row[2] for row in SCHEDULE}
        }

        group.schedule_items.all().delete()
        ScheduleItem.objects.bulk_create(
            ScheduleItem(
                group=group,
                weekday=weekday,
                lesson_number=number,
                subject=subjects[name],
                room=room,
                week_type=week_type,
                teacher=teacher if name in ("Программирование на Python", "Веб-разработка") else None,
            )
            for weekday, number, name, room, week_type in SCHEDULE
        )

        # Замена на ближайший учебный день (пн-пт), чтобы её было видно сразу.
        day = timezone.localdate()
        while day.isoweekday() > 5:
            day += timedelta(days=1)
        ScheduleItem.objects.create(
            group=group,
            is_replacement=True,
            date=day,
            lesson_number=2,
            subject=subjects["Веб-разработка"],
            teacher=teacher,
            room="305",
            note="Преподаватель на конференции, пару ведёт А. Иванова.",
        )

        group.homeworks.all().delete()
        now = timezone.now()

        def evening(days):
            day = timezone.localdate() + timedelta(days=days)
            return timezone.make_aware(datetime.combine(day, time(18, 0)))

        Homework.objects.bulk_create(
            [
                Homework(
                    group=group, subject=subjects["Веб-разработка"], teacher=teacher,
                    title="Сверстать страницу профиля",
                    description="Адаптивная вёрстка, без фреймворков. Ссылку на репозиторий пришлите в портал.",
                    deadline=(now + timedelta(hours=18)).replace(minute=0, second=0, microsecond=0),
                ),
                Homework(
                    group=group, subject=subjects["Программирование на Python"], teacher=teacher,
                    title="Задачи 12–18 по спискам и словарям",
                    deadline=evening(3),
                ),
                Homework(
                    group=group, subject=subjects["Базы данных"],
                    title="Спроектировать схему БД библиотеки",
                    description="ER-диаграмма и SQL-скрипт создания таблиц.",
                    deadline=evening(7),
                ),
            ]
        )

        self.stdout.write(self.style.SUCCESS("Демо-данные готовы."))
        self.stdout.write(f"Логины: student, teacher, admin. Пароль у всех: {DEMO_PASSWORD}")

    def _user(self, username, first_name, last_name, **fields):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first_name, "last_name": last_name, **fields},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user
