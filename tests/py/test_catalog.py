"""shared/catalog.json 과 server/catalog.py(로더)의 테스트.

진짜 카탈로그의 값은 스펙 5.7 / Task 9 에 적힌 그대로인지 보고,
로더는 깨진 카탈로그를 "어느 키가 문제인지" 담은 ValueError 로 거부하는지 본다.
"""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from server.catalog import Catalog, load_catalog

CATALOG_PATH = Path(__file__).resolve().parents[2] / "shared" / "catalog.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(CATALOG_PATH)


def write_catalog(tmp_path: Path, data) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 진짜 카탈로그

def test_catalog_loads_with_spec_values():
    c = load_catalog(Path("shared/catalog.json"))
    assert c.max_ops == 4 and c.max_count == 5 and c.max_items == 20
    assert {"red", "blue", "yellow", "green"} <= set(c.colors) and len(c.colors) == 8
    assert "hatch" in c.actor_actions("dino") and "chomp" in c.actor_actions("chomp")
    assert c.is_food("apple") and not c.is_food("egg") and c.is_wearable("socks") and c.can_color("egg")


def test_every_emoji_object_has_emoji():
    c = load_catalog(Path("shared/catalog.json"))
    assert all(o.get("emoji") for o in c.objects.values() if o["render"] == "emoji")


def test_exact_values_from_the_task_brief(catalog):
    assert catalog.version == 1
    assert catalog.colors == ("red", "blue", "yellow", "green", "orange", "purple", "pink", "white")
    assert catalog.sizes == ("small", "big")
    assert catalog.zones == ("left", "center", "right", "sky", "ground")
    assert catalog.relations == ("on", "under", "next_to", "in")
    assert set(catalog.actors) == {"chomp", "dino"}
    assert catalog.actor_actions("chomp") == frozenset({"jump", "chomp", "bark", "spin", "hide", "sleep"})
    assert catalog.actor_actions("dino") == frozenset({
        "hatch", "turn_around", "walk", "hide", "kick", "stretch_arms", "fold_arms", "thumbs_up", "climb_up",
        "yawn", "sneeze", "deep_breath", "clean_up", "throw_out", "put_on", "wash_hands", "go_to_bed",
        "clap", "dance", "high_five",
    })
    faces = frozenset({"happy", "excited", "sleepy", "surprised", "sad", "shy"})
    assert catalog.actor_faces("chomp") == faces and catalog.actor_faces("dino") == faces


def test_object_table_matches_the_task_brief(catalog):
    svg_color_size = {"egg", "ball", "block", "nest", "tree"}  # 스펙 5.7: nest, tree 도 색과 크기를 바꿀 수 있다
    svg_color_only = {"socks", "hat"}
    emoji = {"toys": "🧸", "sun": "☀️", "flower": "🌼", "apple": "🍎", "cookie": "🍪", "banana": "🍌"}
    assert set(catalog.objects) == svg_color_size | svg_color_only | set(emoji)
    for kind in svg_color_size:
        assert catalog.objects[kind]["render"] == "svg"
        assert catalog.can_color(kind) and catalog.can_size(kind)
    for kind in svg_color_only:
        assert catalog.objects[kind]["render"] == "svg"
        assert catalog.can_color(kind) and not catalog.can_size(kind)
        assert catalog.is_wearable(kind)
    for kind, char in emoji.items():
        assert catalog.objects[kind]["render"] == "emoji" and catalog.objects[kind]["emoji"] == char
        assert not catalog.can_color(kind) and not catalog.can_size(kind)
    assert {k for k in catalog.objects if catalog.is_food(k)} == {"apple", "cookie", "banana"}
    assert {k for k in catalog.objects if catalog.is_wearable(k)} == {"socks", "hat"}


def test_predicates_and_lookups_never_raise_on_unknown_input(catalog):
    # 검증기(Task 12)는 LLM 이 낸 모르는 이름을 그대로 물어본다. 예외 대신 "아니오"/빈 집합이어야 한다.
    for name in ("dragon", "", "EGG", "egg1"):
        assert not catalog.is_food(name) and not catalog.is_wearable(name)
        assert not catalog.can_color(name) and not catalog.can_size(name)
    assert catalog.actor_actions("cat") == frozenset() and catalog.actor_faces("cat") == frozenset()
    assert catalog.actor_actions("") == frozenset()
    # JSON 에서 온 값은 문자열이 아닐 수도 있다(목록, 딕셔너리, 숫자, null): 해시 오류도 나면 안 된다.
    for odd in (["egg"], {"a": 1}, 7, None):
        assert not catalog.is_food(odd) and not catalog.can_color(odd)
        assert catalog.actor_actions(odd) == frozenset() and catalog.actor_faces(odd) == frozenset()


def test_lookups_return_frozensets(catalog):
    assert isinstance(catalog.actor_actions("dino"), frozenset)
    assert isinstance(catalog.actor_faces("chomp"), frozenset)


def test_catalog_is_read_only(catalog):
    with pytest.raises(dataclasses.FrozenInstanceError):
        catalog.max_ops = 99
    with pytest.raises(TypeError):
        catalog.objects["dragon"] = {"render": "svg"}
    with pytest.raises(TypeError):
        catalog.objects["egg"]["food"] = True
    with pytest.raises(TypeError):
        catalog.actors["dino"]["actions"] = ()
    assert isinstance(catalog.colors, tuple) and isinstance(catalog.actors["dino"]["actions"], tuple)


def test_catalog_file_is_plain_utf8_json(raw):
    # 브라우저가 /shared/catalog.json 을 fetch 해서 JSON.parse 한다: BOM 없는 UTF-8, 이모지는 글자 그대로.
    data = CATALOG_PATH.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "🍎" in data.decode("utf-8")
    assert set(raw) == {"version", "actors", "objects", "colors", "sizes", "max_count",
                        "zones", "relations", "max_ops", "max_items"}
    # JS(Task 14)는 catalog.actors.dino.actions 를 읽는다.
    assert raw["actors"]["dino"]["actions"] == list(load_catalog(CATALOG_PATH).actors["dino"]["actions"])


def test_json_shape_is_what_the_js_renderer_will_read(raw):
    assert isinstance(raw["objects"]["egg"], dict) and raw["objects"]["egg"]["render"] == "svg"
    assert set(raw["actors"]["chomp"]) == {"actions", "faces"}


# ---------------------------------------------------------------- 깨진 카탈로그

def broken(raw, mutate):
    data = copy.deepcopy(raw)
    mutate(data)
    return data


@pytest.mark.parametrize("key", ["version", "actors", "objects", "colors", "sizes", "max_count",
                                 "zones", "relations", "max_ops", "max_items"])
def test_missing_top_level_key_names_the_key(tmp_path, raw, key):
    data = broken(raw, lambda d: d.pop(key))
    with pytest.raises(ValueError, match=key):
        load_catalog(write_catalog(tmp_path, data))


def test_not_a_json_object(tmp_path):
    with pytest.raises(ValueError, match="object"):
        load_catalog(write_catalog(tmp_path, ["not", "an", "object"]))


def test_invalid_json_is_a_value_error(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(path)


def test_emoji_object_without_emoji(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"]["apple"].pop("emoji"))
    with pytest.raises(ValueError, match=r"apple.*emoji"):
        load_catalog(write_catalog(tmp_path, data))


def test_emoji_object_with_empty_emoji(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"]["apple"].update(emoji=""))
    with pytest.raises(ValueError, match=r"apple.*emoji"):
        load_catalog(write_catalog(tmp_path, data))


def test_svg_object_must_not_carry_emoji(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"]["egg"].update(emoji="🥚"))
    with pytest.raises(ValueError, match=r"egg.*emoji"):
        load_catalog(write_catalog(tmp_path, data))


def test_emoji_object_cannot_be_colored_or_sized(tmp_path, raw):
    for flag in ("color", "size"):
        data = broken(raw, lambda d: d["objects"]["sun"].update({flag: True}))
        with pytest.raises(ValueError, match=rf"sun.*{flag}"):
            load_catalog(write_catalog(tmp_path, data))


@pytest.mark.parametrize("render", ["canvas", "SVG", "", None, 3])
def test_unknown_render_value(tmp_path, raw, render):
    data = broken(raw, lambda d: d["objects"]["ball"].update(render=render))
    with pytest.raises(ValueError, match=r"ball.*render"):
        load_catalog(write_catalog(tmp_path, data))


def test_object_without_render(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"]["ball"].pop("render"))
    with pytest.raises(ValueError, match=r"ball.*render"):
        load_catalog(write_catalog(tmp_path, data))


def test_unknown_object_key_is_rejected(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"]["socks"].update(wearble=True))
    with pytest.raises(ValueError, match=r"socks.*wearble"):
        load_catalog(write_catalog(tmp_path, data))


def test_object_flag_must_be_a_boolean(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"]["apple"].update(food="yes"))
    with pytest.raises(ValueError, match=r"apple.*food"):
        load_catalog(write_catalog(tmp_path, data))


def test_object_entry_must_be_an_object(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"].update(egg="svg"))
    with pytest.raises(ValueError, match="egg"):
        load_catalog(write_catalog(tmp_path, data))


def test_object_kind_cannot_shadow_an_actor(tmp_path, raw):
    data = broken(raw, lambda d: d["objects"].update(dino={"render": "svg"}))
    with pytest.raises(ValueError, match="dino"):
        load_catalog(write_catalog(tmp_path, data))


def test_object_kind_must_be_a_plain_lowercase_name(tmp_path, raw):
    # 장면의 id 는 "egg1" 처럼 kind + 번호라서, kind 에 숫자나 대문자가 있으면 id 를 되짚을 수 없다.
    for bad in ("Egg", "egg1", "big egg", "egg\n", ""):
        data = broken(raw, lambda d: d["objects"].update({bad: {"render": "svg"}}))
        with pytest.raises(ValueError, match="objects"):
            load_catalog(write_catalog(tmp_path, data))


@pytest.mark.parametrize("key", ["colors", "sizes", "zones", "relations"])
def test_duplicate_values_name_the_key_and_value(tmp_path, raw, key):
    data = broken(raw, lambda d: d[key].append(d[key][0]))
    with pytest.raises(ValueError, match=rf"{key}.*{raw[key][0]}"):
        load_catalog(write_catalog(tmp_path, data))


@pytest.mark.parametrize("key", ["colors", "sizes", "zones", "relations"])
def test_empty_or_non_string_lists_are_rejected(tmp_path, raw, key):
    for bad in ([], [1, 2], "red", ["red", ""]):
        data = broken(raw, lambda d: d.update({key: bad}))
        with pytest.raises(ValueError, match=key):
            load_catalog(write_catalog(tmp_path, data))


@pytest.mark.parametrize("field", ["actions", "faces"])
def test_actor_lists_are_validated(tmp_path, raw, field):
    dup = broken(raw, lambda d: d["actors"]["dino"][field].append(d["actors"]["dino"][field][0]))
    with pytest.raises(ValueError, match=rf"dino.*{field}"):
        load_catalog(write_catalog(tmp_path, dup))
    empty = broken(raw, lambda d: d["actors"]["chomp"].update({field: []}))
    with pytest.raises(ValueError, match=rf"chomp.*{field}"):
        load_catalog(write_catalog(tmp_path, empty))
    missing = broken(raw, lambda d: d["actors"]["chomp"].pop(field))
    with pytest.raises(ValueError, match=rf"chomp.*{field}"):
        load_catalog(write_catalog(tmp_path, missing))
    spaced = broken(raw, lambda d: d["actors"]["dino"][field].append("two words"))
    with pytest.raises(ValueError, match=rf"dino.*{field}"):
        load_catalog(write_catalog(tmp_path, spaced))


def test_actor_keys_must_be_exactly_chomp_and_dino(tmp_path, raw):
    # 장면 대상 id 로 "chomp"/"dino" 를 그대로 쓰는 뒤쪽 Task 들이 이 이름에 기대고 있다.
    extra = broken(raw, lambda d: d["actors"].update(cat={"actions": ["jump"], "faces": ["happy"]}))
    with pytest.raises(ValueError, match="actors"):
        load_catalog(write_catalog(tmp_path, extra))
    missing = broken(raw, lambda d: d["actors"].pop("dino"))
    with pytest.raises(ValueError, match="actors"):
        load_catalog(write_catalog(tmp_path, missing))


@pytest.mark.parametrize("key", ["version", "max_count", "max_ops", "max_items"])
def test_integer_fields_must_be_positive_integers(tmp_path, raw, key):
    for bad in (0, -1, 2.5, "4", True, None):
        data = broken(raw, lambda d: d.update({key: bad}))
        with pytest.raises(ValueError, match=key):
            load_catalog(write_catalog(tmp_path, data))
