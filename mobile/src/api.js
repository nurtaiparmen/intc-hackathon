// Клиент API Smart College. Описание адресов — API.md в корне проекта.
export const API_URL = "https://smartcollege.pythonanywhere.com/api";

async function request(path, { token, body } = {}) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: body ? "POST" : "GET",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Token ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error("Нет связи с сервером. Проверьте интернет.");
  }
  let data = {};
  try {
    data = await response.json();
  } catch (e) {
    // сервер вернул не JSON (например, страница ошибки)
  }
  if (!response.ok) {
    const error = new Error(data.error || `Ошибка сервера (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export const login = (username, password) => request("/login/", { body: { username, password } });
export const getToday = (token) => request("/today/", { token });
export const getWeek = (token, date) => request(`/week/${date ? `?date=${date}` : ""}`, { token });
export const getHomework = (token) => request("/homework/", { token });
export const getMe = (token) => request("/me/", { token });
export const askAgent = (token, question, history) =>
  request("/chat/", { token, body: { question, history } });
