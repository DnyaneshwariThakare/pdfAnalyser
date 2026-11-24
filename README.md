# PDF Analyser - Pune Housing Board applications

Parses the Pune Housing Board (MHADA) "Draft List of Accepted Applications" PDF and reports:

1. Total applications for a scheme you enter
2. All schemes with their total applications
3. Flats available per scheme and category (from `data/mhada_flats_aug2026.csv`) next to the application counts
4. Schemes where you have the best chance of winning, optionally filtered by category and budget

## Setup

```
pip install pandas pymupdf
```

## Usage

```
python pdf_analyser.py --pdf "path\to\list.pdf"          # summary + interactive scheme lookup
python pdf_analyser.py --scheme 936                      # one scheme, by scheme code
python pdf_analyser.py --best                            # highest chance of winning
python pdf_analyser.py --best --category GP --max-price 20   # by category, within budget (lakh Rs)
python pdf_analyser.py --refresh                         # re-extract from the PDF
```

The first run parses the PDF (a few minutes) and caches the rows in `output/applications.csv`; later runs are instant.
The `output/` folder is not in this repo because it holds applicant names from the PDF.
