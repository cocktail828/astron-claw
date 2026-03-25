# BridgeClient 旧 Socket 污染新连接竞态分析

日期：2026-03-24

## 概述

本报告聚焦分析 AstronClaw 插件 `BridgeClient` 中“旧 socket 事件污染新连接”的竞态问题。该问题是本次 WebSocket 长连接异常、重复建连、`4005` 接管、以及回复丢失问题的核心根因之一。

这里讨论的不是多线程并发，而是单进程、单线程、事件驱动模型下的异步乱序问题。Node.js 虽然单线程，但 WebSocket 的 `open`、`message`、`error`、`close` 都是异步事件。只要不同生命周期的 socket 共享同一份全局状态，就会出现典型的状态机竞态。

## 问题定义

问题位置在：

- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts)

旧版本中，`BridgeClient` 的事件处理逻辑直接作用于共享字段：

- `this.ws`
- `this.ready`
- `this.reconnectTimer`
- `this.backoffMs`
- `this.attempts`
- `this.evicted`

但这些事件处理器没有校验“自己属于哪一条 socket”。因此会出现：

1. socket A 已经过时
2. socket B 已成为当前有效连接
3. A 晚到的 `error` / `close` 仍然执行
4. A 的事件修改了当前全局状态
5. 进而干扰 B，甚至触发 socket C

这就是“旧 socket 事件污染新连接”。

## 为什么单进程仍然会发生竞态

这里的竞态不是指两段代码同时执行，而是：

1. 某个旧连接生命周期尚未完全结束
2. 新连接生命周期已经开始
3. 旧连接晚到的事件在错误时间修改了当前状态

也就是说，本质是：

**异步事件跨代修改同一份可变状态。**

Node.js 单线程并不意味着没有 race condition。只要存在：

- 异步回调
- 生命周期重叠
- 共享状态

就可能发生逻辑竞态。

## 旧实现为什么危险

旧实现的典型模式类似：

```ts
this.ws = new WebSocket(...)

this.ws.on("open", () => {
  this.ready = true;
});

this.ws.on("close", () => {
  this.ready = false;
  this._scheduleReconnect();
});
```

这里最大的问题是：

1. 回调没有绑定 socket 身份
2. 回调不关心自己是 A 还是 B
3. 只要事件触发，就直接修改全局状态

于是，一旦某条旧连接的事件晚到，就会误操作当前连接。

## 典型时序

下面是一条最典型的故障时序：

1. `t0`：`_connect()` 创建 socket A，`this.ws = A`
2. `t1`：由于某次异常、重试、或生命周期重复启动，又创建 socket B，`this.ws = B`
3. `t2`：B 已 `open`，成为当前有效连接
4. `t3`：A 晚到一个 `error`
5. `t4`：A 晚到一个 `close`
6. `t5`：旧代码在 A 的 `close` 回调里执行：
   - `this.ready = false`
   - `_stopPing()`
   - `_scheduleReconnect()`
7. `t6`：当前有效连接 B 被误判为已断线
8. `t7`：重连逻辑又触发 socket C

此时同一进程会短时间出现：

- A：旧连接，已过时
- B：当前有效连接
- C：被旧事件误触发出的新连接

这会进一步导致：

- 同 token 多连接竞争
- 服务端 generation 持续递增
- 旧连接收到 `4005`
- 客户端进入更复杂的恢复状态

## 两类直接后果

### 1. 旧连接把新连接打成“断线”

如果 B 已经连上，而 A 晚到 `close`，旧逻辑会执行：

- `this.ready = false`
- `this._stopPing()`
- `this.onClose?.()`

但此时真正在线的是 B，不是 A。

因此，系统会错误地进入“当前连接断了”的状态，哪怕 B 还活着。

### 2. 旧连接凭空触发额外重连

如果 A 晚到一个：

- `close`
- `error`
- `unexpected-response`

旧逻辑就会调用 `_scheduleReconnect()`。

这意味着：

1. B 已经在线
2. A 却又拉起一次重连
3. 新的 socket C 被创建
4. 服务端开始看到重复连接

这是导致连接接管风暴的关键原因之一。

## 什么情况下会触发两个 socket

基于本次排查，主要有四类场景会触发同一账号生命周期里出现两个 socket。

### 1. 旧 socket 还没结束，新 socket 已经开始

最常见。

例如：

1. socket A 正在连接或未完全关闭
2. 某次重连逻辑已经开始新一轮 `_connect()`
3. 创建 socket B

### 2. 旧 socket 的晚到 `close/error` 又触发了一次重连

例如：

1. socket B 已经成为当前连接
2. socket A 晚到 `close`
3. 旧逻辑调度 `_scheduleReconnect()`
4. 于是创建 socket C

### 3. 同一个账号被重复启动

这不是单个 `BridgeClient` 内部的问题，而是账号生命周期层面的重复启动。

例如：

1. OpenClaw 调了一次 `startAccount`
2. 旧 client 还没完全 stop
3. 又来一次 `startAccount`
4. 新 `BridgeClient` 被创建

于是同一个进程里会存在两个不同的 `BridgeClient`，各自持有一条 socket。

### 4. 握手失败路径未及时释放当前 socket

例如：

1. socket A 握手期间收到 `502`
2. 插件安排下一次重连
3. 但 A 仍保留在 `this.ws`
4. 下一次 `_connect()` 看到当前有 `CONNECTING` socket
5. 被直接拦截

这类场景不一定立刻生成第二条 socket，但会把系统推入“连接重叠/状态错乱”的高风险边缘。

## 为什么该问题在 WebSocket 客户端里尤其常见

WebSocket 生命周期天然具备以下特征：

1. 握手失败可能很晚才报错
2. `unexpected-response`、`error`、`close` 的顺序不总是稳定
3. TCP 关闭与应用层 close frame 并不完全同步
4. 新连接可能已经建立，而旧连接的事件还没完全消化

因此，WS 客户端如果不做 connection instance fencing，几乎必然会在高压场景下出问题。

## 修复原则

修复的核心原则是：

**只有当前代的 socket，才有资格修改连接状态。**

这意味着：

1. 只有当前 `this.ws` 对应的实例，才能：
   - 修改 `this.ready`
   - 调 `_stopPing()`
   - 调 `_scheduleReconnect()`
   - 调 `onClose()`
2. 任何过时代的事件都必须被忽略

## 实际修复方案

修复后的关键模式是：

```ts
const ws = new WebSocket(...)
this.ws = ws

ws.on("open", () => {
  if (this.ws !== ws) return
  ...
})

ws.on("message", () => {
  if (this.ws !== ws) return
  ...
})

ws.on("error", () => {
  if (this.ws !== ws) return
  ...
})

ws.on("close", () => {
  if (this.ws !== ws) return
  ...
})
```

对应实现见：

- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts#L107)

这行判断：

```ts
if (this.ws !== ws) return;
```

等价于给每条 socket 增加一层“代际校验”。它确保：

1. 该事件属于哪一代 socket
2. 只有当它仍是当前 owner 时，事件才有效
3. 一旦 `this.ws` 已切到更新一代，旧事件自动作废

## 为什么还需要“已有 CONNECTING/OPEN 不再新建连接”

除了事件代际保护，还补了一层：

```ts
if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
  return;
}
```

对应位置：

- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts#L95)

这层保护用于防止同一时刻无意义地起多条连接。

但它也带来一个新要求：

**失败中的握手 socket 必须及时释放。**

否则下一次真正需要重连时，会被残留的 `CONNECTING` socket 错误拦住。

## `502` 场景下的额外修复

后来在生产验证中发现，`502` 触发的 `unexpected-response` 路径存在“失败 socket 未及时释放”的问题。

因此又补了：

1. `_abandonSocket(ws)`
2. `unexpected-response` 遇到 `502` 时：
   - 立即将失败握手 socket 从 `this.ws` 上摘掉
   - 停止相关状态
   - 销毁底层 socket
   - 再安排下一次重连

对应实现见：

- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts#L160)
- [client.ts](/home/hygao1024/astron-claw/plugin/src/bridge/client.ts#L198)

这解决了“理论上下一次重连应立即发生，但实际上被卡到 60 秒握手超时后才恢复”的问题。

## 为什么这个 bug 难以在平时发现

因为它通常只在特定时序下暴露：

1. 服务滚动更新
2. LB / Ingress 临时 `502`
3. 握手超时
4. 旧连接与新连接生命周期交叠
5. 同 token 接管
6. reconnect timer 与旧 close/error 同时存在

在本地稳定环境下，它往往表现不明显，因此很容易被误认为“偶发网络问题”。

但本质上，它不是偶发，而是确定存在的状态机缺陷。

## 如何在代码审查中提前识别这类问题

对于任何连接型客户端代码，如果同时满足以下条件，就应该默认存在“跨代污染”风险：

1. 一个共享字段（如 `this.ws`）会被反复覆盖
2. 旧对象注册的回调没有身份校验
3. 回调中会直接修改全局状态
4. 回调会触发下一轮连接或重试
5. 协议里存在强语义 close code，如 `4005`

这 5 点同时出现时，应优先审查：

1. 事件是否与连接实例绑定
2. 旧连接事件是否可无害化忽略
3. 握手失败路径是否完整释放资源
4. 重连逻辑是否会被过时事件误触发

## 结论

“旧 socket 事件污染新连接”不是一个小型实现瑕疵，而是 WebSocket 客户端状态机设计缺陷。

它的本质是：

**过时连接的异步事件在错误时间修改了当前连接状态。**

修复关键不在于“多加几处日志”，而在于建立连接代际不变量：

1. 只有当前 socket 才能修改连接状态
2. 旧 socket 的事件必须自动失效
3. 失败握手必须及时释放
4. 新连接创建前必须校验当前是否已有有效连接

这次修复后，AstronClaw 插件的连接状态机已经从“共享状态无代际保护”升级为“基于当前 socket owner 的事件防抖模型”，这是本次生产问题得到真正收敛的核心原因之一。
