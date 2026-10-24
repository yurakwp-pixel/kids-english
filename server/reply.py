"""LLM 의 답을 읽고(parse_reply_text) 검증하는(validate_reply) 안전망.

LLM 이 무엇을 내보내든(코드펜스, 설명문, 잘린 JSON, 엉뚱한 타입, 한글 `say`, 모르는 키) 서버 밖으로 나가는 답은
언제나 `shared/schemas/turn_reply.schema.json` 을 만족한다. 필드마다 따로 고치고, 고친 내역은 issue 문자열로 남긴다.
고칠 수 없는 필드는 "없음"에 해당하는 기본값(null, [], 기본 utterance)으로 바꾸고, `say` 가 통째로 못 쓸 때는
`server.canned` 의 대사로 바꾼다. 놀이는 멈추지 않는다.

ops 는 카탈로그(`server.catalog.Catalog`)가 있어야만 검사해서 통과시킨다. 카탈로그가 없으면 모두 버린다(M1).
통과한 op 는 fastpath(`server.fastpath`)가 내는 것과 같은 모양으로 새로 만든다(모르는 키는 버려진다).

issue 문자열 (고친 것이 있을 때만 들어간다):
  reply_not_object                  답이 JSON 객체가 아님 -> 통째로 canned "error" 답
  say_missing / say_not_string / say_empty
  say_hangul / say_markup / say_url  문제가 있는 문장만 빼고 나머지를 쓴다
  say_negative                      "wrong"/"incorrect" 가 낱말로 든 문장만 뺀다(안티골 A1). "No" 는 걸러내지 않는다
  say_too_long                      15단어(200자)를 넘는 뒤쪽 문장을 뺀다
  say_replaced                      쓸 수 있는 문장이 없어 canned 대사로 바꿈(korean_hint 도 짝에 맞춰 바꾸고 choice 는 버린다)
  recast_invalid / recast_negative / recast_ask_repeat_defaulted
  choice_invalid / choice_item_dropped / choice_negative / choice_trimmed / choice_too_few
  korean_hint_invalid / korean_hint_unsafe
  ops_invalid                       ops 가 목록이 아님
  ops_dropped_hangul                한글이 든 입력에는 ops 가 없어야 한다
  ops_dropped_lang_ko               답의 utterance.lang 이 "ko" 다(스펙 §4): 아이의 한국어는 en-US 인식기에서 영어 글자처럼 와서 한글 검사로 못 잡는다
  ops_dropped_say_replaced          말 전체가 canned 대사로 바뀌었다: "다시 해 보자" 와 바뀐 장면이 섞이지 않게 ops 도 버린다
  ops_dropped_no_catalog            카탈로그 없이는 아무 op 도 통과시키지 않는다
  ops_item_dropped                  카탈로그/장면/안전 규칙에 맞지 않는 op 를 버렸다
  ops_already_applied               fastpath 가 이미 적용한 op 를 LLM 이 되풀이해서 버렸다
  ops_fixed                         개수를 1~max_count 로 자르거나, 못 가지는 속성을 떼거나, 쓸 수 없는 id 를 새로 정했다
  ops_trimmed                       4개(스키마 한도)를 넘는 뒤쪽 op 를 버렸다
  scene_full                        이 add 로 장면 물건이 max_items 를 넘는다(op 는 남기고 정리는 JS 장면 모델이 한다)
  mood_invalid                      카탈로그가 있으면 그 캐릭터의 표정 목록에 없는 것도 여기에 든다
  utterance_missing / utterance_invalid / utterance_lang_invalid / words_used_fixed / errors_fixed
"""
from __future__ import annotations

import json
import re
from typing import Any, Sequence

from server.canned import fallback_reply
from server.catalog import ACTORS, Catalog
from server.ids import next_ids
from server.text import has_hangul

MAX_SAY_WORDS = 15
MAX_ENGLISH_CHARS = 200  # say / recast.model / choice 한 개. 15단어여도 글자 수가 말이 안 되면 거른다.
MAX_HINT_CHARS = 150
MIN_CHOICES, MAX_CHOICES = 2, 3
MAX_OPS = 4  # 스키마의 maxItems 와 같은 값. 카탈로그의 max_ops 가 더 커도 이 값을 넘기지 않는다.
MAX_OPS_SCANNED = 40  # 아주 긴 ops 목록을 끝까지 읽지 않는다: 살아남은 4개가 차면 어차피 멈춘다
MAX_WORDS_USED, MAX_WORD_ID_CHARS = 20, 60
MAX_ERRORS, MAX_ERROR_VALUE_CHARS, MAX_ERROR_KEY_CHARS = 10, 100, 30

LANGS = ("en", "ko", "mixed", "unknown")

_URL = re.compile(
    r"[a-z][a-z0-9+.-]*://|www\.|\b[a-z0-9-]+\.(?:com|net|org|kr|io|app|dev|tv|xyz)\b",
    re.IGNORECASE,
)
_ALNUM = re.compile(r"[A-Za-z0-9]")
_FACE = re.compile(r"[a-z][a-z-]{0,19}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# 안티골 A1: 아이 말이 틀려도 "Wrong" 이라고 하지 않는다. 낱말 그대로만 본다("Wrongly", "Strong" 은 괜찮다). "No" 는 일부러 거르지 않는다.
_NEGATIVE = re.compile(r"(?<![A-Za-z])(?:wrong|incorrect)(?![A-Za-z])", re.IGNORECASE)
_ID_TAIL = r"[1-9][0-9]{0,8}"  # 장면 id = 카탈로그 이름 + 번호 (server/ids.py)
# 때리거나 내쫓는 동작은 캐릭터를 대상으로 하지 않는다(CLAUDE.md: 때리기·괴롭히기 없음). chomp/put_on 은 음식/입는 것만 받으니 따로 막을 필요가 없다.
_NEVER_AT_CHARACTERS = frozenset({"kick", "throw_out"})
_OBJECT_START = re.compile(r"\{\s*[\"}]")  # JSON 객체처럼 보이는 여는 중괄호: 설명문 속 `{is}` 같은 것은 건너뛴다
_DECODER = json.JSONDecoder()


def parse_reply_text(text: str) -> dict | None:
    """텍스트 안에서 JSON 객체 하나를 찾아 dict 로 돌려준다. 없으면 None.

    코드펜스와 앞뒤 설명문은 괜찮다. 규칙: JSON 객체처럼 보이는 첫 `{` 가 답을 정한다. 그 객체가 잘렸거나
    문법이 틀렸으면 None 이다(안쪽 조각을 답으로 착각하지 않는다). 너무 깊은 중첩도 None.
    """
    if not isinstance(text, str):
        return None
    match = _OBJECT_START.search(text)
    if match is None:
        return None
    try:
        value, _end = _DECODER.raw_decode(text, match.start())
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def validate_reply(
    obj: Any,
    *,
    turn_id: int,
    catalog: Catalog | None = None,
    scene: Sequence[dict] = (),
    applied_ops: Sequence[dict] = (),
    input_has_hangul: bool = False,
) -> tuple[dict, list[str]]:
    """어떤 입력이 와도 스키마를 만족하는 답과, 고친 내역(issues)을 돌려준다. 예외를 내지 않는다.

    `catalog is None` 이면 `ops` 는 모두 버린다(M1). 있으면 `_validate_ops` 가 검사하고, `mood` 의 표정도 그 캐릭터의 목록과 맞춘다.
    `scene` 은 장면의 물건 목록, `applied_ops` 는 fastpath 가 이미 적용한 op 다(LLM 이 되풀이하면 버린다).
    """
    turn_id = turn_id if isinstance(turn_id, int) and not isinstance(turn_id, bool) else 0
    if not isinstance(obj, dict):
        return fallback_reply("error", turn_id), ["reply_not_object"]

    issues: list[str] = []
    say = _clean_say(obj, issues)
    say_replaced = say is None
    if say is None:
        # 말이 바뀌었으니, 그 말에 딸린 보기(choice)는 질문 없이 남지 않게 버리고 힌트는 canned 짝으로 바꾼다.
        canned = fallback_reply("error", turn_id)
        say, korean_hint, choice = canned["say"], canned["korean_hint"], None
        _note(issues, "say_replaced")
    else:
        choice = _clean_choice(obj.get("choice"), issues)
        korean_hint = _clean_korean_hint(obj.get("korean_hint"), issues)
    recast = _clean_recast(obj.get("recast"), issues)
    utterance_issues: list[str] = []  # issues 의 차례(ops 다음)를 지키려고 따로 모은다
    utterance = _clean_utterance(obj.get("utterance"), utterance_issues)  # ops 검사에 lang 이 필요해서 먼저 정리한다
    ops, ops_issues = _validate_ops(
        obj.get("ops"),
        catalog=catalog,
        scene=scene,
        applied_ops=applied_ops,
        input_has_hangul=input_has_hangul,
        say_replaced=say_replaced,
        lang_ko=utterance["lang"] == "ko",
    )
    issues.extend(ops_issues)
    mood = _clean_mood(obj.get("mood"), issues, catalog)
    issues.extend(utterance_issues)

    reply = {  # 키 순서는 스펙 5.5 그대로, `say` 가 첫 키. 모르는 키는 여기서 버려진다.
        "say": say,
        "recast": recast,
        "choice": choice,
        "korean_hint": korean_hint,
        "ops": ops,
        "mood": mood,
        "utterance": utterance,
    }
    return reply, issues


# ---------------------------------------------------------------- ops


def _validate_ops(
    raw: Any,
    *,
    catalog: Catalog | None,
    scene: Sequence[dict],
    applied_ops: Sequence[dict],
    input_has_hangul: bool,
    say_replaced: bool = False,
    lang_ko: bool = False,
) -> tuple[list[dict], list[str]]:
    """LLM 의 ops 를 검사해 (살아남은 ops, issues) 를 돌려준다.

    통째로 버리는 경우(앞의 것이 먼저): 목록이 아님, 한글 입력, 답의 `utterance.lang` 이 "ko", `say` 가 canned 대사로 바뀜, 카탈로그 없음.
    그 밖에는 `_OpsChecker` 가 op 를 하나씩 본다.
    """
    if raw is None or raw == []:
        return [], []
    if not isinstance(raw, list):
        return [], ["ops_invalid"]
    if input_has_hangul:  # 한글이 들어간 입력의 답에는 ops 가 없어야 한다(전역 제약)
        return [], ["ops_dropped_hangul"]
    if lang_ko:  # LLM 이 "아이가 한국어로 말했다" 고 봤다: 화면은 바꾸지 않고 영어로 따라 말하게만 한다(스펙 §4)
        return [], ["ops_dropped_lang_ko"]
    if say_replaced:  # "Hmm! Let's try that again!" 와 바뀐 장면이 같이 나가면 아이가 헷갈린다
        return [], ["ops_dropped_say_replaced"]
    if catalog is None:
        return [], ["ops_dropped_no_catalog"]
    return _OpsChecker(catalog, scene, applied_ops).check(raw)


def _name(value: Any) -> str | None:
    """카탈로그 이름으로 쓸 글자: 앞뒤 공백을 떼고 소문자로. 문자열이 아니면 None."""
    return value.strip().lower() if isinstance(value, str) else None


def _signature(op: Any) -> str | None:
    """두 op 가 같은 조작인지 비교하는 열쇠. `add` 의 id 는 서버가 정하는 값이라 빼고(LLM 은 그 id 를 모른다), 개수는 채워 넣는다."""
    if not isinstance(op, dict):
        return None
    try:
        sig = {key: value for key, value in op.items() if key not in ("id", "ids")}
        if op.get("op") == "add" and "count" not in sig:
            many = op.get("ids")
            sig["count"] = len(many) if isinstance(many, list) and many else 1
        return json.dumps(sig, sort_keys=True, default=repr)
    except (TypeError, ValueError, RecursionError):
        return None


class _OpsChecker:
    """한 답의 ops 를 앞에서부터 하나씩 검사해, 통과한 것만 fastpath 와 같은 모양의 새 dict 로 모은다.

    기억하는 것: 장면 물건과 이미 적용한 op(applied_ops)가 만든 id, 이 답에서 앞서 통과한 op 가 만들거나 지운 물건.
    그래서 "add 하고 그걸 chomp 하기" 는 되고, "remove 한 것을 또 kick" 은 안 된다. 캐릭터(chomp, dino)는 장면에 안 적혀 있어도 늘 대상이다.

    규칙: 이름이 카탈로그에 없거나 값이 틀리면 op 를 버린다. 값은 맞는데 그 물건이 못 가지는 속성(사과에 색)은 fastpath 처럼 속성만 떼고,
    속성이 하나도 안 남는 set 은 버린다. 멍멍이는 음식만 물고(chomp), put_on 은 입을 수 있는 것만. 캐릭터를 차거나(kick) 내던지지(throw_out) 않고,
    캐릭터는 remove/move 하지 않는다. 자리(place)의 ref 는 장면의 물건만 되고, 서로 얹는 고리는 막는다.
    """

    def __init__(self, catalog: Catalog, scene: Any, applied_ops: Any) -> None:
        self.catalog = catalog
        self.scene = list(scene) if isinstance(scene, (list, tuple)) else []
        self.applied = [op for op in applied_ops if isinstance(op, dict)] if isinstance(applied_ops, (list, tuple)) else []
        self.kinds: dict[str, str] = {actor: actor for actor in ACTORS}  # 지금 대상이 될 수 있는 id -> kind ("" = 모름)
        self.used: set[str] = set(ACTORS)  # 한 번이라도 쓰인 id: 지워져도 다시 쓰지 않는다(server/ids.py)
        self.places: dict[str, str] = {}  # id -> 그것이 기대어 있는 물건의 id (고리 검사용)
        self.live: set[str] = set()  # 장면에 있는 물건 id (scene_full 셈)
        self.accepted: list[dict] = []
        self.issues: list[str] = []
        for entry in self.scene:
            if isinstance(entry, dict):
                self._see(entry.get("id"), entry.get("kind"), entry.get("place"))
        for op in self.applied:
            self._see_applied(op)
        self.applied_sigs = {sig for sig in map(_signature, self.applied) if sig is not None}

    # ---- 전체

    def check(self, raw: list) -> tuple[list[dict], list[str]]:
        limit = min(self.catalog.max_ops, MAX_OPS)
        trimmed = len(raw) > MAX_OPS_SCANNED
        for item in raw[:MAX_OPS_SCANNED]:
            if len(self.accepted) >= limit:
                trimmed = True
                break
            self._one(item)
        if trimmed:
            _note(self.issues, "ops_trimmed")
        return self.accepted, self.issues

    def _one(self, item: Any) -> None:
        name = _name(item.get("op")) if isinstance(item, dict) else None
        op = getattr(self, "_op_" + name)(item) if name in ("add", "set", "move", "remove", "act") else None
        if op is None:
            _note(self.issues, "ops_item_dropped")
            return
        if _signature(op) in self.applied_sigs:
            _note(self.issues, "ops_already_applied")
            return
        self._commit(op)

    def _commit(self, op: dict) -> None:
        name = op["op"]
        if name == "add":
            for ident in op.get("ids") or [op["id"]]:
                self._see(ident, op["kind"], op.get("place"))
            if len(self.live) > self.catalog.max_items:
                _note(self.issues, "scene_full")  # 버리지 않는다: 오래된 것부터 치우는 건 JS 장면 모델이 한다
        elif name == "remove":
            self._forget(op["target"])
        elif name == "move":
            self._set_place(op["target"], op["place"])
        self.accepted.append(op)

    # ---- 장면 기억

    def _see(self, ident: Any, kind: Any, place: Any) -> None:
        if not isinstance(ident, str) or not ident or ident in ACTORS:
            return
        kind = kind if isinstance(kind, str) else ""
        self.used.add(ident)
        self.kinds[ident] = kind
        self._set_place(ident, place)
        if kind in self.catalog.objects:
            self.live.add(ident)

    def _see_applied(self, op: dict) -> None:
        name = _name(op.get("op"))
        if name == "add":
            many = op.get("ids")
            for ident in many if isinstance(many, list) else [op.get("id")]:
                self._see(ident, op.get("kind"), op.get("place"))
        elif name == "remove":
            self._forget(op.get("target"))
        elif name == "move" and isinstance(op.get("target"), str) and op["target"] in self.kinds:
            self._set_place(op["target"], op.get("place"))

    def _forget(self, ident: Any) -> None:
        if isinstance(ident, str) and ident not in ACTORS:
            self.kinds.pop(ident, None)
            self.places.pop(ident, None)
            self.live.discard(ident)

    def _set_place(self, ident: str, place: Any) -> None:
        ref = place.get("ref") if isinstance(place, dict) else None
        if isinstance(ref, str):
            self.places[ident] = ref
        else:
            self.places.pop(ident, None)

    def _is_object(self, ident: Any) -> bool:
        return isinstance(ident, str) and self.kinds.get(ident, "") in self.catalog.objects

    def _fixed(self) -> None:
        _note(self.issues, "ops_fixed")

    # ---- 조각: 개수, 속성, 자리, id

    def _count(self, item: dict) -> int | None:
        raw = item.get("count")
        if raw is None:
            many = item.get("ids")
            raw = len(many) if isinstance(many, list) and many else 1
        elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        elif isinstance(raw, float):
            if not raw.is_integer():  # 2.5, nan, inf
                return None
            raw = int(raw)
        count = max(1, min(raw, self.catalog.max_count))
        if count != raw:
            self._fixed()
        return count

    def _attrs(self, item: dict, kind: str) -> dict[str, str] | None:
        """color/size. 값이 카탈로그에 없으면 None(op 를 버린다). 그 물건이 못 가지는 속성은 조용히 뗀다."""
        catalog, attrs = self.catalog, {}
        for key, allowed, can in (("color", catalog.colors, catalog.can_color), ("size", catalog.sizes, catalog.can_size)):
            if item.get(key) is None:
                continue
            value = _name(item[key])
            if value is None or value not in allowed:
                return None
            if can(kind):
                attrs[key] = value
            else:
                self._fixed()
        return attrs

    def _place(self, raw: Any, own: str | None) -> dict | None:
        """{"zone": Z} 또는 {"rel": R, "ref": 물건 id}. 아니면 None. `own` 은 자리를 옮기는 물건(자기 위에 놓는 고리를 막는다)."""
        if not isinstance(raw, dict):
            return None
        raw = {key: value for key, value in raw.items() if value is not None}
        if "zone" in raw:
            zone = _name(raw["zone"])
            return {"zone": zone} if "rel" not in raw and "ref" not in raw and zone in self.catalog.zones else None
        rel, ref = _name(raw.get("rel")), raw.get("ref")
        if rel in self.catalog.relations and self._is_object(ref) and not self._makes_loop(own, ref):
            return {"rel": rel, "ref": ref}
        return None

    def _makes_loop(self, own: str | None, ref: str) -> bool:
        """own 을 ref 에 얹으면 ref -> ... -> own 으로 되돌아오는 고리가 되나. 장면에 이미 있는 고리에서도 멈춘다."""
        seen: set[str] = set()
        current: str | None = ref
        while current is not None and current not in seen:
            if current == own:
                return True
            seen.add(current)
            current = self.places.get(current)
        return False

    def _new_ids(self, item: dict, kind: str, count: int) -> list[str]:
        """LLM 이 낸 id 가 모양이 맞고, 안 쓰였고, 개수와 같으면 그대로(뒤의 op 가 그 id 를 가리킬 수 있다). 아니면 서버가 새로 정한다."""
        many, one = item.get("ids"), item.get("id")
        asked = many if isinstance(many, list) else ([] if one is None else [one])
        if asked and len(asked) == count and all(self._free_id(ident, kind) for ident in asked) and len(set(asked)) == count:
            return list(asked)
        if asked:
            self._fixed()
        return next_ids(kind, count, self.scene, [*self.applied, *self.accepted])

    def _free_id(self, ident: Any, kind: str) -> bool:
        return (
            isinstance(ident, str)
            and re.fullmatch(re.escape(kind) + _ID_TAIL, ident) is not None
            and ident not in self.used
        )

    # ---- op 종류별 검사. 통과하면 새 dict, 아니면 None

    def _op_add(self, item: dict) -> dict | None:
        kind = _name(item.get("kind"))
        if kind not in self.catalog.objects:
            return None
        count, attrs = self._count(item), self._attrs(item, kind)
        place = None
        if item.get("place") is not None:
            place = self._place(item["place"], None)
            if place is None:
                return None
        if count is None or attrs is None:
            return None
        ids = self._new_ids(item, kind, count)
        op: dict[str, Any] = {"op": "add", "kind": kind}
        if count == 1:
            op["id"] = ids[0]
        else:
            op["ids"] = ids
        op["count"] = count
        op.update(attrs)
        if place is not None:
            op["place"] = place
        return op

    def _op_act(self, item: dict) -> dict | None:
        actor, action = _name(item.get("actor")), _name(item.get("action"))
        if action is None or action not in self.catalog.actor_actions(actor):
            return None
        op: dict[str, Any] = {"op": "act", "actor": actor, "action": action}
        target = item.get("target")
        if target is not None:
            if not isinstance(target, str) or target not in self.kinds:
                return None
            op["target"] = target
        kind = self.kinds.get(target, "") if isinstance(target, str) else ""
        if action == "chomp" and not self.catalog.is_food(kind):  # 멍멍이는 음식만 문다 (대상이 없어도 안 된다)
            return None
        if action == "put_on" and not self.catalog.is_wearable(kind):
            return None
        if action in _NEVER_AT_CHARACTERS and target in ACTORS:
            return None
        return op

    def _op_set(self, item: dict) -> dict | None:
        target = item.get("target")
        if not isinstance(target, str) or target not in self.kinds:
            return None
        if target in ACTORS:  # 표정: 캐릭터에게만, 그 캐릭터의 표정 목록 안에서
            face = _name(item.get("face"))
            if face is None or face not in self.catalog.actor_faces(target):
                return None
            if item.get("color") is not None or item.get("size") is not None:
                self._fixed()
            return {"op": "set", "target": target, "face": face}
        if not self._is_object(target):
            return None
        attrs = self._attrs(item, self.kinds[target])
        if not attrs:  # 값이 틀렸거나(None), 이 물건이 가질 수 있는 속성이 하나도 없다({})
            return None
        if item.get("face") is not None:
            self._fixed()
        return {"op": "set", "target": target, **attrs}

    def _op_move(self, item: dict) -> dict | None:
        target = item.get("target")
        if not self._is_object(target):
            return None
        place = self._place(item.get("place"), target)
        return None if place is None else {"op": "move", "target": target, "place": place}

    def _op_remove(self, item: dict) -> dict | None:
        target = item.get("target")
        return {"op": "remove", "target": target} if self._is_object(target) else None


# ---------------------------------------------------------------- 필드별 고치기


def _note(issues: list[str], name: str) -> None:
    if name not in issues:
        issues.append(name)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _text_problem(text: str) -> str | None:
    """아이에게 보여 주고 읽어 줄 영어에 들어가면 안 되는 것: 한글(`server.text.has_hangul`), `<`, URL."""
    if has_hangul(text):
        return "hangul"
    if "<" in text:
        return "markup"
    if _URL.search(text):
        return "url"
    return None


def _clean_say(obj: dict, issues: list[str]) -> str | None:
    """쓸 수 있는 `say` 를 돌려준다. 문제 있는 문장은 빼고, 15단어를 넘는 뒤쪽 문장도 뺀다. 남는 게 없으면 None."""
    if "say" not in obj:
        _note(issues, "say_missing")
        return None
    raw = obj["say"]
    if not isinstance(raw, str):
        _note(issues, "say_not_string")
        return None
    text = _squash(raw)
    if not text:
        _note(issues, "say_empty")
        return None

    sentences = []
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) > MAX_ENGLISH_CHARS:
            # 한 문장이 이미 한도를 넘는다. 이 문장부터 뒤는 어차피 못 쓰니 정규식을 돌리지 않고 끊는다
            # (아주 긴 글에 URL 정규식을 돌리면 느려진다).
            _note(issues, "say_too_long")
            break
        problem = _text_problem(sentence) or ("negative" if _NEGATIVE.search(sentence) else None)
        if problem:
            _note(issues, f"say_{problem}")
        else:
            sentences.append(sentence)

    kept: list[str] = []
    words = chars = 0
    for sentence in sentences:
        words += len(sentence.split())
        chars += len(sentence) + (1 if kept else 0)
        if words > MAX_SAY_WORDS or chars > MAX_ENGLISH_CHARS:
            _note(issues, "say_too_long")
            break
        kept.append(sentence)

    result = " ".join(kept)
    if kept and not _ALNUM.search(result):  # "..." 같은 소리 없는 말
        _note(issues, "say_empty")
        return None
    return result or None


def _clean_english(raw: Any, issues: list[str], field: str) -> str | None:
    """recast.model, choice 한 개처럼 통째로 쓰거나 버리는 영어 한마디. `wrong`/`incorrect` 가 든 문장만 먼저 뺀다(A1)."""
    if not isinstance(raw, str):
        return None
    text = _squash(raw)
    if len(text) <= MAX_ENGLISH_CHARS:  # 정규식은 짧은 글에만 돌린다
        sentences = _SENTENCE_END.split(text)
        polite = [sentence for sentence in sentences if not _NEGATIVE.search(sentence)]
        if len(polite) != len(sentences):
            _note(issues, f"{field}_negative")
            text = " ".join(polite)
    if (
        not text
        or len(text) > MAX_ENGLISH_CHARS  # 길이를 먼저 본다: 정규식은 짧은 글에만 돌린다
        or len(text.split()) > MAX_SAY_WORDS
        or _text_problem(text)
        or not _ALNUM.search(text)
    ):
        return None
    return text


def _clean_recast(raw: Any, issues: list[str]) -> dict | None:
    if raw is None:
        return None
    model = _clean_english(raw.get("model"), issues, "recast") if isinstance(raw, dict) else None
    if model is None:
        _note(issues, "recast_invalid")
        return None
    ask_repeat = raw.get("ask_repeat")
    if not isinstance(ask_repeat, bool):
        ask_repeat = True  # 따라 말하게 하려고 만든 recast 다
        _note(issues, "recast_ask_repeat_defaulted")
    return {"model": model, "ask_repeat": ask_repeat}


def _clean_choice(raw: Any, issues: list[str]) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        _note(issues, "choice_invalid")
        return None
    options: list[str] = []
    for item in raw:
        text = _clean_english(item, issues, "choice")
        if text is None or text.lower() in (o.lower() for o in options):
            _note(issues, "choice_item_dropped")
        else:
            options.append(text)
    if len(options) > MAX_CHOICES:
        options = options[:MAX_CHOICES]
        _note(issues, "choice_trimmed")
    if len(options) < MIN_CHOICES:
        if raw:
            _note(issues, "choice_too_few")
        return None
    return options


def _clean_korean_hint(raw: Any, issues: list[str]) -> str | None:
    """한글이 들어갈 수 있는 유일한 필드. 모양("뜻 → English")은 강제하지 않고 안전만 본다."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        _note(issues, "korean_hint_invalid")
        return None
    text = _squash(raw)
    if not text:
        return None
    if len(text) > MAX_HINT_CHARS or "<" in text or _URL.search(text):
        _note(issues, "korean_hint_unsafe")
        return None
    return text


def _clean_mood(raw: Any, issues: list[str], catalog: Catalog | None) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict) and isinstance(raw.get("actor"), str) and isinstance(raw.get("face"), str):
        actor, face = raw["actor"].strip().lower(), raw["face"].strip().lower()
        if actor in ACTORS and _FACE.fullmatch(face) and (catalog is None or face in catalog.actor_faces(actor)):
            return {"actor": actor, "face": face}
    _note(issues, "mood_invalid")
    return None


def _clean_utterance(raw: Any, issues: list[str]) -> dict:
    result: dict = {"lang": "unknown", "words_used": [], "errors": []}
    if raw is None:
        _note(issues, "utterance_missing")
        return result
    if not isinstance(raw, dict):
        _note(issues, "utterance_invalid")
        return result

    lang = raw.get("lang")
    lang = lang.strip().lower() if isinstance(lang, str) else None
    if lang in LANGS:
        result["lang"] = lang
    else:
        _note(issues, "utterance_lang_invalid")

    words_used = raw.get("words_used", [])
    result["words_used"] = _clean_words_used(words_used)
    if result["words_used"] != words_used:
        _note(issues, "words_used_fixed")

    errors = raw.get("errors", [])
    result["errors"] = _clean_errors(errors)
    if result["errors"] != errors:
        _note(issues, "errors_fixed")
    return result


def _clean_words_used(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    ids = [item.strip() for item in raw if isinstance(item, str)]
    return [i for i in ids if i and len(i) <= MAX_WORD_ID_CHARS][:MAX_WORDS_USED]


def _clean_errors(raw: Any) -> list[dict]:
    """errors 는 `{"said": ..., "better": ...}` 같은 작은 메모다. 문자열 값만 남기고 크기를 제한한다."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        note = {
            key: value
            for key, value in item.items()
            if isinstance(key, str)
            and len(key) <= MAX_ERROR_KEY_CHARS
            and isinstance(value, str)
            and value.strip()
            and len(value) <= MAX_ERROR_VALUE_CHARS
        }
        if note:
            cleaned.append(note)
    return cleaned[:MAX_ERRORS]
