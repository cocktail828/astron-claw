# AstronClaw WS 连接问题复盘

日期：2026-03-24

## 背景

本次问题发生在 AstronClaw 渠道插件通过 WebSocket 与服务端建立长连接的生产场景中。故障主要出现在服务端滚动重启、重部署以及连接恢复窗口，表现为 bot 断连、短时间不可用、部分场景下不再重连，或者 bot 本地已生成回复但前端只看到 `: heartbeat`。

本次排查覆盖了：

- 服务端连接管理与 `shutdown()` 清理逻辑
- 插件 `BridgeClient` 的重连、接管、握手失败处理
- bot 侧消息生成与回传链路
- 滚动更新期间的真实时序与日志对齐

## 现象

故障期间主要有两类表现：

1. 服务端重启或重部署后，聊天接口返回：

```json
{"code":400,"error":"No bot connected"}
```

2. bot 侧日志显示已经收到了 prompt 并完成生成，但前端 SSE 端只持续收到：

```text
: heartbeat
```

同时，在不同阶段的日志中，还出现过以下典型特征：

- `closed code=1012`
- `unexpected http response status=502`
- `Opening handshake has timed out`
- `closed code=1006`
- `closed code=4005 reason=Evicted by newer connection`
- `evicted by newer connection, will not retry`

## 时间线概览

问题大致经历了两个阶段。

### 第一阶段：旧模型下的重复连接拒绝

旧日志中，bot 经常出现以下循环：

1. `connected`
2. 收到服务端消息 `Bot already connected`
3. `closed code=4002`
4. 1 秒后重新连接

这说明旧服务模型是“重复连接直接拒绝”，而客户端会持续重试，导致重连风暴。

### 第二阶段：接管模型下的异常停机与长空窗

后续服务端改成“新连接接管旧连接”，症状转为：

1. 旧连接收到 `4005 Evicted by newer connection`
2. bot 侧把 `4005` 当作永久退出，不再重连
3. 服务重启期间还伴随 `1012`、`502`、`1006`
4. 某些请求里 bot 已本地生成回复，但服务端收不到回包

## 根因分析

本次故障不是单点问题，而是多层缺陷叠加。

### 1. 插件把 `4005` 视为永久退出

在插件 [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts) 的旧逻辑中，收到 `4005` 会设置 `evicted = true`，而 `_scheduleReconnect()` 会在 `evicted` 为真时直接返回，不再继续重连。

这意味着：

1. 只要某次接管命中 `4005`
2. 这个 bot 实例就进入永久停机状态
3. 后续即使再遇到 `1012`、`1006`，也不会恢复

这正是早期“服务一重启，bot 断开后不再回来”的核心原因。

### 2. 旧 socket 的晚到事件会污染当前连接

插件 `BridgeClient` 旧版本的 `open`、`message`、`error`、`close` 事件处理，没有和某个具体 socket 实例绑定。

因此会出现：

1. socket A 已经过时
2. socket B 已经成为当前有效连接
3. socket A 晚到的 `error` / `close` 仍然会修改全局状态
4. 触发额外 `_scheduleReconnect()`
5. 造成同 token 多条连接竞争

这类竞态会直接放大 `4005` 接管问题，并制造“连接明明已经建立，但又被旧事件打断”的假象。

### 3. `502` 后失败握手 socket 未及时释放

在服务重部署期间，bot 收到 `1012` 后会开始重连。某次重连如果遇到 `502`，旧逻辑只会安排下一次重试，但不会立刻放弃当前失败中的握手 socket。

后续我们为避免并发建连，又加入了保护：

- 如果当前已有 `CONNECTING` 或 `OPEN` 的 socket，就不再发起新的 `_connect()`

这两个逻辑叠加后，导致：

1. `502` 发生后，理论上应在 2 秒后再次重连
2. 但失败中的握手 socket 仍挂在 `this.ws`
3. 下次 `_connect()` 被“已有 CONNECTING socket”拦住
4. 只能等 60 秒握手超时后，才真正继续重连

这就是后来出现“重启后要多等接近 1 分钟才恢复”的直接原因。

### 4. 回复发送失败被静默吞掉

在 [inbound.ts](/home/hygao1024/astron-claw/plugin/src/messaging/inbound.ts) 的旧逻辑中：

- `sendChunk`
- `sendFinal`
- `result`
- `error`

都没有检查 `bridgeClient.send()` 是否成功。

因此在 bot 本地链路里会出现：

1. bot 正常收到 prompt
2. LLM 正常生成完整回复
3. 本地日志打印“dispatch completed without final deliver, sending final”
4. 但这时 bridge 已经断开
5. `send()` 失败，但代码没有记录或处理
6. 服务端一直收不到 `session/update` / `done`
7. 前端 SSE 只会持续输出 `: heartbeat`

这就是“bot 明明答了，但前端看不到”的根因。

### 5. 服务端 `shutdown()` 旧逻辑存在所有权竞态

服务端旧版 `shutdown()` 会基于本地 `_bots` 无条件清 Redis 在线状态和 inbox。

在多 Pod / 多 worker 场景下，可能发生：

1. 旧 worker A 开始退出
2. bot 已经重连到新 worker B
3. A 继续按“自己仍持有 owner”来清 Redis
4. B 刚建好的在线状态被 A 误删

这是一个真实存在的服务端放大器问题，虽然它不是最后生产验证通过时的主症状，但必须修。

## 修复方案

本次最终落地了三组关键修复。

### 一、服务端修复：安全退出与 draining

提交：

- `d61020d` `Harden bot WS shutdown and draining`

主要内容：

1. `shutdown()` 改为 ownership-safe 清理
2. worker 增加 draining 状态
3. draining worker 不再接收新 bot 连接
4. 清理 Redis 前先做 owner / generation 校验

主要文件：

- [bridge.py](/home/hygao1024/astron-claw/server/services/bridge.py)
- [websocket.py](/home/hygao1024/astron-claw/server/routers/websocket.py)

### 二、插件修复：socket 代际保护与发送失败显式化

提交：

- `506ba4d` `Fix bridge client stale socket races`

主要内容：

1. 给 `BridgeClient` 增加当前 socket 实例保护
2. 旧 socket 晚到的 `open/message/error/close` 不再影响当前连接
3. `session/update`、`result`、`error` 发送时检查 `send()` 返回值
4. 工具事件和主动外发链路也不再静默吞发送失败

主要文件：

- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts)
- [inbound.ts](/home/hygao1024/astron-claw/plugin/src/messaging/inbound.ts)
- [hooks.ts](/home/hygao1024/astron-claw/plugin/src/hooks.ts)
- [outbound.ts](/home/hygao1024/astron-claw/plugin/src/messaging/outbound.ts)

### 三、插件修复：`502` 后立即放弃失败握手 socket

提交：

- `524f52f` `Fix reconnect stall after handshake errors`

主要内容：

1. `unexpected-response` 遇到 `502` 时立即放弃当前失败握手 socket
2. 清理 `this.ws`
3. 销毁底层 socket
4. 允许下一次 `_connect()` 按预期及时触发

主要文件：

- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts)

## 验证结果

### 本地验证

新增并通过了两个关键回归脚本：

- [test_stale_socket_events.ts](/home/hygao1024/astron-claw/plugin/tests/test_stale_socket_events.ts)
- [test_unexpected_response_reconnect.ts](/home/hygao1024/astron-claw/plugin/tests/test_unexpected_response_reconnect.ts)

验证内容包括：

1. 旧 socket 晚到的 `error/close` 不会再触发第 3 条连接
2. `502` 后不会再卡住等待 60 秒握手超时

### 生产验证

最终生产验证结果显示：

1. 服务重部署后 bot 能恢复建连
2. 恢复后会话正常
3. 之前的“永久不回连”和“长时间不可用窗口”已收敛

## 最终定性

本次问题最终定性为：

1. 不是安装脚本导致插件损坏
2. 也不是单纯“集群部署天然冲突”
3. 真正根因是“服务重部署窗口 + 插件连接状态机缺陷 + 握手失败恢复不完整”的组合故障

更具体地说：

1. 服务端滚动更新触发正常断连与重连
2. 插件旧版本把 `4005` 当永久退出
3. 插件存在旧 socket 事件污染当前连接的竞态
4. 插件对 `502` 的恢复不完整，导致长时间空窗
5. 服务端 `shutdown()` 旧逻辑又进一步放大了不稳定性

## 剩余建议

虽然当前生产验证已通过，但仍建议后续继续做两项增强。

### 1. 服务端优化“无 bot 在线”的用户体验

当前如果 bot 仍在恢复中，服务端仍可能立即返回：

```json
{"code":400,"error":"No bot connected"}
```

建议后续优化为：

1. 聊天请求先等待 bot 恢复若干秒
2. 若恢复成功则继续投递
3. 超时后再返回错误

这样发布窗口内的短暂重连将不再直接暴露为用户错误。

### 2. 进一步评估 `4005` 的长期语义

当前在单实例单 token 假设下，`4005` 表示被更“新”的连接接管，这个设计本身是合理的。

但如果未来需要兼容更复杂的部署或容灾场景，可以考虑让 `4005` 也支持退避重连，而不是永久停机。

## 结论

本次故障已完成：

1. 根因定位
2. 插件与服务端修复
3. 本地回归验证
4. 生产实测验证

最终修复重点是：

1. 服务端退出路径安全化
2. 插件连接状态机去竞态
3. 插件握手失败恢复路径补全
4. 回复发送失败显式化

当前版本已能正确通过服务重部署后的连接恢复与正常会话验证。
