// 놀이판 그림: 멍멍이(Chomp), 아기 공룡(Baby Dino), 물건. 모두 직접 그린 SVG 와 시스템 글꼴 이모지다(공식 그림 없음).
//
// "우리 멍멍이"는 공식 그림이 아니라 직접 그린 SVG 다(개인·비상업 팬아트로만 쓴다). 기본 모습은 kids-games/index.html 94–110행:
// 남색 동그란 쇠공 몸, 크고 반짝이는 하얀 눈, 크게 벌린 입 안에 하얀 세모 이빨, 몸 아래 회색 쇠사슬.
// 무섭지 않게 눈은 크고 동글동글하게. 멍멍이는 왼쪽을 본다.
//
// 아기 공룡(dinoSvg)은 초록 몸, 큰 머리와 큰 눈, 주황 등 돌기. 오른쪽(멍멍이 쪽)을 본다. 부위마다 <g data-part="…"> 그룹이고
// 그룹의 transform-origin 이 그 부위의 관절이다(poses.js 의 약속). 렌더러는 그룹의 CSS transform 만 바꾼다.
// 물건(objectSvg)은 layout.js 의 footprint 상자 크기로 그린다. 크기(big/small)는 layout 이 상자째 키우므로 여기서는 적어만 둔다.
//
// 문자열만 만드는 순수 함수라 node 로 시험한다. 표정 이름과 색은 표에서 고르기만 하고 그림에 끼워 넣지 않는다(주입 방지).
// 이모지만 카탈로그에서 오므로 글자를 이스케이프한다.
import { footprint } from "./layout.js";

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

// ================================================================ 색

/** 카탈로그의 색 8가지. fill = 칠, dark = 테두리와 무늬. 이 표에 없는 색 이름은 그 물건의 기본색이 된다. */
export const PALETTE = Object.freeze({
  red: Object.freeze({ fill: "#ff5a5f", dark: "#c73c41" }),
  blue: Object.freeze({ fill: "#4d8dff", dark: "#2c64c9" }),
  yellow: Object.freeze({ fill: "#ffd23f", dark: "#d9a90f" }),
  green: Object.freeze({ fill: "#4cc46a", dark: "#2f9149" }),
  orange: Object.freeze({ fill: "#ff9f43", dark: "#d7761a" }),
  purple: Object.freeze({ fill: "#a66cff", dark: "#7a45d1" }),
  pink: Object.freeze({ fill: "#ff8fc8", dark: "#d9609f" }),
  white: Object.freeze({ fill: "#ffffff", dark: "#bdb6cc" }),
});

// 색을 말하지 않았을 때의 모습
const DEFAULT_TONE = {
  egg: { fill: "#fff4dc", dark: "#d9bf8c" },
  ball: { fill: "#ff7b54", dark: "#d4572f" },
  block: { fill: "#ffd23f", dark: "#d9a90f" },
  socks: { fill: "#ffffff", dark: "#bdb6cc" },
  hat: { fill: "#ff5a5f", dark: "#c73c41" },
  nest: { fill: "#b9824a", dark: "#7d5228" },
  tree: { fill: "#58b85c", dark: "#3a8a40" },
};

const isColor = (color) => typeof color === "string" && Object.hasOwn(PALETTE, color);
const toneOf = (kind, color) => (isColor(color) ? PALETTE[color] : DEFAULT_TONE[kind]);

// 양말 줄무늬: 흰 양말(기본, white)에는 빨강, 색 양말에는 흰색
const sockStripe = (color) => (isColor(color) && color !== "white" ? "#ffffff" : PALETTE.red.fill);

// ================================================================ 아기 공룡

const DINO = { body: "#6cc56b", edge: "#3f9a4c", belly: "#dff7b9", spike: "#ffb84d", spikeEdge: "#e08e1f", ink: "#1e2b22" };
const SVG_OPEN = 'xmlns="http://www.w3.org/2000/svg"';

/** 부위별 관절(공룡 그림 좌표, 상자 120x150). 렌더러가 이 점을 축으로 돌린다. */
export const DINO_PIVOTS = Object.freeze({
  body: Object.freeze({ x: 60, y: 150 }), // 발바닥 가운데
  head: Object.freeze({ x: 62, y: 74 }), // 목
  armL: Object.freeze({ x: 31, y: 92 }), // 보는 사람 기준 왼쪽 어깨
  armR: Object.freeze({ x: 89, y: 92 }),
  legL: Object.freeze({ x: 46, y: 124 }), // 엉덩이
  legR: Object.freeze({ x: 74, y: 124 }),
  tail: Object.freeze({ x: 34, y: 116 }), // 꼬리 뿌리
});

/** 렌더러가 채우는 빈자리: 표정, 모자(머리 위), 양말(다리마다). */
export const DINO_SLOTS = Object.freeze(["face", "hat", "sockL", "sockR"]);

const part = (name, inner) => {
  const { x, y } = DINO_PIVOTS[name];
  return `<g data-part="${name}" style="transform-origin:${x}px ${y}px">${inner}</g>`;
};
const slot = (name, inner = "") => `<g data-slot="${name}">${inner}</g>`;
const LINE = `stroke="${DINO.edge}" stroke-width="2.5" stroke-linejoin="round"`;
const spike = (d) => `<path d="${d}" fill="${DINO.spike}" stroke="${DINO.spikeEdge}" stroke-width="1.5" stroke-linejoin="round"/>`;

function leg(x, foot) {
  // 다리 하나: 통통한 기둥과 앞(오른쪽)을 향한 발
  return `<rect x="${x}" y="116" width="17" height="26" rx="8" fill="${DINO.body}" ${LINE}/>` +
    `<ellipse cx="${foot}" cy="144" rx="13" ry="6.5" fill="${DINO.body}" ${LINE}/>`;
}

function arm(x, hand) {
  return `<rect x="${x}" y="88" width="12" height="22" rx="6" fill="${DINO.body}" ${LINE}/>` +
    `<circle cx="${hand}" cy="110" r="6.5" fill="${DINO.body}" ${LINE}/>`;
}

// 눈(흰자, 눈동자, 반짝임), 볼, 입. 공룡은 오른쪽을 보므로 눈동자가 오른쪽으로 쏠린다.
const eye = (r = 11) => `<circle cx="76" cy="38" r="${r}" fill="#fff" stroke="${DINO.edge}" stroke-width="1.5"/>`;
const dot = (x, y, r, fill = DINO.ink) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${fill}"/>`;
const cheek = (rx = 6, ry = 4, opacity = 0.75) => `<ellipse cx="80" cy="58" rx="${rx}" ry="${ry}" fill="#ff9fb4" opacity="${opacity}"/>`;
const stroke = (d, width = 2.5) => `<path d="${d}" fill="none" stroke="#2d6b36" stroke-width="${width}" stroke-linecap="round"/>`;
const MOUTH_RED = "#a8324a";

const DINO_FACE_ART = {
  happy: eye() + dot(79, 39, 6.5) + dot(76.5, 36, 2.3, "#fff") + dot(81.5, 42, 1, "#fff") + cheek() + stroke("M88 62 Q96 68 104 61"),
  excited:
    eye(12) + dot(79, 38, 7.5) + dot(76, 34.5, 2.8, "#fff") + dot(82.5, 42, 1.4, "#fff") + cheek(6.5, 4.5, 0.85) +
    `<path d="M86 60 Q96 75 106 59 Z" fill="${MOUTH_RED}" stroke="#2d6b36" stroke-width="2" stroke-linejoin="round"/>`,
  sleepy: stroke("M67 40 Q76 47 86 40", 3) + cheek() + stroke("M90 63 Q96 66 102 62"),
  surprised: eye(13) + dot(78, 38, 4.5) + dot(76.5, 36.5, 1.5, "#fff") + cheek(5, 3.5, 0.6) +
    `<ellipse cx="97" cy="64" rx="4" ry="5" fill="${MOUTH_RED}"/>`,
  sad: eye() + dot(77, 42, 6) + dot(75, 39.5, 2, "#fff") + stroke("M66 25 L85 30", 2.5) + cheek(5, 3.5, 0.5) +
    stroke("M88 66 Q96 60 104 66"),
  shy: eye() + dot(75, 42, 6) + dot(73, 40, 2, "#fff") + cheek(9, 5, 0.95) + stroke("M90 63 Q94 66 98 63 Q102 60 105 63"),
};

export const DINO_FACES = Object.freeze(Object.keys(DINO_FACE_ART));

/** 표정 자리(data-slot="face")에 넣는 그림. 모르는 표정은 happy. */
export function dinoFaceSvg(face = "happy") {
  return DINO_FACE_ART[Object.hasOwn(DINO_FACE_ART, face) ? face : "happy"];
}

/** 입은 물건 자리의 그림: "hat"(머리 위 모자), "sockL"/"sockR"(그 발의 양말). 모르는 자리는 "". 모르는 색은 기본색. */
export function dinoWornSvg(slotName, color) {
  if (slotName === "hat") {
    const tone = toneOf("hat", color);
    return `<path d="M46 22 Q48 2 68 2 Q88 2 90 22 Z" fill="${tone.fill}" stroke="${tone.dark}" stroke-width="2"/>` +
      `<path d="M52 14 Q68 9 86 14" fill="none" stroke="#fff" stroke-width="3.5" opacity=".7"/>` +
      `<ellipse cx="76" cy="22" rx="28" ry="5" fill="${tone.fill}" stroke="${tone.dark}" stroke-width="2"/>` +
      `<circle cx="66" cy="3" r="5" fill="#fff" stroke="${tone.dark}" stroke-width="1.5"/>`;
  }
  if (slotName === "sockL" || slotName === "sockR") {
    const shift = slotName === "sockL" ? 0 : 28;
    const tone = toneOf("socks", color);
    const edge = `stroke="${tone.dark}" stroke-width="2"`;
    return `<rect x="${37 + shift}" y="128" width="19" height="15" rx="4" fill="${tone.fill}" ${edge}/>` +
      `<ellipse cx="${50 + shift}" cy="144" rx="14" ry="7.5" fill="${tone.fill}" ${edge}/>` +
      `<rect x="${38 + shift}" y="130" width="17" height="4" rx="2" fill="${sockStripe(color)}"/>`;
  }
  return "";
}

/** 아기 공룡. 부위 그룹 7개(body 가 나머지를 감싼다)와 빈자리 4개. 120x150 상자, 발바닥이 상자 아래끝. */
export function dinoSvg() {
  const tail = part("tail",
    `<path d="M40 104 C24 106 10 116 3 134 C16 132 30 128 42 124 Z" fill="${DINO.body}" ${LINE}/>` +
    spike("M24 111 L17 103 L28 106 Z") + spike("M13 120 L5 114 L16 115 Z"));
  const legs = part("legL", leg(38, 50) + slot("sockL")) + part("legR", leg(66, 78) + slot("sockR"));
  const torso =
    spike("M32 84 L20 86 L29 95 Z") + spike("M28 98 L16 102 L27 108 Z") + spike("M29 111 L19 118 L31 120 Z") +
    `<ellipse cx="60" cy="104" rx="33" ry="31" fill="${DINO.body}" ${LINE}/>` +
    `<ellipse cx="65" cy="110" rx="20" ry="21" fill="${DINO.belly}"/>`;
  const arms = part("armL", arm(25, 31)) + part("armR", arm(83, 89));
  const head = part("head",
    spike("M42 34 L32 28 L44 26 Z") + spike("M49 22 L42 12 L54 15 Z") + spike("M60 16 L58 5 L68 12 Z") +
    `<path d="M38 52 C34 28 52 14 70 16 C88 18 98 32 100 42 C110 44 112 60 104 68 C96 76 74 76 62 74 C46 72 40 64 38 52 Z" fill="${DINO.body}" ${LINE}/>` +
    `<circle cx="102" cy="50" r="1.8" fill="${DINO.edge}"/>` +
    slot("face", dinoFaceSvg("happy")) + slot("hat"));
  return (
    `<svg class="dino-svg" ${SVG_OPEN} viewBox="0 0 120 150" width="120" height="150" overflow="visible" aria-hidden="true">` +
    part("body", tail + legs + torso + arms + head) +
    "</svg>"
  );
}

// ================================================================ 물건

const escapeText = (text) =>
  String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const SAFE_KIND = /^[a-z][a-z0-9_-]{0,31}$/;

const ART = {
  egg(t) {
    return `<path d="M28 4 C44 4 53 30 53 46 C53 62 42 69 28 69 C14 69 3 62 3 46 C3 30 12 4 28 4 Z" fill="${t.fill}" stroke="${t.dark}" stroke-width="2.5"/>` +
      `<ellipse cx="19" cy="22" rx="5" ry="8" fill="#fff" opacity=".55" transform="rotate(20 19 22)"/>` +
      `<g fill="${t.dark}" opacity=".35"><circle cx="36" cy="30" r="3"/><circle cx="19" cy="48" r="2.5"/><circle cx="38" cy="54" r="2"/></g>` +
      // 부화할 때 렌더러가 보이게 하는 금
      `<g data-crack="" opacity="0"><path d="M4 40 L13 35 L19 42 L27 34 L34 43 L41 35 L52 40" fill="none" stroke="#6b4a2a" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></g>`;
  },
  ball(t) {
    return `<circle cx="32" cy="32" r="28" fill="${t.fill}" stroke="${t.dark}" stroke-width="2.5"/>` +
      `<path d="M6 27 Q32 42 58 27" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" opacity=".9"/>` +
      `<ellipse cx="22" cy="16" rx="7" ry="4.5" fill="#fff" opacity=".6" transform="rotate(-25 22 16)"/>`;
  },
  block(t) {
    return `<rect x="5" y="5" width="54" height="54" rx="9" fill="${t.fill}" stroke="${t.dark}" stroke-width="3"/>` +
      `<rect x="13" y="13" width="38" height="38" rx="6" fill="#fff" opacity=".3"/>` +
      `<path d="M32 19 L35.5 28 L45 28.5 L37.5 34.5 L40 44 L32 38.5 L24 44 L26.5 34.5 L19 28.5 L28.5 28 Z" fill="${t.dark}" opacity=".55"/>`;
  },
  socks(t, color) {
    const one = (o) =>
      `<path d="M${o + 4} 3 H${o + 18} V28 Q${o + 18} 31 ${o + 21} 32 L${o + 24} 33 Q${o + 28} 35 ${o + 27} 41 Q${o + 26} 46 ${o + 20} 46 H${o + 9} Q${o + 4} 46 ${o + 4} 40 Z" fill="${t.fill}" stroke="${t.dark}" stroke-width="2"/>` +
      `<rect x="${o + 4}" y="7" width="14" height="4" fill="${sockStripe(color)}"/><rect x="${o + 4}" y="14" width="14" height="4" fill="${sockStripe(color)}"/>`;
    return one(1) + one(30);
  },
  hat(t) {
    return `<path d="M12 40 Q12 8 38 8 Q64 8 64 40 Z" fill="${t.fill}" stroke="${t.dark}" stroke-width="2.5"/>` +
      `<path d="M18 30 Q38 23 58 30" fill="none" stroke="#fff" stroke-width="4" opacity=".7"/>` +
      `<path d="M8 40 H62 Q72 40 70 46 Q68 50 58 50 H12 Q6 50 6 45 Z" fill="${t.fill}" stroke="${t.dark}" stroke-width="2.5"/>` +
      `<circle cx="38" cy="8" r="6" fill="#fff" stroke="${t.dark}" stroke-width="1.5"/>`;
  },
  nest(t, _color, front) {
    // 앞 테두리. 둥지 속 물건(알)은 안쪽(hollow)과 앞 테두리 사이에 그려져 둥지 안에 든 것처럼 보인다.
    const rim =
      `<path d="M4 30 Q70 50 136 30 Q134 62 70 66 Q6 62 4 30 Z" fill="${t.fill}" stroke="${t.dark}" stroke-width="2.5" stroke-linejoin="round"/>` +
      `<g fill="none" stroke="${t.dark}" stroke-width="2" stroke-linecap="round" opacity=".6">` +
      `<path d="M14 40 Q44 52 76 50"/><path d="M60 58 Q96 56 126 42"/><path d="M24 52 Q50 60 82 60"/></g>`;
    if (front) return rim;
    const hollow = `<ellipse cx="70" cy="30" rx="63" ry="14" fill="${t.dark}" stroke="${t.fill}" stroke-width="5"/>`;
    return hollow + rim;
  },
  tree(t) {
    const blobs = [[75, 66, 52], [38, 104, 34], [112, 104, 34], [75, 118, 36]];
    return `<path d="M64 118 Q62 200 56 226 H94 Q88 200 86 118 Z" fill="#9a6a3c" stroke="#6e4826" stroke-width="2.5" stroke-linejoin="round"/>` +
      // 테두리를 먼저 그리고 같은 색 원을 조금 작게 덮어서, 겹친 원의 안쪽 선이 보이지 않게 한다
      `<g fill="${t.fill}" stroke="${t.dark}" stroke-width="5">${blobs.map(([x, y, r]) => `<circle cx="${x}" cy="${y}" r="${r}"/>`).join("")}</g>` +
      `<g fill="${t.fill}">${blobs.map(([x, y, r]) => `<circle cx="${x}" cy="${y}" r="${r - 2.5}"/>`).join("")}</g>` +
      `<circle cx="56" cy="50" r="13" fill="#fff" opacity=".22"/><circle cx="100" cy="96" r="8" fill="#fff" opacity=".18"/>`;
  },
};

/** 색과 크기를 바꿀 수 있는 SVG 물건(카탈로그 render:"svg"). */
export const SVG_OBJECTS = Object.freeze(Object.keys(ART));

/**
 * 물건 하나의 그림: footprint 상자 크기의 <svg>. SVG 물건은 팔레트 색으로, 그 밖에는 카탈로그의 `emoji` 를 <text> 로 그린다.
 * `part: "front"` 는 둥지의 앞 테두리만(렌더러가 둥지 속 물건 위에 한 번 더 그린다).
 */
export function objectSvg(kind, { color, size, emoji, part: layer } = {}) {
  const known = typeof kind === "string" && SAFE_KIND.test(kind);
  const { w, h } = footprint(known ? kind : "");
  const attrs = [
    known ? ` data-kind="${kind}"` : "",
    isColor(color) && Object.hasOwn(ART, kind) ? ` data-color="${color}"` : "",
    size === "big" || size === "small" ? ` data-size="${size}"` : "",
    layer === "front" && kind === "nest" ? ' data-part-of="front"' : "",
  ].join("");
  const open = `<svg class="obj" ${SVG_OPEN} viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" overflow="visible"${attrs} aria-hidden="true">`;
  let inner;
  if (known && Object.hasOwn(ART, kind)) {
    inner = ART[kind](toneOf(kind, color), color, layer === "front");
  } else {
    const glyph = typeof emoji === "string" && emoji.trim() ? escapeText(emoji.trim().slice(0, 8)) : "✨";
    inner = `<text x="${w / 2}" y="${h / 2}" font-size="${Math.round(Math.min(w, h) * 0.9)}" text-anchor="middle" dominant-baseline="central">${glyph}</text>`;
  }
  return open + inner + "</svg>";
}
