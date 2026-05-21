# Horizon01 - AI 热点选题到飞书多维表格

这是一个面向「AI 小白 / AI 工具 / AI 热点 / AI 教程 / AI 玩法」的自动热点选题仓库。

底层使用 [Thysrael/Horizon](https://github.com/Thysrael/Horizon) 做信息抓取、AI 打分、去重和总结；本仓库额外增加一层：把原文内容转成适合后续二次写稿的「喂鱼选题卡」，并写入飞书多维表格。

## 它会做什么

1. 每天定时抓取 AI 相关信息源。
2. 用 DeepSeek 筛选值得关注的内容。
3. 可选抓取 X/Twitter 关键词帖和 YouTube 高播放视频。
4. 先保留原文事实，再生成切入点、选题标题、开头钩子、中间论证和结尾收束。
5. 写入飞书多维表格。
6. 用 `去重Key` 避免重复写入旧选题。

## 飞书表格字段

第一列必须有内容。脚本会同时写入 `Title` 和 `原文标题`：如果你的飞书第一列仍叫 `Title`，它会被填满；如果你把第一列改成 `原文标题`，也能继续使用。

建议字段顺序：

- Title
- 原文标题
- 原文发表日期
- 原文链接
- 原文核心观点
- 原文逐字稿/操作说明
- 来源平台
- 一级分类
- AI评分
- 可靠性
- 标签
- 切入点
- 选题标题
- 开头钩子
- 中间论证
- 结尾收束
- 选题受众
- 选题目的
- 适合内容形态
- 状态
- 去重Key
- AI摘要

字段逻辑：

- `Title / 原文标题`：放抓取下来的原文标题。英文标题会在同一个单元格里追加中文括号翻译。
- `原文发表日期`：原文或帖子发布时间，拿不到时用本次抓取时间。
- `原文链接`：作为事实佐证，方便手动回看。
- `原文核心观点`：先还原原文到底讲了什么。
- `原文逐字稿/操作说明`：X 会保留原帖正文；YouTube 先保留标题和简介；普通网页先保留摘要，并标明不是完整逐字稿。
- `切入点 / 选题标题 / 开头钩子 / 中间论证 / 结尾收束`：这是二次写稿层，会基于原文单独生成，不应该批量套模板。

## GitHub Secrets

打开仓库的 `Settings -> Secrets and variables -> Actions -> New repository secret`。

必须添加：

- `DEEPSEEK_API_KEY`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

如果你已经把 DeepSeek key 命名成 `Deepseek_API_KEY`，也可以先不改，workflow 已兼容这个写法。更推荐的标准写法是 `DEEPSEEK_API_KEY`。

飞书目标有两种填法，二选一即可。

### 方式一：你只有 wiki 链接

添加：

- `FEISHU_WIKI_TOKEN`

它可以填整条 wiki 链接，也可以只填 `/wiki/` 后面的那串 token。脚本会自动把 wiki 节点换成真实的多维表格 token，并默认写入第一张数据表。

### 方式二：你能拿到 base 链接

添加：

- `FEISHU_BITABLE_APP_TOKEN`
- `FEISHU_TABLE_ID`

`FEISHU_BITABLE_APP_TOKEN` 是 `/base/` 后面的那串，`FEISHU_TABLE_ID` 通常是 `tbl` 开头。

### 可选：增强抓取范围

- `GITHUB_TOKEN_FOR_HORIZON`：提高 GitHub API 抓取额度。如果不填，会使用 GitHub Actions 自带 token。
- `X_BEARER_TOKEN`：开启 X/Twitter 关键词抓取。也兼容 `TWITTER_BEARER_TOKEN`。
- `X_SEARCH_QUERY`：自定义 X 搜索关键词。不填会默认抓 AI、ChatGPT、Claude、Gemini、AI agent、AI tools、AI workflow、vibe coding。
- `X_SEARCH_MAX_RESULTS`：每次最多抓多少条 X 帖子，默认 50，最高 100。
- `YOUTUBE_API_KEY`：开启 YouTube 视频搜索。
- `YOUTUBE_SEARCH_QUERIES`：自定义 YouTube 搜索词，用 `|` 分隔，例如 `AI tools tutorial|Claude Code tutorial|AI agent workflow`。
- `YOUTUBE_RESULTS_PER_QUERY`：每个 YouTube 搜索词抓多少条，默认 8。

## 怎么运行

手动运行：

1. 打开仓库的 `Actions`
2. 选择 `AI Topic Collector to Feishu`
3. 点击 `Run workflow`

如果你已经清空了飞书表格，手动运行时把 `reset_seen` 填成 `true`，这样仓库里的去重记录也会一起重置。

自动运行：

默认每天北京时间 9:30 和 15:30 各跑一次。

## 注意

这个仓库负责「收集选题和入库」，不直接生成完整口播稿。选题确认后，再接你的喂鱼内容写作流程更合适。

当前字段会借用喂鱼内容的结构化表达方式：先讲原文事实，再找切入点，最后落到一个动作。但不会编造喂鱼自己的经历、案例或学员故事，方便后续二次写稿时手动补真实素材。