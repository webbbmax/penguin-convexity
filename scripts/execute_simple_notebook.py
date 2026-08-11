#!/usr/bin/env python3
"""Execute code cells sequentially and persist outputs in a standard ipynb file."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import traceback
from pathlib import Path


def execute(path: Path, working_directory: Path) -> None:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    namespace = {"__name__": "__notebook__"}
    original_directory = Path.cwd()
    count = 0
    try:
        os.chdir(working_directory)
        for index, cell in enumerate(notebook.get("cells") or []):
            if cell.get("cell_type") != "code":
                continue
            count += 1
            output = io.StringIO()
            cell["execution_count"] = count
            cell["outputs"] = []
            try:
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    exec(compile(cell.get("source") or "", f"{path.name}:cell-{index}", "exec"), namespace)
            except Exception as error:
                text = output.getvalue() + traceback.format_exc()
                cell["outputs"] = [
                    {
                        "output_type": "error",
                        "ename": type(error).__name__,
                        "evalue": str(error),
                        "traceback": text.splitlines(),
                    }
                ]
                path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                raise
            text = output.getvalue()
            if text:
                cell["outputs"] = [{"output_type": "stream", "name": "stdout", "text": text}]
    finally:
        os.chdir(original_directory)
    path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args()
    execute(args.notebook.resolve(), args.cwd.resolve())
    print(args.notebook.resolve())


if __name__ == "__main__":
    main()
