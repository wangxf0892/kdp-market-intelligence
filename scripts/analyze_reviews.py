import os
import re
import csv
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

def get_top_asins(limit=3):
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
    candidates.sort(key=lambda x: x['bsr'])
    return candidates[:limit]

def fetch_reviews_via_ajax(session, asin, filter_star="critical"):
    """
    通过亚马逊底层 AJAX 接口直取评论片段，绕开详情页的 JS 懒加载壁垒
    """
    url = "https://www.amazon.com/hz/reviews-render/ajax/reviews/get"
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'Referer': f'https://www.amazon.com/dp/{asin}',
        'Origin': 'https://www.amazon.com',
        'DNT': '1'
    }
    
    payload = {
        'sortBy': 'recent',
        'reviewerType': 'all_reviews',
        'formatType': '',
        'mediaType': '',
        'filterByStar': filter_star, # "critical" (1-3星) 或 "" (全部)
        'filterByAge': '',
        'pageNumber': '1',
        'filterByLanguage': '',
        'filterByKeyword': '',
        'shouldAppend': 'undefined',
        'deviceType': 'desktop',
        'canShowIntHeader': 'undefined',
        'reftag': 'cm_cr_arp_d_paging_btm_next_1',
        'pageSize': '10',
        'asin': asin,
        'scope': 'reviewsAjax0'
    }
    
    sleep_time = random.uniform(8, 14)
    print(f"[*] Sleeping {sleep_time:.1f}s before AJAX review fetch on ASIN: {asin} (filter={filter_star})")
    time.sleep(sleep_time)
    
    try:
        res = session.post(url, headers=headers, data=payload, timeout=25)
        if res.status_code != 200:
            print(f"[!] AJAX returned status {res.status_code} for {asin}")
            return []
            
        # 接口返回的是以 &&& 分隔的片段，包含转义后的 HTML
        clean_html = res.text.replace(r'\"', '"').replace(r'\/', '/')
        soup = BeautifulSoup(clean_html, 'html.parser')
        
        cards = soup.find_all('div', {'data-hook': 'review'})
        if not cards:
            cards = soup.select('.review')
            
        reviews = []
        for card in cards:
            # 提取星级
            star_tag = card.find('i', {'data-hook': 'review-star-rating'}) or card.find('span', {'class': 'a-icon-alt'})
            stars_text = star_tag.get_text(strip=True) if star_tag else "3.0 out of 5 stars"
            
            star_num = 3.0
            match = re.search(r'([0-5]\.[0-9])', stars_text)
            if match:
                star_num = float(match.group(1))
            elif "1" in stars_text:
                star_num = 1.0
            elif "2" in stars_text:
                star_num = 2.0
                
            # 提取标题
            title_tag = card.find('a', {'data-hook': 'review-title'}) or card.find('span', {'data-hook': 'review-title'})
            title = title_tag.get_text(strip=True) if title_tag else ""
            if "stars" in title:
                title = title.split("stars")[-1].strip()
                
            # 提取正文
            body_tag = card.find('span', {'data-hook': 'review-body'})
            body = body_tag.get_text(strip=True) if body_tag else ""
            
            if body:
                reviews.append({
                    'stars': f"{star_num} stars",
                    'title': title,
                    'body': body
                })
                
        print(f"[+] Successfully extracted {len(reviews)} reviews for {asin}")
        return reviews
    except Exception as e:
        print(f"[!] Error on AJAX {asin}: {e}")
        return []

def scrape_reviews_for_asin(session, asin):
    # 优先抓取 1-3 星差评
    reviews = fetch_reviews_via_ajax(session, asin, filter_star="critical")
    # 如果该品只有很少差评，退一步抓最新所有评论从中筛选抱怨
    if not reviews:
        print(f"[*] Critical empty for {asin}, fetching all recent reviews...")
        reviews = fetch_reviews_via_ajax(session, asin, filter_star="")
    return reviews

def categorize_pain_points(reviews):
    buckets = {
        "答案错误 / 题目有 Bug (Errors in Answers)": [],
        "缺乏详细解题步骤 (No Explanations / Too Vague)": [],
        "排版混乱 / 字体不适 (Formatting & Layout Issues)": [],
        "内容过时 / 与新考试不符 (Outdated Content)": [],
        "线上激活码失效 / 强制注册 (Online Code / Login Traps)": [],
        "题目太少 / 刷题不尽兴 (Not Enough Practice)": [],
        "其他买家吐槽 (General Dissatisfaction)": []
    }
    
    for r in reviews:
        text = (r['title'] + " " + r['body']).lower()
        matched = False
        
        if any(w in text for w in ['wrong answer', 'incorrect', 'typo', 'mistake', 'error', 'wrong solution', 'math error']):
            buckets["答案错误 / 题目有 Bug (Errors in Answers)"].append(r)
            matched = True
        if any(w in text for w in ['explain', 'no explanation', 'how they got', 'step-by-step', 'does not show', 'why']):
            buckets["缺乏详细解题步骤 (No Explanations / Too Vague)"].append(r)
            matched = True
        if any(w in text for w in ['format', 'font', 'print', 'small text', 'cheap paper', 'spacing', 'hard to read']):
            buckets["排版混乱 / 字体不适 (Formatting & Layout Issues)"].append(r)
            matched = True
        if any(w in text for w in ['outdated', 'old version', 'not on the test', 'changed format']):
            buckets["内容过时 / 与新考试不符 (Outdated Content)"].append(r)
            matched = True
        if any(w in text for w in ['login', 'website', 'code', 'online access', 'register', 'expired', 'account']):
            buckets["线上激活码失效 / 强制注册 (Online Code / Login Traps)"].append(r)
            matched = True
        if any(w in text for w in ['not enough', 'only a few', 'more questions', 'short', 'few practice']):
            buckets["题目太少 / 刷题不尽兴 (Not Enough Practice)"].append(r)
            matched = True
            
        if not matched:
            buckets["其他买家吐槽 (General Dissatisfaction)"].append(r)
            
    return buckets

def generate_markdown_report(targets_data):
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
        md.append(f"**成功捕获买家反馈样本**: {len(reviews)} 条\n\n")
        md.append(f"### ⚡ 提炼出的高频缺陷分布:\n")
        
        has_any_flaw = False
        for category, revs in buckets.items():
            if revs:
                has_any_flaw = True
                md.append(f"#### 🔴 {category} ({len(revs)} 条买家真实反馈)\n")
                for sample in revs[:2]:
                    snippet = sample['body'].replace('\n', ' ')
                    if len(snippet) > 280:
                        snippet = snippet[:280] + "..."
                    md.append(f"> **评级: {sample['stars']} | \"{sample['title']}\"**  \n> {snippet}\n>\n")
                    
        if not has_any_flaw:
            md.append("*(该竞品暂未捕获到明显的负面抱怨段落)*\n")

        md.append("\n### 💡 针对该竞品的反制出版方案 (Counter-Strategy):\n")
        valid_buckets = {k: v for k, v in buckets.items() if k != "其他买家吐槽 (General Dissatisfaction)" and len(v) > 0}
        top_flaw = max(valid_buckets.items(), key=lambda x: len(x[1]))[0] if valid_buckets else "答案错误 / 题目有 Bug (Errors in Answers)"
        
        md.append(f"- **核心反杀卖点**: 强攻 **【{top_flaw}】**。\n")
        md.append(f"- **封面与副标题反制话术**:\n")
        md.append(f"  - 若错题多 ➔ 副标题必加 `100% Error-Free Verified Solutions`\n")
        md.append(f"  - 若无解析 ➔ 副标题必加 `Step-by-Step Logic for Every Math & Arithmetic Problem`\n")
        md.append(f"  - 若线上注册坑人 ➔ 副标题必加 `100% Offline: Zero Logins, Zero Registrations, Zero Online Codes`\n")
        md.append("\n---\n")
        
    with open(OUTPUT_MD, 'w', encoding='utf-8') as f:
        f.write("\n".join(md))
    print(f"[+] Battlecard written to: {OUTPUT_MD}")

def main():
    top_targets = get_top_asins(limit=3)
    if not top_targets:
        print("[!] No valid top BSR ASINs found.")
        return
        
    session = requests.Session()
    try:
        session.get("https://www.amazon.com", headers={'User-Agent': random.choice(USER_AGENTS)}, timeout=15)
        time.sleep(2)
    except:
        pass

    all_analyzed = []
    for target in top_targets:
        print(f"[*] Analyzing competitor ASIN: {target['asin']} (#{target['bsr']})")
        revs = scrape_reviews_for_asin(session, target['asin'])
        target['reviews'] = revs
        all_analyzed.append(target)
        
    generate_markdown_report(all_analyzed)

if __name__ == '__main__':
    main()
