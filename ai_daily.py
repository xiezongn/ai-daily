"""AI 日报 — 每日自动产出并分发"""
import json
import logging
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
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

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s")
日志 = logging.getLogger(__name__)


# ============================================================
# Firecrawl 搜索 + 爬取
# ============================================================
FIRECRAWL_KEY = "fc-da7871fc9d8f45aaafe06f75014f0603"
FIRE_SEARCH = "https://api.firecrawl.dev/v1/search"
FIRE_SCRAPE = "https://api.firecrawl.dev/v1/scrape"

搜索词 = [
    ("36氪", "site:36kr.com AI 人工智能 最新"),
    ("知乎", "site:zhihu.com AI 人工智能 新闻"),
    ("虎嗅", "site:huxiu.com AI 人工智能"),
]

def 采集() -> list:
    """用 Firecrawl 搜 AI 热点，返回 [{标题, 链接, 来源}]"""
    全部 = []
    for 名称, 关键词 in 搜索词:
        try:
            请求体 = json.dumps({"query": 关键词, "limit": 5}).encode()
            请求 = urllib.request.Request(FIRE_SEARCH, data=请求体,
                headers={"Authorization": f"Bearer {FIRECRAWL_KEY}", "Content-Type": "application/json"})
            响应 = json.loads(urllib.request.urlopen(请求, timeout=15).read())
            for 条目 in 响应.get("data", []):
                标题 = 条目.get("title", "").strip()
                if 标题:
                    全部.append({"标题": 标题, "链接": 条目.get("url", ""), "来源": 名称})
            日志.info(f"【{名称}】搜到 {len(响应.get('data',[]))} 条")
        except Exception as e:
            日志.warning(f"【{名称}】搜索失败: {e}")
    # 简单去重
    去重, 见过 = [], set()
    for n in 全部:
        if n["标题"] not in 见过:
            见过.add(n["标题"]); 去重.append(n)
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
        提示 += f"{i+1}. {n['标题'][:100]}\n"

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

def 生成封面(日期: str, 条数: int, 输出路径: Path) -> str:
    """生成封面图"""
    背景图列表 = list(BG目录.glob("*"))
    if 背景图列表:
        底图 = Image.open(random.choice(背景图列表)).resize((图片宽, 图片高)).convert("RGBA")
    else:
        底图 = Image.new("RGBA", (图片宽, 图片高), (10, 15, 40))
    # 半透明黑色蒙层
    蒙层 = Image.new("RGBA", 底图.size, (0, 0, 0, 180))
    底图 = Image.alpha_composite(底图, 蒙层)
    绘图 = ImageDraw.Draw(底图)

    大字体 = ImageFont.truetype(粗体字, 96)
    中字体 = ImageFont.truetype(粗体字, 36)
    居中写文字(绘图, "AI 日报", 大字体, "white", 820)
    居中写文字(绘图, 日期, 中字体, "white", 960)
    居中写文字(绘图, f"今日 {条数} 条热点", 中字体, "white", 1020)

    文件路径 = str(输出路径 / "封面.png")
    底图.save(文件路径)
    return 文件路径

def 生成内容页(标题: str, 页码: int, 总页: int, 输出路径: Path) -> str:
    """生成单条新闻内容页"""
    背景图列表 = list(BG目录.glob("*"))
    if 背景图列表:
        底图 = Image.open(random.choice(背景图列表)).resize((图片宽, 图片高)).convert("RGBA")
    else:
        底图 = Image.new("RGBA", (图片宽, 图片高), (10, 15, 40))
    蒙层 = Image.new("RGBA", 底图.size, (0, 0, 0, 120))
    底图 = Image.alpha_composite(底图, 蒙层)

    # 文字卡片（毛玻璃效果用半透明白色矩形模拟）
    卡片 = Image.new("RGBA", (920, 280), (255, 255, 255, 40))
    底图.paste(卡片, (80, 820), 卡片)
    绘图 = ImageDraw.Draw(底图)

    标题字体 = ImageFont.truetype(粗体字, 52)
    小字体 = ImageFont.truetype(常规字体, 28)
    居中写文字(绘图, 标题, 标题字体, "white", 960)
    绘图.text((920, 1720), f"{页码}/{总页}", fill=(255, 255, 255, 120), font=小字体, anchor="ra")

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
    图片列表.append(生成封面(今日, len(精选结果), 本次输出))
    # 内容页
    for i, 条目 in enumerate(精选结果):
        图片列表.append(生成内容页(条目["标题"], i + 1, len(精选结果), 本次输出))

    # 文案
    文案行 = ["🤖 AI 日报\n"]
    for 条目 in 精选结果:
        文案行.append(f"🔥 {条目['标题']}")
        文案行.append(f"{条目['摘要']}\n")
    文案 = "\n".join(文案行)

    return 图片列表, 文案


# ============================================================
# 发布
# ============================================================
def 发布(图片列表: list, 文案: str) -> dict:
    """调用 sau CLI 发布到各平台，返回 {平台: 成功/失败}"""
    结果 = {}
    for 平台 in 平台列表:
        try:
            # ponytail: 直接调 CLI，不 capture_output（sau 输出有 emoji，GBK 解码会炸）
            命令 = ["sau", 平台, "upload-note",
                    "--account", 账号,
                    "--title", "AI 日报",
                    "--note", 文案]
            for 图 in 图片列表:
                命令 += ["--images", 图]
            执行 = subprocess.run(命令, capture_output=False, timeout=300)
            结果[平台] = 执行.returncode == 0
            日志.info(f"【{平台}】{'OK' if 结果[平台] else 'FAIL (code %d)' % 执行.returncode}")
        except subprocess.TimeoutExpired:
            结果[平台] = False
            日志.error(f"【{平台}】超时")
        except Exception as e:
            结果[平台] = False
            日志.error(f"【{平台}】异常: {e}")
    return 结果

def 测试发布():
    """用测试图验证各平台发布"""
    import subprocess
    for 平台 in 平台列表:
        print(f"\n=== 测试 {平台} ===")
        命令 = ["sau", 平台, "upload-note",
                "--account", 账号,
                "--title", "AI日报测试",
                "--note", "这是一条测试，稍后删除",
                "--images", "output/test.png",
                "--headless"]
        r = subprocess.run(命令, capture_output=True, text=True, timeout=180)
        print(f"返回码: {r.returncode}")
        print(f"输出: {r.stdout[:300]}")
        if r.stderr:
            print(f"错误: {r.stderr[:300]}")


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
    # ponytail: git 存档一步，不写归档模块
    subprocess.run(["git", "add", "-A"], cwd=Path(__file__).parent)
    subprocess.run(
        ["git", "commit", "-m", f"日常: AI日报 {datetime.now().strftime('%Y-%m-%d')}"],
        cwd=Path(__file__).parent,
    )

    return 成功数 > 0

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
