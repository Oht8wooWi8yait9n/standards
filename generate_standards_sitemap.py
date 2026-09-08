#!/usr/bin/env python3
"""
NASA Technical Standards System (NTSS) Sitemap & URL Generator
Crawls https://standards.nasa.gov/ to aggregate all active NASA Technical Standards,
including Agency-wide (NASA-STD, NASA-HDBK) and Center-specific standards (GSFC, MSFC, JSC, etc.),
along with their latest approved full PDF documents.

Strictly enforces the "Latest Revision Only" policy by excluding historical/superseded
revision PDFs, change histories, and inactive documents.
"""

import concurrent.futures
from datetime import datetime, timezone
import os
import re
import sys
import time
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import requests

BASE_URL = "https://standards.nasa.gov"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
}

DISCIPLINE_PAGES = [
    "/NASA-Technical-Standards",
    "/all-standards",
    "/center-specific-standards",
    "/documentation-and-configuration",
    "/systems-engineering-and-integration",
    "/computer-systems-software-information-systems",
    "/human-factors-and-health",
    "/electrical-and-electronics-systems",
    "/structures-mechanical-systems",
    "/materials-and-processes-parts",
    "/systems-and-subsystem-test",
    "/safety-quality-reliability-maintainability",
    "/operations-command-control",
    "/construction-institutional-support",
]

MINIMUM_EXPECTED_URLS = 500
MAX_RETRIES = 4
BACKOFF_BASE = 2
MAX_WORKERS = 6


def fetch_with_retry(session: requests.Session, url: str, method: str = "GET") -> requests.Response | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method.upper() == "HEAD":
                resp = session.head(url, headers=HEADERS, timeout=12, allow_redirects=True)
            else:
                resp = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in [401, 403, 404]:
                return resp
        except Exception:
            pass

        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE ** attempt)
    return None


def get_field_val(html: str, label: str) -> str:
    pattern = (
        r'<div[^>]*class=[\"\'][^\"\']*field__label[^\"\']*[\"\']?>\s*'
        + re.escape(label)
        + r'\s*</div>\s*<div[^>]*class=[\"\'][^\"\']*field__item[^\"\']?>\s*(.*?)\s*</div>'
    )
    m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
    return ""


def main():
    print("=" * 70)
    print(" NASA Technical Standards System (NTSS) Sitemap Generator")
    print(" (Latest Approved Revisions Only - Historical Revisions Excluded)")
    print("=" * 70)

    session = requests.Session()
    session.headers.update(HEADERS)

    standard_paths = set()

    # 1. Discover Agency-wide standards across paginated /all-standards
    print("[*] Collecting Agency-wide standards from /all-standards...")
    page = 0
    while True:
        catalog_url = f"{BASE_URL}/all-standards?page={page}"
        resp = fetch_with_retry(session, catalog_url)
        if not resp or resp.status_code != 200:
            break
        found = re.findall(r'href=[\"\'](/standard/[^\"\']+)[\"\']', resp.text)
        if not found:
            break
        prev_len = len(standard_paths)
        standard_paths.update(found)
        print(f"    Page {page}: found {len(found)} entries ({len(standard_paths)} total so far)")
        if len(standard_paths) == prev_len:
            break
        page += 1

    # 2. Discover Center-specific standards
    print("[*] Collecting Center-specific standards from /center-specific-standards...")
    center_resp = fetch_with_retry(session, f"{BASE_URL}/center-specific-standards")
    if center_resp and center_resp.status_code == 200:
        center_found = re.findall(r'href=[\"\'](/standard/[^\"\']+)[\"\']', center_resp.text)
        standard_paths.update(center_found)
        print(f"    Found {len(center_found)} center-specific standard entries")
    else:
        print("[!] Warning: Could not retrieve center-specific standards list.")

    sorted_paths = sorted(list(standard_paths))
    print(f"[+] Total unique standards discovered: {len(sorted_paths)}")

    # 3. Process each standard page concurrently
    print(f"[*] Crawling {len(sorted_paths)} standards with {MAX_WORKERS} workers...")

    def process_standard(path: str):
        thread_session = requests.Session()
        thread_session.headers.update(HEADERS)
        std_url = f"{BASE_URL}{path}"

        resp = fetch_with_retry(thread_session, std_url)
        if not resp or resp.status_code != 200:
            return path, "FETCH_ERROR", None, set()

        html = resp.text
        doc_no = get_field_val(html, "Document Number") or path.split("/")[-1]
        is_active = get_field_val(html, "Is Active?").upper()

        # Discard inactive / cancelled standards
        if is_active == "INACTIVE" or "CANCEL" in is_active:
            return path, "INACTIVE", doc_no, set()

        urls = set()
        urls.add(std_url)

        # Extract only the current approved public PDF from "PUBLIC: Upload Publicly Available Standard"
        # We explicitly skip "PUBLIC: Document History for Standard" and any paths containing "/Historical/"
        pub_pdf_match = re.search(
            r'PUBLIC:\s*Upload Publicly Available Standard.*?<a\s+[^>]*href=[\"\']([^\"\']+\.pdf[^\s\"\'<>]*)[\"\']',
            html,
            re.DOTALL | re.IGNORECASE,
        )

        pdf_url = None
        if pub_pdf_match:
            raw_pdf = pub_pdf_match.group(1).split("?")[0].split("#")[0]
            if "historical" not in raw_pdf.lower():
                clean_pdf = urljoin(BASE_URL, raw_pdf)
                # Verify PDF is publicly accessible
                p_resp = fetch_with_retry(thread_session, clean_pdf, method="HEAD")
                if p_resp and p_resp.status_code == 200:
                    urls.add(clean_pdf)
                    pdf_url = clean_pdf

        status = "PUBLIC_WITH_PDF" if pdf_url else "PUBLIC_METADATA_ONLY"
        return path, status, doc_no, urls

    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(process_standard, sorted_paths))
    elapsed = time.time() - start_time
    print(f"[+] Completed initial crawl in {elapsed:.1f}s")

    # 4. Sequential retry pass for any transient fetch errors
    failed_items = [r[0] for r in results if r[1] == "FETCH_ERROR"]
    if failed_items:
        print(f"[*] Retrying {len(failed_items)} failed standard(s) sequentially...")
        for path in failed_items:
            res = process_standard(path)
            for idx, r in enumerate(results):
                if r[0] == path:
                    results[idx] = res
                    if res[1] != "FETCH_ERROR":
                        print(f"    [+] Successfully recovered {path}")

    # 5. Aggregate all verified URLs
    all_content_urls = set()

    # Add master discipline and catalog pages
    for d in DISCIPLINE_PAGES:
        all_content_urls.add(f"{BASE_URL}{d}")

    stats = {
        "PUBLIC_WITH_PDF": 0,
        "PUBLIC_METADATA_ONLY": 0,
        "INACTIVE": 0,
        "FETCH_ERROR": 0,
    }

    for path, status, doc_no, urls in results:
        stats[status] = stats.get(status, 0) + 1
        all_content_urls.update(urls)

    sorted_all_urls = sorted(list(all_content_urls))
    pdf_urls = [u for u in sorted_all_urls if u.lower().endswith(".pdf")]
    html_urls = [u for u in sorted_all_urls if not u.lower().endswith(".pdf")]

    print("\n" + "=" * 70)
    print("[+] Summary of Standards Processed:")
    print(f"    - Active Standards with Approved Public PDF: {stats.get('PUBLIC_WITH_PDF', 0)}")
    print(f"    - Active Standards (Restricted/Internal PDF): {stats.get('PUBLIC_METADATA_ONLY', 0)}")
    print(f"    - Inactive / Cancelled Standards Excluded:   {stats.get('INACTIVE', 0)}")
    print(f"    - Fetch Errors:                              {stats.get('FETCH_ERROR', 0)}")
    print(f"\n[+] Total Content URLs Aggregated: {len(sorted_all_urls)}")
    print(f"    - Latest Approved Master PDFs:               {len(pdf_urls)}")
    print(f"    - Standards Landing & Discipline Pages:      {len(html_urls)}")
    print("=" * 70)

    # 6. Safety check threshold
    if len(sorted_all_urls) < MINIMUM_EXPECTED_URLS:
        print(
            f"[!] ERROR: Aggregated URLs ({len(sorted_all_urls)}) is below "
            f"safety threshold ({MINIMUM_EXPECTED_URLS}). Aborting file update."
        )
        sys.exit(1)

    # 7. Write XML Sitemap and URL List
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sitemap_path = os.path.join(script_dir, "standards_sitemap.xml")
    urls_txt_path = os.path.join(script_dir, "standards_urls.txt")

    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for u in sorted_all_urls:
        escaped_url = escape(u)
        if u.lower().endswith(".pdf"):
            priority = "0.9"
            changefreq = "monthly"
        elif u.split("standards.nasa.gov")[-1] in DISCIPLINE_PAGES:
            priority = "0.7"
            changefreq = "weekly"
        else:
            priority = "0.8"
            changefreq = "monthly"

        xml_lines.append("  <url>")
        xml_lines.append(f"    <loc>{escaped_url}</loc>")
        xml_lines.append(f"    <lastmod>{current_date}</lastmod>")
        xml_lines.append(f"    <changefreq>{changefreq}</changefreq>")
        xml_lines.append(f"    <priority>{priority}</priority>")
        xml_lines.append("  </url>")

    xml_lines.append("</urlset>\n")

    xml_content = "\n".join(xml_lines)

    # Verify XML well-formedness before saving
    try:
        ET.fromstring(xml_content.encode("utf-8"))
        print("[+] XML sitemap validated successfully with ElementTree.")
    except ET.ParseError as pe:
        print(f"[!] FATAL: Generated XML failed parsing check: {pe}")
        sys.exit(1)

    with open(sitemap_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    print(f"[+] Successfully wrote: {sitemap_path}")

    with open(urls_txt_path, "w", encoding="utf-8") as f:
        for u in sorted_all_urls:
            f.write(f"{u}\n")
    print(f"[+] Successfully wrote: {urls_txt_path}")


if __name__ == "__main__":
    main()
