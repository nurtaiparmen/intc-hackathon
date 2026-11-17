# Как выложить Smart College на PythonAnywhere (бесплатно)

Займёт ~15 минут. Везде, где написано `ЛОГИН`, подставьте свой логин на PythonAnywhere
(например, если логин `rpo624r`, сайт будет `rpo624r.pythonanywhere.com`).

ИИ на бесплатном тарифе работает: сервер Gemini (`*.googleapis.com`) есть в белом списке PythonAnywhere.

## 1. Аккаунт

1. Откройте https://www.pythonanywhere.com → **Pricing & signup** → **Create a Beginner account** (бесплатно).
2. Подтвердите почту.

## 2. Загрузить проект

1. На компьютере возьмите архив `_materials/smart_college_deploy.zip` (в нём уже нет `venv` и лишних файлов, но есть `.env` с ключом Gemini).
2. На PythonAnywhere: вкладка **Files** → кнопка **Upload a file** → выберите `smart_college_deploy.zip`.
   Он окажется в `/home/ЛОГИН/`.

## 3. Установить (вкладка Consoles → Bash)

Откройте **Consoles** → **Bash** и вставляйте команды по одной:

```bash
cd ~
unzip -o smart_college_deploy.zip -d smart_college
cd smart_college
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Последняя команда печатает много строк и заканчивается `Successfully installed Django-... openai-... openpyxl-...`.

Теперь настройте `.env` под сервер (замените ЛОГИН на свой!):

```bash
sed -i 's/^DJANGO_DEBUG=.*/DJANGO_DEBUG=0/' .env
echo "DJANGO_ALLOWED_HOSTS=ЛОГИН.pythonanywhere.com" >> .env
echo "DJANGO_CSRF_TRUSTED=https://ЛОГИН.pythonanywhere.com" >> .env
```

База, демо-данные и статика:

```bash
python manage.py migrate
python manage.py seed_demo
python manage.py collectstatic --noinput
```

Ожидаемо: `Демо-данные группы РПО6-24Р готовы.` и `... static files copied to '/home/ЛОГИН/smart_college/staticfiles'`.

## 4. Веб-приложение (вкладка Web)

1. **Web** → **Add a new web app** → Next → **Manual configuration** (не «Django»!) → **Python 3.12** → Next.
2. В разделе **Virtualenv** впишите: `/home/ЛОГИН/smart_college/venv` и нажмите галочку.
3. В разделе **Code**: *Source code* — `/home/ЛОГИН/smart_college`.
4. Там же нажмите на ссылку **WSGI configuration file** (`/var/www/ЛОГИН_pythonanywhere_com_wsgi.py`),
   удалите всё содержимое и вставьте (заменив ЛОГИН):

   ```python
   import os
   import sys

   path = "/home/ЛОГИН/smart_college"
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```

   Нажмите **Save**.
5. Раздел **Static files** → *Enter URL* `/static/`, *Enter path* `/home/ЛОГИН/smart_college/staticfiles`.
6. Вверху страницы зелёная кнопка **Reload ЛОГИН.pythonanywhere.com**.

Откройте `https://ЛОГИН.pythonanywhere.com` — появится страница входа ИТК.
Демо-входы (пароль `demo12345`): `student`, `teacher`, `admin`, `asanali`, `sultan`, `nurtay`.

## Если что-то не так

- **Something went wrong / ошибка 500** — вкладка **Web** → внизу ссылка **Error log**, смотрите последние строки.
- **DisallowedHost** — в `.env` неправильно вписан `DJANGO_ALLOWED_HOSTS` (без `https://`, только `ЛОГИН.pythonanywhere.com`). После правки — **Reload**.
- **Сайт без стилей** — не сделан `collectstatic` или неверный путь в Static files.
- **ИИ отвечает «простой режим»** — проверьте, что в `.env` есть `GEMINI_API_KEY=...`, и нажмите **Reload**.
- **Перезапустить демо-данные перед защитой** (вернуть расписание и долги как было):
  ```bash
  cd ~/smart_college && source venv/bin/activate && python manage.py seed_demo
  ```
- Бесплатный сайт нужно продлевать раз в 3 месяца кнопкой **Run until 3 months from today** на вкладке Web.

## Для Султана (React)

После деплоя API для мобильного приложения доступен по `https://ЛОГИН.pythonanywhere.com/api/`.
Вход по токену, все адреса и пример на React Native — в [API.md](API.md).
Проверка в браузере: откройте `https://ЛОГИН.pythonanywhere.com/api/` — должен показаться список адресов.
