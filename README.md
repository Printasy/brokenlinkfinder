# Broken Link Checker

A production-ready Python crawler that scans your entire website page by page, checks every link it finds, and generates detailed reports showing which links are broken.

## Why Use This Tool?

Broken links hurt your website in multiple ways:

- **SEO damage** — Search engines like Google penalise sites with broken links, pushing your pages down in search results.
- **Lost visitors** — Users who click a dead link lose trust and leave. Studies show 88% of visitors won't return after a bad experience.
- **Lost revenue** — Broken product links, contact forms, or call-to-action buttons directly cost you business.
- **Professional image** — A site full of 404 errors looks unmaintained and unprofessional to clients and partners.
- **Wasted crawl budget** — Search engine bots waste time on dead links instead of indexing your valuable content.

This tool crawls your site exactly the way a search engine would — following links from page to page — and tells you exactly what's broken and where.

## Features

- **Full website crawl** — Starts from your homepage and follows every internal link automatically
- **Thorough checking** — Checks links in `<a>`, `<link>`, `<script>`, `<img>`, and `<iframe>` tags
- **Smart detection** — Classifies issues as broken (4xx/5xx), timeouts, connection errors, redirects, or blocked
- **Live output** — Reports are written in real-time so you can watch progress as it goes
- **Respects robots.txt** — Follows your site's crawl rules by default
- **4 output formats** — Markdown report, full CSV, broken-only CSV, and JSON summary

## Requirements

- Python 3.11 or higher
- pip (Python package manager)

## Installation

1. Clone this repository:
```bash
git clone https://github.com/Printasy/brokenlinkfinder.git
cd brokenlinkfinder
```

2. Install the dependencies:
```bash
python -m pip install -r requirements.txt
```

## Usage

### Interactive Mode (Recommended)

Simply run the script without arguments and it will ask you everything:

```bash
python linkchecker.py
```

You'll be prompted for:
```
  Start URL (required): https://yourwebsite.com
  Max pages to crawl [100]: 500
  Crawl delay in seconds [0.5]: 0.3
  Request timeout in seconds [30]: 15
  Output directory [./output]: ./my_report
  Include subdomains? [y/N]: n
  Ignore robots.txt? [y/N]: n
```

Press **Enter** to accept any default value shown in brackets.

### Command-Line Mode

For scripting or quick runs, you can pass all parameters directly:

```bash
python linkchecker.py https://yourwebsite.com
```

With custom options:

```bash
python linkchecker.py https://yourwebsite.com --max-pages 500 --delay 0.3 --timeout 15 --output-dir ./my_report
```

### All Options

| Option | Default | Description |
|---|---|---|
| `url` | *(required)* | The website URL to start crawling from |
| `--max-pages` | `100` | Maximum number of pages to crawl |
| `--delay` | `0.5` | Seconds to wait between page requests |
| `--timeout` | `30` | Seconds to wait for a server response |
| `--output-dir` | `./output` | Folder where reports are saved |
| `--include-subdomains` | `off` | Also crawl subdomains (e.g. blog.yoursite.com) |
| `--ignore-robots` | `off` | Ignore the site's robots.txt restrictions |

### Examples

**Quick scan of a small site:**
```bash
python linkchecker.py https://example.com --max-pages 50
```

**Deep scan with all subdomains:**
```bash
python linkchecker.py https://example.com --max-pages 500 --include-subdomains --delay 0.3
```

**Scan a site that blocks crawlers:**
```bash
python linkchecker.py https://example.com --ignore-robots
```

## Output Files

The tool creates 4 files in your output directory:

### 1. `crawl_report.md`
A comprehensive Markdown report you can send directly to a client or developer. Includes:
- Crawl settings used
- Summary statistics
- All broken/error links grouped by error type
- All links grouped by source page
- Complete table of every link checked

### 2. `visited_links.csv`
Full spreadsheet with one row per checked link. Columns include source page, URL, status code, result classification, response time, content type, depth, and timestamp.

### 3. `broken_links_only.csv`
Same format as above, but filtered to only show problematic links — perfect for creating a fix-it task list.

### 4. `summary.json`
Machine-readable statistics for automated monitoring or integration with other tools.

## Link Classifications

| Result | Meaning |
|---|---|
| `OK` | Link works fine (2xx response) |
| `REDIRECT` | Link redirects to another URL |
| `BROKEN_4XX` | Client error — page not found, forbidden, etc. |
| `BROKEN_5XX` | Server error — the server failed to respond properly |
| `TIMEOUT` | Server took too long to respond |
| `CONNECTION_ERROR` | Could not connect to the server at all |
| `INVALID_URL` | The URL itself is malformed |
| `BLOCKED` | Blocked by robots.txt |
| `SKIPPED_NON_HTML` | Not an HTML page (PDF, image, etc.) |

## Live Progress

While the crawler runs, you'll see real-time progress in the terminal:

```
  [  42/500]  depth=3  queue=156   results=2847   https://yoursite.com/about
  [  43/500]  depth=3  queue=155   results=2910   https://yoursite.com/contact
```

The output files are also **written live** — you can open `crawl_report.md` or `summary.json` at any time during the crawl to see results so far.

## Tips

- **Start small** — Try `--max-pages 20` first to see how your site behaves before doing a full scan.
- **Adjust the delay** — If the site is slow or you're getting blocked, increase `--delay` to 1 or 2 seconds.
- **Check robots.txt** — If the crawler is finding 0 pages, your robots.txt may be blocking it. Use `--ignore-robots` to bypass.
- **Watch the live files** — Open `summary.json` during a long crawl to track broken link counts in real-time.

## License

MIT
