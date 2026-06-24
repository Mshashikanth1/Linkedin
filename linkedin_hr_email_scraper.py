"""
LinkedIn HR Email Scraper
Searches LinkedIn for hiring posts, extracts email addresses from post text
and saves them (with context) to hr_emails.csv.

Requirements:
  pip install selenium webdriver-manager

Usage:
  python3 linkedin_hr_email_scraper.py
"""

import re
import csv
import time
import hashlib
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
)
from webdriver_manager.chrome import ChromeDriverManager

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_FILE   = "/Users/shashikantverma/birlaPivot/hr_emails.csv"

# Multiple search queries to cast a wider net
SEARCH_QUERIES = [
    "hiring software engineer email",
    "we are hiring SDE email",
    "recruiting software developer contact",
    "HR hiring engineer apply email",
    "job opening software engineer send resume",
]

DATE_FILTER   = "%5B%22past-week%22%5D"   # past week  (use past-24h for fresher)
SCROLL_PAUSES = 6
SCROLL_DELAY  = 2.0
POST_LIMIT    = 50      # posts to scan per search query
LOOP_INTERVAL = 300     # seconds between full re-scans (5 min)

# ── XPaths ────────────────────────────────────────────────────────────────────
POSTS_CONTAINER_XPATH = (
    '/html/body/div/div[2]/div[2]/div[2]/main'
    '/div/div/div/section/div/div[1]/div'
)
SCROLLABLE_MAIN_XPATH = '/html/body/div/div[2]/div[2]/div[2]/main'
LOAD_MORE_BTN_XPATH   = (
    '//*[@id="workspace"]/div/div/div/section/div/div[1]/div/div/button'
)

# "See more" expand button inside a post (shows full text)
SEE_MORE_REL_XPATH    = './/button[contains(@class,"see-more") or '                          'contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ",'                          '"abcdefghijklmnopqrstuvwxyz"),"see more") or '                          'contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ",'                          '"abcdefghijklmnopqrstuvwxyz"),"show more")]'

# Post author name + headline (to capture poster info alongside email)
AUTHOR_NAME_REL_XPATH  = (
    './/span[contains(@class,"feed-shared-actor__name")] | '
    './/span[contains(@class,"update-components-actor__name")] | '
    './/a[contains(@class,"update-components-actor__meta-link")]//span'
)
POST_TEXT_REL_XPATH    = (
    './/div[contains(@class,"feed-shared-update-v2__description")] | '
    './/div[contains(@class,"update-components-text")] | '
    './/div[contains(@class,"feed-shared-text")]'
)
# ─────────────────────────────────────────────────────────────────────────────

# Catches plain emails, obfuscated [at]/(at), and mailto: links
EMAIL_PATTERN = re.compile(
    r'(?:mailto:)?'                          # optional mailto: prefix
    r'[a-zA-Z0-9._%+\-]{1,64}'             # local part
    r'\s*(?:@|\[at\]|\(at\)|&#64;)\s*'     # @ symbol (various forms)
    r'[a-zA-Z0-9.\-]{1,255}'               # domain
    r'\.'                                   # dot before TLD
    r'[a-zA-Z]{2,10}',                      # TLD (supports .io .co .ai .in etc.)
    re.IGNORECASE,
)


def normalise_email(raw: str) -> str:
    """Convert 'name [at] domain.com' → 'name@domain.com' and lowercase."""
    return re.sub(r'\s*(?:\[at\]|\(at\))\s*', '@', raw, flags=re.IGNORECASE).lower().strip()


def extract_emails(text: str) -> list:
    found = EMAIL_PATTERN.findall(text)
    return [normalise_email(e) for e in found]


def build_search_url(query: str) -> str:
    encoded = query.replace(" ", "%20")
    return (
        f"https://www.linkedin.com/search/results/content/"
        f"?keywords={encoded}&origin=CLUSTER_EXPANSION&datePosted={DATE_FILTER}"
    )


# ── Driver ────────────────────────────────────────────────────────────────────

def build_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-popup-blocking")
    opts.add_experimental_option("detach", True)
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def wait_for_login(driver: webdriver.Chrome) -> None:
    print("\n[!] Please log in to LinkedIn in the browser window.")
    print("    Waiting up to 120 seconds…")
    try:
        WebDriverWait(driver, 120).until(EC.url_contains("linkedin.com/feed"))
        print("[✓] Login detected.\n")
    except TimeoutException:
        print("[!] Timeout — continuing anyway.\n")


# ── Scroll & load-more ────────────────────────────────────────────────────────

def click_load_more(driver: webdriver.Chrome) -> bool:
    try:
        for btn in driver.find_elements(By.XPATH, LOAD_MORE_BTN_XPATH):
            if btn.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.4)
                driver.execute_script("arguments[0].click();", btn)
                print(f'    [+] "Load more" clicked')
                time.sleep(2.5)
                return True
    except Exception:
        pass
    return False


def scroll_and_load(driver: webdriver.Chrome) -> None:
    main_el = None
    try:
        main_el = driver.find_element(By.XPATH, SCROLLABLE_MAIN_XPATH)
    except Exception:
        pass

    for i in range(SCROLL_PAUSES):
        try:
            if main_el:
                driver.execute_script("arguments[0].scrollTop += 1400;", main_el)
            else:
                driver.execute_script("window.scrollBy(0, 1400);")
        except Exception:
            driver.execute_script("window.scrollBy(0, 1400);")
        time.sleep(SCROLL_DELAY)
        print(f"    Scrolled {i + 1}/{SCROLL_PAUSES}")
        click_load_more(driver)


# ── Post parsing ──────────────────────────────────────────────────────────────

def expand_post(driver: webdriver.Chrome, post_el) -> None:
    """
    Try every known selector for the 'See more' / 'show more' button
    and click the first one found so the full post text is revealed.
    """
    see_more_xpaths = [
        # text-based (most reliable across LinkedIn UI versions)
        './/button[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"see more")]',
        './/button[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"show more")]',
        './/button[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"more")]',
        # span inside button
        './/button//span[contains(translate(text(),"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"see more")]',
        # class-based
        './/button[contains(@class,"see-more")]',
        './/button[contains(@class,"inline-show-more-text")]',
        './/a[contains(@class,"see-more")]',
    ]
    for xpath in see_more_xpaths:
        try:
            btn = post_el.find_element(By.XPATH, xpath)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(0.8)
            return
        except (NoSuchElementException, StaleElementReferenceException, Exception):
            continue


def get_post_text(driver: webdriver.Chrome, post_el) -> str:
    """
    Return the fullest possible text from a post element.
    Priority:
      1. JavaScript innerText of the whole post element  ← captures everything
      2. selenium .text of the whole post element
      3. innerText of known text-container sub-elements
    """
    # Layer 1 — JS innerText is the most complete (includes hidden/lazy text)
    try:
        text = driver.execute_script("return arguments[0].innerText;", post_el)
        if text and len(text.strip()) > 20:
            return text.strip()
    except Exception:
        pass

    # Layer 2 — Selenium .text
    try:
        text = post_el.text.strip()
        if text:
            return text
    except Exception:
        pass

    # Layer 3 — specific content divs
    text_xpaths = [
        './/div[contains(@class,"feed-shared-update-v2__description")]',
        './/div[contains(@class,"update-components-text")]',
        './/div[contains(@class,"feed-shared-text")]',
        './/div[contains(@class,"attributed-text-segment-list")]',
        './/span[contains(@class,"break-words")]',
        './/p',
    ]
    collected = []
    for xpath in text_xpaths:
        try:
            els = post_el.find_elements(By.XPATH, xpath)
            for el in els:
                t = el.text.strip()
                if t:
                    collected.append(t)
        except Exception:
            continue
    return "\n".join(collected)


def get_author_name(post_el) -> str:
    for xpath in AUTHOR_NAME_REL_XPATH.split(' | '):
        try:
            el = post_el.find_element(By.XPATH, xpath.strip())
            name = el.text.strip()
            if name:
                return name
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return "Unknown"


def get_post_id(post_el) -> str:
    for attr in ("data-urn", "data-id", "id"):
        try:
            v = post_el.get_attribute(attr)
            if v:
                return v
        except Exception:
            pass
    try:
        return hashlib.md5(post_el.text[:150].encode()).hexdigest()
    except Exception:
        return ""


# ── CSV output ────────────────────────────────────────────────────────────────

def init_csv(path: str) -> None:
    """Write CSV header if the file doesn't exist yet."""
    try:
        with open(path, "x", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["email", "author", "post_snippet", "search_query", "found_at"])
    except FileExistsError:
        pass   # file already has a header


def save_emails(path: str, rows: list) -> None:
    """Append rows to CSV. Each row: (email, author, snippet, query, timestamp)."""
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def load_seen_emails(path: str) -> set:
    """Read already-saved emails so we don't write duplicates across runs."""
    seen = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)   # skip header
            for row in reader:
                if row:
                    seen.add(row[0].lower())
    except FileNotFoundError:
        pass
    return seen


# ── Core scrape ───────────────────────────────────────────────────────────────

def scrape_query(driver: webdriver.Chrome, query: str,
                 seen_post_ids: set, seen_emails: set) -> list:
    """
    Load the search page for `query`, scroll, then scan every post for emails.
    Returns list of new CSV rows found.
    """
    url = build_search_url(query)
    print(f'\n  [→] Query: "{query}"')
    driver.get(url)
    time.sleep(4)
    scroll_and_load(driver)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, POSTS_CONTAINER_XPATH))
        )
    except TimeoutException:
        print("  [!] Posts container not found for this query.")
        return []

    container = driver.find_element(By.XPATH, POSTS_CONTAINER_XPATH)
    posts     = container.find_elements(By.XPATH, "./div")

    print(f"  [i] {len(posts)} post(s) found — scanning…")
    new_rows = []

    for post in posts[:POST_LIMIT]:
        try:
            post_id = get_post_id(post)
            if post_id and post_id in seen_post_ids:
                continue

            if post_id:
                seen_post_ids.add(post_id)

            # Expand truncated post text then read full content
            expand_post(driver, post)

            text   = get_post_text(driver, post)
            emails = extract_emails(text)

            if not emails:
                continue

            author  = get_author_name(post)
            snippet = text[:120].replace("\n", " ")
            ts      = datetime.now().strftime("%Y-%m-%d %H:%M")

            for email in emails:
                if email in seen_emails:
                    continue
                seen_emails.add(email)
                new_rows.append((email, author, snippet, query, ts))
                print(f"    ✉  {email}  ({author})")

        except (StaleElementReferenceException, Exception):
            continue

    return new_rows


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    init_csv(OUTPUT_FILE)
    seen_emails   = load_seen_emails(OUTPUT_FILE)
    seen_post_ids = set()
    cycle         = 0

    print(f"[i] Output file : {OUTPUT_FILE}")
    print(f"[i] Already have {len(seen_emails)} email(s) from previous runs.\n")

    driver = build_driver()

    try:
        print("[→] Opening LinkedIn…")
        driver.get("https://www.linkedin.com/login")
        wait_for_login(driver)

        while True:
            cycle += 1
            print(f"\n{'═'*55}")
            print(f"[→] Cycle {cycle} — scanning {len(SEARCH_QUERIES)} search queries…")

            cycle_new = 0
            for query in SEARCH_QUERIES:
                rows = scrape_query(driver, query, seen_post_ids, seen_emails)
                if rows:
                    save_emails(OUTPUT_FILE, rows)
                    cycle_new += len(rows)

            print(f"\n[✓] Cycle {cycle} complete — {cycle_new} new email(s) saved.")
            print(f"    Total unique emails so far: {len(seen_emails)}")
            print(f"    File: {OUTPUT_FILE}")
            print(f"\n[⏳] Waiting {LOOP_INTERVAL}s before next cycle… (Ctrl+C to stop)")
            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        print("\n[!] Stopped by user.")
    finally:
        print(f"\n[→] Total unique emails collected: {len(seen_emails)}")
        print(f"[→] Saved to: {OUTPUT_FILE}")
        print("[→] Browser left open. Close it manually.")


if __name__ == "__main__":
    main()
