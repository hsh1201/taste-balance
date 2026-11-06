(function () {
  const root = document.getElementById("game");
  if (!root) return;

  const PICK_MS = 280;
  const DRAG_START_PX = 10;
  const SWIPE_PICK_PX = 56;
  const DRAG_FOLLOW_RATIO = 0.25;

  const questions = JSON.parse(document.getElementById("game-data").textContent);
  const storageKey = `tb:${root.dataset.slug}`;

  const screens = Object.fromEntries(
    [...root.querySelectorAll("[data-screen]")].map((element) => [element.dataset.screen, element]),
  );
  const startButton = root.querySelector("[data-action=start]");
  const backButton = root.querySelector("[data-action=back]");
  const retryButton = root.querySelector("[data-action=retry]");
  const stage = root.querySelector("[data-stage]");
  const choiceButtons = [...stage.querySelectorAll("[data-choice]")];
  const questionText = root.querySelector("[data-question]");
  const countText = root.querySelector("[data-count]");
  const progress = root.querySelector("[data-progress]");
  const progressFill = progress.firstElementChild;
  const hint = root.querySelector("[data-hint]");

  let answers = loadSavedAnswers();
  let index = 0;
  let locked = false;

  function loadSavedAnswers() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      const isValid =
        Array.isArray(saved) &&
        saved.length <= questions.length &&
        saved.every((choice) => choice === 0 || choice === 1);
      return isValid ? saved : [];
    } catch {
      return [];
    }
  }

  function saveAnswers() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(answers));
    } catch {
      // 저장소를 못 쓰는 브라우저에서도 게임은 계속할 수 있다.
    }
  }

  function clearSavedAnswers() {
    try {
      localStorage.removeItem(storageKey);
    } catch {
      // 지우지 못해도 다음 접속 때 서버가 "이미 완료" 화면을 보여준다.
    }
  }

  function showScreen(name) {
    Object.entries(screens).forEach(([key, element]) => {
      element.hidden = key !== name;
    });
  }

  function setProgress(answered) {
    progressFill.style.width = `${(answered / questions.length) * 100}%`;
    progress.setAttribute("aria-valuenow", String(answered));
  }

  function renderQuestion() {
    const question = questions[index];
    questionText.textContent = question.text;
    countText.textContent = String(index + 1);
    setProgress(index);
    backButton.classList.toggle("is-hidden", index === 0);
    hint.classList.toggle("is-hidden", index !== 0);

    choiceButtons.forEach((button, choice) => {
      button.classList.remove("is-picked", "is-dropped", "is-leaning");
      button.classList.toggle("was-picked", answers[index] === choice);
      button.querySelector(".choice-emoji").textContent = question.choices[choice].emoji;
      button.querySelector(".choice-label").textContent = question.choices[choice].label;
    });

    // 클래스를 다시 붙여도 애니메이션이 재생되도록 레이아웃을 한 번 읽는다.
    stage.classList.remove("is-entering");
    void stage.offsetWidth;
    stage.classList.add("is-entering");
  }

  function pick(choice) {
    if (locked) return;
    locked = true;
    answers[index] = choice;
    saveAnswers();
    setProgress(index + 1);
    choiceButtons.forEach((button, buttonChoice) => {
      button.classList.remove("was-picked", "is-leaning");
      button.classList.add(buttonChoice === choice ? "is-picked" : "is-dropped");
    });

    setTimeout(() => {
      locked = false;
      if (index === questions.length - 1) {
        submitAnswers();
        return;
      }
      index += 1;
      renderQuestion();
    }, PICK_MS);
  }

  async function submitAnswers() {
    showScreen("waiting");
    try {
      const response = await fetch(`/g/${root.dataset.slug}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answers: questions.map((question, position) => ({
            question_id: question.id,
            choice: answers[position],
          })),
        }),
      });
      if (response.ok) {
        const { next } = await response.json();
        clearSavedAnswers();
        window.location.replace(next);
        return;
      }
      // 이미 응답했거나 기간이 지났다면 서버가 알맞은 화면을 보여준다.
      if (response.status === 409 || response.status === 410) {
        clearSavedAnswers();
        window.location.reload();
        return;
      }
      showScreen("error");
    } catch {
      showScreen("error");
    }
  }

  // 스와이프: 손가락을 민 쪽의 카드를 고른다.
  let drag = null;
  let suppressClick = false;

  function setLean(dx) {
    choiceButtons[0].classList.toggle("is-leaning", dx <= -SWIPE_PICK_PX);
    choiceButtons[1].classList.toggle("is-leaning", dx >= SWIPE_PICK_PX);
  }

  function finishDrag(shouldPick) {
    if (!drag) return;
    const { active, dx } = drag;
    drag = null;
    if (!active) return;
    stage.style.transform = "";
    setLean(0);
    // 드래그 직후 따라오는 click 이벤트만 무시한다.
    setTimeout(() => {
      suppressClick = false;
    }, 50);
    if (shouldPick && Math.abs(dx) >= SWIPE_PICK_PX) pick(dx < 0 ? 0 : 1);
  }

  stage.addEventListener("pointerdown", (event) => {
    if (locked) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, dx: 0, active: false };
  });

  stage.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.active) {
      if (Math.abs(dx) < DRAG_START_PX || Math.abs(dx) < Math.abs(dy)) return;
      drag.active = true;
      suppressClick = true;
      stage.setPointerCapture(event.pointerId);
    }
    drag.dx = dx;
    stage.style.transform = `translateX(${dx * DRAG_FOLLOW_RATIO}px)`;
    setLean(dx);
  });

  stage.addEventListener("pointerup", () => finishDrag(true));
  stage.addEventListener("pointercancel", () => finishDrag(false));

  choiceButtons.forEach((button, choice) => {
    button.addEventListener("click", () => {
      if (!suppressClick) pick(choice);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (screens.play.hidden) return;
    if (event.key === "ArrowLeft") pick(0);
    if (event.key === "ArrowRight") pick(1);
  });

  backButton.addEventListener("click", () => {
    if (locked || index === 0) return;
    index -= 1;
    renderQuestion();
  });

  startButton.addEventListener("click", () => {
    index = Math.min(answers.length, questions.length - 1);
    showScreen("play");
    renderQuestion();
  });

  retryButton.addEventListener("click", submitAnswers);

  if (answers.length > 0) startButton.textContent = "이어서 하기";
})();
