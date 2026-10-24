// 놀이판 그림. 지금은 멍멍이(Chomp)만 있다. 아기 공룡과 물건은 M2(Task 15)에서 더한다.
//
// "우리 멍멍이"는 공식 그림이 아니라 직접 그린 SVG 다(개인·비상업 팬아트로만 쓴다). 기본 모습은 kids-games/index.html 94–110행:
// 남색 동그란 쇠공 몸, 크고 반짝이는 하얀 눈, 크게 벌린 입 안에 하얀 세모 이빨, 몸 아래 회색 쇠사슬.
// 무섭지 않게 눈은 크고 동글동글하게. 멍멍이는 왼쪽을 본다.
//
// 문자열만 만드는 순수 함수라 node 로 시험한다. 표정 이름은 표에서 고르기만 하고 그림에 끼워 넣지 않는다(주입 방지).

const BODY = { cx: 95, cy: 95, r: 70 };
const NAVY = "#23306b";

// 입: 몸 중심에서 왼쪽으로 벌어진 쐐기. 크게 벌림(평소)과 거의 다묾(말할 때 번갈아) 두 단계.
const MOUTHS = {
  open: { half: 35, apex: [110, 95], teeth: [20, 20, 15], lowerTeeth: [20, 20] },
  closed: { half: 12, apex: [84, 95], teeth: [11, 10, 9], lowerTeeth: [11, 10] },
};

function fmt(n) {
  return String(Math.round(n * 10) / 10);
}

function onRim(deg) {
  const a = (deg * Math.PI) / 180;
  return [BODY.cx + BODY.r * Math.cos(a), BODY.cy + BODY.r * Math.sin(a)];
}

// 입술 선(입 가장자리 edge → 안쪽 꼭짓점 apex)을 따라 세모 이빨을 세운다. 이빨 끝은 입 안쪽(가운데 줄 y=95)을 향한다.
function teethAlong(edge, apex, sizes, startGap) {
  const dx = apex[0] - edge[0];
  const dy = apex[1] - edge[1];
  const len = Math.hypot(dx, dy);
  const ux = dx / len;
  const uy = dy / len;
  // 두 수직 방향 중 입 안쪽(가운데 줄 쪽)을 고른다.
  let nx = -uy;
  let ny = ux;
  if (Math.sign(ny) !== Math.sign(BODY.cy - edge[1])) {
    nx = -nx;
    ny = -ny;
  }
  let s = startGap;
  const out = [];
  for (const size of sizes) {
    const a = [edge[0] + ux * s, edge[1] + uy * s];
    const b = [edge[0] + ux * (s + size), edge[1] + uy * (s + size)];
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const tip = [mid[0] + nx * size * 0.9, mid[1] + ny * size * 0.9];
    out.push(`<path d="M${fmt(a[0])} ${fmt(a[1])} L${fmt(b[0])} ${fmt(b[1])} L${fmt(tip[0])} ${fmt(tip[1])} Z"/>`);
    s += size + 2;
  }
  return out.join("");
}

function mouthSvg(open) {
  const m = open ? MOUTHS.open : MOUTHS.closed;
  const upper = onRim(180 + m.half);
  const lower = onRim(180 - m.half);
  const [ax, ay] = m.apex;
  const wedge = `<path d="M${ax} ${ay} L${fmt(upper[0])} ${fmt(upper[1])} A${BODY.r} ${BODY.r} 0 0 0 ${fmt(lower[0])} ${fmt(lower[1])} Z" fill="#5a0f12"/>`;
  // 아래 이빨은 반 칸 늦게 시작해 위 이빨과 엇갈린다.
  const teeth = teethAlong(upper, m.apex, m.teeth, 3) + teethAlong(lower, m.apex, m.lowerTeeth, 3 + m.lowerTeeth[0] / 2);
  return `${wedge}<g fill="#fff">${teeth}</g>`;
}

// 눈(과 볼). 눈 흰자 가운데 (116, 54). 눈이 몸 위쪽 끝에 붙어 있어 눈썹은 그리지 않고,
// 표정마다 눈동자 위치·크기, 반짝임, 눈꺼풀, 볼터치를 바꾼다.
const EYE_WHITE = '<ellipse cx="116" cy="54" rx="22" ry="24" fill="#fff"/>';
const pupil = (x, y, r) => `<circle cx="${x}" cy="${y}" r="${r}" fill="#111"/>`;
const shine = (x, y, r) => `<circle cx="${x}" cy="${y}" r="${r}" fill="#fff"/>`;
const LID_LINE = 'stroke="#0e1640" stroke-width="4" stroke-linecap="round"';

const FACES = {
  happy: EYE_WHITE + pupil(110, 57, 10) + shine(106, 52, 3.5) + shine(114, 62, 1.6),
  excited: EYE_WHITE + pupil(110, 55, 13) + shine(104, 48, 5) + shine(116, 61, 2.6) + shine(113, 50, 1.6),
  surprised: '<ellipse cx="116" cy="52" rx="25" ry="28" fill="#fff"/>' + pupil(114, 54, 6) + shine(112, 51, 2),
  sleepy:
    EYE_WHITE + pupil(110, 64, 9) +
    `<path d="M93 57 A23 28 0 0 1 139 57 Z" fill="${NAVY}"/>` + // 내려온 눈꺼풀(흰자 위쪽을 다 덮는다)
    `<path d="M93 57 L139 57" ${LID_LINE}/>`,
  sad:
    EYE_WHITE + pupil(108, 63, 9) + shine(105, 59, 3) +
    // 뒤쪽으로 처진 눈꺼풀: 흰자 테두리(215°→10°) 위쪽 호와 비스듬한 줄로 닫아 눈 밖으로 나가지 않는다
    `<path d="M98 40.2 A22 24 0 0 1 137.7 58.2 Z" fill="${NAVY}"/>` +
    `<path d="M98 40.2 L137.7 58.2" ${LID_LINE}/>`,
  shy:
    EYE_WHITE + pupil(106, 62, 9) + shine(103, 58, 3) +
    '<ellipse cx="134" cy="92" rx="14" ry="8" fill="#ff8fb1" opacity=".75"/>', // 볼터치
};

export const CHOMP_FACES = Object.freeze(Object.keys(FACES));

export function chompSvg({ mouthOpen = true, face = "happy" } = {}) {
  const faceName = Object.hasOwn(FACES, face) ? face : "happy";
  const mouth = mouthOpen ? "open" : "closed";
  return (
    `<svg class="chomp-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200" ` +
    `data-face="${faceName}" data-mouth="${mouth}" aria-hidden="true">` +
    // 쇠사슬(몸 오른쪽 아래)
    '<g fill="none" stroke="#8a8f99" stroke-width="7">' +
    '<ellipse cx="146" cy="160" rx="10" ry="6" transform="rotate(45 146 160)"/>' +
    '<ellipse cx="160" cy="174" rx="6" ry="10" transform="rotate(45 160 174)"/>' +
    '<ellipse cx="174" cy="188" rx="10" ry="6" transform="rotate(45 174 188)"/>' +
    "</g>" +
    // 쇠공 몸과 반짝임
    `<circle cx="${BODY.cx}" cy="${BODY.cy}" r="${BODY.r}" fill="${NAVY}"/>` +
    '<ellipse cx="80" cy="50" rx="28" ry="14" fill="#5566b8" opacity=".55" transform="rotate(-25 80 50)"/>' +
    mouthSvg(mouthOpen) +
    FACES[faceName] +
    "</svg>"
  );
}
