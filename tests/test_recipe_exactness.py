"""Recipe facts must never be lost or altered: a duplicated line is acceptable,
a dropped note, a changed quantity or a re-serialised number is not.
Every recipe here is invented."""

import pytest

from scrapepy.convert.recipe import find_recipes, loads_lenient, split_steps, strip_recipe_text

from .test_recipe import STORY, convert, ld, page, section


def recipe(**kw):
    base = {"@type": "Recipe", "name": "Honey Milk Oats",
            "recipeIngredient": ["100 g porridge oats"],
            "recipeInstructions": ["Warm the milk gently."]}
    base.update(kw)
    return base


# Blocker 1: only exact (normalised) matches are removed from the page text

@pytest.mark.parametrize("data,visible", [
    ({"recipeInstructions": ["Stir the honey into the warm milk."]},
     "<p>Never stir the honey into the warm milk.</p>"),
    ({"recipeInstructions": ["Leave the mixture to cool completely."]},
     "<p>Don't leave the mixture to cool completely.</p>"),
    ({"recipeInstructions": ["Mix the oats and butter."]},
     "<ul><li>Do not mix the oats and butter.</li></ul>"),
    ({"recipeIngredient": ["1 tsp vanilla extract"]},
     "<ul><li>1 tsp vanilla extract (optional)</li></ul>"),
    ({"recipeInstructions": ["Preheat the oven to 180C"]},
     "<ol><li>Preheat the oven to 180C (160C fan)</li></ol>"),
])
def test_page_text_that_adds_or_reverses_a_fact_is_kept(tmp_path, data, visible):
    front, body = convert(tmp_path, page(ld(recipe(**data)), f"<p>{STORY}</p>{visible}"))
    text = visible.split(">", 1)[1].split("<", 1)[0]
    if visible.startswith("<ul><li>") or visible.startswith("<ol><li>"):
        text = visible.split("<li>", 1)[1].split("<", 1)[0]
    page_text = body.split("## Page text", 1)[1]
    assert text in page_text


def test_close_but_longer_item_is_kept_not_fuzzy_matched(tmp_path):
    # Contains the 12+ character step and is under 12 characters longer
    data = recipe(recipeInstructions=["Whisk the eggs well."])
    front, body = convert(tmp_path, page(ld(data), f"<p>{STORY}</p>"
                                         "<ul><li>Whisk the eggs well, twice.</li></ul>"))
    assert "Whisk the eggs well, twice." in body.split("## Page text", 1)[1]


def test_equal_items_still_removed_with_spans_fractions_and_numbering(tmp_path):
    data = recipe(
        recipeIngredient=["1/2 cup milk", "150 g rolled oats"],
        recipeInstructions=["Warm the milk gently.", "Stir in the oats."],
    )
    visible = (
        "<ul><li>\u2022 <span>1/2</span> <span>cup</span> <span>milk</span></li>"
        "<li><span>150</span><span> g </span><span>rolled oats</span></li></ul>"
        "<ol><li>1. Warm the milk gently</li><li>Step 2: Stir in the oats.</li></ol>"
    )
    front, body = convert(tmp_path, page(ld(data), f"<p>{STORY}</p>{visible}"))
    assert body.count("rolled oats") == 1
    assert body.count("1/2 cup milk") == 1
    assert body.count("Stir in the oats") == 1


def test_removal_is_one_to_one_and_skips_comments():
    data = recipe(recipeInstructions=["Warm the milk gently.", "Enjoy!"])
    visible = (
        "<p>Enjoy!</p><p>Enjoy!</p>"
        "<div id='comments'><p>Warm the milk gently.</p><p>Enjoy!</p></div>"
        "<ol class='reply-list'><li>Enjoy!</li></ol>"
    )
    html = page(ld(data), f"<p>{STORY}</p>{visible}")
    out = strip_recipe_text(html, find_recipes(html))
    # One "Enjoy!" removed for the one step; the second and the comments stay
    assert out.count("Enjoy!") == 3
    assert out.count("Warm the milk gently.") == 1
    assert "<div id=\"comments\"><p>Warm the milk gently.</p><p>Enjoy!</p></div>" in out


# Blocker 2: quantities at the start of a step are never taken for numbering

@pytest.mark.parametrize("steps,expected", [
    (["Warm the milk.", "2 - 3 drops of food colouring go in now."],
     ["Warm the milk.", "2 - 3 drops of food colouring go in now."]),
    (["Warm the milk.", "3 cups of flour go in next."],
     ["Warm the milk.", "3 cups of flour go in next."]),
    (["1. Mix"], ["Mix"]),
    (["Warm the milk.", "Step 2: Bake"], ["Warm the milk.", "Bake"]),
    ([f"Do thing {i}." for i in range(1, 10)] + ["10) Serve"],
     [f"Do thing {i}." for i in range(1, 10)] + ["Serve"]),
    (["Warm.", "Pour.", "Stir.", "Taste.", "2. Add 2 eggs"],
     ["Warm.", "Pour.", "Stir.", "Taste.", "2. Add 2 eggs"]),
    (["1. Warm the milk.", "2. Add the oats.", "3) Stir for 2 minutes."],
     ["Warm the milk.", "Add the oats.", "Stir for 2 minutes."]),
    (["Warm the milk.", "1.5 cups of oats go in."], ["Warm the milk.", "1.5 cups of oats go in."]),
    (["Warm the milk.", "2) 1/2 cup sugar."], ["Warm the milk.", "2) 1/2 cup sugar."]),
])
def test_step_numbering_rules(steps, expected):
    html = ld(recipe(recipeInstructions=steps))
    (r,) = find_recipes(html)
    got = [s for _title, part in r.method for s in part]
    assert got == expected


# Blocker 3: one instruction string is split on newlines or explicit numbers only

def test_abbreviations_do_not_split_a_step():
    text = "Heat oven to approx. 180 C. Mix 1 tsp. salt with flour."
    assert split_steps(text) == [text]


@pytest.mark.parametrize("text", [
    "Add 2 tbsp. sugar, e.g. caster, i.e. fine. Weigh 4 oz. butter.",
    "Use no. 2 tin. Bake 10-15 min. until golden.",
    "Add 1.5 cups milk. Rest 5 min. Serve.",
])
def test_single_line_is_one_step(text):
    assert split_steps(text) == [text]


def test_multi_line_string():
    assert split_steps("Warm the milk.\nAdd 1.5 cups oats.\n\nServe.") == [
        "Warm the milk.", "Add 1.5 cups oats.", "Serve."]


def test_explicitly_numbered_one_line_string():
    (r,) = find_recipes(ld(recipe(
        recipeInstructions="1. Warm the milk. 2. Add 1.5 cups oats. 3. Serve warm.")))
    assert r.method == [(None, ["Warm the milk.", "Add 1.5 cups oats.", "Serve warm."])]
    (r,) = find_recipes(ld(recipe(
        recipeInstructions="Step 1 Warm the milk. Step 2 Add the oats.")))
    assert r.method == [(None, ["Warm the milk.", "Add the oats."])]


def test_converted_page_keeps_one_step(tmp_path):
    data = recipe(recipeInstructions="Heat oven to approx. 180 C. Mix 1 tsp. salt with flour.")
    front, body = convert(tmp_path, page(ld(data), f"<p>{STORY}</p>"))
    assert section(body, "Method") == ["1. Heat oven to approx. 180 C. Mix 1 tsp. salt with flour."]


# Should-fix 3: nothing dropped silently

def test_nutrition_list_dict_and_nested_ingredients(tmp_path):
    data = recipe(
        recipeIngredient=[{"@type": "HowToSupply", "name": "2 eggs"},
                          {"amount": "1", "unit": "cup", "food": "milk"},
                          ["50 g sugar", ["1 pinch salt"]]],
        nutrition={"@type": "NutritionInformation",
                   "calories": ["120 kcal", "502 kJ"], "fatContent": []},
    )
    front, body = convert(tmp_path, page(ld(data), f"<p>{STORY}</p>"))
    assert section(body, "Ingredients") == [
        "- 2 eggs", "- amount: 1; unit: cup; food: milk", "- 50 g sugar", "- 1 pinch salt"]
    nutrition = section(body, "Nutrition")
    assert "- Calories: 120 kcal / 502 kJ" in nutrition
    assert "- Fat: not stated" in nutrition


# Should-fix 4: numbers keep their published text

def test_numbers_keep_their_text(tmp_path):
    raw = ('{"@type": "Recipe", "name": "N", "recipeYield": 12345678901234567890123,'
           ' "recipeIngredient": ["1 egg"], "recipeInstructions": ["Mix."],'
           ' "nutrition": {"calories": 1e21, "fatContent": 0.10, "sugarContent": 1.0,'
           ' "proteinContent": 12.50, "sodiumContent": NaN, "fiberContent": 1e400,'
           ' "carbohydrateContent": "200 kcal"}}')
    front, body = convert(tmp_path, page(ld(raw=raw), f"<p>{STORY}</p>"))
    nutrition = section(body, "Nutrition")
    for line in ("- Calories: 1e21", "- Fat: 0.10", "- Sugar: 1.0", "- Protein: 12.50",
                 "- Sodium: NaN", "- Fibre: 1e400", "- Carbohydrate: 200 kcal"):
        assert line in nutrition
    assert front["servings"] == "12345678901234567890123"


def test_loads_lenient_keeps_number_text():
    assert loads_lenient('{"a": 1.0, "b": 0.10, "c": 7}') == {"a": "1.0", "b": "0.10", "c": "7"}


# Re-review: normalising must never hide a difference in the facts

def _stripped(ingredients, visible_items):
    data = recipe(recipeIngredient=ingredients)
    html = page(ld(data), f"<p>{STORY}</p><ul>" + "".join(f"<li>{v}</li>" for v in visible_items)
                + "</ul>")
    return strip_recipe_text(html, find_recipes(html))


@pytest.mark.parametrize("json_item,visible,kept", [
    ("200 g sugar", "~200 g sugar", "~200 g sugar"),
    ("1 T sugar", "1 t sugar", "1 t sugar"),
    ("1/2 cup milk", "1-2 cup milk", "1-2 cup milk"),
    ("2 eggs", "*2 eggs", "*2 eggs"),
    ("5 g salt", "&lt;5 g salt", "&lt;5 g salt"),
    ("11/2 cups flour", "1\u00bd cups flour", "1\u00bd cups flour"),
    ("102 cm tin", "10\u00b2 cm tin", "10\u00b2 cm tin"),
    ("21/2 tbsp oil", "2\u00b9\u2044\u2082 tbsp oil", "2\u00b9\u2044\u2082 tbsp oil"),
    ("1 1/2 cups flour", "1\u00bd cups flour", "1\u00bd cups flour"),
    ("1/2 cup milk", "\u00bd cup milk", "\u00bd cup milk"),
    ("\u00bd cup milk", "1/2 cup milk", "1/2 cup milk"),
    ("2 eggs", "* 2 eggs", "* 2 eggs"),
    ("2 eggs", "\u2605 2 eggs", "\u2605 2 eggs"),
    ("eggs", "2) eggs", "2) eggs"),
    ("2.5 g salt", "2,5 g salt", "2,5 g salt"),
    ("1-2 tsp honey", "1\u20132 tsp honey", "1\u20132 tsp honey"),
])
def test_visible_item_that_differs_is_kept(json_item, visible, kept):
    assert f"<li>{kept}</li>" in _stripped([json_item], [visible])


@pytest.mark.parametrize("json_item,visible", [
    ("200 g sugar", "200 g sugar"),
    ("200 g sugar", "  200   g  sugar "),
    ("200 g sugar", "200\u2009g\tsugar"),
    ("200 g sugar", "200 \u200bg sugar"),
    ("2 eggs", "- 2 eggs"),
    ("2 eggs", "\u00b7 2 eggs"),
    ("2 eggs", "2 eggs:"),
    ("2 eggs", "• 2 eggs"),
    ("2 eggs", "2 eggs."),
])
def test_same_item_is_still_removed(json_item, visible):
    out = _stripped([json_item], [visible])
    assert "<li>" not in out


def test_unit_spacing_difference_is_left_as_a_duplicate():
    # "200g" and "200 g" are not treated as equal: a duplicate line is acceptable
    assert "<li>200g sugar</li>" in _stripped(["200 g sugar"], ["200g sugar"])
