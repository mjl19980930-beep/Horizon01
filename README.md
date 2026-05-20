# Horizon01 - AI 热点选题到飞书多维表格

这是一个面向「AI 小白 / AI 工具 / AI 热点 / AI 教程 / AI 玩法」的自动热点选题仓库。

底层使用 [Thysrael/Horizon](https://github.com/Thysrael/Horizon) 做信息抓取、AI 打分、去重和总结；本仓库额外增加一层：把 Horizon 的结果转成适合内容选题池的字段，并写入飞书多维表格。

## 它会做什么

1. 每天定时抓取 AI 相关信息源。
2. 用 AI 筛选值得关注的内容。
3. 转成内容选题字段，而不是普通新闻日报。
4. 写入飞书多维表格。
5. 用 `去重Key` 避免重复写入旧选题。

## 飞书表格字段

建议你的多维表格包含这些字段。脚本默认会尝试自动创建缺失字段。

- 日期
- 一级分类
- 标题
- 一句话解释
- 为什么适合AI小白
- 可拍角度
- 教程切入点
- 工具/产品名
- 来源平台
- 原始链接
- AI评分
- 可靠性
- 适合内容形态
- 状态
- 去重Key
- 标签
- AI摘要

## GitHub Secrets

打开仓库的 `Settings -> Secrets and variables -> Actions -> New repository secret`，依次添加：

- `OPENAI_API_KEY`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BITABLE_APP_TOKEN`
- `FEISHU_TABLE_ID`

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
