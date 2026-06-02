# Verified URLs & Programmatic Access Methods for U.S. Government Continuity (COOP) and Business Continuity Documents

## TL;DR
- **Nearly every requested document is free, open, and directly fetchable via `curl`/`wget` from `.gov` PDF URLs** — the FEMA Continuity Resource Toolkit, the August 2024 Federal Continuity Directive series and Continuity Guidance Circular, CISA's Emergency Services Sector suite, NIST SP 800-34 Rev. 1, and the CMS Emergency Preparedness Rule are all openly accessible without registration.
- **The major exceptions are PPD-40 (no public full text exists — it is classified) and NFPA 1600 (a copyrighted standard, now consolidated into NFPA 1660, requiring free registration to read online or purchase to download).** HSPD-5 and HSPD-7 are fully open.
- **Four federal APIs cover these documents programmatically:** OpenFEMA (`www.fema.gov/api/open`, no key), govinfo (`api.govinfo.gov`, free api.data.gov key), regulations.gov (`api.regulations.gov/v4`, free key), and FederalRegister.gov (no key). For static doctrine PDFs, however, the simplest "API" is a direct HTTPS GET on the PDF URL.

## Key Findings

The FEMA Office of National Continuity Programs (ONCP) consolidated and re-issued its core doctrine in **August 2024**, replacing the old single FCD 1/FCD 2 numbering with a **named three-part Federal Continuity Directive series** plus an updated Continuity Guidance Circular. All are hosted as open PDFs under `fema.gov/sites/default/files/`. The legacy 2017 FCD 1 and 2012–2020-era templates remain online at older `fema.gov/pdf/about/org/ncp/` paths and are still widely referenced.

For programmatic harvesting, FEMA's doctrine PDFs are **not** in the OpenFEMA dataset API (which serves disaster/structured data, not doctrine documents). The correct programmatic method for the doctrine is a direct `curl`/`wget` on the PDF URL. For federal directives and the CMS rule as *published regulatory/Federal Register records*, the govinfo, regulations.gov, and FederalRegister.gov APIs apply.

## Details

### 1. Continuity of Operations (COOP) & Strategic Recovery Protocols

**FEMA Continuity Resource Toolkit (main hub page)**
- Hub: `https://www.fema.gov/emergency-managers/national-preparedness/continuity`
- Documents library: `https://www.fema.gov/emergency-managers/national-preparedness/continuity/documents`
- Type: Web portal (FEMA ONCP). Free/open, no registration.

**Federal Continuity Directive series (August 2024 update)** — issued by FEMA ONCP. Free/open. The 2024 reissue uses three named directives rather than the old "FCD 1 / FCD 2":
- FCD: Federal Executive Branch Continuity Program Management Requirements (Aug 2024, the functional successor to FCD 1): `https://www.fema.gov/sites/default/files/documents/fema_oncp_fcd-federal-executive-branch-continuity-program-management-requirements.pdf`
- FCD: Continuity Planning Framework for the Federal Executive Branch: `https://www.fema.gov/sites/default/files/documents/fema_federal-continuity-directive-planning-framework.pdf`
- FCD: Federal Executive Branch Essential Functions Risk Identification and Management (the functional successor to FCD 2): `https://www.fema.gov/sites/default/files/documents/fema_oncp-fcd-federal-executive-branch-essential-functions-risk-identification-management.pdf`

**Legacy FCD 1 (2007 and 2017)** — still useful and online:
- FCD 1 (January 17, 2017), FEMA mirror: `https://www.fema.gov/sites/default/files/2020-07/January2017FCD1.pdf`
- FCD 1 (January 17, 2017), GPO mirror: `https://www.gpo.gov/docs/default-source/accessibility-privacy-coop-files/January2017FCD1-2.pdf`
- FCD 1 (November 6, 2007 original): `https://www.fema.gov/pdf/about/org/ncp/fcd1.pdf`

**Continuity Guidance Circular (CGC), August 2024 update** — FEMA ONCP. Free/open. Per FEMA's August 21, 2024 Stakeholder Advisory and the PDF's own title "Continuity Guidance Circular – February 2018 (2024 Update)": "FEMA has released the 2024 update of the Continuity Guidance Circular. This document applies a whole of community approach to continuity operations outlined in the Federal Continuity Directives" (issued under FEMA Administrator Deanne Criswell / FEMA ONCP).
- Landing page: `https://www.fema.gov/emergency-managers/national-preparedness/continuity/circular`
- Direct PDF: `https://www.fema.gov/sites/default/files/documents/fema_continuity-guidance-circular_082024.pdf`

**FEMA Devolution of Operations Plan Template** — FEMA NCP. Free/open.
- PDF: `https://www.fema.gov/pdf/about/org/ncp/dev_template.pdf`
- Word (.docx): `https://www.fema.gov/sites/default/files/2020-07/devolution-plan-template_082319.docx`

**FEMA Reconstitution Plan Template** — FEMA ONCP. Free/open.
- Reconstitution Plan/Annex Template & Instructions: `https://www.fema.gov/sites/default/files/2020-09/fema_reconstitution-plan_template_10-22-19.pdf`
- Related — Reconstitution Manager's Guide: `https://www.fema.gov/sites/default/files/documents/fema_reconstitution-managers-guide.pdf`
- Related — Executive Branch Reconstitution Concept of Operations: `https://www.fema.gov/sites/default/files/documents/fema_executive-branch-reconstitution-conop_01-2021.pdf`

**FEMA Continuity Plan Template for Federal Departments/Agencies** — FEMA NCP. Free/open.
- Oct 2020 version: `https://www.fema.gov/sites/default/files/2020-10/fema_planning-template-federal-departments-agencies_october-2020_0.pdf`
- Legacy versions: `https://www.fema.gov/pdf/about/org/ncp/coop/continuity_plan_federal_d_a.pdf` and `https://www.fema.gov/pdf/about/org/ncp/coop/continuity_plan_template.pdf`

**Presidential Policy Directive 40 (PPD-40), National Continuity Policy (July 15, 2016)** — The White House.
- **No public full text exists; PPD-40 is classified.** The White House never released a public or redacted full version. It replaced NSPD-51/HSPD-20.
- Best authoritative references that quote/summarize it: FCD 1 (2017) at `https://www.gpo.gov/docs/default-source/accessibility-privacy-coop-files/January2017FCD1-2.pdf`, and **Executive Order 13961, "Governance and Integration of Federal Mission Resilience," signed December 7 and published December 10, 2020 at 85 FR 79379 (Federal Register doc 2020-27353)**, which amends PPD-40 §6: `https://www.federalregister.gov/documents/2020/12/10/2020-27353/governance-and-integration-of-federal-mission-resilience` (govinfo PDF: `https://www.govinfo.gov/content/pkg/FR-2020-12-10/pdf/2020-27353.pdf`). For context, PPD-40 defines a "Catastrophic Emergency" as "Any event, regardless of location, that results in extraordinary levels of mass casualties, damage or disruption severely affecting the U.S. population, infrastructure, environment, economy or government functions" (quoted in the 2024 FCD).

**HSPD-5, Management of Domestic Incidents (Feb 28, 2003)** — The White House / DHS. Free/open.
- DHS PDF: `https://www.dhs.gov/sites/default/files/publications/Homeland%20Security%20Presidential%20Directive%205.pdf`
- DHS landing page: `https://www.dhs.gov/publication/homeland-security-presidential-directive-5`

**HSPD-7, Critical Infrastructure Identification, Prioritization, and Protection (Dec 17, 2003)** — The White House / CISA. Free/open.
- CISA hosted text: `https://www.cisa.gov/news-events/directives/homeland-security-presidential-directive-7`

**Continuity Assessment Tool (CAT)** — FEMA ONCP. Free/open. Listed on the FEMA Continuity Resources documents page with a "Download Document" link (dated May 23, 2022). Legacy media-library direct identifier: `https://www.fema.gov/media-library/assets/documents/158679`. The exact current `/sites/default/files/` filename should be copied from the live documents page (see Recommendation 6).

**Continuity Risk Toolkit** — FEMA NCP. Free/open (marked FOUO on its face but publicly posted).
- Verified direct PDF: `https://www.fema.gov/sites/default/files/2020-07/Continuity-Risk-Toolkit_013118.pdf`

### 2. Business Continuity and Compliance Vault

**NFPA 1600, Standard on Continuity, Emergency, and Crisis Management** — National Fire Protection Association. **Not free to download** (copyrighted consensus standard).
- Free online reading (no-cost NFPA account/registration required): `https://www.nfpa.org/codes-and-standards/nfpa-1600-standard-development/1600`
- Purchase: `https://www.nfpa.org/product/nfpa-1600-standard/p1600code`
- **Important — superseded:** NFPA 1600 (2019 edition) was consolidated, per NFPA's notice, into **NFPA 1660, "Standard for Emergency, Continuity, and Crisis Management: Preparedness, Response, and Recovery, 2024 edition,"** which merges NFPA 1600 (2019), NFPA 1616 (2020), and NFPA 1620 (2020); the digital version is posted at `nfpa.org/1660`. Use NFPA 1660 for current work; cite NFPA 1600 (2019) only for legacy compliance.

**FISMA / NIST SP 800-34 Rev. 1, Contingency Planning Guide for Federal Information Systems** — NIST. Free/open, public domain. The publication is **NIST SP 800-34 Rev. 1, dated May 2010 (150 pages), by Marianne Swanson, Pauline Bowen, Amy Wohl Phillips, Dean Gallup, and David Lynes** (per the NIST CSRC record: "Natl. Inst. Stand. Technol. Spec. Publ. 800-34, 150 pages (May 2010)").
- CSRC landing page: `https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final`
- Direct PDF (NIST NVL): `https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-34r1.pdf`
- Supplemental templates (BIA, and Low/Moderate/High-Impact ISCP) are .docx files linked from the CSRC page.

**CMS Emergency Preparedness Rule** — Centers for Medicare & Medicaid Services. Free/open. The original rule is **CMS-3178-F, "Medicare and Medicaid Programs; Emergency Preparedness Requirements for Medicare and Medicaid Participating Providers and Suppliers," published 09/16/2016 at 81 FR 63860 (pages 63860–64044, 185 pages), RIN 0938-AO91; effective November 16, 2016, with compliance required by November 15, 2017.** It was later amended by the 2019 Burden Reduction Final Rule (84 FR 51732).
- Main rule page: `https://www.cms.gov/medicare/health-safety-standards/quality-safety-oversight-emergency-preparedness/emergency-preparedness-rule`
- Core elements: `https://www.cms.gov/medicare/health-safety-standards/quality-safety-oversight-emergency-preparedness/core-ep-rule-elements`
- State Operations Manual Appendix Z (interpretive guidance) PDF: `https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/downloads/som107ap_z_emergprep.pdf`
- Original 2016 Final Rule, govinfo PDF: `https://www.govinfo.gov/content/pkg/FR-2016-09-16/pdf/2016-21404.pdf`

**CISA Emergency Services Sector Continuity Planning Suite (ESS-CPS)** — CISA. Free/open.
- Program page: `https://www.cisa.gov/emergency-services-sector-continuity-planning-suite`
- Checklist PDF: `https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-checklist-022018-508.pdf`
- Continuity Capability Evaluation form (Nov 2023): `https://www.cisa.gov/sites/default/files/2023-12/ess-continuity-capability-evaluation-form_112023_508.pdf`
- Worksheet examples: Orders of Succession — `https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-worksheet-2-orders-of-succession-022018-508.pdf`; Human Resources — `https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-worksheet-7-human-resources-022018-508.pdf`; Test/Training/Exercise — `https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-worksheet-8-test-training-and-exercise-022018-508.pdf`; Program Plans & Procedures — `https://www.cisa.gov/sites/default/files/publications/emergency-services-sector-continuity-planning-suite-worksheet-11-program-plans-and-procedures-022018-508.pdf`

**FEMA non-federal continuity planning templates** — FEMA NCP / CISA. Free/open.
- Continuity Plan Template for Non-Federal Governments (PDF): `https://www.fema.gov/sites/default/files/2020-10/non-federal-continuity-plan-template_083118.pdf`
- CISA hosted landing page: `https://www.cisa.gov/resources-tools/resources/fema-continuity-plan-template-and-instructions-non-federal-governments`

**DHS Additional Resources portal**
- DHS Continuity of Government page: `https://www.dhs.gov/continuity-government`
- CISA resources/tools hub: `https://www.cisa.gov/resources-tools/resources`

### 3. APIs and Programmatic Access Methods

**Direct PDF fetch (recommended for all doctrine PDFs):** every `.gov` PDF above is fetchable with `curl -O <url>` or `wget <url>`, no key, no registration.

**OpenFEMA API** — `https://www.fema.gov/about/openfema/api`. Free, no key/registration. RESTful, OData-style query strings. Base path: `https://www.fema.gov/api/open`. Example: `curl 'https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?$filter=disasterNumber eq 1491'`. Per FEMA's OpenFEMA Developer Resources: "only 1,000 records are returned per API endpoint call by default (i.e., if no value is set for $top). The maximum number of records that can be returned per call is 10,000 (i.e., $top=10000)"; page using `$skip` and `$inlinecount` (note: `$inlinecount` is now deprecated in favor of `$count`, and a new `$allrecords` parameter forces a full download). **Caveat: OpenFEMA serves structured datasets, not doctrine PDFs.**

**govinfo API** — `https://api.govinfo.gov` (docs: `https://api.govinfo.gov/docs/`). Requires a free api.data.gov key (use `DEMO_KEY` for testing). Collections, packages, published, related, and search services. Example: `https://api.govinfo.gov/collections?api_key=DEMO_KEY`. Good for HSPDs, Federal Register rules (CMS), and presidential documents.

**regulations.gov API v4** — `https://api.regulations.gov/v4` (docs: `https://open.gsa.gov/api/regulationsgov/`). Requires a free key passed in the `X-Api-Key` HTTP header. Endpoints: `/documents`, `/comments`, `/dockets`. Best for the CMS EP Rule docket and public comments. Default rate limit 1,000 requests/hour per key.

**FederalRegister.gov API v1** — `https://www.federalregister.gov/developers/documentation/api/v1`. No key required. Best first stop for the CMS rules and EO 13961.

**api.data.gov key** — a single 40-character key works across govinfo, regulations.gov, and other participating agencies. Register free at `https://api.data.gov`.

## Recommendations
1. **For automated ingestion of the core COOP doctrine, script direct `wget`/`curl` pulls** of the FEMA `sites/default/files/...` PDF URLs above. Prioritize the August 2024 authoritative versions — the three named FCDs and the `_082024` CGC.
2. **Do not rely on OpenFEMA for doctrine documents** — it serves structured disaster datasets, not policy PDFs. Use it only if you also need disaster/preparedness data.
3. **For PPD-40, ingest the surrogate sources** (FCD 1 2017 and EO 13961 at 85 FR 79379) since no public PPD-40 text exists; explicitly flag this gap to stakeholders so they do not chase a non-existent file.
4. **For NFPA, migrate to NFPA 1660 (2024)** and budget for procurement or use the free-registration online reader — it cannot be programmatically fetched as an open PDF; treat it as a licensed standard, not a government work.
5. **For the CMS rule and Federal Register items, use the FederalRegister.gov API (no key) first**, falling back to govinfo (PDF `FR-2016-09-16/pdf/2016-21404.pdf`) or regulations.gov (with an api.data.gov key) for full docket/comment harvesting.
6. **Verify the Continuity Assessment Tool's exact current filename** by loading the FEMA documents page and copying the live "Download Document" href, since the dated `/sites/default/files/` filename could not be independently confirmed; the legacy `media-library/assets/documents/158679` identifier is the reliable interim pointer.

**Thresholds that change these steps:** If FEMA migrates off the `sites/default/files/` path (a periodic occurrence), re-derive URLs from the documents landing page. If you need change-tracking on the CMS rule rather than a static copy, switch from one-off PDF pulls to the regulations.gov docket API with date filters.

## Caveats
- **PPD-40 is classified; there is no authentic public full-text PDF.** Any site claiming to host the "full" PPD-40 should be treated with suspicion.
- **NFPA 1600/1660 is copyrighted** and not an open government document; "free access" means free online viewing after registration, not free download. NFPA 1600 (2019) has been superseded by NFPA 1660 (2024).
- Several FEMA template PDFs date to 2014–2020 and predate the August 2024 doctrine reissue; they remain posted but may not reflect the latest FCD structure. Use them as structural starting points, not current-policy authorities.
- The Continuity Risk Toolkit and some FEMA/CISA continuity documents are marked "For Official Use Only" on their face yet are publicly posted; handle per your organization's information-handling policy.
- CISA.gov pages displayed a notice that, due to a lapse in federal funding, the website "will not be actively managed" — links remain live but may not be updated promptly during any shutdown period.
- Third-party mirrors (state agencies, Scribd, ANSI webstore) exist for several documents but should always be treated as secondary to the `.gov` originals.