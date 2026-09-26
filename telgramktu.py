import os
import re
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

# --- CONFIGURATION (read from environment variables / GitHub Secrets) ---
TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = os.environ['SHEET_WEBAPP_URL']  # Your Apps Script deployed Web App URL


def send_telegram_message(text):
    api_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        resp = requests.post(api_url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, timeout=15)
        result = resp.json()
        if not result.get('ok'):
            # Telegram responded but rejected the message (e.g. bad HTML, blocked bot,
            # wrong chat_id). This does NOT raise an exception on its own, so without
            # this check it fails silently while the item still gets marked as "sent".
            print(f"(debug) Telegram REJECTED message: {result}")
            return False
        return True
    except Exception as e:
        print(f"Telegram send failed (network/connection error): {e}")
        return False


def load_memory():
    """Reads the list of already-seen items from the Google Sheet (via Apps Script GET).
    Raises on failure instead of returning an empty list — treating a failed/slow
    read as 'nothing has ever been seen' is what caused a mass-resend snowball
    (empty memory -> everything looks new -> sheet grows -> reads get slower -> repeat)."""
    resp = requests.get(SHEET_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    mem = [str(item).strip() for item in data if item]
    print(f"(debug) Loaded {len(mem)} items from memory sheet")
    if mem:
        print(f"(debug) Sample of loaded memory (first 3): {mem[:3]}")
    return mem


def save_memory(item):
    """Appends a new item to the Google Sheet (via Apps Script POST). Returns True if the write appears to have succeeded."""
    try:
        resp = requests.post(SHEET_URL, data={'item': item}, timeout=20)
        resp.raise_for_status()
        print(f"(debug) Sheet write response: {resp.text[:200]!r}")
        return True
    except Exception as e:
        print(f"(debug) COULD NOT save to Sheet — this item will look 'new' again next run: {e}")
        return False


def check_ktu(driver, memory):
    print("\n--- Checking KTU Announcements ---")
    driver.get('https://ktu.edu.in/Menu/announcements')
    time.sleep(10)  # hard wait for JS-rendered announcement cards to appear

    print(f"(debug) KTU page title: {driver.title!r}")
    try:
        body_text_len = len(driver.find_element(By.TAG_NAME, 'body').text)
        print(f"(debug) KTU page body text length: {body_text_len} characters")
    except Exception as e:
        print(f"(debug) Could not read page body at all: {e}")

    # Each announcement is one div.col-sm-11, containing an h6 title and a
    # themed date div. This is far more reliable than the old approach of
    # looping over buttons and guessing a title by climbing up the DOM —
    # that broke on cards with multiple buttons (counted the same card
    # 2-3 times) and sometimes grabbed the wrong "first line" as the title.
    cards = driver.find_elements(By.CSS_SELECTOR, "div.col-sm-11")
    print(f"(debug) {len(cards)} announcement cards found on KTU page")
    updates_found = False

    for card in cards:
        try:
            title_el = card.find_elements(By.CSS_SELECTOR, "h6.f-w-bold")
            if not title_el:
                continue  # not every col-sm-11 on the page is an announcement card
            title = title_el[0].text.strip()

            date_text = ""
            date_el = card.find_elements(By.CSS_SELECTOR, "div.font-14.text-theme.h6.m-t-10.f-w-bold")
            if date_el:
                date_text = date_el[0].text.strip()

            # Composite key: date + heading, far more robust than title alone.
            key = f"{date_text} | {title}" if date_text else title

            # Backward-compatible check: recognize items saved under the OLD
            # title-only memory format too, so switching key formats doesn't
            # cause everything currently on the page to look "new" again.
            already_seen = (key in memory) or (title in memory)
            print(f"(debug) candidate: date='{date_text}' title='{title}' | already_in_memory={already_seen}")

            if title and not already_seen:
                print(f"KTU Update: {key}")
                msg = f"🚨 <b>New KTU Announcement</b> 🚨\n\n<b>{title}</b>\n🗓️ {date_text}\n\n🔗 <a href='https://ktu.edu.in/Menu/announcements'>Visit KTU to download</a>"
                if send_telegram_message(msg):
                    saved = save_memory(key)
                    if saved:
                        memory.append(key)
                        updates_found = True
                    else:
                        print(f"(debug) Save failed — will be retried next run: {key}")
                else:
                    print(f"(debug) NOT saving to memory since delivery failed — will retry next run: {key}")
        except Exception as e:
            print(f"(debug) FAILED to process a candidate card: {e}")

    if not updates_found:
        print("No new KTU announcements.")


def check_gec(driver, memory):
    print("\n--- Checking GEC News ---")
    driver.get('https://gectcr.ac.in/all-news')
    time.sleep(6)  # hard wait for JS-rendered content to appear

    links = driver.find_elements(By.TAG_NAME, 'a')

    # Collect only PDF links, preserving the order they appear on the page
    # (the site lists newest first, so position in this list = recency)
    pdf_links = []
    for a in links:
        href = a.get_attribute('href')
        if href and '.pdf' in href.lower():
            pdf_links.append(href)

    # Only ever consider the most recent 5 items on the page.
    # We deliberately never look further down the list, so there's no
    # older backlog to slowly drain out over future runs.
    latest_five = pdf_links[:5]
    print(f"(debug) {len(pdf_links)} total PDF links on page, checking latest {len(latest_five)}")

    updates_found = False
    for link_url in latest_five:
        already_seen = link_url in memory
        print(f"(debug) candidate URL='{link_url}' | already_in_memory={already_seen}")

        if not already_seen:
            raw_filename = link_url.split('/')[-1]
            clean_title = re.sub(r'^\d+_', '', raw_filename).replace('.pdf', '').replace('_', ' ')

            print(f"GEC Update: {clean_title}")
            msg = f"🏛️ <b>New GEC Thrissur Update</b> 🏛️\n\n<b>{clean_title}</b>\n\n🔗 <a href='{link_url}'>Click to view PDF</a>"
            if send_telegram_message(msg):
                saved = save_memory(link_url)
                if saved:
                    memory.append(link_url)
                    updates_found = True
                else:
                    print(f"(debug) Save failed — NOT adding to in-run memory either, so this will be retried next run")
            else:
                print(f"(debug) NOT saving to memory since delivery failed — will retry next run: {clean_title}")

    if not updates_found:
        print("No new GEC announcements.")


def run_once():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--ignore-certificate-errors')
    options.add_experimental_option('excludeSwitches', ['enable-logging'])

    browser = None
    try:
        browser = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        try:
            current_memory = load_memory()
        except Exception as e:
            print(f"(debug) FATAL: could not load memory this run — ABORTING checks entirely "
                  f"to avoid treating everything as new: {e}")
            return

        check_ktu(browser, current_memory)
        check_gec(browser, current_memory)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if browser:
            browser.quit()


if __name__ == "__main__":
    print("Bot check started (single-run mode, meant to be triggered on a schedule).")
    run_once()
    print("Bot check finished.")
