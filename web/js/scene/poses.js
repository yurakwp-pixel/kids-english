// 아기 공룡의 동작 포즈 표 (스펙 6장). 동작 하나를 더할 때 그림을 새로 그리지 않고 이 표에 한 줄만 더한다.
//
// 포즈 = 가만히 서 있는 모습(idle)에서 부위별로 얼마나 움직이는가. 적은 부위만 움직이고 나머지는 제자리다.
// 렌더러(render.js)는 부위 그룹마다 이 값을 CSS transform 으로 바꾸고 transition 으로 부드럽게 잇는다.
//
// 약속 (모든 값은 렌더러가 부위 그룹에 `rotate(rot deg) translate(dx px, dy px)` 로 쓴다):
//   - rot: 도(degree), 시계 방향이 +. 돌리는 축은 그 부위가 몸에 붙는 곳(어깨, 엉덩이, 목, 꼬리 뿌리, 몸은 발바닥 가운데).
//   - dx, dy: 화면 픽셀 단위 이동(공룡 그림의 기준 크기에서). 오른쪽이 +, 아래쪽이 +.
//   - 공룡은 오른쪽(멍멍이 쪽)을 본다. armL/armR/legL/legR 은 보는 사람 기준 왼쪽/오른쪽이다.
//   - 아래로 늘어진 팔다리는 rot > 0 이면 끝이 왼쪽(공룡의 뒤)으로, rot < 0 이면 오른쪽(앞)으로 간다.
//     그래서 armL 을 +100 쯤 돌리면 왼쪽 옆으로 들리고, armR 을 -100 쯤 돌리면 오른쪽 옆으로 들린다.
//   - body 의 rot 이 + 이면 위쪽이 오른쪽(앞)으로 기울고, dy 가 + 이면 몸 전체가 내려앉는다.
//     body 는 공룡 전체(나머지 부위의 부모)에 걸린다. head 의 rot 이 + 이면 고개가 앞으로 숙는다.
//     tail 의 rot 이 + 이면 꼬리 끝이 위로 든다.
//
// 숫자는 첫 초안이다. 보면서 고치는 것은 Task 15(렌더링)의 미리보기 확인에서 한다. 이 표의 약속(부위 이름, 단위)만 지키면 된다.
// 'turn_around' 의 좌우 뒤집기처럼 몸 부위 값으로 표현할 수 없는 움직임은 렌더러가 동작 이름을 보고 따로 더한다.

export const PARTS = Object.freeze(["body", "head", "armL", "armR", "legL", "legR", "tail"]);

function freezeDeep(table) {
  for (const pose of Object.values(table)) {
    for (const move of Object.values(pose)) Object.freeze(move);
    Object.freeze(pose);
  }
  return Object.freeze(table);
}

export const POSES = freezeDeep({
  // 가만히 서 있기. 다른 모든 포즈의 기준이다.
  idle: {},

  // 알에서 튀어나온 순간: 폴짝 솟아 두 팔을 번쩍.
  hatch: { body: { dy: -10 }, head: { rot: -8, dy: -2 }, armL: { rot: 70 }, armR: { rot: -70 }, tail: { rot: 25 } },
  // 돌다 만 순간: 고개와 꼬리가 먼저 돌아가고 발이 엇갈린다 (좌우 뒤집기는 렌더러가 더한다).
  turn_around: { body: { rot: -6 }, head: { rot: -20 }, legL: { rot: -15 }, legR: { rot: 15 }, tail: { rot: -30 } },
  // 성큼 한 걸음.
  walk: { body: { dy: -2 }, armL: { rot: -20 }, armR: { rot: 20 }, legL: { rot: 25 }, legR: { rot: -25 }, tail: { rot: 10 } },
  // 몸을 낮추고 두 팔로 얼굴을 가린다.
  hide: { body: { dy: 24 }, head: { rot: 15, dy: 10 }, armL: { rot: 150 }, armR: { rot: -150 }, legL: { rot: 10 }, legR: { rot: -10 } },
  // 앞다리를 쭉 뻗어 찬다. 몸은 살짝 뒤로.
  kick: { body: { rot: -8 }, armL: { rot: 50 }, armR: { rot: -30 }, legL: { rot: 10 }, legR: { rot: -60 }, tail: { rot: 20 } },
  // 두 팔을 위로 쭉.
  stretch_arms: { body: { dy: -3 }, head: { rot: -5, dy: -2 }, armL: { rot: 140 }, armR: { rot: -140 }, tail: { rot: 15 } },
  // 두 팔을 가슴 앞에서 엇갈려 접는다.
  fold_arms: { head: { rot: 3 }, armL: { rot: -75, dx: 6, dy: -6 }, armR: { rot: 75, dx: -6, dy: -6 } },
  // 오른팔(앞쪽)을 번쩍 든다.
  thumbs_up: { body: { dy: -1 }, head: { rot: 5 }, armL: { rot: 10 }, armR: { rot: -120, dy: -4 }, tail: { rot: 10 } },
  // 몸이 위로 올라가고 팔다리가 엇갈려 오른다.
  climb_up: { body: { rot: 6, dy: -14 }, armL: { rot: 160 }, armR: { rot: -120 }, legL: { rot: -30 }, legR: { rot: 35 } },
  // 고개를 젖히고 왼팔로 입을 가린다.
  yawn: { body: { rot: -3, dy: 1 }, head: { rot: -18, dy: -3 }, armL: { rot: 140 }, armR: { rot: -20 } },
  // 에취: 고개와 몸이 앞으로 확 숙는다.
  sneeze: { body: { rot: 10, dy: 2 }, head: { rot: 22, dy: 4 }, armR: { rot: -100 }, tail: { rot: -15 } },
  // 가슴을 펴고 두 팔을 옆으로 크게.
  deep_breath: { body: { dy: -4 }, head: { rot: -8, dy: -3 }, armL: { rot: 95 }, armR: { rot: -95 }, tail: { rot: 12 } },
  // 허리를 숙여 바닥을 정리한다.
  clean_up: { body: { rot: 18, dy: 6 }, head: { rot: 15 }, armL: { rot: -40 }, armR: { rot: -55 }, legL: { rot: 5 }, legR: { rot: -5 } },
  // 팔을 위로 들어 앞으로 던진다.
  throw_out: { body: { rot: -10 }, armL: { rot: 30 }, armR: { rot: -140 }, legL: { rot: 12 }, legR: { rot: -12 }, tail: { rot: -10 } },
  // 몸을 숙이고 한 발을 들어 양말을 신는다.
  put_on: { body: { rot: 14, dy: 8 }, head: { rot: 14 }, armL: { rot: -60 }, armR: { rot: -70 }, legR: { rot: -35, dy: -6 } },
  // 두 손을 모아 쓱쓱 문지른다.
  wash_hands: { head: { rot: 6 }, armL: { rot: -45, dx: 8, dy: -8 }, armR: { rot: 45, dx: -8, dy: -8 } },
  // 눕는다: 몸을 뒤로 눕히고 팔다리는 편하게.
  go_to_bed: { body: { rot: 90, dx: -10, dy: 35 }, head: { rot: -10 }, armL: { rot: 20 }, armR: { rot: -20 }, tail: { rot: -10 } },
  // 두 손이 가슴 앞에서 마주친다.
  clap: { body: { dy: -2 }, head: { rot: -4 }, armL: { rot: -95, dy: -6 }, armR: { rot: 95, dy: -6 } },
  // 몸을 흔들고 한 팔은 위로, 한 발은 들고.
  dance: { body: { rot: 8, dy: -8 }, head: { rot: -12 }, armL: { rot: 140 }, armR: { rot: -60 }, legL: { rot: 20 }, legR: { rot: -10, dy: -8 }, tail: { rot: -30 } },
  // 오른팔을 높이 들어 손바닥을 마주친다.
  high_five: { body: { rot: -4, dy: -2 }, head: { rot: -6 }, armL: { rot: 15 }, armR: { rot: -165 }, tail: { rot: 12 } },
});

/** 동작 이름의 포즈. 표에 없는 이름(`__proto__`, `constructor` 같은 것도)이면 idle. */
export function poseFor(action) {
  return typeof action === "string" && Object.hasOwn(POSES, action) ? POSES[action] : POSES.idle;
}
