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
    """Reads the list of already-seen items from the Google Sheet (via Apps Script GET)."""
    try:
        resp = requests.get(SHEET_URL, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # data is a list of first-column values from the sheet; clean it up
        return [str(item).strip() for item in data if item]
    except Exception as e:
        print(f"Could not load memory from Sheet (continuing with empty memory this run): {e}")
        return []


def save_memory(item):
    """Appends a new item to the Google Sheet (via Apps Script POST)."""
    try:
        requests.post(SHEET_URL, data={'item': item}, timeout=20)
    except Exception as e:
        print(f"Could not save memory to Sheet: {e}")


def check_ktu(driver, memory):
    print("\n--- Checking KTU Announcements ---")
    driver.get('https://ktu.edu.in/Menu/announcements')
    time.sleep(10)  # hard wait for JS-rendered announcement cards to appear

    buttons = driver.find_elements(By.TAG_NAME, 'button')
    print(f"(debug) {len(buttons)} <button> elements found on KTU page")
    updates_found = False

    candidate_buttons = 0
    for btn in buttons:
        btn_text = btn.text.strip().lower()
        if 'notification' in btn_text or 'order' in btn_text or 'download' in btn_text:
            candidate_buttons += 1
            try:
                card = btn.find_element(By.XPATH, "./../..")
                title = card.text.split('\n')[0].strip()
                if len(title) < 15:
                    title = btn.find_element(By.XPATH, "./../../..").text.split('\n')[0].strip()

                # Log every candidate title regardless of memory state, so we can
                # see exactly what's being extracted and why it is/isn't sent.
                already_seen = title in memory
                print(f"(debug) candidate title='{title}' | len={len(title)} | already_in_memory={already_seen}")

                if len(title) > 15 and not already_seen:
                    print(f"KTU Update: {title}")
                    msg = f"🚨 <b>New KTU Announcement</b> 🚨\n\n<b>{title}</b>\n\n🔗 <a href='https://ktu.edu.in/Menu/announcements'>Visit KTU to download</a>"
                    if send_telegram_message(msg):
                        save_memory(title)
                        memory.append(title)
                        updates_found = True
                    else:
                        print(f"(debug) NOT saving to memory since delivery failed — will retry next run: {title}")
            except Exception as e:
                # Log failures instead of silently swallowing them
                print(f"(debug) FAILED to extract title from a candidate button: {e}")

    print(f"(debug) {candidate_buttons} candidate buttons matched keyword filter (of {len(buttons)} total buttons)")

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
        if link_url not in memory:
            raw_filename = link_url.split('/')[-1]
            clean_title = re.sub(r'^\d+_', '', raw_filename).replace('.pdf', '').replace('_', ' ')

            print(f"GEC Update: {clean_title}")
            msg = f"🏛️ <b>New GEC Thrissur Update</b> 🏛️\n\n<b>{clean_title}</b>\n\n🔗 <a href='{link_url}'>Click to view PDF</a>"
            if send_telegram_message(msg):
                save_memory(link_url)
                memory.append(link_url)
                updates_found = True
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
        current_memory = load_memory()
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
