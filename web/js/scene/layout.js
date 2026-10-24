// 놀이판 배치. 장면(Scene)만 보고 물건과 캐릭터가 화면 어디에 서는지 정한다. 같은 장면이면 늘 같은 그림이다(결정적, 순수 함수).
//
// layout(scene, catalog, view) -> { [id]: { x, y, scale } }   id 는 물건 id 와 "chomp", "dino"
//   - (x, y) 는 그 물건이 차지하는 상자의 가운데 (view 픽셀, 위쪽이 y=0). 상자의 크기는 footprint(kind) × scale 이다.
//     렌더러(render.js)는 각 그림을 footprint 상자에 맞춰 그리고 scale 만큼 키운다 (big 1.4, small 0.7, 나머지 1).
//   - 겹쳐 보일 때는 상자의 아래쪽 끝(y + 높이/2)이 큰 것을 나중에 그린다 (아래에 있는 것이 앞).
//   - view 는 기본 1200x600 이다. 렌더러는 이 크기의 viewBox 를 쓰고 CSS 로 늘이고 줄이면 된다.
//
// 놀이판의 모양 (view 높이 h, 너비 w 기준)
//   하늘      위쪽 띠. 줄 2개 (아래끝 0.21h, 0.33h). place 가 없는 sun 은 여기 선다.
//   무대      캐릭터가 서는 줄 (아래끝 0.58h). 공룡은 왼쪽(0.42w), 멍멍이는 오른쪽(0.86w)이 고정 자리다.
//   앞마당    물건이 서는 줄 3개 (아래끝 0.88h, 0.76h, 0.64h). 앞줄부터 채운다. 맨 앞줄 아래의 0.12h 는 under 로 놓일 물건의 자리다.
//   가로로는 12칸이다(칸 너비 w/12). left 는 0~3칸, center 는 4~7칸, right 는 8~11칸, ground/sky 는 전체.
//
// 자리 정하기
//   - place 가 없거나 zone 이면 슬롯 격자에 앉힌다: 그 zone 의 칸에서 앞줄부터 왼쪽부터, 물건의 너비만큼 칸을 쓴다.
//     같은 줄에서 상자가 겹치지 않는다. zone 이 꽉 차면 다른 칸으로 넘치고, 격자가 다 차면 겹쳐서라도 서로 다른 자리에 앉힌다.
//     zone 이 없으면 ground (sun 만 sky). 키가 큰 물건은 캐릭터의 상자를 가리지 않는 칸만 쓴다.
//   - on/under/next_to/in 은 ref 의 자리에서 계산한다 (ref 가 또 다른 물건에 얹혀 있어도 된다).
//     on 은 ref 위에 얹혀(y 가 작다), under 는 ref 의 바로 아래(y 가 크다), next_to 는 옆에 같은 바닥으로, in 은 같은 자리.
//     같은 ref 에 둘 이상이 오면 좌우로 나눠 앉고, 첫 번째는 정확히 그 자리다 (둥지 속 알은 둥지와 같은 x, y).
//   - 입고 있는 물건(worn_by)은 그 캐릭터 몸에 붙는다: hat 은 머리 위, 나머지(socks)는 발.
//   - 가리키는 물건이 없거나 서로를 가리키는 고리면 place 가 없는 것으로 보고 격자에 앉힌다.
//   - 모든 상자는 view 안으로 밀어 넣는다.

export const DEFAULT_VIEW = Object.freeze({ w: 1200, h: 600 });

/** 그림을 그릴 상자 크기 (scale 1 일 때, view 가 1200x600 일 때의 픽셀). */
export const FOOTPRINT = Object.freeze({
  chomp: { w: 150, h: 150 },
  dino: { w: 120, h: 150 },
  egg: { w: 56, h: 72 },
  ball: { w: 64, h: 64 },
  block: { w: 64, h: 64 },
  socks: { w: 60, h: 50 },
  hat: { w: 72, h: 56 },
  nest: { w: 140, h: 64 },
  tree: { w: 150, h: 230 },
  sun: { w: 96, h: 96 },
  toys: { w: 64, h: 64 },
  flower: { w: 64, h: 64 },
  apple: { w: 56, h: 56 },
  cookie: { w: 56, h: 56 },
  banana: { w: 64, h: 56 },
});
const FALLBACK_FOOTPRINT = Object.freeze({ w: 64, h: 64 });

export function footprint(kind) {
  return typeof kind === "string" && Object.hasOwn(FOOTPRINT, kind) ? FOOTPRINT[kind] : FALLBACK_FOOTPRINT;
}

const SCALES = { big: 1.4, small: 0.7 };
const COLS = 12;
const ZONE_COLS = { left: [0, 3], center: [4, 7], right: [8, 11], ground: [0, 11], sky: [0, 11] };
const DEFAULT_ZONE = new Map([["sun", "sky"]]);
const ROW_STEP = 0.12; // 줄과 줄 사이 (view 높이의 비율)
const GROUND_FRONT = 0.88; // 앞마당 맨 앞줄의 아래끝
const GROUND_ROWS = 3;
const SKY_FIRST = 0.21;
const SKY_ROWS = 2;
const STAGE_BASE = 0.58; // 캐릭터가 서는 줄의 아래끝
const HOME_X = { dino: 0.42, chomp: 0.86 };
const GAP = 6;
const RELATIONS = new Set(["on", "under", "next_to", "in"]);

const scaleOf = (size) => (typeof size === "string" && Object.hasOwn(SCALES, size) ? SCALES[size] : 1);
const positive = (value, fallback) => (Number.isFinite(value) && value > 0 ? value : fallback);
const round = (value) => Math.round(value * 100) / 100;

export function layout(scene, catalog, view = DEFAULT_VIEW) {
  const w = positive(view?.w, DEFAULT_VIEW.w);
  const h = positive(view?.h, DEFAULT_VIEW.h);
  const zones = Array.isArray(catalog?.zones) ? catalog.zones : null;
  const items = Array.isArray(scene?.items) ? scene.items.filter((item) => item && typeof item.id === "string") : [];
  const byId = new Map(items.map((item) => [item.id, item]));
  const pos = new Map(); // id -> { x, y, scale, w, h } (가운데, 상자 크기)

  const put = (id, kind, scale, x, y) => {
    const box = sizeOf(kind, scale);
    pos.set(id, { x: clamp(x, box.w / 2, w - box.w / 2), y: clamp(y, box.h / 2, h - box.h / 2), scale, ...box });
  };

  // 1. 캐릭터: 고정 자리
  for (const actor of ["chomp", "dino"]) {
    put(actor, actor, 1, HOME_X[actor] * w, STAGE_BASE * h - footprint(actor).h / 2);
  }
  const actorBoxes = ["chomp", "dino"].map((id) => boxOf(pos.get(id)));

  // 2. 입고 있는 물건: 입은 캐릭터 몸에
  const worn = items.filter((item) => pos.has(item.worn_by));
  const wornIds = new Set(worn.map((item) => item.id));
  const wornCount = new Map();
  for (const item of worn) {
    const wearer = pos.get(item.worn_by);
    const scale = scaleOf(item.size);
    const box = sizeOf(item.kind, scale);
    const head = item.kind === "hat";
    const k = take(wornCount, `${item.worn_by}|${head ? "head" : "feet"}`);
    if (head) {
      put(item.id, item.kind, scale, wearer.x + spread(k, 14), wearer.y - wearer.h / 2 + 0.2 * box.h);
    } else {
      const side = k % 2 === 0 ? -1 : 1;
      put(item.id, item.kind, scale, wearer.x + side * (0.22 * wearer.w + Math.floor(k / 2) * 14), wearer.y + wearer.h / 2 - box.h / 2);
    }
  }

  // 3. 물건을 둘로 가른다: ref 에 기대는 것(relative)과 격자에 앉는 것(flow)
  const relative = new Set(items.filter((item) => !wornIds.has(item.id) && anchored(item, byId, wornIds)).map((item) => item.id));
  const flow = items.filter((item) => !wornIds.has(item.id) && !relative.has(item.id));

  // 4. 격자에 앉히기 (장면 순서대로)
  const cellW = w / COLS;
  const grids = { ground: new Map(), sky: new Map() };
  let overflow = 0;
  for (const item of flow) {
    const scale = scaleOf(item.size);
    const box = sizeOf(item.kind, scale);
    const zone = zoneOf(item, zones);
    const sky = zone === "sky";
    const grid = sky ? grids.sky : grids.ground;
    const rows = sky ? SKY_ROWS : GROUND_ROWS;
    const baseline = (row) => (sky ? SKY_FIRST + row * ROW_STEP : GROUND_FRONT - row * ROW_STEP) * h;
    const span = Math.max(1, Math.ceil(box.w / cellW - 1e-9));
    const fits = (row, col) => {
      for (let c = col; c < col + span; c += 1) if (grid.get(`${row}:${c}`)) return false;
      const x = (col + span / 2) * cellW;
      const there = { left: x - box.w / 2, right: x + box.w / 2, top: baseline(row) - box.h, bottom: baseline(row) };
      return sky || !actorBoxes.some((actor) => overlaps(there, actor));
    };
    const find = (from, to) => {
      for (let row = 0; row < rows; row += 1) {
        for (let col = from; col + span - 1 <= to; col += 1) if (fits(row, col)) return { row, col };
      }
      return null;
    };
    const [from, to] = ZONE_COLS[zone];
    let slot = find(from, to) ?? (from === 0 && to === COLS - 1 ? null : find(0, COLS - 1));
    let drift = 0;
    if (slot === null) { // 격자가 다 찼다: 겹치더라도 서로 다른 자리에
      slot = { row: overflow % rows, col: Math.min((overflow * 5) % COLS, Math.max(0, COLS - span)) };
      overflow += 1;
      drift = overflow * 11; // 늘 어긋나게: 같은 칸에 앉은 다른 물건과 정확히 같은 자리가 되지 않는다
    } else {
      for (let c = slot.col; c < slot.col + span; c += 1) grid.set(`${slot.row}:${c}`, true);
    }
    put(item.id, item.kind, scale, (slot.col + span / 2) * cellW + drift, baseline(slot.row) - box.h / 2);
  }

  // 5. ref 에 기댄 물건: ref 가 정해진 뒤에 계산한다 (ref 도 relative 면 먼저)
  const seat = new Map(); // "rel|ref" -> 지금까지 앉은 수 (장면 순서로 번호를 매겨 결과가 resolve 순서와 무관하다)
  const turn = new Map(items.filter((item) => relative.has(item.id)).map((item) => [item.id, take(seat, `${item.place.rel}|${item.place.ref}`)]));
  const settle = (item) => {
    if (pos.has(item.id)) return;
    const { rel, ref } = item.place;
    settle(byId.get(ref));
    const at = pos.get(ref);
    const scale = scaleOf(item.size);
    const box = sizeOf(item.kind, scale);
    const k = turn.get(item.id);
    let x = at.x;
    let y = at.y;
    if (rel === "on") {
      x = at.x + spread(k, box.w + 4);
      y = at.y - at.h / 2 - box.h / 2;
    } else if (rel === "under") {
      x = at.x + spread(k, box.w + 4);
      y = at.y + at.h / 2 + box.h / 2 + 4;
    } else if (rel === "next_to") {
      x = besideX(at, box, k, w);
      y = at.y + at.h / 2 - box.h / 2;
    } else {
      x = at.x + spread(k, 18);
    }
    put(item.id, item.kind, scale, x, y);
  };
  for (const item of items) if (relative.has(item.id)) settle(item);

  const out = {};
  for (const [id, p] of pos) out[id] = { x: round(p.x), y: round(p.y), scale: p.scale };
  return out;
}

// ---------------------------------------------------------------- 조각

function sizeOf(kind, scale) {
  const fp = footprint(kind);
  return { w: fp.w * scale, h: fp.h * scale };
}

function zoneOf(item, zones) {
  const zone = item.place?.zone;
  if (typeof zone === "string" && Object.hasOwn(ZONE_COLS, zone) && (zones === null || zones.includes(zone))) return zone;
  return DEFAULT_ZONE.get(item.kind) ?? "ground";
}

/** 가리키는 물건(ref)이 있고 고리 없이 입은 물건이나 격자에 앉는 물건에서 끝나는가. */
function anchored(item, byId, wornIds) {
  const seen = new Set();
  let current = item;
  while (isRelative(current, byId)) {
    if (seen.has(current.id)) return false;
    seen.add(current.id);
    current = byId.get(current.place.ref);
    if (wornIds.has(current.id)) return true;
  }
  return current !== item;
}

function isRelative(item, byId) {
  const place = item.place;
  return Boolean(place) && RELATIONS.has(place.rel) && typeof place.ref === "string" && place.ref !== item.id && byId.has(place.ref);
}

/** ref 옆 k 번째 자리의 x: 오른쪽, 왼쪽, 오른쪽 한 칸 더, ... 순서에서 화면 안에 들어오는 k 번째. */
function besideX(at, box, k, viewW) {
  const reach = (n) => at.w / 2 + box.w / 2 + GAP + Math.floor(n / 2) * (box.w + GAP);
  const fitting = [];
  for (let n = 0; fitting.length <= k && n < 2 * k + 4; n += 1) {
    const x = at.x + (n % 2 === 0 ? 1 : -1) * reach(n);
    if (x - box.w / 2 >= 0 && x + box.w / 2 <= viewW) fitting.push(x);
  }
  return fitting[k] ?? at.x + reach(k); // 들어갈 곳이 없으면 밀려난 채로 (put 이 화면 안으로 민다)
}

/** 같은 자리를 나눠 쓰는 k 번째의 좌우 어긋남: 0, +step, -step, +2step, -2step, ... */
function spread(k, step) {
  return k === 0 ? 0 : (k % 2 === 1 ? 1 : -1) * Math.ceil(k / 2) * step;
}

function take(counts, key) {
  const n = counts.get(key) ?? 0;
  counts.set(key, n + 1);
  return n;
}

function boxOf(p) {
  return { left: p.x - p.w / 2, right: p.x + p.w / 2, top: p.y - p.h / 2, bottom: p.y + p.h / 2 };
}

function overlaps(a, b) {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

function clamp(value, low, high) {
  return high < low ? (low + high) / 2 : Math.min(Math.max(value, low), high);
}
