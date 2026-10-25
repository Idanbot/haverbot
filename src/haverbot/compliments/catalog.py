"""Strict, all-or-nothing JSONL validation and the bundled Hebrew catalog."""

import json
import re
from importlib.resources import files
from pathlib import Path
from string import Formatter

from haverbot.compliments.domain import Category, Compliment, Gender


def load_catalog(text: str) -> list[Compliment]:
    records = []
    ids: set[int] = set()
    texts: set[tuple[Gender, str]] = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if (
                not isinstance(data, dict)
                or not {"id", "gender", "text"} <= set(data)
                or set(data) - {"id", "gender", "text", "category"}
            ):
                raise ValueError("expected id, gender, text and optional category")
            identity, gender, template = data["id"], Gender(data["gender"]), data["text"]
            category = Category(data.get("category", Category.GENERAL))
            if type(identity) is not int or identity <= 0 or identity in ids:
                raise ValueError("invalid or duplicate id")
            if not isinstance(template, str) or not 15 <= len(template) <= 600:
                raise ValueError("invalid text length")
            if any(char in template for char in "\n\r\t"):
                raise ValueError("compliments must be single lines")
            fields = [
                (field, spec, conversion)
                for _, field, spec, conversion in Formatter().parse(template)
                if field is not None
            ]
            if fields != [("name", "", None)]:
                raise ValueError("exactly one plain {name} placeholder is required")
            plain = template.replace("{name}", "")
            if not re.search(r"[א-ת]", plain) or re.search(
                r"[^א-ת\u0591-\u05c7\s.,!?;:׳״'\"־–—()\-]", plain
            ):
                raise ValueError("compliment text must be Hebrew only")
            if (gender, template) in texts:
                raise ValueError("duplicate compliment text")
            ids.add(identity)
            texts.add((gender, template))
            records.append(Compliment(identity, gender, template, category))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"Invalid compliment JSONL line {number}: {exc}") from None
    if not records:
        raise ValueError("Compliment catalog is empty")
    return records


def bundled_catalog() -> list[Compliment]:
    resources = files("haverbot.compliments").joinpath("resources")
    return load_catalog(
        "\n".join(
            resources.joinpath(name).read_text("utf-8")
            for name in (
                "female.he.jsonl",
                "male.he.jsonl",
                "female.romantic.he.jsonl",
                "male.romantic.he.jsonl",
            )
        )
    )


def catalog_file(path: Path) -> list[Compliment]:
    return load_catalog(path.read_text(encoding="utf-8"))
