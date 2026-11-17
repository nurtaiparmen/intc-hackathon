from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Group(models.Model):
    """Учебная группа (не путать с django.contrib.auth.models.Group — это права)."""

    name = models.CharField("Название", max_length=50, unique=True)
    course = models.PositiveSmallIntegerField("Курс", default=1)

    class Meta:
        verbose_name = "Учебная группа"
        verbose_name_plural = "Учебные группы"
        ordering = ["name"]

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "Студент"
        TEACHER = "teacher", "Преподаватель"

    role = models.CharField("Роль", max_length=10, choices=Role.choices, default=Role.STUDENT)

    # Для студента: группа, в которой он учится.
    study_group = models.ForeignKey(
        Group,
        verbose_name="Группа студента",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    # Для преподавателя: группы, которыми он управляет (выдаются в админке).
    teaching_groups = models.ManyToManyField(
        Group,
        verbose_name="Группы преподавателя",
        blank=True,
        related_name="teachers",
    )

    class Meta(AbstractUser.Meta):
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT

    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER

    def __str__(self):
        return self.get_full_name() or self.username


class Subject(models.Model):
    name = models.CharField("Название", max_length=150, unique=True)

    class Meta:
        verbose_name = "Предмет"
        verbose_name_plural = "Предметы"
        ordering = ["name"]

    def __str__(self):
        return self.name


class WeekType(models.TextChoices):
    EVERY = "every", "Каждую неделю"
    NUMERATOR = "numerator", "Числитель"
    DENOMINATOR = "denominator", "Знаменатель"


class Weekday(models.IntegerChoices):
    MONDAY = 1, "Понедельник"
    TUESDAY = 2, "Вторник"
    WEDNESDAY = 3, "Среда"
    THURSDAY = 4, "Четверг"
    FRIDAY = 5, "Пятница"
    SATURDAY = 6, "Суббота"
    SUNDAY = 7, "Воскресенье"


class ScheduleItem(models.Model):
    """Одна пара в расписании группы.

    Два вида записей в одной таблице:
    * постоянная пара: is_replacement=False, date пустая, повторяется по
      weekday + week_type;
    * замена: is_replacement=True, date — конкретный день. Она перекрывает
      постоянную пару с тем же номером в этот день (или отменяет её,
      если is_cancelled=True).
    """

    group = models.ForeignKey(
        Group, verbose_name="Группа", on_delete=models.CASCADE, related_name="schedule_items"
    )
    subject = models.ForeignKey(
        Subject, verbose_name="Предмет", on_delete=models.PROTECT, related_name="schedule_items"
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Преподаватель",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"role": "teacher"},
        related_name="schedule_items",
    )

    weekday = models.PositiveSmallIntegerField("День недели", choices=Weekday.choices)
    lesson_number = models.PositiveSmallIntegerField(
        "Номер пары", validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    start_time = models.TimeField("Начало", null=True, blank=True)
    end_time = models.TimeField("Конец", null=True, blank=True)
    room = models.CharField("Аудитория", max_length=30, blank=True)
    week_type = models.CharField(
        "Тип недели", max_length=12, choices=WeekType.choices, default=WeekType.EVERY
    )

    is_replacement = models.BooleanField("Замена", default=False)
    date = models.DateField("Дата замены", null=True, blank=True)
    is_cancelled = models.BooleanField("Пара отменена", default=False)
    note = models.CharField("Комментарий", max_length=200, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Пара в расписании"
        verbose_name_plural = "Расписание"
        ordering = ["weekday", "lesson_number"]
        constraints = [
            # У замены всегда есть дата, у постоянной пары — никогда.
            models.CheckConstraint(
                condition=Q(is_replacement=True, date__isnull=False)
                | Q(is_replacement=False, date__isnull=True),
                name="schedule_replacement_has_date",
            ),
            # Одна постоянная пара на слот (группа, день, номер, тип недели).
            models.UniqueConstraint(
                fields=["group", "weekday", "lesson_number", "week_type"],
                condition=Q(is_replacement=False),
                name="schedule_unique_regular_slot",
            ),
            # Одна замена на слот (группа, дата, номер пары).
            models.UniqueConstraint(
                fields=["group", "date", "lesson_number"],
                condition=Q(is_replacement=True),
                name="schedule_unique_replacement_slot",
            ),
        ]
        indexes = [models.Index(fields=["group", "is_replacement", "date"])]

    def clean(self):
        if self.is_replacement and not self.date:
            raise ValidationError({"date": "Для замены нужно указать дату."})
        if not self.is_replacement and self.is_cancelled:
            raise ValidationError({"is_cancelled": "Отменить можно только через замену на дату."})

    def save(self, *args, **kwargs):
        if self.is_replacement and self.date:
            # Замена живёт на конкретной дате: день недели выводим из неё.
            self.weekday = self.date.isoweekday()
            self.week_type = WeekType.EVERY
        else:
            self.date = None
        super().save(*args, **kwargs)

    @property
    def times(self):
        """(начало, конец): из записи, а если не заданы — из расписания звонков."""
        bell_start, bell_end = settings.BELL_SCHEDULE.get(self.lesson_number, (None, None))
        return self.start_time or bell_start, self.end_time or bell_end

    def __str__(self):
        when = f"{self.date:%d.%m.%Y}" if self.date else self.get_weekday_display()
        prefix = "Замена: " if self.is_replacement else ""
        return f"{prefix}{self.group} / {when} / {self.lesson_number} пара / {self.subject}"


class Homework(models.Model):
    group = models.ForeignKey(
        Group, verbose_name="Группа", on_delete=models.CASCADE, related_name="homeworks"
    )
    subject = models.ForeignKey(
        Subject, verbose_name="Предмет", on_delete=models.PROTECT, related_name="homeworks"
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Преподаватель",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"role": "teacher"},
        related_name="homeworks",
    )
    title = models.CharField("Задание", max_length=200)
    description = models.TextField("Подробности", blank=True)
    deadline = models.DateTimeField("Сдать до")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Домашнее задание"
        verbose_name_plural = "Домашние задания"
        ordering = ["deadline"]
        indexes = [models.Index(fields=["group", "deadline"])]

    @property
    def is_overdue(self):
        return self.deadline < timezone.now()

    @property
    def is_soon(self):
        """До срока сдачи меньше суток."""
        return timezone.now() <= self.deadline <= timezone.now() + timedelta(days=1)

    def __str__(self):
        return f"{self.group} / {self.subject}: {self.title}"
