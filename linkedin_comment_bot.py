"""
LinkedIn SDE Hiring Comment Bot — endless loop, deduplication, duplicate-comment cleanup.

Requirements:
  pip install selenium webdriver-manager

Usage:
  python3 linkedin_comment_bot.py
"""

import time
import hashlib
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
    ElementClickInterceptedException,
)
from webdriver_manager.chrome import ChromeDriverManager

# ── Config ────────────────────────────────────────────────────────────────────
LINKEDIN_URL  = (
    "https://www.linkedin.com/search/results/content/"
    "?keywords=sde%20hiring&origin=CLUSTER_EXPANSION&datePosted=%5B%22past-24h%22%5D"
)
COMMENT_TEXT  = "interested"
MY_NAME       = "shashi"     # used to detect your own comments
SCROLL_PAUSES = 8
SCROLL_DELAY  = 2.0
ACTION_DELAY  = 1.5
POST_LIMIT    = 100
LOOP_INTERVAL = 120          # seconds between cycles

# ── XPaths ────────────────────────────────────────────────────────────────────
POSTS_CONTAINER_XPATH = (
    '/html/body/div/div[2]/div[2]/div[2]/main'
    '/div/div/div/section/div/div[1]/div'
)
SCROLLABLE_MAIN_XPATH = '/html/body/div/div[2]/div[2]/div[2]/main'

LOAD_MORE_BTN_XPATH     = '//*[@id="workspace"]/div/div/div/section/div/div[1]/div/div/button'
LOAD_MORE_BTN_ALT_XPATH = (
    '/html/body/div/div[2]/div[2]/div[2]/main'
    '/div/div/div/section/div/div[1]/div/div/button'
)

# Relative to each post div
COMMENT_BTN_REL_XPATH   = './div/div/div/div[1]/div/div[3]/button[1]'
SUBMIT_BTN_REL_XPATH    = './div/div/div/div[3]/div/div/div/div/div[3]/div[2]/button'
COMMENT_INPUT_REL_XPATH = './/div[@contenteditable="true"]'

# Comment author block (from your DOM inspection)
# //*[@id="workspace"]/.../div[4]/div[2]/div[2]/div/div/div/div/div[2]/div[1]/div/a/div
# The "• You" span is the most reliable indicator that a comment is yours
MY_COMMENT_INDICATOR_XPATH = (
    './div/div/div/div[4]/div[2]/div[2]/div/div/div/div/div[2]/div[1]/div/a/div'
)

# Three-dot overflow button on a comment  →  id="overflow-web-ios-small"
OVERFLOW_BTN_XPATH = './/*[@id="overflow-web-ios-small"]'

# Delete menu that appears after clicking the three-dot
DELETE_MENU_XPATH  = '/html/body/div[2]/div/div'
DELETE_ITEM_XPATH  = (
    '/html/body/div[2]/div/div//*[@role="menuitem" and '
    'contains(translate(.,"DELETE","delete"),"delete")]'
)
# ─────────────────────────────────────────────────────────────────────────────


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
        print("[!] Login not detected — continuing anyway.\n")


def get_post_id(post_element) -> str:
    for attr in ("data-urn", "data-id", "id"):
        try:
            val = post_element.get_attribute(attr)
            if val:
                return val
        except Exception:
            pass
    try:
        return hashlib.md5(post_element.text[:200].encode()).hexdigest()
    except Exception:
        return ""


# ── Scroll & load-more ────────────────────────────────────────────────────────

def click_load_more(driver: webdriver.Chrome) -> bool:
    for xpath in (LOAD_MORE_BTN_XPATH, LOAD_MORE_BTN_ALT_XPATH):
        try:
            for btn in driver.find_elements(By.XPATH, xpath):
                if btn.is_displayed():
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", btn
                    )
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", btn)
                    print(f'    [+] "Load more" clicked  ("{btn.text.strip()}")')
                    time.sleep(2.5)
                    return True
        except Exception:
            continue
    return False


def scroll_to_load_posts(driver: webdriver.Chrome) -> None:
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


# ── Comment detection ─────────────────────────────────────────────────────────

def _is_my_comment(comment_el) -> bool:
    """
    Return True if this comment block belongs to the logged-in user (me).
    Detection layers:
      1. '• You' text anywhere in the block  ← most reliable (LinkedIn adds it)
      2. Author name element contains MY_NAME
      3. Specific author div XPath from your DOM inspection
    """
    try:
        text = comment_el.text
        # Layer 1 — LinkedIn's own "• You" marker
        if "• You" in text or "• you" in text.lower():
            return True

        # Layer 2 — author name contains MY_NAME
        for xpath in [
            './/span[contains(@class,"_4ab588a1")]',           # class from your DOM
            './/span[contains(@class,"comments-post-meta__name")]',
            './/a[contains(@class,"app-aware-link")]//span',
            MY_COMMENT_INDICATOR_XPATH,
        ]:
            try:
                el = comment_el.find_element(By.XPATH, xpath)
                if MY_NAME.lower() in el.text.lower():
                    return True
            except NoSuchElementException:
                continue

        # Layer 3 — fallback: MY_NAME anywhere in block
        if MY_NAME.lower() in text.lower():
            return True

    except (StaleElementReferenceException, Exception):
        pass
    return False


def find_my_comments(post_element) -> list:
    """Return list of comment elements on this post that belong to me."""
    mine = []
    try:
        # LinkedIn wraps each comment in a div with these patterns
        candidates = post_element.find_elements(
            By.XPATH,
            './/div[contains(@class,"comments-comment-item") or '
            'contains(@class,"comments-comment-entity") or '
            'contains(@class,"comments-comment-list__comment-item")]'
        )
        for c in candidates:
            if _is_my_comment(c):
                mine.append(c)
    except (NoSuchElementException, StaleElementReferenceException):
        pass
    return mine


def already_commented(post_element) -> bool:
    return len(find_my_comments(post_element)) > 0


# ── Delete duplicate comments ─────────────────────────────────────────────────

def delete_comment(driver: webdriver.Chrome, comment_el) -> bool:
    """
    Click the three-dot (overflow) menu on a comment then click Delete.
    Returns True on success.
    """
    try:
        # Step 1: scroll comment into view
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", comment_el
        )
        time.sleep(0.5)

        # Step 2: find and click the three-dot button
        overflow_btn = None
        try:
            overflow_btn = comment_el.find_element(By.XPATH, OVERFLOW_BTN_XPATH)
        except NoSuchElementException:
            # fallback: hover first so the button appears, then find it
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(driver).move_to_element(comment_el).perform()
            time.sleep(0.6)
            overflow_btn = comment_el.find_element(By.XPATH, OVERFLOW_BTN_XPATH)

        driver.execute_script("arguments[0].click();", overflow_btn)
        time.sleep(1.0)

        # Step 3: click Delete in the dropdown
        # Try the specific menu XPath first
        try:
            delete_item = driver.find_element(By.XPATH, DELETE_ITEM_XPATH)
            driver.execute_script("arguments[0].click();", delete_item)
            time.sleep(1.0)
            return True
        except NoSuchElementException:
            pass

        # Fallback: any menuitem whose text contains "delete"
        menu_container = driver.find_element(By.XPATH, DELETE_MENU_XPATH)
        for item in menu_container.find_elements(By.XPATH, './/*[@role="menuitem"]'):
            if "delete" in item.text.lower():
                driver.execute_script("arguments[0].click();", item)
                time.sleep(1.0)
                return True

        print("      ✗ Delete menu item not found")
        return False

    except Exception as exc:
        print(f"      ✗ delete_comment error: {exc}")
        return False


def delete_duplicate_my_comments(driver: webdriver.Chrome, post_element) -> int:
    """
    If I have more than 1 comment on this post, delete all extras (keep 1).
    Returns the number of comments deleted.
    """
    my_comments = find_my_comments(post_element)
    if len(my_comments) <= 1:
        return 0

    deleted = 0
    # Keep the last one (index -1), delete the rest
    to_delete = my_comments[:-1]
    print(f"    [!] Found {len(my_comments)} of your comments — deleting {len(to_delete)} duplicate(s)…")
    for c in to_delete:
        if delete_comment(driver, c):
            print("        ✓ Deleted duplicate comment")
            deleted += 1
        else:
            print("        ✗ Could not delete comment")
        time.sleep(1)
    return deleted


# ── Comment actions ───────────────────────────────────────────────────────────

def click_element(driver: webdriver.Chrome, element) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.4)
        element.click()
        return True
    except ElementClickInterceptedException:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False
    except Exception:
        return False


def open_comment_box(driver: webdriver.Chrome, post_element) -> bool:
    try:
        btn = post_element.find_element(By.XPATH, COMMENT_BTN_REL_XPATH)
        if click_element(driver, btn):
            time.sleep(ACTION_DELAY)
            return True
    except (NoSuchElementException, StaleElementReferenceException):
        pass

    try:
        btn = post_element.find_element(
            By.XPATH,
            './/button[contains(translate(@aria-label,"ABCDEFGHIJKLMNOPQRSTUVWXYZ",'
            '"abcdefghijklmnopqrstuvwxyz"),"comment")]'
        )
        if click_element(driver, btn):
            time.sleep(ACTION_DELAY)
            return True
    except (NoSuchElementException, StaleElementReferenceException):
        pass

    try:
        for btn in post_element.find_elements(By.TAG_NAME, "button"):
            if "comment" in btn.text.lower():
                if click_element(driver, btn):
                    time.sleep(ACTION_DELAY)
                    return True
    except StaleElementReferenceException:
        pass

    return False


def find_comment_input(post_element):
    try:
        inp = post_element.find_element(By.XPATH, COMMENT_INPUT_REL_XPATH)
        if inp.is_displayed():
            return inp
    except (NoSuchElementException, StaleElementReferenceException):
        pass
    return None


def submit_comment(driver: webdriver.Chrome, post_element, input_el, text: str) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", input_el)
        time.sleep(0.3)
        input_el.click()
        time.sleep(0.4)
        input_el.send_keys(text)
        time.sleep(0.8)

        try:
            submit_btn = post_element.find_element(By.XPATH, SUBMIT_BTN_REL_XPATH)
            if click_element(driver, submit_btn):
                time.sleep(ACTION_DELAY)
                return True
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        try:
            submit_btn = post_element.find_element(
                By.XPATH,
                './/button[contains(translate(@aria-label,"ABCDEFGHIJKLMNOPQRSTUVWXYZ",'
                '"abcdefghijklmnopqrstuvwxyz"),"post") or '
                'contains(translate(@aria-label,"ABCDEFGHIJKLMNOPQRSTUVWXYZ",'
                '"abcdefghijklmnopqrstuvwxyz"),"submit")]'
            )
            if click_element(driver, submit_btn):
                time.sleep(ACTION_DELAY)
                return True
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        print("    ✗ Submit button not found")
        return False

    except Exception as exc:
        print(f"    ✗ submit_comment error: {exc}")
        return False


# ── Main processing loop ──────────────────────────────────────────────────────

def process_posts(driver: webdriver.Chrome, seen_ids: set) -> None:
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, POSTS_CONTAINER_XPATH))
        )
    except TimeoutException:
        print("[!] Posts container not found.")
        return

    container = driver.find_element(By.XPATH, POSTS_CONTAINER_XPATH)
    posts     = container.find_elements(By.XPATH, './div')

    if not posts:
        print("[!] No post divs found inside container.")
        return

    print(f"[i] Found {len(posts)} post(s). Processing up to {POST_LIMIT}…\n")
    commented  = 0
    skipped    = 0
    duplicates = 0
    cleaned    = 0

    for idx, post in enumerate(posts[:POST_LIMIT], start=1):
        print(f"  Post {idx}/{min(len(posts), POST_LIMIT)}", end="  ")
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", post)
            time.sleep(0.5)

            # ── Cross-cycle dedup ──
            post_id = get_post_id(post)
            if post_id and post_id in seen_ids:
                print("↳ Already processed — skipping.")
                duplicates += 1
                continue

            # ── Delete any duplicate comments I left on this post ──
            n_deleted = delete_duplicate_my_comments(driver, post)
            cleaned += n_deleted

            # ── Check if I already have exactly 1 comment ──
            if already_commented(post):
                print("↳ Already commented by you — skipping.")
                if post_id:
                    seen_ids.add(post_id)
                skipped += 1
                continue

            # ── Open comment box ──
            if not open_comment_box(driver, post):
                print("↳ Comment button not found — skipping.")
                skipped += 1
                continue

            # ── Find input ──
            input_el = find_comment_input(post)
            if not input_el:
                print("↳ Comment input not visible — skipping.")
                skipped += 1
                continue

            # ── Type & submit ──
            if submit_comment(driver, post, input_el, COMMENT_TEXT):
                print(f'↳ ✓ Commented: "{COMMENT_TEXT}"')
                commented += 1
                if post_id:
                    seen_ids.add(post_id)
            else:
                skipped += 1

            time.sleep(2)

        except StaleElementReferenceException:
            print("↳ Post gone from DOM — skipping.")
            skipped += 1
        except Exception as exc:
            print(f"↳ Error: {exc} — skipping.")
            skipped += 1

    print(
        f"\n[✓] Cycle done — Commented: {commented} | Skipped: {skipped} | "
        f"Duplicates removed: {cleaned} | Cross-cycle skip: {duplicates} | "
        f"Total seen: {len(seen_ids)}"
    )


def main() -> None:
    driver   = build_driver()
    seen_ids = set()
    cycle    = 0

    try:
        print("[→] Opening LinkedIn…")
        driver.get("https://www.linkedin.com/login")
        wait_for_login(driver)

        while True:
            cycle += 1
            print(f"\n{'─'*55}")
            print(f"[→] Cycle {cycle} — reloading search page…")
            driver.get(LINKEDIN_URL)
            time.sleep(4)

            print("[→] Scrolling to load posts…")
            scroll_to_load_posts(driver)

            process_posts(driver, seen_ids)

            print(f"\n[⏳] Waiting {LOOP_INTERVAL}s before next cycle… (Ctrl+C to stop)")
            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        print("\n[!] Stopped by user.")
    finally:
        print(f"[→] Total unique posts seen this session: {len(seen_ids)}")
        print("[→] Browser left open. Close it manually when done.")


if __name__ == "__main__":
    main()
