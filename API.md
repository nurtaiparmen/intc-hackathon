# Smart College API — для мобильного приложения (React / React Native)

Базовый адрес после деплоя: `https://ЛОГИН.pythonanywhere.com/api/`
Локально: `http://127.0.0.1:8000/api/` (с телефона в той же Wi-Fi: `runserver 0.0.0.0:8000` и IP компьютера).

Все ответы — JSON. Ошибка всегда выглядит как `{"error": "текст для пользователя"}`
с кодом 400 / 401 / 403 / 404 / 429.

## Вход

```
POST /api/login/
{"username": "student", "password": "demo12345"}
```
Ответ:
```json
{"token": "eyJ1aWQiOjF9:...", "expires_in_days": 30,
 "user": {"id": 1, "username": "student", "name": "Самандар Айтматов", "role": "student",
          "group": {"id": 1, "name": "РПО6-24Р", "course": 3, "curators": ["Айкын Болатов"]}}}
```
Токен сохраните (например, в `AsyncStorage`) и передавайте в **каждом** запросе:
```
Authorization: Token <token>
```
Токен живёт 30 дней. Нет токена или он устарел — ответ `401`, нужно снова войти.
Cookies и CSRF не нужны.

## Адреса

| Метод | Адрес | Что вернёт |
|-------|-------|-----------|
| GET | `/api/` | список адресов (без входа) |
| GET | `/api/me/` | кто вошёл: роль, группа (у студента) или группы (у преподавателя) |
| GET | `/api/today/` | главный экран: дата, `week` (`left`/`right`), пары на сегодня, у студента `debts_count` и `upcoming_count` |
| GET | `/api/schedule/?date=2026-11-19&days=3` | пары на N дней подряд (1–14) с даты |
| GET | `/api/week/?date=2026-11-19` | вся неделя, в которую попадает дата |
| GET | `/api/homework/` | студент: `upcoming` и `debts`; преподаватель: задания группы и `debtors` |
| GET | `/api/groups/` | только преподаватель: его группы |
| POST | `/api/chat/` | вопрос ИИ-агенту |

Преподаватель может добавить `?group=<id>` к `today`, `schedule`, `week`, `homework`
(без него берётся его первая группа).

### Пара в ответе

```json
{"number": 2, "subject": "Архитектура ПО", "start": "09:30", "end": "10:50",
 "room": "709", "teacher": "Бычкова С.С.",
 "status": "replacement", "instead_of": "Анализ требований и управление проектами",
 "note": "Перенос: преподаватель на курсах повышения квалификации."}
```
`status`: `regular` — обычная, `replacement` — замена (подсветить красным), `cancelled` — отменена.
`number: 0` — кураторский час.

### ИИ-агент

```
POST /api/chat/
{"question": "какие пары завтра?",
 "history": [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "Привет! Чем помочь?"}]}
```
Ответ:
```json
{"answer": "Завтра у тебя 3 пары: ...", "actions": [], "offline": false, "refused": false}
```
- `history` — прошлые сообщения, приложение хранит их само (сервер берёт последние 6).
- `actions` — что агент реально изменил (у преподавателя: «Замена: …»). Если не пусто — обновите экраны.
- `offline: true` — ИИ недоступен, ответ в простом режиме.
- Лимит: 20 вопросов за 10 минут, иначе `429`.

## Пример на React Native

```js
const API = "https://ЛОГИН.pythonanywhere.com/api";

export async function login(username, password) {
  const r = await fetch(`${API}/login/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error);
  return data; // { token, user }
}

export async function api(path, token, body) {
  const r = await fetch(`${API}${path}`, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", Authorization: `Token ${token}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error);
  return data;
}

// const { token } = await login("student", "demo12345");
// const today = await api("/today/", token);
// const reply = await api("/chat/", token, { question: "чё по парам завтра?", history: [] });
```

Код API — `portal/api.py`, тесты — класс `ApiTests` в `portal/tests.py`.
