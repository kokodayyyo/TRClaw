# TRClaw

TRClaw是一个本地运行的多代理原型系统 纯python 语言实现，基于 OpenAI 兼容接口，核心特点是快速运行，服务启动期间占用仅100mb左右内存：

- 一个常驻主代理
- 最多 10 个当前会话下的驻留子代理
- 主记忆使用 `memory/` 下的 markdown 文件
- 子代理工作记忆使用 `submemory/` 下的 markdown 文件
- 主代理和子代理都可以读写本机文件、执行终端命令、执行 Python 代码
- 删除操作必须先由用户明确确认
- 支持 OpenAI 兼容接口、自定义 `base_url`
- 支持 QQ Bot 通道
- 支持持久化长任务、暂停与恢复

## 1. 当前状态

当前版本已经可以本地运行，支持：

- CLI 对话
- QQ Bot 文本对话
- 主代理调度子代理
- 主记忆与子代理记忆落盘
- `Summary` 检索
- 长任务持久化
- QQ 附件自动下载到 `Download/qqbot/`

**Dangerous**: 当前不做强沙盒隔离，执行能力是本机直连模式。

## 2. 运行方式

建议先激活你自己的 conda 环境，再启动：

```powershell
python .\main.py
```

YClaw 默认使用“当前已激活的 Python 环境”执行 Python 代码，不会把某个 conda 环境名写死在代码里。

## 3. 目录结构

```text
YClaw/
  app/
  agents/
    main/
      role.md
      long_memory.md
      tools.md
    subagent/
      role.md
      tools.md
  config/
    app.yaml
    channels.yaml
    models.yaml
    tools.yaml
  data/
  docs/
  Download/
    qqbot/
  logs/
  memory/
  submemory/
  main.py
  README.md
```

## 4. 配置文件

### `config/app.yaml`

主配置文件，控制：

- 路径
- CLI 命令前缀
- 子代理上限
- 主代理/子代理循环预算
- 长任务预算

### `config/models.yaml`

模型配置文件，支持多个 profile，并通过第一行切换默认模型：

```yaml
default_profile: default
```

示例结构：

```yaml
default_profile: default

profiles:
  default:
    base_url: "http://127.0.0.1:1234/v1"
    api_key: "lm-studio"
    model: "your-model"
    timeout: 120
    temperature: 0.2
    max_tokens: null
    reasoning_effort: "high"
    thinking:
      type: "enabled"
    extra_headers: {}
    extra_body: {}
```

支持的扩展字段：

- `reasoning_effort: high | max`
- `thinking.type: enabled | disabled`

### `config/channels.yaml`

QQ Bot 配置文件，示例：

```yaml
channels:
  qqbot:
    enabled: true
    appId: "你的AppID"
    clientSecret: "你的AppSecret"
    clientSecretFile: ""
    sandbox: false
    removeAt: true
    maxRetry: 10
    intents:
      - C2C_MESSAGE_CREATE
      - GROUP_AT_MESSAGE_CREATE
      - AT_MESSAGE_CREATE
    apiBaseUrl: "https://api.sgroup.qq.com"
    tokenUrl: "https://bots.qq.com/app/getAppAccessToken"
    account: "default"
```

也可以通过命令写入：

```powershell
python .\main.py channels add --channel qqbot --token "AppID:AppSecret"
python .\main.py channels check --channel qqbot
```

### `config/tools.yaml`

当前保留为通用工具配置入口。系统内核不依赖你提前把所有业务工具写死在这里。

## 5. 主代理与子代理 markdown

### 主代理

主代理使用以下 markdown 文件：

- `agents/main/role.md`
- `agents/main/long_memory.md`
- `agents/main/tools.md`

这些文件会在运行时加载，不再把主代理角色卡、长期记忆和工具说明硬编码在 Python 里。

### 子代理

子代理使用以下 markdown 文件：

- `agents/subagent/role.md`
- `agents/subagent/tools.md`

注意：

- 子代理没有长期记忆 markdown
- 子代理的人格、任务、授权工具由主代理每次动态注入

## 6. 记忆系统

### 主记忆

主记忆保存在：

```text
memory/YYYY-MM-DD_HH-MM-SS.md
```

规则：

- 一个会话持续写入一个主记忆文件
- `/new` 时切换到新的文件
- 文件正文直接保存用户输入和 LLM 输出

### 主记忆 `Summary` 规则

当前规则非常明确：

- 普通对话不会自动生成或更新 `# Summary`
- 只有用户显式发送：

```text
/remember-你要记住的内容
```

才会更新当前主记忆文件顶部的 `# Summary`

而且：

- `Summary` 只写用户要求记住的内容
- 不带 LLM 回复
- 当前实现是覆盖式，不是追加式

### 子代理记忆

子代理记忆保存在：

```text
submemory/<session_id>/<subagent_id>/*.md
```

当前子代理记忆仍会自动生成 `# Summary`，用于后续检索。

## 7. 检索与 `/callmemory`

主记忆检索使用 `Summary` 优先策略。

### `/callmemory-关键词`

例如：

```text
/callmemory-中文回答 可执行步骤
```

行为：

- 只搜索 `memory/` 下的主记忆
- 返回最匹配的前 5 条
- 显示：
  - `file_id`
  - `time`
  - `summary`
  - `path`

### 匹配方式

`/callmemory` 会自行做相似度匹配，但当前是“轻量文本匹配”，不是向量语义检索。

优先级大致是：

1. SQLite FTS5 匹配 `summary_text`
2. 如果 FTS 不可用，则退化到 `LIKE`
3. 如果 SQLite 不可用，则退化到 JSON 索引的简单分词计分

所以它属于：

- 会自动匹配
- 会返回 top 5
- 但不是 embedding 级别的语义向量相似度

## 8. CLI 命令

### 会话与代理

- `/new`
  创建新会话，并销毁当前会话下所有子代理

- `/agents`
  查看当前会话下子代理状态

- `/kill subagents`
  销毁当前会话下全部子代理

- `/exit`
  退出系统

### 记忆

- `/remember-<内容>`
  把 `<内容>` 写入当前主记忆文件顶部的 `# Summary`

- `/callmemory-<关键词>`
  搜索主记忆中最匹配的 5 条摘要

- `/memory search <关键词>`
  搜索主记忆摘要

- `/submemory search <关键词>`
  搜索子代理记忆摘要

### 长任务

- `/tasks`
  查看已持久化任务

- `/task run <prompt>`
  创建并运行一个长任务

- `/task show <task_id>`
  查看任务详情

- `/task resume <task_id>`
  恢复继续执行一个暂停中的长任务

### 删除确认

- `/confirm delete <path>`
  允许下一次删除该路径

注意：

- 删除不是直接开放给模型的
- 只有用户明确确认后，`delete_path` 工具才会执行

## 9. QQ Bot 行为

### 已支持

- 普通文本消息对话
- `/new`
- `/remember-...`
- `/callmemory-...`
- QQ 附件自动下载到：

```text
Download/qqbot/
```

### QQ 附件

当 QQ 消息带附件时：

- 系统会尝试下载附件到 `Download/qqbot/`
- 然后把本地文件路径附加到传给主代理的文本中

这样主代理后续可以直接继续处理这些本地文件。

## 10. 本机工具能力

当前主代理和子代理可调用的内置工具类型包括：

- `read_file`
- `write_file`
- `list_directory`
- `run_terminal_command`
- `run_python_code`
- `search_memory`
- `search_submemory`
- `read_memory`
- `list_subagents`
- `run_subagent_task`
- `list_skills`
- `delete_path`（需用户确认）

## 11. 长任务能力

当前已支持：

- 持久化任务记录
- 循环预算
- 暂停
- 恢复
- 检查点消息保存

任务保存在：

```text
data/tasks.json
```

当前 QQ 还没有完整接入 `/task run` 这一类长任务命令控制，但 CLI 已可用。

## 12. 注意事项

- 当前不做强沙盒隔离
- `run_terminal_command` 是本机执行
- `run_python_code` 使用当前激活环境
- 删除操作必须先确认
- Windows 控制台下可能遇到部分编码显示问题，但主流程已做容错
- 某些环境下 `sqlite` 或 `__pycache__` 写入可能被系统权限拦截，系统会尽量回退继续工作

## 13. 建议阅读顺序

- `config/app.yaml`
- `config/models.yaml`
- `config/channels.yaml`
- `agents/main/role.md`
- `agents/main/long_memory.md`
- `agents/main/tools.md`
- `docs/SYSTEM_DESIGN.md`
- `docs/INTERFACES.md`
