from django.conf import settings


def college(request):
    return {
        "COLLEGE_NAME": settings.COLLEGE_NAME,
        "CHAT_MAX_QUESTION_LENGTH": settings.CHAT_MAX_QUESTION_LENGTH,
    }
