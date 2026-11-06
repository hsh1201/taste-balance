(function () {
  const COPIED_LABEL_MS = 1500;

  async function copyText(text, button) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    // https가 아닌 주소에서는 clipboard API를 쓸 수 없어 입력칸을 선택해 복사한다.
    const input = button.parentElement.querySelector("input");
    input.select();
    if (!document.execCommand("copy")) throw new Error("copy failed");
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    const label = button.textContent;
    let timer;

    button.addEventListener("click", async () => {
      try {
        await copyText(button.dataset.copy, button);
        button.textContent = "복사됨!";
      } catch {
        button.textContent = "직접 복사";
      }
      clearTimeout(timer);
      timer = setTimeout(() => {
        button.textContent = label;
      }, COPIED_LABEL_MS);
    });
  });

  document.querySelectorAll(".link-input").forEach((input) => {
    input.addEventListener("focus", () => input.select());
  });
})();
