const callbackModal = document.getElementById("callbackModal");
const callbackFloat = document.getElementById("callbackFloat");
const headerCallback = document.getElementById("headerCallback");
const callWidgetOpen = document.getElementById("callWidgetOpen");
const modalClose = document.getElementById("modalClose");

function openCallbackModal() {
  callbackModal.classList.add("is-open");
  callbackModal.setAttribute("aria-hidden", "false");
}

function closeCallbackModal() {
  callbackModal.classList.remove("is-open");
  callbackModal.setAttribute("aria-hidden", "true");
}

[callbackFloat, headerCallback, callWidgetOpen].forEach((button) => {
  if (button) {
    button.addEventListener("click", openCallbackModal);
  }
});

if (modalClose) {
  modalClose.addEventListener("click", closeCallbackModal);
}

modalOpeners.forEach((button) => {
  button?.addEventListener('click', () => {
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
  });
});

modalClose?.addEventListener('click', () => {
  modal.classList.remove('is-open');
  modal.setAttribute('aria-hidden', 'true');
});

modal?.addEventListener('click', (event) => {
  if (event.target === modal) {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
  }
});

async function submitLead(form, statusNode) {
  const payload = Object.fromEntries(new FormData(form).entries());
  statusNode.textContent = 'Отправляем заявку...';

  try {
    const response = await fetch('/api/callback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || 'Не удалось отправить заявку.');
    }

    form.reset();
    statusNode.textContent = 'Готово. Заявка сохранена, менеджер свяжется с вами.';
  } catch (error) {
    statusNode.textContent = error.message;
  }
}

document.querySelector('#leadForm')?.addEventListener('submit', (event) => {
  event.preventDefault();
  submitLead(event.currentTarget, document.querySelector('#formStatus'));
});

document.querySelector('#callbackForm')?.addEventListener('submit', (event) => {
  event.preventDefault();
  submitLead(event.currentTarget, document.querySelector('#callbackStatus'));
});

const callbackForm = document.getElementById("callbackForm");
const callbackStatus = document.getElementById("callbackStatus");

if (callbackForm) {
  callbackForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const formData = new FormData(callbackForm);

    const payload = {
      name: formData.get("name"),
      phone: formData.get("phone"),
      message: "Запрос обратного звонка",
      source: "callback_widget"
    };

    const response = await fetch("/api/callback", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const result = await response.json();

    if (result.ok) {
      callbackStatus.textContent = "Запрос отправлен.";
      callbackForm.reset();
    } else {
      callbackStatus.textContent = result.error || "Ошибка отправки.";
    }
  });
}