# NASA Technical Standards System (NTSS) Sitemap & Scraper Solution

Automated, hardened sitemap generator and weekly crawler for the **NASA Technical Standards System (NTSS)** hosted by the Office of the NASA Chief Engineer (OCE):
`https://standards.nasa.gov/`

This repository generates and maintains a comprehensive, production-ready XML sitemap and URL list optimized for ingestion by **Onyx** (formerly Danswer) and other AI search / RAG platforms.

---

## Architecture & How This Solves NTSS Ingestion

Standard web crawlers fail or index empty content on `standards.nasa.gov` due to several architectural characteristics:

1. **Root SAML Authentication Redirect**:
   - The root path `https://standards.nasa.gov/` issues an HTTP 302 redirect to NASA Launchpad SAML authentication (`/saml/login?destination=/`).
   - However, the actual public standards repository, category disciplines, and standard landing pages are fully public and unauthenticated under:
     - `/all-standards` (Agency-wide standards: NASA-STD and NASA-HDBK)
     - `/center-specific-standards` (Center-level standards: GSFC, MSFC, JSC, KSC, LaRC, etc.)
     - 11 Technical Discipline landing pages (`/documentation-and-configuration`, `/structures-mechanical-systems`, etc.)
     - `/standard/{org}/{doc_number}` (Summary landing pages for all 315 standards)
   - **Solution**: The crawler bypasses the authenticated root and crawls the public catalog entrypoints directly.

2. **Strict "Latest Revision Only" Policy (No Historical / Cancelled Documents)**:
   - Out of 315 standards, 79 have previous/obsolete revisions hosted on the same page under `PUBLIC: Document History for Standard` and paths containing `/Historical/` (e.g., `NASA-STD-5001` hosts 5 obsolete revisions).
   - If ingested, obsolete standards pollute vector search results with superseded safety factors, outdated formulas, and cancelled processes.
   - **Solution**: The generator strictly extracts the single approved current revision from `PUBLIC: Upload Publicly Available Standard` and completely purges all historical PDFs and inactive documents.

3. **Public vs. Restricted Standards Isolation**:
   - 252 standards have active, unrestricted public PDFs (`Export Control: Internet Public`).
   - 63 standards have restricted PDFs (`NASA Internal` or `NASA and NASA Contractors`).
   - **Solution**: The crawler indexes the public landing pages for all active standards (providing metadata: Title, Scope, Responsible Office, and Keywords), but only includes direct PDF download links for cleared public documents.

---

## Indexed Content Overview

The sitemap indexes **581** verified, high-value URLs:

- **315 Active NASA Technical Standards Tracked**:
  - Agency-wide Technical Standards (`NASA-STD`)
  - Agency-wide Technical Handbooks (`NASA-HDBK`)
  - Center-specific Standards & Specifications (`GSFC-STD`, `MSFC-SPEC`, `JSC-STD`, etc.)
- **252 Direct Master PDF Documents**:
  - Full-text, high-resolution approved engineering standards and handbooks
- **329 Clean HTML Pages**:
  - Detailed metadata pages for each standard
  - Master catalog and technical discipline category landing pages
- **0 Historical Revisions / Cancelled Documents**: 100% excluded to protect search accuracy.

---

## Onyx Web Connector Configuration

In your Onyx Admin Console (**Connectors** → **Web**):

| Field | Configuration |
| :--- | :--- |
| **Connector Name** | `NASA-Standards` |
| **Base URL** | `https://raw.githubusercontent.com/Oht8wooWi8yait9n/standards/main/standards_sitemap.xml` |
| **Scrape Method** | `sitemap` |

Click **Create Connector** to begin indexing the full NASA Technical Standards library into Onyx.

---

## Automated Maintenance & CI/CD

- **GitHub Actions Workflow**: Runs automatically every Sunday at 00:00 UTC (`.github/workflows/update-sitemap.yml`).
- **Safety Threshold**: Validates that at least 500 URLs are collected before writing, preventing accidental blanking of the sitemap.
- **Manual Trigger**: Supports on-demand crawl triggers via GitHub Actions `workflow_dispatch`.
