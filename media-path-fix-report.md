# 媒体本地存储路径修复报告

## 问题描述

上传含中文文件名的媒体（如 `微信图片_2026-01-06_185627_042.png`）后，本地保存的文件名为：

```
E5_BE_AE_E4_BF_A1_E5_9B_BE_E7_89_87_2026-01-06_185627_042---{uuid}.jpg
```

而预期应为：

```
微信图片_2026-01-06_185627_042---{uuid}.jpg
```

## 根因分析

S3/MinIO 的 `downloadUrl` 中文件名以 percent-encoding 形式传递：

```
http://.../%E5%BE%AE%E4%BF%A1%E5%9B%BE%E7%89%87_2026-01-06_185627_042.png
```

`loadWebMedia` 从 URL 推断的 `fileName` 保留了 percent-encoding，传入 `buildMediaFileName` 后：

1. `parsePath(fileName).name` → `%E5%BE%AE%E4%BF%A1%E5%9B%BE%E7%89%87_2026-01-06_185627_042`
2. `sanitizeStem` 的正则 `/[^\p{L}\p{N}._-]+/gu` 将 `%` 视为非法字符替换为 `_`
3. 最终变成 `E5_BE_AE_E4_BF_A1_...`（UTF-8 字节的十六进制拼接）

## 修复方案

在 `buildMediaFileName` 中，对 `fileName` 先做 `decodeURIComponent` 解码，再传入 `parsePath` 和 `sanitizeStem`：

```ts
// media-path.ts — 修复前
export function buildMediaFileName(fileName: string, uuid: string, ext: string): string {
  const stem = sanitizeStem(parsePath(fileName).name);
  return stem ? `${stem}---${uuid}${ext}` : `${uuid}${ext}`;
}

// media-path.ts — 修复后
export function buildMediaFileName(fileName: string, uuid: string, ext: string): string {
  // Decode percent-encoded file names (common in S3/HTTP URLs)
  let decoded = fileName;
  try { decoded = decodeURIComponent(fileName); } catch { /* keep original if malformed */ }
  const stem = sanitizeStem(parsePath(decoded).name);
  return stem ? `${stem}---${uuid}${ext}` : `${uuid}${ext}`;
}
```

修改范围：仅 `plugin/src/messaging/media-path.ts` 一个文件，一处改动。

## 测试验证

### 测试步骤

1. 卸载旧插件 → 重新安装含修复的插件 → 重启 gateway
2. 上传中文文件名图片到 S3，获取 percent-encoded 的 `downloadUrl`
3. 通过 `POST /bridge/chat` 发送带图片的消息

### 测试结果

**保存路径** ✅

```
/root/.openclaw/media/inbound/微信图片_2026-01-06_185627_042---e4b47369-96b4-421f-aec9-ae10525e6888.jpg
```

中文文件名正确解码保留，符合 SDK 约定 `{name}---{uuid}{ext}`。

**LLM 识别** ✅

bot 通过 `read` 工具读取了保存的图片文件，成功识别并描述了图片内容（紫色背景的星形卡通 App 图标）。

**完整 SSE 流** ✅

```
event: session     → 会话创建
event: tool_call   → read 图片文件
event: tool_result → 图片识别成功
event: chunk (×N)  → 流式文本输出
event: done        → 完整回复
```
