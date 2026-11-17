from django import forms
from django.conf import settings
from django.utils import timezone

from .models import Homework, ScheduleItem, Subject, User, Weekday, WeekType
from .utils import get_schedule

def _lesson_choices():
    return [
        (number, f"{number} пара, {start:%H:%M}")
        for number, (start, _end) in sorted(settings.BELL_SCHEDULE.items())
    ]


class ReplacementForm(forms.Form):
    """Замена или отмена одной пары на конкретную дату."""

    date = forms.DateField(
        label="Дата",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    lesson_number = forms.TypedChoiceField(label="Пара", coerce=int, choices=_lesson_choices)
    is_cancelled = forms.BooleanField(label="Отменить пару", required=False)
    subject = forms.ModelChoiceField(
        label="Предмет", queryset=Subject.objects.all(), required=False, empty_label="Выберите предмет"
    )
    room = forms.CharField(label="Аудитория", max_length=30, required=False)
    teacher = forms.ModelChoiceField(
        label="Преподаватель",
        queryset=User.objects.filter(role=User.Role.TEACHER),
        required=False,
        empty_label="Не указан",
    )
    note = forms.CharField(label="Комментарий для студентов", max_length=200, required=False)

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group

    def _regular_lesson(self, day, number):
        """Постоянная пара в этом слоте (под уже существующей заменой, если она есть)."""
        for item in get_schedule(self.group, [day])[day]:
            if item.lesson_number == number:
                return item.original if item.is_replacement else item
        return None

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day < timezone.localdate():
            raise forms.ValidationError("Эта дата уже прошла.")
        return day

    def clean(self):
        data = super().clean()
        if "date" not in data or "lesson_number" not in data:
            return data
        if data.get("is_cancelled"):
            regular = self._regular_lesson(data["date"], data["lesson_number"])
            if regular is None:
                raise forms.ValidationError("В этот день такой пары нет — отменять нечего.")
            data["subject"] = regular.subject
            data["teacher"] = regular.teacher
            data["room"] = ""
        elif not data.get("subject"):
            self.add_error("subject", "Выберите предмет или отметьте «Отменить пару».")
        return data

    def save(self) -> ScheduleItem:
        data = self.cleaned_data
        item, _ = ScheduleItem.objects.update_or_create(
            group=self.group,
            is_replacement=True,
            date=data["date"],
            lesson_number=data["lesson_number"],
            defaults={
                "subject": data["subject"],
                "teacher": data["teacher"],
                "room": data["room"],
                "is_cancelled": data["is_cancelled"],
                "note": data["note"],
            },
        )
        return item


class RegularLessonForm(forms.Form):
    """Пара постоянного расписания. Если слот уже занят — пара в нём обновляется."""

    weekday = forms.TypedChoiceField(label="День", coerce=int, choices=Weekday.choices[:6])
    lesson_number = forms.TypedChoiceField(label="Пара", coerce=int, choices=_lesson_choices)
    week_type = forms.ChoiceField(
        label="Неделя",
        choices=[
            (WeekType.EVERY, "Каждую неделю"),
            (WeekType.NUMERATOR, "Только левая"),
            (WeekType.DENOMINATOR, "Только правая"),
        ],
    )
    subject = forms.CharField(
        label="Предмет", max_length=150, widget=forms.TextInput(attrs={"list": "subjects"})
    )
    room = forms.CharField(label="Аудитория", max_length=30, required=False)
    teacher = forms.ModelChoiceField(
        label="Преподаватель",
        queryset=User.objects.filter(role=User.Role.TEACHER),
        required=False,
        empty_label="Не указан",
    )

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group

    def clean_subject(self):
        return " ".join(self.cleaned_data["subject"].split())

    def save(self) -> ScheduleItem:
        data = self.cleaned_data
        wanted = data["subject"].casefold()
        subject = next((s for s in Subject.objects.all() if s.name.casefold() == wanted), None)
        if subject is None:
            subject = Subject.objects.create(name=data["subject"])
        item, _ = ScheduleItem.objects.update_or_create(
            group=self.group,
            is_replacement=False,
            weekday=data["weekday"],
            lesson_number=data["lesson_number"],
            week_type=data["week_type"],
            defaults={"subject": subject, "room": data["room"], "teacher": data["teacher"]},
        )
        return item


class HomeworkForm(forms.ModelForm):
    class Meta:
        model = Homework
        fields = ["subject", "title", "description", "deadline"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].empty_label = "Выберите предмет"

    def clean_deadline(self):
        deadline = self.cleaned_data["deadline"]
        if deadline < timezone.now():
            raise forms.ValidationError("Срок сдачи уже прошёл.")
        return deadline


class ScheduleTextForm(forms.Form):
    text = forms.CharField(
        label="Текст расписания",
        max_length=settings.MAX_SCHEDULE_TEXT_LENGTH,
        widget=forms.Textarea(
            attrs={"rows": 10, "placeholder": "Скопируйте таблицу из Excel, Word или Telegram — или загрузите фото выше"}
        ),
        error_messages={"required": "Загрузите фото или вставьте текст расписания."},
    )
