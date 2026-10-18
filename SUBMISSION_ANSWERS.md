# Submission answers

Subject: AI Developer Project Submission - Sai Leela Kuragayala

Full name: Sai Leela Kuragayala
Email: saileelakuragayala@gmail.com
Source code ZIP attached or repository URL: https://github.com/Kleela3498/knightInsurance
Access instructions if the repository is private: N/A, public repository
Demo video attachment or accessible URL (5-10 minutes): [add your video link or attachment; script in demo_script.md]
README location: README.md (repository root)
Setup and run commands:
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
  ollama pull llama3.2
  .venv\Scripts\python app.py                         # macOS/Linux: .venv/bin/python app.py
  # open http://127.0.0.1:5057
LLM provider and model: Ollama, Meta Llama 3.2 (3B, `llama3.2`), served locally through Ollama's
  OpenAI-compatible endpoint (http://localhost:11434/v1). Configurable via KNIGHT_LLM_BASE_URL /
  KNIGHT_LLM_MODEL / KNIGHT_LLM_API_KEY / KNIGHT_LLM_TIMEOUT (environment only). No hard-coded or
  simulated answers.
AI coding tools used: GitHub Copilot (VS Code agent).
Approximate time spent: 4 hours
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
  extraction, template summary and four template emails); reviews queued and processed one at a time,
  with Queued / Processing / Failed shown in the sidebar; Edge-Case Lab (16 fault scenarios, no live
  model needed).
Known limitations or incomplete requirements: Single-user local tool (Flask dev server). Reviews are
  queued and run one at a time because Ollama serves one model; on a CPU-only machine a review takes
  a minute or more. A 3B model can misread fields; these surface as UNKNOWN/conflict/failure states and
  are correctable. A fix re-runs the rules and rebuilds the summary and email from templates; it does
  not re-run the model. History is a local SQLite file. No OCR, auth or deployment.
Test command and test result summary: `python -m pytest tests -q` -> 96 passed, no model
  needed. Covers missing information, source conflict, unequal vehicle counts, the 150 vs 150.1
  threshold, SUB-A/B/C truth tables (also with no model), CSV counting, loss-runs inventory,
  normalization, edge cases, the model path with a fake client (malformed JSON, missing fields,
  model down), label-matching extraction, email templates, the review queue, and the
  upload / fix / history-reload / undo flow. The real model is checked by hand, not by pytest.

Los Angeles onsite work: [Already able to work onsite / Willing to relocate / Unable to work onsite]
Earliest realistic onsite start or relocation timeline: [YOUR ANSWER]

I confirm that I can explain the submitted implementation and identify the tools and assistance used.
