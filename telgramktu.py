import os
import re
import time
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

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
        print(f"(debug) COULD NOT save to Sheet — this item will look 'new' again next run: {e}")
        return False


def get_free_proxies():
    """Fetches a fresh list of free HTTP proxies that support HTTPS destinations."""
    print("(debug) Fetching free proxies from ProxyScrape...")
    try:
        url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=all"
        resp = requests.get(url, timeout=10)
        # splitlines() handles both \n and \r\n cleanly
        proxies = [p.strip() for p in resp.text.splitlines() if p.strip()]
        print(f"(debug) Fetched {len(proxies)} proxies.")
        return proxies
    except Exception as e:
        print(f"(debug) Failed to fetch proxy list: {e}")
        return []


def check_ktu(memory):
    print("\n--- Checking KTU Announcements (via free proxies) ---")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    proxies_list = get_free_proxies()
    if not proxies_list:
        print("(debug) No proxies available. Skipping KTU this run.")
        return

    html_content = ""
    
    # Try up to 10 proxies max so the GitHub Action doesn't run forever
    for proxy_ip in proxies_list[:10]:
        print(f"(debug) Trying proxy: {proxy_ip}")
        proxies = {
            "http": f"http://{proxy_ip}",
            "https": f"http://{proxy_ip}"
        }
        try:
            # 10s timeout: free proxies are slow, but if it takes longer than 10s it's probably dead
            resp = requests.get('https://ktu.edu.in/Menu/announcements', headers=headers, proxies=proxies, timeout=10)
            
            # A real page is ~40k+ chars. If it's > 5000, we successfully bypassed the firewall
            if resp.status_code == 200 and len(resp.text) > 5000:
                print(f"(debug) -> SUCCESS! Proxy {proxy_ip} worked. HTML length: {len(resp.text)}")
                html_content = resp.text
                break
            else:
                print(f"(debug) -> Proxy connected but returned blocked page (length: {len(resp.text)})")
        except Exception as e:
            # We expect a LOT of these. 
            print(f"(debug) -> Proxy failed: {type(e).__name__}")
            
    if not html_content:
        print("(debug) All attempted proxies failed or were blocked. Will try again next run.")
        return

    soup = BeautifulSoup(html_content, 'html.parser')
    cards = soup.select("div.col-sm-11")
    print(f"(debug) {len(cards)} announcement cards found on KTU page")
    
    updates_found = False

    for card in cards:
        try:
            title_el = card.select_one("h6.f-w-bold")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            date_text = ""
            date_el = card.select_one("div.font-14.text-theme.h6.m-t-10.f-w-bold")
            if date_el:
                date_text = date_el.get_text(strip=True)

            key = f"{date_text} | {title}" if date_text else title
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
    time.sleep(6)

    links = driver.find_elements(By.TAG_NAME, 'a')

    pdf_links = []
    for a in links:
        href = a.get_attribute('href')
        if href and '.pdf' in href.lower():
            pdf_links.append(href)

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
    options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    )

    browser = None
    try:
        browser = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        try:
            browser.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            })
        except Exception as e:
            pass

        try:
            current_memory = load_memory()
        except Exception as e:
            print(f"(debug) FATAL: could not load memory this run: {e}")
            return

        check_ktu(current_memory)
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
