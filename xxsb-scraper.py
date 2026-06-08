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
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

async def fetch(url, session):
    try:
        async with session.get(url, headers=DEFAULT_HEADERS, timeout=20) as response:
            if response.status == 200:
                raw_data = await response.read()
                return raw_data.decode('utf-8', errors='ignore')
            else:
                print(f"❌ 网页请求失败 [状态码 {response.status}]: {url}")
                return ""
    except Exception as e:
        print(f"❌ 网络请求异常 [{type(e).__name__}]: {url}")
        return ""

async def get_article_detail(page_name, title, url, session):
    html = await fetch(url, session)
    if not html: 
        print(f"⚠️ 跳过解析（文章HTML为空）: {url}")
        return None
        
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. 定位正文容器
    content_area = soup.find(id="news_content")
    if not content_area: 
        print(f"❌ 无法解析正文（未找到 id='news_content'）: {url} | 标题: {title}")
        return None

    # 2. 模拟 JS 计算字数逻辑
    pure_text = content_area.get_text()
    clean_text = re.sub(r'\s+', '', pure_text)
    calculated_wordcount = len(clean_text)

    # 3. 将计算出的字数填回 HTML 结构中
    wc_node = soup.find(id="wordcount")
    if wc_node:
        wc_node.string = f"{calculated_wordcount}字"
    else:
        # 如果找不到全局wordcount节点，在正文前追加一个字数提示（可选）
        pass

    # 4. 清理正文中的冗余标签
    for tag in content_area.find_all(['style', 'script']):
        tag.decompose()
    
    final_title = f"[{page_name}] {title} ({calculated_wordcount}字)"
    print(f"✅ 成功抓取: {final_title}")
    
    return {
        'title': final_title,
        'url': url,
        'content_html': str(content_area)
    }

async def parse_single_page(p_name, p_url, session):
    """新增：异步解析单个版面，提取文章链接"""
    p_html = await fetch(p_url, session)
    if not p_html:
        print(f"⚠️ 无法获取版面页面: {p_name} -> {p_url}")
        return []
        
    p_soup = BeautifulSoup(p_html, 'html.parser')
    news_list = p_soup.find(class_='news-list')
    
    articles_in_page = []
    if news_list:
        for a_link in news_list.find_all('a'):
            a_href = a_link.get('href')
            if a_href:
                a_url = urljoin(p_url, a_href)
                a_title = a_link.get_text(strip=True)
                articles_in_page.append((p_name, a_title, a_url))
    return articles_in_page

async def main():
    async with aiohttp.ClientSession() as session:
        print(f"🚀 自动化抓取启动 | 目标日期: {DATE_PATH}")
        print(f"🔗 正在请求首页: {BASE_INDEX}")
        index_html = await fetch(BASE_INDEX, session)
        if not index_html:
            print(f"🛑 错误: 无法获取 {DATE_PATH} 的报纸首页，可能尚未更新或网络阻断。")
            return

        soup = BeautifulSoup(index_html, 'html.parser')
        layout_catalogue = soup.find(class_='layout-catalogue-list')
        if not layout_catalogue: 
            print("🛑 错误: 首页解析失败，未找到 'layout-catalogue-list' 标签！")
            return
        
        page_links = layout_catalogue.find_all('a')
        print(f"📊 首页解析成功，发现 {len(page_links)} 个版面。开始并发解析各版面文章...")
        
        # --- 优化1：并发解析所有版面 ---
        page_tasks = []
        for p_link in page_links:
            p_name = p_link.get_text(strip=True)
            p_url = urljoin(BASE_URL_DIR, p_link.get('href'))
            page_tasks.append(parse_single_page(p_name, p_url, session))
            
        page_results = await asyncio.gather(*page_tasks)
        
        # 汇总所有文章的任务
        article_tasks = []
        for article_list in page_results:
            for p_name, a_title, a_url in article_list:
                article_tasks.append(get_article_detail(p_name, a_title, a_url, session))

        total_links = len(article_tasks)
        print(f"📦 发现总文章链接数: {total_links} 条。开始并发下载...")

        # --- 优化2：倒序修正 ---
        article_tasks.reverse() 

        # 并发抓取文章详情
        results = await asyncio.gather(*article_tasks)
        articles = [r for r in results if r]
        
        print(f"📊 完成并发下载。共发现链接 {total_links} 条，成功解析正文 {len(articles)} 篇。")

        # --- 生成 RSS 文件 ---
        fg = FeedGenerator()
        fg.title(f'学习时报 - {DATE_PATH}')
        fg.link(href=BASE_INDEX, rel='alternate')
        fg.description('学习时报顺序版(含计算字数)')
        fg.language('zh-CN')

        rss_count = 0
        for art in articles:
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['url'])
            
            # 【重要修改】使用 URL + 标题 生成唯一ID，防止相同URL在不同版面被覆盖
            unique_id = f"{art['url']}#{art['title']}"
            fe.id(unique_id)
            
            fe.content(art['content_html'], type='html')
            rss_count += 1

        print(f"📝 正在写入 RSS 文件，共 {rss_count} 条 Item...")
        fg.rss_file('rss_studytimes.xml', pretty=True)
        print(f"✨ 成功！文件已保存至: rss_studytimes.xml")

if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
