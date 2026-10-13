"""The README's worked example must run as printed."""

import re
import runpy
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_worked_example_runs(tmp_path, monkeypatch):
    text = README.read_text(encoding="utf-8")
    section = text.split("## Worked example", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    script = tmp_path / "example.py"
    script.write_text(code, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runpy.run_path(str(script), run_name="__main__")
    svg = (tmp_path / "out" / "EXA-CIV-SEC-002.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and "6.00 m" in svg
