// 놀이판 장면 모델. 서버가 보낸 ops 를 화면 상태에 적용하는 순수 함수들이다 (스펙 6장). DOM 을 쓰지 않아 node 로 시험한다.
//
// 장면(Scene) = { items, actors, counters }
//   items    물건 목록. 앞에 있을수록 오래된 것이다. Item = { id, kind, color?, size?, place?, worn_by? }
//            place 는 { zone: Z } 또는 { rel: "on"|"under"|"next_to"|"in", ref: 물건 id }.
//            worn_by 가 있으면 그 캐릭터가 입고 있는 물건이다(자리 대신 그 캐릭터 몸에 붙는다).
//   actors   { chomp, dino }. ActorState = { state, face, wearing: 입고 있는 물건 id 목록, hatched?(dino 만) }
//            state 는 마지막으로 한 동작(처음은 "idle")이다. 렌더러가 포즈로 바꾸고, 끝나면 act idle 로 되돌릴 수 있다.
//   counters 물건 종류별로 지금까지 본 가장 큰 번호. id 를 우리가 만들 때(방어용) 이 번호 다음부터 쓰므로, 지운 물건의 id 가 다시 쓰이지 않는다.
//
// 지키는 것
//   - applyOps 는 새 장면을 돌려주고 입력(장면, ops)은 고치지 않는다. 결과도 ops 의 객체를 품지 않는다(자리는 복사한다).
//   - 서버가 이미 검사한 ops 지만, 화면이 깨지지 않도록 모르는 op, 없는 대상, 카탈로그에 없는 값은 조용히 무시한다.
//   - add 는 서버가 준 id/ids 를 그대로 쓴다. 둘 다 없을 때만 server/ids.py 의 next_ids 와 같은 규칙("{kind}{n}")으로 만든다.
//   - 장면 물건은 catalog.max_items(20) 개까지. 넘으면 오래된 것부터 지우되, 입고 있는 물건은 지우지 않고 nest, tree 는 맨 마지막에 지운다.

const ACTOR_IDS = ["chomp", "dino"];
const HATCH_EGG = "egg1"; // 처음 화면의 둥지 속 알. server/turn.py 의 HATCH_EGG 와 같다.
const DEFAULT_MAX_ITEMS = 20;
const DEFAULT_MAX_COUNT = 5;
const SAFE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
// 한도를 넘어 지울 때의 순서: 보통 물건 -> 나무 -> 둥지. (입고 있는 물건은 아예 지우지 않는다)
const EVICTION_TIER = new Map([["tree", 1], ["nest", 2]]);

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function initialScene() {
  return {
    items: [
      { id: "nest1", kind: "nest", place: { zone: "left" } },
      { id: "egg1", kind: "egg", place: { rel: "in", ref: "nest1" } },
    ],
    actors: {
      chomp: { state: "idle", face: "happy", wearing: [] },
      dino: { state: "idle", face: "happy", hatched: false, wearing: [] },
    },
    counters: { nest: 1, egg: 1 },
  };
}

/** `scene` 에 `ops` 를 차례로 적용한 새 장면. 입력은 건드리지 않는다. */
export function applyOps(scene, ops, catalog) {
  const next = copyScene(scene);
  const cat = isObject(catalog) ? catalog : {};
  for (const op of Array.isArray(ops) ? ops : []) {
    if (isObject(op)) applyOne(next, op, cat);
  }
  return next;
}

/** `/api/turn` 요청의 scene 필드. 캐릭터 둘이 맨 앞, 그 뒤에 물건 (server/turn.py 와 reply.py 가 읽는 모양). */
export function toWire(scene) {
  const { chomp, dino } = scene.actors;
  const wire = [
    { id: "chomp", kind: "chomp", state: chomp.state, face: chomp.face },
    { id: "dino", kind: "dino", state: dino.state, face: dino.face, hatched: dino.hatched === true },
  ];
  for (const item of scene.items) {
    const entry = { id: item.id, kind: item.kind };
    if (item.color !== undefined) entry.color = item.color;
    if (item.size !== undefined) entry.size = item.size;
    if (item.place !== undefined) entry.place = { ...item.place };
    if (item.worn_by) entry.state = "worn";
    wire.push(entry);
  }
  return wire;
}

// ---------------------------------------------------------------- 복사

function copyScene(scene) {
  const source = isObject(scene) ? scene : initialScene();
  const fresh = initialScene();
  const actors = isObject(source.actors) ? source.actors : {};
  return {
    items: (Array.isArray(source.items) ? source.items : []).map(copyItem),
    actors: {
      chomp: copyActor(actors.chomp, fresh.actors.chomp),
      dino: copyActor(actors.dino, fresh.actors.dino),
    },
    counters: { ...(isObject(source.counters) ? source.counters : {}) },
  };
}

function copyItem(item) {
  const copy = { ...item };
  if (isObject(item.place)) copy.place = { ...item.place };
  return copy;
}

function copyActor(actor, fallback) {
  const base = isObject(actor) ? actor : fallback;
  return { ...base, wearing: Array.isArray(base.wearing) ? [...base.wearing] : [] };
}

// ---------------------------------------------------------------- op 하나

function applyOne(scene, op, cat) {
  switch (op.op) {
    case "add": return opAdd(scene, op, cat);
    case "act": return opAct(scene, op, cat);
    case "remove": return opRemove(scene, op);
    case "set": return opSet(scene, op, cat);
    case "move": return opMove(scene, op, cat);
    default: return undefined; // 모르는 op 는 무시
  }
}

function opAdd(scene, op, cat) {
  const kind = op.kind;
  const def = objectDef(cat, kind);
  if (!def) return;
  const attrs = cleanAttrs(def, op, cat);
  const place = cleanPlace(op.place, scene, cat, null);
  for (const id of newIds(scene, op, kind, cat)) {
    const item = { id, kind, ...attrs };
    if (place) item.place = { ...place };
    scene.items.push(item);
    noteNumber(scene.counters, kind, id);
  }
  enforceLimit(scene, cat);
}

/** 서버가 준 id(s)를 그대로. 둘 다 없을 때만 우리가 만든다. 쓸 수 없는 id(이미 있음, 캐릭터 이름, 이상한 글자)는 그 물건만 건너뛴다. */
function newIds(scene, op, kind, cat) {
  const given = Array.isArray(op.ids) && op.ids.length ? op.ids : op.id !== undefined && op.id !== null ? [op.id] : null;
  if (given === null) return makeIds(scene, kind, cat, op.count);
  const used = new Set([...ACTOR_IDS, ...scene.items.map((item) => item.id)]);
  const fresh = [];
  for (const id of given) {
    if (typeof id !== "string" || !SAFE_ID.test(id) || used.has(id)) continue;
    used.add(id);
    fresh.push(id);
  }
  return fresh;
}

/** server/ids.py next_ids: "{kind}{n}", n = 그 kind 의 가장 큰 번호 + 1. 우리는 지금까지 본 가장 큰 번호(counters)도 함께 본다. */
function makeIds(scene, kind, cat, rawCount) {
  const max = Number.isInteger(cat.max_count) && cat.max_count > 0 ? cat.max_count : DEFAULT_MAX_COUNT;
  const count = Number.isInteger(rawCount) ? Math.max(1, Math.min(rawCount, max)) : 1;
  let top = Number.isInteger(scene.counters[kind]) ? scene.counters[kind] : 0;
  for (const item of scene.items) top = Math.max(top, numberOf(kind, item.id));
  return Array.from({ length: count }, (_, i) => `${kind}${top + i + 1}`);
}

function numberOf(kind, id) {
  const found = typeof id === "string" && id.startsWith(kind) ? /^[0-9]{1,9}$/.exec(id.slice(kind.length)) : null;
  return found ? Number(found[0]) : 0;
}

function noteNumber(counters, kind, id) {
  const n = numberOf(kind, id);
  if (n > 0) counters[kind] = Math.max(Number.isInteger(counters[kind]) ? counters[kind] : 0, n);
}

function opAct(scene, op, cat) {
  const actor = op.actor;
  if (!ACTOR_IDS.includes(actor)) return;
  const action = op.action;
  const allowed = cat.actors && isObject(cat.actors[actor]) && Array.isArray(cat.actors[actor].actions) ? cat.actors[actor].actions : [];
  if (action !== "idle" && !allowed.includes(action)) return; // idle 은 화면이 스스로 쉬는 모습으로 돌릴 때 쓴다. 서버는 보내지 않는다.
  const state = scene.actors[actor];
  if (action === "hatch") {
    // 멱등: 이미 깨어났으면 아무것도 하지 않는다. 서버가 다시 시작되며 hatch 를 또 보내도 아이가 그 뒤에 만든 egg1 을 지우지 않는다.
    if (actor !== "dino" || state.hatched === true) return;
    state.hatched = true;
    state.state = "hatch";
    dropItems(scene, new Set([HATCH_EGG]));
    return;
  }
  state.state = action;
  if (action === "put_on") wear(scene, actor, op.target, cat);
}

function wear(scene, actor, target, cat) {
  const item = findItem(scene, target);
  if (!item || item.worn_by || !objectDef(cat, item.kind)?.wearable) return;
  item.worn_by = actor;
  delete item.place; // 입었으니 놀이판 자리는 더 이상 없다
  scene.actors[actor].wearing.push(item.id);
}

function opRemove(scene, op) {
  if (findItem(scene, op.target)) dropItems(scene, new Set([op.target]));
}

function opSet(scene, op, cat) {
  const target = op.target;
  if (ACTOR_IDS.includes(target)) { // 표정: 캐릭터에게만, 그 캐릭터의 표정 목록 안에서
    const faces = cat.actors && isObject(cat.actors[target]) && Array.isArray(cat.actors[target].faces) ? cat.actors[target].faces : [];
    if (typeof op.face === "string" && faces.includes(op.face)) scene.actors[target].face = op.face;
    return;
  }
  const item = findItem(scene, target);
  if (!item) return;
  Object.assign(item, cleanAttrs(objectDef(cat, item.kind) ?? {}, op, cat));
}

function opMove(scene, op, cat) {
  const item = findItem(scene, op.target);
  if (!item) return;
  const place = cleanPlace(op.place, scene, cat, item.id);
  if (!place) return;
  if (item.worn_by) takeOff(scene, item); // 놀이판에 내려놓으면 더는 입고 있지 않다
  item.place = place;
}

function takeOff(scene, item) {
  delete item.worn_by;
  for (const actor of ACTOR_IDS) scene.actors[actor].wearing = scene.actors[actor].wearing.filter((id) => id !== item.id);
}

// ---------------------------------------------------------------- 값 검사

function objectDef(cat, kind) {
  return typeof kind === "string" && isObject(cat.objects) && Object.hasOwn(cat.objects, kind) && isObject(cat.objects[kind]) ? cat.objects[kind] : null;
}

function findItem(scene, id) {
  return typeof id === "string" ? scene.items.find((item) => item.id === id) : undefined;
}

/** color/size: 카탈로그의 값이고 그 물건이 가질 수 있는 속성만 남긴다 (그림의 fill 에 들어가는 글자라 모르는 값을 통과시키지 않는다). */
function cleanAttrs(def, src, cat) {
  const attrs = {};
  if (def.color === true && typeof src.color === "string" && Array.isArray(cat.colors) && cat.colors.includes(src.color)) attrs.color = src.color;
  if (def.size === true && typeof src.size === "string" && Array.isArray(cat.sizes) && cat.sizes.includes(src.size)) attrs.size = src.size;
  return attrs;
}

/** { zone } 또는 { rel, ref } 의 새 객체. 틀리면 null. ref 는 장면에 있는 물건이어야 하고, `own` 을 거쳐 자기에게 돌아오는 고리는 안 된다. */
function cleanPlace(raw, scene, cat, own) {
  if (!isObject(raw)) return null;
  const zone = raw.zone ?? null;
  const rel = raw.rel ?? null;
  const ref = raw.ref ?? null;
  if (zone !== null) {
    return rel === null && ref === null && typeof zone === "string" && Array.isArray(cat.zones) && cat.zones.includes(zone) ? { zone } : null;
  }
  if (typeof rel !== "string" || !Array.isArray(cat.relations) || !cat.relations.includes(rel)) return null;
  if (!findItem(scene, ref) || makesLoop(scene, own, ref)) return null;
  return { rel, ref };
}

function makesLoop(scene, own, ref) {
  const seen = new Set();
  for (let current = ref; typeof current === "string" && !seen.has(current); current = findItem(scene, current)?.place?.ref) {
    if (current === own) return true;
    seen.add(current);
  }
  return false;
}

// ---------------------------------------------------------------- 지우기

/** `gone` 의 물건을 치우고, 입고 있던 것이면 입은 목록에서 빼고, 그 물건을 가리키던 자리(place)는 없앤다. */
function dropItems(scene, gone) {
  scene.items = scene.items.filter((item) => !gone.has(item.id));
  for (const actor of ACTOR_IDS) scene.actors[actor].wearing = scene.actors[actor].wearing.filter((id) => !gone.has(id));
  for (const item of scene.items) {
    if (item.place && gone.has(item.place.ref)) delete item.place;
  }
}

/** 물건이 max_items 를 넘으면 오래된 것부터 지운다. 입고 있는 물건은 남기고, nest/tree 는 다른 것이 없을 때만 (나무, 둥지 순으로). */
function enforceLimit(scene, cat) {
  const limit = Number.isInteger(cat.max_items) && cat.max_items > 0 ? cat.max_items : DEFAULT_MAX_ITEMS;
  while (scene.items.length > limit) {
    let victim = null;
    let best = Infinity;
    for (const item of scene.items) {
      if (item.worn_by) continue;
      const tier = EVICTION_TIER.get(item.kind) ?? 0;
      if (tier < best) { victim = item; best = tier; }
    }
    if (victim === null) return; // 남은 것이 전부 입고 있는 물건
    dropItems(scene, new Set([victim.id]));
  }
}
