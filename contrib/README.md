# contrib — handing us drawing code you already have

If you have written drawing code of your own — a script that generates a sheet, a layout or
placement helper, a DXF cleaner, a plotting wrapper — this is where it lands, together with the
example drawings that prove it works.

The point of the lane is that **we read your code before we merge it**. Nothing here is on
`PATH`, nothing here runs in a pipeline, and nothing here affects a drawing we issue. It is a
reviewed inbox, so you can hand over an unfinished thing without breaking anything.

You do not need to make it match our toolkit first. Working code and a filled-in manifest are
enough; the shaping-into-`technical_drawings_for_agents` conversation happens on the PR.

## Where to put it

```
contrib/incoming/<your-name>/
  manifest.yaml          # copy ../../manifest.template.yaml and fill it in
  code/                  # your scripts, as they are
  drawings/              # example drawings your code produces or consumes
  notes.md               # optional — anything you want to say in prose
```

One directory per person. Add files, never edit someone else's.

## Doing it with Claude Code

From a clone of this repo (or your fork):

```bash
git checkout -b contrib/<your-name>-drawings
```

Then point Claude Code at this file and ask it to do the handover. A prompt that works:

> Read `contrib/README.md`. Copy my drawing code from `<path>` into
> `contrib/incoming/<my-name>/code/`, copy the example drawings into
> `drawings/`, fill in `manifest.yaml` from the template by reading the code, check the
> "what not to send" list, then commit and open a PR.

Let it write the manifest by reading the code — it is more accurate than either of us
remembering, and it is the part we most need.

```bash
gh pr create --fill --base main
```

CI runs on the PR. It will not test your code (nothing here is imported), so a red build means
you touched something outside `contrib/` — usually not what you intended.

## What not to send

- **Credentials of any kind** — keys, tokens, connection strings, a `.env`. If one has ever been
  in a file you are sending, treat it as leaked and rotate it, whatever the file looks like now.
- **Big binaries.** Anything over ~10 MB, and any raster: orthophotos, DEMs, point clouds, hillshades.
  These belong in your own data store, which is their source of truth. Reference the path; do not
  copy the file. A repo that carries a 2.9 GB ortho is a repo nobody can clone.
- **DWG originals from a supplier or a client.** Say which file you mean and where it lives. The
  drawing stays where its provenance is legible.
- **A whole project drawing set.** This directory wants *examples* — the smallest drawings that
  exercise your code. Project drawing sets live in their project's own repo.

If something you need to send does not fit these limits, say so in `notes.md` and we will find it
a home. Do not work around the limit quietly.

## Two rules we hold ourselves to

They are the whole reason this toolkit exists, so they apply to contributed code too. Neither is a
style preference.

**Never let a drawing assert something it has not derived.** A sheet that says `1:1250` in the
title block and plots at 1:1157 is not a typo — it is a claim nothing checked. That exact defect
survived several people looking at it, and a reader scaling off the bar would have measured 54 m
where the drawing said 50. So scale, scale bar and north arrow are computed from the geometry, and
the build fails when a stated scale disagrees with what is drawn.

**If a check gets in your way, tell us — do not switch it off.** A check that cries wolf is worse
than no check, because it teaches everyone to ignore the log. If one is wrong we would rather fix
it. Open an issue, or say it on the PR.

## What happens next

We read it, and reply with which parts should become real `technical_drawings_for_agents` modules, which stay as
examples, and which we should leave alone. Merging the PR only means *received and read* — it does
not put anything into production.

Contributed code stays under `contrib/` until it has tests and an interface we both agree on.
That boundary is what lets us take your work early instead of waiting for it to be finished.
