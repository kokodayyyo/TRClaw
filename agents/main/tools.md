# Main Agent Tool List

主代理可根据实际注册结果调用以下类型工具：

记忆相关：
- `search_memory`
- `search_submemory`
- `read_memory`

本机文件与执行相关：
- `read_file`
- `write_file`
- `list_directory`
- `run_terminal_command`
- `run_python_code`
- `delete_path`（只有用户确认后才允许）

子代理相关：
- `list_subagents`
- `run_subagent_task`

其他：
- `list_skills`

使用原则：
- 记忆召回优先看摘要，再决定是否读取全文。
- 涉及本机操作时尽量说明你将做什么。
- 删除操作必须等用户确认。
