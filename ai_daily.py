"""AI 日报 — 每日自动产出并分发"""
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ── 配置 ──
BG目录 = Path("backgrounds")
输出目录 = Path("output")
DEEPSEEK端口 = 9226
账号 = "xie_total_default"
平台列表 = ["kuaishou", "xiaohongshu", "douyin"]
最多条数 = 7
粗体字 = "C:/Windows/Fonts/msyhbd.ttc"
常规字体 = "C:/Windows/Fonts/msyh.ttc"
图片宽 = 1080
图片高 = 1920
FIRECRAWL_KEY = os.getenv("FIRECRAWL_KEY", "fc-da7871fc9d8f45aaafe06f75014f0603")
字体_行楷 = ImageFont.truetype("fonts/三极行楷简体-粗.ttf", 80)
字体_花体 = ImageFont.truetype("fonts/EnglandHandDB.ttf", 36)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s")
日志 = logging.getLogger(__name__)

# 预建固定尺寸的蒙层和卡片（省得每次重新建）
_蒙层厚 = Image.new("RGBA", (图片宽, 图片高), (0, 0, 0, 180))
_蒙层薄 = Image.new("RGBA", (图片宽, 图片高), (0, 0, 0, 120))
_卡片 = Image.new("RGBA", (920, 280), (255, 255, 255, 40))

# 背景图缓存（glob 一次后面复用）
_BG缓存 = None
def _取背景图():
    global _BG缓存
    if _BG缓存 is None:
        _BG缓存 = list(BG目录.glob("*.jpg")) + list(BG目录.glob("*.png")) + list(BG目录.glob("*.jpeg"))
    return _BG缓存 or None

def _加载底图(alpha: int) -> Image.Image:
    """加载背景图 + 半透明蒙层"""
    背景列表 = _取背景图()
    if 背景列表:
        底图 = Image.open(random.choice(背景列表)).resize((图片宽, 图片高)).convert("RGBA")
    else:
        底图 = Image.new("RGBA", (图片宽, 图片高), (10, 15, 40))
    蒙层 = _蒙层厚 if alpha >= 150 else _蒙层薄
    return Image.alpha_composite(底图, 蒙层)


# ============================================================
# Firecrawl 搜索
# ============================================================
FIRE_SEARCH = "https://api.firecrawl.dev/v1/search"

搜索词 = [
    ("36氪", "site:36kr.com AI 人工智能 最新"),
    ("知乎", "site:zhihu.com AI 人工智能 新闻"),
    ("虎嗅", "site:huxiu.com AI 人工智能"),
]

def _搜单源(名称: str, 关键词: str) -> list:
    """搜一个源，返回 [{标题, 链接, 来源, 描述}]"""
    结果 = []
    try:
        请求体 = json.dumps({"query": 关键词, "limit": 5}).encode()
        请求 = urllib.request.Request(FIRE_SEARCH, data=请求体,
            headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"})
        with urllib.request.urlopen(请求, timeout=15) as 响应:
            数据 = json.loads(响应.read())
        for 条目 in 数据.get("data", []):
            标题 = 条目.get("title", "").strip()
            if 标题:
                结果.append({"标题": 标题, "链接": 条目.get("url", ""),
                            "来源": 名称, "描述": 条目.get("description", "")[:300]})
        日志.info(f"【{名称}】搜到 {len(数据.get('data',[]))} 条")
    except Exception as e:
        日志.warning(f"【{名称}】搜索失败: {e}")
    return 结果


FIRE_SCRAPE = "https://api.firecrawl.dev/v1/scrape"

def _爬全文(url: str) -> str | None:
    """抓取文章全文前 500 字"""
    try:
        请求体 = json.dumps({"url": url, "formats": ["markdown"]}).encode()
        请求 = urllib.request.Request(FIRE_SCRAPE, data=请求体,
            headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"})
        with urllib.request.urlopen(请求, timeout=15) as 响应:
            数据 = json.loads(响应.read())
        markdown = 数据.get("data", {}).get("markdown", "")[:500]
        return markdown.strip() or None
    except Exception:
        return None


def 采集() -> list:
    with ThreadPoolExecutor(max_workers=3) as 池:
        全部 = []
        for 一批 in 池.map(lambda s: _搜单源(*s), 搜索词):
            全部.extend(一批)
    去重, 见过 = [], set()
    for n in 全部:
        if n["标题"] not in 见过:
            见过.add(n["标题"]); 去重.append(n)

    # 并行爬全文
    def _补全文(条目):
        if 全文 := _爬全文(条目["链接"]):
            条目["描述"] = 全文
        return 条目

    with ThreadPoolExecutor(max_workers=5) as 池:
        去重 = list(池.map(_补全文, 去重))

    return 去重


# ============================================================
# DeepSeek 网页版精选
# ============================================================
def 精选(原始数据: list) -> list:
    """喂给 DeepSeek 网页版，返回 [{标题, 摘要, 排序}]"""
    from playwright.sync_api import sync_playwright

    # 连浏览器 CDP（用 /json/version 拿浏览器级地址）
    try:
        信息 = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEEPSEEK端口}/json/version", timeout=5).read())
        CDP地址 = 信息["webSocketDebuggerUrl"]
    except Exception as e:
        日志.error(f"CDP 连接失败: {e}")
        return []

    提示 = (
        "你是一个AI新闻编辑。从以下昨日AI新闻中选出最重要的5-7条，按热度排序。\n"
        "只输出JSON数组，不要多余文字：\n"
        "```json\n"
        '[{"标题":"标题(≤15字)","摘要":"摘要(≤60字)","排序":1}]\n'
        "```\n\n新闻：\n"
    )
    for i, n in enumerate(原始数据[:25]):
        提示 += f"{i+1}. {n['标题'][:100]}\n   简介: {n.get('描述', '')[:200]}\n"

    with sync_playwright() as p:
        浏览器 = p.chromium.connect_over_cdp(CDP地址)
        # 找 DeepSeek 页面
        页面 = None
        for ctx in 浏览器.contexts:
            for pg in ctx.pages:
                if "deepseek" in pg.url or "chat" in pg.url:
                    页面 = pg
                    break
        if not 页面:
            日志.error("没找到 DeepSeek 页面")
            return []

        # ponytail: 假设页面已打开 + 已登录
        输入框 = 页面.locator("textarea").first
        输入框.fill(提示)
        页面.keyboard.press("Enter")
        日志.info("等待 DeepSeek 回复...")
        time.sleep(30)  # ponytail: 固定等待
        # 拿最后一次回复（最后一条AI消息）
        回复 = 页面.locator(".ds-markdown, .message:last-child, [class*='ds-markdown']").last.text_content()

    # 解析 JSON
    匹配 = re.search(r'```json\s*([\s\S]*?)\s*```', 回复 or "")
    if 匹配:
        return json.loads(匹配.group(1))
    return json.loads(回复)  # 裸 JSON 兜底


# ============================================================
# Pillow 渲染
# ============================================================
def 居中写文字(绘图, 文字, 字体, 颜色, y坐标):
    """水平居中绘制文字"""
    边框 = 绘图.textbbox((0, 0), 文字, font=字体)
    x = (图片宽 - (边框[2] - 边框[0])) // 2
    绘图.text((x, y坐标), 文字, fill=颜色, font=字体)

def 生成封面(日期: str, 最热标题: str, 输出路径: Path) -> str:
    """纯黑底 + 最热标题 + 英文花体 + 日期"""
    底图 = Image.new("RGBA", (图片宽, 图片高), (2, 2, 5))
    绘图 = ImageDraw.Draw(底图)

    # 中文标题（居中偏上，如果太长就缩小）
    标题字体 = 字体_行楷
    bbox = 绘图.textbbox((0, 0), 最热标题, font=标题字体)
    标题宽 = bbox[2] - bbox[0]
    if 标题宽 > 900:
        标题字体 = ImageFont.truetype("fonts/三极行楷简体-粗.ttf", 60)
    elif 标题宽 > 700:
        标题字体 = ImageFont.truetype("fonts/三极行楷简体-粗.ttf", 70)
    居中写文字(绘图, 最热标题, 标题字体, (255, 255, 255), 700)

    # 英文花体（三行，居中）
    英文行 = ["A transformation", "beyond all measure", "is upon us"]
    y = 900
    for 行 in 英文行:
        居中写文字(绘图, 行, 字体_花体, (255, 255, 255), y)
        y += 50

    # 日期（右下角）
    绘图.text((1020, 1840), 日期, fill=(255, 255, 255),
              font=ImageFont.truetype(常规字体, 28), anchor="ra")

    文件路径 = str(输出路径 / "封面.png")
    底图.save(文件路径)
    return 文件路径

def 生成内容页(标题: str, 摘要: str, 页码: int, 总页: int, 输出路径: Path) -> str:
    """科技背景 + 卡片 + 标题 + 摘要"""
    底图 = _加载底图(120)
    绘图 = ImageDraw.Draw(底图)

    # 卡片（水平居中，顶部留 192px）
    卡片 = Image.new("RGBA", (920, 400), (255, 255, 255, 40))
    底图.paste(卡片, (80, 192), 卡片)

    # 标题（行楷，自适应大小确保不超卡片）
    标题尺寸 = 52
    for 尝试 in range(3):
        标题字体 = ImageFont.truetype(str(Path("fonts") / "三极行楷简体-粗.ttf"), 标题尺寸)
        bbox = 绘图.textbbox((0, 0), 标题, font=标题字体)
        if bbox[2] - bbox[0] <= 840:
            break
        标题尺寸 -= 8
    bbox = 绘图.textbbox((0, 0), 标题, font=标题字体)
    标题宽 = bbox[2] - bbox[0]
    x = (图片宽 - 标题宽) // 2
    绘图.text((x, 230), 标题, fill="white", font=标题字体)

    # 摘要（32px 常规，限制宽度）
    摘要字体 = ImageFont.truetype(常规字体, 32)
    bbox2 = 绘图.textbbox((0, 0), 摘要, font=摘要字体)
    if bbox2[2] - bbox2[0] > 840:
        摘要短 = 摘要[:50] + "..."
    else:
        摘要短 = 摘要
    bbox2 = 绘图.textbbox((0, 0), 摘要短, font=摘要字体)
    x2 = (图片宽 - (bbox2[2] - bbox2[0])) // 2
    绘图.text((x2, 340), 摘要短, fill="white", font=摘要字体)

    # 页码
    绘图.text((920, 1720), f"{页码}/{总页}", fill=(255, 255, 255, 120),
              font=ImageFont.truetype(常规字体, 28), anchor="ra")

    文件路径 = str(输出路径 / f"第{页码}页.png")
    底图.save(文件路径)
    return 文件路径

def 渲染轮播图(精选结果: list) -> tuple:
    """生成多图轮播，返回 (图片路径列表, 文案)"""
    今日 = datetime.now().strftime("%Y-%m-%d")
    本次输出 = 输出目录 / 今日
    本次输出.mkdir(parents=True, exist_ok=True)

    图片列表 = []
    # 封面
    图片列表.append(生成封面(今日, 精选结果[0]["标题"], 本次输出))
    # 内容页
    for i, 条目 in enumerate(精选结果):
        图片列表.append(生成内容页(条目["标题"], 条目["摘要"], i + 1, len(精选结果), 本次输出))

    # 文案
    文案 = f"🤖 AI 日报 | {今日}\n\n今日 {len(精选结果)} 条 AI 热点速览：\n\n"
    for i, 条目 in enumerate(精选结果, 1):
        文案 += f"{i}. {条目['标题']}\n"
        文案 += f"   {条目['摘要']}\n\n"
    文案 += "#AI #人工智能 #科技早报"

    return 图片列表, 文案


# ============================================================
# 发布
# ============================================================
def 发布(图片列表: list, 文案: str) -> dict:
    """并行发到各平台，互不阻塞，超时120s"""
    def _发一个(平台: str) -> tuple:
        命令 = ["sau", 平台, "upload-note",
                "--account", 账号,
                "--title", "AI 日报",
                "--note", 文案]
        for 图 in 图片列表:
            命令 += ["--images", 图]
        try:
            执行 = subprocess.run(命令, capture_output=False, timeout=120)
            ok = 执行.returncode == 0
            日志.info(f"【{平台}】{'OK' if ok else 'FAIL'}")
            return 平台, ok
        except subprocess.TimeoutExpired:
            日志.error(f"【{平台}】超时")
            return 平台, False
        except Exception as e:
            日志.error(f"【{平台}】异常: {e}")
            return 平台, False

    with ThreadPoolExecutor(max_workers=3) as 池:
        任务 = {池.submit(_发一个, p): p for p in 平台列表}
        结果 = {}
        for 完成 in as_completed(任务):
            p, ok = 完成.result()
            结果[p] = ok
        return 结果

# ============================================================
# 主入口
# ============================================================
def main():
    日志.info("=== AI 日报 开始 ===")

    # 1. 采集（带重试）
    # ponytail: while 循环重试，不写装饰器
    新闻 = None
    for 尝试 in range(3):
        try:
            新闻 = 采集()
            if 新闻:
                break
        except Exception as e:
            日志.warning(f"采集失败 (第{尝试+1}次): {e}")
            time.sleep(5)

    if not 新闻:
        日志.error("采集失败，退出")
        return False
    日志.info(f"采集到 {len(新闻)} 条原始新闻")

    # 2. 精选
    精选结果 = 精选(新闻)
    if not 精选结果:
        日志.error("精选失败，退出")
        return False
    日志.info(f"精选出 {len(精选结果)} 条热点")

    # 3. 渲染
    图片列表, 文案 = 渲染轮播图(精选结果[:最多条数])
    if not 图片列表:
        日志.error("渲染失败，退出")
        return False
    日志.info(f"生成 {len(图片列表)} 张图片")

    # 4. 发布
    发布结果 = 发布(图片列表, 文案)
    成功数 = sum(1 for v in 发布结果.values() if v)
    日志.info(f"完成: {成功数}/{len(发布结果)} 平台发布成功")

    # 5. 归档
    # ponytail: git 存档一步
    subprocess.run(["git", "commit", "-a", "-m", f"日常: AI日报 {datetime.now().strftime('%Y-%m-%d')}"],
                   cwd=Path(__file__).parent)

    return 成功数 > 0

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
