"""
Moore Czech Republic News Scraper
Scrapes all news articles from https://www.moore-czech.cz/news/
and appends only new articles to a CSV file.
"""

import csv
import os
import time
import logging
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Configuration ─────────────────────────────────────────────────────────────
BASE_URL = "https://www.moore-czech.cz"
NEWS_URL = f"{BASE_URL}/news/"
CSV_FILE = "moore_news.csv"
DELAY_SECONDS = 1.5          # polite delay between requests
REQUEST_TIMEOUT = 30

# CSV columns (in order)
CSV_COLUMNS = [
    "url",
    "title",
    "date_published",
    "date_modified",
    "author",
    "category",
    "excerpt",
    "thumbnail_url",
    "reading_time",
    "full_text",
    "scraped_at",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MooreCzechNewsScraper/1.0; "
        "+https://github.com/your-org/your-repo)"
    ),
    "Accept-Language": "cs,en;q=0.9",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_soup(url: str) -> BeautifulSoup | None:
    """Fetch a URL and return a BeautifulSoup object, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as exc:
        log.warning("Failed to fetch %s: %s", url, exc)
        return None


def load_existing_urls(csv_path: str) -> set[str]:
    """Return the set of URLs already stored in the CSV."""
    if not os.path.exists(csv_path):
        return set()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row["url"] for row in reader if row.get("url")}


def append_rows(csv_path: str, rows: list[dict]) -> None:
    """Append new rows to the CSV, creating the file with a header if needed."""
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


# ── Listing pages ─────────────────────────────────────────────────────────────

def get_all_article_urls() -> list[str]:
    """
    Crawl every paginated page of /news/ and collect article URLs.
    The site uses standard WordPress pagination: /news/page/2/, /news/page/3/ …
    Filters are client-side JavaScript (they don't change the URL), so all
    articles are already present across the paginated listing.
    """
    urls: list[str] = []
    page = 1

    while True:
        if page == 1:
            listing_url = NEWS_URL
        else:
            listing_url = f"{BASE_URL}/news/page/{page}/"

        log.info("Scraping listing page %d → %s", page, listing_url)
        soup = get_soup(listing_url)

        if soup is None:
            log.warning("Could not load listing page %d, stopping.", page)
            break

        # Article cards – each is an <a> that wraps the card block
        # The site uses a standard WP loop; links are <a href="..."> inside
        # the article list area. We identify them by looking at the main
        # content section.
        article_links = []

        # Primary selector: <a> tags inside the news listing grid
        # The listing section contains cards with direct article links.
        content = soup.find("main") or soup.find("div", id="content") or soup
        for a in content.find_all("a", href=True):
            href = a["href"]
            # Skip navigation, filter, and pagination links
            if (
                href.startswith(BASE_URL)
                and "/news/" not in href
                and "/services/" not in href
                and "/industries/" not in href
                and "/about" not in href
                and "/get-in-touch" not in href
                and "/ochrana" not in href
                and "/magazin" not in href
                and href != BASE_URL
                and href != BASE_URL + "/"
            ):
                full = href if href.startswith("http") else urljoin(BASE_URL, href)
                if full not in article_links:
                    article_links.append(full)

        if not article_links:
            log.info("No more articles found on page %d, stopping.", page)
            break

        log.info("  Found %d article links on page %d", len(article_links), page)
        urls.extend(article_links)

        # Check if there is a "next page" pagination link
        next_link = soup.find("a", string=lambda t: t and str(page + 1) == t.strip())
        has_next = next_link is not None or soup.find(
            "a", href=lambda h: h and f"/news/page/{page + 1}/" in h
        )
        if not has_next:
            log.info("No next page found after page %d.", page)
            break

        page += 1
        time.sleep(DELAY_SECONDS)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    log.info("Total unique article URLs collected: %d", len(unique))
    return unique


# ── Article scraping ──────────────────────────────────────────────────────────

def scrape_article(url: str) -> dict | None:
    """
    Scrape a single article page and return a dict matching CSV_COLUMNS.
    Returns None if the page cannot be parsed.
    """
    soup = get_soup(url)
    if soup is None:
        return None

    def meta(name: str) -> str:
        """Extract <meta property="name"> or <meta name="name"> content."""
        tag = soup.find("meta", property=name) or soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else ""

    # ── Title ─────────────────────────────────────────────────────────────────
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else meta("og:title").replace(" - Moore Czech Republic", "")

    # ── Dates ─────────────────────────────────────────────────────────────────
    date_published = meta("article:published_time")
    date_modified  = meta("article:modified_time")

    # Fallback: look for a visible date element
    if not date_published:
        date_tag = soup.find("time") or soup.find(class_=lambda c: c and "date" in c)
        if date_tag:
            date_published = date_tag.get("datetime") or date_tag.get_text(strip=True)

    # ── Author ────────────────────────────────────────────────────────────────
    author = meta("author") or meta("twitter:data1")
    if not author:
        # Look for "Autor XYZ" pattern in the page
        autor_tag = soup.find(string=lambda t: t and "Autor" in t)
        if autor_tag:
            author = autor_tag.replace("Autor", "").strip()

    # ── Category ──────────────────────────────────────────────────────────────
    # Usually shown as a small label above the title (e.g. "Tisková zpráva")
    category = ""
    cat_link = soup.find("a", href=lambda h: h and "/category/" in h)
    if cat_link:
        category = cat_link.get_text(strip=True)

    # ── Excerpt / description ─────────────────────────────────────────────────
    excerpt = meta("og:description")

    # ── Thumbnail ─────────────────────────────────────────────────────────────
    thumbnail_url = meta("og:image")

    # ── Reading time ──────────────────────────────────────────────────────────
    reading_time = meta("twitter:data2")   # e.g. "4 minuty"

    # ── Full text ─────────────────────────────────────────────────────────────
    # Main article body – try common selectors
    body = (
        soup.find("article")
        or soup.find("div", class_=lambda c: c and "entry-content" in c)
        or soup.find("div", class_=lambda c: c and "post-content" in c)
        or soup.find("main")
    )
    if body:
        # Remove nav / footer noise that got pulled in
        for tag in body.find_all(["nav", "footer", "aside", "script", "style"]):
            tag.decompose()
        full_text = body.get_text(separator="\n", strip=True)
        # Trim to a reasonable length
        full_text = full_text[:8000]
    else:
        full_text = ""

    return {
        "url":            url,
        "title":          title,
        "date_published": date_published,
        "date_modified":  date_modified,
        "author":         author,
        "category":       category,
        "excerpt":        excerpt,
        "thumbnail_url":  thumbnail_url,
        "reading_time":   reading_time,
        "full_text":      full_text,
        "scraped_at":     datetime.utcnow().isoformat(),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Moore Czech Republic news scraper starting ===")
    log.info("CSV file: %s", os.path.abspath(CSV_FILE))

    existing_urls = load_existing_urls(CSV_FILE)
    log.info("Articles already in CSV: %d", len(existing_urls))

    all_urls = get_all_article_urls()

    new_urls = [u for u in all_urls if u not in existing_urls]
    log.info("New articles to scrape: %d", len(new_urls))

    if not new_urls:
        log.info("Nothing new to add. Exiting.")
        return

    new_rows: list[dict] = []
    for i, url in enumerate(new_urls, 1):
        log.info("[%d/%d] Scraping %s", i, len(new_urls), url)
        row = scrape_article(url)
        if row:
            new_rows.append(row)
        else:
            log.warning("  Skipped (scraping failed).")
        time.sleep(DELAY_SECONDS)

    if new_rows:
        append_rows(CSV_FILE, new_rows)
        log.info("Appended %d new articles to %s", len(new_rows), CSV_FILE)
    else:
        log.info("No valid rows to append.")

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
