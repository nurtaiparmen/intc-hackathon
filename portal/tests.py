"""Тесты запускаются без интернета и без ключа: вызовы Gemini подменены."""

import json
from datetime import date, time, timedelta
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai_services
from .ai_services import (
    REFUSAL_TEXT,
    AIServiceError,
    BotAnswer,
    ParsedSchedule,
    apply_parsed_schedule,
    ask_college_bot,
    parse_schedule_image,
)
from .models import Group, Homework, ScheduleItem, Subject, User, WeekType
from .utils import build_student_context, get_day_schedule, get_week_number, get_week_type


@override_settings(SEMESTER_START=date(2026, 9, 1), FIRST_WEEK_TYPE="numerator")
class WeekTypeTests(TestCase):
    def test_first_week_is_numerator_for_all_its_days(self):
        # 1 сентября 2026 — вторник; вся неделя 31.08–06.09 считается первой.
        for day in (date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 6)):
            self.assertEqual(get_week_type(day), WeekType.NUMERATOR)
            self.assertEqual(get_week_number(day), 1)

    def test_weeks_alternate(self):
        self.assertEqual(get_week_type(date(2026, 9, 7)), WeekType.DENOMINATOR)
        self.assertEqual(get_week_type(date(2026, 9, 14)), WeekType.NUMERATOR)
        self.assertEqual(get_week_type(date(2026, 11, 17)), WeekType.DENOMINATOR)
        self.assertEqual(get_week_number(date(2026, 11, 17)), 12)

    def test_alternation_survives_new_year(self):
        self.assertNotEqual(get_week_type(date(2026, 12, 28)), get_week_type(date(2027, 1, 4)))

    @override_settings(FIRST_WEEK_TYPE="denominator")
    def test_first_week_type_is_configurable(self):
        self.assertEqual(get_week_type(date(2026, 9, 1)), WeekType.DENOMINATOR)
        self.assertEqual(get_week_type(date(2026, 9, 8)), WeekType.NUMERATOR)


class PortalTestCase(TestCase):
    """Общие данные: группа, преподаватель, студент, пара предметов."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name="ПО-21")
        cls.other_group = Group.objects.create(name="ИС-11")
        cls.math = Subject.objects.create(name="Математика")
        cls.physics = Subject.objects.create(name="Физика")
        cls.teacher = User.objects.create_user(
            "teacher", password="pass12345", role=User.Role.TEACHER, last_name="Иванова"
        )
        cls.teacher.teaching_groups.add(cls.group)
        cls.student = User.objects.create_user(
            "student", password="pass12345", role=User.Role.STUDENT, study_group=cls.group
        )

    def setUp(self):
        cache.clear()


@override_settings(SEMESTER_START=date(2026, 9, 1), FIRST_WEEK_TYPE="numerator")
class ScheduleTests(PortalTestCase):
    MONDAY_NUM = date(2026, 9, 14)  # числитель
    MONDAY_DEN = date(2026, 9, 21)  # знаменатель

    def setUp(self):
        super().setUp()
        common = {"group": self.group, "weekday": 1}
        ScheduleItem.objects.create(**common, lesson_number=1, subject=self.math, room="204")
        ScheduleItem.objects.create(
            **common, lesson_number=2, subject=self.math, week_type=WeekType.NUMERATOR
        )
        ScheduleItem.objects.create(
            **common, lesson_number=2, subject=self.physics, week_type=WeekType.DENOMINATOR
        )

    def test_numerator_and_denominator_weeks_differ(self):
        on_numerator = get_day_schedule(self.group, self.MONDAY_NUM)
        on_denominator = get_day_schedule(self.group, self.MONDAY_DEN)
        self.assertEqual([i.subject for i in on_numerator], [self.math, self.math])
        self.assertEqual([i.subject for i in on_denominator], [self.math, self.physics])

    def test_replacement_overrides_only_its_date(self):
        ScheduleItem.objects.create(
            group=self.group, is_replacement=True, date=self.MONDAY_NUM,
            lesson_number=1, subject=self.physics, room="301",
        )
        replaced = get_day_schedule(self.group, self.MONDAY_NUM)[0]
        self.assertTrue(replaced.is_replacement)
        self.assertEqual(replaced.subject, self.physics)
        self.assertEqual(replaced.original.subject, self.math)
        self.assertEqual(replaced.weekday, 1)  # выставлен из даты в save()
        untouched = get_day_schedule(self.group, self.MONDAY_NUM + timedelta(days=14))[0]
        self.assertFalse(untouched.is_replacement)

    def test_times_fall_back_to_bell_schedule(self):
        first = get_day_schedule(self.group, self.MONDAY_NUM)[0]
        self.assertEqual(first.times, (time(8, 30), time(10, 0)))


class RoleRoutingTests(PortalTestCase):
    def test_anonymous_goes_to_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

    def test_home_redirects_by_role(self):
        self.client.force_login(self.student)
        self.assertRedirects(self.client.get(reverse("home")), reverse("student_dashboard"))
        self.client.force_login(self.teacher)
        self.assertRedirects(self.client.get(reverse("home")), reverse("teacher_dashboard"))

    def test_student_cannot_open_teacher_pages(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("teacher_group", args=[self.group.pk]))
        self.assertRedirects(response, reverse("home"), target_status_code=302)

    def test_teacher_cannot_open_foreign_group(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("teacher_group", args=[self.other_group.pk]))
        self.assertEqual(response.status_code, 404)

    def test_pages_render(self):
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("student_dashboard")), "Сегодня")
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("teacher_dashboard")), "ПО-21")
        self.assertContains(
            self.client.get(reverse("teacher_group", args=[self.group.pk])), "Заменить или отменить"
        )


class TeacherActionsTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.teacher)
        self.day = timezone.localdate() + timedelta(days=1)
        self.regular = ScheduleItem.objects.create(
            group=self.group, weekday=self.day.isoweekday(), lesson_number=1,
            subject=self.math, room="204",
        )

    def _post_replacement(self, **extra):
        data = {"date": self.day.isoformat(), "lesson_number": 1, **extra}
        return self.client.post(reverse("replacement_create", args=[self.group.pk]), data)

    def test_replacement_is_shown_to_student(self):
        response = self._post_replacement(subject=self.physics.pk, room="301", note="Перенос")
        self.assertEqual(response.status_code, 302)
        lesson = get_day_schedule(self.group, self.day)[0]
        self.assertEqual((lesson.subject, lesson.room, lesson.is_replacement), (self.physics, "301", True))

        self.client.force_login(self.student)
        page = self.client.get(reverse("student_dashboard"), {"date": self.day.isoformat()})
        self.assertContains(page, "Замена")

    def test_repeated_replacement_updates_the_same_record(self):
        self._post_replacement(subject=self.physics.pk, room="301")
        self._post_replacement(subject=self.physics.pk, room="302")
        replacement = ScheduleItem.objects.get(is_replacement=True)
        self.assertEqual(replacement.room, "302")

    def test_cancel_keeps_original_subject(self):
        self._post_replacement(is_cancelled="on")
        lesson = get_day_schedule(self.group, self.day)[0]
        self.assertTrue(lesson.is_cancelled)
        self.assertEqual(lesson.subject, self.math)

    def test_cannot_cancel_missing_lesson(self):
        response = self.client.post(
            reverse("replacement_create", args=[self.group.pk]),
            {"date": self.day.isoformat(), "lesson_number": 5, "is_cancelled": "on"},
        )
        self.assertContains(response, "отменять нечего")
        self.assertFalse(ScheduleItem.objects.filter(is_replacement=True).exists())

    def test_replacement_can_be_removed(self):
        self._post_replacement(subject=self.physics.pk)
        item = ScheduleItem.objects.get(is_replacement=True)
        self.client.post(reverse("replacement_delete", args=[self.group.pk, item.pk]))
        self.assertEqual(get_day_schedule(self.group, self.day)[0], self.regular)

    def test_homework_is_created_for_group(self):
        deadline = (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            reverse("homework_create", args=[self.group.pk]),
            {"subject": self.math.pk, "title": "Задачи 1–5", "description": "", "deadline": deadline},
        )
        self.assertEqual(response.status_code, 302)
        homework = Homework.objects.get()
        self.assertEqual((homework.group, homework.teacher), (self.group, self.teacher))

        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("student_dashboard")), "Задачи 1–5")


def fake_gemini(text):
    """Подмена клиента Gemini: generate_content() возвращает ответ с .text."""
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text=text)
    return patch.object(ai_services, "_get_client", return_value=client), client


PARSED_JSON = json.dumps(
    {
        "group_name": "ПО-21",
        "lessons": [
            {"weekday": 1, "lesson_number": 1, "start_time": "8.30", "end_time": "10:00",
             "subject": "Математика", "room": "204", "teacher": "Иванова А.С.", "week_type": "every"},
            {"weekday": 1, "lesson_number": 2, "start_time": None, "end_time": None,
             "subject": "Веб-разработка", "room": "", "teacher": None, "week_type": "numerator"},
            {"weekday": 1, "lesson_number": 2, "start_time": None, "end_time": None,
             "subject": "физика", "room": "210", "teacher": None, "week_type": "denominator"},
            {"weekday": 1, "lesson_number": 2, "start_time": None, "end_time": None,
             "subject": "Дубль", "room": None, "teacher": None, "week_type": "denominator"},
        ],
    },
    ensure_ascii=False,
)


class ScheduleParsingTests(PortalTestCase):
    def test_parse_validates_and_normalizes(self):
        patcher, client = fake_gemini(PARSED_JSON)
        with patcher:
            parsed = parse_schedule_image(b"fake-image", "image/png", group_name="ПО-21")
        self.assertEqual(len(parsed.lessons), 4)
        self.assertEqual(parsed.lessons[0].start_time, "08:30")
        self.assertIsNone(parsed.lessons[1].room)

        call = client.models.generate_content.call_args.kwargs
        self.assertEqual(call["config"].response_mime_type, "application/json")
        self.assertIn("lessons", call["config"].response_json_schema["properties"])
        self.assertEqual(call["contents"][0].inline_data.mime_type, "image/png")

    def test_invalid_or_empty_answer_raises_friendly_error(self):
        for answer in ("не json", '{"group_name": null, "lessons": []}', ""):
            patcher, _ = fake_gemini(answer)
            with patcher, self.assertRaises(AIServiceError):
                parse_schedule_image(b"fake-image", "image/png")

    def test_apply_replaces_regular_schedule_and_keeps_replacements(self):
        ScheduleItem.objects.create(group=self.group, weekday=3, lesson_number=1, subject=self.math)
        replacement = ScheduleItem.objects.create(
            group=self.group, is_replacement=True, date=timezone.localdate(),
            lesson_number=1, subject=self.math,
        )
        count = apply_parsed_schedule(self.group, ParsedSchedule.model_validate_json(PARSED_JSON))

        self.assertEqual(count, 3)  # дубль слота отброшен
        regular = self.group.schedule_items.filter(is_replacement=False)
        self.assertEqual(regular.count(), 3)
        self.assertFalse(regular.filter(weekday=3).exists())
        self.assertTrue(ScheduleItem.objects.filter(pk=replacement.pk).exists())

        first = regular.get(lesson_number=1)
        self.assertEqual((first.start_time, first.teacher), (time(8, 30), self.teacher))
        # «физика» нашла существующий предмет «Физика», новый предмет создан один.
        self.assertEqual(regular.get(week_type="denominator").subject, self.physics)
        self.assertEqual(Subject.objects.count(), 3)

    def test_upload_preview_confirm_flow(self):
        self.client.force_login(self.teacher)
        upload_url = reverse("schedule_upload", args=[self.group.pk])
        preview_url = reverse("schedule_preview", args=[self.group.pk])
        image = SimpleUploadedFile("schedule.png", b"fake-image", content_type="image/png")

        patcher, _ = fake_gemini(PARSED_JSON)
        with patcher:
            response = self.client.post(upload_url, {"image": image})
        self.assertRedirects(response, preview_url)
        self.assertFalse(self.group.schedule_items.exists())  # до подтверждения база не тронута

        self.assertContains(self.client.get(preview_url), "Веб-разработка")
        self.assertRedirects(
            self.client.post(preview_url), reverse("teacher_group", args=[self.group.pk])
        )
        self.assertEqual(self.group.schedule_items.count(), 3)

    def test_upload_rejects_non_image(self):
        self.client.force_login(self.teacher)
        file = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        response = self.client.post(reverse("schedule_upload", args=[self.group.pk]), {"image": file})
        self.assertContains(response, "Нужна картинка")


class ChatBotTests(PortalTestCase):
    def test_prompt_contains_guardrail_knowledge_and_student_context(self):
        patcher, client = fake_gemini("Сессия начинается 14 декабря.")
        with patcher:
            answer = ask_college_bot("Когда сессия?", student_context="Группа ПО-21")
        self.assertEqual(answer, BotAnswer("Сессия начинается 14 декабря.", is_refusal=False))

        config = client.models.generate_content.call_args.kwargs["config"]
        self.assertIn(REFUSAL_TEXT, config.system_instruction)
        self.assertIn("Зимняя сессия", config.system_instruction)  # из базы знаний
        self.assertIn("Группа ПО-21", config.system_instruction)
        self.assertEqual(config.max_output_tokens, 1024)

    def test_refusal_is_detected(self):
        patcher, _ = fake_gemini(REFUSAL_TEXT)
        with patcher:
            self.assertTrue(ask_college_bot("Какая погода в Алматы?").is_refusal)

    def test_too_long_question_never_reaches_llm(self):
        patcher, client = fake_gemini("ответ")
        with patcher:
            answer = ask_college_bot("а" * 501)
        self.assertTrue(answer.is_refusal)
        client.models.generate_content.assert_not_called()

    def test_history_is_trimmed(self):
        history = [{"role": "user" if i % 2 == 0 else "model", "text": str(i)} for i in range(20)]
        patcher, client = fake_gemini("ок")
        with patcher:
            ask_college_bot("вопрос", history=history)
        contents = client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(len(contents), 2 * 3 + 1)  # CHAT_HISTORY_TURNS пар + новый вопрос
        self.assertEqual(contents[-1].parts[0].text, "вопрос")

    @override_settings(GEMINI_API_KEY="")
    def test_missing_key_gives_clear_error(self):
        ai_services._get_client.cache_clear()
        with self.assertRaisesMessage(AIServiceError, "GEMINI_API_KEY"):
            ask_college_bot("Когда сессия?")

    def test_student_context_lists_schedule_and_homework(self):
        today = timezone.localdate()
        ScheduleItem.objects.create(
            group=self.group, weekday=today.isoweekday(), lesson_number=1,
            subject=self.math, room="204",
        )
        Homework.objects.create(
            group=self.group, subject=self.math, title="Задачи 1–5",
            deadline=timezone.now() + timedelta(days=1),
        )
        context = build_student_context(self.student)
        for expected in ("ПО-21", "Математика", "ауд. 204", "Задачи 1–5"):
            self.assertIn(expected, context)


class ChatEndpointTests(PortalTestCase):
    def _ask(self, question):
        return self.client.post(
            reverse("student_chat"), json.dumps({"question": question}), content_type="application/json"
        )

    def test_answer_and_history(self):
        self.client.force_login(self.student)
        with patch("portal.views.ask_college_bot", return_value=BotAnswer("14 декабря")) as bot:
            response = self._ask("Когда сессия?")
            self._ask("А каникулы?")
        self.assertEqual(response.json(), {"answer": "14 декабря", "refused": False})
        # Второй вопрос ушёл уже с историей первого.
        self.assertEqual(len(bot.call_args.kwargs["history"]), 2)

    def test_refusal_is_not_saved_to_history(self):
        self.client.force_login(self.student)
        with patch("portal.views.ask_college_bot", return_value=BotAnswer(REFUSAL_TEXT, True)):
            response = self._ask("Напиши код на Python")
        self.assertEqual(response.json(), {"answer": REFUSAL_TEXT, "refused": True})
        self.assertNotIn("chat_history", self.client.session)

    @override_settings(CHAT_RATE_LIMIT=2)
    def test_rate_limit(self):
        self.client.force_login(self.student)
        with patch("portal.views.ask_college_bot", return_value=BotAnswer("ок")) as bot:
            statuses = [self._ask("Когда сессия?").status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
        self.assertEqual(bot.call_count, 2)

    def test_ai_failure_returns_readable_error(self):
        self.client.force_login(self.student)
        with patch("portal.views.ask_college_bot", side_effect=AIServiceError("ИИ недоступен")):
            response = self._ask("Когда сессия?")
        self.assertEqual((response.status_code, response.json()), (503, {"error": "ИИ недоступен"}))

    def test_teacher_and_get_are_rejected(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self._ask("вопрос").status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("student_chat")).status_code, 405)
