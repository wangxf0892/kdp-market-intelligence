import os
import re
import csv
import time
import random
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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

def scrape_reviews_with_browser(page, asin):
    """
    启动真实 Chrome 浏览器，模拟真人滚动页面，强制触发客户端 JavaScript 懒加载评论
    """
    url = f"https://www.amazon.com/dp/{asin}"
    print(f"[*] Navigating headless Chromium to: {url}")
    
    try:
        # 打开页面，等待网络空闲
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        
        # 检查是否遇到验证码
        if "Robot Check" in page.title() or page.locator("form[action*='validateCaptcha']").count() > 0:
            print(f"[!] Captcha hit on browser for {asin}")
            return []
            
        # 核心动作：模拟真实用户向下滚动触发懒加载
        print("[*] Scrolling to activate lazy-loaded customer reviews...")
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 1800);")
            time.sleep(2)
            
        # 尝试显式等待评论容器渲染（最多等 8 秒）
        try:
            page.wait_for_selector("#cm-cr-dp-review-list, [data-hook='review']", timeout=8000)
        except:
            pass

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        cards = soup.select('[data-hook="review"]')
        if not cards:
            cards = soup.select('#cm-cr-dp-review-list div.review')
            
        print(f"[+] Successfully extracted {len(cards)} review elements via Playwright for {asin}")
        
        reviews = []
        for card in cards:
            # 提取星级
            star_el = card.select_one('[data-hook="review-star-rating"]') or card.select_one('.a-icon-alt')
            stars = star_el.get_text(strip=True) if star_el else "3.0 out of 5 stars"
            
            # 提取标题
            title_el = card.select_one('[data-hook="review-title"]')
            title = title_el.get_text(strip=True) if title_el else ""
            if "stars" in title:
                title = title.split("stars")[-1].strip()
                
            # 提取正文
            body_el = card.select_one('[data-hook="review-body"]')
            body = body_el.get_text(strip=True) if body_el else ""
            
            if body:
                reviews.append({
                    'stars': stars,
                    'title': title,
                    'body': body
                })
                
        return reviews
        
    except Exception as e:
        print(f"[!] Browser scrape error on {asin}: {e}")
        return []

def categorize_pain_points(reviews):
    buckets = {
        "答案错误 / 题目有 Bug (Errors in Answers)": [],
        "缺乏详细解题步骤 (No Explanations / Too Vague)": [],
        "排版混乱 / 字体不适 (Formatting & Layout Issues)": [],
        "内容过时 / 与新考试不符 (Outdated Content)": [],
        "线上激活码失效 / 强制注册 (Online Code / Login Traps)": [],
        "题目太少 / 刷题不尽兴 (Not Enough Practice)": [],
        "其他买家真实吐槽与反馈 (General Complaints)": []
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
            buckets["其他买家真实吐槽与反馈 (General Complaints)"].append(r)
            
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
        md.append(f"**成功抓获真实买家反馈**: {len(reviews)} 条\n\n")
        md.append(f"### ⚡ 提炼出的高频缺陷分布:\n")
        
        has_any_flaw = False
        for category, revs in buckets.items():
            if revs:
                has_any_flaw = True
                md.append(f"#### 🔴 {category} ({len(revs)} 条买家真实原话)\n")
                for sample in revs[:3]:
                    snippet = sample['body'].replace('\n', ' ')
                    if len(snippet) > 280:
                        snippet = snippet[:280] + "..."
                    md.append(f"> **评级: {sample['stars']} | \"{sample['title']}\"**  \n> {snippet}\n>\n")
                    
        if not has_any_flaw:
            md.append("*(该竞品页面未抓取到有效文本)*\n")

        md.append("\n### 💡 针对该竞品的反制出版方案 (Counter-Strategy):\n")
        valid_buckets = {k: v for k, v in buckets.items() if k != "其他买家真实吐槽与反馈 (General Complaints)" and len(v) > 0}
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
        # 启动 Chromium 无头浏览器，配置真实视口与语言环境
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1440, "height": 900}
        )
        page = context.new_page()

        all_analyzed = []
        for target in top_targets:
            print(f"[*] Analyzing competitor ASIN with Chromium: {target['asin']} (#{target['bsr']})")
            revs = scrape_reviews_with_browser(page, target['asin'])
            target['reviews'] = revs
            all_analyzed.append(target)
            time.sleep(random.uniform(5, 8))
            
        browser.close()

    generate_markdown_report(all_analyzed)

if __name__ == '__main__':
    main()
