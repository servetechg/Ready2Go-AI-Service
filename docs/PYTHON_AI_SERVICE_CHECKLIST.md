# Smart Document Service — Simple Progress Checklist

> **What this is:** an easy-to-read progress tracker for the new service that
> reads uploaded continuity documents, checks how well each one fits its plan,
> and writes a short, always-up-to-date summary.
>
> **Why we're building it:** the current system is slow, costs too much, and the
> summary keeps growing longer with every file. The new service fixes all three.
>
> **How to read this:** each big step has a goal and a few small tasks. Tick the
> boxes as they get done.
>
> **Boxes:** `[ ]` = not started · `[~]` = working on it · `[x]` = done · `[!]` = stuck
> **Last updated:** 2026-05-30

---

## The big picture (at a glance)

| # | Big step | Status | Done |
|---|---|---|---|
| 0 | Get ready (decisions + accounts) | [ ] Not started | 0 / 6 |
| 1 | Build the basic service | [ ] Not started | 0 / 6 |
| 2 | Read the documents | [ ] Not started | 0 / 6 |
| 3 | Give documents a "memory" | [ ] Not started | 0 / 6 |
| 4 | Score each document | [ ] Not started | 0 / 7 |
| 5 | Avoid repeat work (save money) | [ ] Not started | 0 / 5 |
| 6 | Write a one-line note per document | [ ] Not started | 0 / 4 |
| 7 | Write the short overall summary | [ ] Not started | 0 / 6 |
| 8 | Connect it to the main app | [ ] Not started | 0 / 6 |
| 9 | Go live safely | [ ] Not started | 0 / 6 |

**👉 Next thing to do:** Step 0 — make a few key decisions and set up the accounts the service needs.

---

## Step 0 — Get ready
**Goal:** decide the few open questions and open the accounts we need.

- [ ] Agree on a few open choices (how documents are grouped, how often the summary refreshes, etc.)
- [ ] Open an account for the "document memory" tool (the search/vector service)
- [ ] Set up a small database to remember results so we don't repeat work
- [ ] Pick where the new service will live online (the hosting provider)
- [ ] Create the secret passwords/keys the service needs to stay private
- [ ] Confirm we can reuse the existing AI key (or get a new one for this service)

---

## Step 1 — Build the basic service
**Goal:** a simple, secure service that's online and responds when asked.
**Done when:** it answers a basic "are you alive?" check and blocks anyone without the right key.

- [ ] Create the starter project
- [ ] Set up the basic "front door" that receives requests
- [ ] Load all the settings and secret keys safely
- [ ] Add security so only the main app can talk to it
- [ ] Add a simple health check ("is everything running?")
- [ ] Put it online and confirm the main app can reach it

---

## Step 2 — Read the documents
**Goal:** open each uploaded file (PDF, Word, Excel, CSV) and pull out the text — the *whole* file, not just the first part.
**Done when:** every file type is read correctly, including blank or scan-only files.

- [ ] Download the uploaded file from storage
- [ ] Read text from PDFs
- [ ] Read text from Word, Excel, and CSV files
- [ ] Detect when a file is empty or just a scan (no real text)
- [ ] Break long documents into smaller, sensible pieces
- [ ] Stop cutting off long documents early (read all of it)

---

## Step 3 — Give documents a "memory"
**Goal:** store each document in a smart, searchable form so we can compare it to others quickly — even with thousands of files.
**Done when:** documents can be saved and found again, and each customer's files stay separate.

- [ ] Connect to the document-memory tool
- [ ] Set up the storage so each customer's files are kept apart
- [ ] Turn document text into a form the computer can compare
- [ ] Save each document (and replace it cleanly if re-uploaded)
- [ ] Be able to search documents by meaning *and* by keyword
- [ ] Add reference examples of each plan type to compare against

---

## Step 4 — Score each document *(the heart of it)*
**Goal:** give each document a fair, explainable score for how well it fits its plan — instead of a quick guess.
**Done when:** the score is reliable and we can explain *why* a document got it.

- [ ] Check if the document's **content** matches the plan
- [ ] Check if the **file name** matches the plan
- [ ] Check if it's filed under the **right category**
- [ ] Check the **quality** of the text we could read
- [ ] Check for **duplicates** or odd files in the same plan
- [ ] Combine these into one clear score and label (In Sync / Reviewing / Deviation Found)
- [ ] Use a quick AI double-check only for borderline cases (to save money)

---

## Step 5 — Avoid repeat work *(save money)*
**Goal:** never re-process the same file twice. This is the biggest cost saver.
**Done when:** re-uploading the exact same file returns the saved result instantly with no AI cost.

- [ ] Remember each result so identical files reuse it
- [ ] Skip all the work when a file hasn't changed
- [ ] Re-check a file only when our method is improved
- [ ] Keep a record of every AI request (for cost tracking and troubleshooting)
- [ ] Confirm identical re-uploads cost nothing extra

---

## Step 6 — Write a one-line note per document
**Goal:** a short, plain-English sentence describing each document.
**Done when:** every document gets a tidy one-liner that stays the same on re-upload.

- [ ] Create a short summary for each document
- [ ] Save it so it doesn't get re-written needlessly
- [ ] Send it back to the main app to show on screen
- [ ] Have a sensible backup line if the AI is unavailable

---

## Step 7 — Write the short overall summary *(fixes the long-summary problem)*
**Goal:** one short overview of the whole document library that stays short — whether there are 10 files or 10,000.
**Done when:** the summary stays brief and cheap to refresh no matter how many files exist.

- [ ] Keep a running tally as documents come in (no re-reading everything)
- [ ] Track the few most important issues (low scores, gaps, missing items)
- [ ] Build the summary from the tally + a small sample, not the whole library
- [ ] Handle very large libraries gracefully
- [ ] Work out the overall health rating (Resilient / Steady / At Risk)
- [ ] Only refresh the summary when needed, not on every single upload

---

## Step 8 — Connect it to the main app
**Goal:** plug the new service into the existing upload page, with a safety switch to turn it on/off.
**Done when:** uploading a file shows a real score and a short summary on the existing page.

- [ ] Add the on/off switch and connection settings in the main app
- [ ] Send each new upload to the new service and save its result
- [ ] If the service is slow or down, the upload still works (safe fallback)
- [ ] Use the new service for the overall summary too
- [ ] Keep the old method available as a backup
- [ ] Test uploading every file type and confirm it shows correctly

---

## Step 9 — Go live safely
**Goal:** switch over carefully, score the old files, and keep an eye on things.
**Done when:** the new service runs in production and older files are scored too.

- [ ] Run new and old side-by-side for a while to compare results
- [ ] Confirm the new results look right
- [ ] Turn the new service on for real users
- [ ] Score all the older documents that were uploaded before
- [ ] Set up a simple dashboard (cost, speed, errors)
- [ ] Do a "what if it breaks?" test to confirm uploads still work

---

## Always keep an eye on these

- [ ] **Cost** — make sure we're not paying to redo work
- [ ] **Reliability** — it should recover on its own from hiccups
- [ ] **Speed** — results should feel quick
- [ ] **Privacy & security** — each customer's data stays separate and protected
- [ ] **Don't break the screen** — the existing page must keep working exactly as before
