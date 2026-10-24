// 한 장면을 그리기 위한 순수 계산. DOM 을 쓰지 않아 node 로 시험한다(tests/js/frame.test.mjs). render.js 가 쓴다.
//
// drawOrder(scene, positions)  그리는 순서(뒤 → 앞)의 키 목록. 키는 물건 id, "chomp", "dino", 둥지 앞 테두리 frontKey(id).
//   - 다른 물건 "안에"(place.rel "in") 있는 물건은 그 ref 바로 뒤에 그린다. 둥지는 속에 든 물건 뒤에 앞 테두리를 한 번 더
//     그려서, 첫 화면의 알이 둥지 속에 앉아 보인다(egg1 in nest1).
//   - 입고 있는 물건(worn_by)은 입은 캐릭터 바로 뒤에 그린다.
//   - 나머지는 상자의 아래끝(y + 높이/2)이 작은 것부터(화면 아래쪽일수록 앞). 같으면 캐릭터, 그다음 장면 순서.
//     자리를 모르는 것은 맨 뒤에 그린다(빠뜨리지 않는다). 서로를 가리키는 고리는 각자 따로 선다.
// diffScene(prev, next, ops)  앞 장면과 새 장면의 차이: 새로 생긴 것, 사라진 것(어떻게: 부화/먹힘/치움), 동작, 모습이 바뀐 것.
// partTransform / poseTransforms  poses.js 의 포즈 값을 부위 그룹의 CSS transform 문자열로.
import { footprint } from "./layout.js";
import { PARTS, poseFor } from "./poses.js";

const ACTORS = ["chomp", "dino"];
const HATCH_EGG = "egg1"; // model.js 의 HATCH_EGG 와 같다

export const frontKey = (id) => `${id}#front`;

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const itemsOf = (scene) => (isObject(scene) && Array.isArray(scene.items) ? scene.items.filter((item) => isObject(item) && typeof item.id === "string") : []);

// ---------------------------------------------------------------- 그리는 순서

export function drawOrder(scene, positions) {
  const pos = isObject(positions) ? positions : {};
  const items = itemsOf(scene);
  const byId = new Map(items.map((item) => [item.id, item]));
  const parentOf = (item) => {
    if (typeof item.worn_by === "string" && ACTORS.includes(item.worn_by)) return item.worn_by;
    const place = item.place;
    if (isObject(place) && place.rel === "in" && typeof place.ref === "string" && place.ref !== item.id && byId.has(place.ref)) return place.ref;
    return null;
  };
  // 고리(a in b, b in a)에 걸린 물건은 부모가 없는 것으로 본다
  const looped = (item) => {
    const seen = new Set([item.id]);
    for (let parent = parentOf(item); parent !== null && !ACTORS.includes(parent); parent = parentOf(byId.get(parent))) {
      if (seen.has(parent)) return true;
      seen.add(parent);
    }
    return false;
  };

  const entries = [
    ...ACTORS.map((id, index) => ({ id, kind: id, index, parent: null })),
    ...items.map((item, i) => ({ id: item.id, kind: item.kind, index: ACTORS.length + i, parent: looped(item) ? null : parentOf(item) })),
  ];
  const children = new Map();
  for (const entry of entries) {
    if (entry.parent === null) continue;
    if (!children.has(entry.parent)) children.set(entry.parent, []);
    children.get(entry.parent).push(entry);
  }
  const bottom = (entry) => {
    const p = pos[entry.id];
    if (!p || !Number.isFinite(p.y)) return Infinity;
    const scale = Number.isFinite(p.scale) ? p.scale : 1;
    return p.y + (footprint(entry.kind).h * scale) / 2;
  };
  const roots = entries.filter((entry) => entry.parent === null);
  const keyOf = new Map(roots.map((entry) => [entry, bottom(entry)]));
  roots.sort((a, b) => keyOf.get(a) - keyOf.get(b) || a.index - b.index);

  const out = [];
  const emitted = new Set();
  const emit = (entry) => {
    if (emitted.has(entry.id)) return;
    emitted.add(entry.id);
    out.push(entry.id);
    for (const child of children.get(entry.id) ?? []) emit(child);
    if (entry.kind === "nest") out.push(frontKey(entry.id));
  };
  for (const root of roots) emit(root);
  return out;
}

// ---------------------------------------------------------------- 차이

/**
 * prev 가 없으면(첫 그림) 모두 added 이고 움직일 것이 없다.
 * removed[].how: "hatched"(부화로 깨진 첫 알), "eaten"(멍멍이가 문 음식), "gone"(그 밖에 치움).
 * acts: ops 의 act 중 모델이 받아들인 것(새 장면에서 그 캐릭터의 상태가 그 동작). ops 를 모르면 상태가 바뀐 것.
 */
export function diffScene(prev, next, ops) {
  const before = new Map(itemsOf(prev).map((item) => [item.id, item]));
  const after = new Map(itemsOf(next).map((item) => [item.id, item]));
  const actorsOf = (scene) => (isObject(scene) && isObject(scene.actors) ? scene.actors : {});
  const prevActors = actorsOf(prev);
  const nextActors = actorsOf(next);
  const hatched = prev != null && prevActors.dino?.hatched !== true && nextActors.dino?.hatched === true;
  const list = Array.isArray(ops) ? ops.filter(isObject) : null;
  const eatenIds = new Set((list ?? []).filter((op) => op.op === "act" && op.actor === "chomp" && op.action === "chomp").map((op) => op.target));

  const added = [...after.keys()].filter((id) => !before.has(id));
  const removed = prev == null ? [] : [...before.values()].filter((item) => !after.has(item.id)).map((item) => ({
    id: item.id,
    kind: item.kind,
    how: hatched && item.id === HATCH_EGG ? "hatched" : eatenIds.has(item.id) ? "eaten" : "gone",
  }));
  const changed = [...after.values()].filter((item) => {
    const old = before.get(item.id);
    return old !== undefined && (old.kind !== item.kind || old.color !== item.color || old.size !== item.size);
  }).map((item) => item.id);

  let acts = [];
  if (prev != null && list !== null) {
    acts = list
      .filter((op) => op.op === "act" && ACTORS.includes(op.actor) && op.action !== "idle" && nextActors[op.actor]?.state === op.action)
      .map((op) => ({ actor: op.actor, action: op.action, target: typeof op.target === "string" ? op.target : null }));
  } else if (prev != null) {
    acts = ACTORS.filter((actor) => {
      const state = nextActors[actor]?.state;
      return typeof state === "string" && state !== "idle" && state !== prevActors[actor]?.state;
    }).map((actor) => ({ actor, action: nextActors[actor].state, target: null }));
  }
  return { added, removed, changed, hatched, acts };
}

// ---------------------------------------------------------------- 포즈

const num = (value) => (typeof value === "number" && Number.isFinite(value) ? Math.round(value * 100) / 100 : 0);

/** poses.js 의 약속대로 `rotate(rot deg) translate(dx px, dy px)`. 늘 같은 모양이라 CSS transition 이 부드럽게 잇는다. */
export function partTransform(move) {
  const m = isObject(move) ? move : {};
  return `rotate(${num(m.rot)}deg) translate(${num(m.dx)}px, ${num(m.dy)}px)`;
}

/** 동작 이름 → { 부위: transform }. 포즈가 움직이지 않는 부위는 쉬는 자리. */
export function poseTransforms(action) {
  const pose = poseFor(action);
  return Object.fromEntries(PARTS.map((name) => [name, partTransform(pose[name])]));
}
