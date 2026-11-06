(function () {
  const slider = document.querySelector("[data-range-slider]");
  if (!slider) return;

  const [minInput, maxInput] = slider.querySelectorAll("input[type=range]");
  const fill = slider.querySelector(".range-fill");
  const output = document.getElementById("budget-output");
  const floor = Number(minInput.min);
  const ceiling = Number(minInput.max);
  const step = Number(minInput.step);

  const percent = (value) => ((value - floor) / (ceiling - floor)) * 100;
  const toWon = (value) => `${value / 10000}만 원`;

  function render() {
    const low = Number(minInput.value);
    const high = Number(maxInput.value);
    fill.style.left = `${percent(low)}%`;
    fill.style.right = `${100 - percent(high)}%`;
    output.textContent = `${toWon(low)} ~ ${toWon(high)}`;
    // 두 핸들이 오른쪽 끝에서 겹쳐도 최소 핸들을 잡을 수 있게 한다.
    minInput.style.zIndex = percent(low) > 50 ? "3" : "1";
    maxInput.style.zIndex = "2";
  }

  minInput.addEventListener("input", () => {
    minInput.value = Math.min(Number(minInput.value), Number(maxInput.value) - step);
    render();
  });
  maxInput.addEventListener("input", () => {
    maxInput.value = Math.max(Number(maxInput.value), Number(minInput.value) + step);
    render();
  });

  render();
})();
