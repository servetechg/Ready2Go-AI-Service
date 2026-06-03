# Vector DB & RAG — Conceptual Refresher

> **Purpose:** refresh the core concepts behind the Ready2Go AI service —
> embeddings, vector databases, similarity, the parse→chunk→embed→search pipeline,
> and Retrieval-Augmented Generation (RAG) — and answer one specific question:
> **does our current Weaviate design actually need RAG?** (Short answer: no, not
> classic RAG — see §6.)
>
> **Audience:** anyone reviewing or building this service who wants the mental
> model, not the code. The *how-to-build* lives separately in
> `docs/PHASE_B_VECTOR_IMPLEMENTATION_PLAN.md`. The architecture contract lives in
> `docs/PYTHON_AI_SERVICE_ARCHITECTURE.md`.
>
> **This doc is conceptual only.** No file paths, no APIs — just the ideas.

---

## 1. The problem we're modelling

We receive a continuity document (PDF/DOCX/XLSX/CSV) that *claims* to belong to a
plan (a COOP / BCP / compliance plan with a label, overview, and steps). We must
answer, **defensibly**:

- How well does this file's **content** actually match the plan it's filed under?
- Is it in the **right category**?
- Is it a **duplicate** of something already there?
- Could we even **read** it (or is it a blank/scanned image)?

The old system asked one LLM "rate this 0–100" on the first 8 000 characters. That
is a *guess*. We want a **calculated, explainable** score. The tool that makes that
possible is the **embedding** + **vector database**.

---

## 2. Embeddings — turning meaning into numbers

An **embedding model** (we use OpenAI `text-embedding-3-small`) reads a piece of
text and outputs a fixed-length list of numbers — a **vector** (1 536 numbers for
this model). Think of it as a coordinate in a 1 536-dimensional space.

The key property: **text with similar meaning lands close together**, even when the
words differ.

```
"pandemic continuity of operations plan"   → [0.021, -0.184, 0.077, … ]  ┐ close
"plan for keeping operations running        → [0.019, -0.171, 0.080, … ]  ┘ together
 during a disease outbreak"
"quarterly cafeteria menu"                  → [-0.203, 0.401, -0.058, … ]  ← far away
```

You never read these numbers yourself. You **compare** them.

### Cosine similarity — the comparison
The standard way to compare two vectors is **cosine similarity**: it measures the
*angle* between them, ignoring length. Conceptually it ranges from **−1** (opposite)
through **0** (unrelated) to **1** (identical meaning). In practice, for text
embeddings you'll see values like 0.3 (loosely related) up to 0.9+ (near-identical).

**This single number — "how aligned are these two pieces of text" — is the
foundation of every scoring signal in this service.**

---

## 3. What a vector database is (and why Weaviate)

If embeddings are coordinates, a **vector database** is the spatial index that
stores millions of them and answers, very fast: *"give me the points nearest to
this one."* That "nearest neighbour" search is what powers semantic search,
recommendations, dedup, and similarity scoring.

A plain list of vectors would force you to compare against *every* stored vector
(slow at scale). Vector DBs use **approximate nearest-neighbor (ANN)** indexes
(e.g. HNSW) to find the closest matches in roughly logarithmic time.

We use **Weaviate** (self-hosted, open-source) for three properties beyond raw ANN:

| Feature | What it gives us |
|---|---|
| **Multi-tenancy** | Each subadmin's documents live in their own isolated tenant. Tenant A can never retrieve tenant B's vectors — the privacy boundary is enforced by the store, not by hand-written filters. |
| **Hybrid search** | Combines **vector** similarity (meaning) with **BM25** keyword scoring (exact terms). Useful when a filename or slug should match on literal words *and* meaning. |
| **Filtering + metadata** | Each vector carries fields (`attachmentId`, `planId`, `category`, `contentHash`, …) so we can scope, group, and replace by document. |

> **Mental model:** Weaviate is a *search/similarity engine*, not a database of
> answers. It tells us "how close" and "what's nearby." It does **not**, by itself,
> write prose.

---

## 4. The pipeline — what actually happens to one file

This is the journey from raw bytes to a score. Each step exists for a reason.

```
  bytes ─▶ extract ─▶ clean ─▶ chunk ─▶ embed ─▶ upsert ─▶ search/score ─▶ summarise
 (fetch)   (parse)  (normalise) (split) (vectorise) (store)  (compare)     (one-liner)
```

1. **Fetch** — download the file bytes from Cloudinary and compute a SHA-256 hash.
   The hash is the **fingerprint**: identical bytes ⇒ identical hash ⇒ we can reuse
   a cached result and spend **zero** tokens. *(Already built.)*

2. **Parse / extract** — convert the binary file into plain text, per type:
   - **PDF** → text extractor (with a fallback for tricky PDFs)
   - **DOCX** → paragraph text
   - **XLSX** → each sheet flattened to readable rows
   - **CSV** → the raw table text

   We also record a **quality read**: how many characters/pages/rows, and whether
   the file is **empty** or **scan-only** (a PDF that is just photos with no real
   text). This replaces the old "score 35–55 conservatively" guess with a real,
   deterministic signal. **No 8 000-character cap** — we read the whole document.

3. **Clean / normalise** — collapse whitespace, strip repeated headers/footers and
   boilerplate so the embedding reflects *content*, not layout noise.

4. **Chunk** — split the text into **~500-token windows with ~50-token overlap**.
   Why chunk at all?
   - Embedding models have an input limit; a 100-page plan won't fit in one call.
   - A single vector for a whole document **averages away** detail — a focused
     paragraph and a vague one would blur together. Smaller chunks keep meaning
     sharp and let us find *which part* matched.
   - Overlap stops us from cutting a sentence/idea exactly at a boundary.

   We **cap** the number of chunks per document so one giant file can't run up an
   unbounded embedding bill.

5. **Embed** — send all the chunks in **one batched API call** and get back one
   vector per chunk.

6. **Upsert** — store each chunk (text + vector + metadata) in Weaviate **under the
   document's tenant**. On re-upload, we delete that attachment's old chunks first
   (tenant-scoped) so there are no stale duplicates.

7. **Search / score** — now the comparisons that produce the integrity signals:
   - **Content alignment** — average the document's chunk vectors into one
     "centroid," then cosine-compare it to the embedding of the **plan context**
     (label + overview + steps). High = the file is about what the plan says.
   - **Category fit** — cosine-compare the centroid to a stored **prototype** of
     each category (coop/bcp/compliance); if a *different* category scores higher,
     the file is likely **mis-filed**.
   - **Duplication** — nearest-neighbour search among **sibling** files in the same
     plan flags near-duplicates or incoherent additions.
   - **Name alignment** — **hybrid** search of the filename/slug against the plan
     label/category (keyword + meaning).
   - **Extraction quality** — the deterministic read from step 2.

   These five numbers are combined with weights into one 0–100 score and a status
   (**In Sync / Reviewing / Deviation Found**). Because each signal is visible, the
   score is **explainable** — we can say *why*.

8. **Summarise** — produce a ≤280-char one-liner for the document from its
   **complete** set of chunks (all of this document's pieces, fetched by id — we own
   the whole file, so there is no "most relevant" subset), and (separately, on
   demand) a short vault-wide audit narrative from a bounded sample. See §6.

> **Note none of steps 1–7 involve an LLM writing text.** They are extraction,
> math, and search. The only places an LLM *generates* anything are the summaries
> (§6) and an optional tie-breaker for borderline scores.

---

## 5. RAG — what it is, and when you actually need it

**RAG = Retrieval-Augmented Generation.** The pattern is three moves:

```
  1. RETRIEVE   — embed the user's question, search the vector DB for the
                  most relevant chunks.
  2. AUGMENT    — paste those chunks into the LLM prompt as context.
  3. GENERATE   — the LLM writes an answer grounded in those chunks
                  (ideally citing them).
```

RAG exists to let an LLM answer about a **large or constantly-changing body of
knowledge it was never trained on** — your private documents — without retraining
the model and without exceeding its context window.

### When RAG IS the right tool
- **"Chat with your documents"** / Q&A over a corpus.
- Answers that must **cite source passages**.
- A knowledge base **too big to fit** in a single prompt.
- Content that **changes often** (new uploads) and must be reflected immediately.

### When RAG is NOT needed
- **Scoring / classification by similarity** — you're comparing vectors and
  producing a number or a label. Nothing is generated. *(This is us.)*
- **Dedup, clustering, search ranking** — pure vector operations.
- Tasks where the input already fits in the prompt — just send it directly.

> **One-line test:** *Is an LLM writing an answer grounded in retrieved passages?*
> Yes → RAG. No (you're just measuring closeness or ranking) → not RAG, even though
> you're using the exact same embeddings and vector DB.

---

## 6. Does OUR current Weaviate design need RAG?

**No — not classic, open-domain RAG.** Here is each place we touch the vector DB
and whether it's RAG:

| Where | What happens | Is it RAG? |
|---|---|---|
| **Integrity scoring** (content / category / duplication / name signals) | Embeddings used as **features**; cosine-similarity math produces numbers. **Nothing is generated.** | **No.** Weaviate is a similarity/search engine here. |
| **Per-document one-liner** | Fetch *this one document's* **complete** chunk set (by `attachmentId`) → small LLM call → one sentence. | **Not RAG.** No query to rank against; it's a fetch-by-id of a doc we own, then summarise the whole thing. |
| **Vault-wide audit summary** | Generate from **rolling aggregate counts** + a **capped sample of already-stored one-liners** (not raw chunks). | **Not classic RAG** — bounded sample over summaries, not the corpus; cost is O(1) regardless of how many docs exist. |
| **Borderline LLM judge** (optional) | For scores in a narrow grey band, send the excerpt + plan context for a tie-break. | **No** — not retrieval-based. |

**Why this matters:** the headline value of this service — a calculated,
explainable integrity score — comes from **embeddings + similarity**, which is
*not* RAG. Weaviate earns its place for **multi-tenant similarity search and
duplicate detection**, not for feeding an LLM. The only generation steps work on
**bounded inputs** (one document's own chunks, or a capped sample), so we never pay
the "stuff the whole corpus into a prompt" cost that classic RAG is designed to
manage.

### When we *would* add real RAG later
If the product later wants any of these, full chunk-grounded RAG becomes justified:
- **"Explain why this file scored low,"** quoting the exact passages that conflict
  with the plan.
- **"Ask a question across the whole continuity vault"** (operator Q&A).
- **Citation-backed audit findings** that link to source text.

Those are generation-over-retrieved-chunks features — and the chunks are *already*
in Weaviate from the scoring pipeline, so adding RAG then is incremental, not a
rebuild.

---

## 6.5 Does fetching a whole document "waste" similarity search?

Short answer: **no.** Pulling all of one document's chunks for its summary and doing
fast similarity search are **two different jobs**, and we still use similarity
search for the part that matters most.

There are two ways we read from Weaviate:

| Access pattern | Question it answers | Where we use it |
|---|---|---|
| **Similarity / ANN search** | *"Which pieces are nearest to this vector / match this query?"* | **Scoring** — content-vs-plan, category fit, duplication, name. Plus any future corpus Q&A. This is Weaviate's core strength and is **fully used**. |
| **Fetch-by-id (filter)** | *"Give me this one document's pieces."* | The **per-document summary** — there is no query to rank against; the document itself is the subject, so we take all of it. |

Key points:

- "Top-k most relevant chunks" only makes sense when you have a **question** and want
  the chunks closest to it (that's RAG). For *"summarise this whole file,"* every
  piece is relevant by definition — forcing top-k would **throw away parts of the
  document** and make the summary worse.
- Fetch-by-id is a **filtered metadata lookup**, a normal, intended Weaviate
  operation — not a bypass or misuse of the vector index.
- It is **bounded**: one document's chunks are capped (e.g. ≤200), so we never scan
  the corpus. The only corpus-wide step — the vault audit — still uses a capped
  **sample**, not everything.

**One line:** similarity search answers *"which pieces match this query?"*; the
per-document summary asks *"what is this whole file about?"* — different questions,
so a full fetch is correct there while similarity search stays the heart of scoring.

---

## 7. One-paragraph summary

We embed each document's text into vectors and store them per-tenant in Weaviate.
**Scoring is similarity math over those vectors — explainable, cheap, and not RAG.**
We generate only two small things: a per-doc one-liner (from the document's
**complete** chunk set, fetched by id — not a relevance subset) and a vault-wide
summary (from a **bounded sample**). Neither is classic Retrieval-Augmented
Generation. Real RAG (an LLM answering open questions over the corpus) is **not
required for v1** and is a clean future add-on if we ever build operator Q&A or
cited findings.
