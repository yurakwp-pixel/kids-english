// 턴 끝내기: 답을 보였으면 끝, 아니면 대체 대사. DOM 을 모르는 작은 함수 둘이라 node 로 시험한다(tests/js/turn_end.test.mjs).
//
// main.js 의 runTurn 은 reply 줄이 오면 startShowing 으로 "장면 연출을 기다렸다가 LINE_REPLY 보내기"를 시작하고,
// 서버와의 일이 다 끝나면(성공이든 오류든) endTurn 을 부른다. 기다리기, dispatch, 그리기 어디서 throw/reject 하든,
// reply 줄이 아예 오지 않았든(null), 리듀서가 답을 받지 않았든(false), 마지막에는 대체 대사(fallback)를 낸다.
// 그래서 화면이 "생각 중"(thinking)에 갇혀 마이크가 잠긴 채로 남지 않는다. 두 함수 모두 reject 하지 않는다.

/**
 * wait() 가 끝나면 show() 를 부른다. show 는 리듀서가 답을 받았으면 true 를 돌려준다(main.js 의 dispatch).
 * 결과는 `{ shown }` 또는 `{ shown: false, error }`. throw/reject 도 값으로 바꿔 두어서, endTurn 이 받아 보기 전에
 * "처리 안 된 거절"이 되지 않는다. wait 가 실패하면 show 는 부르지 않는다(대체 대사가 대신한다).
 */
export function startShowing(wait, show) {
  return Promise.resolve()
    .then(wait)
    .then(show)
    .then((shown) => ({ shown: shown === true }), (error) => ({ shown: false, error }));
}

/**
 * 턴의 끝. `showing` 은 startShowing 의 결과(reply 줄이 오지 않았으면 null)다.
 * 답이 보였으면 true. 아니면 fallback() 을 부르고 false. 오류는 onError 로만 알린다(그것이 throw 해도 삼킨다).
 */
export async function endTurn(showing, fallback, onError = () => {}) {
  let result = { shown: false };
  try {
    if (showing) result = await showing;
  } catch (error) {
    result = { shown: false, error };
  }
  if (result && result.error !== undefined) report(onError, result.error);
  if (result && result.shown === true) return true;
  try {
    fallback();
  } catch (error) {
    report(onError, error);
  }
  return false;
}

function report(onError, error) {
  try {
    onError(error);
  } catch {
    // 알리는 쪽(디버그 패널)이 깨져도 턴은 끝난다
  }
}
