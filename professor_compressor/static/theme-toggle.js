// Theme choice: System (follows the device), Light or Dark. Offered as a radio
// group in session page footers and as a menu button in the website header.
// The choice is kept only in this browser's localStorage and is never sent to
// the server. Any failure leaves the page on the System theme and never affects
// the rest of the page.
const STORAGE_KEY = "pc-theme";
const NAMES = { system: "System", light: "Light", dark: "Dark" };

function savedChoice() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch (_) {
    return "system";
  }
}

function saveChoice(choice) {
  try {
    if (choice === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, choice);
  } catch (_) {
    /* Storage is unavailable: the choice lasts until the page is closed. */
  }
}

// Apply a choice to the page and show it in every theme control.
function showChoice(choice) {
  if (choice === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  for (const input of document.querySelectorAll(".theme-switch input")) {
    input.checked = input.value === choice;
  }
  const label = "Theme: " + NAMES[choice];
  for (const button of document.querySelectorAll(".theme-menu-button")) {
    button.dataset.choice = choice;
    button.setAttribute("aria-label", label);
    button.title = label;
  }
  for (const item of document.querySelectorAll('.theme-menu-list [role="menuitemradio"]')) {
    item.setAttribute("aria-checked", String(item.dataset.choice === choice));
  }
}

function choose(choice) {
  saveChoice(choice);
  showChoice(choice);
}

function setUpSwitch(group) {
  group.addEventListener("change", (event) => {
    if (event.target.checked && NAMES[event.target.value]) choose(event.target.value);
  });
  group.hidden = false;
}

// Menu button pattern: Enter, Space, click or Down Arrow opens the menu on the
// checked item; arrow keys, Home and End move; Enter or Space chooses; Escape,
// Tab or a click outside closes it.
function setUpMenu(menu) {
  const button = menu.querySelector(".theme-menu-button");
  const list = menu.querySelector(".theme-menu-list");
  const items = Array.from(list.querySelectorAll('[role="menuitemradio"]'));

  function open(focusIndex) {
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    const checked = items.findIndex((item) => item.getAttribute("aria-checked") === "true");
    items[focusIndex ?? Math.max(0, checked)].focus();
  }

  function close(returnFocus) {
    if (list.hidden) return;
    list.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }

  button.addEventListener("click", () => {
    if (list.hidden) open();
    else close(true);
  });
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      open(event.key === "ArrowUp" ? items.length - 1 : undefined);
    }
  });
  for (const item of items) {
    item.addEventListener("click", () => {
      choose(item.dataset.choice);
      close(true);
    });
  }
  list.addEventListener("keydown", (event) => {
    const index = items.indexOf(document.activeElement);
    const moves = {
      ArrowDown: (index + 1) % items.length,
      ArrowUp: (index - 1 + items.length) % items.length,
      Home: 0,
      End: items.length - 1,
    };
    if (event.key in moves) {
      event.preventDefault();
      items[moves[event.key]].focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "Tab") {
      close(false);
    }
  });
  document.addEventListener("click", (event) => {
    if (!list.hidden && !menu.contains(event.target)) close(true);
  });
  menu.hidden = false;
}

try {
  showChoice(savedChoice());
  for (const group of document.querySelectorAll(".theme-switch")) setUpSwitch(group);
  for (const menu of document.querySelectorAll(".theme-menu")) setUpMenu(menu);
  // Keep other open pages of this site in step with a change made elsewhere.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY || event.key === null) showChoice(savedChoice());
  });
} catch (_) {
  /* The theme controls are optional; the page keeps working without them. */
}
