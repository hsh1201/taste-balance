(function () {
  const container = document.querySelector("[data-radar]");
  // CDN을 못 불러오면 차트 없이 둔다. 같은 내용을 아래 축 목록에서 볼 수 있다.
  if (!container || !window.Chart) return;

  const axes = JSON.parse(document.getElementById("radar-data").textContent);
  const rootStyle = getComputedStyle(document.documentElement);
  const cssColor = (name) => rootStyle.getPropertyValue(name).trim();
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // 값은 50(중간)~100(한쪽 끝)이다. 0부터 그리면 차이가 안 보이고 50부터 그리면
  // 중간인 축이 한 점으로 모이므로, 그 아래에서 시작한다.
  const SCALE_MIN = 30;

  function draw() {
    container.hidden = false;
    new window.Chart(document.getElementById("radar-chart"), {
      type: "radar",
      data: {
        labels: axes.map((axis) => axis.lean_label),
        datasets: [
          {
            data: axes.map((axis) => axis.lean_value),
            backgroundColor: "rgba(230, 62, 102, 0.22)",
            borderColor: cssColor("--primary"),
            borderWidth: 3,
            pointBackgroundColor: cssColor("--primary"),
            pointRadius: 4,
          },
        ],
      },
      options: {
        animation: reduceMotion ? false : { duration: 700 },
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          r: {
            min: SCALE_MIN,
            max: 100,
            ticks: { display: false, stepSize: 35 },
            grid: { color: cssColor("--line") },
            angleLines: { color: cssColor("--line") },
            pointLabels: {
              color: cssColor("--ink"),
              font: { family: "'Pretendard Variable', Pretendard, sans-serif", size: 15, weight: "700" },
            },
          },
        },
      },
    });
  }

  // 캔버스 글자는 글꼴이 준비된 뒤에 그려야 Pretendard로 나온다.
  document.fonts.ready.then(draw);
})();
