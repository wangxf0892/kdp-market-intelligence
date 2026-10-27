import os
import re
import csv
import time
import random
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSR_FILE = os.path.join(BASE_DIR, 'data', 'bsr_report.csv')
OUTPUT_MD = os.path.join(BASE_DIR, 'data', 'pain_points_report.md')

def get_top_asins(limit=2):
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

def scrape_reviews_with_stealth(page, asin):
    """
    注入真实浏览器指纹，抹除 webdriver 标识，直达买家评论归档页
    """
    url = f"https://www.amazon.com/product-reviews/{asin}/?sortBy=recent&reviewerType=all_reviews"
    print(f"[*] Navigating stealth browser to review center: {url}")
    
    # 彻底抹除自动化指纹
    stealth_sync(page)
    
    sleep_time = random.uniform(5, 8)
    time.sleep(sleep_time)
    
    try:
        page.goto(url, wait_until="networkidle", timeout=35000)
        
        # 打印当前标题供日志排查
        page_title = page.title()
        print(f"[*] Landed on page title: '{page_title}'")
        
        if "Robot Check" in page_title or page.locator("form[action*='validateCaptcha']").count() > 0:
            print(f"[!] Hit Amazon Robot Check for ASIN: {asin}")
            return []
            
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        cards = soup.select('div[data-hook="review"]')
        if not cards:
            cards = soup.select('.review')
            
        print(f"[+] Found {len(cards)} review elements for {asin}")
        
        reviews = []
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
            body_el = card.select_one('[data-hook="review-body"] span') or card.select_one('[data-hook="review-body"]')
            body = body_el.get_text(strip=True) if body_el else ""
            
            if body:
                reviews.append({
                    'stars': stars,
                    'title': title,
                    'body': body.replace('\n', ' ').strip()
                })
                
        return reviews
        
    except Exception as e:
        print(f"[!] Stealth scrape error on {asin}: {e}")
        return []

def categorize_pain_points(reviews):
    buckets = {
        "答案错误 / 题目有 Bug (Errors in Answers)": [],
        "缺乏详细解题步骤 (No Explanations / Too Vague)": [],
        "排版混乱 / 字体不适 (Formatting & Layout Issues)": [],
        "内容过时 / 与新考试不符 (Outdated Content)": [],
        "线上激活码失效 / 强制注册 (Online Code / Login Traps)": [],
        "题目太少 / 刷题不尽兴 (Not Enough Practice)": [],
        "其他买家真实原话反馈 (General Feedback)": []
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
            buckets["其他买家真实原话反馈 (General Feedback)"].append(r)
            
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
        md.append(f"**成功截获真实买家反馈**: {len(reviews)} 条\n\n")
        md.append(f"### ⚡ 提炼出的高频缺陷分布:\n")
        
        has_any_flaw = False
        for category, revs in buckets.items():
            if revs:
                has_any_flaw = True
                md.append(f"#### 🔴 {category} ({len(revs)} 条买家真实原话)\n")
                for sample in revs[:3]:
                    snippet = sample['body']
                    if len(snippet) > 280:
                        snippet = snippet[:280] + "..."
                    md.append(f"> **评级: {sample['stars']} | \"{sample['title']}\"**  \n> {snippet}\n>\n")
                    
        if not has_any_flaw:
            md.append("*(该竞品页面未截获到有效文本)*\n")

        md.append("\n### 💡 针对该竞品的反制出版方案 (Counter-Strategy):\n")
        valid_buckets = {k: v for k, v in buckets.items() if k != "其他买家真实原话反馈 (General Feedback)" and len(v) > 0}
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
    top_targets = get_top_asins(limit=2)
    if not top_targets:
        print("[!] No valid top BSR ASINs found in report.")
        return

    with sync_playwright() as p:
        # 启动带防指纹参数的 Chromium
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        all_analyzed = []
        for target in top_targets:
            print(f"[*] Analyzing competitor ASIN with Stealth Browser: {target['asin']} (#{target['bsr']})")
            revs = scrape_reviews_with_stealth(page, target['asin'])
            target['reviews'] = revs
            all_analyzed.append(target)
            time.sleep(random.uniform(4, 7))
            
        browser.close()

    generate_markdown_report(all_analyzed)

if __name__ == '__main__':
    main()
