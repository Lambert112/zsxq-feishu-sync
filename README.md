# ZSXQ → 飞书 内容同步

自动将知识星球博主的内容同步到飞书云文档，每 30 分钟执行一次。

## 工作原理

```
GitHub Actions (每30分钟)
  ├─ 读取同步状态
  ├─ 调用 ZSXQ API 拉取新帖子
  ├─ 处理内容（文字、图片、PDF）
  ├─ 写入飞书文档（按月组织，按日期分组）
  └─ 更新状态 + 错误通知
```

## 文档结构

- 每月一个文档：`知识星球同步 - 2026年5月`
- 日期作为一级标题（H1）
- 帖子标题作为二级标题（H2）
- 每个帖子包含发布时间、正文、图片、附件

## 快速开始

### 1. Fork 仓库

Fork 此仓库到你的 GitHub 账号（设为公开仓库以获得无限 Actions 分钟数）。

### 2. 获取 MCP API Key

通过知识星球 Skill 平台获取 API Key：https://garden.zsxq.com/skill/

### 3. 获取社群 ID

从知识星球网页版 URL 中提取：
```
https://wx.zsxq.com/dweb2/index/group/48888888888
                                      ^^^^^^^^^^^^ 这就是 GROUP_ID
```

### 4. 准备飞书应用

确保飞书应用已配置以下权限：
- `docx:document` — 创建和编辑文档
- `drive:drive` — 上传媒体文件

在飞书云文档中创建一个文件夹用于存放同步内容，获取文件夹 Token。

### 5. 配置 GitHub Secrets

在 GitHub 仓库的 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|--------|------|
| `ZSXQ_MCP_API_KEY` | 知识星球 MCP API Key |
| `ZSXQ_GROUP_ID` | 知识星球社群 ID |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |
| `FEISHU_FOLDER_TOKEN` | 飞书目标文件夹 Token |
| `FEISHU_BOT_WEBHOOK` | 飞书机器人 Webhook（用于错误通知） |

### 6. 启用 Actions

在 GitHub 仓库的 Actions 页面启用 Workflow。定时任务每 30 分钟自动运行一次。

也可以手动触发：Actions → ZSXQ to Feishu Sync → Run workflow。

## 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export ZSXQ_MCP_API_KEY="your-api-key"
export ZSXQ_GROUP_ID="your-group-id"
export FEISHU_APP_ID="your-app-id"
export FEISHU_APP_SECRET="your-app-secret"
export FEISHU_FOLDER_TOKEN="your-folder-token"

# 运行
python -m src.main

# 运行测试
pip install pytest
pytest tests/
```

## API Key

API Key 长期有效，通过知识星球官方 Skill 平台获取，无需手动续期。

## 注意事项

- 首次运行只同步最新 20 条帖子，避免创建超大文档
- 如需全量同步，手动触发 workflow 并设置 `full_sync=true`
- 飞书文档 API 有频率限制（3 次/秒），脚本已内置延迟处理
