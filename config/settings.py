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
STATIC_ROOT = BASE_DIR / "staticfiles"  # для PythonAnywhere: python manage.py collectstatic
CSRF_TRUSTED_ORIGINS = [o for o in os.getenv("DJANGO_CSRF_TRUSTED", "").split(",") if o]
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Колледж -----------------------------------------------------------------

COLLEGE_NAME = os.getenv("COLLEGE_NAME", "Инновационный Технический Колледж")
COLLEGE_SHORT = os.getenv("COLLEGE_SHORT", "ИТК")
COLLEGE_CITY = os.getenv("COLLEGE_CITY", "г. Алматы")
APP_NAME = "Smart College"

# Любой день первой учебной недели семестра и тип этой недели.
# От них считается числитель/знаменатель (см. portal/utils.py).
SEMESTER_START = date.fromisoformat(os.getenv("SEMESTER_START", "2026-09-01"))
FIRST_WEEK_TYPE = os.getenv("FIRST_WEEK_TYPE", "numerator")  # или "denominator"

# Расписание звонков: номер пары -> (начало, конец).
# Используется, когда у пары в базе не указано время.
# 0 — кураторский час (во вторник 08:00–08:25). Во вторник пары сдвинуты на
# 30 минут — у таких пар время хранится в самой записи расписания.
BELL_SCHEDULE = {
    0: (time(8, 0), time(8, 25)),
    1: (time(8, 0), time(9, 20)),
    2: (time(9, 30), time(10, 50)),
    3: (time(11, 0), time(12, 20)),
    4: (time(12, 30), time(13, 50)),
    5: (time(14, 0), time(15, 20)),
    6: (time(15, 30), time(16, 50)),
}

# База знаний чат-бота: обычный текстовый файл, правится без кода.
COLLEGE_KNOWLEDGE_FILE = BASE_DIR / "portal" / "knowledge" / "college.md"

# --- ИИ-агент ---------------------------------------------------------------
# Любой OpenAI-совместимый API. По умолчанию Google Gemini (бесплатный ключ:
# https://aistudio.google.com/apikey) — он же читает фото расписания.
# Можно и Groq: LLM_BASE_URL=https://api.groq.com/openai/v1 (но без фото).
# Без ключа агент работает в запасном режиме по ключевым словам.

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Для удобства можно вписать просто GEMINI_API_KEY — тогда всё настроится само.
_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", GEMINI_BASE_URL)
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or _GEMINI_KEY
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")
# Если основная модель перегружена или упёрлась в лимит, запрос уходит в следующую.
LLM_FALLBACK_MODELS = [
    name.strip()
    for name in os.getenv(
        "LLM_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash,gemini-flash-lite-latest"
    ).split(",")
    if name.strip()
]
# Модель умеет смотреть на картинки (фото расписания уходит прямо в неё).
LLM_VISION = os.getenv("LLM_VISION", "1") == "1"
MAX_UPLOAD_MB = 8
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "1"))
LLM_RETRY_DELAY = float(os.getenv("LLM_RETRY_DELAY", "2"))
AGENT_MAX_STEPS = 6  # сколько раз подряд агент может вызвать инструменты за один вопрос

# Защита чат-бота от нецелевого расхода токенов.
CHAT_MAX_QUESTION_LENGTH = 500  # символов в одном вопросе
CHAT_MAX_OUTPUT_TOKENS = 1024  # потолок одного ответа модели
CHAT_HISTORY_TURNS = 3  # сколько прошлых пар «вопрос-ответ» помнит бот
CHAT_RATE_LIMIT = 20  # сообщений от одного пользователя...
CHAT_RATE_WINDOW = 10 * 60  # ...за это число секунд

MAX_SCHEDULE_TEXT_LENGTH = 8000
