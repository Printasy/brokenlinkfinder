You are a senior Python engineer, technical QA specialist, and web crawler architect.

Your task is to create a production-ready Python 3.11 script that crawls a website from page to page, stays within the same website, and detects error links.

Goal:
Build a crawler that starts from a given URL, visits internal pages, collects all internal links it encounters, checks their result, and produces a document that includes every visited link and its result.

Important:
- Return complete, runnable code.
- Do not give pseudo-code.
- Do not omit implementation details.
- Make reasonable engineering choices without asking follow-up questions.
- Prefer reliability, readability, and maintainability over cleverness.
- Use a single main Python script unless a second file is truly necessary.
- Also provide a minimal requirements.txt.

Functional requirements:
1. The script must accept at least these inputs via CLI arguments:
   - start URL
   - max pages to crawl
   - crawl delay in seconds
   - timeout in seconds
   - output directory
   - optional flag to include subdomains
   - optional flag to ignore robots.txt
2. Crawl only internal links belonging to the same domain by default.
3. Visit pages recursively from page to page using a queue-based crawl strategy.
4. Normalize URLs to avoid duplicate crawling caused by:
   - trailing slashes
   - fragments
   - duplicate query-string variations where reasonable
5. Track:
   - every page visited
   - every discovered link
   - the source page where the link was found
   - HTTP status code
   - final URL after redirects
   - result classification
   - response time
   - content type when available
   - crawl depth
   - timestamp
6. Detect and classify results such as:
   - OK
   - REDIRECT
   - BROKEN_4XX
   - BROKEN_5XX
   - TIMEOUT
   - CONNECTION_ERROR
   - INVALID_URL
   - BLOCKED
   - SKIPPED_NON_HTML
7. For each HTML page visited, extract links from at least:
   - a href
   - link href
   - script src
   - img src
   - iframe src
8. Check internal links robustly:
   - try HEAD first when appropriate
   - fall back to GET if HEAD is not supported or unreliable
   - follow redirects
9. Avoid infinite loops and duplicate checks.
10. Show clear console progress while running.

Output requirements:
Create these output files in the chosen output directory:

1. crawl_report.md
   A readable document that includes:
   - crawl settings used
   - start URL
   - total pages crawled
   - total unique links discovered
   - number of OK links
   - number of redirects
   - number of broken links
   - grouped list of broken/error links
   - grouped list by source page
   - a complete table of all visited/discovered links and their results

2. visited_links.csv
   Must include one row per checked URL with columns such as:
   - source_page
   - discovered_url
   - normalized_url
   - final_url
   - internal_or_external
   - resource_type
   - status_code
   - result
   - response_time_ms
   - depth
   - page_title
   - error_message
   - timestamp

3. broken_links_only.csv
   Only links whose result is not OK.

4. summary.json
   Machine-readable summary stats.

Technical requirements:
- Use Python standard library plus lightweight libraries only where useful.
- Prefer:
  - requests
  - beautifulsoup4
- Use urllib.parse for URL handling.
- Use dataclasses where it improves structure.
- Use argparse for CLI arguments.
- Use csv and json modules for output.
- Include helpful comments and docstrings.
- Handle exceptions cleanly.
- The script must run on Windows, macOS, and Linux.

Quality requirements:
- Keep the code clean and modular.
- Separate responsibilities clearly:
  - crawling
  - URL normalization
  - HTML parsing
  - link checking
  - reporting
- Add sensible defaults.
- Include a custom user-agent string.
- Respect robots.txt by default unless the ignore flag is set.
- Prevent the crawler from leaving scope.
- Skip mailto:, tel:, javascript:, and empty links.

Deliverables format:
Return your answer in this exact order:

1. Brief architecture summary
2. Complete Python script in one code block
3. requirements.txt in one code block
4. Example command-line usage
5. Short explanation of generated output files
6. Optional improvement ideas for future version

Additional implementation detail:
- The Markdown report must be comprehensive and readable enough to send directly to a client or developer.
- Include all visited links and the result of each one in the report.
- If a page cannot be fetched, record it in the report instead of silently failing.
- If a page has no title, leave page_title blank rather than inventing one.
- Keep the script deterministic and practical for real-world websites.

Now produce the full solution.