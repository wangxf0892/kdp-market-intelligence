import os
import re
import csv
import time
import random
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORDS_FILE = os.path.join(BASE_DIR, 'data', 'keywords.txt')
OUTPUT_FILE = os.path.join(BASE_DIR, 'data', 'bsr_report.csv')

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0'
]

def get_base_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'DNT': '1'
    }

def create_authenticated_session():
    """
    先访问首页换取合法 session-id Cookie，彻底规避白闯拦截
    """
    session = requests.Session()
    headers = get_base_headers()
    try:
        print("[*] Initializing Amazon session handshake...")
        session.get("https://www.amazon.com", headers=headers, timeout=15)
        time.sleep(random.uniform(2, 4))
    except Exception as e:
        print(f"[!] Session init warning: {e}")
    return session

def discover_asins_by_keyword(session, keyword, limit=5):
    encoded_kw = urllib.parse.quote_plus(keyword)
    url = f"https://www.amazon.com/s?k={encoded_kw}&i=stripbooks"
    headers = get_base_headers()
    headers['Referer'] = 'https://www.amazon.com/'
    
    print(f"[*] Discovering targets for keyword: '{keyword}'...")
    time.sleep(random.uniform(5, 8))
    
    try:
        res = session.get(url, headers=headers, timeout=25)
        
        # 严格识别是否被打入 Robot Check 页面
        if "Robot Check" in res.text or "validateCaptcha" in res.text:
            print(f"[!] Hit Amazon Robot Check on keyword: '{keyword}'")
            return []
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 锁定真正的自然排名搜索卡片
        cards = soup.find_all('div', {'data-component-type': 's-search-result'})
        if not cards:
            # 备用选择器
            cards = soup.find_all('div', {'data-asin': True})
            
        found_asins = []
        for card in cards:
            asin = card.get('data-asin', '').strip()
            # 过滤广告与无效项
            is_sponsored = "s-sponsored-label" in card.get_text() or "Sponsored" in card.get_text()
            if asin and not is_sponsored and len(asin) == 10:
                if asin not in found_asins:
                    found_asins.append(asin)
            if len(found_asins) >= limit:
                break
                
        print(f"[+] Found {len(found_asins)} organic ASINs for '{keyword}'")
        return found_asins
    except Exception as e:
        print(f"[!] Search discovery error: {e}")
        return []

def extract_bsr(html_text):
    patterns = [
        r'Best Sellers Rank[^\d#]*#([0-9,]+)\s+in\s+([A-Za-z\s&]+)',
        r'#([0-9,]+)\s+in\s+Books',
        r'#([0-9,]+)\s+in\s+Kindle Store',
        r'Best\s+Sellers\s+Rank:[^\n\r<]*#([0-9,]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, re.IGNORECASE)
        if match:
            raw_rank = match.group(1).replace(',', '').strip()
            if raw_rank.isdigit():
                return int(raw_rank)
    return "Not Found"

def extract_rating(soup, html_text):
    rev_container = soup.find('div', {'id': 'averageCustomerReviews'})
    if rev_container:
        rating_span = rev_container.find('span', {'class': 'a-icon-alt'})
        if rating_span and 'out of 5 stars' in rating_span.text:
            return rating_span.text.strip()
    match = re.search(r'([0-5]\.[0-9])\s+out of 5 stars', html_text)
    if match:
        return f"{match.group(1)} out of 5 stars"
    return "N/A"

def extract_review_count(soup, html_text):
    count_tag = soup.find('span', {'id': 'acrCustomerReviewText'})
    if count_tag:
        count_match = re.search(r'([0-9,]+)', count_tag.text)
        if count_match:
            return count_match.group(1).replace(',', '')
    return "0"

def scrape_asin(session, asin, keyword=""):
    url = f"https://www.amazon.com/dp/{asin}"
    headers = get_base_headers()
    headers['Referer'] = 'https://www.amazon.com/'
    
    sleep_time = random.uniform(12, 20)
    print(f"[*] Sleeping {sleep_time:.1f}s before fetching ASIN: {asin}")
    time.sleep(sleep_time)
    
    try:
        res = session.get(url, headers=headers, timeout=25)
        
        if "Robot Check" in res.text or "validateCaptcha" in res.text:
            print(f"[!] Captcha triggered on ASIN: {asin}. Stopping run.")
            return None
            
        if res.status_code != 200:
            print(f"[!] Status {res.status_code} on ASIN: {asin}")
            return None
            
        soup = BeautifulSoup(res.text, 'html.parser')
        title_tag = soup.find('span', {'id': 'productTitle'})
        title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"
        
        bsr = extract_bsr(res.text)
        rating = extract_rating(soup, res.text)
        reviews = extract_review_count(soup, res.text)

        return {
            'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'keyword': keyword,
            'asin': asin,
            'title': title[:60],
            'bsr': bsr,
            'rating': rating,
            'reviews': reviews
        }
    except Exception as e:
        print(f"[!] Error on {asin}: {str(e)}")
        return None

def main():
    if not os.path.exists(KEYWORDS_FILE):
        print("[!] keywords.txt not found. Exiting.")
        return
        
    with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
        keywords = [line.strip() for line in f if line.strip()]
        
    session = create_authenticated_session()
    
    all_targets = []
    for kw in keywords:
        asins = discover_asins_by_keyword(session, kw, limit=5)
        for asin in asins:
            all_targets.append({'asin': asin, 'keyword': kw})
            
    print(f"[*] Total discovered targets across all keywords: {len(all_targets)}")
    
    results = []
    for target in all_targets:
        data = scrape_asin(session, target['asin'], target['keyword'])
        if data:
            results.append(data)
            print(f"[+] Scraped {target['asin']} ({data['keyword']}): BSR={data['bsr']}, Rating={data['rating']}")
        else:
            break
            
    if results:
        # 覆盖更新最新快照
        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['timestamp', 'keyword', 'asin', 'title', 'bsr', 'rating', 'reviews']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in results:
                writer.writerow(row)
        print(f"[+] Overwrote {len(results)} fresh rows to {OUTPUT_FILE}")

if __name__ == '__main__':
    main()
