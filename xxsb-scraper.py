import aiohttp
import asyncio
import os
import pytz
import re
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from feedgen.feed import FeedGenerator

# --- 自动获取日期配置 ---
def get_bj_date():
    tz = pytz.timezone('Asia/Shanghai')
    return datetime.now(tz).strftime("%Y-%m/%d")

DATE_PATH = get_bj_date()
BASE_URL_DIR = f"https://paper.studytimes.cn/cntheory/{DATE_PATH}/"
BASE_INDEX = urljoin(BASE_URL_DIR, "node_1.html")

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
}

# 限制并发连接数
CONNECTOR = aiohttp.TCPConnector(limit_per_host=5, limit=10)

async def fetch(url, session, retries=3):
    """带重试的抓取函数"""
    for attempt in range(retries):
        try:
            async with session.get(url, headers=DEFAULT_HEADERS, timeout=20) as response:
                if response.status == 200:
                    raw_data = await response.read()
                    return raw_data.decode('utf-8', errors='ignore')
                else:
                    print(f"❌ 请求失败 [状态码 {response.status}]: {url}")
                    if attempt < retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue
        except Exception as e:
            print(f"❌ 网络异常 [{type(e).__name__}]: {url}")
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            continue
    return ""

async def get_article_detail(page_name, title, url, session):
    """抓取文章详情，失败时返回占位条目"""
    html = await fetch(url, session)
    if not html:
        print(f"⚠️ 最终失败: {url}")
        return {
            'title': f"[{page_name}] {title} (抓取失败)",
            'url': url,
            'content_html': '<p>抓取失败，请点击链接查看原文。</p>',
            'success': False
        }

    soup = BeautifulSoup(html, 'html.parser')
    content_area = soup.find(id="news_content")
    if not content_area:
        print(f"❌ 未找到正文容器: {url}")
        return {
            'title': f"[{page_name}] {title} (解析失败)",
            'url': url,
            'content_html': '<p>正文解析失败，请点击链接查看原文。</p>',
            'success': False
        }

    # 计算字数
    pure_text = content_area.get_text()
    clean_text = re.sub(r'\s+', '', pure_text)
    word_count = len(clean_text)

    # 更新字数节点
    wc_node = soup.find(id="wordcount")
    if wc_node:
        wc_node.string = f"{word_count}字"

    # 清理冗余标签
    for tag in content_area.find_all(['style', 'script']):
        tag.decompose()

    final_title = f"[{page_name}] {title} ({word_count}字)"
    print(f"✅ 成功抓取: {final_title}")
    return {
        'title': final_title,
        'url': url,
        'content_html': str(content_area),
        'success': True
    }

async def parse_single_page(p_name, p_url, session):
    """解析单个版面，提取文章链接"""
    p_html = await fetch(p_url, session)
    if not p_html:
        print(f"⚠️ 无法获取版面页面: {p_name}")
        return []
    p_soup = BeautifulSoup(p_html, 'html.parser')
    news_list = p_soup.find(class_='news-list')
    articles = []
    if news_list:
        for a_link in news_list.find_all('a'):
            href = a_link.get('href')
            if href:
                url = urljoin(p_url, href)
                title = a_link.get_text(strip=True)
                articles.append((p_name, title, url))
    return articles

async def main():
    async with aiohttp.ClientSession(connector=CONNECTOR) as session:
        print(f"🚀 抓取启动 | 日期: {DATE_PATH}")
        print(f"🔗 首页: {BASE_INDEX}")
        index_html = await fetch(BASE_INDEX, session)
        if not index_html:
            print("🛑 无法获取首页，程序退出。")
            return

        soup = BeautifulSoup(index_html, 'html.parser')
        layout_catalogue = soup.find(class_='layout-catalogue-list')
        if not layout_catalogue:
            print("🛑 未找到版面列表，程序退出。")
            return

        # 获取所有版面链接
        page_links = layout_catalogue.find_all('a')
        print(f"📊 发现 {len(page_links)} 个版面。")

        # ---- 按版面号升序排序（提取数字） ----
        def get_page_order(a_tag):
            text = a_tag.get_text(strip=True)
            match = re.search(r'(\d+)', text)
            return int(match.group(1)) if match else 999
        page_links = sorted(page_links, key=get_page_order)

        # 并发解析所有版面
        page_tasks = []
        for p_link in page_links:
            p_name = p_link.get_text(strip=True)
            p_url = urljoin(BASE_URL_DIR, p_link.get('href'))
            page_tasks.append(parse_single_page(p_name, p_url, session))

        page_results = await asyncio.gather(*page_tasks)

        # 汇总文章任务（顺序已经按版面升序）
        article_tasks = []
        for article_list in page_results:
            for p_name, a_title, a_url in article_list:
                article_tasks.append(
                    get_article_detail(p_name, a_title, a_url, session)
                )

        total_links = len(article_tasks)
        print(f"📦 总文章数: {total_links}")

        # 控制并发（信号量）
        semaphore = asyncio.Semaphore(5)

        async def limited_task(task):
            async with semaphore:
                result = await task
                await asyncio.sleep(0.5)  # 请求间隔
                return result

        results = await asyncio.gather(*[limited_task(task) for task in article_tasks])

        success_count = sum(1 for r in results if r['success'])
        fail_count = total_links - success_count
        print(f"抓取完成。成功 {success_count} 篇，失败 {fail_count} 篇。")

        # 生成 RSS（包含所有条目）
        fg = FeedGenerator()
        fg.title(f'学习时报 - {DATE_PATH}')
        fg.link(href=BASE_INDEX, rel='alternate')
        fg.description('学习时报顺序版（含占位条目）')
        fg.language('zh-CN')

        for art in results:
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['url'])
            fe.id(f"{art['url']}#{art['title']}")
            fe.content(art['content_html'], type='html')

        fg.rss_file('rss_studytimes.xml', pretty=True)
        print(f"✨ RSS 已生成: rss_studytimes.xml （共 {len(results)} 条）")

if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
