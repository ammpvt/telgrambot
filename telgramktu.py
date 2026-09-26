import os
import re
import time
import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- CONFIGURATION (read from environment variables / GitHub Secrets) ---
TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = os.environ['SHEET_WEBAPP_URL']

def send_telegram_message(text):
    api_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        resp = requests.post(api_url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}, timeout=15)
        result = resp.json()
        if not result.get('ok'):
            print(f"(debug) Telegram REJECTED message: {result}")
            return False
        return True
    except Exception as e:
        print(f"Telegram send failed (network/connection error): {e}")
        return False

def load_memory():
    resp = requests.get(SHEET_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    mem = [str(item).strip() for item in data if item]
    print(f"(debug) Loaded {len(mem)} items from memory sheet")
    return mem

def save_memory(item):
    try:
        resp = requests.post(SHEET_URL, data={'item': item}, timeout=20)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"(debug) COULD NOT save to Sheet: {e}")
        return False

def check_ktu(driver, memory):
    print("\n--- Checking KTU Announcements ---")
    driver.get('https://ktu.edu.in/Menu/announcements')

    # Wait up to 30s to allow WAF JS challenges to solve themselves
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h6.f-w-bold"))
        )
    except Exception:
        print("(debug) No h6.f-w-bold appeared within 30s wait (WAF challenge might have blocked us)")

    print(f"(debug) KTU page title: {driver.title!r}")
    try:
        body_text_len = len(driver.find_element(By.TAG_NAME, 'body').text)
        print(f"(debug) KTU page body text length: {body_text_len} characters")
    except Exception as e:
        print(f"(debug) Could not read page body at all: {e}")

    cards = driver.find_elements(By.CSS_SELECTOR, "div.col-sm-11")
    print(f"(debug) {len(cards)} announcement cards found on KTU page")
    updates_found = False

    for card in cards:
        try:
            title_el = card.find_elements(By.CSS_SELECTOR, "h6.f-w-bold")
            if not title_el:
                continue
            title = title_el[0].text.strip()

            date_text = ""
            date_el = card.find_elements(By.CSS_SELECTOR, "div.font-14.text-theme.h6.m-t-10.f-w-bold")
            if date_el:
                date_text = date_el[0].text.strip()

            key = f"{date_text} | {title}" if date_text else title
            already_seen = (key in memory) or (title in memory)
            
            if title and not already_seen:
                print(f"KTU Update: {key}")
                msg = f"🚨 <b>New KTU Announcement</b> 🚨\n\n<b>{title}</b>\n🗓️ {date_text}\n\n🔗 <a href='https://ktu.edu.in/Menu/announcements'>Visit KTU to download</a>"
                if send_telegram_message(msg):
                    saved = save_memory(key)
                    if saved:
                        memory.append(key)
                        updates_found = True
        except Exception as e:
            print(f"(debug) FAILED to process a candidate card: {e}")

    if not updates_found:
        print("No new KTU announcements.")

def check_gec(driver, memory):
    print("\n--- Checking GEC News ---")
    driver.get('https://gectcr.ac.in/all-news')
    time.sleep(6)

    links = driver.find_elements(By.TAG_NAME, 'a')
    pdf_links = [a.get_attribute('href') for a in links if a.get_attribute('href') and '.pdf' in a.get_attribute('href').lower()]
    
    latest_five = pdf_links[:5]
    print(f"(debug) {len(pdf_links)} total PDF links on page, checking latest {len(latest_five)}")

    updates_found = False
    for link_url in latest_five:
        already_seen = link_url in memory
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

    if not updates_found:
        print("No new GEC announcements.")

def run_once():
    options = uc.ChromeOptions()
    # CRITICAL: We DO NOT use --headless anymore. The Xvfb wrapper handles the display.
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    
    driver = None
    try:
        # undetected_chromedriver dynamically patches the browser fingerprint
        driver = uc.Chrome(options=options)

        try:
            current_memory = load_memory()
        except Exception as e:
            print(f"(debug) FATAL: could not load memory this run: {e}")
            return

        check_ktu(driver, current_memory)
        check_gec(driver, current_memory)
        
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    print("Bot check started (Virtual Display stealth mode).")
    run_once()
    print("Bot check finished.")
