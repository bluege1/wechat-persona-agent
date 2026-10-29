# 小信使（WeeMessenger）

这里放的是本项目使用的消息发送组件，接收 HTTP 指令，再通过 Windows 桌面自动化发送微信文字、语音和表情。

原项目：[KilincocomilK/WeeMessenger](https://github.com/KilincocomilK/WeeMessenger)，作者 KoKoMilk，采用 [MIT License](LICENSE)。本目录保留原作者的版权声明，不属于主项目从零编写的代码。

这份副本来自作者本机正在使用的版本。其 Git 元数据记录的基准提交为 `6148af7d6d21466ae541462571510a2e862f1bf2`，包含后续本地修改，不等同于该提交的原始内容。已知适配包括：

- 允许文字指令携带 `refocus`，在输入框失焦时重新搜索目标会话。
- 调整虚拟声卡输出、语音录制前的等待和音频初始化超时处理。
- 默认关闭鼠标漫游；随仓库提供的配置只启用本机 HTTP 服务，关闭 WebSocket。

本目录只包含源码、依赖清单、许可证和示例配置，不包含原机器的虚拟环境、Git 历史或运行数据。后续更新请保留许可证，并检查这些本地适配是否仍然有效。

## 启动

推荐从仓库根目录执行 `0-装依赖.bat`，它会分别创建主程序和小信使的环境。装好后执行 `一键启动.bat`，同时启动发送服务和聊天主循环。详细步骤见[主项目 README](../README.md)。

如果只需要单独启动发送服务，可以在仓库根目录执行：

```powershell
py -3.12 -m venv 小信使/venv
& ./小信使/venv/Scripts/python.exe -m pip install -r ./小信使/requirements.txt
& ./小信使/venv/Scripts/python.exe ./小信使/main.py
```

默认地址为 `http://127.0.0.1:9877`，配置在本目录的 `config.yaml`。微信需已登录。语音另需安装、配置虚拟声卡，并使微信快捷键与发送模块设置一致；驱动和语音模型没有打包进来。

`启动.bat` 会通过系统的 `python` 运行交互启动器。如果电脑上有多套 Python，优先使用上面的显式路径，或仓库根目录的启动脚本。

## HTTP 指令

`GET /health` 检查服务是否响应，不会发送消息。

`POST /` 或 `POST /api/message` 接收以下类型：

| type | 主要字段 |
|---|---|
| `send_message` | `target`、`message`，可选 `refocus` |
| `send_voice` | `target`、`voice_data`（Base64）、`voice_format` 或 `format` |
| `send_sticker` | `target`、`sticker_data`（Base64） |

HTTP 返回成功只表示指令已进入队列，不表示消息已经出现在微信中。主程序会在部分发送路径上进一步回读校验。

不操作微信的接口测试可从仓库根目录运行：

```powershell
& ./程序/.venv/Scripts/python.exe -m unittest discover -s tests -p "test_sender_service.py" -v
```

测试使用临时本机端口和独立队列，不创建发送器，也不向微信发消息。
