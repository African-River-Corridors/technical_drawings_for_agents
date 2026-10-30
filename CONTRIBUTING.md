# Contributing

Thank you for helping. Issues, examples and pull requests are all welcome.

## Set up

```bash
git clone https://github.com/African-River-Corridors/drawing-format-standard
cd drawing-format-standard
uv venv && uv pip install -e '.[dev]'     # or: python -m venv .venv && pip install -e '.[dev]'
```

No system tool is needed. The PDF is written directly, with the font embedded.

## Run the tests

```bash
.venv/bin/python -m pytest
python3 tools/release_guard.py      # must print 0 hits
python3 tools/release_guard.py --history
```

The conformance suite (`docs/standard/conformance/`) and the golden files (`tests/goldens/`) are
**checked by hand**. If your change moves one, say why in the PR. Do not regenerate a golden or an expected
file to make a test pass: if an implementation and a case disagree, check the case first.

## Pull requests

1. Fork the repo (or branch, if you have write access) and make a focused change.
2. Add or update tests. Keep examples and fixtures **synthetic** — no client drawings or site data.
3. Open a PR against `main` and fill in the template. CI runs the tests, the release guard and gitleaks.
4. A code owner reviews. **Every pull request from outside the maintainers needs a code-owner
   approval before it merges** (see `.github/CODEOWNERS`).

Have working drawing code that does not fit the toolkit yet? Use the `contrib/` lane — see
`contrib/README.md`.

## Licence of contributions

This project is licensed under the Apache License 2.0. Under section 5 of that licence, any
contribution you intentionally submit is licensed under the same terms, unless you say otherwise.
There is **no DCO sign-off and no CLA** to sign.

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
