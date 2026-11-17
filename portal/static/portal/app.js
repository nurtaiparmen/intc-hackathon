(function () {
  // --- Тема: светлая / тёмная, выбор запоминается ---------------------------
  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
    });
  });

  // --- Активный пункт меню ---------------------------------------------------
  var path = location.pathname;
  function markNav(key) {
    document.querySelectorAll(".navitem").forEach(function (item) {
      item.classList.toggle("is-active", item.dataset.nav === key);
    });
  }
  var byPath = null;
  document.querySelectorAll("a.navitem").forEach(function (link) {
    var url = new URL(link.href, location.href);
    if (!link.dataset.tab && url.pathname === path) byPath = link.dataset.nav;
  });
  if (byPath) markNav(byPath);

  // --- Вкладки кабинета студента (Главная / Неделя / Задания / Профиль) ------
  var root = document.querySelector("[data-tabs]");
  if (root) {
    root.classList.add("js-tabs");
    var panels = root.querySelectorAll("[data-panel]");
    var names = Array.prototype.map.call(panels, function (p) { return p.dataset.panel; });
    function show(name, scroll) {
      if (names.indexOf(name) === -1) name = names[0];
      panels.forEach(function (p) { p.classList.toggle("is-shown", p.dataset.panel === name); });
      markNav(name);
      if (scroll) window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
    }
    function fromHash() {
      var hash = location.hash.replace("#", "");
      // Якоря внутри вкладок (#debts, #homework) открывают нужную вкладку.
      var target = hash && document.getElementById(hash);
      var panel = target && target.closest("[data-panel]");
      show(panel ? panel.dataset.panel : hash, !panel || panel === target);
    }
    window.addEventListener("hashchange", fromHash);
    fromHash();
  }

  // --- Загрузка фото/Excel с предпросмотром ----------------------------------
  var drop = document.querySelector("[data-drop]");
  if (drop) {
    var input = drop.querySelector("input[type=file]");
    var preview = drop.querySelector("[data-drop-preview]");
    var fileLabel = drop.querySelector("[data-drop-file]");
    function render() {
      var file = input.files && input.files[0];
      if (!file) return;
      fileLabel.textContent = file.name;
      fileLabel.hidden = false;
      if (file.type.indexOf("image/") === 0) {
        preview.src = URL.createObjectURL(file);
        preview.hidden = false;
      } else {
        preview.hidden = true;
      }
    }
    input.addEventListener("change", render);
    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function () { drop.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function () { drop.classList.remove("is-over"); });
    });
    // Вставка скриншота из буфера обмена (Ctrl+V)
    document.addEventListener("paste", function (event) {
      var items = (event.clipboardData || {}).files;
      if (items && items.length && items[0].type.indexOf("image/") === 0) {
        var dt = new DataTransfer();
        dt.items.add(items[0]);
        input.files = dt.files;
        render();
      }
    });
  }

  // --- Кнопка отправки показывает, что ИИ работает ---------------------------
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("[type=submit]");
      var loading = document.querySelector(form.dataset.busy);
      if (button) { button.disabled = true; }
      if (loading) loading.classList.add("is-on");
    });
  });
})();
