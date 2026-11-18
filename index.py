"""
Assignment 3: build the index (`main.py index`).

Corpus: the English product information (SmPC) of five centrally authorised
anticoagulant / antiplatelet medicines, published by the European Medicines
Agency (EMA). Reuse is allowed with acknowledgement of the EMA as the source
(https://www.ema.europa.eu/en/about-us/legal-notice).

Each EMA PDF holds several SmPCs back to back (one per presentation). Only the
FIRST SmPC of each PDF is kept: it is complete, and the others repeat it.

Stages (written as functions so server.py's add-document tool can import them):
    download_corpus()  -> PDFs in corpus/ (git-ignored)
    extract_sections(pdf_path, doc_name) -> list of cleaned sections with metadata
Chunking and embedding are added on top of these (see chunk_* / embed_* below).
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "corpus"

EMA_PI = "https://www.ema.europa.eu/en/documents/product-information/{slug}-epar-product-information_en.pdf"
ACCESSED = "2026-11"  # month the PDFs were downloaded (EMA asks for URL + month of access)

# name = the id used in chunk metadata ("source") and in eval/questions.csv
CORPUS = [
    {"name": "Eliquis", "slug": "eliquis", "substance": "apixaban"},
    {"name": "Xarelto", "slug": "xarelto", "substance": "rivaroxaban"},
    {"name": "Lixiana", "slug": "lixiana", "substance": "edoxaban"},
    {"name": "Pradaxa", "slug": "pradaxa", "substance": "dabigatran etexilate"},
    {"name": "Brilique", "slug": "brilique", "substance": "ticagrelor"},
]
for d in CORPUS:
    d["url"] = EMA_PI.format(slug=d["slug"])
    d["path"] = CORPUS_DIR / f"{d['slug']}.pdf"


# --------------------------------------------------------------------------- #
# 1. Download
# --------------------------------------------------------------------------- #
def download_file(url: str, dest: Path, retries: int = 3) -> None:
    """Download one file. EMA answers 429 when asked too fast: wait and retry."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        r = httpx.get(url, follow_redirects=True, timeout=120)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return
        if r.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"Could not download {url} after {retries} attempts")


def download_corpus() -> None:
    for d in CORPUS:
        if d["path"].exists():
            continue
        print(f"Downloading {d['name']} ...", file=sys.stderr)
        download_file(d["url"], d["path"])
        time.sleep(5)  # be polite with the EMA server


# --------------------------------------------------------------------------- #
# 2. Extract and clean
# --------------------------------------------------------------------------- #
# A section heading is a short paragraph such as "4.3 Contraindications" or
# "4. CLINICAL PARTICULARS".
HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s+([A-Z][A-Za-z0-9 ,/()\-]{2,90})$")
NAME_HEADING = re.compile(r"^1\.?\s+NAME OF THE MEDICINAL PRODUCT\s*$")
PAGE_NUMBER = re.compile(r"^\s*\d{1,3}\s*\n")  # pypdf puts the printed page number first


def _paragraphs(page_text: str) -> list[str]:
    """Split a page into paragraphs, rejoining lines broken by the PDF layout."""
    page_text = PAGE_NUMBER.sub("", page_text, count=1)
    out = []
    for block in re.split(r"\n\s*\n", page_text):
        text = re.sub(r"\s*\n\s*", " ", block).strip()
        text = re.sub(r"[ \t]{2,}", " ", text)
        if text:
            out.append(text)
    return out


def extract_sections(pdf_path: Path, doc_name: str) -> list[dict]:
    """Return the sections of the FIRST SmPC in an EMA product-information PDF.

    Each section: {source, section, title, page, text}. `page` is the PDF page
    where the section starts (1-based, as shown by any PDF viewer).
    """
    reader = PdfReader(str(pdf_path))
    sections: list[dict] = []
    current: dict | None = None
    started = False
    seen_name_heading = 0

    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not started:
            if "SUMMARY OF PRODUCT CHARACTERISTICS" not in text:
                continue
            started = True
        # End of the first SmPC: a second "1. NAME OF THE MEDICINAL PRODUCT"
        # or the start of Annex II.
        for para in _paragraphs(text):
            if NAME_HEADING.match(para):
                seen_name_heading += 1
                if seen_name_heading == 2:
                    return _finish(sections, current)
            if re.match(r"^ANNEX II\b", para):
                return _finish(sections, current)

            m = HEADING.match(para)
            if m and len(para) < 100:
                num, title = m.group(1), m.group(2).strip()
                # ignore numbered lists such as "1 tablet" that look like headings
                if "." in num or title.isupper() or title[0].isupper():
                    if current:
                        sections.append(current)
                    current = {
                        "source": doc_name,
                        "section": num,
                        "title": title,
                        "page": page_no,
                        "text": "",
                    }
                    continue
            if current is None:
                continue  # front matter before section 1
            current["text"] += ("\n\n" if current["text"] else "") + para
    return _finish(sections, current)


def _finish(sections: list[dict], current: dict | None) -> list[dict]:
    if current:
        sections.append(current)
    return [s for s in sections if len(s["text"].split()) >= 3]


def load_corpus() -> dict[str, list[dict]]:
    """Download if needed and extract every document of the corpus."""
    download_corpus()
    return {d["name"]: extract_sections(d["path"], d["name"]) for d in CORPUS}


def word_count(sections: list[dict]) -> int:
    return sum(len(s["text"].split()) for s in sections)


if __name__ == "__main__":
    # Chunking + embedding come next; for now report what extraction produces.
    corpus = load_corpus()
    total = 0
    for name, secs in corpus.items():
        n = word_count(secs)
        total += n
        print(f"{name:9s} {len(secs):3d} sections {n:6d} words")
    print(f"TOTAL     {total} words")
