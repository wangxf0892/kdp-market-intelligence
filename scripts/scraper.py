import os
import re
import csv
import time
import random
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 基础路径配置
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

def clean_bsr(text):
    # 提取类似 "#1,234 in Books" 中的数字
    match = re.search(r'#([0-9,]+)\s+in\s+Books', text)
    if match:
        return int(match.group(1).replace(',', ''))
    return None

def scrape_asin(asin):
    url = f"https://www.amazon.com/dp/{asin}"
    headers = get_headers()
    
    # 随机延时 15-30 秒，防止请求频率过高触发封锁
    sleep_time = random.uniform(15, 30)
    print(f"[*] Sleeping {sleep_time:.1f}s before fetching ASIN: {asin}")
    time.sleep(sleep_time)
    
    try:
        session = requests.Session()
        res = session.get(url, headers=headers, timeout=20)
        
        # 遇验证码直接终止，保护 Actions 运行环境不被列入黑名单
        if "api-services-support@amazon.com" in res.text or "Type the characters you see in this image" in res.text:
            print(f"[!] Captcha triggered on ASIN: {asin}. Terminating run cleanly.")
            return None
            
        if res.status_code != 200:
            print(f"[!] Status {res.status_code} on ASIN: {asin}")
            return None
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 抓取标题
        title_tag = soup.find('span', {'id': 'productTitle'})
        title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"
        
        # 抓取评分与评价数
        rating_tag = soup.find('span', {'class': 'a-icon-alt'})
        rating = rating_tag.get_text(strip=True) if rating_tag else "N/A"
        
        # 抓取 BSR
        bsr = None
        details = soup.find('div', {'id': 'detailBullets_feature_div'})
        if details:
            bsr = clean_bsr(details.get_text())
        
        if not bsr:
            # 针对不同页面模板的兜底查找
            sales_rank = soup.find('tr', {'id': 'SalesRank'})
            if sales_rank:
                bsr = clean_bsr(sales_rank.get_text())

        return {
            'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'asin': asin,
            'title': title[:80], # 截短方便查看
            'bsr': bsr if bsr else "Not Found",
            'rating': rating
        }
        
    except Exception as e:
        print(f"[!] Request error on {asin}: {str(e)}")
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
            print(f"[+] Scraped {asin}: BSR={data['bsr']}, Rating={data['rating']}")
        else:
            # 如果触发验证码，终止后续抓取
            break
            
    if results:
        file_exists = os.path.exists(OUTPUT_FILE)
        with open(OUTPUT_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['timestamp', 'asin', 'title', 'bsr', 'rating'])
            if not file_exists:
                writer.writeheader()
            for row in results:
                writer.writerow(row)
        print(f"[+] Successfully wrote {len(results)} rows to {OUTPUT_FILE}")

if __name__ == '__main__':
    main()
