(function () {
  const section = document.querySelector("[data-ai-comment-url]");
  if (!section) return;

  // 서버 쪽 시간 제한과 재시도를 합친 것보다 넉넉하게 기다린다.
  const TIMEOUT_MS = 120000;

  section.hidden = false;

  fetch(section.dataset.aiCommentUrl, { method: "POST", signal: AbortSignal.timeout(TIMEOUT_MS) })
    .then((response) => (response.status === 200 ? response.text() : null))
    .then((html) => {
      if (html) {
        // 서버 템플릿이 값을 이스케이프한 조각이다.
        section.innerHTML = html;
      } else {
        section.hidden = true;
      }
    })
    .catch(() => {
      section.hidden = true;
    });
})();
