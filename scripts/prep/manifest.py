"""Structured list of openly-fetchable .gov continuity documents.

Source: docs/COOP BC PLANS AND DOCS RESOURCES.md

Each SeedDoc maps to exactly one EmergencyPlan attachment in the test corpus.
The `tenant` field ("A" or "B") controls which synthetic subadmin owns the doc,
spreading the corpus across ≥2 owners so tenant isolation can be verified.

Category assignment:
  coop       — FEMA ONCP doctrine + templates (essential-function continuity)
  bcp        — NIST 800-34 / CISA ESS (IT/contingency + sector continuity)
  compliance — CMS regulatory rule + interpretive guidance

Documents that CANNOT be fetched are recorded in SKIPPED with the reason,
so they are never silently ignored and stakeholders know not to chase them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SeedDoc:
    title: str
    url: str
    category: Literal["coop", "bcp", "compliance"]
    plan_id: str    # slug — groups related files into one EmergencyPlan per owner
    tenant: str     # "A" or "B" — which synthetic/real subadmin owns this doc


# ---------------------------------------------------------------------------
# Fetchable documents
# ---------------------------------------------------------------------------

SEED_DOCS: list[SeedDoc] = [
    # ------------------------------------------------------------------
    # COOP — FEMA ONCP 2024 Federal Continuity Directive series + templates
    # Assigned to test subadmin A
    # ------------------------------------------------------------------
    SeedDoc(
        title="FCD — Federal Executive Branch Continuity Program Management "
              "Requirements (Aug 2024)",
        url="https://www.fema.gov/sites/default/files/documents/"
            "fema_oncp_fcd-federal-executive-branch-continuity-program-management-requirements.pdf",
        category="coop",
        plan_id="federal-continuity-program-mgmt",
        tenant="A",
    ),
    SeedDoc(
        title="FCD — Continuity Planning Framework for the Federal Executive Branch (Aug 2024)",
        url="https://www.fema.gov/sites/default/files/documents/"
            "fema_federal-continuity-directive-planning-framework.pdf",
        category="coop",
        plan_id="continuity-planning-framework",
        tenant="A",
    ),
    SeedDoc(
        title="FCD — Federal Executive Branch Essential Functions Risk "
              "Identification & Management (Aug 2024)",
        url="https://www.fema.gov/sites/default/files/documents/"
            "fema_oncp-fcd-federal-executive-branch-essential-functions-risk-identification-management.pdf",
        category="coop",
        plan_id="essential-functions-risk-mgmt",
        tenant="A",
    ),
    # NOTE: FEMA has moved this PDF — the _082024 URL returns 404 as of 2026-06-02.
    # Verify the current path from the landing page before enabling:
    #   https://www.fema.gov/emergency-managers/national-preparedness/continuity/circular
    # Uncomment and update the URL once confirmed.
    # SeedDoc(
    #     title="Continuity Guidance Circular (Aug 2024 update)",
    #     url="https://www.fema.gov/sites/default/files/documents/"
    #         "fema_continuity-guidance-circular_082024.pdf",
    #     category="coop",
    #     plan_id="continuity-guidance-circular",
    #     tenant="A",
    # ),
    SeedDoc(
        title="FEMA Continuity Plan Template for Federal Departments/Agencies (Oct 2020)",
        url="https://www.fema.gov/sites/default/files/2020-10/"
            "fema_planning-template-federal-departments-agencies_october-2020_0.pdf",
        category="coop",
        plan_id="continuity-plan-template",
        tenant="A",
    ),
    SeedDoc(
        title="FEMA Reconstitution Plan/Annex Template & Instructions",
        url="https://www.fema.gov/sites/default/files/2020-09/"
            "fema_reconstitution-plan_template_10-22-19.pdf",
        category="coop",
        plan_id="reconstitution-plan-template",
        tenant="A",
    ),
    SeedDoc(
        title="FEMA Continuity Risk Toolkit",
        url="https://www.fema.gov/sites/default/files/2020-07/Continuity-Risk-Toolkit_013118.pdf",
        category="coop",
        plan_id="continuity-risk-toolkit",
        tenant="A",
    ),
    SeedDoc(
        title="FCD 1 — Federal Continuity Directive 1 (Jan 2017, legacy)",
        url="https://www.fema.gov/sites/default/files/2020-07/January2017FCD1.pdf",
        category="coop",
        plan_id="fcd-1-2017",
        tenant="A",
    ),
    # DOCX variant — exercises the DOCX extraction path (Phase B) and confirms
    # the seeder handles non-PDF files correctly today.
    SeedDoc(
        title="FEMA Devolution of Operations Plan Template (.docx)",
        url="https://www.fema.gov/sites/default/files/2020-07/devolution-plan-template_082319.docx",
        category="coop",
        plan_id="devolution-plan-template",
        tenant="A",
    ),

    # ------------------------------------------------------------------
    # BCP — NIST contingency guide + CISA Emergency Services Sector suite
    # Assigned to test subadmin B
    # ------------------------------------------------------------------
    SeedDoc(
        title="NIST SP 800-34 Rev.1 — Contingency Planning Guide for Federal Information Systems",
        url="https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-34r1.pdf",
        category="bcp",
        plan_id="nist-800-34-contingency",
        tenant="B",
    ),
    SeedDoc(
        title="CISA Emergency Services Sector Continuity Planning Suite — Checklist",
        url="https://www.cisa.gov/sites/default/files/publications/"
            "emergency-services-sector-continuity-planning-suite-checklist-022018-508.pdf",
        category="bcp",
        plan_id="cisa-ess-checklist",
        tenant="B",
    ),
    SeedDoc(
        title="CISA ESS — Continuity Capability Evaluation Form (Nov 2023)",
        url="https://www.cisa.gov/sites/default/files/2023-12/"
            "ess-continuity-capability-evaluation-form_112023_508.pdf",
        category="bcp",
        plan_id="cisa-ess-capability-eval",
        tenant="B",
    ),
    SeedDoc(
        title="CISA ESS — Orders of Succession Worksheet",
        url="https://www.cisa.gov/sites/default/files/publications/"
            "emergency-services-sector-continuity-planning-suite-worksheet-2-orders-of-succession-022018-508.pdf",
        category="bcp",
        plan_id="cisa-ess-succession",
        tenant="B",
    ),
    SeedDoc(
        title="FEMA Continuity Plan Template for Non-Federal Governments",
        url="https://www.fema.gov/sites/default/files/2020-10/non-federal-continuity-plan-template_083118.pdf",
        category="bcp",
        plan_id="non-federal-continuity-template",
        tenant="B",
    ),

    # ------------------------------------------------------------------
    # COMPLIANCE — CMS Emergency Preparedness Rule + interpretive guidance
    # Assigned to test subadmin B
    # ------------------------------------------------------------------
    SeedDoc(
        title="CMS Emergency Preparedness Final Rule (81 FR 63860, Sep 2016)",
        url="https://www.govinfo.gov/content/pkg/FR-2016-09-16/pdf/2016-21404.pdf",
        category="compliance",
        plan_id="cms-emergency-preparedness-rule",
        tenant="B",
    ),
    SeedDoc(
        title="CMS State Operations Manual Appendix Z — Emergency Preparedness "
              "Interpretive Guidance",
        url="https://www.cms.gov/Regulations-and-Guidance/Guidance/Manuals/downloads/som107ap_z_emergprep.pdf",
        category="compliance",
        plan_id="cms-appendix-z",
        tenant="B",
    ),
]


# ---------------------------------------------------------------------------
# Documents that cannot be fetched — recorded here so they are never silently
# ignored and stakeholders know not to chase them.
# ---------------------------------------------------------------------------

SKIPPED: dict[str, str] = {
    "PPD-40 (Presidential Policy Directive 40)": (
        "Classified — no public full text exists. "
        "Use FCD 1 (2017) and EO 13961 as authoritative surrogates."
    ),
    "NFPA 1600/1660": (
        "Copyrighted consensus standard — not an open government document. "
        "Free online reading requires NFPA registration; download requires purchase. "
        "Treat as a licensed standard, not a fetchable PDF."
    ),
}
