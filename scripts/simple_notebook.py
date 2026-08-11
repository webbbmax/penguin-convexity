"""Minimal standard-library notebook writer used when nbformat is unavailable."""

from __future__ import annotations

import json
from types import SimpleNamespace


def new_notebook():
    return {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


def new_markdown_cell(source=""):
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def new_code_cell(source=""):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def write(notebook, path):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(notebook, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


v4 = SimpleNamespace(
    new_notebook=new_notebook,
    new_markdown_cell=new_markdown_cell,
    new_code_cell=new_code_cell,
)
