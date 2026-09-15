#!/usr/bin/env python3
"""
NASA Technical Standards System (NTSS) Watcher
Monitors critical NASA Technical Standards (e.g., NASA-STD-3001 Vol 1 and Vol 2)
for revision updates, document date changes, and new approved public PDF filenames.

If changes are detected:
  - Formats an alert report (alert_summary.md) for GitHub Issues / email notifications.
  - Updates standards_watch.json state file.
  - Sets GITHUB_OUTPUT changes_detected=true.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
from urllib.parse import unquote, urljoin
import requests

BASE_URL = "https://standards.nasa.gov"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DEFAULT_WATCH_CONFIG = {
    "NASA-STD-3001_VOL_1": {
        "page_url": f"{BASE_URL}/standard/NASA/NASA-STD-3001_VOL_1",
    },
    "NASA-STD-3001_VOL_2": {
        "page_url": f"{BASE_URL}/standard/NASA/NASA-STD-3001_VOL_2",
    },
}


def fetch_standard_metadata(page_url: str, session: requests.Session) -> dict | None:
    """Fetch and parse live metadata and public PDF info for a standard."""
    for attempt in range(1, 4):
        try:
            resp = session.get(page_url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                break
        except Exception as e:
            if attempt == 3:
                print(f"[!] Error fetching {page_url}: {e}")
                return None
            time.sleep(2 * attempt)
    else:
        return None

    # Title extraction
    title_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
    title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else ""

    # Field extractor helper
    def extract_field(label: str) -> str:
        pattern = (
            r'<div[^>]*class=[\"\'][^\"\']*field__label[^\"\']*[\"\']?>\s*'
            + re.escape(label)
            + r'\s*</div>\s*<div[^>]*class=[\"\'][^\"\']*field__item[^\"\']*[\"\']?>\s*(.*?)\s*</div>'
        )
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            return re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
        return ""

    doc_no = extract_field("Document Number")
    version = extract_field("Version")
    doc_date = extract_field("Document Date")

    # Extract public PDF link
    pub_pdf_match = re.search(
        r'PUBLIC:\s*Upload Publicly Available Standard.*?<a\s+[^>]*href=[\"\']([^\"\']+\.pdf[^\s\"\'<>]*)[\"\']',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    pdf_url = ""
    pdf_filename = ""
    if pub_pdf_match:
        raw_pdf = pub_pdf_match.group(1).split("?")[0].split("#")[0]
        if "historical" not in raw_pdf.lower():
            pdf_url = urljoin(BASE_URL, raw_pdf)
            pdf_filename = unquote(os.path.basename(raw_pdf))

    return {
        "title": title,
        "document_number": doc_no,
        "version": version,
        "document_date": doc_date,
        "pdf_filename": pdf_filename,
        "pdf_url": pdf_url,
        "page_url": page_url,
    }


def main():
    parser = argparse.ArgumentParser(description="Check watched NASA standards for revisions.")
    parser.add_argument("--dry-run", action="store_true", help="Check without updating state file")
    parser.add_argument("--output-alert", default="alert_summary.md", help="Path to write alert markdown")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    state_file = os.path.join(script_dir, "standards_watch.json")

    # Load existing state
    current_state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                current_state = json.load(f)
        except Exception as e:
            print(f"[!] Warning: Could not parse {state_file}: {e}")

    session = requests.Session()
    session.headers.update(HEADERS)

    # Watch list: use existing keys or fallback defaults
    watch_keys = list(current_state.keys()) if current_state else list(DEFAULT_WATCH_CONFIG.keys())

    print("=" * 70)
    print(" NASA Technical Standards Watcher")
    print(f" Checking {len(watch_keys)} watched standard(s) at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    changes = []
    updated_state = dict(current_state)

    for key in watch_keys:
        page_url = current_state.get(key, {}).get("page_url") or DEFAULT_WATCH_CONFIG.get(key, {}).get("page_url")
        if not page_url:
            page_url = f"{BASE_URL}/standard/NASA/{key}"

        print(f"[*] Checking {key} ({page_url})...")
        live_meta = fetch_standard_metadata(page_url, session)

        if not live_meta:
            print(f"    [!] Failed to fetch live metadata for {key}. Skipping.")
            continue

        prev_meta = current_state.get(key, {})
        has_changed = False
        diff_details = {}

        for field in ["version", "document_date", "pdf_filename", "pdf_url"]:
            old_val = prev_meta.get(field, "")
            new_val = live_meta.get(field, "")
            if old_val and old_val != new_val:
                has_changed = True
                diff_details[field] = {"old": old_val, "new": new_val}

        if has_changed:
            print(f"    [!] REVISION / FILENAME CHANGE DETECTED FOR {key}!")
            for f, vals in diff_details.items():
                print(f"        - {f}: '{vals['old']}' -> '{vals['new']}'")

            changes.append({
                "key": key,
                "title": live_meta["title"],
                "page_url": page_url,
                "diffs": diff_details,
                "live": live_meta,
                "prev": prev_meta,
            })
            updated_state[key] = live_meta
        else:
            print(f"    [+] Current: Rev {live_meta['version']} ({live_meta['pdf_filename']}) - No change.")
            # Keep populated if it was missing initial data
            if not prev_meta:
                updated_state[key] = live_meta

    print("=" * 70)

    # Handle results
    if changes:
        print(f"[!] {len(changes)} standard(s) have new revisions/updates!")

        # Format markdown alert
        md_lines = [
            "# 🚨 NASA Technical Standard Revision Update Detected",
            "",
            f"> **Date of Detection**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            ">",
            "> One or more watched NASA Technical Standards have been updated on `standards.nasa.gov` with a new approved revision, document date, or PDF filename.",
            "",
            "## Summary of Changes",
            "",
            "| Standard | Field | Previous Value | New Approved Value |",
            "| :--- | :--- | :--- | :--- |",
        ]

        for chg in changes:
            key = chg["key"]
            for field, diff in chg["diffs"].items():
                field_label = field.replace("_", " ").title()
                md_lines.append(f"| **{key}** | {field_label} | `{diff['old']}` | **`{diff['new']}`** |")

        md_lines.extend([
            "",
            "## Document Details & Download Links",
            "",
        ])

        for chg in changes:
            live = chg["live"]
            md_lines.extend([
                f"### {live.get('document_number', chg['key'])}: {live.get('title', '')}",
                f"- **Approved Revision**: **Rev {live.get('version', 'N/A')}**",
                f"- **Approval Date**: {live.get('document_date', 'N/A')}",
                f"- **Standard Portal Page**: [{live.get('page_url')}]({live.get('page_url')})",
                f"- **Direct Full-Text PDF**: [{live.get('pdf_filename')}]({live.get('pdf_url')})",
                "",
            ])

        md_lines.extend([
            "---",
            "*Automated alert generated by the NASA Standards Scraper Daily Watcher.*",
        ])

        alert_content = "\n".join(md_lines) + "\n"

        # Write alert markdown
        alert_path = os.path.join(script_dir, args.output_alert)
        with open(alert_path, "w", encoding="utf-8") as f:
            f.write(alert_content)
        print(f"[+] Alert summary written to {alert_path}")

        # Update state file if not dry run
        if not args.dry_run:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(updated_state, f, indent=2)
            print(f"[+] Updated state saved to {state_file}")

        # Export GITHUB_OUTPUT
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write("changes_detected=true\n")
                f.write(f"alert_file={alert_path}\n")
                f.write(f"changed_count={len(changes)}\n")
    else:
        print("[+] All watched standards are up to date. No action required.")
        # If state file was empty, initialize it
        if not os.path.exists(state_file) and not args.dry_run:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(updated_state, f, indent=2)
            print(f"[+] Initialized state saved to {state_file}")

        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write("changes_detected=false\n")


if __name__ == "__main__":
    main()
