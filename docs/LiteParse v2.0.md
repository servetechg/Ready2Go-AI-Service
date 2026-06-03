# LiteParse v2.0 — Implementation Context File

> Purpose: a verified, agent-ready reference for implementing and using **LiteParse v2.0** (LlamaIndex's open-source, local, LLM-free document parser). Built from primary sources (official blog, GitHub repo, official docs). Every section flags what is VERIFIED vs NOT CONFIRMED so coding agents don't act on marketing claims.

---

## TL;DR
- **Both sources are LIVE and VERIFIED.** The blog post (`llamaindex.ai/blog/liteparse-v2-0-runs-everywhere`, published **May 27, 2026**, author **Logan Markewich**) and the repo (`github.com/run-llama/liteparse`, Apache-2.0) both exist and were inspected directly, along with the official docs at `developers.llamaindex.ai/liteparse`.
- **What it is:** LiteParse is a fast, **local, LLM-free** document parser that extracts spatially-aware text (with bounding boxes), page screenshots, and JSON/text output from PDFs and office docs. v2.0's headline change is a **full rewrite from TypeScript into a Rust core**, exposed as native packages for **Rust, Python, Node.js, and WASM (browser/edge)**.
- **Three claims from the LinkedIn post are inaccurate or imprecise and are corrected below:**
  1. The blog author of record is **Logan Markewich, not Jerry Liu**.
  2. "World's fastest PDF parser" is a marketing line. The real, blog-stated numbers are **up to 5–100× faster on small docs and ~3× on large docs vs LiteParse v1**, plus one absolute figure (0.777s for a 457-page / 100MB doc). There is **no published head-to-head benchmark vs PyMuPDF / PyPDF** in these sources.
  3. It is not just "Python and Node" — it targets **Rust, Python, Node, and WASM/edge**.
- **Critical caveat:** as of this research the **GitHub repo README and its latest GitHub Release (v1.0.0, Mar 19, 2026) still describe v1.0** (TypeScript + PDF.js + Tesseract.js; languages 70.8% TS / 27.8% Python). The **v2.0 Rust rewrite is documented in the blog and the docs site**, and the install commands resolve, but the repo's release tag had not yet been bumped to v2.0. Treat v2.0 API details as sourced from the **docs site**, and pin exact versions at install time.

---

## 1. Verification status of every source

| Source | URL | Status | Notes |
|---|---|---|---|
| Blog | `llamaindex.ai/blog/liteparse-v2-0-runs-everywhere` | LIVE / VERIFIED | "LiteParse v2.0 Runs Everywhere"; meta title "Up to 100x Fast Parsing with LiteParse v2.0 and Rust". Author **Logan Markewich**, **May 27, 2026**. Tags: LiteParse, Open Source, OCR. |
| Repo | `github.com/run-llama/liteparse` | LIVE / VERIFIED | "A fast, helpful, and open-source document parser." License **Apache-2.0**. 154 commits. Languages **TS 70.8% / Python 27.8% / Other 1.4%**. Stars 27 / forks 3 (small, new). **Latest Release v1.0.0 (Mar 19, 2026)** — tag still reflects v1. |
| Docs | `developers.llamaindex.ai/liteparse` | LIVE / VERIFIED | Authoritative for the v2.0 API. Has Getting Started, Library Usage (TS/Python/Rust/WASM), OCR, Multi-Format, Visual Citations, Parsing URLs, Agent Skill, Browser (WASM), Server Usage, CLI Reference, API Reference. |
| PyPI | `pypi.org/project/liteparse` | EXISTS, NOT directly fetchable | Bot challenge blocked the page. `pip install liteparse` confirmed by blog + docs; exact version string / dependency list NOT read. **Flagged.** |
| npm | `@llamaindex/liteparse`, `@llamaindex/liteparse-wasm` | Asserted by blog + docs | Existence stated officially; exact published versions NOT independently confirmed. **Flagged.** |
| crates.io | `liteparse` | Asserted by blog + docs | Docs show `liteparse = "2"` in Cargo. Exact published version NOT independently confirmed. **Flagged.** |

**"Jerry Liu released this" (from the post): NOT CONFIRMED.** Jerry Liu is LlamaIndex's co-founder/CEO, but the blog author of record is **Logan Markewich**. Repo contributors: `logan-markewich`, `AstraBert` (Clelia Bertelli), `TuanaCelik` (Tuana Çelik), `github-actions[bot]`.

---

## 2. What it is and what changed in v2.0

LiteParse is LlamaIndex's open-source, **local-first** document parser. It extracts structured text projected according to the document's layout, **without using LLMs or cloud calls**. v1.0 shipped as a Node/TypeScript package (with a thin Python wrapper around the CLI), which created latency and distribution problems (hard Node dependency, no clean binary).

**v2.0 rewrites the entire core in Rust** so changes propagate to every language binding and the project gains Rust's performance and safety. It now ships as **native Rust, Python, Node, and WASM packages**, runnable on the server, in the browser, and on edge runtimes. The Rust core uses:
- **a custom fork/build of PDFium** for PDF parsing, and
- **`tesseract-rs`** as the default built-in OCR engine.

> **Version/architecture mismatch to remember:** the **repo README still documents the v1 architecture** ("Spatial text parsing using PDF.js", Tesseract.js OCR). The blog/docs describe the v2 architecture (PDFium + tesseract-rs, Rust core). Where they conflict, the **blog + docs reflect v2.0**.

---

## 3. Relationship to LlamaParse (do not confuse them)

| | **LiteParse** (this doc) | **LlamaParse** |
|---|---|---|
| Type | Open-source (Apache-2.0), **local** | **Cloud** service (`cloud.llamaindex.ai`) |
| Cost / auth | Free, **no API key** | Paid, API key / credits |
| LLM use | **LLM-free** | LLM-powered |
| Best for | Clean, straightforward docs (simple layouts, no dense tables/scans) | Complex docs: dense tables, multi-column, charts, handwriting, scanned PDFs |
| Positioning | "fast and light" local extraction | Production-grade RAG accuracy |

The LiteParse README explicitly recommends switching to LlamaParse when you hit the limits of local parsing.

---

## 4. Performance — what "fastest" actually means (VERIFIED, with caveats)

Concrete, attributable claims from the blog:
- vs **LiteParse v1**: small docs **5–100× faster** (v1 runtime was dominated by spinning up a Node process); large docs **~3× faster**.
- One absolute figure: **0.777 s to parse a 457-page, 100 MB document**.
- Qualitative: "blazing fast on most documents," attributed to the custom PDFium fork + `tesseract-rs`.

**NOT CONFIRMED:** No published head-to-head benchmark table vs PyMuPDF / PyPDF / pdfplumber was found. The LinkedIn post's "significantly outperforms PyMuPDF and PyPDF" and "world's fastest PDF parser" are **marketing claims without an accompanying public benchmark dataset/methodology in these sources**. Benchmark on your own documents before capacity planning.

---

## 5. Supported input formats (VERIFIED — note the "50+" nuance)

LiteParse parses PDFs natively and **auto-converts** other formats to PDF first, via external system tools.

**Office docs (via LibreOffice):**
- Word: `.doc`, `.docx`, `.docm`, `.odt`, `.rtf`
- PowerPoint: `.ppt`, `.pptx`, `.pptm`, `.odp`
- Spreadsheets: `.xls`, `.xlsx`, `.xlsm`, `.ods`, `.csv`, `.tsv`

**Images (via ImageMagick, parsed with OCR):**
- `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.webp`, `.svg`

Conversions require the relevant system dependency installed (LibreOffice / ImageMagick).

> The **"50+ document types"** claim from the post is **PARTIALLY VERIFIED** — plausible but not enumerated as a literal list of 50 in these sources. The explicitly documented set is the ~25+ extensions above.

---

## 6. Output formats & data model (VERIFIED)

- **Output formats:** `text` (default) and `json`.
- **JSON** includes per-page **text items with bounding boxes** (`x, y, width, height` in PDF points), enabling visual citations.
- **Screenshots:** page images (PNG/JPG) for feeding LLM agents visual content text can't capture.

**Result object (per docs):**
- `result.text` — full, layout-preserving text.
- `result.pages[]` — per page: page identifier + text items.
  - Python: `page.page_num`, `page.text_items`
  - TS / WASM: `page.pageNum`, `page.textItems`
  - Rust: `page.page_number`, `page.text_items`
  - Each text item carries `text` + bbox (`x, y, width, height`).
- **Screenshot objects:** `page_num`, `width`, `height`, image bytes — Python `s.image_bytes`, TS `shot.imageBuffer`, Rust `shot.image_bytes`.

> Field-name casing differs across bindings (`page_num` vs `page_number` vs `pageNum`; `image_bytes` vs `imageBuffer`). Mirror the exact docs example for your target language.

---

## 7. OCR system (VERIFIED)

- **Built-in default:** Tesseract — `tesseract-rs` in the Rust/native builds; **Tesseract.js** in v1/JS. Zero-setup. Language(s) via `--ocr-language` / `ocr_language` (e.g. `fra`).
- **HTTP OCR servers:** plug in any server (EasyOCR, PaddleOCR, custom) implementing the LiteParse OCR API:
  - `POST /ocr`, accepts `file` + `language`
  - returns `{ results: [{ text, bbox: [x1,y1,x2,y2], confidence }] }`
  - Example wrappers ship under `ocr/easyocr/` and `ocr/paddleocr/`. Full spec: `OCR_API_SPEC.md`.
- **Rust:** implement the `OcrEngine` trait, attach via `.with_ocr_engine(Arc::new(my_engine))`. Cargo feature `tesseract` (default on); disable with `default-features = false` if you only use HTTP OCR or none.
- **WASM/browser:** native Tesseract and HTTP OCR are unavailable; pass a JS-side `ocrEngine` object with `async recognize(imageData, width, height, language)` returning `[{ text, bbox, confidence }]`.
- **Air-gapped:** `TESSDATA_PREFIX` env var (or `tessdata_path` / `tessdataPath` option) points to local `.traineddata` files.

---

## 8. Installation (VERIFIED from blog + docs)

```bash
# Node library + CLI
npm i @llamaindex/liteparse
npm i -g @llamaindex/liteparse        # global install gives the `lit` command

# Python library + CLI
pip install liteparse

# Rust library + CLI
cargo install liteparse

# WASM (browser/edge) — no `lit` CLI
npm i @llamaindex/liteparse-wasm
```

Homebrew (macOS/Linux) for the CLI:
```bash
brew tap run-llama/liteparse
brew install llamaindex-liteparse
```

System deps for non-PDF formats:
```bash
# Office docs
brew install --cask libreoffice        # macOS
apt-get install libreoffice            # Ubuntu/Debian
choco install libreoffice-fresh        # Windows

# Images
brew install imagemagick               # macOS
apt-get install imagemagick            # Ubuntu/Debian
choco install imagemagick.app          # Windows
```

Platforms: **Linux, macOS (Intel/ARM), Windows**.

---

## 9. CLI reference (VERIFIED — binary is `lit`)

Commands: `lit parse <file>`, `lit batch-parse <input-dir> <output-dir>`, `lit screenshot <file>`.

**`lit parse` options:**
| Option | Default | Meaning |
|---|---|---|
| `-o, --output <file>` | — | Output file path |
| `--format json\|text` | `text` | Output format |
| `--ocr-server-url <url>` | — | HTTP OCR server (else Tesseract) |
| `--no-ocr` | — | Disable OCR |
| `--ocr-language <lang>` | `en` | OCR language(s) |
| `--num-workers <n>` | CPU cores − 1 | Pages OCR'd in parallel |
| `--max-pages <n>` | `10000` | Max pages to parse |
| `--target-pages <pages>` | — | e.g. `"1-5,10,15-20"` |
| `--dpi <dpi>` | `150` | Render DPI |
| `--no-precise-bbox` | — | Disable precise bounding boxes |
| `--preserve-small-text` | — | Keep very small text |
| `--config <file>` | — | JSON config file |
| `-q, --quiet` | — | Suppress progress output |

**`lit batch-parse`** adds `--recursive` and `--extension ".pdf"` (and shares the parse options).

**`lit screenshot`** options: `-o/--output-dir` (default `./screenshots`), `--target-pages`, `--dpi` (default 150), `--format png|jpg` (default png), `--config`, `-q`.

Examples:
```bash
lit parse document.pdf
lit parse document.pdf --format json -o output.json --target-pages "1-5,10"
lit parse document.pdf --no-ocr
lit batch-parse ./input-dir ./output-dir --recursive --extension ".pdf"
lit screenshot document.pdf --target-pages "1,3,5" --dpi 300 -o ./screenshots
```

---

## 10. Library API by language (VERIFIED from docs)

### Python
```python
from liteparse import LiteParse

parser = LiteParse(
    ocr_enabled=True,
    ocr_server_url="http://localhost:8828/ocr",   # optional
    ocr_language="fra",
    dpi=300,
    target_pages="1-5",
    password="secret",                            # for encrypted PDFs
)

result = parser.parse("document.pdf")             # or pass raw bytes
print(result.text)
for page in result.pages:
    print(f"Page {page.page_num}: {len(page.text_items)} text items")

# From bytes (e.g. web upload)
with open("document.pdf", "rb") as f:
    result = parser.parse(f.read())

# Screenshots
screenshots = parser.screenshot("document.pdf", page_numbers=[1, 2, 3])
for s in screenshots:
    print(s.page_num, s.width, s.height)          # s.image_bytes holds PNG data
```

### TypeScript / Node
```ts
import { LiteParse } from "@llamaindex/liteparse";

const parser = new LiteParse({
  ocrEnabled: true,
  ocrServerUrl: "http://localhost:8828/ocr",
  ocrLanguage: "fra",
  dpi: 300,
  outputFormat: "json",
  targetPages: "1-10",
  password: "secret",
});

const result = await parser.parse("document.pdf");  // also accepts Buffer / Uint8Array
console.log(result.text);
for (const page of result.pages) {
  for (const item of page.textItems) {
    console.log(item.x, item.y, item.width, item.height, item.text);
  }
}

// From bytes
import { readFile } from "fs/promises";
const result2 = await parser.parse(await readFile("document.pdf"));

// Screenshots
const shots = parser.screenshot("document.pdf", [1, 2, 3]); // shot.imageBuffer = PNG bytes
```

### Rust (`liteparse = "2"`, async via tokio)
```toml
[dependencies]
liteparse = "2"
tokio = { version = "1", features = ["rt-multi-thread", "macros"] }
```
```rust
use liteparse::{LiteParse, LiteParseConfig, OutputFormat};
use liteparse::types::PdfInput;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = LiteParseConfig {
        ocr_enabled: true,
        ocr_language: "fra".to_string(),
        dpi: 300.0,
        target_pages: Some("1-10".to_string()),
        output_format: OutputFormat::Json,
        password: Some("secret".to_string()),
        ..Default::default()
    };
    let parser = LiteParse::new(config);

    let result = parser.parse("document.pdf").await?;                  // path
    for page in &result.pages {
        println!("Page {}: {} items", page.page_number, page.text_items.len());
    }

    // From bytes
    let bytes = std::fs::read("document.pdf")?;
    let result = parser.parse_input(PdfInput::Bytes(bytes)).await?;

    // Screenshots
    let shots = parser.screenshot("document.pdf", Some(vec![1,2,3])).await?;
    Ok(())
}
```
Custom OCR engine: implement the `OcrEngine` trait, then
`LiteParse::new(Default::default()).with_ocr_engine(Arc::new(my_engine))`.

### Browser (WASM) — camelCase config; must `await init()`; input must be `Uint8Array`
```js
import init, { LiteParse } from "@llamaindex/liteparse-wasm";

await init();                                      // required once before use

const parser = new LiteParse({
  ocrEnabled: true,
  ocrLanguage: "eng",
  dpi: 300,
  outputFormat: "json",
  targetPages: "1-10",
  maxPages: 100,
  password: "secret",
  // browser OCR must be provided as a JS callback:
  ocrEngine: {
    async recognize(imageData, width, height, language) {
      // call tesseract.js in a worker or a remote OCR service
      return [{ text: "Hello", bbox: [10, 20, 80, 40], confidence: 0.98 }];
    },
  },
});

const bytes = new Uint8Array(await file.arrayBuffer());  // no file paths in browser
const result = await parser.parse(bytes);
console.log(result.text, result.pages[0]);
```

---

## 11. Configuration cheat-sheet (VERIFIED)

Constructor/config keys (Python snake_case / TS-WASM camelCase / Rust struct fields):

| Concept | Python / Rust | TS / WASM | Notes |
|---|---|---|---|
| Enable OCR | `ocr_enabled` | `ocrEnabled` | |
| HTTP OCR URL | `ocr_server_url` | `ocrServerUrl` | not in WASM |
| OCR language | `ocr_language` | `ocrLanguage` | default `en` |
| Render DPI | `dpi` | `dpi` | default 150 (Rust uses `f64`, e.g. `300.0`) |
| Page selection | `target_pages` | `targetPages` | e.g. `"1-5,10,15-20"` |
| Max pages | `max_pages` | `maxPages` | default 10000 |
| Output format | `output_format` | `outputFormat` | `text` \| `json` |
| Precise bbox | `precise_bounding_box` | `preciseBoundingBox` | |
| Keep tiny text | `preserve_very_small_text` | `preserveVerySmallText` | |
| PDF password | `password` | `password` | encrypted PDFs |
| Tesseract data | `tessdata_path` | `tessdataPath` | also `TESSDATA_PREFIX` env |
| Quiet | (CLI/flag) | `quiet` | |

JSON config file (`liteparse.config.json`) accepted via `--config`, mirrors these keys:
```json
{
  "ocrLanguage": "en",
  "ocrEnabled": true,
  "maxPages": 1000,
  "dpi": 150,
  "outputFormat": "json",
  "preciseBoundingBox": true,
  "preserveVerySmallText": false
}
```
For HTTP OCR, add `"ocrServerUrl": "http://localhost:8828/ocr"`.

---

## 12. Agent / framework integration (VERIFIED)

- **Coding-agent skill:**
  ```bash
  npx skills add run-llama/llamaparse-agent-skills --skill liteparse
  # or copy SKILL.md from run-llama/llamaparse-agent-skills/skills/liteparse/
  ```
  Works with Claude Code, Codex, OpenCode, etc.
- **Pi extension:** `pi install npm:@llamaindex/liteparse-pi-extension@latest`
- **Repo ships `AGENTS.md` and `CLAUDE.md`** with development guidance for coding agents.
- **Server mode / MCP:** docs include "Server Usage"; the LlamaIndex docs site exposes an MCP server. A separate blog "LiteParse Server: Self-Hostable Document Parsing" (May 12, 2026) exists.
- **LlamaIndex framework:** LiteParse is the local OSS parser in the LlamaIndex ecosystem; its output (text / pages / bboxes) maps cleanly to LlamaIndex `Document`/node objects. A dedicated reader-class wiring was NOT enumerated in these sources. **Flagged.**

---

## 13. Repo structure & tech stack (VERIFIED)

**Top-level repo layout:** `src/` (core), `cli/`, `packages/python/`, `ocr/` (easyocr, paddleocr examples), `docs/`, `dataset_eval_utils/`, `scripts/`, `.changeset/`, plus `README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `OCR_API_SPEC.md`, `CHANGELOG.md`, `package.json`, `tsconfig.json`, `vitest.config.ts`, `LICENSE` (Apache-2.0).

> A dedicated Rust `Cargo.toml` / crate subtree was not visible in the rendered repo file list at research time — likely because the v2 source tree had not yet been published to `main`. Confirm in-repo when the v2 tree lands. **Flagged.**

**Tech stack:**
- **v2 (blog):** custom fork/build of **PDFium** + **`tesseract-rs`** (default OCR), Rust core, WASM target.
- **v1 (repo README credits):** **PDF.js** (parsing), **Tesseract.js** (in-process OCR), **EasyOCR / PaddleOCR** (optional HTTP OCR), **Sharp** (image processing).

**License:** Apache-2.0.

---

## 14. Recommendations for your project

1. **Pin versions at install time and verify the binding actually publishes v2.** Because the repo's release tag still reads v1.0.0 while the blog/docs describe v2.0, check the resolved version after install before building against v2-only behavior (Rust core, WASM). Prefer the **docs site** as the API source of truth.
2. **Use LiteParse only for clean, well-structured docs.** For dense tables, multi-column layouts, charts, handwriting, or scanned PDFs, the maintainers recommend the cloud **LlamaParse**. Don't expect enterprise-grade extraction accuracy locally.
3. **For RAG ingestion, request `json` output** to capture bounding boxes (visual citations) and per-page structure; generate **screenshots** for pages where layout/figures matter to a vision-capable LLM.
4. **Plan OCR explicitly per environment:** built-in Tesseract for server/native; an HTTP OCR server (EasyOCR/PaddleOCR) for higher accuracy; a JS `ocrEngine` callback for browser/WASM (no built-in OCR there). Set `TESSDATA_PREFIX` for air-gapped deployments.
5. **Tune throughput** with `--num-workers` (defaults to CPU cores − 1); bound cost with `--max-pages` / `--target-pages`; `--dpi` (default 150) trades OCR/screenshot quality for speed.
6. **Don't rely on the "fastest / beats PyMuPDF & PyPDF" claim** for capacity planning — no public benchmark methodology backs it in these sources. Benchmark on your documents.

---

## 15. Caveats (read before acting)

- **Repo vs blog version mismatch:** README + latest GitHub Release describe **v1.0** (TypeScript / PDF.js / Tesseract.js); the **v2.0 Rust rewrite** is documented in the **blog and docs site**. API specifics here come from the docs site.
- **PyPI page was not directly readable** (bot challenge); exact published Python version and dependency pins could not be captured. npm / crates.io version strings likewise not independently confirmed.
- **"50+ document types"** is not enumerated as a literal 50-item list in these sources; ~25+ extensions are explicitly documented (PARTIALLY VERIFIED).
- **Comparative performance vs PyMuPDF/PyPDF and "world's fastest"** are marketing claims without a published benchmark in these sources (NOT CONFIRMED). Verified numbers are relative-to-v1 speedups plus one absolute figure (0.777s / 457-page doc).
- **Authorship:** the LinkedIn post attributes the release to Jerry Liu; the blog author of record is **Logan Markewich** (Jerry Liu is LlamaIndex co-founder/CEO but not the stated author).
- **Field-name casing differs across bindings** (`page_num` vs `page_number` vs `pageNum`; `image_bytes` vs `imageBuffer`) — mirror the exact docs example for your target language.

---

## 16. Source links
- Blog: `https://www.llamaindex.ai/blog/liteparse-v2-0-runs-everywhere`
- Repo: `https://github.com/run-llama/liteparse`
- Docs (Getting Started): `https://developers.llamaindex.ai/liteparse/getting_started/`
- Docs (Library Usage): `https://developers.llamaindex.ai/liteparse/guides/library-usage/`
- PyPI: `https://pypi.org/project/liteparse/`
- npm: `https://www.npmjs.com/package/@llamaindex/liteparse` · `https://www.npmjs.com/package/@llamaindex/liteparse-wasm`
- crates.io: `https://crates.io/crates/liteparse`
- Browser WASM demo: `https://run-llama.github.io/liteparse/`
- OCR API spec: `https://github.com/run-llama/liteparse/blob/main/OCR_API_SPEC.md`
