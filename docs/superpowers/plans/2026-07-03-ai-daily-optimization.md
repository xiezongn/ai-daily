# AI 日报优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 改善 AI 日报的信息量和视觉效果

**Architecture:** 单文件 `ai_daily.py` 修改 + 下载字体文件，无新增依赖

**Tech Stack:** Python 3.11, Pillow, urllib

---

### Task 1: 下载字体文件

- [ ] 下载三极行楷简体-粗.ttf，放到 `D:/public-workpace/ai-daily/fonts/`
- [ ] 下载 EnglandHandDB.ttf，放到 `D:/public-workpace/ai-daily/fonts/`

### Task 2: 新增全文爬取

**Files:**
- Modify: `D:/public-workpace/ai-daily/ai_daily.py`

在 `_搜单源()` 后面新增 `_爬全文(url)`：

```python
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
```

改 `采集()` 函数，去重后并行 scrape：

```python
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
```

### Task 3: 封面重做

**Files:**
- Modify: `D:/public-workpace/ai-daily/ai_daily.py` — `生成封面()` 函数

```python
字体_行楷 = ImageFont.truetype("fonts/三极行楷简体-粗.ttf", 80)
字体_花体 = ImageFont.truetype("fonts/EnglandHandDB.ttf", 36)

def 生成封面(日期: str, 最热标题: str, 输出路径: Path) -> str:
    """纯黑底 + 最热标题 + 英文花体 + 日期"""
    底图 = Image.new("RGBA", (图片宽, 图片高), (0, 0, 0))
    绘图 = ImageDraw.Draw(底图)

    # 中文标题（居中偏上）
    居中写文字(绘图, 最热标题, 字体_行楷, "white", 700)

    # 英文花体（三行，居中）
    英文行 = ["A transformation", "beyond all measure", "is upon us"]
    y = 900
    for 行 in 英文行:
        居中写文字(绘图, 行, 字体_花体, "white", y)
        y += 50

    # 日期（右下角）
    绘图.text((1020, 1840), 日期, fill="white",
              font=ImageFont.truetype(常规字体, 28), anchor="ra")

    文件路径 = str(输出路径 / "封面.png")
    底图.save(文件路径)
    return 文件路径
```

同时修改 `渲染轮播图()` 中调用 `生成封面()` 处，传入最热标题：

```python
图片列表.append(生成封面(今日, 精选结果[0]["标题"], 本次输出))
```

### Task 4: 内容页重做

**Files:**
- Modify: `D:/public-workpace/ai-daily/ai_daily.py` — `生成内容页()` 函数

```python
def 生成内容页(标题: str, 摘要: str, 页码: int, 总页: int, 输出路径: Path) -> str:
    """科技背景 + 卡片 + 标题 + 摘要"""
    底图 = _加载底图(120)
    绘图 = ImageDraw.Draw(底图)

    # 卡片（水平居中，顶部留 192px）
    卡片 = Image.new("RGBA", (920, 380), (255, 255, 255, 40))
    底图.paste(卡片, (80, 192), 卡片)

    # 标题（52px 行楷）
    标题字体 = ImageFont.truetype(str(Path("fonts") / "三极行楷简体-粗.ttf"), 52)
    bbox = 绘图.textbbox((0, 0), 标题, font=标题字体)
    标题宽 = bbox[2] - bbox[0]
    x = (图片宽 - 标题宽) // 2
    绘图.text((x, 230), 标题, fill="white", font=标题字体)

    # 摘要（32px 常规）
    摘要字体 = ImageFont.truetype(常规字体, 32)
    bbox2 = 绘图.textbbox((0, 0), 摘要, font=摘要字体)
    摘要宽 = bbox2[2] - bbox2[0]
    # 若摘要太长就截短
    if 摘要宽 > 840:
        摘要短 = 摘要[:50] + "..."
    else:
        摘要短 = 摘要
    bbox2 = 绘图.textbbox((0, 0), 摘要短, font=摘要字体)
    摘要宽 = bbox2[2] - bbox2[0]
    x2 = (图片宽 - 摘要宽) // 2
    绘图.text((x2, 320), 摘要短, fill="white", font=摘要字体)

    # 页码
    绘图.text((920, 1720), f"{页码}/{总页}", fill=(255, 255, 255, 120),
              font=ImageFont.truetype(常规字体, 28), anchor="ra")

    文件路径 = str(输出路径 / f"第{页码}页.png")
    底图.save(文件路径)
    return 文件路径
```

修改 `渲染轮播图()` 调用处：

```python
图片列表.append(生成内容页(条目["标题"], 条目["摘要"], i + 1, len(精选结果), 本次输出))
```

### Task 5: 文案重写

**Files:**
- Modify: `D:/public-workpace/ai-daily/ai_daily.py` — `渲染轮播图()` 中文案生成部分

```python
# 文案
文案 = f"🤖 AI 日报 | {今日}\n\n今日 {len(精选结果)} 条 AI 热点速览：\n\n"
for i, 条目 in enumerate(精选结果, 1):
    文案 += f"{i}. {条目['标题']}\n"
    文案 += f"   {条目['摘要']}\n\n"
文案 += "#AI #人工智能 #科技早报"
```

### Task 6: 验证运行

- [ ] 运行 `cd D:/public-workpace/ai-daily && python ai_daily.py`
- [ ] 检查 `output/` 下封面图是否正确（纯黑底 + 行楷标题 + 花体英文）
- [ ] 检查内容页是否有标题 + 摘要
- [ ] 检查文案格式是否为汇总格式
