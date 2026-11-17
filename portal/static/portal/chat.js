(function () {
  const chat = document.getElementById("chat");
  const toggle = document.getElementById("chat-toggle");
  if (!chat || !toggle) return;

  const log = chat.querySelector(".chat__log");
  const form = chat.querySelector(".chat__form");
  const input = form.querySelector("input");
  const sendButton = form.querySelector("button");
  const storeKey = "agent-chat-" + chat.dataset.user;
  const openKey = storeKey + "-open";

  function load() {
    try { return JSON.parse(sessionStorage.getItem(storeKey)) || []; } catch (e) { return []; }
  }
  function save(items) {
    try { sessionStorage.setItem(storeKey, JSON.stringify(items.slice(-30))); } catch (e) {}
  }
  let saved = load();

  function setOpen(open) {
    chat.hidden = !open;
    toggle.hidden = open;
    try { sessionStorage.setItem(openKey, open ? "1" : ""); } catch (e) {}
    if (open) { input.focus(); log.scrollTop = log.scrollHeight; } else toggle.focus();
  }

  function render(item) {
    const message = document.createElement("div");
    message.className = "msg msg--" + item.kind;
    const text = document.createElement("p");
    text.textContent = item.text;
    message.appendChild(text);
    if (item.actions && item.actions.length) {
      const list = document.createElement("ul");
      list.className = "msg__actions";
      item.actions.forEach(function (action) {
        const li = document.createElement("li");
        li.textContent = action;
        list.appendChild(li);
      });
      message.appendChild(list);
    }
    if (item.offline) {
      const tag = document.createElement("span");
      tag.className = "msg__tag";
      tag.textContent = "без ИИ";
      message.appendChild(tag);
    }
    log.appendChild(message);
    log.scrollTop = log.scrollHeight;
    return message;
  }

  function remember(item) {
    saved.push(item);
    save(saved);
    return render(item);
  }

  async function ask(question) {
    remember({ kind: "user", text: question });
    const pending = render({ kind: "pending", text: "Думаю…" });
    input.disabled = sendButton.disabled = true;
    let reload = false;
    try {
      const response = await fetch(chat.dataset.url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": chat.dataset.csrf },
        body: JSON.stringify({ question: question }),
      });
      const data = await response.json();
      pending.remove();
      if (data.error) {
        remember({ kind: "error", text: data.error });
      } else {
        remember({
          kind: data.refused ? "bot msg--refused" : "bot",
          text: data.answer,
          actions: data.actions,
          offline: data.offline,
        });
        reload = data.actions && data.actions.length > 0;
      }
    } catch (error) {
      pending.remove();
      remember({ kind: "error", text: "Вопрос не отправился. Проверьте интернет и попробуйте ещё раз." });
    } finally {
      input.disabled = sendButton.disabled = false;
      input.focus();
    }
    if (reload) {
      render({ kind: "pending", text: "Обновляю страницу, чтобы показать изменения…" });
      setTimeout(function () { window.location.reload(); }, 1500);
    }
  }

  saved.forEach(render);
  if (sessionStorage.getItem(openKey)) setOpen(true);

  toggle.addEventListener("click", function () { setOpen(true); });
  chat.querySelector(".chat__close").addEventListener("click", function () { setOpen(false); });
  chat.querySelector(".chat__clear").addEventListener("click", function () {
    saved = [];
    save(saved);
    log.querySelectorAll(".msg").forEach(function (node) { node.remove(); });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !chat.hidden) setOpen(false);
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    ask(question);
  });

  chat.querySelectorAll("[data-question]").forEach(function (chip) {
    chip.addEventListener("click", function () { ask(chip.dataset.question); });
  });
})();
