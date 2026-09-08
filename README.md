# WeRead Exporter

独立的微信读书导出工具，使用 Click + Playwright + uv。要求 Python 3.13+，使用 `uv.lock` 锁定依赖。
支持扫码登录、交互式章节选择、章节范围、批量书籍配置、失败重试、断点续传和离线导出。
运行、开发和构建依赖均以当前最新稳定版为最低版本，不设置版本上限。
`uv.lock` 固定实际安装版本；后续执行 `uv lock --upgrade && uv sync` 更新到最新兼容版本。

### 安装与运行

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后在项目根目录执行：

```bash
cd ~/workspaces/weread-exporter
uv sync
uv run playwright install chromium
uv run weread-exporter login

# 全书同时导出四种格式，每种格式一个文件
uv run weread-exporter download fa3322105ca0ecfa382cac6 --all \
  -f epub -f pdf -f markdown -f txt --cache

weread-exporter download 394326d0813abb693g011371 --all -f pdf --cache
```

如果已经安装 Chrome，可省略 Chromium 安装，给 `login` 和 `download` 添加 `--channel chrome`。
也可用 `--browser-path` 指定浏览器程序路径。Linux 可执行
`uv run playwright install --with-deps chromium` 安装浏览器系统依赖。

首次登录需要在浏览器中扫码，然后回到终端确认。登录状态默认保存在独立的
`data/user-data/`，后续可以使用 `--headless`。浏览器会在任务结束时自动关闭。
下载直接打开 `/web/reader/书籍ID`，无需经过详情页或点击“开始阅读”。

```bash
# 交互选择章节（序号、序号范围或 all）
uv run weread-exporter download fa3322105ca0ecfa382cac6

# 按完整章节名称选择，... 表示包含两端的范围，也支持开放范围
uv run weread-exporter download BOOK_ID -c '第一章' -c '...' -c '第五章' -f epub

# 已登录时，无界面执行；复用之前下载的完整章节
uv run weread-exporter download BOOK_ID --all --headless --cache -f pdf

# 从缓存离线转换，不需要浏览器或再次登录
uv run weread-exporter export 'output/书名_BOOK_ID/book.json' \
  -f epub -f pdf -f markdown -f txt

uv run weread-exporter --help
uv run weread-exporter download --help
uv run weread-exporter export --help
```

`BOOK_ID` 替换为微信读书的书籍详情 ID，获取方式见下方 FAQ。
不传书籍 ID 时，程序优先使用配置中的书单，否则提示输入。

### 导出格式与缓存

| 格式 | 输出内容 |
| --- | --- |
| EPUB | 每本书一个 EPUB，包含书名、作者、章节目录、正文和内嵌插图 |
| PDF | 每本书一个 PDF，含封面、章节列表、分页正文、页码和插图 |
| Markdown | 每本书一个 `.md`，保留章节标题、列表、表格及 `assets/` 中的本地插图 |
| TXT | 每本书一个 UTF-8 `.txt`，保留章节标题和段落，图片用文字占位 |

`-f/--format` 可重复，默认 `txt`。四种格式均直接生成整本文件，不生成单章 TXT/Markdown 中间文件。
下载时只保留用于续传和离线转换的结构化缓存与插图，所选章节全部完成后再输出成品。
使用 `--all` 导出全书；如果选择了部分章节，则按目录顺序汇总到一个文件。这些格式是**输出格式**，
离线 `export` 的输入是程序生成的 `book.json`，不提供任意 EPUB/PDF 文件之间的转换。

输出结构：

```text
output/书名_BOOK_ID/
  book.json                 # 本次章节选择、元数据与规范化 HTML
  .cache/章节UID.json        # 已完成章节，用于 --cache 续传
  assets/                   # 本地插图
  书名.txt
  书名.md
  书名.epub
  书名.pdf
```

本项目以结构化 HTML 缓存为准，不会把旧 TypeScript 版的 TXT 当作完整章节缓存。
文件通过临时文件原子替换写入；缺失分段、空正文、缺失解密入口和下载失败会报错。
中断后使用相同参数加 `--cache` 续传。所选章节未全部完成时，离线导出会拒绝生成整本文件。
重复章节名可用交互式序号选择或 `--all`；按名称选择存在歧义时会报错。
插图优先使用 `data-src` 中的真实图片地址，携带阅读器请求头并支持微信资源 CDN
跳转到腾讯云图片地址。修复前生成的含图章节缓存会自动失效，使用 `--cache` 即可重新获取真实插图；
纯文本章节仍可复用。旧导出文件需要重新运行下载命令生成，离线导出无法补回已丢失的图片地址。

PDF 优先使用系统中可嵌入的中文字体，也可明确指定：

```bash
uv run weread-exporter export 'output/书名_BOOK_ID' -f pdf --pdf-font /path/to/chinese-font.ttf
```

支持 ReportLab 可读取的 TTF/TTC。未找到系统中文字体时回退到 `STSong-Light`，
这种回退依赖 PDF 阅读器提供中文字体；跨设备分享建议明确指定可嵌入的中文字体。

### 配置

参考 [`config/config.example.json`](config/config.example.json)：

```bash
uv run weread-exporter download --config config/config.example.json
```

也可保存为 `config/config.json` 自动读取。兼容原 TypeScript 项目配置的
`puppeteer.launch.executablePath`、`weread.books`、`chapters`、`enableCache`。
旧配置的 `combine` 字段会被忽略；`--combine` / `--no-combine` 命令参数已移除，始终按整本输出。
支持的配置包括 `browser`、`weread.formats`、`weread.output`、`weread.delay` 和 `weread.retries`；
单本书也可设置 `formats`。命令行参数优先于配置，书籍格式优先于全局格式。
配置中的路径相对于运行命令的当前目录，时间单位为秒。

### 实现与验证

核心流程是：Playwright 拦截阅读器 HTML/JavaScript → tree-sitter 定位并注入状态引用和解密入口
→ 在浏览器中调用网页自身的解密函数 → 提取已加载的章节分段 → 缓存 HTML 与插图 → 导出。
Python 负责调度与文件生成，网页 JavaScript 继续在浏览器中执行，无需手动安装 Bun/Node.js。
当前按书顺序下载，避免多个页面争抢焦点或同时要求终端输入。

```bash
uv run ruff check weread_exporter tests
uv run pytest -m 'not browser'
uv run playwright install chromium
uv run pytest

# 使用系统 Chrome 运行本地浏览器测试
WEREAD_TEST_BROWSER_CHANNEL=chrome uv run pytest
```

测试覆盖配置兼容、章节范围、脚本注入执行、SPA 章节切换、多分段提取、缓存、
四种导出格式和插图打包。浏览器测试使用本地构造的阅读器页面，不依赖微信账号。
真实书籍下载仍需扫码登录验证；网页内部脚本、选择器或分段加载机制变化时可能需要适配。
此版本重排文本型书籍的正文，不提供微信读书原生扫描 PDF 的下载或 OCR，
也不承诺还原复杂原书版式、交互内容及未加载的分段。


### FAQ

#### 如何获取书籍 ID？

打开微信读书网页端，点击书籍，进入阅读（⚠️ 注意：须要第一次进入）。复制浏览器地址栏中的 URL 最末尾的书籍 ID。比如 `https://weread.qq.com/web/reader/fa3322105ca0ecfa382cac6` 中，书籍 ID 为 `fa3322105ca0ecfa382cac6`。你可以通过打开 `https://weread.qq.com/web/bookDetail/书籍 ID`，来确认是否获取到正确的书籍 ID。如果正常展示，则书籍 ID 正确。

### 项目结构

```text
weread-exporter/
  weread_exporter/          # Python 包
  tests/                   # 自动化测试
  config/config.example.json
  pyproject.toml
  uv.lock
```

安装后的命令为 `weread-exporter`，也可用 `uv run python -m weread_exporter`。
本项目不依赖原 TypeScript 项目目录。

### 来源与许可

Python 实现移植自 weread-downloader 的阅读器处理逻辑，使用 MIT 许可，保留原作者版权声明，见 [LICENSE](LICENSE)。
仅供学习和研究使用；导出内容的版权归原作者和出版商所有。
