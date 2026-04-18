#!/usr/bin/env python3
"""
linkchecker.py — Production-ready website link checker and crawler.

Crawls a website from a start URL, discovers internal links page-by-page,
checks their HTTP status, and produces comprehensive reports.

Output files are written LIVE as the crawl progresses — you can open
them at any time to see results so far.

Usage:
    python linkchecker.py https://example.com --max-pages 50 --delay 0.5

Author: LinkChecker Tool
Python: 3.11+
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import (
    urljoin,
    urlparse,
    urlunparse,
    parse_qs,
    urlencode,
    quote,
    unquote,
)
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "1.1.0"
USER_AGENT = f"LinkChecker/{VERSION} (Python/{sys.version_info.major}.{sys.version_info.minor})"

SKIP_SCHEMES = {"mailto", "tel", "javascript", "data", "ftp", "file"}

RESULT_OK = "OK"
RESULT_REDIRECT = "REDIRECT"
RESULT_BROKEN_4XX = "BROKEN_4XX"
RESULT_BROKEN_5XX = "BROKEN_5XX"
RESULT_TIMEOUT = "TIMEOUT"
RESULT_CONNECTION_ERROR = "CONNECTION_ERROR"
RESULT_INVALID_URL = "INVALID_URL"
RESULT_BLOCKED = "BLOCKED"
RESULT_SKIPPED_NON_HTML = "SKIPPED_NON_HTML"

# Resource type tags for discovered links
RESOURCE_ANCHOR = "anchor"
RESOURCE_STYLESHEET = "stylesheet"
RESOURCE_SCRIPT = "script"
RESOURCE_IMAGE = "image"
RESOURCE_IFRAME = "iframe"

# CSV column order
CSV_COLUMNS = [
    "source_page",
    "discovered_url",
    "normalized_url",
    "final_url",
    "internal_or_external",
    "resource_type",
    "status_code",
    "result",
    "response_time_ms",
    "content_type",
    "depth",
    "page_title",
    "error_message",
    "timestamp",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CrawlConfig:
    """Holds all configuration for a crawl session."""

    start_url: str
    max_pages: int = 100
    delay: float = 0.5
    timeout: int = 30
    output_dir: str = "./output"
    include_subdomains: bool = False
    ignore_robots: bool = False

    # Derived at runtime
    start_domain: str = ""
    start_scheme: str = ""

    def __post_init__(self):
        parsed = urlparse(self.start_url)
        self.start_domain = parsed.netloc.lower()
        self.start_scheme = parsed.scheme.lower()


@dataclass
class LinkResult:
    """Stores the check result for a single discovered link."""

    source_page: str
    discovered_url: str
    normalized_url: str
    final_url: str = ""
    internal_or_external: str = "internal"
    resource_type: str = RESOURCE_ANCHOR
    status_code: int = 0
    result: str = ""
    response_time_ms: float = 0.0
    content_type: str = ""
    depth: int = 0
    page_title: str = ""
    error_message: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# URL Normalization
# ---------------------------------------------------------------------------


class URLNormalizer:
    """Normalizes URLs to prevent duplicate crawling."""

    @staticmethod
    def normalize(url: str, base_url: str = "") -> Optional[str]:
        """
        Normalize a URL:
        - Resolve relative URLs against base_url
        - Strip fragments
        - Remove trailing slashes (except root)
        - Sort query parameters
        - Lowercase scheme and host
        """
        if not url or not url.strip():
            return None

        url = url.strip()

        # Skip non-HTTP schemes
        parsed_check = urlparse(url)
        if parsed_check.scheme and parsed_check.scheme.lower() in SKIP_SCHEMES:
            return None

        # Resolve relative URLs
        if base_url:
            url = urljoin(base_url, url)

        parsed = urlparse(url)

        # Must have a valid scheme
        if parsed.scheme.lower() not in ("http", "https"):
            if not parsed.scheme:
                # Try prepending the base scheme
                if base_url:
                    base_parsed = urlparse(base_url)
                    url = f"{base_parsed.scheme}://{url}" if not url.startswith("//") else f"{base_parsed.scheme}:{url}"
                    parsed = urlparse(url)
                else:
                    return None
            else:
                return None

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        if not netloc:
            return None

        # Remove default ports
        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        elif netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]

        # Normalize path
        path = parsed.path
        # Decode and re-encode to normalize percent-encoding
        path = quote(unquote(path), safe="/:@!$&'()*+,;=-._~")
        # Remove trailing slash unless it's the root
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if not path:
            path = "/"

        # Sort query parameters for consistency
        query = parsed.query
        if query:
            params = parse_qs(query, keep_blank_values=True)
            sorted_params = sorted(params.items())
            query = urlencode(sorted_params, doseq=True)

        # Rebuild without fragment
        normalized = urlunparse((scheme, netloc, path, parsed.params, query, ""))
        return normalized

    @staticmethod
    def is_internal(url: str, config: CrawlConfig) -> bool:
        """Check whether a URL belongs to the same domain (or subdomain if enabled)."""
        parsed = urlparse(url)
        url_domain = parsed.netloc.lower()

        # Remove default ports for comparison
        for suffix in (":80", ":443"):
            if url_domain.endswith(suffix):
                url_domain = url_domain[: -len(suffix)]

        start_domain = config.start_domain
        for suffix in (":80", ":443"):
            if start_domain.endswith(suffix):
                start_domain = start_domain[: -len(suffix)]

        if url_domain == start_domain:
            return True

        if config.include_subdomains:
            return url_domain.endswith(f".{start_domain}")

        return False


# ---------------------------------------------------------------------------
# Robots.txt Handler
# ---------------------------------------------------------------------------


class RobotsHandler:
    """Handles robots.txt fetching and checking."""

    def __init__(self, config: CrawlConfig):
        self.config = config
        self._parsers: dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL is allowed by robots.txt (or if robots checking is disabled)."""
        if self.config.ignore_robots:
            return True

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_url not in self._parsers:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                rp.read()
            except Exception:
                # If we can't fetch robots.txt, allow by default
                rp.allow_all = True
            self._parsers[robots_url] = rp

        try:
            return self._parsers[robots_url].can_fetch(USER_AGENT, url)
        except Exception:
            return True


# ---------------------------------------------------------------------------
# HTML Link Extractor
# ---------------------------------------------------------------------------


class LinkExtractor:
    """Extracts links from an HTML document using BeautifulSoup."""

    # Maps tag/attribute pairs to resource types
    TAG_ATTR_MAP = [
        ("a", "href", RESOURCE_ANCHOR),
        ("link", "href", RESOURCE_STYLESHEET),
        ("script", "src", RESOURCE_SCRIPT),
        ("img", "src", RESOURCE_IMAGE),
        ("iframe", "src", RESOURCE_IFRAME),
    ]

    @staticmethod
    def extract(html: str, base_url: str) -> list[tuple[str, str]]:
        """
        Parse HTML and return a list of (raw_url, resource_type) tuples.
        URLs are NOT normalized yet — that is the caller's responsibility.
        """
        results = []
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            # Fallback parser
            soup = BeautifulSoup(html, "html.parser")

        # Handle <base> tag
        base_tag = soup.find("base", href=True)
        if base_tag:
            base_url = urljoin(base_url, base_tag["href"])

        for tag_name, attr_name, resource_type in LinkExtractor.TAG_ATTR_MAP:
            for tag in soup.find_all(tag_name, attrs={attr_name: True}):
                raw = tag.get(attr_name, "").strip()
                if raw:
                    results.append((raw, resource_type))

        return results

    @staticmethod
    def extract_title(html: str) -> str:
        """Extract the <title> text from HTML, or return empty string."""
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            return title_tag.string.strip()
        return ""


# ---------------------------------------------------------------------------
# Link Checker
# ---------------------------------------------------------------------------


class LinkChecker:
    """Checks a URL's HTTP status using HEAD-then-GET strategy."""

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        # Don't follow redirects automatically — we want to track them
        self.session.max_redirects = 10

    def check(self, url: str) -> tuple[int, str, str, float, str, str]:
        """
        Check a URL and return:
            (status_code, result, final_url, response_time_ms, content_type, error_message)
        """
        start_time = time.monotonic()

        try:
            # Try HEAD first for efficiency
            resp = self.session.head(
                url,
                timeout=self.config.timeout,
                allow_redirects=True,
            )

            # Some servers return 405 or 501 for HEAD — fall back to GET
            if resp.status_code in (405, 501):
                resp = self.session.get(
                    url,
                    timeout=self.config.timeout,
                    allow_redirects=True,
                    stream=True,  # Don't download body unless needed
                )
                resp.close()

        except requests.exceptions.Timeout:
            elapsed = (time.monotonic() - start_time) * 1000
            return (0, RESULT_TIMEOUT, url, elapsed, "", "Request timed out")

        except requests.exceptions.ConnectionError as e:
            elapsed = (time.monotonic() - start_time) * 1000
            return (0, RESULT_CONNECTION_ERROR, url, elapsed, "", str(e)[:200])

        except requests.exceptions.TooManyRedirects as e:
            elapsed = (time.monotonic() - start_time) * 1000
            return (0, RESULT_BROKEN_4XX, url, elapsed, "", f"Too many redirects: {e}")

        except requests.exceptions.InvalidURL as e:
            elapsed = (time.monotonic() - start_time) * 1000
            return (0, RESULT_INVALID_URL, url, elapsed, "", str(e)[:200])

        except requests.exceptions.RequestException as e:
            elapsed = (time.monotonic() - start_time) * 1000
            return (0, RESULT_CONNECTION_ERROR, url, elapsed, "", str(e)[:200])

        elapsed = (time.monotonic() - start_time) * 1000
        status = resp.status_code
        final_url = resp.url
        content_type = resp.headers.get("Content-Type", "")

        result = self._classify(status, url, final_url)
        return (status, result, final_url, elapsed, content_type, "")

    @staticmethod
    def _classify(status_code: int, original_url: str, final_url: str) -> str:
        """Classify the result based on status code and redirect behavior."""
        if 200 <= status_code < 300:
            # Check if it was redirected
            if original_url.rstrip("/") != final_url.rstrip("/"):
                return RESULT_REDIRECT
            return RESULT_OK
        elif 300 <= status_code < 400:
            return RESULT_REDIRECT
        elif status_code == 403:
            return RESULT_BLOCKED
        elif 400 <= status_code < 500:
            return RESULT_BROKEN_4XX
        elif 500 <= status_code < 600:
            return RESULT_BROKEN_5XX
        else:
            return RESULT_CONNECTION_ERROR

    def fetch_page(self, url: str) -> tuple[Optional[str], int, str, float, str, str]:
        """
        Fetch a page's full HTML content via GET.
        Returns: (html_content, status_code, final_url, response_time_ms, content_type, error_message)
        Returns html_content=None if the page cannot be fetched or is not HTML.
        """
        start_time = time.monotonic()

        try:
            resp = self.session.get(
                url,
                timeout=self.config.timeout,
                allow_redirects=True,
            )
        except requests.exceptions.Timeout:
            elapsed = (time.monotonic() - start_time) * 1000
            return (None, 0, url, elapsed, "", "Request timed out")
        except requests.exceptions.ConnectionError as e:
            elapsed = (time.monotonic() - start_time) * 1000
            return (None, 0, url, elapsed, "", str(e)[:200])
        except requests.exceptions.RequestException as e:
            elapsed = (time.monotonic() - start_time) * 1000
            return (None, 0, url, elapsed, "", str(e)[:200])

        elapsed = (time.monotonic() - start_time) * 1000
        content_type = resp.headers.get("Content-Type", "")

        # Only parse HTML pages
        if "text/html" not in content_type.lower():
            return (None, resp.status_code, resp.url, elapsed, content_type, "Non-HTML content type")

        if resp.status_code >= 400:
            return (None, resp.status_code, resp.url, elapsed, content_type, f"HTTP {resp.status_code}")

        return (resp.text, resp.status_code, resp.url, elapsed, content_type, "")

    def close(self):
        """Close the HTTP session."""
        self.session.close()


# ---------------------------------------------------------------------------
# Live File Writer — writes results incrementally as they arrive
# ---------------------------------------------------------------------------


class LiveWriter:
    """
    Writes crawl results to output files in real-time.

    - visited_links.csv:    appended immediately per result
    - broken_links_only.csv: appended immediately per broken result
    - summary.json:         rewritten after each page
    - crawl_report.md:      rewritten after each page
    """

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.results: list[LinkResult] = []
        self._csv_all_file = None
        self._csv_all_writer = None
        self._csv_broken_file = None
        self._csv_broken_writer = None

    def open(self):
        """Create the output directory and open CSV files for streaming writes."""
        os.makedirs(self.config.output_dir, exist_ok=True)

        # Open visited_links.csv for appending
        path_all = os.path.join(self.config.output_dir, "visited_links.csv")
        self._csv_all_file = open(path_all, "w", newline="", encoding="utf-8")
        self._csv_all_writer = csv.DictWriter(self._csv_all_file, fieldnames=CSV_COLUMNS)
        self._csv_all_writer.writeheader()
        self._csv_all_file.flush()

        # Open broken_links_only.csv for appending
        path_broken = os.path.join(self.config.output_dir, "broken_links_only.csv")
        self._csv_broken_file = open(path_broken, "w", newline="", encoding="utf-8")
        self._csv_broken_writer = csv.DictWriter(self._csv_broken_file, fieldnames=CSV_COLUMNS)
        self._csv_broken_writer.writeheader()
        self._csv_broken_file.flush()

        # Write initial empty markdown and json
        self._write_json_summary()
        self._write_markdown_report()

    def add_result(self, lr: LinkResult):
        """Add a single result — immediately written to CSV files."""
        self.results.append(lr)
        row = self._lr_to_row(lr)

        # Append to visited_links.csv
        self._csv_all_writer.writerow(row)
        self._csv_all_file.flush()

        # Append to broken_links_only.csv if not OK
        if lr.result not in (RESULT_OK, RESULT_REDIRECT):
            self._csv_broken_writer.writerow(row)
            self._csv_broken_file.flush()

    def update_reports(self):
        """Rewrite the markdown report and JSON summary with current data."""
        self._write_json_summary()
        self._write_markdown_report()

    def close(self):
        """Close all open file handles and write final reports."""
        if self._csv_all_file:
            self._csv_all_file.close()
        if self._csv_broken_file:
            self._csv_broken_file.close()

        # Final write of report and summary
        self._write_json_summary()
        self._write_markdown_report()

    # -- JSON Summary --

    def _write_json_summary(self):
        """Write/overwrite summary.json with current stats."""
        stats = self._compute_stats()
        path = os.path.join(self.config.output_dir, "summary.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

    # -- Markdown Report --

    def _write_markdown_report(self):
        """Write/overwrite crawl_report.md with all current results."""
        stats = self._compute_stats()
        broken = [lr for lr in self.results if lr.result not in (RESULT_OK, RESULT_REDIRECT)]

        # Group broken links by result type
        broken_by_type: dict[str, list[LinkResult]] = {}
        for lr in broken:
            broken_by_type.setdefault(lr.result, []).append(lr)

        # Group all results by source page
        by_source: dict[str, list[LinkResult]] = {}
        for lr in self.results:
            by_source.setdefault(lr.source_page, []).append(lr)

        lines = []
        lines.append("# Link Checker — Crawl Report")
        lines.append("")
        lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append(f"**Status:** {'⏳ Crawl in progress...' if not stats.get('completed') else '✅ Crawl completed'}")
        lines.append("")

        # -- Settings --
        lines.append("## Crawl Settings")
        lines.append("")
        lines.append("| Setting | Value |")
        lines.append("|---|---|")
        lines.append(f"| Start URL | `{self.config.start_url}` |")
        lines.append(f"| Max Pages | {self.config.max_pages} |")
        lines.append(f"| Crawl Delay | {self.config.delay}s |")
        lines.append(f"| Timeout | {self.config.timeout}s |")
        lines.append(f"| Include Subdomains | {self.config.include_subdomains} |")
        lines.append(f"| Ignore robots.txt | {self.config.ignore_robots} |")
        lines.append("")

        # -- Summary Stats --
        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|---|---|")
        lines.append(f"| Total link results | {stats['total_links']} |")
        lines.append(f"| Unique URLs checked | {stats['unique_urls']} |")
        lines.append(f"| OK | {stats['ok']} |")
        lines.append(f"| Redirects | {stats['redirects']} |")
        lines.append(f"| Broken (4xx) | {stats['broken_4xx']} |")
        lines.append(f"| Broken (5xx) | {stats['broken_5xx']} |")
        lines.append(f"| Timeouts | {stats['timeouts']} |")
        lines.append(f"| Connection errors | {stats['connection_errors']} |")
        lines.append(f"| Invalid URLs | {stats['invalid_urls']} |")
        lines.append(f"| Blocked (robots.txt) | {stats['blocked']} |")
        lines.append(f"| Skipped (non-HTML) | {stats['skipped_non_html']} |")
        lines.append(f"| **Total broken/error** | **{stats['total_broken']}** |")
        lines.append("")

        # -- Broken/Error Links Grouped by Type --
        if broken:
            lines.append("## Broken & Error Links")
            lines.append("")
            for result_type, items in sorted(broken_by_type.items()):
                lines.append(f"### {result_type} ({len(items)})")
                lines.append("")
                lines.append("| URL | Status | Source Page | Error |")
                lines.append("|---|---|---|---|")
                for lr in items:
                    url_display = self._md_escape(lr.normalized_url)
                    source_display = self._md_escape(lr.source_page)
                    error_display = self._md_escape(lr.error_message) if lr.error_message else "—"
                    lines.append(f"| {url_display} | {lr.status_code or '—'} | {source_display} | {error_display} |")
                lines.append("")
        else:
            lines.append("## Broken & Error Links")
            lines.append("")
            lines.append("✅ **No broken or error links found!**")
            lines.append("")

        # -- Grouped by Source Page --
        lines.append("## Links by Source Page")
        lines.append("")
        for source, items in sorted(by_source.items()):
            lines.append(f"### `{source}`")
            lines.append("")
            lines.append("| URL | Type | Status | Result |")
            lines.append("|---|---|---|---|")
            for lr in items:
                url_display = self._md_escape(lr.normalized_url)
                lines.append(f"| {url_display} | {lr.resource_type} | {lr.status_code or '—'} | {lr.result} |")
            lines.append("")

        # -- Complete Link Table --
        lines.append("## Complete Link Table")
        lines.append("")
        lines.append("| # | URL | Status | Result | Source | Response (ms) | Depth |")
        lines.append("|---|---|---|---|---|---|---|")
        for idx, lr in enumerate(self.results, 1):
            url_display = self._md_escape(lr.normalized_url)
            source_display = self._md_escape(lr.source_page)
            lines.append(
                f"| {idx} | {url_display} | {lr.status_code or '—'} | {lr.result} | "
                f"{source_display} | {lr.response_time_ms:.0f} | {lr.depth} |"
            )
        lines.append("")

        # Write file
        path = os.path.join(self.config.output_dir, "crawl_report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # -- Helpers --

    def _compute_stats(self) -> dict:
        """Compute summary statistics."""
        unique_urls = set()
        counts: dict[str, int] = {}
        for lr in self.results:
            unique_urls.add(lr.normalized_url)
            counts[lr.result] = counts.get(lr.result, 0) + 1

        total_broken = sum(
            counts.get(r, 0)
            for r in (
                RESULT_BROKEN_4XX,
                RESULT_BROKEN_5XX,
                RESULT_TIMEOUT,
                RESULT_CONNECTION_ERROR,
                RESULT_INVALID_URL,
                RESULT_BLOCKED,
                RESULT_SKIPPED_NON_HTML,
            )
        )

        return {
            "start_url": self.config.start_url,
            "crawl_timestamp": datetime.now(timezone.utc).isoformat(),
            "total_links": len(self.results),
            "unique_urls": len(unique_urls),
            "ok": counts.get(RESULT_OK, 0),
            "redirects": counts.get(RESULT_REDIRECT, 0),
            "broken_4xx": counts.get(RESULT_BROKEN_4XX, 0),
            "broken_5xx": counts.get(RESULT_BROKEN_5XX, 0),
            "timeouts": counts.get(RESULT_TIMEOUT, 0),
            "connection_errors": counts.get(RESULT_CONNECTION_ERROR, 0),
            "invalid_urls": counts.get(RESULT_INVALID_URL, 0),
            "blocked": counts.get(RESULT_BLOCKED, 0),
            "skipped_non_html": counts.get(RESULT_SKIPPED_NON_HTML, 0),
            "total_broken": total_broken,
        }

    @staticmethod
    def _lr_to_row(lr: LinkResult) -> dict:
        """Convert a LinkResult to a CSV row dict."""
        return {col: getattr(lr, col, "") for col in CSV_COLUMNS}

    @staticmethod
    def _md_escape(text: str) -> str:
        """Escape pipe characters for Markdown tables."""
        return text.replace("|", "\\|") if text else ""


# ---------------------------------------------------------------------------
# Crawler Engine
# ---------------------------------------------------------------------------


class Crawler:
    """BFS queue-based crawler engine with live output."""

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.normalizer = URLNormalizer()
        self.robots = RobotsHandler(config)
        self.extractor = LinkExtractor()
        self.checker = LinkChecker(config)
        self.writer = LiveWriter(config)

        # Crawl state
        self.queue: deque[tuple[str, int]] = deque()  # (url, depth)
        self.crawled_urls: set[str] = set()  # pages we've fully fetched and parsed
        self.checked_urls: set[str] = set()  # links we've already checked
        self.pages_crawled = 0

    def run(self) -> list[LinkResult]:
        """Execute the crawl and return all link results."""
        self._print(f"\n{'=' * 70}")
        self._print(f"  LinkChecker v{VERSION}")
        self._print(f"{'=' * 70}")
        self._print(f"  Start URL:          {self.config.start_url}")
        self._print(f"  Max pages:          {self.config.max_pages}")
        self._print(f"  Crawl delay:        {self.config.delay}s")
        self._print(f"  Timeout:            {self.config.timeout}s")
        self._print(f"  Include subdomains: {self.config.include_subdomains}")
        self._print(f"  Ignore robots.txt:  {self.config.ignore_robots}")
        self._print(f"  Output directory:   {self.config.output_dir}")
        self._print(f"{'=' * 70}\n")

        # Normalize and enqueue the start URL
        start_normalized = self.normalizer.normalize(self.config.start_url)
        if not start_normalized:
            self._print("[ERROR] Invalid start URL.")
            return []

        # Open live writer — creates output dir and CSV files immediately
        self.writer.open()
        self._print(f"  [LIVE] Output files created in: {os.path.abspath(self.config.output_dir)}")
        self._print(f"     You can open them now to watch live results.\n")

        self.queue.append((start_normalized, 0))
        crawl_start = time.monotonic()

        while self.queue and self.pages_crawled < self.config.max_pages:
            url, depth = self.queue.popleft()

            if url in self.crawled_urls:
                continue

            # Robots.txt check
            if not self.robots.is_allowed(url):
                lr = LinkResult(
                    source_page=url,
                    discovered_url=url,
                    normalized_url=url,
                    final_url="",
                    internal_or_external="internal",
                    resource_type=RESOURCE_ANCHOR,
                    status_code=0,
                    result=RESULT_BLOCKED,
                    response_time_ms=0,
                    content_type="",
                    depth=depth,
                    page_title="",
                    error_message="Blocked by robots.txt",
                )
                self.writer.add_result(lr)
                continue

            self.crawled_urls.add(url)
            self.pages_crawled += 1

            # Progress display
            self._print_progress(url, depth)

            # Fetch page
            html, status_code, final_url, resp_time, content_type, error_msg = (
                self.checker.fetch_page(url)
            )

            page_title = ""

            if html is None:
                # Page could not be fetched or is not HTML — record it
                result_class = self._classify_fetch_error(status_code, error_msg)
                lr = LinkResult(
                    source_page="(start)" if depth == 0 else url,
                    discovered_url=url,
                    normalized_url=url,
                    final_url=final_url,
                    internal_or_external="internal",
                    resource_type=RESOURCE_ANCHOR,
                    status_code=status_code,
                    result=result_class,
                    response_time_ms=round(resp_time, 1),
                    content_type=content_type,
                    depth=depth,
                    page_title="",
                    error_message=error_msg,
                )
                self.writer.add_result(lr)
                # Update reports after each page
                self.writer.update_reports()
                continue

            page_title = self.extractor.extract_title(html)

            # Record the page itself as visited
            page_result = LinkChecker._classify(status_code, url, final_url)
            lr = LinkResult(
                source_page="(start)" if depth == 0 else url,
                discovered_url=url,
                normalized_url=url,
                final_url=final_url,
                internal_or_external="internal",
                resource_type=RESOURCE_ANCHOR,
                status_code=status_code,
                result=page_result,
                response_time_ms=round(resp_time, 1),
                content_type=content_type,
                depth=depth,
                page_title=page_title,
                error_message="",
            )
            self.writer.add_result(lr)

            # Extract links from this page
            discovered_links = self.extractor.extract(html, final_url)

            for raw_url, resource_type in discovered_links:
                normalized = self.normalizer.normalize(raw_url, base_url=final_url)
                if normalized is None:
                    continue

                is_internal = self.normalizer.is_internal(normalized, self.config)

                # Create a dedup key per (source, normalized_url)
                dedup_key = (url, normalized)
                if dedup_key in self.checked_urls:
                    continue
                self.checked_urls.add(dedup_key)

                # Check the link
                (
                    link_status,
                    link_result,
                    link_final_url,
                    link_time,
                    link_ct,
                    link_err,
                ) = self.checker.check(normalized)

                link_lr = LinkResult(
                    source_page=url,
                    discovered_url=raw_url,
                    normalized_url=normalized,
                    final_url=link_final_url,
                    internal_or_external="internal" if is_internal else "external",
                    resource_type=resource_type,
                    status_code=link_status,
                    result=link_result,
                    response_time_ms=round(link_time, 1),
                    content_type=link_ct,
                    depth=depth + 1,
                    page_title="",
                    error_message=link_err,
                )
                self.writer.add_result(link_lr)

                # Enqueue internal HTML links for further crawling
                if (
                    is_internal
                    and normalized not in self.crawled_urls
                    and resource_type == RESOURCE_ANCHOR
                    and link_result in (RESULT_OK, RESULT_REDIRECT)
                    and ("text/html" in link_ct.lower() if link_ct else True)
                ):
                    self.queue.append((normalized, depth + 1))

            # Update markdown report and JSON summary after each page
            self.writer.update_reports()

            # Crawl delay
            if self.queue:
                time.sleep(self.config.delay)

        crawl_elapsed = time.monotonic() - crawl_start

        # Close writer — writes final reports
        self.writer.close()
        self.checker.close()

        self._print(f"\n{'=' * 70}")
        self._print(f"  Crawl completed in {crawl_elapsed:.1f}s")
        self._print(f"  Pages crawled: {self.pages_crawled}")
        self._print(f"  Total link results: {len(self.writer.results)}")
        self._print(f"{'=' * 70}")

        self._print(f"\n  Reports saved to: {os.path.abspath(self.config.output_dir)}")
        self._print(f"    - crawl_report.md")
        self._print(f"    - visited_links.csv")
        self._print(f"    - broken_links_only.csv")
        self._print(f"    - summary.json")

        # Final summary to console
        stats = self.writer._compute_stats()
        self._print(f"\n  Summary:")
        self._print(f"    OK links:      {stats['ok']}")
        self._print(f"    Redirects:     {stats['redirects']}")
        self._print(f"    Broken/errors: {stats['total_broken']}")
        self._print(f"\n  Done.\n")

        return self.writer.results

    @staticmethod
    def _classify_fetch_error(status_code: int, error_msg: str) -> str:
        """Classify a fetch error."""
        if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
            return RESULT_TIMEOUT
        if status_code == 0:
            return RESULT_CONNECTION_ERROR
        if "non-html" in error_msg.lower():
            return RESULT_SKIPPED_NON_HTML
        if 400 <= status_code < 500:
            return RESULT_BROKEN_4XX
        if 500 <= status_code < 600:
            return RESULT_BROKEN_5XX
        return RESULT_CONNECTION_ERROR

    def _print_progress(self, url: str, depth: int):
        """Print crawl progress to the console."""
        truncated = url if len(url) <= 60 else url[:57] + "..."
        self._print(
            f"  [{self.pages_crawled:>4}/{self.config.max_pages}]  "
            f"depth={depth}  "
            f"queue={len(self.queue):<4}  "
            f"results={len(self.writer.results):<5}  "
            f"{truncated}"
        )

    @staticmethod
    def _print(msg: str):
        """Print with immediate flush so output is visible in real-time."""
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# CLI & Main
# ---------------------------------------------------------------------------


def _prompt(label: str, default: str = "") -> str:
    """Prompt the user for input, showing a default value."""
    suffix = f" [{default}]: " if default else ": "
    value = input(f"  {label}{suffix}").strip()
    return value if value else default


def _prompt_bool(label: str, default: bool = False) -> bool:
    """Prompt the user for a yes/no answer."""
    hint = "Y/n" if default else "y/N"
    value = input(f"  {label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "1", "true")


def interactive_config() -> CrawlConfig:
    """Interactively ask the user for crawl parameters."""
    print(f"\n{'=' * 70}")
    print(f"  LinkChecker v{VERSION} — Interactive Setup")
    print(f"{'=' * 70}")
    print(f"  Press Enter to accept the default value shown in [brackets].\n")

    url = _prompt("Start URL (required)")
    while not url:
        print("    URL is required.")
        url = _prompt("Start URL (required)")

    max_pages = int(_prompt("Max pages to crawl", "100"))
    delay = float(_prompt("Crawl delay in seconds", "0.5"))
    timeout = int(_prompt("Request timeout in seconds", "30"))
    output_dir = _prompt("Output directory", "./output")
    include_subdomains = _prompt_bool("Include subdomains?", False)
    ignore_robots = _prompt_bool("Ignore robots.txt?", False)

    # Ensure the URL has a scheme
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    print()
    return CrawlConfig(
        start_url=url,
        max_pages=max_pages,
        delay=delay,
        timeout=timeout,
        output_dir=output_dir,
        include_subdomains=include_subdomains,
        ignore_robots=ignore_robots,
    )


def parse_args() -> CrawlConfig:
    """Parse command-line arguments and return a CrawlConfig."""
    parser = argparse.ArgumentParser(
        prog="linkchecker",
        description="Crawl a website and check all internal links for errors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python linkchecker.py https://example.com
  python linkchecker.py https://example.com --max-pages 200 --delay 1
  python linkchecker.py https://example.com --include-subdomains --ignore-robots
  python linkchecker.py https://example.com --output-dir ./reports --timeout 15
        """,
    )

    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="The start URL to begin crawling from.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum number of pages to crawl (default: 100).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between page crawls (default: 0.5).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP request timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory to save output reports (default: ./output).",
    )
    parser.add_argument(
        "--include-subdomains",
        action="store_true",
        default=False,
        help="Also crawl subdomains of the start domain.",
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        default=False,
        help="Ignore robots.txt restrictions.",
    )

    args = parser.parse_args()

    # If no URL provided, switch to interactive mode
    if args.url is None:
        return interactive_config()

    # Ensure the URL has a scheme
    url = args.url
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    return CrawlConfig(
        start_url=url,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
        output_dir=args.output_dir,
        include_subdomains=args.include_subdomains,
        ignore_robots=args.ignore_robots,
    )


def main():
    """Main entry point."""
    config = parse_args()
    crawler = Crawler(config)
    crawler.run()


if __name__ == "__main__":
    main()
