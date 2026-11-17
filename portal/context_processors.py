from django.conf import settings

from . import llm


def college(request):
    context = {
        "COLLEGE_NAME": settings.COLLEGE_NAME,
        "COLLEGE_SHORT": settings.COLLEGE_SHORT,
        "COLLEGE_CITY": settings.COLLEGE_CITY,
        "APP_NAME": settings.APP_NAME,
        "CHAT_MAX_QUESTION_LENGTH": settings.CHAT_MAX_QUESTION_LENGTH,
        "AI_ON": llm.is_configured(),
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and getattr(user, "is_teacher", False):
        # Для нижнего меню преподавателя: его первая группа.
        context["nav_group"] = user.teaching_groups.first()
    return context
