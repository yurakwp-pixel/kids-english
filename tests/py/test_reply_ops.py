"""validate_reply 의 ops 검사(Task 12): LLM 이 낸 장면 조작은 카탈로그에 있고, 실제 물건을 가리키고, 아이 안전 규칙을 지킬 때만 화면에 나간다.

fastpath(Task 11)가 내는 op 모양을 그대로 받는다:
  add    {"op","kind","id"|"ids","count","color"?,"size"?,"place"?}
  act    {"op","actor","action","target"?}
  remove {"op","target"}
  set    {"op","target","color"?,"size"?} 또는 {"op","target": 캐릭터,"face"}
  move   {"op","target","place"}
"""
import json
import random
import re
from pathlib import Path

import jsonschema
import pytest

from server import catalog as catalog_module
from server.canned import fallback_reply
from server import reply as reply_module
from server.catalog import load_catalog
from server.reply import parse_reply_text, validate_reply

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "shared" / "schemas" / "turn_reply.schema.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
FIXTURE = ROOT / "tests" / "fixtures" / "llm_outputs.jsonl"

BALL1 = {"id": "ball1", "kind": "ball", "color": "red", "size": "big"}
APPLE1 = {"id": "apple1", "kind": "apple"}
HAT1 = {"id": "hat1", "kind": "hat", "color": "blue"}
SOCKS1 = {"id": "socks1", "kind": "socks"}
EGG1 = {"id": "egg1", "kind": "egg"}
NEST1 = {"id": "nest1", "kind": "nest"}
TOYS1 = {"id": "toys1", "kind": "toys"}
DINO = {"id": "dino", "kind": "dino", "state": "idle"}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(ROOT / "shared" / "catalog.json")


def run(catalog, ops, *, say="Ok!", scene=(), applied=(), hangul=False, **extra):
    """ops 만 검사해 (살아남은 ops, issues) 를 돌려준다. 답 전체는 언제나 스키마를 만족해야 한다."""
    reply, issues = validate_reply(
        {"say": say, "ops": ops, **extra}, turn_id=1, catalog=catalog, scene=list(scene),
        applied_ops=list(applied), input_has_hangul=hangul,
    )
    VALIDATOR.validate(reply)
    return reply["ops"], issues


# ---------------------------------------------------------------- 브리프의 기본 사례


def test_unknown_kind_and_action_dropped(catalog):
    ops, issues = run(catalog, [
        {"op": "add", "kind": "dragon"},
        {"op": "act", "actor": "dino", "action": "fly"},
        {"op": "add", "kind": "egg", "color": "red"},
    ])
    # 서버가 id 를 정하고(규칙 4) 개수를 채운다: fastpath 의 add 와 같은 모양
    assert ops == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1, "color": "red"}]
    assert "ops_item_dropped" in issues


def test_chomp_only_eats_food(catalog):
    ops, _ = run(catalog, [{"op": "act", "actor": "chomp", "action": "chomp", "target": "dino"}], scene=[DINO])
    assert ops == []


def test_chomp_eats_food_in_the_scene(catalog):
    op = {"op": "act", "actor": "chomp", "action": "chomp", "target": "apple1"}
    assert run(catalog, [op], scene=[APPLE1])[0] == [op]


@pytest.mark.parametrize("target", ["ball1", "chomp", "dino", "egg9", None, 7, ["apple1"]])
def test_chomp_never_bites_non_food_or_nothing(catalog, target):
    op = {"op": "act", "actor": "chomp", "action": "chomp"}
    if target is not None:
        op["target"] = target
    assert run(catalog, [op], scene=[BALL1, APPLE1, DINO])[0] == []


def test_chomp_can_eat_food_the_same_reply_just_added(catalog):
    ops, _ = run(catalog, [
        {"op": "add", "kind": "cookie", "id": "cookie1"},
        {"op": "act", "actor": "chomp", "action": "chomp", "target": "cookie1"},
    ])
    assert [o["op"] for o in ops] == ["add", "act"] and ops[1]["target"] == "cookie1"


def test_chomp_cannot_eat_a_non_food_the_same_reply_just_added(catalog):
    ops, _ = run(catalog, [
        {"op": "add", "kind": "ball", "id": "ball1"},
        {"op": "act", "actor": "chomp", "action": "chomp", "target": "ball1"},
    ])
    assert [o["op"] for o in ops] == ["add"]


def test_put_on_needs_something_wearable(catalog):
    wear = {"op": "act", "actor": "dino", "action": "put_on", "target": "hat1"}
    assert run(catalog, [wear], scene=[HAT1])[0] == [wear]
    assert run(catalog, [{**wear, "target": "ball1"}], scene=[BALL1])[0] == []
    assert run(catalog, [{"op": "act", "actor": "dino", "action": "put_on"}])[0] == []


def test_duplicate_of_applied_ops_dropped(catalog):
    op = {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}
    ops, issues = run(catalog, [op], say="Kick!", scene=[BALL1], applied=[op])
    assert ops == [] and "ops_already_applied" in issues


def test_a_different_op_than_the_applied_one_survives(catalog):
    applied = {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}
    other = {"op": "act", "actor": "dino", "action": "clap"}
    assert run(catalog, [applied, other], scene=[BALL1], applied=[applied])[0] == [other]


def test_applied_add_is_a_duplicate_even_though_the_llm_cannot_know_its_id(catalog):
    applied = {"op": "add", "kind": "egg", "id": "egg2", "count": 1, "color": "red"}
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "color": "red"}], applied=[applied])
    assert ops == []
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "color": "blue"}], applied=[applied])
    assert len(ops) == 1


def test_ids_made_by_applied_ops_are_valid_targets_and_never_reused(catalog):
    applied = {"op": "add", "kind": "ball", "id": "ball3", "count": 1}
    kick = {"op": "act", "actor": "dino", "action": "kick", "target": "ball3"}
    ops, _ = run(catalog, [kick, {"op": "add", "kind": "ball", "color": "blue"}], applied=[applied])
    assert ops[0] == kick and ops[1]["id"] == "ball4"


def test_target_must_exist(catalog):
    ops, issues = run(catalog, [{"op": "move", "target": "egg9", "place": {"zone": "left"}}], say="Go!")
    assert ops == [] and "ops_item_dropped" in issues


def test_hangul_input_drops_ops(catalog):
    ops, issues = run(catalog, [{"op": "add", "kind": "egg"}], say="Say: red egg!", hangul=True)
    assert ops == [] and "ops_dropped_hangul" in issues


# ---------------------------------------------------------------- op 종류와 모양


@pytest.mark.parametrize("junk", [
    {"op": "explode"}, {"op": "ADD2"}, {"op": 5}, {"op": None}, {"kind": "egg"}, {}, "add an egg", 7, None, [], [{"op": "add"}],
])
def test_unknown_op_kinds_and_non_dict_items_are_dropped(catalog, junk):
    ops, issues = run(catalog, [junk])
    assert ops == [] and "ops_item_dropped" in issues


def test_op_kind_is_case_insensitive_and_output_uses_the_clean_name(catalog):
    ops, _ = run(catalog, [{"op": "Remove", "target": "egg1"}], scene=[EGG1])
    assert ops == [{"op": "remove", "target": "egg1"}]


def test_extra_keys_are_not_passed_through(catalog):
    ops, _ = run(catalog, [{"op": "act", "actor": "dino", "action": "clap", "html": "<svg/>", "x": 1}])
    assert ops == [{"op": "act", "actor": "dino", "action": "clap"}]


def test_names_are_matched_case_insensitively(catalog):
    ops, _ = run(catalog, [{"op": "add", "kind": "Egg", "color": "RED", "size": " Big "}])
    assert ops == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1, "color": "red", "size": "big"}]


def test_null_optional_keys_mean_absent(catalog):
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "id": None, "count": None, "color": None, "size": None, "place": None}])
    assert ops == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1}]


def test_every_dino_and_chomp_action_in_the_catalog_is_accepted(catalog):
    # 장면에 음식과 입을 것을 두고, 카탈로그의 모든 동작이 (필요한 대상과 함께) 통과해야 한다
    scene = [APPLE1, HAT1]
    for actor in ("chomp", "dino"):
        for action in sorted(catalog.actor_actions(actor)):
            op = {"op": "act", "actor": actor, "action": action}
            if action == "chomp":
                op["target"] = "apple1"
            if action == "put_on":
                op["target"] = "hat1"
            assert run(catalog, [op], scene=scene)[0] == [op], (actor, action)


def test_actor_cannot_do_the_other_actors_action(catalog):
    assert run(catalog, [{"op": "act", "actor": "chomp", "action": "kick"}])[0] == []
    assert run(catalog, [{"op": "act", "actor": "dino", "action": "bark"}])[0] == []
    assert run(catalog, [{"op": "act", "actor": "robot", "action": "jump"}])[0] == []


@pytest.mark.parametrize("action", ["kick", "throw_out"])
@pytest.mark.parametrize("target", ["chomp", "dino"])
def test_hitting_or_throwing_out_never_targets_a_character(catalog, action, target):
    # CLAUDE.md: 때리기·괴롭히기 없음. 공은 차도 멍멍이나 아기 공룡을 차거나 내던지지는 않는다.
    assert run(catalog, [{"op": "act", "actor": "dino", "action": action, "target": target}], scene=[DINO])[0] == []
    ball = {"op": "act", "actor": "dino", "action": action, "target": "ball1"}
    assert run(catalog, [ball], scene=[BALL1])[0] == [ball]


def test_act_target_may_be_an_actor_even_if_not_listed_in_the_scene(catalog):
    op = {"op": "act", "actor": "dino", "action": "high_five", "target": "chomp"}
    assert run(catalog, [op])[0] == [op]


# ---------------------------------------------------------------- add: 개수, 속성, id


@pytest.mark.parametrize("count, expected", [(1, 1), (3, 3), (5, 5), (6, 5), (99, 5), (0, 1), (-4, 1), (2.0, 2), (10**30, 5)])
def test_count_is_clamped_to_one_through_max_count(catalog, count, expected):
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "count": count}])
    assert len(ops) == 1 and ops[0]["count"] == expected
    if expected > 1:
        assert ops[0]["ids"] == [f"egg{i}" for i in range(1, expected + 1)] and "id" not in ops[0]
    else:
        assert ops[0]["id"] == "egg1" and "ids" not in ops[0]


@pytest.mark.parametrize("count", ["3", "three", True, 2.5, float("nan"), float("inf"), [], {}])
def test_count_that_is_not_a_whole_number_drops_the_op(catalog, count):
    assert run(catalog, [{"op": "add", "kind": "egg", "count": count}])[0] == []


def test_clamped_count_is_reported(catalog):
    _, issues = run(catalog, [{"op": "add", "kind": "egg", "count": 10}])
    assert "ops_fixed" in issues


@pytest.mark.parametrize("attrs", [{"color": "rainbow"}, {"size": "huge"}, {"color": 5}, {"size": ["big"]}, {"color": "red", "size": "tiny"}])
def test_color_or_size_not_in_the_catalog_drops_the_op(catalog, attrs):
    assert run(catalog, [{"op": "add", "kind": "egg", **attrs}])[0] == []


def test_attribute_a_kind_cannot_take_is_removed_like_the_fastpath_does(catalog):
    ops, issues = run(catalog, [{"op": "add", "kind": "apple", "color": "red", "size": "big"}])
    assert ops == [{"op": "add", "kind": "apple", "id": "apple1", "count": 1}] and "ops_fixed" in issues
    ops, _ = run(catalog, [{"op": "add", "kind": "socks", "color": "pink", "size": "big"}])
    assert ops == [{"op": "add", "kind": "socks", "id": "socks1", "count": 1, "color": "pink"}]


@pytest.mark.parametrize("kind", ["dino", "chomp", "Chomp", "", None, 5, ["egg"], "egg1"])
def test_actors_and_junk_cannot_be_added_as_kinds(catalog, kind):
    assert run(catalog, [{"op": "add", "kind": kind}])[0] == []


def test_llm_ids_that_are_missing_or_used_are_reassigned_by_the_server(catalog):
    scene = [{"id": "egg1", "kind": "egg"}, {"id": "egg3", "kind": "egg"}]
    ops, _ = run(catalog, [
        {"op": "add", "kind": "egg"},                       # id 없음 -> egg4
        {"op": "add", "kind": "egg", "id": "egg1"},         # 장면에서 이미 씀 -> egg5
        {"op": "add", "kind": "egg", "id": "egg4"},         # 같은 답의 앞선 add 가 이미 씀 -> egg6
    ], scene=scene)
    assert [o["id"] for o in ops] == ["egg4", "egg5", "egg6"]


def test_a_free_valid_llm_id_is_kept_so_later_ops_can_point_at_it(catalog):
    ops, _ = run(catalog, [
        {"op": "add", "kind": "ball", "id": "ball7"},
        {"op": "act", "actor": "dino", "action": "kick", "target": "ball7"},
    ])
    assert ops[0]["id"] == "ball7" and ops[1]["target"] == "ball7"


@pytest.mark.parametrize("bad_id", ["dino", "chomp", "egg", "ball1", "egg0", "egg01", "eggs1", "<b>", "egg1 ", "", 5, ["egg1"], {"a": 1}, "EGG1"])
def test_ids_that_do_not_look_like_a_server_id_are_replaced(catalog, bad_id):
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "id": bad_id}])
    assert ops == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1}]


def test_ids_list_is_kept_when_valid_and_replaced_when_not(catalog):
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "ids": ["egg2", "egg3"]}])
    assert ops == [{"op": "add", "kind": "egg", "ids": ["egg2", "egg3"], "count": 2}]
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "ids": ["egg2", "egg2"]}])
    assert ops[0]["ids"] == ["egg1", "egg2"]
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "ids": ["egg2"], "count": 3}])
    assert ops[0]["ids"] == ["egg1", "egg2", "egg3"] and ops[0]["count"] == 3
    ops, _ = run(catalog, [{"op": "add", "kind": "egg", "ids": "egg2"}])
    assert ops == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1}]


def test_ids_stay_unique_across_a_whole_reply_even_across_kinds(catalog):
    ops, _ = run(catalog, [
        {"op": "add", "kind": "egg", "count": 2},
        {"op": "add", "kind": "ball"},
        {"op": "add", "kind": "egg"},
    ])
    issued = [i for o in ops for i in (o.get("ids") or [o["id"]])]
    assert issued == ["egg1", "egg2", "ball1", "egg3"]


def test_scene_full_is_reported_but_the_add_is_kept(catalog):
    scene = [{"id": f"egg{i}", "kind": "egg"} for i in range(1, 20)]  # 19개
    ops, issues = run(catalog, [{"op": "add", "kind": "ball", "count": 3}], scene=scene)
    assert len(ops) == 1 and ops[0]["count"] == 3 and "scene_full" in issues


def test_scene_not_full_is_silent_and_actors_do_not_count(catalog):
    scene = [DINO, {"id": "chomp", "kind": "chomp"}] + [{"id": f"egg{i}", "kind": "egg"} for i in range(1, 19)]  # 물건 18개
    _, issues = run(catalog, [{"op": "add", "kind": "ball", "count": 2}], scene=scene)
    assert "scene_full" not in issues  # 18 + 2 = 20 은 넘지 않는다
    _, issues = run(catalog, [{"op": "add", "kind": "ball", "count": 3}], scene=scene)
    assert "scene_full" in issues


def test_removing_in_the_same_reply_makes_room_for_scene_full(catalog):
    scene = [{"id": f"egg{i}", "kind": "egg"} for i in range(1, 21)]  # 꽉 참
    _, issues = run(catalog, [{"op": "remove", "target": "egg1"}, {"op": "add", "kind": "ball"}], scene=scene)
    assert "scene_full" not in issues
    _, issues = run(catalog, [{"op": "add", "kind": "ball"}], scene=scene)
    assert "scene_full" in issues


# ---------------------------------------------------------------- 4개 제한


def test_at_most_four_ops_survive_counted_after_dropping_the_bad_ones(catalog):
    junk = [{"op": "explode"}, {"op": "add", "kind": "dragon"}]
    good = [{"op": "act", "actor": "dino", "action": a} for a in ("clap", "dance", "jump", "yawn", "walk", "sneeze")]
    ops, issues = run(catalog, junk + good)
    assert [o.get("action") for o in ops] == ["clap", "dance", "yawn", "walk"]  # jump 은 dino 동작이 아니라 버려졌다
    assert "ops_trimmed" in issues


def test_exactly_four_valid_ops_are_not_reported_as_trimmed(catalog):
    ops, issues = run(catalog, [{"op": "act", "actor": "dino", "action": "clap"}] * 4)
    assert len(ops) == 4 and "ops_trimmed" not in issues


def test_a_huge_ops_list_is_cut_quickly(catalog):
    ops, issues = run(catalog, [{"op": "act", "actor": "dino", "action": "clap"}] * 100_000)
    assert len(ops) == 4 and "ops_trimmed" in issues


def test_catalog_max_ops_above_the_schema_limit_cannot_overflow_the_schema(catalog):
    class Wide:
        """max_ops 만 큰 카탈로그. 스키마 한도(4)가 이긴다."""
        def __getattr__(self, name):
            return 99 if name == "max_ops" else getattr(catalog, name)
    ops, _ = run(Wide(), [{"op": "act", "actor": "dino", "action": "clap"}] * 9)
    assert len(ops) == 4


# ---------------------------------------------------------------- set / remove / move


def test_set_color_and_size_on_objects_that_allow_them(catalog):
    ops, _ = run(catalog, [{"op": "set", "target": "ball1", "color": "blue", "size": "small"}], scene=[BALL1])
    assert ops == [{"op": "set", "target": "ball1", "color": "blue", "size": "small"}]


def test_set_color_on_something_that_cannot_be_colored_is_dropped(catalog):
    assert run(catalog, [{"op": "set", "target": "apple1", "color": "red"}], scene=[APPLE1])[0] == []
    assert run(catalog, [{"op": "set", "target": "socks1", "size": "big"}], scene=[SOCKS1])[0] == []
    # 되는 속성만 남는다
    ops, _ = run(catalog, [{"op": "set", "target": "socks1", "size": "big", "color": "green"}], scene=[SOCKS1])
    assert ops == [{"op": "set", "target": "socks1", "color": "green"}]


@pytest.mark.parametrize("attrs", [{"color": "rainbow"}, {"size": "huge"}, {}, {"color": None}])
def test_set_with_a_bad_or_missing_attribute_is_dropped(catalog, attrs):
    assert run(catalog, [{"op": "set", "target": "ball1", **attrs}], scene=[BALL1])[0] == []


def test_set_color_never_applies_to_an_actor(catalog):
    assert run(catalog, [{"op": "set", "target": "dino", "color": "red"}])[0] == []
    assert run(catalog, [{"op": "set", "target": "chomp", "size": "big"}])[0] == []


def test_set_face_on_actors_with_faces_from_the_catalog(catalog):
    for actor in ("chomp", "dino"):
        op = {"op": "set", "target": actor, "face": "happy"}
        assert run(catalog, [op])[0] == [op]  # 장면에 안 적혀 있어도 캐릭터는 대상이다


@pytest.mark.parametrize("face", ["angry", "", None, 7, ["happy"], "happy!"])
def test_set_face_outside_the_actors_faces_is_dropped(catalog, face):
    assert run(catalog, [{"op": "set", "target": "dino", "face": face}])[0] == []


def test_set_face_on_an_object_is_dropped(catalog):
    assert run(catalog, [{"op": "set", "target": "ball1", "face": "happy"}], scene=[BALL1])[0] == []
    # 색은 되고 face 는 없는 것으로: 되는 속성만 남는다
    ops, _ = run(catalog, [{"op": "set", "target": "ball1", "face": "happy", "color": "pink"}], scene=[BALL1])
    assert ops == [{"op": "set", "target": "ball1", "color": "pink"}]


def test_set_face_on_an_actor_ignores_color_and_size(catalog):
    ops, _ = run(catalog, [{"op": "set", "target": "chomp", "face": "sleepy", "color": "red"}])
    assert ops == [{"op": "set", "target": "chomp", "face": "sleepy"}]


def test_set_target_must_exist(catalog):
    assert run(catalog, [{"op": "set", "target": "ball9", "color": "red"}], scene=[BALL1])[0] == []
    assert run(catalog, [{"op": "set", "color": "red"}], scene=[BALL1])[0] == []


def test_remove_objects_but_never_a_character(catalog):
    assert run(catalog, [{"op": "remove", "target": "egg1"}], scene=[EGG1])[0] == [{"op": "remove", "target": "egg1"}]
    assert run(catalog, [{"op": "remove", "target": "egg2"}], scene=[EGG1])[0] == []
    assert run(catalog, [{"op": "remove", "target": "chomp"}], scene=[DINO])[0] == []
    assert run(catalog, [{"op": "remove", "target": "dino"}], scene=[DINO])[0] == []


def test_a_removed_object_is_no_longer_a_target(catalog):
    ops, _ = run(catalog, [
        {"op": "remove", "target": "ball1"},
        {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"},
        {"op": "set", "target": "ball1", "color": "red"},
    ], scene=[BALL1])
    assert ops == [{"op": "remove", "target": "ball1"}]


def test_act_then_remove_of_the_same_object_is_fine(catalog):
    kick = {"op": "act", "actor": "dino", "action": "throw_out", "target": "ball1"}
    ops, _ = run(catalog, [kick, {"op": "remove", "target": "ball1"}], scene=[BALL1])
    assert ops == [kick, {"op": "remove", "target": "ball1"}]


def test_move_to_a_zone_or_next_to_an_object(catalog):
    zone = {"op": "move", "target": "ball1", "place": {"zone": "sky"}}
    rel = {"op": "move", "target": "ball1", "place": {"rel": "next_to", "ref": "nest1"}}
    assert run(catalog, [zone, rel], scene=[BALL1, NEST1])[0] == [zone, rel]


@pytest.mark.parametrize("place", [
    None, "left", 5, [], {}, {"zone": "moon"}, {"zone": None}, {"zone": 3},
    {"rel": "on"}, {"ref": "nest1"}, {"rel": "above", "ref": "nest1"}, {"rel": "on", "ref": "nest9"},
    {"rel": "on", "ref": "dino"}, {"rel": "on", "ref": 4}, {"rel": "on", "ref": "ball1"},
    {"zone": "left", "rel": "on", "ref": "nest1"}, {"zone": "left", "ref": "nest1"},
])
def test_move_with_an_invalid_place_is_dropped(catalog, place):
    op = {"op": "move", "target": "ball1"}
    if place is not None:
        op["place"] = place
    assert run(catalog, [op], scene=[BALL1, NEST1, DINO])[0] == []


def test_move_cannot_make_a_loop_of_objects(catalog):
    a_on_b = {"op": "move", "target": "ball1", "place": {"rel": "on", "ref": "nest1"}}
    b_on_a = {"op": "move", "target": "nest1", "place": {"rel": "on", "ref": "ball1"}}
    ops, _ = run(catalog, [a_on_b, b_on_a], scene=[BALL1, NEST1])
    assert ops == [a_on_b]
    # 장면에 이미 적힌 place 도 본다
    on_ball = {"id": "nest1", "kind": "nest", "place": {"rel": "on", "ref": "ball1"}}
    assert run(catalog, [a_on_b], scene=[BALL1, on_ball])[0] == []


def test_a_scene_that_already_contains_a_loop_does_not_hang_the_validator(catalog):
    x = {"id": "ball1", "kind": "ball", "place": {"rel": "on", "ref": "ball2"}}
    y = {"id": "ball2", "kind": "ball", "place": {"rel": "on", "ref": "ball1"}}
    z = {"op": "move", "target": "egg1", "place": {"rel": "on", "ref": "ball1"}}
    assert run(catalog, [z], scene=[x, y, EGG1])[0] == [z]


def test_characters_do_not_move(catalog):
    assert run(catalog, [{"op": "move", "target": "dino", "place": {"zone": "left"}}], scene=[DINO])[0] == []


def test_add_with_a_place_is_checked_the_same_way(catalog):
    ops, _ = run(catalog, [{"op": "add", "kind": "ball", "place": {"rel": "on", "ref": "tree1"}}],
                 scene=[{"id": "tree1", "kind": "tree"}])
    assert ops[0]["place"] == {"rel": "on", "ref": "tree1"}
    assert run(catalog, [{"op": "add", "kind": "ball", "place": {"rel": "on", "ref": "tree9"}}])[0] == []
    assert run(catalog, [{"op": "add", "kind": "ball", "place": {"zone": "moon"}}])[0] == []
    ops, _ = run(catalog, [{"op": "add", "kind": "tree", "id": "tree1"},
                           {"op": "add", "kind": "ball", "place": {"rel": "on", "ref": "tree1"}}])
    assert ops[1]["place"] == {"rel": "on", "ref": "tree1"}


def test_extra_keys_inside_place_are_not_passed_through(catalog):
    ops, _ = run(catalog, [{"op": "move", "target": "ball1", "place": {"zone": "left", "z": 9, "css": "x"}}], scene=[BALL1])
    assert ops == [{"op": "move", "target": "ball1", "place": {"zone": "left"}}]


# ---------------------------------------------------------------- 답 전체와의 관계 (규칙 3, 5, 6, 7)


def test_mood_must_match_the_catalog_when_a_catalog_is_given(catalog):
    reply, issues = validate_reply({"say": "Hi!", "mood": {"actor": "chomp", "face": "sleepy"}}, turn_id=1, catalog=catalog)
    assert reply["mood"] == {"actor": "chomp", "face": "sleepy"} and "mood_invalid" not in issues
    for bad in ({"actor": "chomp", "face": "furious"}, {"actor": "robot", "face": "happy"}, {"actor": "dino", "face": "Happy2"}):
        reply, issues = validate_reply({"say": "Hi!", "mood": bad}, turn_id=1, catalog=catalog)
        assert reply["mood"] is None and "mood_invalid" in issues


def test_mood_without_a_catalog_still_uses_the_loose_face_pattern(catalog):
    reply, issues = validate_reply({"say": "Hi!", "mood": {"actor": "chomp", "face": "furious"}}, turn_id=1)
    assert reply["mood"] == {"actor": "chomp", "face": "furious"} and "mood_invalid" not in issues


def test_mood_face_is_normalised_before_the_catalog_check(catalog):
    reply, _ = validate_reply({"say": "Hi!", "mood": {"actor": " Dino ", "face": "HAPPY"}}, turn_id=1, catalog=catalog)
    assert reply["mood"] == {"actor": "dino", "face": "happy"}


def test_a_replaced_say_takes_the_llm_ops_with_it(catalog):
    reply, issues = validate_reply(
        {"say": "안녕!", "ops": [{"op": "add", "kind": "egg"}, {"op": "act", "actor": "dino", "action": "clap"}]},
        turn_id=1, catalog=catalog)
    assert reply["ops"] == [] and "say_replaced" in issues and "ops_dropped_say_replaced" in issues
    VALIDATOR.validate(reply)


def test_a_replaced_say_with_no_ops_has_no_ops_issue(catalog):
    for body in ({"say": 5}, {"say": 5, "ops": []}, {"say": 5, "ops": None}):
        reply, issues = validate_reply(body, turn_id=1, catalog=catalog)
        assert not [i for i in issues if i.startswith("ops")], body


def test_a_partly_salvaged_say_keeps_its_ops(catalog):
    reply, issues = validate_reply(
        {"say": "안녕! Clap your hands!", "ops": [{"op": "act", "actor": "dino", "action": "clap"}]},
        turn_id=1, catalog=catalog)
    assert reply["say"] == "Clap your hands!" and "say_hangul" in issues and "say_replaced" not in issues
    assert reply["ops"] == [{"op": "act", "actor": "dino", "action": "clap"}]


def test_hangul_input_check_comes_before_the_replaced_say_check(catalog):
    _, issues = validate_reply({"say": 5, "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1, catalog=catalog, input_has_hangul=True)
    assert "ops_dropped_hangul" in issues and "ops_dropped_say_replaced" not in issues


def _with_lang(lang, ops=None):
    return {"say": "Red egg? Say: red egg!", "ops": ops if ops is not None else [{"op": "add", "kind": "egg"}],
            "utterance": {"lang": lang, "words_used": [], "errors": []}}


def test_a_reply_that_says_the_child_spoke_korean_has_no_ops(catalog):
    # 아이의 한국어는 en-US 인식기를 거치면 영어처럼 보이는 글자로 온다. 한글 검사로는 못 잡고, 답의 utterance.lang 이 유일한 신호다(스펙 §4).
    reply, issues = validate_reply(_with_lang("ko"), turn_id=1, catalog=catalog)
    VALIDATOR.validate(reply)
    assert reply["ops"] == [] and "ops_dropped_lang_ko" in issues
    assert reply["say"] == "Red egg? Say: red egg!" and reply["utterance"]["lang"] == "ko"  # 말은 그대로 나간다


def test_lang_ko_drops_ops_even_without_a_catalog_and_is_case_insensitive():
    reply, issues = validate_reply(_with_lang(" KO "), turn_id=1)
    assert reply["ops"] == [] and "ops_dropped_lang_ko" in issues and "ops_dropped_no_catalog" not in issues


@pytest.mark.parametrize("lang", ["en", "mixed", "unknown"])
def test_other_languages_keep_their_ops(catalog, lang):
    reply, issues = validate_reply(_with_lang(lang), turn_id=1, catalog=catalog)
    assert reply["ops"] == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1}]
    assert "ops_dropped_lang_ko" not in issues


@pytest.mark.parametrize("utterance", [None, "ko", 5, {}, {"lang": "French"}, {"lang": None}, {"lang": ["ko"]}])
def test_a_missing_or_broken_utterance_is_not_treated_as_korean(catalog, utterance):
    body = _with_lang("en")
    body["utterance"] = utterance
    reply, issues = validate_reply(body, turn_id=1, catalog=catalog)
    assert len(reply["ops"]) == 1 and "ops_dropped_lang_ko" not in issues


def test_lang_ko_without_ops_has_no_ops_issue(catalog):
    for ops in (None, []):
        body = _with_lang("ko")
        body["ops"] = ops
        _, issues = validate_reply(body, turn_id=1, catalog=catalog)
        assert not [i for i in issues if i.startswith("ops")]


def test_hangul_input_is_reported_before_lang_ko_and_lang_ko_before_a_replaced_say(catalog):
    _, issues = validate_reply(_with_lang("ko"), turn_id=1, catalog=catalog, input_has_hangul=True)
    assert "ops_dropped_hangul" in issues and "ops_dropped_lang_ko" not in issues
    body = _with_lang("ko")
    body["say"] = 5
    _, issues = validate_reply(body, turn_id=1, catalog=catalog)
    assert "ops_dropped_lang_ko" in issues and "ops_dropped_say_replaced" not in issues


def test_ops_that_are_not_a_list_are_still_reported_with_a_catalog(catalog):
    for raw in ("add an egg", 3, {"op": "add"}):
        reply, issues = validate_reply({"say": "Ok!", "ops": raw}, turn_id=1, catalog=catalog)
        assert reply["ops"] == [] and "ops_invalid" in issues


def test_without_a_catalog_every_op_is_still_dropped():
    reply, issues = validate_reply({"say": "Ok!", "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1)
    assert reply["ops"] == [] and "ops_dropped_no_catalog" in issues


def test_actors_are_defined_once():
    assert reply_module.ACTORS is catalog_module.ACTORS


def test_the_output_does_not_alias_the_input_ops(catalog):
    place = {"zone": "left"}
    ids = ["egg4", "egg5"]
    body = {"say": "Ok!", "ops": [{"op": "move", "target": "ball1", "place": place},
                                   {"op": "add", "kind": "egg", "ids": ids}]}
    reply, _ = validate_reply(body, turn_id=1, catalog=catalog, scene=[BALL1])
    reply["ops"][0]["place"]["zone"] = "right"
    reply["ops"][1]["ids"].append("x")
    assert place == {"zone": "left"} and ids == ["egg4", "egg5"]


def test_scene_and_applied_ops_are_not_modified(catalog):
    scene = [dict(BALL1), dict(DINO)]
    applied = [{"op": "add", "kind": "egg", "ids": ["egg1", "egg2"], "count": 2}]
    before = json.dumps([scene, applied])
    run(catalog, [{"op": "add", "kind": "egg"}, {"op": "remove", "target": "ball1"}], scene=scene, applied=applied)
    assert json.dumps([scene, applied]) == before


@pytest.mark.parametrize("scene", [None, 5, "scene", {"id": "ball1"}, [None, 5, "x", [], {"id": 5}, {"kind": "ball"}, {"id": ""}]])
def test_a_broken_scene_never_raises(catalog, scene):
    reply, _ = validate_reply({"say": "Ok!", "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1, catalog=catalog, scene=scene)
    VALIDATOR.validate(reply)
    assert reply["ops"] == [{"op": "add", "kind": "egg", "id": "egg1", "count": 1}]


@pytest.mark.parametrize("applied", [None, 5, "x", [None, 5, "x", [], {"op": "add"}, {"op": "add", "kind": 5, "ids": 3}, {"op": "remove"}]])
def test_broken_applied_ops_never_raise(catalog, applied):
    reply, _ = validate_reply({"say": "Ok!", "ops": [{"op": "add", "kind": "egg"}]}, turn_id=1, catalog=catalog, applied_ops=applied)
    VALIDATOR.validate(reply)
    assert len(reply["ops"]) == 1


# ---------------------------------------------------------------- 안티골 A1: 부정 표현 걸러내기


def test_a_sentence_with_wrong_is_dropped_from_say(catalog):
    reply, issues = validate_reply({"say": "That is wrong! Kick the ball!"}, turn_id=1, catalog=catalog)
    assert reply["say"] == "Kick the ball!" and "say_negative" in issues and "say_replaced" not in issues


@pytest.mark.parametrize("sentence", ["Wrong!", "WRONG answer.", "That is incorrect.", "Not Incorrect!", "Oh, wrong-way!", "It's wrong, try again."])
def test_wrong_and_incorrect_as_whole_words_are_filtered_case_insensitively(sentence):
    reply, issues = validate_reply({"say": f"{sentence} Good job!"}, turn_id=1)
    assert reply["say"] == "Good job!" and "say_negative" in issues


@pytest.mark.parametrize("say", ["No problem!", "No, thank you!", "Oh no! Try again!", "Wrongly spelled?", "Incorrectly done!", "Strongest!", "Throng!"])
def test_no_and_lookalike_words_are_not_filtered(say):
    reply, issues = validate_reply({"say": say}, turn_id=1)
    assert reply["say"] == say and "say_negative" not in issues


def test_a_say_with_nothing_left_after_the_filter_becomes_the_canned_line_and_drops_ops(catalog):
    body = {"say": "Wrong! That is incorrect!", "choice": ["Kick", "Jump"], "korean_hint": "틀렸어 → Wrong",
            "ops": [{"op": "act", "actor": "dino", "action": "clap"}]}
    reply, issues = validate_reply(body, turn_id=1, catalog=catalog)
    assert reply["say"] == fallback_reply("error", 1)["say"]
    assert reply["choice"] is None and reply["ops"] == []
    assert "say_negative" in issues and "say_replaced" in issues and "ops_dropped_say_replaced" in issues


def test_wrong_in_recast_model_is_filtered_sentence_by_sentence():
    reply, issues = validate_reply({"say": "Hi!", "recast": {"model": "Wrong! Kick the ball!", "ask_repeat": True}}, turn_id=1)
    assert reply["recast"] == {"model": "Kick the ball!", "ask_repeat": True} and "recast_negative" in issues
    reply, issues = validate_reply({"say": "Hi!", "recast": {"model": "That is incorrect!", "ask_repeat": True}}, turn_id=1)
    assert reply["recast"] is None and "recast_invalid" in issues


def test_wrong_in_a_choice_drops_that_choice():
    reply, issues = validate_reply({"say": "Which one?", "choice": ["Kick", "Wrong way", "Jump"]}, turn_id=1)
    assert reply["choice"] == ["Kick", "Jump"] and "choice_negative" in issues
    reply, issues = validate_reply({"say": "Which one?", "choice": ["Kick", "Wrong way"]}, turn_id=1)
    assert reply["choice"] is None and "choice_too_few" in issues


def test_the_filter_keeps_the_reply_a_fixed_point():
    body = {"say": "Wrong! Nice try! That is incorrect.", "recast": {"model": "Wrong. Kick it!", "ask_repeat": True},
            "choice": ["Wrong", "Kick", "Jump"]}
    reply, _ = validate_reply(body, turn_id=3)
    again, issues = validate_reply(reply, turn_id=3)
    assert again == reply and issues == []


# ---------------------------------------------------------------- 퍼징 (카탈로그 있음)

_NAMES = ["egg", "ball", "block", "socks", "hat", "nest", "tree", "toys", "sun", "flower", "apple", "cookie", "banana",
          "dino", "chomp", "dragon", "Egg", "", None, 7, ["egg"], {"a": 1}]
_ACTIONS = ["jump", "chomp", "bark", "spin", "hide", "sleep", "kick", "put_on", "hatch", "clap", "fly", "", None, 3]
_COLORS = ["red", "blue", "pink", "rainbow", "RED", "", None, 5, ["red"]]
_SIZES = ["big", "small", "huge", None, 2, ["big"]]
_TARGETS = ["ball1", "egg1", "apple1", "hat1", "socks1", "nest1", "dino", "chomp", "egg9", "egg2", "ball2", "", None, 5, [], {}]
_PLACES = [None, {"zone": "left"}, {"zone": "moon"}, {"rel": "on", "ref": "nest1"}, {"rel": "under", "ref": "ball1"},
           {"rel": "in", "ref": "egg2"}, {"rel": "above", "ref": "nest1"}, {"rel": "on", "ref": "dino"}, {"rel": "on"}, "left", [], 5]
_COUNTS = [None, 1, 2, 5, 6, 99, 0, -1, 2.0, 2.5, "3", True, None]
_IDS = [None, "egg2", "egg1", "ball2", "dino", "x", 5, ["egg3", "egg4"], ["egg3"], ["egg3", "egg3"], [], "egg3"]
_SCENE = [DINO, {"id": "chomp", "kind": "chomp"}, BALL1, APPLE1, HAT1, SOCKS1, EGG1, NEST1]
_APPLIED = [{"op": "act", "actor": "dino", "action": "kick", "target": "ball1"}, {"op": "add", "kind": "egg", "id": "egg2", "count": 1}]


# 거의 맞는 op: 규칙을 통과하는 쪽(멍멍이가 사과 물기, 모자 쓰기 등)도 퍼징에서 자주 나오게 한다
_NEARLY_RIGHT = [
    {"op": "act", "actor": "chomp", "action": "chomp", "target": "apple1"},
    {"op": "act", "actor": "dino", "action": "put_on", "target": "hat1"},
    {"op": "act", "actor": "dino", "action": "kick", "target": "ball1"},
    {"op": "add", "kind": "cookie", "id": "cookie1"},
    {"op": "act", "actor": "chomp", "action": "chomp", "target": "cookie1"},
    {"op": "set", "target": "chomp", "face": "happy"},
    {"op": "set", "target": "ball1", "color": "blue", "size": "small"},
    {"op": "move", "target": "ball1", "place": {"rel": "on", "ref": "nest1"}},
    {"op": "move", "target": "egg1", "place": {"zone": "sky"}},
    {"op": "remove", "target": "egg1"},
]


def _random_op(rng):
    if rng.random() < 0.4:
        op = dict(rng.choice(_NEARLY_RIGHT))
        if rng.random() < 0.5:
            key = rng.choice(["op", "actor", "action", "target", "kind", "face", "color", "size", "place"])
            pools = {"op": ["add", "act", "set", "move", "remove"], "actor": ["chomp", "dino"], "action": _ACTIONS,
                     "target": _TARGETS, "kind": _NAMES, "face": ["happy", "angry", None], "color": _COLORS,
                     "size": _SIZES, "place": _PLACES}
            op[key] = rng.choice(pools[key])
        return op
    kind = rng.choice(["add", "act", "set", "move", "remove", "add", "act", "explode", None, "SET"])
    op = {"op": kind}
    keys = {
        "kind": _NAMES, "actor": ["chomp", "dino", "robot", None], "action": _ACTIONS, "target": _TARGETS, "color": _COLORS,
        "size": _SIZES, "count": _COUNTS, "id": _IDS, "ids": _IDS, "place": _PLACES, "face": ["happy", "sleepy", "angry", None, 4],
    }
    for key, pool in keys.items():
        if rng.random() < 0.5:
            op[key] = rng.choice(pool)
    if rng.random() < 0.05:
        op["junk"] = "<script>"
    return op


def _random_ops(rng):
    roll = rng.random()
    if roll < 0.04:
        return rng.choice([None, "ops", 5, {"op": "add"}, [None], [[]], [[], {}]])
    return [_random_op(rng) if rng.random() < 0.9 else rng.choice([None, 5, "x", [], {}]) for _ in range(rng.randint(0, 9))]


def _food_and_wearable(catalog, ops):
    """장면과 이 답의 add 로 target id -> kind 를 만든다."""
    kinds = {e["id"]: e["kind"] for e in _SCENE} | {"dino": "dino", "chomp": "chomp", "egg2": "egg"}  # egg2: _APPLIED 가 만듦
    for op in ops:
        if op["op"] == "add":
            for ident in op.get("ids") or [op["id"]]:
                kinds[ident] = op["kind"]
    return kinds


def _check_invariants(catalog, ops):
    assert len(ops) <= catalog.max_ops
    kinds = _food_and_wearable(catalog, ops)
    seen_ids = {e["id"] for e in _SCENE} | {"egg2"}
    for op in ops:
        assert op["op"] in ("add", "set", "move", "remove", "act")
        if op["op"] == "add":
            assert op["kind"] in catalog.objects and 1 <= op["count"] <= catalog.max_count
            ids = op.get("ids") or [op["id"]]
            assert len(ids) == op["count"] and not (set(ids) & seen_ids) and len(set(ids)) == len(ids)
            assert all(re.fullmatch(re.escape(op["kind"]) + r"[1-9][0-9]*", i) for i in ids)
            seen_ids.update(ids)
            if "color" in op:
                assert catalog.can_color(op["kind"]) and op["color"] in catalog.colors
            if "size" in op:
                assert catalog.can_size(op["kind"]) and op["size"] in catalog.sizes
        elif op["op"] == "act":
            assert op["actor"] in catalog.actors and op["action"] in catalog.actor_actions(op["actor"])
            if op["action"] == "chomp":
                assert catalog.is_food(kinds[op["target"]])  # 멍멍이는 음식만 문다
            if op["action"] == "put_on":
                assert catalog.is_wearable(kinds[op["target"]])
            if op["action"] in ("kick", "throw_out"):
                assert op.get("target") not in catalog.actors
            if "target" in op:
                assert op["target"] in kinds
        elif op["op"] == "set":
            if "face" in op:
                assert op["target"] in catalog.actors and op["face"] in catalog.actor_faces(op["target"])
            else:
                assert op["target"] not in catalog.actors
                assert ("color" in op and catalog.can_color(kinds[op["target"]])) or ("size" in op and catalog.can_size(kinds[op["target"]]))
        elif op["op"] == "move":
            assert op["target"] in kinds and op["target"] not in catalog.actors
            assert "zone" in op["place"] or (op["place"]["ref"] in kinds and op["place"]["ref"] not in catalog.actors)
        else:
            assert op["target"] in kinds and op["target"] not in catalog.actors


def test_random_ops_always_stay_inside_the_catalog_and_the_reply_stays_schema_valid(catalog):
    rng = random.Random(20261023)
    for i in range(4000):
        body = {"say": "Ok!", "ops": _random_ops(rng), "mood": rng.choice([None, {"actor": "dino", "face": "angry"}, {"actor": "dino", "face": "happy"}])}
        reply, issues = validate_reply(body, turn_id=i, catalog=catalog, scene=_SCENE, applied_ops=_APPLIED)
        VALIDATOR.validate(reply)
        _check_invariants(catalog, reply["ops"])
        assert reply["mood"] in (None, {"actor": "dino", "face": "happy"})
        # 한 번 고친 답은 같은 입력에서 다시 고칠 것이 없다
        again, again_issues = validate_reply(reply, turn_id=i, catalog=catalog, scene=_SCENE, applied_ops=_APPLIED)
        assert again == reply and [x for x in again_issues if x != "scene_full"] == [], (body, reply, again_issues)


def test_random_ops_with_hangul_input_or_without_catalog_are_always_empty(catalog):
    rng = random.Random(5)
    for i in range(500):
        body = {"say": "Ok!", "ops": _random_ops(rng)}
        assert validate_reply(body, turn_id=i, catalog=catalog, scene=_SCENE, input_has_hangul=True)[0]["ops"] == []
        assert validate_reply(body, turn_id=i, scene=_SCENE)[0]["ops"] == []


# ---------------------------------------------------------------- 픽스처 파일 (ops 사례)


def _ops_fixture_cases():
    cases = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if "expect_ops" in c]


def test_fixture_file_has_six_ops_cases():
    assert len(_ops_fixture_cases()) == 6


@pytest.mark.parametrize("case", _ops_fixture_cases(), ids=lambda c: c["name"])
def test_fixture_ops_cases_with_the_catalog(catalog, case):
    obj = parse_reply_text(case["text"])
    reply, issues = validate_reply(obj, turn_id=7, catalog=catalog, scene=case.get("scene", []),
                                   applied_ops=case.get("applied_ops", []))
    VALIDATOR.validate(reply)
    assert reply["ops"] == case["expect_ops"], case["name"]
    for issue in case.get("ops_issues", []):
        assert issue in issues, f"{case['name']}: expected {issue!r}, got {issues}"
    for issue in case.get("ops_absent_issues", []):
        assert issue not in issues, f"{case['name']}: did not expect {issue!r}, got {issues}"


def test_every_fixture_line_is_schema_valid_with_the_catalog_too(catalog):
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            reply, _ = validate_reply(parse_reply_text(case["text"]), turn_id=7, catalog=catalog)
            VALIDATOR.validate(reply)
