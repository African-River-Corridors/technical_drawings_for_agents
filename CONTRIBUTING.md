# Contributing

Thank you for helping. Issues, examples and pull requests are all welcome.

## Set up

```bash
git clone https://github.com/African-River-Corridors/technical_drawings_for_agents
cd technical_drawings_for_agents
uv venv && uv pip install -e '.[dev,plot,verify]'     # or: python -m venv .venv && pip install -e '.[dev,plot,verify]'
```

System tool for the plot path: `rsvg-convert` (macOS `brew install librsvg`; Debian/Ubuntu
`apt-get install librsvg2-bin`).

## Run the tests

```bash
.venv/bin/python -m pytest
python3 tools/release_guard.py      # must print 0 hits
```

Many tests compare output **byte for byte** against files in `tests/goldens/`. If your change moves a
golden, say why in the PR; do not regenerate goldens to make a test pass.

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
