import os
import re
import csv
import time
import random
import requests
from bs4 import BeautifulSoup
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS_FILE = os.path.join(BASE_DIR, 'data', 'targets.txt')
OUTPUT_FILE = os.path.join(BASE_DIR, 'data', 'bsr_report.csv')

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0'
]

def get_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.google.com/',
        'Device-Memory': '8',
        'DNT': '1'
    }

def extract_bsr(html_text):
    """
    通过正则扫描全文提取主类目（Books / Kindle Store）的 BSR 排名
    """
    # 规则 1：匹配 "Best Sellers Rank: #1,234 in Books" 或 "#1,234 in Kindle Store"
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
    """
    精准锁定真实用户评分，避开轮播图组件里的伪图标
    """
    # 策略 1：锁定评论统计容器中的星级
    rev_container = soup.find('div', {'id': 'averageCustomerReviews'})
    if rev_container:
        rating_span = rev_container.find('span', {'class': 'a-icon-alt'})
        if rating_span and 'out of 5 stars' in rating_span.text:
            return rating_span.text.strip()

    # 策略 2：正则扫描 "X.X out of 5 stars"
    match = re.search(r'([0-5]\.[0-9])\s+out of 5 stars', html_text)
    if match:
        return f"{match.group(1)} out of 5 stars"

    return "N/A"

def extract_review_count(soup, html_text):
    """
    提取总评论数
    """
    count_tag = soup.find('span', {'id': 'acrCustomerReviewText'})
    if count_tag:
        count_match = re.search(r'([0-9,]+)', count_tag.text)
        if count_match:
            return count_match.group(1).replace(',', '')
    return "0"

def scrape_asin(asin):
    url = f"https://www.amazon.com/dp/{asin}"
    headers = get_headers()
    
    sleep_time = random.uniform(15, 25)
    print(f"[*] Sleeping {sleep_time:.1f}s before fetching ASIN: {asin}")
    time.sleep(sleep_time)
    
    try:
        session = requests.Session()
        res = session.get(url, headers=headers, timeout=25)
        
        # 遇验证码直接安全退出
        if "api-services-support@amazon.com" in res.text or "Type the characters you see in this image" in res.text:
            print(f"[!] Captcha triggered on ASIN: {asin}. Terminating cleanly.")
            return None
            
        if res.status_code != 200:
            print(f"[!] Status {res.status_code} on ASIN: {asin}")
            return None
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 抓取标题
        title_tag = soup.find('span', {'id': 'productTitle'})
        title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"
        
        # 提取核心数据
        bsr = extract_bsr(res.text)
        rating = extract_rating(soup, res.text)
        reviews = extract_review_count(soup, res.text)

        return {
            'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
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
    if not os.path.exists(TARGETS_FILE):
        print("[!] targets.txt not found.")
        return
        
    with open(TARGETS_FILE, 'r') as f:
        asins = [line.strip() for line in f if line.strip()]
        
    results = []
    for asin in asins:
        data = scrape_asin(asin)
        if data:
            results.append(data)
            print(f"[+] Scraped {asin}: BSR={data['bsr']}, Rating={data['rating']}, Reviews={data['reviews']}")
        else:
            break
            
    if results:
        file_exists = os.path.exists(OUTPUT_FILE)
        with open(OUTPUT_FILE, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['timestamp', 'asin', 'title', 'bsr', 'rating', 'reviews']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists or os.path.getsize(OUTPUT_FILE) == 0:
                writer.writeheader()
            for row in results:
                writer.writerow(row)
        print(f"[+] Wrote {len(results)} rows to {OUTPUT_FILE}")

if __name__ == '__main__':
    main()
