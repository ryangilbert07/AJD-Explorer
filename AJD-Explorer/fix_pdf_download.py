"""Add a browser-session PDF fetch before Chrome's download.save_as path."""

import ast
from pathlib import Path


path = Path(__file__).resolve().parent / "scrape_ajds.py"
if not path.exists():
    raise SystemExit("Place this file beside scrape_ajds.py in the AJD-Explorer folder.")

original = path.read_text(encoding="utf-8")
marker = "# Browser-session PDF fetch (save_as fallback)"
anchor = '    print(f"Downloading through Chrome: {pdf_url}")\n\n    page = context.new_page()\n'
replacement = '''    # Browser-session PDF fetch (save_as fallback)
    # The USACE server sometimes closes Chrome's PDF download page before
    # Playwright's download.save_as completes. Use the same context's cookies
    # to request the PDF directly, then keep Chrome's download as fallback.
    try:
        print(f"Requesting PDF through browser session: {pdf_url}")
        response = context.request.get(pdf_url, timeout=90000)
        content = response.body() if response.ok else b""
        if content.startswith(b"%PDF-") and len(content) > 1000:
            temporary_path = local_path + ".part"
            with open(temporary_path, "wb") as pdf_file:
                pdf_file.write(content)
            os.replace(temporary_path, local_path)
            print(f"Saved valid PDF: {local_path}")
            return local_path
        print(f"Browser-session PDF request returned HTTP {response.status}; trying Chrome download.")
    except Exception as error:
        print(f"Browser-session PDF request failed: {error}; trying Chrome download.")

    print(f"Downloading through Chrome: {pdf_url}")

    page = context.new_page()
'''

if marker in original:
    print("PDF download fallback is already installed.")
else:
    if original.count(anchor) != 1:
        raise SystemExit("Could not locate the Chrome download function. No changes made.")
    updated = original.replace(anchor, replacement, 1)
    ast.parse(updated, str(path))
    path.write_text(updated, encoding="utf-8")
    print("Added browser-session PDF download with Chrome fallback.")
