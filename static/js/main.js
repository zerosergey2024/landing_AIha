const callbackModal = document.getElementById("callbackModal");
const callbackFloat = document.getElementById("callbackFloat");
const headerCallback = document.getElementById("headerCallback");
const callWidgetOpen = document.getElementById("callWidgetOpen");
const modalClose = document.getElementById("modalClose");

const leadForm = document.getElementById("leadForm");
const formStatus = document.getElementById("formStatus");

const callbackForm = document.getElementById("callbackForm");
const callbackStatus = document.getElementById("callbackStatus");


function openCallbackModal() {
  if (!callbackModal) return;

  callbackModal.classList.add("is-open");
  callbackModal.setAttribute("aria-hidden", "false");
}


function closeCallbackModal() {
  if (!callbackModal) return;

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


if (callbackModal) {
  callbackModal.addEventListener("click", (event) => {
    if (event.target === callbackModal) {
      closeCallbackModal();
    }
  });
}


async function submitForm(form, statusNode, endpoint, successMessage) {
  if (!form) return;

  const payload = Object.fromEntries(new FormData(form).entries());

  if (statusNode) {
    statusNode.textContent = "Отправляем...";
  }

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const result = await response.json();

    if (!response.ok || !result.ok) {
      throw new Error(result.error || "Не удалось отправить заявку.");
    }

    form.reset();

    if (statusNode) {
      statusNode.textContent = successMessage;
    }

    return result;
  } catch (error) {
    if (statusNode) {
      statusNode.textContent = "Не удалось отправить заявку. Попробуйте позже.";
    }

    console.error("Form submit error:", error);
    return null;
  }
}


if (leadForm) {
  leadForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    await submitForm(
      leadForm,
      formStatus,
      "/api/leads",
      "Готово. Заявка сохранена, менеджер свяжется с вами."
    );
  });
}


if (callbackForm) {
  callbackForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const messageInput = callbackForm.querySelector('[name="message"]');

    if (!messageInput) {
      const hiddenMessage = document.createElement("input");
      hiddenMessage.type = "hidden";
      hiddenMessage.name = "message";
      hiddenMessage.value = "Запрос обратного звонка";
      callbackForm.appendChild(hiddenMessage);
    }

    await submitForm(
      callbackForm,
      callbackStatus,
      "/api/callback",
      "Запрос отправлен."
    );
  });
}