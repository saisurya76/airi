import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import project, Archetype


def test_single_archetype():
    r = project([Archetype(name="chat reply", volume=1000, prompt="Hello!", model="gpt-4o", expected_output_tokens=50)])
    assert r.total_volume == 1000
    assert len(r.archetypes) == 1
    unit = r.archetypes[0].unit
    assert r.archetypes[0].projected_total_tokens == unit.estimated_total_tokens * 1000
    assert abs(r.total_cost - r.archetypes[0].projected_cost) < 1e-9
    print("OK: single_archetype ->", r.to_dict())


def test_multiple_archetypes_mixed_models():
    r = project(
        [
            Archetype(name="chat reply", volume=10000, prompt="Hi there, how can I help?", model="gpt-4o-mini", expected_output_tokens=80),
            Archetype(name="doc summary", volume=500, prompt="Summarize: " + "lorem ipsum " * 200, model="claude-3-5-sonnet", expected_output_tokens=300),
        ]
    )
    assert len(r.archetypes) == 2
    assert set(r.cost_by_model.keys()) == {"gpt-4o-mini", "claude-3-5-sonnet"}
    assert r.total_volume == 10500
    assert r.total_cost == round(sum(a.projected_cost for a in r.archetypes), 6)
    assert r.total_tokens == sum(a.projected_total_tokens for a in r.archetypes)
    print("OK: multiple_archetypes_mixed_models ->", r.total_cost, r.cost_by_model)


def test_dict_input_accepted():
    r = project([{"name": "x", "volume": 5, "prompt": "hi", "model": "gpt-4o"}])
    assert r.total_volume == 5
    print("OK: dict_input_accepted")


def test_zero_volume_archetype_contributes_nothing():
    r = project([Archetype(name="unused feature", volume=0, prompt="hello", model="gpt-4o")])
    assert r.total_volume == 0
    assert r.total_cost == 0.0
    assert r.total_tokens == 0
    print("OK: zero_volume_archetype_contributes_nothing")


def test_negative_volume_rejected():
    try:
        project([Archetype(name="bad", volume=-1, prompt="hi", model="gpt-4o")])
        assert False, "should have raised"
    except ValueError:
        print("OK: negative_volume_rejected")


def test_empty_list_rejected():
    try:
        project([])
        assert False, "should have raised"
    except ValueError:
        print("OK: empty_list_rejected")


def test_too_many_archetypes_rejected():
    try:
        project([Archetype(name=f"a{i}", volume=1, prompt="hi", model="gpt-4o") for i in range(51)])
        assert False, "should have raised"
    except ValueError:
        print("OK: too_many_archetypes_rejected")


def test_unknown_model_still_projects():
    r = project([Archetype(name="mystery", volume=100, prompt="hi", model="some-future-model")])
    assert r.archetypes[0].unit.known_model is False
    assert r.archetypes[0].unit.confidence == "low"
    print("OK: unknown_model_still_projects")


def test_any_exceeded_flag():
    huge = "word " * 20000
    r = project(
        [
            Archetype(name="fits fine", volume=10, prompt="hi", model="gpt-4o"),
            Archetype(name="blows context", volume=10, prompt=huge, model="gpt-4"),
        ]
    )
    assert r.any_exceeded is True
    print("OK: any_exceeded_flag")


if __name__ == "__main__":
    test_single_archetype()
    test_multiple_archetypes_mixed_models()
    test_dict_input_accepted()
    test_zero_volume_archetype_contributes_nothing()
    test_negative_volume_rejected()
    test_empty_list_rejected()
    test_too_many_archetypes_rejected()
    test_unknown_model_still_projects()
    test_any_exceeded_flag()
    print("\nAll projector sanity checks passed.")
