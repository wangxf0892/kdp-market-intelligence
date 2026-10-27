import os
import re
import csv
import ast
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
    'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
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

def fetch_reviews_via_ajax(session, asin):
    """
    直连带签名路由的 AJAX 管道，并通过 AST 反序列化精确还原 HTML
    """
    url = "https://www.amazon.com/hz/reviews-render/ajax/reviews/get/ref=cm_cr_arp_d_paging_btm_next_2"
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
        'filterByStar': '',  # 不在服务端限制，拿取全量近期反馈
        'filterByAge': '',
        'pageNumber': '1',
        'filterByLanguage': '',
        'filterByKeyword': '',
        'shouldAppend': 'undefined',
        'deviceType': 'desktop',
        'canShowIntHeader': 'undefined',
        'reftag': 'cm_cr_arp_d_paging_btm_next_2',
        'pageSize': '10',
        'asin': asin,
        'scope': 'reviewsAjax0'
    }
    
    sleep_time = random.uniform(8, 14)
    print(f"[*] Sleeping {sleep_time:.1f}s before fetching AJAX reviews for ASIN: {asin}")
    time.sleep(sleep_time)
    
    try:
        res = session.post(url, headers=headers, data=payload, timeout=25)
        print(f"[*] AJAX response status={res.status_code}, len={len(res.text)}")
        
        if res.status_code != 200:
            return []
            
        if "Robot Check" in res.text:
            print(f"[!] Captcha hit on reviews for ASIN: {asin}")
            return []

        # 策略 1：使用标准 AST 反序列化提取真实 HTML
        # 亚马逊返回结构为 &&&["append","#cm_cr-review_list","<div id=...</div>"]
        raw_divs = re.findall(r'"<div id=.*?</div>"', res.text)
        reviews = []

        if raw_divs:
            html_blocks = []
            for d in raw_divs:
                try:
                    html_blocks.append(ast.literal_eval(d))
                except Exception:
                    pass
            
            soup = BeautifulSoup("\n".join(html_blocks), "html.parser")
            cards = soup.select('[data-hook="review"]')
            
            for card in cards:
                # 提取星级
                star_el = card.select_one('[data-hook="review-star-rating"] span.a-icon-alt') or card.select_one('.a-icon-alt')
                stars = star_el.get_text(strip=True) if star_el else "3.0 out of 5 stars"
                
                # 提取标题
                title_el = card.select_one('[data-hook="review-title"] span:not(.a-letter-space)') or card.select_one('[data-hook="review-title"]')
                title = title_el.get_text(strip=True) if title_el else ""
                if "stars" in title:
                    title = title.split("stars")[-1].strip()
                    
                # 提取正文
                body_el = card.select_one('[data-hook="review-body"]')
                body = body_el.get_text(strip=True) if body_el else ""
                
                if body:
                    reviews.append({'stars': stars, 'title': title, 'body': body})

        # 策略 2：如果 AST 匹配为空，启动全文正则抽取作为兜底
        if not reviews:
            print("[*] Falling back to direct regex extraction...")
            bodies = re.findall(r'data-hook=\\"review-body\\"[^>]*>.*?<span[^>]*>(.*?)</span>', res.text)
            titles = re.findall(r'data-hook=\\"review-title\\"[^>]*>.*?<span[^>]*>(.*?)</span>', res.text)
            
            for i in range(min(len(bodies), len(titles))):
                clean_body = re.sub(r'<.*?>', '', bodies[i]).replace('\\n', ' ').strip()
                clean_title = re.sub(r'<.*?>', '', titles[i]).replace('\\n', ' ').strip()
                if clean_body:
                    reviews.append({
                        'stars': "Customer Review",
                        'title': clean_title,
                        'body': clean_body
                    })

        print(f"[+] Total reviews successfully parsed for {asin}: {len(reviews)}")
        return reviews
        
    except Exception as e:
        print(f"[!] Error fetching AJAX for {asin}: {e}")
        return []

def categorize_pain_points(reviews):
    buckets = {
        "答案错误 / 题目有 Bug (Errors in Answers)": [],
        "缺乏详细解题步骤 (No Explanations / Too Vague)": [],
        "排版混乱 / 字体不适 (Formatting & Layout Issues)": [],
        "内容过时 / 与新考试不符 (Outdated Content)": [],
        "线上激活码失效 / 强制注册 (Online Code / Login Traps)": [],
        "题目太少 / 刷题不尽兴 (Not Enough Practice)": [],
        "一般买家吐槽与改进期待 (Specific Complaints)": []
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
            buckets["一般买家吐槽与改进期待 (Specific Complaints)"].append(r)
            
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
        valid_buckets = {k: v for k, v in buckets.items() if k != "一般买家吐槽与改进期待 (Specific Complaints)" and len(v) > 0}
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
        print("[!] No valid top BSR ASINs found in report.")
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
        revs = fetch_reviews_via_ajax(session, target['asin'])
        target['reviews'] = revs
        all_analyzed.append(target)
        
    generate_markdown_report(all_analyzed)

if __name__ == '__main__':
    main()
