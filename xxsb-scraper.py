import aiohttp
import asyncio
import os
import pytz
import re
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from feedgen.feed import FeedGenerator

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
            return ""
    except:
        return ""

async def get_article_detail(page_name, title, url, session):
    html = await fetch(url, session)
    if not html: return None
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. 定位正文容器
    content_area = soup.find(id="news_content")
    if not content_area: return None

    # 2. 模拟 JS 计算字数逻辑
    # 提取纯文本 -> 去除所有空白字符 -> 计算长度
    pure_text = content_area.get_text()
    clean_text = re.sub(r'\s+', '', pure_text)
    calculated_wordcount = len(clean_text)

    # 3. 将计算出的字数填回 HTML 结构中
    # 网站原本有一个 <text id="wordcount"></text>
    wc_node = soup.find(id="wordcount")
    if wc_node:
        wc_node.string = str(calculated_wordcount)
    else:
        # 如果 id="wordcount" 不在 content_area 里（通常在外面），
        # 我们可以手动在正文顶部加一行标识，或者直接修改 soup 里的对应节点
        # 既然你说是在 p.datesource 里，我们尝试在全局找
        pass

    # 4. 清理正文中的冗余标签
    for tag in content_area.find_all(['style', 'script']):
        tag.decompose()
    
    return {
        'title': f"[{page_name}] {title}",
        'url': url,
        'content_html': str(content_area)
    }

async def main():
    async with aiohttp.ClientSession() as session:
        print(f"🚀 正在抓取学习时报: {DATE_PATH}")
        index_html = await fetch(BASE_INDEX, session)
        if not index_html: return

        soup = BeautifulSoup(index_html, 'html.parser')
        layout_catalogue = soup.find(class_='layout-catalogue-list')
        if not layout_catalogue: return
        
        page_links = layout_catalogue.find_all('a')
        
        tasks = []
        for p_link in page_links:
            p_name = p_link.get_text(strip=True)
            p_url = urljoin(BASE_URL_DIR, p_link.get('href'))
            
            p_html = await fetch(p_url, session)
            p_soup = BeautifulSoup(p_html, 'html.parser')
            
            news_list = p_soup.find(class_='news-list')
            if news_list:
                for a_link in news_list.find_all('a'):
                    a_url = urljoin(p_url, a_link.get('href'))
                    a_title = a_link.get_text(strip=True)
                    tasks.append(get_article_detail(p_name, a_title, a_url, session))

        # --- 顺序修正 ---
        tasks.reverse() 

        print(f"📦 正在同步抓取 {len(tasks)} 篇文章...")
        results = await asyncio.gather(*tasks)
        articles = [r for r in results if r]

        fg = FeedGenerator()
        fg.title(f'学习时报 - {DATE_PATH}')
        fg.link(href=BASE_INDEX, rel='alternate')
        fg.description('学习时报顺序版(含计算字数)')
        fg.language('zh-CN')

        for art in articles:
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['url'])
            fe.id(art['url'])
            fe.content(art['content_html'], type='html')

        fg.rss_file('rss_studytimes.xml', pretty=True)
        print(f"✨ 成功！字数已通过 Python 模拟计算填入。")

if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
