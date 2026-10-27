import os
import re
import csv
import json
import time
import random
import requests
from bs4 import BeautifulSoup
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSR_FILE = os.path.join(BASE_DIR, 'data', 'bsr_report.csv')
OUTPUT_MD = os.path.join(BASE_DIR, 'data', 'pain_points_report.md')

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
        'DNT': '1'
    }

def get_top_asins(limit=3):
    """从 bsr_report.csv 选出 BSR 最小（销量最高）的前几个竞品"""
    if not os.path.exists(BSR_FILE):
        return []
        
    candidates = []
    with open(BSR_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            bsr_str = row.get('bsr', 'Not Found')
            if bsr_str.isdigit():
                candidates.append({
                    'asin': row['asin'],
                    'title': row['title'],
                    'bsr': int(bsr_str),
                    'rating': row.get('rating', 'N/A')
                })
                
    # 按 BSR 升序排列（排名越小卖得越猛）
    candidates.sort(key=lambda x: x['bsr'])
    return candidates[:limit]

def scrape_critical_reviews(asin):
    """定向抓取该 ASIN 的 1-3 星差评页面"""
    # filterByStar=critical 直接过滤出 1, 2, 3 星
    url = f"https://www.amazon.com/product-reviews/{asin}?filterByStar=critical&reviewerType=all_reviews&pageNumber=1"
    headers = get_headers()
    
    sleep_time = random.uniform(15, 25)
    print(f"[*] Sleeping {sleep_time:.1f}s before fetching critical reviews for ASIN: {asin}")
    time.sleep(sleep_time)
    
    try:
        res = requests.get(url, headers=headers, timeout=25)
        if res.status_code != 200 or "api-services-support@amazon.com" in res.text:
            print(f"[!] Blocked or captcha on review page for {asin}")
            return []
            
        soup = BeautifulSoup(res.text, 'html.parser')
        review_cards = soup.find_all('div', {'data-hook': 'review'})
        
        parsed_reviews = []
        for card in review_cards:
            # 提取星级
            star_tag = card.find('i', {'data-hook': 'review-star-rating'}) or card.find('span', {'class': 'a-icon-alt'})
            stars = star_tag.get_text(strip=True) if star_tag else "Critical"
            
            # 提取标题
            title_tag = card.find('a', {'data-hook': 'review-title'}) or card.find('span', {'data-hook': 'review-title'})
            # 处理可能的副文本
            title = title_tag.get_text(strip=True) if title_tag else ""
            if "stars" in title:
                # 剔除包含星级的冗余文字
                parts = title.split("stars")
                title = parts[-1].strip() if len(parts) > 1 else title
                
            # 提取正文
            body_tag = card.find('span', {'data-hook': 'review-body'})
            body = body_tag.get_text(strip=True) if body_tag else ""
            
            if body:
                parsed_reviews.append({
                    'stars': stars,
                    'title': title,
                    'body': body
                })
                
        print(f"[+] Extracted {len(parsed_reviews)} critical reviews for {asin}")
        return parsed_reviews
    except Exception as e:
        print(f"[!] Error fetching reviews for {asin}: {e}")
        return []

def categorize_pain_points(reviews):
    """自动对买家抱怨的核心痛点进行语义分类"""
    buckets = {
        "答案错误 / 题目有 Bug (Errors in Answers)": [],
        "缺乏详细解题步骤 (No Explanations / Too Vague)": [],
        "排版混乱 / 字体不适 (Formatting & Layout Issues)": [],
        "内容过时 / 与新考试不符 (Outdated Content)": [],
        "线上激活码失效 / 强制注册 (Online Code / Login Traps)": [],
        "题目太少 / 刷题不尽兴 (Not Enough Practice)": [],
        "其他吐槽 (General Dissatisfaction)": []
    }
    
    for r in reviews:
        text = (r['title'] + " " + r['body']).lower()
        matched = False
        
        if any(w in text for w in ['wrong answer', 'incorrect', 'typo', 'mistake', 'error', 'wrong solution']):
            buckets["答案错误 / 题目有 Bug (Errors in Answers)"].append(r)
            matched = True
        if any(w in text for w in ['explain', 'no explanation', 'how they got', 'step-by-step', 'does not show']):
            buckets["缺乏详细解题步骤 (No Explanations / Too Vague)"].append(r)
            matched = True
        if any(w in text for w in ['format', 'font', 'print', 'small text', 'cheap paper', 'spacing']):
            buckets["排版混乱 / 字体不适 (Formatting & Layout Issues)"].append(r)
            matched = True
        if any(w in text for w in ['outdated', 'old version', 'not on the test', 'changed format']):
            buckets["内容过时 / 与新考试不符 (Outdated Content)"].append(r)
            matched = True
        if any(w in text for w in ['login', 'website', 'code', 'online access', 'register', 'expired']):
            buckets["线上激活码失效 / 强制注册 (Online Code / Login Traps)"].append(r)
            matched = True
        if any(w in text for w in ['not enough', 'only a few', 'more questions', 'short']):
            buckets["题目太少 / 刷题不尽兴 (Not Enough Practice)"].append(r)
            matched = True
            
        if not matched:
            buckets["其他吐槽 (General Dissatisfaction)"].append(r)
            
    return buckets

def generate_markdown_report(targets_data):
    """将提炼出的差评直接转化成《竞品破壁战报》"""
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    md = [f"# KDP 竞品致命差评与痛点挖掘战报\n", f"> **更新时间**: {now}\n\n---\n"]
    
    for item in targets_data:
        asin = item['asin']
        title = item['title']
        bsr = item['bsr']
        reviews = item['reviews']
        buckets = categorize_pain_points(reviews)
        
        md.append(f"## 🎯 目标竞品: `{asin}` (BSR: #{bsr})\n")
        md.append(f"**标题**: {title}\n")
        md.append(f"**分析差评样本数**: {len(reviews)} 条\n\n")
        md.append(f"### ⚡ 提炼出的高频缺陷分布:\n")
        
        for category, revs in buckets.items():
            if revs:
                md.append(f"#### 🔴 {category} ({len(revs)} 条抱怨)\n")
                # 展示最具代表性的前 2 条买家原话
                for sample in revs[:2]:
                    md.append(f"> **\"{sample['title']}\"**  \n> {sample['body'][:300]}...\n>\n")
                    
        md.append("\n### 💡 针对该竞品的反制出版方案 (Counter-Strategy):\n")
        # 针对该书最集中的缺陷，自动生成改写书名和功能建议
        top_flaw = max(buckets.items(), key=lambda x: len(x[1]))[0]
        md.append(f"- **核心主打卖点**: 强攻 **【{top_flaw}】**。\n")
        md.append(f"- **封面/副标题必带标签建议**:\n")
        md.append(f"  - 若对手错题多 ➔ 副标题加入 `100% Verified Answers & Zero-Error Guarantee`\n")
        md.append(f"  - 若对手没解析 ➔ 副标题加入 `Detailed Step-by-Step Logic for Every Single Question`\n")
        md.append(f"  - 若对手骗线上注册 ➔ 副标题加入 `100% Offline Practice: No Online Codes or Logins Required`\n")
        md.append("\n---\n")
        
    with open(OUTPUT_MD, 'w', encoding='utf-8') as f:
        f.write("\n".join(md))
        
    print(f"[+] Successfully generated battlecard at: {OUTPUT_MD}")

def main():
    top_targets = get_top_asins(limit=3)
    if not top_targets:
        print("[!] No valid top BSR ASINs found in report.")
        return
        
    all_analyzed = []
    for target in top_targets:
        print(f"[*] Analyzing competitor ASIN: {target['asin']} (#{target['bsr']})")
        revs = scrape_critical_reviews(target['asin'])
        target['reviews'] = revs
        all_analyzed.append(target)
        
    generate_markdown_report(all_analyzed)

if __name__ == '__main__':
    main()
