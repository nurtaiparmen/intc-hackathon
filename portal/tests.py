"""Тесты идут без интернета и без ключа: вызовы модели подменены."""

import json
from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:  # новые версии openai перешли на httpx2
    import httpx2 as httpx
except ImportError:
    import httpx
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openai import APIStatusError

from . import llm
from .agent import AgentContext, parse_day, run_agent
from .ai_services import AIServiceError, ParsedSchedule, apply_parsed_schedule, parse_schedule_text
from .models import Group, Homework, ScheduleItem, Subject, Submission, User, WeekType
from .utils import get_day_schedule, get_week_number, get_week_type, group_debtors, student_homework_status

LLM_ON = override_settings(
    LLM_API_KEY="test-key", LLM_MODEL="main-model", LLM_FALLBACK_MODELS=["spare-model"],
    LLM_RETRIES=1, LLM_RETRY_DELAY=0,
)


def text_reply(text):
    return SimpleNamespace(content=text, tool_calls=None)


def tool_reply(name, arguments, call_id="call-1"):
    call = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
    return SimpleNamespace(content="", tool_calls=[call])


def status_error(code):
    request = httpx.Request("POST", "https://llm.test/chat/completions")
    return APIStatusError("error", response=httpx.Response(code, request=request), body=None)


def fake_llm(*replies):
    """Подмена клиента: каждый вызов create() отдаёт следующий ответ или бросает ошибку."""
    client = MagicMock()
    effects = [
        reply if isinstance(reply, Exception) else SimpleNamespace(choices=[SimpleNamespace(message=reply)])
        for reply in replies
    ]
    client.chat.completions.create.side_effect = effects
    llm.get_client.cache_clear()
    return patch.object(llm, "get_client", return_value=client), client


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
        self.assertEqual(first.times, settings.BELL_SCHEDULE[1])


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


REFUSAL = "Я отвечаю только на вопросы, связанные с нашим колледжем."


@LLM_ON
class LLMClientTests(PortalTestCase):
    def test_overload_is_retried_then_fallback_model_is_used(self):
        patcher, client = fake_llm(status_error(503), status_error(503), text_reply("ok"))
        with patcher:
            self.assertEqual(llm.complete([{"role": "user", "content": "hi"}]).content, "ok")
        models = [call.kwargs["model"] for call in client.chat.completions.create.call_args_list]
        self.assertEqual(models, ["main-model", "main-model", "spare-model"])

    def test_all_models_down_gives_readable_error(self):
        patcher, client = fake_llm(*[status_error(503)] * 4)
        with patcher, self.assertRaisesMessage(llm.LLMError, "не отвечает"):
            llm.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(client.chat.completions.create.call_count, 4)

    def test_bad_key_stops_immediately(self):
        patcher, client = fake_llm(status_error(401))
        with patcher, self.assertRaisesMessage(llm.LLMError, "Ключ"):
            llm.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_thinking_tags_are_removed(self):
        self.assertEqual(llm.clean_text("<think>хм</think> Ответ"), "Ответ")


@LLM_ON
class ScheduleParsingTests(PortalTestCase):
    def test_parse_validates_normalizes_and_skips_broken_rows(self):
        data = json.loads(PARSED_JSON)
        data["lessons"].append({"weekday": 9, "lesson_number": 1, "subject": "Мусор"})
        patcher, client = fake_llm(text_reply("```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"))
        with patcher:
            parsed = parse_schedule_text("Пн 1 Математика 204", group_name="ПО-21")
        self.assertEqual(len(parsed.lessons), 4)
        self.assertEqual(parsed.lessons[0].start_time, "08:30")
        call = client.chat.completions.create.call_args.kwargs
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertIn("ПО-21", call["messages"][1]["content"])

    def test_invalid_or_empty_answer_raises_friendly_error(self):
        for answer in ("не json", '{"group_name": null, "lessons": []}'):
            patcher, _ = fake_llm(text_reply(answer))
            with patcher, self.assertRaises(AIServiceError):
                parse_schedule_text("текст")

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
        patcher, _ = fake_llm(text_reply(PARSED_JSON))
        with patcher:
            response = self.client.post(upload_url, {"text": "Пн: 1 Математика 204"})
        self.assertRedirects(response, preview_url)
        self.assertFalse(self.group.schedule_items.exists())

        self.assertContains(self.client.get(preview_url), "Веб-разработка")
        self.assertRedirects(self.client.post(preview_url), reverse("teacher_group", args=[self.group.pk]))
        self.assertEqual(self.group.schedule_items.count(), 3)

    def test_empty_upload_asks_for_photo_or_text(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("schedule_upload", args=[self.group.pk]), {"text": ""})
        self.assertContains(response, "Загрузите фото или вставьте текст")


class AgentToolTests(PortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student.first_name, cls.student.last_name = "Алексей", "Смирнов"
        cls.student.save()
        ScheduleItem.objects.create(group=cls.group, weekday=1, lesson_number=2, subject=cls.math, room="204")
        cls.monday = timezone.localdate() + timedelta(days=(1 - timezone.localdate().isoweekday()) % 7 or 7)

    def run_tool(self, user, name, **arguments):
        return AgentContext(user).run(name, json.dumps(arguments, ensure_ascii=False))

    def test_parse_day(self):
        today = timezone.localdate()
        self.assertEqual(parse_day("завтра"), today + timedelta(days=1))
        self.assertEqual(parse_day("2026-12-01"), date(2026, 12, 1))
        self.assertEqual(parse_day("01.12.2026"), date(2026, 12, 1))
        self.assertEqual(parse_day("понедельник").isoweekday(), 1)

    def test_student_reads_schedule_of_own_group(self):
        result = self.run_tool(self.student, "get_schedule", date=self.monday.isoformat())
        self.assertEqual(result["group"], "ПО-21")
        self.assertEqual(result["days"][0]["lessons"][0]["subject"], "Математика")

    def test_student_cannot_use_teacher_tools(self):
        result = self.run_tool(self.student, "make_replacement", date=self.monday.isoformat(),
                               lesson_number=2, subject="Физика")
        self.assertIn("error", result)
        self.assertFalse(ScheduleItem.objects.filter(is_replacement=True).exists())

    def test_teacher_makes_replacement_with_fuzzy_subject_and_self(self):
        ctx = AgentContext(self.teacher)
        result = ctx.run("make_replacement", json.dumps(
            {"date": self.monday.isoformat(), "lesson_number": 2, "subject": "физику", "teacher": "я", "room": "305"}
        ))
        self.assertTrue(result["ok"], result)
        item = ScheduleItem.objects.get(is_replacement=True)
        self.assertEqual((item.subject, item.teacher, item.room), (self.physics, self.teacher, "305"))
        self.assertIn("Замена", ctx.actions[0])
        lessons = get_day_schedule(self.group, self.monday)
        self.assertEqual(lessons[0].subject, self.physics)

    def test_teacher_cancels_and_restores_lesson(self):
        self.assertTrue(self.run_tool(self.teacher, "cancel_lesson", date=self.monday.isoformat(), lesson_number=2)["ok"])
        self.assertTrue(get_day_schedule(self.group, self.monday)[0].is_cancelled)
        self.assertTrue(self.run_tool(self.teacher, "remove_replacement", date=self.monday.isoformat(), lesson_number=2)["ok"])
        self.assertFalse(get_day_schedule(self.group, self.monday)[0].is_replacement)

    def test_form_validation_reaches_the_model(self):
        result = self.run_tool(self.teacher, "cancel_lesson", date=self.monday.isoformat(), lesson_number=5)
        self.assertIn("отменять нечего", result["error"])
        result = self.run_tool(self.teacher, "cancel_lesson", date="2020-01-01", lesson_number=2)
        self.assertIn("прошла", result["error"])

    def test_teacher_cannot_touch_foreign_group(self):
        result = self.run_tool(self.teacher, "get_debtors", group="ИС-11")
        self.assertIn("не закреплена", result["error"])

    def test_add_homework_and_mark_submission(self):
        deadline = (timezone.localdate() + timedelta(days=3)).isoformat()
        result = self.run_tool(self.teacher, "add_homework", subject="математика", title="Задачи 20–25", deadline=deadline)
        homework = Homework.objects.get()
        self.assertEqual((homework.subject, homework.teacher), (self.math, self.teacher))
        self.assertEqual(timezone.localtime(homework.deadline).hour, 18)

        result = self.run_tool(self.teacher, "mark_submissions", homework="задачи 20", students=["Смирнов"])
        self.assertEqual(result["submitted_now"], "1 из 1")
        self.assertTrue(Submission.objects.filter(homework=homework, student=self.student).exists())
        result = self.run_tool(self.teacher, "mark_submissions", homework=str(homework.pk), students=["Петров"])
        self.assertIn("Не нашёл", result["error"])

    def test_set_and_delete_regular_lesson(self):
        result = self.run_tool(self.teacher, "set_regular_lesson", weekday=3, lesson_number=1,
                               subject="Физика", week="left", room="210")
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.group.schedule_items.filter(weekday=3, week_type=WeekType.NUMERATOR).exists())
        self.assertTrue(self.run_tool(self.teacher, "delete_regular_lesson", weekday=3, lesson_number=1, week="left")["ok"])


@LLM_ON
class AgentLoopTests(PortalTestCase):
    def test_agent_calls_tool_then_answers(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        patcher, client = fake_llm(
            tool_reply("get_schedule", {"date": tomorrow}),
            text_reply("Завтра пар нет, отдыхай."),
        )
        with patcher:
            reply = run_agent(self.student, "чё по парам завтра")
        self.assertEqual(reply.text, "Завтра пар нет, отдыхай.")
        second_call = client.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertEqual(second_call[-1]["role"], "tool")
        self.assertIn("ПО-21", second_call[-1]["content"])
        tool_names = {t["function"]["name"] for t in client.chat.completions.create.call_args.kwargs["tools"]}
        self.assertNotIn("make_replacement", tool_names)

    def test_teacher_action_is_reported(self):
        ScheduleItem.objects.create(group=self.group, weekday=timezone.localdate().isoweekday(), lesson_number=1, subject=self.math)
        patcher, _ = fake_llm(
            tool_reply("cancel_lesson", {"date": timezone.localdate().isoformat(), "lesson_number": 1}),
            text_reply("Готово, 1 пара сегодня отменена."),
        )
        with patcher:
            reply = run_agent(self.teacher, "отмени сегодня первую пару")
        self.assertEqual(len(reply.actions), 1)
        self.assertIn("Отменена", reply.actions[0])

    def test_prompt_has_dates_week_side_and_knowledge(self):
        patcher, client = fake_llm(text_reply(REFUSAL))
        with patcher:
            reply = run_agent(self.student, "расскажи анекдот")
        system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn(timezone.localdate().isoformat(), system)
        self.assertIn("левая", system)
        self.assertIn("Пересдачи", system)
        self.assertTrue(reply.is_refusal)

    def test_llm_failure_falls_back_to_offline_answer(self):
        patcher, _ = fake_llm(*[status_error(503)] * 4)
        with patcher:
            reply = run_agent(self.student, "какая сейчас неделя")
        self.assertTrue(reply.offline)
        self.assertIn("учебная неделя", reply.text)

    def test_too_long_question_never_reaches_llm(self):
        patcher, client = fake_llm()
        with patcher:
            reply = run_agent(self.student, "а" * 600)
        self.assertTrue(reply.is_refusal)
        client.chat.completions.create.assert_not_called()


@override_settings(LLM_API_KEY="")
class OfflineAgentTests(PortalTestCase):
    def test_schedule_week_and_help(self):
        ScheduleItem.objects.create(group=self.group, weekday=timezone.localdate().isoweekday(), lesson_number=1,
                                    subject=self.math, room="204")
        reply = run_agent(self.student, "какие пары сегодня?")
        self.assertTrue(reply.offline)
        self.assertIn("Математика, ауд. 204", reply.text)
        self.assertIn("неделя", run_agent(self.student, "какая неделя").text)
        self.assertIn("простые вопросы", run_agent(self.student, "привет").text)

    def test_teacher_sees_debtors(self):
        self.assertIn("ПО-21", run_agent(self.teacher, "кто должники?").text)


class AgentEndpointTests(PortalTestCase):
    url = reverse("agent_chat")

    def post(self, question):
        return self.client.post(self.url, json.dumps({"question": question}), content_type="application/json")

    @LLM_ON
    def test_answer_actions_and_history(self):
        self.client.force_login(self.teacher)
        patcher, _ = fake_llm(text_reply("Ваша группа: ПО-21."))
        with patcher:
            data = self.post("мои группы").json()
        self.assertEqual((data["answer"], data["actions"], data["offline"]), ("Ваша группа: ПО-21.", [], False))
        self.assertEqual(len(self.client.session["chat_history"]), 2)

    @override_settings(LLM_API_KEY="", CHAT_RATE_LIMIT=2)
    def test_rate_limit(self):
        self.client.force_login(self.student)
        statuses = [self.post("какая неделя").status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])

    def test_get_and_anonymous_are_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_chat_widget_on_pages(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("teacher_dashboard")), "ИИ-агент")
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("student_dashboard")), "ИИ-агент")


@override_settings(SEMESTER_START=date(2026, 1, 1))
class DebtTests(PortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.classmate = User.objects.create_user(
            "classmate", password="pass12345", role=User.Role.STUDENT, study_group=cls.group, last_name="Ким"
        )
        now = timezone.now()
        cls.overdue = Homework.objects.create(
            group=cls.group, subject=cls.math, title="Старая задача", deadline=now - timedelta(days=3)
        )
        cls.future = Homework.objects.create(
            group=cls.group, subject=cls.physics, title="Новая задача", deadline=now + timedelta(days=3)
        )
        Submission.objects.create(homework=cls.overdue, student=cls.classmate)

    def test_overdue_without_mark_is_a_debt(self):
        status = student_homework_status(self.student)
        self.assertEqual([h.title for h in status["debts"]], ["Старая задача"])
        self.assertEqual(status["by_subject"][0]["subject"], "Физика")
        self.assertEqual([entry["student"] for entry in group_debtors(self.group)], [self.student])

    def test_student_sees_debts_and_bot_knows_them(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("student_dashboard"))
        self.assertContains(response, "Мои долги")
        self.assertContains(response, "Старая задача")
        result = AgentContext(self.student).run("get_my_debts", "{}")
        self.assertEqual(result["debts"][0]["title"], "Старая задача")

    def test_teacher_marks_submission_and_debt_disappears(self):
        self.client.force_login(self.teacher)
        url = reverse("homework_check", args=[self.group.pk, self.overdue.pk])
        self.assertContains(self.client.get(url), "сдали 1 из 2")
        outsider = User.objects.create_user("outsider", role=User.Role.STUDENT, study_group=self.other_group)
        response = self.client.post(url, {"done": [self.student.pk, outsider.pk]})
        self.assertRedirects(response, reverse("teacher_group", args=[self.group.pk]))
        self.assertEqual(
            set(self.overdue.submissions.values_list("student_id", flat=True)), {self.student.pk}
        )
        self.assertEqual(student_homework_status(self.student)["debts"], [])
        self.assertEqual([e["student"] for e in group_debtors(self.group)], [self.classmate])

    def test_teacher_group_page_lists_debtors(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("teacher_group", args=[self.group.pk]))
        self.assertContains(response, "Должники")
        self.assertContains(response, "сдали 1 из 2")


class ScheduleEditorTests(PortalTestCase):
    def test_add_update_and_delete_regular_lesson(self):
        self.client.force_login(self.teacher)
        url = reverse("schedule_edit", args=[self.group.pk])
        lesson = {"weekday": 2, "lesson_number": 3, "week_type": "numerator", "subject": "  физика ", "room": "210"}
        self.assertRedirects(self.client.post(url, lesson), url)
        self.assertRedirects(self.client.post(url, {**lesson, "room": "305"}), url)

        item = self.group.schedule_items.get()
        self.assertEqual((item.subject, item.room, item.week_type), (self.physics, "305", "numerator"))
        self.assertContains(self.client.get(url), "Левая")

        delete_url = reverse("schedule_item_delete", args=[self.group.pk, item.pk])
        self.assertRedirects(self.client.post(delete_url), url)
        self.assertFalse(self.group.schedule_items.exists())

    def test_new_subject_is_created(self):
        self.client.force_login(self.teacher)
        self.client.post(
            reverse("schedule_edit", args=[self.group.pk]),
            {"weekday": 1, "lesson_number": 1, "week_type": "every", "subject": "Робототехника"},
        )
        self.assertTrue(Subject.objects.filter(name="Робототехника").exists())

    def test_student_cannot_edit(self):
        self.client.force_login(self.student)
        self.assertRedirects(
            self.client.get(reverse("schedule_edit", args=[self.group.pk])),
            reverse("home"),
            target_status_code=302,
        )


@LLM_ON
class VisionAndExcelTests(PortalTestCase):
    """Версия 4: фото уходит прямо в Gemini, Excel читается openpyxl."""

    def test_photo_upload_goes_to_vision_model(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.teacher)
        photo = SimpleUploadedFile("schedule.png", b"\x89PNG fake", content_type="image/png")
        patcher, client = fake_llm(text_reply(PARSED_JSON))
        with patcher:
            response = self.client.post(reverse("schedule_upload", args=[self.group.pk]), {"file": photo})
        self.assertRedirects(response, reverse("schedule_preview", args=[self.group.pk]))
        content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_wrong_file_type_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.teacher)
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        response = self.client.post(reverse("schedule_upload", args=[self.group.pk]), {"file": bad})
        self.assertContains(response, "Подходят фото")

    def test_excel_is_converted_to_text_with_merged_days(self):
        import io

        from openpyxl import Workbook

        from .ai_services import excel_to_text

        book = Workbook()
        sheet = book.active
        sheet.append(["Понедельник", 1, "Структуры данных", "602"])
        sheet.append([None, 2, "Физкультура", "спортзал"])
        sheet.merge_cells("A1:A2")
        buffer = io.BytesIO()
        book.save(buffer)
        buffer.seek(0)
        text = excel_to_text(buffer)
        self.assertIn("Понедельник | 1 | Структуры данных | 602", text)
        self.assertIn("Понедельник | 2 | Физкультура", text)

    def test_weekday_words_kazakh_and_curator_hour(self):
        from .ai_services import ParsedLesson

        lesson = ParsedLesson.model_validate(
            {"weekday": "Сейсенбі", "lesson_number": 0, "subject": "Кураторский час", "week_type": None}
        )
        self.assertEqual((lesson.weekday, lesson.lesson_number, lesson.week_type), (2, 0, "every"))

    def test_gemini_thought_signature_is_sent_back(self):
        from .agent import _call_dict

        call = SimpleNamespace(
            id="c1", function=SimpleNamespace(name="get_week", arguments="{}"),
            extra_content={"google": {"thought_signature": "abc"}},
        )
        self.assertEqual(_call_dict(call)["extra_content"], {"google": {"thought_signature": "abc"}})


class AboutPageTests(TestCase):
    def test_about_is_public_and_lists_team(self):
        response = self.client.get(reverse("about"))
        for name in ("Пармен Нуртай", "Алтынбек Султан", "Исмаилов Асанали", "Айтматов Самандар", "РПО6-24Р"):
            self.assertContains(response, name)


class FirstNameMatchTests(PortalTestCase):
    def test_student_found_by_first_name_only(self):
        from .agent import _best_match, _norm

        s = User.objects.create_user("asanali", first_name="Асанали", last_name="Исмаилов",
                                     role=User.Role.STUDENT, study_group=self.group)
        options = {}
        for key in (s.last_name, s.first_name, s.get_full_name(), f"{s.last_name} {s.first_name}", s.username):
            options.setdefault(_norm(key), s)
        self.assertEqual(_best_match("Асанали", options, "студент"), s)
        self.assertEqual(_best_match("Исмаил", options, "студент"), s)


@LLM_ON
class ApiTests(PortalTestCase):
    """JSON API для мобильного приложения."""

    def _token(self, username, password="pass12345"):
        user = User.objects.get(username=username)
        user.set_password(password)
        user.save()
        response = self.client.post(reverse("api_login"), {"username": username, "password": password},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def _get(self, name, token, **params):
        return self.client.get(reverse(name), params, HTTP_AUTHORIZATION=f"Token {token}")

    def test_login_errors_and_token_required(self):
        bad = self.client.post(reverse("api_login"), {"username": "x", "password": "y"}, content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(reverse("api_me")).status_code, 401)
        self.assertEqual(self.client.get(reverse("api_me"), HTTP_AUTHORIZATION="Token мусор").status_code, 401)
        self.assertEqual(self.client.get(reverse("api_index"))["Access-Control-Allow-Origin"], "*")

    def test_student_schedule_today_homework(self):
        ScheduleItem.objects.create(group=self.group, weekday=timezone.localdate().isoweekday(),
                                    lesson_number=1, subject=self.math, room="204")
        token = self._token(self.student.username)
        me = self._get("api_me", token).json()
        self.assertEqual((me["role"], me["group"]["name"]), ("student", self.group.name))
        today = self._get("api_today", token).json()
        self.assertEqual(today["lessons"][0]["subject"], "Математика")
        self.assertIn(today["week"], {"left", "right"})
        days = self._get("api_schedule", token, days=3).json()["days"]
        self.assertEqual(len(days), 3)
        self.assertTrue(self._get("api_week", token).json()["days"])
        self.assertIn("debts", self._get("api_homework", token).json())
        self.assertEqual(self._get("api_groups", token).status_code, 403)

    def test_teacher_groups_and_foreign_group(self):
        token = self._token(self.teacher.username)
        self.assertEqual(self._get("api_groups", token).json()["groups"][0]["name"], self.group.name)
        self.assertEqual(self._get("api_schedule", token, group=self.other_group.pk).status_code, 404)
        self.assertIn("debtors", self._get("api_homework", token).json())

    def test_chat_with_history(self):
        token = self._token(self.student.username)
        patcher, client = fake_llm(text_reply("Завтра две пары."))
        with patcher:
            response = self.client.post(
                reverse("api_chat"),
                {"question": "пары завтра?", "history": [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "Привет!"}]},
                content_type="application/json", HTTP_AUTHORIZATION=f"Token {token}",
            )
        self.assertEqual(response.json()["answer"], "Завтра две пары.")
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
