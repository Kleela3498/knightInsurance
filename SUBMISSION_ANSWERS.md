# Submission_Template.txt: pre-filled technical answers

(Copy into the reply email; fill in the bracketed lines. Edit anything that is not accurate for you.)

Subject: AI Developer Project Submission - [YOUR FULL NAME]

Full name: [YOUR FULL NAME]
Email: [YOUR EMAIL]
Source code ZIP attached or repository URL: [attach knight-insurance-24h/ as ZIP, excluding .venv/]
Access instructions if the repository is private: [n/a, or invite details]
Demo video attachment or accessible URL (5-10 minutes): [attach / paste URL; script in demo_script.md]
README location: knight-insurance-24h/README.md
Setup and run commands:
  cd knight-insurance-24h
  python -m venv .venv && .venv/bin/pip install -r requirements.txt
  ollama pull llama3.2
  .venv/bin/python app.py        # Windows: .venv\Scripts\python app.py
  # open http://127.0.0.1:5057
LLM provider and model: llama3.2 (3B) served locally by Ollama through its OpenAI-compatible
  endpoint (http://localhost:11434/v1). Configurable via KNIGHT_LLM_BASE_URL / KNIGHT_LLM_MODEL /
  KNIGHT_LLM_API_KEY (environment only). No hard-coded or simulated answers.
AI coding tools used: GitHub Copilot (VS Code agent) [edit to list every tool you actually used].
Approximate time spent: [YOUR ACTUAL TIME]
Implemented features: Flask backend; blank-start upload form (nothing preloaded); SQLite history of past
  runs that can be reopened, renamed, deleted and restored; feedback loop (open field to see source -> fix ->
  rules re-run -> saved, with undo/redo and restore-original); saved draft email, print;
  8-field extraction with
  per-value evidence (source file, location, excerpt), excerpts checked against the source;
  missing values name the sources inspected; email-vs-PDF conflicts shown with both values and no
  source preferred; deterministic R1-R5 (R1 absence precedence, R2-R4 conflict before threshold,
  150-mile boundary); AI summary + editable draft broker email (draft only, no send path);
  human overrides with original evidence retained and rules re-run; independent packets;
  processing / success / failure states, with fallbacks when the model fails (label-matching
  extraction, template summary and four template emails).
Known limitations or incomplete requirements: Single-user local tool (Flask dev server; Ollama
  serializes model calls; concurrency hardening out of scope). A 3B model can misread fields;
  these surface as UNKNOWN/conflict/failure states and are correctable. A fix re-runs the rules,
    not the model. History is a local SQLite file. No OCR, auth or deployment.
Test command and test result summary: `.venv/bin/python -m pytest tests -q` -> 94 passed, no model
  needed. Covers missing information, source conflict, unequal vehicle counts, the 150 vs 150.1
  threshold, SUB-A/B/C truth tables (also with no model), CSV counting, loss-runs inventory,
  normalization, edge cases, the model path with a fake client (malformed JSON, missing fields,
  model down), label-matching extraction, email templates, and the
  upload / fix / history-reload / undo flow.
  Live check with llama3.2: 9/9 runs (3 per packet) matched the README truth table, 0 failures.

Los Angeles onsite work: [Already able to work onsite / Willing to relocate / Unable to work onsite]
Earliest realistic onsite start or relocation timeline: [YOUR ANSWER]

I confirm that I can explain the submitted implementation and identify the tools and assistance used.
