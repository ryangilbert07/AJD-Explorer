# AJD Explorer

AJD Explorer is an unofficial research dashboard for searching, comparing, and
reviewing publicly available U.S. Army Corps of Engineers Approved
Jurisdictional Determinations (AJDs).

## Run the dashboard

Install the dependencies and start Streamlit:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The question feature requires an OpenAI API key. Set `OPENAI_API_KEY` in the
environment (recommended for Render), or add it locally to
`.streamlit/secrets.toml`:

```toml
OPENAI_API_KEY = "your-key-here"
```

Do not commit API keys to the repository.

## Render settings

- Build command: `pip install -r requirements.txt`
- Start command: `streamlit run app.py --server.address 0.0.0.0 --server.port $PORT`
- Environment variable: `OPENAI_API_KEY`

The app searches the complete stored OCR text for every AJD in the current
filter set, ranks matching passages, and asks the model to answer with links and
evidence references.

## Refresh consultant attribution

Consultant extraction prioritizes Section 8 sources/supporting data, then cited
supporting reports, and finally map title blocks near the end of each PDF. The
refresh records the source, page when page markers are available, confidence,
and a short evidence phrase.

Test a few records without changing the database:

```bash
OPENAI_API_KEY="your-key-here" python reenrich_consultants.py --limit 5 --dry-run
```

Refresh only records that currently have no consultant:

```bash
OPENAI_API_KEY="your-key-here" python reenrich_consultants.py --only-missing
```

Refresh every stored AJD:

```bash
OPENAI_API_KEY="your-key-here" python reenrich_consultants.py
```

Existing OCR may not contain page markers. Newly scraped PDFs store
`=== PAGE N ===` markers, allowing the consultant page to be recorded when the
supporting evidence is explicit.
