# Horizon01 - AI 热点选题到飞书多维表格

这是一个面向「AI 小白 / AI 工具 / AI 热点 / AI 教程 / AI 玩法」的自动热点选题仓库。

底层使用 [Thysrael/Horizon](https://github.com/Thysrael/Horizon) 做信息抓取、AI 打分、去重和总结；本仓库额外增加一层：把 Horizon 的结果转成更适合后续二次写稿的「喂鱼选题卡」，并写入飞书多维表格。

## 它会做什么

1. 每天定时抓取 AI 相关信息源。
2. 用 DeepSeek 筛选值得关注的内容。
3. 把新闻和工具动态转成内容选题字段，而不是普通日报。
4. 写入飞书多维表格。
5. 用 `去重Key` 避免重复写入旧选题。

## 飞书表格字段

建议你的多维表格包含这些字段。脚本默认会尝试自动创建缺失字段。

- 热点
- 时间
- 切入点
- 选题
- 标题
- 选题受众
- 选题目的
- 开头
- 中间
- 结尾
- 逐字稿
- 一级分类
- 来源平台
- 原始链接
- AI评分
- 可靠性
- 适合内容形态
- 状态
- 去重Key
- 标签
- AI摘要

字段的使用方式：

- `热点`：保留原始事件，方便回看事实来源。
- `切入点`：把新闻转成内容角度，不直接照搬资讯。
- `选题`：给后续写稿用的选题方向。
- `标题`：更偏短视频标题，参考「先指出误区，再给正确用法」的表达。
- `开头 / 中间 / 结尾`：只给写稿骨架，不直接生成完整口播稿。
- `逐字稿`：默认留空，等你确认选题后再二次写稿。

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

可选：

- `GITHUB_TOKEN_FOR_HORIZON`：提高 GitHub API 抓取额度。如果不填，会使用 GitHub Actions 自带 token。

## 怎么运行

手动运行：

1. 打开仓库的 `Actions`
2. 选择 `AI Topic Collector to Feishu`
3. 点击 `Run workflow`

自动运行：

默认每天北京时间 9:30 和 15:30 各跑一次。

## 注意

这个仓库负责「收集选题和入库」，不直接生成完整口播稿。选题确认后，再接你的喂鱼内容写作流程更合适。

当前字段会借用喂鱼内容的结构化表达方式：先讲现象，再拆误区，最后落到一个动作。但不会编造喂鱼自己的经历、案例或学员故事，方便后续二次写稿时手动补真实素材。