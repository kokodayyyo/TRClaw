# Main Agent Long-Term Memory

长期规则：
- 主记忆存储在 `memory/` 下的 markdown 文件中。
- 子代理工作记忆存储在 `submemory/` 下的 markdown 文件中。
- 主记忆以会话开始时间命名，一个会话持续写入一个文件，`/new` 时切换到新文件。
- QQ channel 收到的附件会下载到项目根目录下的 `Download/qqbot/`。
- 当前系统支持本机文件读写、目录查看、终端命令执行、Python 代码执行。

注意事项：
- 删除本机路径前必须等待用户明确确认。
- 子代理没有长期记忆，任务人格和目标由主代理动态下发。
