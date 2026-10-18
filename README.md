# Knight Insurance: AI Solutions Developer Exercise

A small local web app for reviewing a commercial-auto submission packet. The screen starts
blank: the underwriter uploads a packet (application PDF, broker email, vehicle CSV, loss
runs) and sees:

1. the 8 business fields, each with its source evidence (file, location, excerpt);
2. the 5 rules (R1-R5) as PASS / FLAG / UNKNOWN, each with a reason;
3. an AI summary and an editable **draft** broker email (never sent: there is no send path);
4. a feedback loop (below);
5. a **Past reviews** list (SQLite) to reopen any earlier run, including its fixes.

Every upload is a fresh, independent run. Nothing is preloaded. Reviews are queued and processed
one at a time (the local model handles one request at a time); each shows "Queued" or "Processing" in
the Past reviews list until it is done, and you can open other reviews meanwhile. To try the samples, upload
the files from `sample_packets/SUB-A|B|C` (attach `loss_runs.pdf` under "Loss runs").

## Feedback loop (conflicting evidence)

1. A field where email and PDF disagree shows **both values** and is marked CONFLICT.
2. **Open a field** (or click one of its sources) and the Email and Application PDF tabs highlight where it came from, whole-word and next to the row label, so a "No" in a note is not marked.
3. **Fix** lets the underwriter type a value or pick the email's or the PDF's value.
4. The rules re-run instantly and the summary and draft are rebuilt from a template (no model call; use the email feedback buttons to have the AI rewrite the draft). A banner shows what changed
   (e.g. "R3 UNKNOWN -> FLAG"). The field is tagged "Fixed by you"; the original value and
   evidence stay visible, and **Undo my fix** reverts it.
5. The fix is saved with the run and survives a restart.

A fix re-applies the rules to the corrected value; it does not re-run model extraction.

## Everyday buttons

| Button | What it does |
|---|---|
| ↶ Undo / ↷ Redo | In "Your fixes" above the fields list. Step back or forward through your fixes (also Ctrl+Z / Ctrl+Y). Each step re-runs the checks. Up to 50 steps, saved with the run. |
| ⟲ Restore original values | Same bar. Drops every fix and returns to what the AI found. It can itself be undone. |
| Save draft / Use new AI draft | Save your edits to the broker email, or go back to the AI draft. If a fix changes the checks after you saved, you are told the draft may be out of date. |
| Email feedback: Shorter / Friendlier / More formal / Add a deadline / your own words | **Email feedback loop.** Tell the AI what to change and it rewrites the draft. Your requests are logged under the draft. If the model is down you get a clear message and the draft is left unchanged. |
| ↶ Previous version | Steps the draft back one version (works after your own edits, restores and AI rewrites). |
| ✎ Rename | Change the label of a review. |
| Template picker (under the draft) | Replace the draft with a ready-made email: Standard (everything open), Missing items only, Clarifications only, or Acknowledgement. Built from the check results with the real values, no AI, so it works when the model is down. "Previous version" undoes it. |
| 🖨 Print | Prints a plain, table-based sheet: logo, status, summary, checks table, fields table (value, status, source quote) and the draft email. Everything else is hidden. The URL, date and page numbers at the top and bottom of the page are Chrome's own; untick "Headers and footers" in the print dialog to remove them. |
| 🗑 Delete | Moves a review to "Recently deleted" with an Undo toast. From there: **Restore**, or **Delete forever** (asks for a second click; removes the row and the uploaded files). |
| Search | Filters past reviews by label or business name. |

## Run it

```bash
cd knight-insurance-24h
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
ollama pull llama3.2                              # a local model served by Ollama
.venv/bin/python app.py                           # Windows: .venv\Scripts\python app.py
# open http://127.0.0.1:5057
```

The app talks to any OpenAI-compatible endpoint. Defaults are for Ollama; override with
environment variables (see `.env.example`): `KNIGHT_LLM_BASE_URL`
(`http://localhost:11434/v1`), `KNIGHT_LLM_MODEL` (`llama3.2`), `KNIGHT_LLM_API_KEY`
(optional, env only, never in source), `PORT` (`5057`), `KNIGHT_DB` (history database,
default `data/knight.db`; uploads are kept under `data/uploads/`), `KNIGHT_DEV_MODE` (`0` hides
the Edge-Case Lab, off by default). The header chip shows the
live model, or "Model unreachable".

## Tests

```bash
.venv/bin/python -m pytest tests -q      # 94 tests, no model or network needed
```

Covers the three required cases (missing information, source conflict, unequal vehicle
counts) and the 150 vs 150.1 mile threshold, plus the SUB-A/B/C truth tables, R1 absence
precedence, conflict-before-threshold, CSV counting, loss-runs inventory, normalization,
the model path using a fake client (malformed JSON, missing fields, model down, evidence
grounding), and the app flow: upload, conflict, fix, R3 UNKNOWN -> FLAG,
reload from history, undo, bad input, nothing preloaded; plus undo/redo/restore-original,
draft save/restore, email feedback rewrite and previous version, rename, and delete/restore/delete-forever.

The tests are **not only the three samples**. Beyond the SUB-A/B/C truth tables they cover edge
cases: the 150 / 150.1 boundary, harmless formatting differences that must not count as conflicts,
absence-vs-conflict precedence, zero vehicles, an unreadable CSV or loss document, invalid dates /
radii / counts, "no mention" of hazmat not being "No", a model that returns malformed JSON, drops
fields, invents an excerpt or is down, label-matching extraction (odd casing and spacing, a label
with no value, an empty or scanned PDF), each email template, and the template endpoint
rejecting an unknown kind. The real model is checked by hand (see "Model used"), not by pytest.

## Edge-Case Lab

A **🧪 Edge-Case Lab** button is in the header (set `KNIGHT_DEV_MODE=0` to hide it). **Run all checks** pushes the sample packets through the
real pipeline while a fault is injected. The page opens with a plain-English "what is this / how to read it",
then a one-line verdict ("The app handled every problem safely", 16 of 16), then 16 scenarios grouped by theme.
Click any row to see **what was broken, why it matters, what the app should do, what it actually did**
and the five check results (actual vs expected, with the reason for each), plus the **summary and draft email**
the underwriter would still receive (labelled AI-written or Template):

- model down (SUB-A, SUB-B, SUB-C), model replies with plain text, JSON with missing fields,
  JSON wrapped in a code fence, invented evidence quotes, invented values, only the email call failing
- scanned PDF with no text, vehicle list not uploaded, unreadable loss-runs file, a cover letter that says
  "loss runs to follow"
- an email that tells the AI to "approve everything" while the model obeys in its summary
- a radius of 150.1 miles (just over the limit) and a radius written as a range ("150 to 200")

**No live model is called** (the brief asks to test the rule logic without live model calls): the faults are
simulated by a stand-in model built from the documents themselves, so the lab and its tests run offline.
"Slow" is modelled as a timeout, which the app treats like "down". Nothing is saved to history, and the
button and `/api/dev/lab` do not exist when `KNIGHT_DEV_MODE=0`.
The same scenarios run in `pytest` (`tests/test_lab.py`).

## Where the AI is used (and where it is not)

| Step | Uses the LLM? |
|---|---|
| Reading text out of the PDF, email and CSV | No (pypdf, plain parsing) |
| Finding the 7 text field values and quoting the source line | **Yes** (reads email and PDF text, returns JSON). If the model is down: label matching, no AI |
| Checking the quote really exists in the source, normalising values, spotting conflicts | No. A value the model reports that is not in its source text is **ignored** (treated as missing, with a note), never trusted |
| Vehicle count from the CSV, loss-runs attached or not | No. Loss runs pass only if a readable file's own title says "loss runs / loss history"; a promise or a mere mention is not enough |
| Rules R1-R5 and the overall status | **No**, plain code in `rules.py` |
| Summary and draft broker email | **Yes**. If the model is down: a template built from the rule results, labelled "Template (AI was unavailable)" |
| Email feedback ("make it shorter") | **Yes** (if the model is down, the draft is left alone and a template can be picked) |

So the pass/flag/unknown decision never depends on the model; a wrong model answer shows up as a
visible value with its evidence that the reviewer can fix.

## If the AI is not available

Each step has a fallback, so the reviewer always has something to work with:

1. **Field extraction** falls back to label matching (`label_extract` in `extract.py`): it reads
   `Label: value` lines in the email and `Label` / `value` rows in the PDF. A banner says the values
   were read without AI and must be checked. Anything it cannot find shows as Missing.
2. **Rules** are unaffected (they never used the model).
3. **Summary** falls back to a template (`template_summary`): status line, one sentence per open
   check, and the "not a coverage decision" line.
4. **Draft email** falls back to a template (`template_draft`) with the real values, e.g. "the email
   says 180 miles but the application says 80 miles". Four kinds: standard, missing only,
   clarifications only, acknowledgement. The reviewer can also pick one at any time.
5. The summary and the email fall back independently: if only the second model call fails, you keep the
   AI summary and get the template email.

Limit: label matching only understands the layouts of the sample documents (labelled lines and rows).
A free-form email ("we run about 180 miles") is the AI's job; without it that field shows as Missing.

## How it is built

**Backend: Flask** (synchronous; fits blocking calls to a local model) with a SQLite file
(stdlib `sqlite3`) for run history.

```
app.py                Flask routes: /, /api/llm-status, GET/POST /api/runs, GET|DELETE /api/runs/<id>,
                      /api/runs/<id>/{pdf,override,undo,redo,reset,draft,draft/revise,draft/previous,draft/template,rename,restore},
                      /api/dev, POST /api/dev/lab
knight/lab.py         Edge-Case Lab: fault scenarios + scripted stand-in model.
knight/store.py       SQLite history: one row per run (JSON result + sources), uploads on disk.
knight/llm.py         One OpenAI-compatible client (stdlib urllib) + prompts. Packet text is
                      sent as DATA; the system prompt says to ignore embedded instructions.
knight/extract.py     PDF text (pypdf), CSV row count, loss-runs inventory, normalization,
                      two-source merge with CONFLICT handling, evidence grounding.
                      The model only supplies per-source raw values and excerpts; label_extract
                      is the no-AI fallback with the same output shape.
knight/rules.py       R1-R5 exactly per rules.json. Plain code; no model involved.
knight/narratives.py  Summary + draft email (and rewrites) from the rule results; templates
                      when the model fails or the reviewer asks for one.
knight/models.py      FieldValue / RuleResult / PacketResult.
static/index.html     Single-page UI (no build step).
```

Design choices that map to the brief:

- **Rules are deterministic.** The model never decides a rule; it only reads documents.
- **Evidence is checked, not trusted.** A model excerpt is kept only if it appears in the
  source and contains the extracted value; otherwise the real source line is used. Missing
  values list the sources inspected.
- **Conflicts are never silently resolved.** Email and PDF disagreement gives CONFLICT with
  both values shown; rules treat it as UNKNOWN before any threshold test.
- **Failures are visible.** Malformed JSON or a missing field from the model, or an
  unreachable model, shows an "AI extraction failed" banner. The fields are then read by label
  matching (no AI), the rules still run, and the summary/email become templates labelled
  "Template (AI was unavailable)". See "If the AI is not available".
- **No hard-coded answers.** There is no demo or fixture mode; every extracted value comes
  from the model reading the documents.

## Expected results (README truth table)

| | SUB-A | SUB-B | SUB-C |
|---|---|---|---|
| R1 | PASS | FLAG (date absent) | PASS |
| R2 | PASS (3=3) | PASS (2=2) | FLAG (declared 4 vs schedule 3) |
| R3 | PASS (150 <= 150) | UNKNOWN (missing) | UNKNOWN (180 vs 80 conflict) |
| R4 | PASS (No) | UNKNOWN (missing) | UNKNOWN (Yes vs No conflict) |
| R5 | PASS (report attached) | FLAG (promise is not an attachment) | FLAG (not attached) |
| Overall | READY_FOR_REVIEW | NEEDS_REVIEW | NEEDS_REVIEW |

Fix example on SUB-C: fix radius to `180` and R3 becomes FLAG; fix hazmat to `No` and R4
becomes PASS. The CONFLICT, both alternatives and the evidence stay visible.

## Model used and results

**`llama3.2` (3B) served locally by Ollama.** Last run, through the real model and the final
code: SUB-A, SUB-B and SUB-C each 3 times (9 runs), all 9 matched the table above, with
**0 extraction failures**. Model-down check (endpoint pointed at a dead
port): SUB-B was read by label matching and still gave R1 FLAG, R2 PASS, R3/R4 UNKNOWN, R5 FLAG,
with the "read without AI" banner and a template summary and email.

Notes from testing: `llama3.2` wraps its JSON in a ```` ```json ```` fence (the parser strips
it); it sometimes picks a loose excerpt (now corrected by the grounding step above); a 3B
model can drop fields, and the extractor rejects such replies instead of guessing. Ollama's
JSON mode made this worse, so it is not used.

## AI tools used

GitHub Copilot (VS Code agent) was used to fix the real-model path, redesign the UI, add the
logo, add evidence grounding, remove demo mode and write tests. The original base
implementation came from an earlier AI-assisted draft. All code was run and the tests and
live runs above were executed. *(Edit this line so it lists exactly the tools you used.)*

## Limitations

- **Single-user, local tool.** Flask's dev server is threaded and each request has its own
  run id with no shared applicant state, but Ollama serves one model, so simultaneous
  extractions queue behind each other. Concurrency hardening is out of scope.
- History is a local SQLite file (`data/`); it has no auth, no multi-user separation and no
  deployment. A fix re-runs the rules, not the model. `data/` and `.venv` should be left
  out of a submission ZIP.
- A 3B model is the weak point: occasional wrong or missing field values are possible. They
  surface as UNKNOWN/conflict/failure states, and the reviewer can correct any field. A value the
  model reports that is not in the document text is ignored, so it cannot drive a rule.
- Date, state, count and Yes/No values the model rewrites (2026-11-01, CA, 3) are matched back to
  common spellings in the text (November 1, 2026 / 11/1/2026, California, three). An unusual
  spelling is ignored and shows as Missing, which the reviewer can fix.
- Custom uploads need a text-based PDF (no OCR) and a UTF-8 email.
- Timeout is 60 s per model call; a slow machine may need a smaller model or a longer timeout.

## Time spent

*(Fill in your actual time.)*
