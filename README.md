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

3. **Public vs. Restricted Standards & Launchpad SSO Isolation**:
   - **207 Active Standards** have genuinely public, unrestricted PDFs (`/sites/default/files/standards/...`) that are verified accessible without authentication.
   - **45 Active Standards** have their latest PDFs uploaded under `/system/files/tmp/...` on Drupal, which issues an HTTP 302 redirect to **NASA Launchpad SSO** (`auth.launchpad.nasa.gov`).
     - If crawled, Onyx would index the HTML of the Launchpad login page (`Access Launchpad`) instead of the engineering standard.
     - **Solution**: The crawler probes every candidate PDF with HTTP `HEAD` / streaming `GET` and validates both status code and content type. Any link redirecting to Launchpad SSO is **excluded** from `standards_sitemap.xml` and cataloged in [**`sso_locked_standards.md`**](sso_locked_standards.md).
     - *Investigation Note*: These 45 files require further assessment to determine whether the restriction is intentional (e.g., CUI, ITAR, internal distribution) or unintentional (Drupal temporary directory upload misconfiguration).
   - **63 Active Standards** have restricted PDFs (`NASA Internal` or `NASA and NASA Contractors`) with no public link.
   - For both SSO-locked and restricted standards, the crawler indexes the public landing page (providing Title, Scope, Responsible Office, and Keywords), ensuring their existence is searchable without ingesting login screens.

4. **Zero-Churn Sitemap Maintenance**:
   - The crawler preserves existing `<lastmod>` dates from the prior sitemap, only stamping newly added or updated documents with the current date.
   - When no standards change, the sitemap XML remains 100% byte-for-byte identical, eliminating spurious weekly git commits and preventing unnecessary vector re-embedding in Onyx.

---

## Indexed Content Overview

The sitemap indexes **536** verified, high-value URLs:

- **315 Active NASA Technical Standards Tracked**:
  - Agency-wide Technical Standards (`NASA-STD`)
  - Agency-wide Technical Handbooks (`NASA-HDBK`)
  - Center-specific Standards & Specifications (`GSFC-STD`, `MSFC-SPEC`, `JSC-STD`, etc.)
- **207 Direct Master PDF Documents (100% Public & Verified)**:
  - Full-text, high-resolution approved engineering standards and handbooks accessible without credentials.
- **329 Clean HTML Pages**:
  - Detailed metadata pages for each standard
  - Master catalog and technical discipline category landing pages
- **45 SSO-Locked Documents Documented Separately**:
  - Tracked with landing page and target path in [**`sso_locked_standards.md`**](sso_locked_standards.md).
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

- **Weekly Sitemap Synchronization**: Runs automatically every Sunday at 00:00 UTC (`.github/workflows/update-sitemap.yml`) to crawl all active standards, update `standards_sitemap.xml`, `standards_urls.txt`, and regenerate `sso_locked_standards.md`.
- **Daily Revision Watcher (`NASA-STD-3001`)**: Runs Monday through Friday at 12:00 UTC / 8:00 AM EDT (`.github/workflows/daily-standards-watch.yml`) to check for new revision releases, document approval dates, and PDF filename changes for **NASA-STD-3001 Vol 1** and **Vol 2**.
  - Automatically files a GitHub Issue (which triggers instant email notifications to watchers) with before/after revision diffs and direct PDF download links whenever a new revision is released.
- **State Tracking**: `standards_watch.json` tracks known approved revisions to prevent duplicate alert notifications.
- **Safety Threshold**: Validates that at least 450 URLs are collected before writing, preventing accidental blanking of the sitemap.
- **Manual Trigger**: Supports on-demand crawl triggers via GitHub Actions `workflow_dispatch`.
