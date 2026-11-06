(function () {
  const card = document.querySelector("[data-persona-card]");
  if (!card) return;

  const WIDTH = 1080;
  const HEIGHT = 1350;
  const MARGIN = 70;
  const TEXT_FONT = '"Pretendard Variable", Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif';
  const EMOJI_FONT = '"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif';
  const INK = "#2b2440";
  const INK_SOFT = "#6a6279";
  const TAGLINE_FONT = `500 44px ${TEXT_FONT}`;
  const TAGLINE_LINE = 64;
  const TRAIT_FONT = `700 36px ${TEXT_FONT}`;
  const TRAIT_PADDING_X = 34;
  const TRAIT_HEIGHT = 80;
  const TRAIT_GAP = 16;
  const MAX_EXTRA_HEIGHT = TAGLINE_LINE + TRAIT_HEIGHT + TRAIT_GAP;

  const saveButton = document.querySelector("[data-action=save-image]");
  const shareButton = document.querySelector("[data-action=share-image]");
  const preview = document.querySelector("[data-image-preview]");

  const { name, emoji, tagline } = card.dataset;
  const traits = JSON.parse(card.dataset.traits);
  const title = `${name}형!`;
  const fileName = `취향밸런스-${name.replaceAll(" ", "")}.png`;

  function roundedRect(context, x, y, width, height, radius) {
    context.beginPath();
    context.moveTo(x + radius, y);
    context.arcTo(x + width, y, x + width, y + height, radius);
    context.arcTo(x + width, y + height, x, y + height, radius);
    context.arcTo(x, y + height, x, y, radius);
    context.arcTo(x, y, x + width, y, radius);
    context.closePath();
  }

  function wrapByWord(context, text, maxWidth) {
    const lines = [];
    let line = "";
    text.split(" ").forEach((word) => {
      const candidate = line ? `${line} ${word}` : word;
      if (line && context.measureText(candidate).width > maxWidth) {
        lines.push(line);
        line = word;
      } else {
        line = candidate;
      }
    });
    if (line) lines.push(line);
    return lines;
  }

  function layoutTraitRows(context, maxWidth) {
    context.font = TRAIT_FONT;
    const rows = [[]];
    let rowWidth = 0;
    traits.forEach((trait) => {
      const label = `#${trait}`;
      const width = context.measureText(label).width + TRAIT_PADDING_X * 2;
      if (rowWidth > 0 && rowWidth + TRAIT_GAP + width > maxWidth) {
        rows.push([]);
        rowWidth = 0;
      }
      rows[rows.length - 1].push({ label, width });
      rowWidth += (rowWidth > 0 ? TRAIT_GAP : 0) + width;
    });
    return rows;
  }

  function drawTraitRows(context, rows, centerX, startY, accent) {
    context.font = TRAIT_FONT;
    rows.forEach((row, rowIndex) => {
      const total = row.reduce((sum, chip) => sum + chip.width, 0) + TRAIT_GAP * (row.length - 1);
      let x = centerX - total / 2;
      const y = startY + rowIndex * (TRAIT_HEIGHT + TRAIT_GAP);
      row.forEach((chip) => {
        roundedRect(context, x, y, chip.width, TRAIT_HEIGHT, TRAIT_HEIGHT / 2);
        context.fillStyle = accent;
        context.fill();
        context.fillStyle = "#ffffff";
        context.textBaseline = "middle";
        context.fillText(chip.label, x + chip.width / 2, y + TRAIT_HEIGHT / 2 + 2);
        context.textBaseline = "alphabetic";
        x += chip.width + TRAIT_GAP;
      });
    });
  }

  async function drawCard() {
    const allText = [title, tagline, "당신은", "취향 밸런스", ...traits, "#"].join("");
    // 폰트를 못 받아도 기본 글꼴로 카드를 만들 수 있게 실패를 무시한다.
    await Promise.allSettled(
      [500, 700, 800].map((weight) => document.fonts.load(`${weight} 40px "Pretendard Variable"`, allText)),
    );

    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = HEIGHT;
    const context = canvas.getContext("2d");
    const styles = getComputedStyle(card);
    const background = styles.getPropertyValue("--persona-bg").trim();
    const accent = styles.getPropertyValue("--persona-accent").trim();
    const centerX = WIDTH / 2;
    const innerWidth = WIDTH - MARGIN * 2 - 120;

    context.fillStyle = background;
    context.fillRect(0, 0, WIDTH, HEIGHT);
    roundedRect(context, MARGIN, MARGIN, WIDTH - MARGIN * 2, HEIGHT - MARGIN * 2, 64);
    context.fillStyle = "#ffffff";
    context.fill();

    context.textAlign = "center";

    context.font = TAGLINE_FONT;
    const taglineLines = wrapByWord(context, tagline, innerWidth);
    const traitRows = layoutTraitRows(context, innerWidth);

    // 문구 길이가 달라도 내용이 세로 중앙에 오도록 전체를 내려 그린다.
    const extraHeight = (taglineLines.length - 1) * TAGLINE_LINE + (traitRows.length - 1) * (TRAIT_HEIGHT + TRAIT_GAP);
    const offset = Math.max(0, (MAX_EXTRA_HEIGHT - extraHeight) / 2);

    context.fillStyle = INK;
    context.font = `220px ${EMOJI_FONT}`;
    context.fillText(emoji, centerX, 360 + offset);

    context.font = `700 48px ${TEXT_FONT}`;
    context.fillStyle = INK_SOFT;
    context.fillText("당신은", centerX, 490 + offset);

    context.font = `800 104px ${TEXT_FONT}`;
    context.fillStyle = accent;
    context.fillText(title, centerX, 610 + offset);

    context.font = TAGLINE_FONT;
    context.fillStyle = INK;
    let y = 740 + offset;
    taglineLines.forEach((line) => {
      context.fillText(line, centerX, y);
      y += TAGLINE_LINE;
    });

    drawTraitRows(context, traitRows, centerX, y + 16, accent);

    context.font = `700 34px ${TEXT_FONT}`;
    context.fillStyle = INK_SOFT;
    context.fillText("취향 밸런스", centerX, HEIGHT - MARGIN - 56);

    return canvas;
  }

  async function createImageBlob() {
    const canvas = await drawCard();
    return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  }

  // iOS Safari는 탭과 같은 흐름에서만 공유를 허용하므로, 탭하기 전에 이미지를 미리 만들어 둔다.
  let imageBlob = null;
  const imageReady = createImageBlob().then((blob) => {
    imageBlob = blob;
    return blob;
  });

  function showPreview(blob) {
    const image = preview.querySelector("img");
    if (!image.src) image.src = URL.createObjectURL(blob);
    preview.hidden = false;
  }

  async function saveImage() {
    const blob = imageBlob || (await imageReady);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = fileName;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    // 인앱 브라우저는 다운로드를 막기도 해서 길게 눌러 저장할 수 있게 이미지도 보여준다.
    showPreview(blob);
  }

  async function shareImage() {
    const blob = imageBlob || (await imageReady);
    const file = new File([blob], fileName, { type: "image/png" });
    try {
      await navigator.share({ files: [file], title: `나는 ${name}형!` });
    } catch (error) {
      // 사용자가 공유 창을 닫은 경우는 오류가 아니다.
      if (error.name !== "AbortError") showPreview(blob);
    }
  }

  saveButton.addEventListener("click", saveImage);

  const probeFile = new File(["x"], "probe.png", { type: "image/png" });
  if (navigator.canShare && navigator.canShare({ files: [probeFile] })) {
    shareButton.hidden = false;
    shareButton.addEventListener("click", shareImage);
  }
})();
