# AstronClaw Channel 配置归一化设计

**日期：** 2026-03-24  
**范围：** 将 astron-claw 的业务配置统一收敛到 `channels.astron-claw`，不再使用 `plugins.entries.astron-claw.config` 作为运行时配置来源。

## 目标

让 `astron-claw` 在安装完成后符合 OpenClaw 对 channel 插件的配置模型：

- 正式 channel 配置只存在于 `channels.astron-claw`
- `plugins.entries.astron-claw` 只保留插件注册和元信息
- 旧版位于 `plugins.entries.astron-claw.config` 的配置在安装阶段自动迁移
- 运行时代码不再读取 legacy 的 plugin entry 配置

## 当前问题

`astron-claw` 虽然声明为 channel 插件，但当前安装脚本为了绕开插件注册时序问题，将运行时配置写入了 `plugins.entries.astron-claw.config`。与此同时，运行时代码又同时兼容读取：

- `channels.astron-claw`
- `plugins.entries.astron-claw.config`

这会带来几个问题：

- 安装结果与 channel 配置的预期位置不一致
- 运行时存在双配置源，语义不清晰
- 部分代码路径已经默认 `channels.astron-claw` 才是规范位置
- 用户查看 `openclaw.json` 时会看到配置落在一个不标准的位置

## 设计决策

采用严格的单一真源模型：

- 唯一正式配置源：`channels.astron-claw`
- 兼容迁移边界：仅限安装脚本
- 运行时对旧配置的兼容读取：删除

已有旧安装的用户需要重新执行一次安装脚本。安装脚本会把旧的 `plugins.entries.astron-claw.config` 迁移到 `channels.astron-claw`，并删除 legacy 配置载荷。

## 非目标

- 不做运行时自迁移
- 不保留对旧配置路径的持续兼容
- 不顺带修改与本任务无关的 OpenClaw 插件配置行为
- 不改动 bridge 业务功能本身，只处理配置读写与迁移

## 目标完成后的期望状态

在一次成功的新安装或重装后，配置应表现为：

```json
{
  "channels": {
    "astron-claw": {
      "enabled": true,
      "name": "AstronClaw",
      "bridge": {
        "url": "ws://...",
        "token": "..."
      },
      "allowFrom": ["*"]
    }
  },
  "plugins": {
    "entries": {
      "astron-claw": {
        "enabled": true
      }
    }
  }
}
```

其中 `plugins.entries.astron-claw` 的具体内容可以包含 OpenClaw 自己维护的字段，但 astron-claw 运行时不能再依赖它作为配置来源。

## 设计方案

### 1. 安装脚本负责迁移

`install.sh` 仍然先执行插件安装与启用，因为只有在 OpenClaw 完成插件注册后，`channels.astron-claw` 才能被安全写入。

在插件注册完成之后，安装脚本执行以下流程：

1. 读取旧的 `plugins.entries.astron-claw.config`（如果存在）
2. 构造目标 channel 配置对象
3. 按以下优先级合并最终配置：
   - 显式安装输入，例如 `--bot-token`、`--server-url`、默认账户名
   - 旧配置中仍然有价值、且未被安装参数覆盖的可选字段
4. 将最终结果写入 `channels.astron-claw`
5. 删除 `plugins.entries.astron-claw.config`
6. 如果正式写入失败，或 legacy 清理失败，则安装失败

这样可以把兼容逻辑集中在安装阶段，而不是长期保留在运行时路径中。

### 2. 运行时只读取 channel 配置

插件运行时代码只允许从 `cfg.channels.astron-claw` 解析配置。

受影响的范围包括：

- account discovery
- account resolution
- allowlist resolution
- 任何基于持久化配置推导运行状态的逻辑

这样可以消除双来源优先级歧义，并让运行时行为与对外文档一致。

### 3. 运行时写回路径继续保持规范

现有已经写入 `channels.*` 的代码路径应继续保留。例如 logout 时清理凭据，仍然应写回 `channels.astron-claw`。

运行时任何路径都不应再重新创建或依赖 `plugins.entries.astron-claw.config`。

### 4. 卸载脚本继续保留历史兼容清理

`uninstall.sh` 继续删除：

- `channels.astron-claw`
- `plugins.entries.astron-claw.config`（如果存在）

这里保留 legacy 清理只是为了处理历史残留，不代表它仍然是一个受支持的运行时配置位置。

## 合并规则

安装脚本的合并行为应当保持收敛且可预测：

- 始终写出 `enabled`、`name`、`bridge.url`、`bridge.token`、`allowFrom`
- 如果旧配置中存在 `media`、`retry` 等可选字段，并且没有被显式安装输入覆盖，则迁移后保留
- 对格式错误或结构异常的旧值不做盲目保留；最终写入的必须是插件期望的有效 JSON 结构

如果不存在旧配置，则安装脚本直接写出标准的新安装 channel 配置。

## 失败处理

以下情况必须直接让安装失败：

- 写入 `channels.astron-claw` 失败
- 删除 legacy 的 `plugins.entries.astron-claw.config` 失败

如果两个位置同时残留有效配置，就会违背这次“彻底归一化”的目标，也会重新引入优先级不明确的问题。

## 测试策略

### 运行时测试

补充配置解析测试，证明：

- `channels.astron-claw` 是唯一被接受的运行时配置来源
- 仅存在 `plugins.entries.astron-claw.config` 时，运行时不会把它当作有效配置
- 当只有 legacy plugin entry 配置时，account discovery 不应激活

### 安装脚本测试

将迁移逻辑抽出或隔离到足够可测的最小单元，验证：

- 旧的 plugin entry 配置会迁移到 `channels.astron-claw`
- `plugins.entries.astron-claw.config` 会在迁移后被移除
- 旧配置中的 `retry`、`media` 等可选字段会被保留
- 不存在旧配置时，新安装仍然会生成规范的 channel 配置

## 风险

- 用户如果只更新代码但没有重新执行安装脚本，在新运行时代码下可能暂时丢失配置
- 安装脚本的迁移逻辑如果处理不严谨，可能会静默丢失旧配置中的可选字段
- OpenClaw CLI 在插件注册后写入 `channels.*` 的行为必须继续稳定可用

## 发布说明

- 这是一个对旧安装有迁移要求的变更
- 发布说明中应明确提示：已有用户需要重新执行一次 `install.sh` 完成配置迁移

## 验收标准

- 新安装后，astron-claw 的运行时配置只出现在 `channels.astron-claw`
- 旧安装重跑安装脚本后，会把 legacy plugin entry 配置迁移到 `channels.astron-claw`
- `plugins.entries.astron-claw.config` 在安装结束后被移除
- 插件运行时代码不再读取 `plugins.entries.astron-claw.config`
- 测试覆盖迁移行为以及运行时对规范路径的强约束
