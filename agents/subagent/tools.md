# Subagent Tool List

子代理只应使用主代理本轮授权给它的工具。

常见可授权工具类型：
- `read_file`
- `write_file`
- `list_directory`
- `run_terminal_command`
- `run_python_code`
- `search_memory`
- `search_submemory`
- `read_memory`

使用原则：
- 只围绕当前任务使用工具。
- 如无必要，不主动扩展额外步骤。
- 删除操作如果出现在授权范围内，仍必须遵守用户确认规则。
