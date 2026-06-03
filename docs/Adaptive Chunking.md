# Adaptive Chunking (Ekimetrics) — Implementation Context File

## TL;DR
- **Both primary sources are LIVE and VERIFIED.** The arXiv paper `2603.25333` ("Adaptive Chunking: Optimizing Chunking-Method Selection for RAG," LREC 2026, submitted 26 Mar 2026) and the GitHub repo `github.com/ekimetrics/adaptive-chunking` (MIT-licensed core, package `adaptive-chunking` v0.1.0, Python ≥3.11) both exist and were fully inspected.
- **Two details from the social-media post are inaccurate and corrected here:** the repo is a brand-new "initial release" whose star count was climbing rapidly during this research (snapshots showed 4 → 9 → 67, and the repo Actions page showed **133 stars / 12 forks** by end of research) — so the "~106 stars" figure was not a fixed/established number; and the five-metric and four-chunker descriptions differ slightly from the post (correct names and the fact that the paper benchmarks **8** methods total are given below).
- **The method:** at the ingestion step, multiple chunkers run per document, each output is scored by 5 label-free intrinsic metrics, and the strategy with the highest **unweighted mean** of the 5 metrics is selected per document. No ground-truth QA or labeled data is needed. Reported RAG gains: Retrieval Completeness 67.7 vs 58.1 (LangChain recursive baseline), Answer Correctness 78.0 vs 70.1, and 65/99 vs 49/99 queries answered (>30% more questions resolved).

## Verification Status of Provided Links
- **`https://arxiv.org/abs/2603.25333` — LIVE / VERIFIED.** Title: "Adaptive Chunking: Optimizing Chunking-Method Selection for RAG." Authors: Paulo Roberto de Moura Júnior, Jean Lelong, Annabelle Blangero (Ekimetrics, France). Submitted Thu, 26 Mar 2026 11:20:52 UTC (v1, 108 KB), category cs.CL. Contact: jean.lelong@ekimetrics.com, annabelle.blangero@ekimetrics.com. (The arXiv PDF text layer did not extract cleanly through the fetch tool, but the abstract, author list, table captions, and appendix text were confirmed across the arXiv abstract page, the HuggingFace papers page, and search snippets.)
- **`https://github.com/ekimetrics/adaptive-chunking` — LIVE / VERIFIED.** MIT license (core). "Accepted at LREC 2026." Repo id 1192490705, owner org `ekimetrics`. Contributors: `Avo-k` (Jean Lelong) and a `claude` bot account. "Initial release" with a small number of commits. 100% Python. Topics: nlp, information-retrieval, chunking, rag, llm, text-splitting.
- **Star count claim (~106): NOT CONFIRMED as stated.** Different page snapshots during this research showed 4, 9, and 67 stars, and the repository Actions page showed **"Star 133 · Fork 12"** by the end of research; the HuggingFace "GitHub" badge variously read 15 and 124. Conclusion: the repo is new and stars are volatile/climbing — there was no stable "~106" figure.
- Also LIVE: **HuggingFace paper page** `huggingface.co/papers/2603.25333` — "arxiv:2603.25333 · Published on Mar 26 · Ekimetrics · Upvote · 4" (4 upvotes at research time); and the **LightOn blog post** "Adaptive Chunking: Reasoning Starts Before the LLM Sees a Token" (lighton.ai), which states "Chunking is selected at ingestion time based on document structure… Agents resolve queries in one pass instead of three."
- **PyPI: NOT FOUND.** No evidence the package is published to PyPI. Installation is from source/git only (`pip install -e .`); `pip install adaptive-chunking` is NOT confirmed to work.

## Key Findings

### What it is
No single chunking method is best for every document in a RAG pipeline. Adaptive Chunking evaluates multiple chunking strategies against five intrinsic, document-level quality metrics — requiring no labels, no QA pairs, and no LLM-as-judge for the *selection* step — and automatically selects the best strategy **per document** at ingestion time. Both the set of competing chunkers and the set of metrics are modular and user-extensible.

### Authors & venue
Paulo Roberto de Moura Júnior, Jean Lelong, Annabelle Blangero — Ekimetrics (France). Accepted at the 15th Language Resources and Evaluation Conference (LREC 2026), confirmed by the repo BibTeX: `@inproceedings{demoura2026adaptive … booktitle={Proceedings of the 15th Language Resources and Evaluation Conference (LREC 2026)}, year={2026}}`.

### Package facts (from `pyproject.toml` — VERIFIED)
- Distribution name `adaptive-chunking`; import package `adaptive_chunking`; version `0.1.0`; license MIT; `requires-python = ">=3.11"` (classifiers list 3.11 and 3.12); Development Status: 3 - Alpha; build backend hatchling; wheel package `src/adaptive_chunking`.
- **Core dependencies:** `tiktoken>=0.9.0`, `pandas>=2.2.3`, `numpy`, `tqdm>=4.67.1`, `python-dotenv>=1.1.0`, `sentence-transformers>=3.1`, `spacy>=3.8.4`, `scikit-learn`, `scipy`, `langdetect`.
- **Optional extras:**
  - `[coref]` → `maverick-coref` (CC BY-NC-SA 4.0 — non-commercial).
  - `[parsing]` → `docling`, `pymupdf4llm` (AGPL-3.0 / Artifex commercial), `azure-ai-documentintelligence`, `markdownify`.
  - `[paper]` → `[parsing,coref]` + `torch==2.6.0`, `torchvision==0.21.0`, `langchain>=0.3.21`, `langchain-experimental`, `stanza>=1.10.1`, `nltk`, `haystack-ai`, `openai`, `deepeval`, `groq>=0.21.0`, `pydantic`, `matplotlib`, `seaborn`, `ipywidgets`, `markdown`, `tabulate`, `ipykernel>=6.29.5`.
  - `[dev]` → `[paper]` + `pytest>=8.0`, `pytest-asyncio`.
- Torch/torchvision are pinned to the CUDA 12.4 index (`download.pytorch.org/whl/cu124`) via uv sources.

## Details

### The 5 Intrinsic Metrics (VERIFIED from `metrics.py` source + paper)
All five return a score in [0,1] (reported as %). Paper abbreviations: **RC, ICC, DCC, BI, SC.** Important naming note: the README's "What it measures" table labels "Filtered Missing Reference Error" as RC, but in the paper **RC = References Completeness**, implemented as `1 −` the filtered missing-reference error.

1. **Size Compliance (SC)** — `compute_size_compliance(chunks, max_tokens=1100, min_tokens=100, count_tokens_func=count_tokens)`. No embeddings/LLM. Formula: `1 - out_of_span/len(chunks)`, where a chunk is out-of-span if its token count `> max_tokens` or `< min_tokens`. Default bounds **100–1100 tokens** (Joe Sack's analysis of the paper describes this as "a target range of roughly 75 to 825 words").

2. **Intrachunk Cohesion (ICC)** — `compute_intrachunk_cohesion(chunks, full_text, split_points, model, chunk_embeddings=None, batch_size=16, progress_bar=False)`. Requires a SentenceTransformer-style embedder. Reconstructs sentences per chunk from `split_points` (character offsets), embeds each sentence and the whole chunk (normalized), and scores each chunk as the **mean cosine similarity** between its sentences and the chunk embedding. Chunks with <2 sentences are skipped. Returns the mean over chunks, clipped to [0,1]. (README's simplified `compute_intrachunk_cohesion(chunks, embedder)` is illustrative; the real signature requires `full_text` and `split_points`.)

3. **Document Contextual Coherence (DCC)** — `compute_contextual_coherence(chunks, full_text, model, window_context_tokens=3000, count_tokens_func=count_tokens, window_step=1, batch_size=16, chunk_embeddings=None, progress_bar=False)`. Builds sliding context windows of ≤3000 tokens (non-overlapping token sum, slides `window_step` chunks at a time), embeds each window, and scores each chunk as the cosine similarity between its embedding and its containing window's embedding. Returns mean, clipped [0,1]. Embedding-based.

4. **Block Integrity (BI)** — `compute_block_integrity(chunks, doc_split_points, full_text, tolerance_chars=5)`. No embeddings. Uses the parser's gold block boundaries (`doc_split_points`). A gold block counts as "intact" if no predicted chunk boundary falls strictly inside it (with 5-character tolerance at both edges). Returns `intact_blocks/total_blocks`.

5. **References Completeness (RC)** — `compute_filtered_missing_ref_error(full_text, chunks, entity_pron_pairs)`. Returns `missing_references/total_references` (an error rate; the paper reports completeness = `1 − error`). A reference is "missing" if any chunk boundary falls strictly between an entity mention and its pronoun (each entity–pronoun pair counted at most once). Entity–pronoun pairs come from coreference resolution via the `CoreferenceSolver` class (default model `sapienzanlp/maverick-mes-ontonotes`, tokenizer `microsoft/deberta-large`) plus spaCy POS filtering in `extract_entity_pronoun_pairs` (uses `en_core_web_sm` and a built-in `PERSONAL_PRONOUNS` set). This is the most expensive metric and requires the `[coref]` extra (non-commercial license).

**Additional metrics also present in `metrics.py`** (not part of the headline five): `compute_missing_ref_error` (concatenation-based variant), `compute_semantic_dissimilarity` (sliding-window cosine dissimilarity with a small-chunk penalty), `compute_lexical_dissimilarity` (TF-IDF version), and `compute_normalized_intrachunk_sim`.

### Aggregation / selection scheme
The per-document winner is the strategy with the **highest unweighted arithmetic mean of the 5 metrics** (argmax over methods). This is confirmed by reverse-engineering the README's Table 3 "Mean" column: the Adaptive row (99.0, 68.2, 88.8, 99.4, 99.9) averages to **91.07**; LLM-regex (98.0, 70.9, 82.4, 98.1, 99.6) → 89.80; every row reproduces as a simple five-metric average. Orchestration entry points (from LLM.md): `split_documents.split_documents_from_dir()` (chunking across a directory) and `compute_metrics.compute_metrics_per_origin()` (scoring). (The literal selection-function source was not directly fetchable; the unweighted-mean formula is inferred from the documented Table 3 values and the LLM.md pipeline description — flagged as such.)

### Chunking strategies
**4 DEFAULT competing strategies** (README): **Recursive (s=1100)**, **Recursive (s=600)**, **Page** (split on page breaks with size-enforcing post-processing), and **LLM-Regex** (an LLM generates a document-specific delimiter regex). The default-method set was deliberately revised before release (repo commit "update default chunking methods in README and SVG diagrams"). The post's vague "fourth chunker" = the **LLM-Regex** splitter.

**The paper benchmarks 8 methods total** (per LLM.md pipeline): page, sentence, LangChain-recursive (default), LangChain-recursive (1100), our recursive (1100), our recursive (600), semantic, and LLM-regex.

Two newly introduced chunkers from the paper:
- **Split-then-merge recursive splitter** — `RecursiveSplitter` in `splitters.py`. Verbatim signature:
  ```python
  RecursiveSplitter(
      chunk_size=1000,
      chunk_overlap=0,
      length_function=count_tokens,
      attach_separator_to="start",        # "start" | "end"
      is_separator_regex=False,
      separators=["\n\n", "\n", " ", ""],
      merging="to_chunk_size",            # "to_chunk_size" | "small_only"
      max_tokens_strategy="chunk_size_plus_overlap",  # | "chunk_size"
      min_chunk_tokens=100,
      merging_order="forward",            # "forward" | "backward"
  )
  ```
  Main method `split_text(text) -> List[str]`. Two merge modes: `to_chunk_size` (merge small splits up to ~chunk_size, adding backtracked overlap) and `small_only` (only merge chunks below `min_chunk_tokens` with a neighbor). Recursively splits on the separator priority list; the empty-string separator triggers a binary-search hard split. Module-level helpers: `group_chunks(blocks, tokenizer_func, max_tokens, chunk_block_overlap=0, verbose=False)`, `group_pages(doc_pages, pages_per_group=2, overlap_lines=10)`, `combine_blocks(blocks, max_tokens, count_tokens_func)`, `regex_splitter(text, regex_pattern, attach_to="start", min_len=10)`.
- **LLM-Regex splitter** — `LLMRegexSplitter(base_prompt, async_client_completion_func, count_tokens_func=count_tokens, context_tokens=8000)` in `paper/splitters.py`. **Model-agnostic**: the LLM is injected via `async_client_completion_func` (caller supplies the model; the paper's default uses an OpenAI client and requires `OPENAI_API_KEY`). It builds `prompt = base_prompt + "<Input>" + document_context + "</Input>"`, parses a `<regex>…</regex>` block (`r"<regex>\s*([\s\S]+?)\s*</regex>"`, case-insensitive), validates/repairs the regex, then applies `regex_splitter(text, pattern, attach_to="start")`; on failure returns `[text]`.
- Other paper chunkers in `paper/splitters.py`: `LongContextSemanticSplitter(sentence_splitter=None, threshold="max_tokens", max_context_tokens=8000, max_chunk_tokens=1200, quantile_value=0.90, sentence_overlap=5, model_name="Qwen/Qwen3-Embedding-0.6B", device="cpu", batch_size=2, visualize_splitting=False)`; `SentenceSplitter(method="nltk", sentences_per_chunk=1, device="cpu")`; plus LangChain recursive baselines.

### Post-processing (VERIFIED from paper)
Two-stage pipeline applied to the `*` methods (our recursive 1100/600, page, LLM-regex): (1) **oversized re-split** — only chunks > 1100 tokens; (2) **tiny-chunk merge** — only chunks < 100 tokens, with a maximum merged size of 1150 tokens. Final SC is sometimes < 100% because avoiding tiny chunks is prioritized over strict adherence to the upper bound. Largest gains: LLM-regex raw SC 58.3% → 99.6% (mean 80.0% → 89.8%); semantic raw SC 48.1% → 99.9% (mean 76.5% → 88.9%). The `†` methods (sentence, semantic, page raw, LangChain recursive) are scored on raw chunks for comparison.

### PDF parsing backends (VERIFIED)
`adaptive_chunking.parsing` defines a `BaseParser` ABC plus four concrete parsers:
- **`DoclingParser`** — default, open-source (Docling).
- **`PyMuPDFParser`** — lightweight (uses `pymupdf4llm`, AGPL-licensed).
- **`AzureDIParser`** — cloud (Azure Document Intelligence); credentials via `.env`: `ADI_ENDPOINT`, `ADI_KEY`.
- **`ExcelParser`** — Excel support.

Custom parser: subclass `BaseParser` and implement `parse_docs_in_dir()` and `convert_raw_results_to_markdown()` (per LLM.md). (Exact `__init__` signatures for the parser classes could not be fetched — `parsing.py` source was not retrievable — flagged.)

### Public API & quickstart (VERIFIED from README)
```python
from adaptive_chunking import chunk_files

# Parse PDFs and chunk in one step (requires pip install -e ".[parsing]")
chunks = chunk_files("path/to/pdfs/", chunk_size=600, chunk_overlap=50)
# Each chunk is a dict: doc_name, chunk_index, chunk_text, chunk_pages, titles_context, chunk_len
for chunk in chunks:
    print(chunk["doc_name"], chunk["chunk_index"], chunk["chunk_len"])

# Single file also works:
chunks = chunk_files("path/to/report.pdf")

# Choose a different parser:
from adaptive_chunking.parsing import PyMuPDFParser
chunks = chunk_files("path/to/pdfs/", parser=PyMuPDFParser())
```
Using the splitter and metrics directly:
```python
from adaptive_chunking.splitters import RecursiveSplitter
splitter = RecursiveSplitter(chunk_size=600, chunk_overlap=50,
                             separators=["\n\n", "\n", " ", ""],
                             merging="small_only", min_chunk_tokens=100)
chunks = splitter.split_text(document_text)

from adaptive_chunking.metrics import (compute_size_compliance,
                                       compute_intrachunk_cohesion,
                                       compute_block_integrity)
score = compute_size_compliance(chunks, min_tokens=100, max_tokens=1100)
```
**Returned chunk dict keys (VERIFIED):** `doc_name`, `chunk_index`, `chunk_text`, `chunk_pages`, `titles_context`, `chunk_len`. **Parsed-document JSON format (from LLM.md):** `{"document_name": str, "pages": {page_num: markdown}, "full_text": str, "split_points": [int], "titles": [{title, start, end, level}]}`. Token counting uses tiktoken `o200k_base`. Confirmed `chunk_files` args: a path (directory or single file) as first positional, plus `chunk_size`, `chunk_overlap`, and `parser=` (default `DoclingParser`). (The full literal `chunk_files` signature with all defaults was not fetchable — `__init__.py`/`split_documents.py` source was not retrievable — flagged.)

### Module layout (VERIFIED from README + LLM.md)
- `adaptive_chunking.splitters` — `RecursiveSplitter`, `group_chunks`, `combine_blocks`, `regex_splitter`, `group_pages`.
- `adaptive_chunking.metrics` — the five metrics + extras (see above).
- `adaptive_chunking.parsing` — `BaseParser`, `DoclingParser`, `PyMuPDFParser`, `AzureDIParser`, `ExcelParser`.
- `adaptive_chunking.postprocessing` — gap detection/repair, page/title metadata, chunk location (`find_chunks_start_and_end`).
- `adaptive_chunking.compute_metrics` — orchestrates metric computation; entry `compute_metrics_per_origin()`.
- `adaptive_chunking.split_documents` — orchestrates chunking across a directory; entry `split_documents_from_dir()`.
- `adaptive_chunking.extract_mentions` — coreference; entry `find_mentions_per_origin()`.
- `adaptive_chunking.chunking_utils` — `count_tokens` (tiktoken).
- `adaptive_chunking.jina_embedder` — Jina REST drop-in for SentenceTransformer (auto-used when `JINA_API_KEY` set).
- `adaptive_chunking.paper.*` — `replicate` (CLI), `splitters`, `rag_utils`, `rag_eval`, `analysis`, `visualization`.

Repo top-level also ships `data/clair/` (33 parsed JSON docs + pre-computed mentions), `docs/` (architecture.svg), `tests/`, `LLM.md` (LLM-coding-assistant context), `REPLICATE_GUIDELINES.md`, `NOTICE`, `SBOM.md`, `CONTRIBUTING.md`.

### LLM / embedding dependencies (VERIFIED)
- **ICC & DCC embeddings:** `jinaai/jina-embeddings-v3` locally, or the Jina REST API if `JINA_API_KEY` is set (~30 min vs ~9 hours on an RTX 4090).
- **Semantic chunker:** `Qwen/Qwen3-Embedding-0.6B` (needs GPU + flash-attention).
- **Coreference (RC):** `sapienzanlp/maverick-mes-ontonotes` + `microsoft/deberta-large` tokenizer (via `[coref]`).
- **LLM-Regex chunker + RAG eval:** OpenAI (`OPENAI_API_KEY`). RAG-eval LLM judge: **OpenAI GPT-4.1, temperature 0.**
- `GROQ_API_KEY` is also referenced (LLM.md) for RAG eval/coreference.
- **Environment variables:** `ADI_ENDPOINT`, `ADI_KEY` (AzureDIParser), `OPENAI_API_KEY` (LLM-regex + RAG eval), `JINA_API_KEY` (optional, speeds up metrics). Set via a `.env` file.

### Experimental setup & results (VERIFIED from paper/README)
- **Corpus:** the **CLAIR corpus** — 33 documents, ~1.18M tokens, 3 domains. **Domain-naming discrepancy to flag:** the paper abstract says "legal, technical, and **social science**," whereas the README's features list says "technical, legal, and **sustainability reporting**." Treat the third domain as social-science / sustainability-reporting (unresolved in available text).
- **Retrieval:** hybrid search pipeline (Figure 2). RAG eval judged by OpenAI GPT-4.1 (temp 0). Retrieval Completeness is computed over all queries; Answer Correctness skips queries the model declined to answer (insufficient context).
- **Table 5 (RAG):** Retrieval Completeness — Adaptive **67.7** vs LangChain recursive 58.1 vs Page splitting 59.1 (Wilcoxon p < 0.05). Answer Correctness — Adaptive **78.0** vs 70.1 vs 73.3. Answered queries — Adaptive **65/99** vs 49/99 vs 49/99. The abstract summarizes this as "raising answers correctness to 72% (from 62-64%) and increasing the number of successfully answered questions by over 30% (65 vs. 49)."
- **Table 3 (intrinsic, mean % across all domains; Wilcoxon p < 0.001 vs every individual method):** Adaptive 99.0/68.2/88.8/99.4/99.9 → mean **91.07**; LLM-regex (GPT) 98.0/70.9/82.4/98.1/99.6 → 89.80; LangChain recursive 96.1/65.6/88.8/95.0/97.7 → 88.62; Semantic 97.5/69.3/76.3/91.3/48.1 → 76.49; Sentence 86.3/78.4/72.5/61.9/67.2 → 73.26 (columns: RC, ICC, DCC, BI, SC). The paper notes an "amplification effect": small per-metric intrinsic gains compound into larger downstream RAG gains.
- **Cost (Table 6):** Document Contextual Coherence (~15:58) and entity–pronoun extraction (~13:13) dominate evaluation time — these (DCC and the coreference-based RC) are the runtime bottlenecks.

### Reproduction
```bash
pip install -e ".[paper]"
python -m spacy download en_core_web_sm
python -m adaptive_chunking.paper.replicate \
    --data-dir data/clair/ --output-dir results/ \
    --steps chunking metrics raw_metrics analysis table3 --device cuda:0
```
Steps: `chunking` (33 docs × 8 methods + post-processing; GPU for the semantic chunker), `mentions` (pre-computed in `data/clair/mentions/`), `metrics` / `raw_metrics` (~9h local or ~30 min with `JINA_API_KEY`; resumable), `analysis` (Tables 1–2, Figure 1), `table3`, `rag` (Tables 4–5; expensive — hundreds of OpenAI calls + GPU). Flags `--skip-llm-regex` (needs `OPENAI_API_KEY`) and `--skip-semantic` (needs GPU + flash-attention).

### Integration with RAG frameworks
Framework-agnostic at the core: any chunker is "a callable that takes text and returns a list of chunks," and metrics operate on chunk lists. The core depends on LangChain only through the `[paper]` extra (for baselines), not for production use. Output is plain Python dicts, easily mapped to LangChain/LlamaIndex `Document` objects. **No native LangChain/LlamaIndex integration class ships** with the library.

## Recommendations
1. **To use in production now:** install from git (`pip install -e ".[parsing]"`) — it is not on PyPI. Default `DoclingParser` for general PDFs; `PyMuPDFParser` for speed (mind the AGPL license); `AzureDIParser` for complex layouts/tables (set `ADI_ENDPOINT`/`ADI_KEY`).
2. **Avoid the `[coref]` extra in commercial settings** — `maverick-coref` is CC BY-NC-SA 4.0 (non-commercial). Without it the RC metric is unavailable; the maintainers state they are actively replacing copyleft dependencies. If commercial, run adaptive selection on the other four metrics (SC, ICC, DCC, BI).
3. **Set `JINA_API_KEY`** to cut metric computation from ~9h to ~30min, or inject your own embedder. DCC and coreference are the hot spots — budget for them or disable RC.
4. **For custom chunkers/metrics:** register any `text -> List[str]` callable as a chunker (wire into `split_documents.py`); add a scoring function to `metrics.py` and wire into `compute_metrics.py`. For a custom parser, subclass `BaseParser` and implement `parse_docs_in_dir()` + `convert_raw_results_to_markdown()`.
5. **Calibrate expectations on maturity:** this is an alpha (v0.1.0), "initial release" repo (star count was rising fast during research, ~133 by end). Pin to a specific commit and expect API churn; do not assume the established-project status the post implied.
6. **Decision threshold:** if your corpus is single-domain and homogeneous, the per-document selection overhead may not pay off. Measure intrinsic-metric variance across your documents first — if nearly all documents select the same chunker, use that chunker statically and skip the selection machinery. The method's value comes specifically from heterogeneous corpora (contracts + slide decks + large tables + technical docs).

## Caveats
- The arXiv PDF's machine-readable text layer did not extract through the fetch tool; paper specifics (full tables, appendices) were corroborated via the arXiv abstract page, HuggingFace, the LightOn blog, the Joe Sack Substack analysis, and search snippets rather than a clean full-text PDF fetch.
- Source files `__init__.py`, `parsing.py`, `split_documents.py`, `compute_metrics.py`, and `paper/replicate.py` could not be fetched directly (not indexed / fetch restrictions). The full `chunk_files()` signature defaults, exact parser `__init__` parameters, and the literal selection-function code are therefore inferred from README + LLM.md + Table-3 values rather than copied verbatim — flagged inline. `metrics.py`, `splitters.py`, and `pyproject.toml` WERE fetched in full and are quoted verbatim.
- GitHub star/fork counts and the HuggingFace GitHub badge were inconsistent across snapshots (4–133 stars; 15–124 on badge) — the repo is new and the numbers are volatile; the "~106 stars" claim was not confirmed as a stable value.
- Domain-naming discrepancy between the paper abstract ("social science") and the README ("sustainability reporting") is unresolved.
- "MIT licensed" is correct for the **core**, but two optional extras carry restrictive licenses: `maverick-coref` (CC BY-NC-SA 4.0, non-commercial) and `pymupdf4llm` (AGPL-3.0 / Artifex commercial). Confirm your license posture before shipping. These extras are not installed by default; see the repo's `NOTICE` and `SBOM.md`.