"""Настройки Smart College Portal (прототип для хакатона)."""

import os
from datetime import date, time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# --- Базовое -----------------------------------------------------------------

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
# В режиме отладки пускаем с любого адреса, чтобы открыть демо с телефона по Wi-Fi.
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*" if DEBUG else "localhost").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "portal.context_processors.college",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --- Пользователи и вход -----------------------------------------------------

AUTH_USER_MODEL = "portal.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"  # home сам разводит студента и преподавателя
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# --- Локаль ------------------------------------------------------------------

LANGUAGE_CODE = "ru"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Almaty")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Колледж -----------------------------------------------------------------

COLLEGE_NAME = os.getenv("COLLEGE_NAME", "Наш колледж")

# Любой день первой учебной недели семестра и тип этой недели.
# От них считается числитель/знаменатель (см. portal/utils.py).
SEMESTER_START = date.fromisoformat(os.getenv("SEMESTER_START", "2026-09-01"))
FIRST_WEEK_TYPE = os.getenv("FIRST_WEEK_TYPE", "numerator")  # или "denominator"

# Расписание звонков: номер пары -> (начало, конец).
# Используется, когда у пары в базе не указано время.
BELL_SCHEDULE = {
    1: (time(8, 30), time(10, 0)),
    2: (time(10, 10), time(11, 40)),
    3: (time(12, 10), time(13, 40)),
    4: (time(13, 50), time(15, 20)),
    5: (time(15, 30), time(17, 0)),
    6: (time(17, 10), time(18, 40)),
}

# База знаний чат-бота: обычный текстовый файл, правится без кода.
COLLEGE_KNOWLEDGE_FILE = BASE_DIR / "portal" / "knowledge" / "college.md"

# --- AI (Google Gemini) ------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-3.8-flash")
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
# Глубина «размышлений» модели: low — быстрее и дешевле.
# Пустая строка — не отправлять параметр (нужно для моделей Gemini 2.5).
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")
GEMINI_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))

# Защита чат-бота от нецелевого расхода токенов.
CHAT_MAX_QUESTION_LENGTH = 500  # символов в одном вопросе
CHAT_MAX_OUTPUT_TOKENS = 1024  # потолок ответа (включая «размышления»)
CHAT_HISTORY_TURNS = 3  # сколько прошлых пар «вопрос-ответ» помнит бот
CHAT_RATE_LIMIT = 15  # сообщений от одного студента...
CHAT_RATE_WINDOW = 10 * 60  # ...за это число секунд

MAX_SCHEDULE_IMAGE_SIZE = 8 * 1024 * 1024  # 8 МБ
