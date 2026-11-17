// Виджет чат-помощника: открытие/закрытие и отправка вопроса на сервер.
(function () {
  const chat = document.getElementById("chat");
  const toggle = document.getElementById("chat-toggle");
  if (!chat || !toggle) return;

  const log = chat.querySelector(".chat__log");
  const form = chat.querySelector(".chat__form");
  const input = form.querySelector("input");
  const sendButton = form.querySelector("button");

  function setOpen(open) {
    chat.hidden = !open;
    toggle.hidden = open;
    if (open) input.focus();
    else toggle.focus();
  }

  function addMessage(text, kind) {
    const message = document.createElement("div");
    message.className = "msg msg--" + kind;
    message.textContent = text; // textContent: ответ модели не исполняется как HTML
    log.appendChild(message);
    log.scrollTop = log.scrollHeight;
    return message;
  }

  async function ask(question) {
    addMessage(question, "user");
    const reply = addMessage("Ищу ответ…", "pending");
    input.disabled = sendButton.disabled = true;
    try {
      const response = await fetch(chat.dataset.url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": chat.dataset.csrf },
        body: JSON.stringify({ question }),
      });
      const data = await response.json();
      if (data.error) {
        reply.className = "msg msg--error";
        reply.textContent = data.error;
      } else {
        reply.className = "msg msg--bot" + (data.refused ? " msg--refused" : "");
        reply.textContent = data.answer;
      }
    } catch (error) {
      reply.className = "msg msg--error";
      reply.textContent = "Вопрос не отправился. Проверьте интернет и попробуйте ещё раз.";
    } finally {
      input.disabled = sendButton.disabled = false;
      input.focus();
      log.scrollTop = log.scrollHeight;
    }
  }

  toggle.addEventListener("click", () => setOpen(true));
  chat.querySelector(".chat__close").addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !chat.hidden) setOpen(false);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    ask(question);
  });

  chat.querySelectorAll("[data-question]").forEach((chip) => {
    chip.addEventListener("click", () => ask(chip.dataset.question));
  });
})();
