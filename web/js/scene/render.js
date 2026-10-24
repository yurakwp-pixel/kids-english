// 놀이판 그리기(DOM). 장면(model.js)과 자리(layout.js)를 받아 #board SVG 안의 그림을 맞춘다.
//
// render(svgRoot, scene, positions, catalog, { ops }?)
//   - id 마다 <g> 하나를 만들어 다시 쓴다. 자리와 크기는 그 <g> 의 CSS transform 이라 바뀌면 0.35초 transition 으로 움직인다.
//   - 아기 공룡의 포즈는 부위 그룹(data-part)의 CSS transform(poses.js 의 값, frame.js 의 문자열)이고 역시 0.35초로 바뀐다.
//   - 한 번만 하는 움직임(퐁 생기기, 흔들기, 차인 공, 덥석, 점프, 돌기)은 Web Animations(element.animate)로 .fx/.flip 그룹에 건다.
//     자리·포즈를 정하는 transform 과 다른 그룹이라 서로 덮어쓰지 않는다.
//   - 앞 장면과의 차이(frame.js diffScene)로 사라진 것을 안다. 사라진 물건은 짧게 작아지며 흐려진다(0.3초).
//     멍멍이가 문 음식은 멍멍이가 그쪽으로 덥석 달려든 뒤 입으로 빨려 들어가며 사라진다. 첫 알은 흔들리다 금이 가고 깨지며
//     아기 공룡이 튀어나온다(약 1초). 번쩍임은 없다.
//   - 그리는 순서는 frame.js drawOrder: 둥지 속 알은 둥지 다음, 그 위에 둥지 앞 테두리. 나머지는 아래끝 순.
//     순서를 맞출 때 제자리인 그룹은 옮기지 않는다(옮긴 그룹은 transition 이 끊긴다).
//   - 동작 줄이기(prefers-reduced-motion)면 한 번만 하는 움직임을 건너뛴다(CSS 가 transition 도 끈다).
// setChompMouth(svgRoot, open)  말하는 동안 멍멍이 입을 여닫는다(main.js 가 0.2초마다).
//
// 그림 문자열은 sprites.js 가 표에서만 만든다(색·표정·종류는 표에 있는 값만). id 는 setAttribute 로만 넣는다.
import { chompSvg, dinoSvg, dinoFaceSvg, dinoWornSvg, objectSvg } from "./sprites.js";
import { footprint } from "./layout.js";
import { drawOrder, frontKey, diffScene, poseTransforms } from "./frame.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const CHOMP_FIT = 1; // 200x200 멍멍이 그림을 150 상자에: 쇠공 지름 140, 쇠사슬만 상자 밖 오른쪽 아래로
const CHOMP_CENTER = 95; // chompSvg 의 쇠공 가운데
const DINO_BOX = { w: 120, h: 150 };
const POP_MS = 380;
const EXIT_MS = 300;
const BITE_MS = 260; // 덥석: 멍멍이가 다가가 입을 다무는 때
const EAT_MS = 320;
const LUNGE_MS = 760;
const HATCH_WOBBLE_MS = 560;
const HATCH_CRACK_AT = 420;
const HATCH_OPEN_AT = 600; // 알이 깨지고 공룡이 튀어나오는 때

const views = new WeakMap();
const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
const has = (object, key) => object !== null && typeof object === "object" && Object.hasOwn(object, key);

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
  return node;
}

function animate(target, keyframes, options) {
  try {
    return target.animate(keyframes, options);
  } catch {
    return null;
  }
}

/** 애니메이션이 끝나면(또는 늦어도 ms+200 뒤) 그룹을 치운다. */
function removeAfter(g, anim, ms) {
  let gone = false;
  const drop = () => {
    if (!gone) {
      gone = true;
      g.remove();
    }
  };
  anim?.finished.then(drop, drop);
  setTimeout(drop, ms + 200);
}

// ---------------------------------------------------------------- 그룹 만들기

function viewOf(svgRoot) {
  let v = views.get(svgRoot);
  if (!v) {
    let layer = svgRoot.querySelector("g.scene-layer");
    if (!layer) {
      layer = svgEl("g", { class: "scene-layer" });
      svgRoot.append(layer);
    }
    v = { layer, nodes: new Map(), prev: null, pos: {}, mouthOpen: true, chompFace: "happy", chompSvg: "", hatching: false };
    views.set(svgRoot, v);
  }
  return v;
}

function addNode(v, node) {
  v.layer.append(node.g);
  v.nodes.set(node.key, node);
  return node;
}

function makeThing(key, kind, front) {
  const g = svgEl("g", { class: front ? "thing front" : "thing", "data-key": key, "data-kind": kind });
  const fx = svgEl("g", { class: "fx" });
  g.append(fx);
  return { key, g, fx, look: "", front, fresh: true };
}

/** 모습(종류·색·크기)이 바뀌었을 때만 그림을 다시 넣는다. */
function paintThing(node, item, catalog) {
  const look = `${item.kind}|${item.color ?? ""}|${item.size ?? ""}`;
  if (look === node.look) return;
  const def = has(catalog?.objects, item.kind) ? catalog.objects[item.kind] : null;
  const fp = footprint(item.kind);
  const art = objectSvg(item.kind, { color: item.color, size: item.size, emoji: def?.emoji, part: node.front ? "front" : undefined });
  node.fx.innerHTML = `<g transform="translate(${-fp.w / 2} ${-fp.h / 2})">${art}</g>`;
  node.look = look;
}

function makeDino() {
  const g = svgEl("g", { class: "actor dino unhatched", "data-key": "dino" });
  g.innerHTML =
    '<ellipse class="shadow" cx="0" cy="74" rx="40" ry="7" fill="#000" opacity=".12"/>' +
    `<g class="fx"><g class="flip"><g transform="translate(${-DINO_BOX.w / 2} ${-DINO_BOX.h / 2})">${dinoSvg()}</g></g></g>`;
  const pick = (selector, attr) => Object.fromEntries([...g.querySelectorAll(selector)].map((n) => [n.getAttribute(attr), n]));
  return {
    key: "dino", g, fx: g.querySelector(".fx"), flip: g.querySelector(".flip"),
    parts: pick("[data-part]", "data-part"), slots: pick("[data-slot]", "data-slot"), slotArt: {}, face: "happy", pose: "",
  };
}

function makeChomp() {
  const g = svgEl("g", { class: "actor chomp", "data-key": "chomp" });
  const offset = -CHOMP_CENTER * CHOMP_FIT;
  g.innerHTML =
    '<ellipse class="shadow" cx="0" cy="72" rx="52" ry="10" fill="#000" opacity=".12"/>' +
    // 쇠사슬을 묶어 둔 말뚝(사슬 끝 (174,188) 아래). 멍멍이가 뛰어도 말뚝은 그대로다.
    '<g class="stake"><ellipse cx="79" cy="118" rx="15" ry="4" fill="#000" opacity=".12"/>' +
    '<rect x="71" y="84" width="16" height="34" rx="5" fill="#a0703c" stroke="#7a5228" stroke-width="2"/></g>' +
    `<g class="fx"><g class="pose"><g class="hop"><g transform="translate(${offset} ${offset}) scale(${CHOMP_FIT})">` +
    '<g class="chomp-art"></g></g></g>' +
    '<text class="zz" x="40" y="-66" font-size="40">💤</text></g></g>';
  return { key: "chomp", g, fx: g.querySelector(".fx"), art: g.querySelector(".chomp-art") };
}

function place(node, p) {
  node.g.style.transform = `translate(${p.x}px, ${p.y}px) scale(${p.scale})`;
}

// ---------------------------------------------------------------- 멍멍이 그림

function paintChomp(v) {
  const chomp = v.nodes.get("chomp");
  if (!chomp) return;
  const svg = chompSvg({ mouthOpen: v.mouthOpen, face: v.chompFace });
  if (svg !== v.chompSvg) {
    chomp.art.innerHTML = svg;
    v.chompSvg = svg;
  }
}

export function setChompMouth(svgRoot, open) {
  const v = viewOf(svgRoot);
  v.mouthOpen = open !== false;
  paintChomp(v);
}

function snapMouth(v, times) {
  for (const [at, open] of times) {
    setTimeout(() => {
      v.mouthOpen = open;
      paintChomp(v);
    }, at);
  }
}

// ---------------------------------------------------------------- 그리기

export function render(svgRoot, scene, positions, catalog, { ops } = {}) {
  const v = viewOf(svgRoot);
  const first = v.prev === null;
  const motion = !first && !reducedMotion();
  const pos = positions ?? {};
  const before = v.pos;
  const diff = diffScene(v.prev, scene, ops);

  const chomp = v.nodes.get("chomp") ?? addNode(v, makeChomp());
  const dino = v.nodes.get("dino") ?? addNode(v, makeDino());

  // 1. 물건: 있는 것은 다시 쓰고, 새것은 만들고, 둥지에는 앞 테두리도 둔다
  const live = new Set(["chomp", "dino"]);
  for (const item of scene.items) {
    for (const front of item.kind === "nest" ? [false, true] : [false]) {
      const key = front ? frontKey(item.id) : item.id;
      live.add(key);
      const node = v.nodes.get(key) ?? addNode(v, makeThing(key, item.kind, front));
      paintThing(node, item, catalog);
      node.g.classList.toggle("worn", item.worn_by === "dino"); // 공룡이 입은 것은 공룡 그림(빈자리)에 그린다
    }
  }

  // 2. 사라진 물건은 떠나는 움직임을 하고 치운다
  const how = new Map(diff.removed.map((r) => [r.id, r.how]));
  for (const [key, node] of [...v.nodes]) {
    if (live.has(key)) continue;
    v.nodes.delete(key);
    const id = key.endsWith("#front") ? key.slice(0, -"#front".length) : key;
    leave(v, node, how.get(id) ?? "gone", motion, before[id]);
  }

  // 3. 자리와 순서
  for (const [key, node] of v.nodes) {
    const id = key.endsWith("#front") ? key.slice(0, -"#front".length) : key;
    if (key === "dino" && v.hatching) continue; // 부화 중에는 알 자리에서 튀어나올 때 자리를 잡는다
    if (pos[id]) place(node, pos[id]);
  }
  reorder(v, drawOrder(scene, pos));
  for (const node of v.nodes.values()) {
    if (node.fresh && motion) {
      animate(node.fx, [
        { transform: "scale(0.2)", opacity: 0 },
        { transform: "scale(1.12)", opacity: 1, offset: 0.7 },
        { transform: "scale(1)", opacity: 1 },
      ], { duration: POP_MS, easing: "ease-out" });
    }
    node.fresh = false;
  }

  // 4. 캐릭터의 지금 모습: 표정, 포즈, 입은 것, 계속되는 상태(숨기, 자기)
  const actors = scene.actors ?? {};
  v.chompFace = actors.chomp?.face ?? "happy";
  paintChomp(v);
  chomp.g.setAttribute("data-state", actors.chomp?.state ?? "idle");
  drawDino(v, dino, scene, motion);

  // 5. 한 번만 하는 움직임
  if (diff.hatched) hatch(v, dino, before.egg1 ?? pos.nest1, motion);
  if (motion) {
    for (const act of diff.acts) playAct(v, act, { chomp, dino, pos, before });
  }

  v.prev = scene;
  v.pos = pos;
}

function drawDino(v, dino, scene, motion) {
  const state = scene.actors?.dino ?? {};
  dino.g.classList.toggle("unhatched", state.hatched !== true || v.hatching);
  dino.g.setAttribute("data-state", state.state ?? "idle");
  const action = state.hatched === true ? state.state : "idle";
  if (action !== dino.pose) {
    const transforms = poseTransforms(action);
    for (const [name, group] of Object.entries(dino.parts)) group.style.transform = transforms[name];
    dino.pose = action;
  }
  const face = state.face ?? "happy";
  if (face !== dino.face) {
    dino.slots.face.innerHTML = dinoFaceSvg(face);
    dino.face = face;
  }
  // 입은 것: 같은 종류가 여럿이면 마지막에 입은 것
  const worn = {};
  for (const item of scene.items) if (item.worn_by === "dino") worn[item.kind] = item;
  const want = {
    sockL: worn.socks ? dinoWornSvg("sockL", worn.socks.color) : "",
    sockR: worn.socks ? dinoWornSvg("sockR", worn.socks.color) : "",
    hat: worn.hat ? dinoWornSvg("hat", worn.hat.color) : "",
  };
  for (const [slotName, art] of Object.entries(want)) {
    if ((dino.slotArt[slotName] ?? "") === art) continue;
    dino.slots[slotName].innerHTML = art;
    dino.slotArt[slotName] = art;
    if (art && motion) animate(dino.slots[slotName], [{ opacity: 0 }, { opacity: 1 }], { duration: 350, delay: 150, fill: "backwards" });
  }
}

/** 제자리인 그룹은 두고, 순서가 틀린 그룹만 옮긴다. 떠나는 중인 그룹은 건너뛴다. */
function reorder(v, order) {
  const want = order.map((key) => v.nodes.get(key)?.g).filter(Boolean);
  let cursor = v.layer.firstElementChild;
  for (const g of want) {
    while (cursor && cursor.classList.contains("leaving")) cursor = cursor.nextElementSibling;
    if (cursor === g) {
      cursor = cursor.nextElementSibling;
      continue;
    }
    v.layer.insertBefore(g, cursor);
  }
}

// ---------------------------------------------------------------- 떠나기

function leave(v, node, how, motion, at) {
  node.g.classList.add("leaving");
  if (!motion) {
    node.g.remove();
    return;
  }
  if (how === "hatched") return hatchEgg(node);
  if (how === "eaten" && at && v.pos.chomp) return beEaten(node, at, v.pos.chomp);
  const anim = animate(node.fx, [{ transform: "scale(1)", opacity: 1 }, { transform: "scale(0.2)", opacity: 0 }],
    { duration: EXIT_MS, easing: "ease-in", fill: "forwards" });
  removeAfter(node.g, anim, EXIT_MS);
}

/** 멍멍이가 그쪽으로 얼마나 달려드나: 입(쇠공 왼쪽)이 음식에 닿도록, 쇠사슬 길이만큼까지. */
function lungeOf(c, food) {
  const clamp = (value, limit) => Math.max(-limit, Math.min(limit, value));
  return { x: clamp((food.x - c.x + 45) * 0.8, 340), y: clamp((food.y - c.y) * 0.8, 160) };
}

function beEaten(node, food, c) {
  const lunge = lungeOf(c, food);
  const mouth = { x: c.x + lunge.x - 38, y: c.y + lunge.y + 4 };
  const scale = food.scale || 1;
  const dx = (mouth.x - food.x) / scale;
  const dy = (mouth.y - food.y) / scale;
  const anim = animate(node.fx, [
    { transform: "translate(0px, 0px) scale(1)", opacity: 1 },
    { transform: `translate(${dx}px, ${dy}px) scale(0.15)`, opacity: 0 },
  ], { duration: EAT_MS, delay: BITE_MS, easing: "ease-in", fill: "forwards" });
  removeAfter(node.g, anim, BITE_MS + EAT_MS);
}

// ---------------------------------------------------------------- 부화

function hatchEgg(node) {
  animate(node.fx, [
    { transform: "rotate(0deg)" }, { transform: "rotate(-12deg)" }, { transform: "rotate(12deg)" },
    { transform: "rotate(-9deg)" }, { transform: "rotate(7deg)" }, { transform: "rotate(-3deg)" }, { transform: "rotate(0deg)" },
  ], { duration: HATCH_WOBBLE_MS, easing: "ease-in-out" });
  setTimeout(() => node.g.querySelector("[data-crack]")?.setAttribute("opacity", "1"), HATCH_CRACK_AT);
  setTimeout(() => {
    const anim = animate(node.fx, [
      { transform: "translate(0px, 0px) scale(1)", opacity: 1 },
      { transform: "translate(0px, -10px) scale(1.2)", opacity: 0 },
    ], { duration: 260, easing: "ease-out", fill: "forwards" });
    removeAfter(node.g, anim, 260);
  }, HATCH_OPEN_AT);
}

/** 공룡이 알 자리(from)에서 작게 튀어나와 제자리로 폴짝 간다. 움직임이 없으면 바로 제자리에 선다. */
function hatch(v, dino, from, motion) {
  if (!motion || !from) {
    v.hatching = false;
    dino.g.classList.remove("unhatched");
    return;
  }
  v.hatching = true;
  dino.g.classList.add("unhatched");
  setTimeout(() => {
    v.hatching = false;
    dino.g.classList.remove("unhatched");
    dino.g.style.transition = "none";
    dino.g.style.transform = `translate(${from.x}px, ${from.y - 10}px) scale(0.35)`;
    void dino.g.getBoundingClientRect(); // 시작 자리를 먼저 그리게 한다
    dino.g.style.transition = "";
    if (v.pos.dino) place(dino, v.pos.dino);
    animate(dino.fx, [
      { transform: "translate(0px, 0px)" }, { transform: "translate(0px, -46px)", offset: 0.4 }, { transform: "translate(0px, 0px)" },
    ], { duration: 520, easing: "ease-out" });
  }, HATCH_OPEN_AT);
}

// ---------------------------------------------------------------- 동작

function playAct(v, { actor, action, target }, { chomp, dino, pos, before }) {
  if (actor === "chomp") {
    if (action === "chomp") {
      const food = (target && (before[target] ?? pos[target])) || null;
      const c = pos.chomp;
      if (food && c) {
        const lunge = lungeOf(c, food);
        animate(chomp.fx, [
          { transform: "translate(0px, 0px)" },
          { transform: `translate(${lunge.x}px, ${lunge.y}px)`, offset: 0.45 },
          { transform: "translate(0px, 0px)" },
        ], { duration: LUNGE_MS, easing: "ease-in-out" });
      }
      snapMouth(v, [[0, true], [BITE_MS, false], [BITE_MS + 140, true], [BITE_MS + 280, false], [BITE_MS + 420, true]]);
    } else if (action === "jump") {
      animate(chomp.fx, [
        { transform: "translate(0px, 0px)" }, { transform: "translate(0px, -80px)", offset: 0.4 },
        { transform: "translate(0px, 0px)", offset: 0.7 }, { transform: "translate(0px, -16px)", offset: 0.85 }, { transform: "translate(0px, 0px)" },
      ], { duration: 720, easing: "ease-out" });
    } else if (action === "spin") {
      animate(chomp.fx, [{ transform: "rotate(0deg)" }, { transform: "rotate(-360deg)" }], { duration: 800, easing: "ease-in-out" });
    } else if (action === "bark") {
      animate(chomp.fx, [
        { transform: "scale(1, 1)" }, { transform: "scale(1.12, 0.92)" }, { transform: "scale(0.96, 1.06)" },
        { transform: "scale(1.1, 0.94)" }, { transform: "scale(1, 1)" },
      ], { duration: 560, easing: "ease-in-out" });
      snapMouth(v, [[0, false], [130, true], [260, false], [390, true]]);
    }
    return;
  }
  if (actor !== "dino") return;
  if (action === "turn_around") {
    animate(dino.flip, [{ transform: "scaleX(1)" }, { transform: "scaleX(-1)", offset: 0.5 }, { transform: "scaleX(1)" }],
      { duration: 1000, easing: "ease-in-out" });
  } else if (action === "walk") {
    animate(dino.fx, [
      { transform: "translate(0px, 0px)" }, { transform: "translate(26px, -6px)", offset: 0.25 }, { transform: "translate(52px, 0px)", offset: 0.5 },
      { transform: "translate(26px, -6px)", offset: 0.75 }, { transform: "translate(0px, 0px)" },
    ], { duration: 1100, easing: "ease-in-out" });
  } else if (action === "dance") {
    animate(dino.fx, [
      { transform: "translate(0px, 0px) rotate(0deg)" }, { transform: "translate(0px, -16px) rotate(-6deg)", offset: 0.25 },
      { transform: "translate(0px, 0px) rotate(0deg)", offset: 0.5 }, { transform: "translate(0px, -16px) rotate(6deg)", offset: 0.75 },
      { transform: "translate(0px, 0px) rotate(0deg)" },
    ], { duration: 1000, easing: "ease-in-out" });
  } else if (action === "kick" && target && v.nodes.has(target)) {
    const ball = v.nodes.get(target);
    const at = pos[target];
    const s = at?.scale || 1;
    const dir = at && at.x > 1000 ? -1 : 1; // 오른쪽 끝의 공은 왼쪽으로
    const k = (x, y, deg, offset) => ({ transform: `translate(${(dir * x) / s}px, ${y / s}px) rotate(${dir * deg}deg)`, ...(offset ? { offset } : {}) });
    animate(ball.fx, [k(0, 0, 0), k(70, -90, 200, 0.35), k(120, 0, 360, 0.6), k(60, -24, 300, 0.8), k(0, 0, 360)],
      { duration: 1100, delay: 120, easing: "ease-out" });
  }
}
