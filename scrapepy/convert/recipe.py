"""schema.org Recipe data to a structured Markdown block.

A page that carries Recipe data (JSON-LD, or simple microdata) gets its
recipes written in a fixed shape (see docs/MARKDOWN-OUTPUT.md):

    ## Recipe details      description, servings, times, author, ...
    ## Ingredients         one bullet per ingredient, text as published
    ## Method              numbered steps, ### per HowToSection
    ## Nutrition           every field, "not stated" when absent
    ## Page text           the rest of the page, headings one level down

Several recipes on one page get one "## <name>" section each, with the
headings above one level deeper. The ingredient and method text is removed
from the ordinary page text before it is converted, so it appears once.

Nothing here may stop a page converting: broken JSON, odd shapes and
missing fields are skipped (logged at debug level) and the page falls back
to the normal conversion.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# Nutrition fields in output order: (schema.org property, label)
NUTRITION_FIELDS = (
    ("calories", "Calories"),
    ("fatContent", "Fat"),
    ("saturatedFatContent", "Saturated fat"),
    ("unsaturatedFatContent", "Unsaturated fat"),
    ("transFatContent", "Trans fat"),
    ("carbohydrateContent", "Carbohydrate"),
    ("sugarContent", "Sugar"),
    ("fiberContent", "Fibre"),
    ("proteinContent", "Protein"),
    ("sodiumContent", "Sodium"),
    ("cholesterolContent", "Cholesterol"),
)

NOT_STATED = "not stated"
PAGE_TEXT_HEADING = "Page text"

# Headings in the page text that only label the lists the block replaces
_LABEL_HEADINGS = {
    "ingredients", "ingredient", "ingredient list", "you will need", "what you need",
    "what you will need", "method", "methods", "instructions", "directions", "steps",
    "preparation", "nutrition", "nutrition information", "nutrition facts",
    "nutritional information", "nutrition info",
}

_MAX_DEPTH = 30


@dataclass
class Recipe:
    name: str = ""
    description: str = ""
    author: str = ""
    yields: list[str] = field(default_factory=list)
    prep_time: str = ""
    cook_time: str = ""
    total_time: str = ""
    categories: list[str] = field(default_factory=list)
    cuisines: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    diets: list[str] = field(default_factory=list)
    date_published: str = ""
    ingredients: list[str] = field(default_factory=list)
    # [(section title or None, [step, ...]), ...]
    method: list[tuple[str | None, list[str]]] = field(default_factory=list)
    nutrition: dict[str, str] | None = None
    serving_size: str = ""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_RE_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")
_RE_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")
_BLOCKISH = ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6")


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup.find_all(_BLOCKISH):
        tag.insert_after(" ")
    return soup.get_text("")


def clean_text(value) -> str:
    """Plain text from a schema.org string: entities decoded, tags removed,
    whitespace collapsed. Numbers become their plain string form."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return format_number(value)
    if isinstance(value, dict):
        for key in ("text", "name", "@value", "value"):
            if key in value:
                return clean_text(value[key])
        return ""
    if isinstance(value, list):
        return " ".join(t for t in (clean_text(v) for v in value) if t)
    text = str(value)
    for _ in range(3):
        if not _RE_ENTITY.search(text):
            break
        text = html_lib.unescape(text)
    if _RE_TAG.search(text):
        text = _html_to_text(text)
    return " ".join(text.split())


def format_number(value) -> str:
    """A JSON number as written: 200 stays 200, 12.5 stays 12.5."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    out = []
    for v in values:
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _text_list(value, split_commas: bool = False) -> list[str]:
    """Strings from a string or a list, optionally split on commas."""
    out = []
    for item in _as_list(value):
        text = clean_text(item)
        if not text:
            continue
        if split_commas:
            out.extend(p.strip() for p in text.split(",") if p.strip())
        else:
            out.append(text)
    return _dedupe(out)


# ---------------------------------------------------------------------------
# Durations
# ---------------------------------------------------------------------------

_RE_DURATION = re.compile(
    r"^P(?:(?P<years>\d+(?:[.,]\d+)?)Y)?(?:(?P<months>\d+(?:[.,]\d+)?)M)?"
    r"(?:(?P<weeks>\d+(?:[.,]\d+)?)W)?(?:(?P<days>\d+(?:[.,]\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:[.,]\d+)?)H)?(?:(?P<minutes>\d+(?:[.,]\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:[.,]\d+)?)S)?)?$",
    re.IGNORECASE,
)
_DURATION_UNITS = (
    ("years", "year"), ("months", "month"), ("weeks", "week"), ("days", "day"),
    ("hours", "hour"), ("minutes", "minute"), ("seconds", "second"),
)


def readable_duration(raw: str) -> str:
    """ISO 8601 duration to words: PT1H30M -> "1 hour 30 minutes".

    Units are kept as given (PT90M is "90 minutes"). A value that is not an
    ISO 8601 duration is returned unchanged; an empty one gives "".
    """
    raw = (raw or "").strip()
    m = _RE_DURATION.match(raw)
    if not m or raw.upper() in ("P", "PT"):
        return raw
    parts = []
    for group, unit in _DURATION_UNITS:
        value = m.group(group)
        if value is None:
            continue
        value = value.replace(",", ".")
        number = float(value)
        if number == 0:
            continue
        shown = value.lstrip("0") or "0"
        if shown.startswith("."):
            shown = "0" + shown
        parts.append(f"{shown} {unit}" + ("" if number == 1 else "s"))
    return " ".join(parts) if parts else "0 minutes"


def duration_line(raw: str) -> str:
    """"1 hour 30 minutes (PT1H30M)", or the raw value when it is not ISO."""
    readable = readable_duration(raw)
    if readable and readable != raw:
        return f"{readable} ({raw})"
    return raw


# ---------------------------------------------------------------------------
# Lenient JSON
# ---------------------------------------------------------------------------

def _strip_comments_and_trailing_commas(text: str) -> str:
    """Remove // and /* */ comments and commas before } or ], outside strings."""
    return _strip_pass(_strip_pass(text, comments=True), comments=False)


def _strip_pass(text: str, comments: bool) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if not comments:
            if c == ",":
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] in "}]":
                    i += 1
                    continue
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def loads_lenient(text: str):
    """Parse JSON-LD text, forgiving comments, trailing commas, raw control
    characters in strings and HTML comment or CDATA wrappers. Raises
    ValueError when the text still cannot be read."""
    text = (text or "").strip()
    for prefix in ("<!--", "//<![CDATA[", "<![CDATA["):
        if text.startswith(prefix):
            text = text[len(prefix):]
    for suffix in ("-->", "//]]>", "]]>"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    text = text.strip().rstrip(";")
    try:
        return json.loads(text, strict=False)
    except ValueError:
        pass
    return json.loads(_strip_comments_and_trailing_commas(text), strict=False)


# ---------------------------------------------------------------------------
# Finding Recipe objects
# ---------------------------------------------------------------------------

def _types(node: dict) -> set[str]:
    out = set()
    for t in _as_list(node.get("@type")):
        if isinstance(t, str) and t:
            out.add(re.split(r"[/:#]", t.strip())[-1].lower())
    return out


def _index_ids(node, ids: dict, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            _index_ids(item, ids, depth + 1)
    elif isinstance(node, dict):
        node_id = node.get("@id")
        if isinstance(node_id, str) and len(node) > 1:
            ids.setdefault(node_id, node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _index_ids(value, ids, depth + 1)


def _walk_recipes(node, out: list, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            _walk_recipes(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    types = _types(node)
    if "recipe" in types:
        out.append(node)
        return
    if "itemlist" in types:
        return  # a listing of recipes is not a recipe
    for key, value in node.items():
        if key != "@context" and isinstance(value, (dict, list)):
            _walk_recipes(value, out, depth + 1)


class _Resolver:
    """Follows {"@id": ...} references within the page's JSON-LD."""

    def __init__(self, ids: dict):
        self.ids = ids

    def __call__(self, value):
        if isinstance(value, dict) and set(value) <= {"@id", "@type"} and "@id" in value:
            return self.ids.get(value["@id"], value)
        return value


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

_RE_STEP_NUMBER = re.compile(r"^(?:step\s*)?\d{1,2}\s*[.):\-]\s+", re.IGNORECASE)
_RE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(“])")


def _clean_step(text: str) -> str:
    return _RE_STEP_NUMBER.sub("", clean_text(text)).strip()


def split_steps(value: str) -> list[str]:
    """Steps from one instruction string: list items, paragraphs or lines,
    else sentences."""
    text = str(value or "")
    for _ in range(3):
        if "&lt;" in text or "&amp;lt;" in text:
            text = html_lib.unescape(text)
        else:
            break
    if _RE_TAG.search(text):
        soup = BeautifulSoup(text, "html.parser")
        items = soup.find_all("li")
        if items:
            return [s for s in (_clean_step(li.get_text(" ")) for li in items) if s]
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(["p", "div"]):
            block.insert_after("\n")
        text = soup.get_text("")
    else:
        text = html_lib.unescape(text)
    lines = [ln for ln in re.split(r"\s*\n\s*", text) if ln.strip()]
    if len(lines) == 1:
        lines = _RE_SENTENCE_SPLIT.split(lines[0])
    return [s for s in (_clean_step(ln) for ln in lines) if s]


def _ingredients(value) -> list[str]:
    items = _as_list(value)
    if len(items) == 1 and isinstance(items[0], str):
        lines = [ln for ln in re.split(r"\s*(?:\n|<br\s*/?>)\s*", items[0]) if ln.strip()]
        items = lines
    out = []
    for item in items:
        text = clean_text(item)
        if text:
            out.append(text)
    return out


def _instructions(value, resolve) -> list[tuple[str | None, list[str]]]:
    parts: list[list] = []

    def add(step: str) -> None:
        if not step:
            return
        if parts and parts[-1][0] is None:
            parts[-1][1].append(step)
        else:
            parts.append([None, [step]])

    def step_text(item: dict) -> str:
        text = _clean_step(item.get("text") or "")
        if not text and item.get("itemListElement"):
            text = " ".join(
                t for t in (step_text(resolve(c)) if isinstance(c, dict) else _clean_step(c)
                            for c in _as_list(item["itemListElement"])) if t
            )
        if not text:
            text = _clean_step(item.get("name") or "")
        return text

    def collect(items, depth: int) -> list[str]:
        """Flat list of steps inside a section (nested sections flattened)."""
        steps: list[str] = []
        if depth > _MAX_DEPTH:
            return steps
        for item in _as_list(items):
            item = resolve(item)
            if isinstance(item, str):
                steps.extend(split_steps(item))
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                steps.append(format_number(item))
            elif isinstance(item, list):
                steps.extend(collect(item, depth + 1))
            elif isinstance(item, dict):
                types = _types(item)
                if "howtosection" in types or "itemlist" in types:
                    steps.extend(collect(item.get("itemListElement"), depth + 1))
                elif "howtotip" in types:
                    tip = step_text(item)
                    if tip:
                        steps.append(f"Tip: {tip}")
                else:
                    text = step_text(item)
                    if text:
                        steps.append(text)
        return steps

    def visit(item, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        item = resolve(item)
        if isinstance(item, str):
            for s in split_steps(item):
                add(s)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            add(format_number(item))
        elif isinstance(item, list):
            for x in item:
                visit(x, depth + 1)
        elif isinstance(item, dict):
            types = _types(item)
            is_section = "howtosection" in types or (
                "itemlist" in types or (not types and item.get("itemListElement")
                                        and not item.get("text"))
            )
            if is_section:
                steps = collect(item.get("itemListElement"), depth + 1)
                if steps:
                    title = clean_text(item.get("name")) or None
                    parts.append([title or "", steps])
            elif "howtotip" in types:
                tip = step_text(item)
                if tip:
                    add(f"Tip: {tip}")
            else:
                add(step_text(item))

    visit(value, 0)
    # Untitled sections get a title so their steps never read as part of the
    # section before them
    out: list[tuple[str | None, list[str]]] = []
    seen_titled = False
    for n, (title, steps) in enumerate(parts, start=1):
        if title == "":
            title = f"Part {n}"
        if title is None and seen_titled:
            title = "Other steps"
        if title:
            seen_titled = True
        out.append((title, steps))
    return out


def _nutrition_value(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        number = value.get("value")
        unit = clean_text(value.get("unitText") or value.get("unitCode") or "")
        text = clean_text(number) if number is not None else clean_text(value)
        return f"{text} {unit}".strip() if text else ""
    return clean_text(value)


def _nutrition(value, resolve) -> tuple[dict[str, str] | None, str]:
    node = resolve(value)
    if isinstance(node, list):
        node = next((resolve(n) for n in node if isinstance(resolve(n), dict)), None)
    if not isinstance(node, dict):
        return None, ""
    found = {}
    for key, _label in NUTRITION_FIELDS:
        text = _nutrition_value(node.get(key))
        if text:
            found[key] = text
    serving = _nutrition_value(node.get("servingSize"))
    if not found and not serving:
        return None, ""
    return found, serving


def _people(value, resolve) -> str:
    names = []
    for item in _as_list(value):
        item = resolve(item)
        if isinstance(item, dict):
            name = clean_text(item.get("name"))
        else:
            name = clean_text(item)
        if name:
            names.append(name)
    return ", ".join(_dedupe(names))


def _diet_name(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    last = re.split(r"[/:#]", text)[-1]
    if re.fullmatch(r"[A-Za-z]+Diet", last):
        words = re.findall(r"[A-Z][a-z]*|[a-z]+", last[: -len("Diet")])
        if words:
            phrase = " ".join(w.lower() for w in words)
            return phrase[0].upper() + phrase[1:]
    return text


def parse_recipe(node: dict, resolve=None) -> Recipe:
    """A Recipe from one schema.org Recipe object."""
    resolve = resolve or (lambda v: v)
    r = Recipe()
    r.name = clean_text(node.get("name") or node.get("headline"))
    r.description = clean_text(node.get("description"))
    r.author = _people(node.get("author"), resolve)
    r.yields = _text_list(node.get("recipeYield") if "recipeYield" in node else node.get("yield"))
    r.prep_time = clean_text(node.get("prepTime"))
    r.cook_time = clean_text(node.get("cookTime"))
    r.total_time = clean_text(node.get("totalTime"))
    r.categories = _text_list(node.get("recipeCategory"), split_commas=True)
    r.cuisines = _text_list(node.get("recipeCuisine"), split_commas=True)
    r.keywords = _text_list(node.get("keywords"), split_commas=True)
    r.diets = _dedupe([d for d in (_diet_name(x) for x in _as_list(node.get("suitableForDiet")))
                       if d])
    r.date_published = clean_text(node.get("datePublished"))
    ingredients = node.get("recipeIngredient")
    if ingredients is None:
        ingredients = node.get("ingredients")
    r.ingredients = _ingredients(ingredients)
    r.method = _instructions(node.get("recipeInstructions"), resolve)
    r.nutrition, r.serving_size = _nutrition(node.get("nutrition"), resolve)
    return r


# ---------------------------------------------------------------------------
# Microdata
# ---------------------------------------------------------------------------

_MICRODATA_LIST_PROPS = {"recipeIngredient", "ingredients", "recipeInstructions",
                         "recipeCategory", "recipeCuisine", "suitableForDiet",
                         "itemListElement", "author", "recipeYield"}


def _microdata_value(tag: Tag, prop: str):
    if tag.has_attr("itemscope"):
        return _microdata_item(tag)
    if tag.has_attr("content"):
        return tag["content"]
    if tag.name == "time" and tag.has_attr("datetime"):
        return tag["datetime"]
    if tag.name in ("a", "link") and tag.has_attr("href"):
        return tag["href"]
    if tag.name in ("data", "meter") and tag.has_attr("value"):
        return tag["value"]
    if prop == "recipeInstructions":
        return tag.decode_contents()
    return tag.get_text(" ")


def _microdata_item(scope: Tag) -> dict:
    item: dict = {}
    itemtype = scope.get("itemtype") or ""
    if isinstance(itemtype, list):
        itemtype = " ".join(itemtype)
    if itemtype:
        item["@type"] = [t.rstrip("/").rsplit("/", 1)[-1] for t in itemtype.split()]
    for tag in scope.find_all(attrs={"itemprop": True}):
        owner = tag.find_parent(attrs={"itemscope": True})
        if owner is not scope:
            continue
        props = tag["itemprop"]
        props = props.split() if isinstance(props, str) else props
        for prop in props:
            value = _microdata_value(tag, prop)
            if prop in _MICRODATA_LIST_PROPS:
                item.setdefault(prop, []).append(value)
            elif prop not in item:
                item[prop] = value
    return item


def _microdata_recipes(soup: BeautifulSoup) -> list[dict]:
    out = []
    pattern = re.compile(r"schema\.org/Recipe\b", re.IGNORECASE)
    for scope in soup.find_all(attrs={"itemscope": True, "itemtype": pattern}):
        if scope.find_parent(attrs={"itemscope": True, "itemtype": pattern}):
            continue
        out.append(_microdata_item(scope))
    return out


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _recipe_key(r: Recipe) -> tuple:
    return (r.name.lower(), tuple(i.lower() for i in r.ingredients))


def find_recipes(html: str) -> list[Recipe]:
    """Every schema.org Recipe on the page, in page order. Never raises."""
    try:
        return _find_recipes(html)
    except Exception as e:  # never stop a page converting
        log.debug("recipe extraction failed: %s: %s", type(e).__name__, e)
        return []


def _find_recipes(html: str) -> list[Recipe]:
    if not html or ("ld+json" not in html and "schema.org/Recipe" not in html
                    and "schema.org/recipe" not in html.lower()):
        return []
    soup = BeautifulSoup(html, "lxml")
    documents = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        text = script.string if script.string is not None else script.get_text()
        if not text or not text.strip():
            continue
        try:
            documents.append(loads_lenient(text))
        except (ValueError, RecursionError) as e:
            log.debug("skipping unreadable JSON-LD block: %s", e)
    ids: dict = {}
    for doc in documents:
        _index_ids(doc, ids)
    resolve = _Resolver(ids)
    nodes: list[dict] = []
    for doc in documents:
        _walk_recipes(doc, nodes)
    if not nodes:
        nodes = _microdata_recipes(soup)
    recipes: list[Recipe] = []
    seen = set()
    for node in nodes:
        try:
            recipe = parse_recipe(node, resolve)
        except Exception as e:
            log.debug("skipping unreadable Recipe object: %s: %s", type(e).__name__, e)
            continue
        if not (recipe.ingredients or recipe.method):
            continue
        key = _recipe_key(recipe)
        if key in seen:
            continue
        seen.add(key)
        recipes.append(recipe)
    return recipes


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def strip_recipe_text(html: str, recipes: list[Recipe]) -> str:
    """The page HTML without the list items, paragraphs and label headings
    that repeat the recipes' ingredients and steps, and without microdata
    recipe properties. Never raises; on error the HTML comes back unchanged."""
    try:
        return _strip_recipe_text(html, recipes)
    except Exception as e:
        log.debug("could not remove recipe text from the page: %s: %s", type(e).__name__, e)
        return html


def _matches(text: str, targets: set[str], long_targets: list[str]) -> bool:
    norm = _norm(text)
    if not norm:
        return False
    if norm in targets:
        return True
    return any(t in norm and len(norm) <= len(t) + 12 for t in long_targets)


def _strip_recipe_text(html: str, recipes: list[Recipe]) -> str:
    targets: set[str] = set()
    for r in recipes:
        targets.update(_norm(i) for i in r.ingredients)
        for title, steps in r.method:
            targets.update(_norm(s) for s in steps)
            if title:
                targets.add(_norm(title))
    targets.discard("")
    long_targets = [t for t in targets if len(t) >= 12]
    soup = BeautifulSoup(html, "lxml")
    # Content extractors read JSON-LD too, which would bring the steps back
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        script.decompose()
    body = soup.body or soup

    for tag in list(body.find_all(attrs={"itemprop": True})):
        if tag.decomposed:
            continue
        props = tag["itemprop"]
        props = props.split() if isinstance(props, str) else props
        if set(props) & {"recipeIngredient", "ingredients", "recipeInstructions", "nutrition"}:
            tag.decompose()

    for tag in list(body.find_all(["li", "p"])):
        if tag.decomposed:
            continue
        if tag.name == "p" and tag.find_parent("li") is not None:
            continue
        if _matches(tag.get_text(" "), targets, long_targets):
            tag.decompose()

    for lst in list(body.find_all(["ul", "ol"])):
        if not lst.decomposed and not lst.find("li"):
            lst.decompose()

    headings = ["h2", "h3", "h4", "h5", "h6"]
    for h in list(body.find_all(headings)):
        if h.decomposed:
            continue
        text = _norm(h.get_text(" "))
        if text not in _LABEL_HEADINGS and text not in targets:
            continue
        nxt = h.find_next_sibling()
        while nxt is not None and not nxt.get_text(strip=True) and nxt.name not in headings:
            nxt = nxt.find_next_sibling()
        if nxt is None or nxt.name in headings:
            h.decompose()
    return str(soup)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _escape_line(text: str) -> str:
    """Keep a paragraph from starting like a heading or a list item. Text
    inside list items and headings is written exactly as published."""
    return re.sub(r"^(#+|[-+*]|\d+[.)])(?=\s)", r"\\\1", text)


def recipe_markdown(recipe: Recipe, level: int = 2) -> str:
    """The structured block for one recipe, headings starting at `level`."""
    h = "#" * level
    sub = "#" * min(level + 1, 6)
    out: list[str] = [f"{h} Recipe details", ""]
    if recipe.description:
        out += [_escape_line(recipe.description), ""]
    details = []
    if recipe.name:
        details.append(("Name", recipe.name))
    if recipe.yields:
        details.append(("Servings", "; ".join(recipe.yields)))
    for label, raw in (("Prep time", recipe.prep_time), ("Cook time", recipe.cook_time),
                       ("Total time", recipe.total_time)):
        if raw:
            details.append((label, duration_line(raw)))
    if recipe.author:
        details.append(("Author", recipe.author))
    if recipe.categories:
        details.append(("Category", ", ".join(recipe.categories)))
    if recipe.cuisines:
        details.append(("Cuisine", ", ".join(recipe.cuisines)))
    if recipe.diets:
        details.append(("Diet", ", ".join(recipe.diets)))
    if recipe.keywords:
        details.append(("Keywords", ", ".join(recipe.keywords)))
    if recipe.date_published:
        details.append(("Published", recipe.date_published))
    if details:
        out += [f"- {label}: {value}" for label, value in details] + [""]

    out += [f"{h} Ingredients", ""]
    if recipe.ingredients:
        out += [f"- {i}" for i in recipe.ingredients]
    else:
        out.append("Not stated.")
    out.append("")

    out += [f"{h} Method", ""]
    if recipe.method:
        for title, steps in recipe.method:
            if title:
                out += [f"{sub} {title}", ""]
            out += [f"{n}. {s}" for n, s in enumerate(steps, start=1)]
            out.append("")
    else:
        out += ["Not stated.", ""]

    out += [f"{h} Nutrition", ""]
    if recipe.nutrition is None:
        out.append("Not stated.")
    else:
        size = recipe.serving_size or NOT_STATED
        out += [f"Serving size: {size}", ""]
        for key, label in NUTRITION_FIELDS:
            out.append(f"- {label}: {recipe.nutrition.get(key, NOT_STATED)}")
    out.append("")
    return "\n".join(out)


def recipes_markdown(recipes: list[Recipe]) -> str:
    """The structured block for all recipes on a page."""
    if len(recipes) == 1:
        return recipe_markdown(recipes[0], level=2)
    parts = []
    for n, recipe in enumerate(recipes, start=1):
        name = recipe.name or f"Recipe {n}"
        parts.append(f"## {name}\n\n" + recipe_markdown(recipe, level=3))
    return "\n".join(parts)


_RE_FENCE = re.compile(r"^\s*(```|~~~)")


def _demote_headings(md: str) -> str:
    """Every ATX heading one level deeper (to H6 at most), outside code fences."""
    out = []
    in_fence = False
    for line in md.split("\n"):
        if _RE_FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            m = re.match(r"^(#{1,6})(\s)", line)
            if m:
                level = min(len(m.group(1)) + 1, 6)
                line = "#" * level + line[len(m.group(1)):]
        out.append(line)
    return "\n".join(out)


def merge_recipe_markdown(md: str, recipes: list[Recipe]) -> str:
    """Title line, then the recipe block, then the remaining page text under
    "## Page text" (its headings moved one level down)."""
    md = (md or "").strip("\n")
    title_line = ""
    rest = md
    m = re.match(r"^# .*$", md, re.MULTILINE)
    if m and m.start() == 0:
        title_line = m.group(0)
        rest = md[m.end():]
    if not title_line:
        name = next((r.name for r in recipes if r.name), "") or "Recipe"
        title_line = f"# {name}"
    parts = [title_line, "", recipes_markdown(recipes).rstrip("\n")]
    rest = rest.strip("\n")
    if rest.strip():
        parts += ["", f"## {PAGE_TEXT_HEADING}", "", _demote_headings(rest)]
    return "\n".join(parts) + "\n"


def recipe_frontmatter(recipes: list[Recipe]) -> dict:
    """Frontmatter fields from the first recipe on the page."""
    r = recipes[0]
    out: dict = {"page_type": "recipe"}
    if r.categories:
        out["recipe_category"] = ", ".join(r.categories)
    if r.cuisines:
        out["recipe_cuisine"] = ", ".join(r.cuisines)
    if r.yields:
        out["servings"] = r.yields[0]
    if r.total_time:
        out["total_time"] = readable_duration(r.total_time)
    if r.keywords:
        out["keywords"] = list(r.keywords)
    return out
