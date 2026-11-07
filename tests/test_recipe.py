"""schema.org Recipe pages: the structured block, frontmatter and messy markup.

Every recipe here is invented. Pages go through the real converter
(HTMLToMarkdown.convert_file); test_recipe_crawl.py runs the same pages
through a real crawl of a server on 127.0.0.1.
"""

import json
import re

import pytest
import yaml

from scrapepy.convert.recipe import (
    find_recipes,
    loads_lenient,
    readable_duration,
    split_steps,
)
from scrapepy.convert.to_markdown import HTMLToMarkdown

STORY = (
    "We first made this on a wet Saturday with two small helpers and far too much flour. "
    "It has become a weekly favourite because everyone gets a job and nothing is too hot "
    "or sharp for little hands."
)
TIPS = "Swap the raisins for chopped apricots if you prefer, and keep leftovers in a tin."

EMPTY_NUTRITION = [
    "- Calories: not stated", "- Fat: not stated", "- Saturated fat: not stated",
    "- Unsaturated fat: not stated", "- Trans fat: not stated",
    "- Carbohydrate: not stated", "- Sugar: not stated", "- Fibre: not stated",
    "- Protein: not stated", "- Sodium: not stated", "- Cholesterol: not stated",
]


def ld(*objects, raw=None):
    """JSON-LD script tags for the given objects (or raw text)."""
    blocks = [raw] if raw is not None else []
    blocks += [json.dumps(o) for o in objects]
    return "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)


def page(head="", body="", title="Rainbow Oat Bites"):
    return (
        f"<html><head><title>{title}</title>{head}</head><body>"
        f"<nav><a href='/'>Home</a> <a href='/recipes'>Recipes</a></nav>"
        f"<article><h1>{title}</h1>{body}</article>"
        f"<footer>Example kitchen footer</footer></body></html>"
    )


def convert(tmp_path, html, url="https://kitchen.example/recipes/oat-bites"):
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    conv = HTMLToMarkdown(src, output_dir=tmp_path / "out", base_url="https://kitchen.example",
                          domain="kitchen.example", absolute_links=True)
    meta = {"url": url, "page_type": "article", "domain": "kitchen.example"}
    ok = conv.convert_file(src / "page.html", metadata=meta, html=html)
    assert ok, conv.last_error
    text = open(conv.last_output_path, encoding="utf-8").read()
    end = text.index("\n---\n", 3)
    front = yaml.safe_load(text[4:end])
    body = text[end + 5:].removeprefix("\n")
    return front, body


def section(body, heading, level=2):
    """Lines under a heading, up to the next heading of the same or higher level."""
    lines = body.split("\n")
    marker = "#" * level + " " + heading
    start = lines.index(marker) + 1
    out = []
    for line in lines[start:]:
        m = re.match(r"^(#{1,6}) ", line)
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    return [ln for ln in out if ln.strip()]


BASIC = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Rainbow Oat Bites",
    "description": "No-bake oat bites for small hands.",
    "author": {"@type": "Person", "name": "Pat Example"},
    "datePublished": "2026-03-14",
    "recipeYield": "16 bites",
    "prepTime": "PT20M",
    "cookTime": "PT0M",
    "totalTime": "PT1H20M",
    "recipeCategory": "Snack",
    "recipeCuisine": "British",
    "keywords": "oats, no bake, lunchbox",
    "suitableForDiet": ["https://schema.org/VegetarianDiet", "https://schema.org/GlutenFreeDiet"],
    "recipeIngredient": [
        "150 g rolled oats",
        "3 tbsp smooth peanut butter",
        "2 tbsp runny honey",
        "50 g raisins",
    ],
    "recipeInstructions": [
        {"@type": "HowToStep", "text": "Tip the oats into a large bowl."},
        {"@type": "HowToStep", "text": "Stir in the peanut butter and honey until sticky."},
        {"@type": "HowToStep", "text": "Fold through the raisins, roll into 16 balls and chill for 1 hour."},
    ],
    "nutrition": {
        "@type": "NutritionInformation",
        "servingSize": "1 bite",
        "calories": "95 kcal",
        "fatContent": "4.1 g",
        "saturatedFatContent": "0.8 g",
        "carbohydrateContent": "12 g",
        "sugarContent": "5.2 g",
        "fiberContent": "1.3 g",
        "proteinContent": "2.6 g",
        "sodiumContent": "25 mg",
    },
}

BASIC_PROSE = (
    f"<p>{STORY}</p>"
    "<h2>Ingredients</h2><ul>"
    "<li>150 g rolled oats</li><li>3 tbsp smooth peanut butter</li>"
    "<li>2 tbsp runny honey</li><li>50 g raisins</li></ul>"
    "<h2>Method</h2><ol>"
    "<li>Tip the oats into a large bowl.</li>"
    "<li>Stir in the peanut butter and honey until sticky.</li>"
    "<li>Fold through the raisins, roll into 16 balls and chill for 1 hour.</li></ol>"
    f"<h2>Tips</h2><p>{TIPS}</p>"
)


# 1. A complete recipe: exact block, frontmatter, no duplicated lists
def test_basic_recipe_exact_block(tmp_path):
    front, body = convert(tmp_path, page(ld(BASIC), BASIC_PROSE))
    expected = "\n".join([
        "# Rainbow Oat Bites",
        "",
        "## Recipe details",
        "",
        "No-bake oat bites for small hands.",
        "",
        "- Name: Rainbow Oat Bites",
        "- Servings: 16 bites",
        "- Prep time: 20 minutes (PT20M)",
        "- Cook time: 0 minutes (PT0M)",
        "- Total time: 1 hour 20 minutes (PT1H20M)",
        "- Author: Pat Example",
        "- Category: Snack",
        "- Cuisine: British",
        "- Diet: Vegetarian, Gluten free",
        "- Keywords: oats, no bake, lunchbox",
        "- Published: 2026-03-14",
        "",
        "## Ingredients",
        "",
        "- 150 g rolled oats",
        "- 3 tbsp smooth peanut butter",
        "- 2 tbsp runny honey",
        "- 50 g raisins",
        "",
        "## Method",
        "",
        "1. Tip the oats into a large bowl.",
        "2. Stir in the peanut butter and honey until sticky.",
        "3. Fold through the raisins, roll into 16 balls and chill for 1 hour.",
        "",
        "## Nutrition",
        "",
        "Serving size: 1 bite",
        "",
        "- Calories: 95 kcal",
        "- Fat: 4.1 g",
        "- Saturated fat: 0.8 g",
        "- Unsaturated fat: not stated",
        "- Trans fat: not stated",
        "- Carbohydrate: 12 g",
        "- Sugar: 5.2 g",
        "- Fibre: 1.3 g",
        "- Protein: 2.6 g",
        "- Sodium: 25 mg",
        "- Cholesterol: not stated",
        "",
        "## Page text",
        "",
        STORY,
        "",
        "### Tips",
        "",
        TIPS,
    ])
    assert body.strip() == expected
    assert front["page_type"] == "recipe"
    assert front["title"] == "Rainbow Oat Bites"
    assert front["url"] == "https://kitchen.example/recipes/oat-bites"
    assert front["recipe_category"] == "Snack"
    assert front["recipe_cuisine"] == "British"
    assert front["servings"] == "16 bites"
    assert front["total_time"] == "1 hour 20 minutes"
    assert front["keywords"] == ["oats", "no bake", "lunchbox"]
    # Each ingredient and step appears once
    assert body.count("rolled oats") == 1
    assert body.count("Tip the oats") == 1
    assert "\n## Ingredients" in body and body.count("Ingredients") == 1


# 2. @graph, @type as a list, author by @id reference, HowToSection
def test_graph_type_list_and_sections(tmp_path):
    recipe = {
        "@type": ["Recipe", "NewsArticle"],
        "@id": "https://kitchen.example/#recipe",
        "name": "Traffic Light Pizza Toasts",
        "author": {"@id": "https://kitchen.example/#cook"},
        "recipeYield": ["4", "4 toasts"],
        "recipeIngredient": ["4 slices bread", "4 tbsp passata", "1 pepper, sliced"],
        "recipeInstructions": [
            {"@type": "HowToSection", "name": "Make the base", "itemListElement": [
                {"@type": "HowToStep", "text": "Toast the bread lightly."},
                {"@type": "HowToStep", "text": "Spread with passata."},
            ]},
            {"@type": "HowToSection", "name": "Top and bake", "itemListElement": [
                {"@type": "HowToStep", "text": "Add the pepper in red, amber and green rows."},
                {"@type": "HowToSection", "name": "inner", "itemListElement": [
                    {"@type": "HowToStep", "text": "Grill for 5 minutes."},
                ]},
            ]},
        ],
    }
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": "https://kitchen.example/#site", "name": "Kitchen"},
        recipe,
        {"@type": "Person", "@id": "https://kitchen.example/#cook", "name": "Sam Cook"},
    ]}
    front, body = convert(tmp_path, page(ld(graph), f"<p>{STORY}</p>",
                                         title="Traffic Light Pizza Toasts"))
    assert section(body, "Method") == [
        "### Make the base",
        "1. Toast the bread lightly.",
        "2. Spread with passata.",
        "### Top and bake",
        "1. Add the pepper in red, amber and green rows.",
        "2. Grill for 5 minutes.",
    ]
    assert "- Author: Sam Cook" in body
    assert "- Servings: 4; 4 toasts" in body
    assert front["servings"] == "4"
    assert front["page_type"] == "recipe"


# 3. Several JSON-LD blocks: one broken, one irrelevant, one recipe
def test_multiple_blocks_with_broken_and_irrelevant(tmp_path):
    head = ld({"@context": "https://schema.org", "@type": "Organization", "name": "Kitchen"},
              BASIC, raw="{ this is not json at all")
    # A recipe card that splits amount, unit and name into spans with a checkbox
    card = (
        "<div class='card'><h3>Ingredients</h3><ul>"
        "<li><span>&#9634;</span> <span>150</span> <span>g</span> <span>rolled oats</span></li>"
        "<li><span>&#9634;</span> <span>3</span> <span>tbsp</span> "
        "<span>smooth peanut butter</span></li></ul>"
        "<h3>Instructions</h3><ol><li><p>Tip the oats into a large bowl.</p></li></ol>"
        "<h3>Notes</h3><p>Keeps for a week in the fridge in a sealed box.</p></div>"
    )
    front, body = convert(tmp_path, page(head, f"<p>{STORY}</p>{card}"))
    assert body.count("rolled oats") == 1
    assert body.count("Tip the oats") == 1
    assert "Instructions" not in body
    assert "### Notes\n\nKeeps for a week in the fridge in a sealed box." in body
    assert front["page_type"] == "recipe"
    assert section(body, "Ingredients") == [
        "- 150 g rolled oats", "- 3 tbsp smooth peanut butter",
        "- 2 tbsp runny honey", "- 50 g raisins",
    ]


# 4. Two recipes on one page: one titled section each
def test_two_recipes_get_a_section_each(tmp_path):
    a = {"@type": "Recipe", "name": "Banana Pancakes", "recipeYield": 2,
         "recipeIngredient": ["1 ripe banana", "2 eggs"],
         "recipeInstructions": ["Mash the banana.", "Beat in the eggs and fry small rounds."]}
    b = {"@type": "Recipe", "name": "Berry Sauce", "recipeYield": "1 small jar",
         "recipeIngredient": ["100 g frozen berries", "1 tsp honey"],
         "recipeInstructions": "Warm the berries with the honey until soft.",
         "nutrition": {"@type": "NutritionInformation", "calories": "20 kcal"}}
    front, body = convert(tmp_path, page(ld([a, b]), f"<p>{STORY}</p>",
                                         title="Pancake Morning"))
    assert "## Banana Pancakes" in body and "## Berry Sauce" in body
    assert body.index("## Banana Pancakes") < body.index("## Berry Sauce")
    pancakes = body[body.index("## Banana Pancakes"):body.index("## Berry Sauce")]
    assert section(pancakes, "Ingredients", level=3) == ["- 1 ripe banana", "- 2 eggs"]
    assert section(pancakes, "Method", level=3) == [
        "1. Mash the banana.", "2. Beat in the eggs and fry small rounds."]
    assert section(pancakes, "Nutrition", level=3) == ["Not stated."]
    sauce = body[body.index("## Berry Sauce"):]
    assert section(sauce, "Nutrition", level=3) == (
        ["Serving size: not stated", "- Calories: 20 kcal"] + EMPTY_NUTRITION[1:])
    # Frontmatter comes from the first recipe
    assert front["servings"] == "2"
    assert front["title"] == "Pancake Morning"


# 5. HTML entities and tags inside strings
def test_entities_and_tags_in_strings(tmp_path):
    recipe = {
        "@type": "Recipe",
        "name": "Mac &amp; Cheese Muffins",
        "description": "<p>Cheesy &amp; <em>easy</em>.</p>",
        "recipeIngredient": ["&frac12; cup <b>milk</b>", "100&nbsp;g cheddar, grated",
                             "Pinch of salt &#038; pepper", "&amp;lt;b&amp;gt;1&amp;lt;/b&amp;gt; egg"],
        "recipeInstructions": [{"@type": "HowToStep",
                                "text": "Mix <a href='/x'>everything</a> &ndash; then bake."}],
    }
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>"))
    assert section(body, "Ingredients") == [
        "- ½ cup milk", "- 100 g cheddar, grated", "- Pinch of salt & pepper", "- 1 egg"]
    assert section(body, "Method") == ["1. Mix everything – then bake."]
    assert "- Name: Mac & Cheese Muffins" in body
    assert "Cheesy & easy." in body


# 6. Trailing commas and comments in the JSON
def test_trailing_commas_and_comments(tmp_path):
    raw = """
    // recipe card data
    {
      "@context": "https://schema.org", /* inline */
      "@type": "Recipe",
      "name": "Cucumber Boats",
      "recipeIngredient": [
        "1 cucumber",
        "2 tbsp cream cheese", // the filling
      ],
      "recipeInstructions": ["Halve the cucumber lengthways.", "Fill with cream cheese.",],
      "url": "https://kitchen.example/a//b",
    }
    """
    front, body = convert(tmp_path, page(ld(raw=raw), f"<p>{STORY}</p>", title="Cucumber Boats"))
    assert section(body, "Ingredients") == ["- 1 cucumber", "- 2 tbsp cream cheese"]
    assert section(body, "Method") == ["1. Halve the cucumber lengthways.",
                                       "2. Fill with cream cheese."]


# 7. Nutrition numbers as strings with units, bare numbers and objects
def test_nutrition_values_kept_exactly(tmp_path):
    recipe = dict(BASIC, nutrition={
        "@type": "NutritionInformation",
        "calories": 210,
        "fatContent": "12 g",
        "saturatedFatContent": 2.5,
        "carbohydrateContent": "20.0g",
        "sugarContent": {"@type": "QuantitativeValue", "value": 9, "unitText": "g"},
        "proteinContent": "",
        "sodiumContent": "0.3 g salt",
    })
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>"))
    assert section(body, "Nutrition") == [
        "Serving size: not stated",
        "- Calories: 210",
        "- Fat: 12 g",
        "- Saturated fat: 2.5",
        "- Unsaturated fat: not stated",
        "- Trans fat: not stated",
        "- Carbohydrate: 20.0g",
        "- Sugar: 9 g",
        "- Fibre: not stated",
        "- Protein: not stated",
        "- Sodium: 0.3 g salt",
        "- Cholesterol: not stated",
    ]


# 8. Ingredients and instructions as single strings with newlines
def test_single_string_lists(tmp_path):
    recipe = {
        "@type": "Recipe", "name": "Fruit Kebabs",
        "recipeIngredient": "6 strawberries\n1 banana, sliced\r\n12 grapes\n",
        "recipeInstructions": "1. Wash the fruit.\n2. Thread onto blunt skewers.\n\n3. Serve straight away.",
    }
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>", title="Fruit Kebabs"))
    assert section(body, "Ingredients") == ["- 6 strawberries", "- 1 banana, sliced", "- 12 grapes"]
    assert section(body, "Method") == ["1. Wash the fruit.", "2. Thread onto blunt skewers.",
                                       "3. Serve straight away."]


# 9. Instructions as one paragraph of sentences, numeric yield
def test_sentence_instructions_and_numeric_yield(tmp_path):
    recipe = {
        "@type": "Recipe", "name": "Yoghurt Bark",
        "recipeYield": 6, "totalTime": "PT2H",
        "recipeIngredient": ["300 g Greek yoghurt", "A handful of blueberries"],
        "recipeInstructions": "Line a tray with paper. Spread the yoghurt thinly! "
                              "Scatter over the berries. Freeze for 2 hours, then snap.",
    }
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>", title="Yoghurt Bark"))
    assert section(body, "Method") == [
        "1. Line a tray with paper.", "2. Spread the yoghurt thinly!",
        "3. Scatter over the berries.", "4. Freeze for 2 hours, then snap."]
    assert front["servings"] == "6"
    assert front["total_time"] == "2 hours"
    assert "- Total time: 2 hours (PT2H)" in body


# 10. Instructions as an HTML list inside a string
def test_html_list_instructions(tmp_path):
    recipe = {
        "@type": "Recipe", "name": "Cheese Straws",
        "recipeIngredient": ["1 sheet puff pastry", "50 g cheddar"],
        "recipeInstructions": "<ol><li>Sprinkle the cheese over the pastry.</li>"
                              "<li>Cut into strips and twist.</li><li>Bake for 12 minutes.</li></ol>",
    }
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>", title="Cheese Straws"))
    assert section(body, "Method") == ["1. Sprinkle the cheese over the pastry.",
                                       "2. Cut into strips and twist.", "3. Bake for 12 minutes."]


# 11. Missing fields everywhere
def test_minimal_recipe_says_not_stated(tmp_path):
    recipe = {"@type": "Recipe", "recipeIngredient": ["2 apples"]}
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>", title="Apple Snack"))
    assert section(body, "Recipe details") == []
    assert section(body, "Ingredients") == ["- 2 apples"]
    assert section(body, "Method") == ["Not stated."]
    assert section(body, "Nutrition") == ["Not stated."]
    assert front["page_type"] == "recipe"
    for key in ("recipe_category", "recipe_cuisine", "servings", "total_time", "keywords"):
        assert key not in front


# 12. A listing page (ItemList of recipes) is not a recipe
def test_item_list_is_not_a_recipe(tmp_path):
    prose = f"<p>{STORY}</p><p>{TIPS}</p>"
    listing = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "item": dict(BASIC)},
        {"@type": "ListItem", "position": 2, "url": "https://kitchen.example/recipes/two"},
    ]}
    front, body = convert(tmp_path, page(ld(listing), prose, title="All recipes"))
    assert front["page_type"] == "article"
    assert "## Ingredients" not in body
    assert "wet Saturday" in body


# 13. Only a broken block: normal conversion, no crash
def test_broken_block_falls_back(tmp_path):
    front, body = convert(tmp_path, page(ld(raw='{"@type": "Recipe", "name": "Oops", [[['),
                                         f"<p>{STORY}</p><p>{TIPS}</p>"))
    assert front["page_type"] == "article"
    assert "## Recipe details" not in body
    assert "wet Saturday" in body


# 14. Microdata
def test_microdata_recipe(tmp_path):
    body_html = f"""
    <p>{STORY}</p>
    <div itemscope itemtype="https://schema.org/Recipe">
      <h2 itemprop="name">Mini Veggie Frittatas</h2>
      <meta itemprop="recipeYield" content="12">
      <meta itemprop="totalTime" content="PT35M">
      <span itemprop="author" itemscope itemtype="https://schema.org/Person">
        <span itemprop="name">Lee Example</span></span>
      <span itemprop="recipeCategory">Lunch</span>
      <h3>Ingredients</h3>
      <ul><li itemprop="recipeIngredient">6 eggs</li>
          <li itemprop="recipeIngredient">1 handful spinach, chopped</li></ul>
      <h3>Method</h3>
      <ol itemprop="recipeInstructions">
        <li>Whisk the eggs.</li><li>Stir in the spinach.</li><li>Bake in a muffin tin.</li>
      </ol>
      <div itemprop="nutrition" itemscope itemtype="https://schema.org/NutritionInformation">
        <span itemprop="calories">60 kcal</span>
        <meta itemprop="proteinContent" content="5 g">
      </div>
    </div>
    <p>{TIPS}</p>"""
    front, body = convert(tmp_path, page("", body_html, title="Mini Veggie Frittatas"))
    assert section(body, "Ingredients") == ["- 6 eggs", "- 1 handful spinach, chopped"]
    assert section(body, "Method") == ["1. Whisk the eggs.", "2. Stir in the spinach.",
                                       "3. Bake in a muffin tin."]
    nutrition = section(body, "Nutrition")
    assert "- Calories: 60 kcal" in nutrition and "- Protein: 5 g" in nutrition
    assert "- Author: Lee Example" in body
    assert front["servings"] == "12"
    assert front["recipe_category"] == "Lunch"
    assert body.count("6 eggs") == 1 and body.count("Whisk the eggs") == 1
    assert TIPS in body


# 15. Prefixed @type strings, list keywords, name-only steps, tips, a nutrition list
def test_odd_types_tips_and_lists(tmp_path):
    recipe = {
        "@context": "http://schema.org",
        "@type": "http://schema.org/Recipe",
        "name": "Hedgehog Rolls",
        "keywords": ["bread", "Bread", "baking with kids"],
        "recipeCategory": ["Baking", "Party food"],
        "recipeIngredient": [{"@type": "PropertyValue", "name": "500 g bread dough"},
                             "12 currants", None, ""],
        "recipeInstructions": {"@type": "HowToSection", "name": "", "itemListElement": [
            {"@type": "HowToStep", "name": "Shape the dough into 6 ovals."},
            {"@type": "HowToTip", "text": "Wet scissors cut cleanly."},
            {"@type": "HowToStep", "text": "Snip spikes and press in currant eyes."},
        ]},
        "nutrition": [{"@type": "NutritionInformation", "servingSize": "1 roll",
                       "calories": "180 kcal"}],
    }
    front, body = convert(tmp_path, page(ld(recipe), f"<p>{STORY}</p>", title="Hedgehog Rolls"))
    assert section(body, "Ingredients") == ["- 500 g bread dough", "- 12 currants"]
    assert section(body, "Method") == [
        "### Part 1",
        "1. Shape the dough into 6 ovals.",
        "2. Tip: Wet scissors cut cleanly.",
        "3. Snip spikes and press in currant eyes.",
    ]
    assert section(body, "Nutrition")[:2] == ["Serving size: 1 roll", "- Calories: 180 kcal"]
    assert front["keywords"] == ["bread", "baking with kids"]
    assert front["recipe_category"] == "Baking, Party food"


# 16. A recipe with no other page text still converts
def test_recipe_without_page_text(tmp_path):
    front, body = convert(tmp_path, page(ld(BASIC), "", title="Rainbow Oat Bites"))
    assert body.startswith("# Rainbow Oat Bites\n\n## Recipe details\n")
    assert "## Page text" not in body
    assert front["page_type"] == "recipe"


@pytest.mark.parametrize("raw,expected", [
    ("PT1H30M", "1 hour 30 minutes"),
    ("PT90M", "90 minutes"),
    ("P0DT0H45M", "45 minutes"),
    ("PT0.5H", "0.5 hours"),
    ("P1DT2H", "1 day 2 hours"),
    ("PT1M30S", "1 minute 30 seconds"),
    ("PT0S", "0 minutes"),
    ("pt20m", "20 minutes"),
    ("30 mins", "30 mins"),
    ("", ""),
])
def test_readable_duration(raw, expected):
    assert readable_duration(raw) == expected


def test_loads_lenient_shapes():
    assert loads_lenient('<!-- {"a": [1, 2,],} -->') == {"a": [1, 2]}
    assert loads_lenient('{"a": "line\nbreak"}') == {"a": "line\nbreak"}
    assert loads_lenient('{"u": "http://x.example/a//b"} // note') == {"u": "http://x.example/a//b"}
    with pytest.raises(ValueError):
        loads_lenient("{nope")


def test_split_steps_numbering_removed():
    assert split_steps("Step 1: Mix.\nStep 2) Bake.") == ["Mix.", "Bake."]


def test_find_recipes_never_raises():
    weird = [
        "", "<html></html>",
        '<script type="application/ld+json">null</script>',
        '<script type="application/ld+json">[1, "x", {"@type": 5}]</script>',
        '<script type="application/ld+json">{"@type": "Recipe", "recipeIngredient": {"a": 1},'
        ' "recipeInstructions": 7, "nutrition": "lots"}</script>',
        '<script type="application/ld+json">' + "[" * 5000 + "]" * 5000 + "</script>",
    ]
    for html in weird:
        assert isinstance(find_recipes(html), list)
