#!/usr/bin/env node
// Node 小岛：仅保留飞书 registerApp（扫码一键建应用）。
// Python 主体没有 registerApp 的等价物，故保留此脚本作为子进程调用。
//
// 用法：node register_app.mjs <output-json-path>
//   - 二维码与提示打印到 stderr（继承终端，供用户扫码）
//   - 成功后把 { appId, appSecret, userOpenId? } 写入 output-json-path
//
// 群聊只用 tenant 身份；单聊按需申请当前用户的日历只读 OAuth。
import { writeFileSync } from "node:fs";
import * as Lark from "@larksuiteoapi/node-sdk";
import qrcodeTerminal from "qrcode-terminal";

const qr = qrcodeTerminal.default || qrcodeTerminal;

async function main() {
  const outputPath = process.argv[2];
  if (!outputPath) {
    console.error("用法：node register_app.mjs <output-json-path>");
    process.exit(2);
  }

  console.error("即将创建飞书机器人应用，请使用飞书扫码确认。");
  const credentials = await Lark.registerApp({
    source: "customer-a-ma-demo",
    appPreset: {
      name: "客户A MA Demo Bot",
      desc: "由火山方舟 Managed Agents 驱动的飞书机器人（迁移方案演示）"
    },
    addons: {
      // tenant scope 用于 Bot 消息与群上下文；user scope 仅供单聊按需只读本人日历和搜索文档。
      //   - im:message:send_as_bot   以应用身份发消息 / 回复
      //   - im:message               基础消息读取
      //   - im:message.group_msg     读整段群历史（窗口上下文靠 im.v1.message.list 拉历史，缺此 scope 会 400 / 230027）
      //   - im:chat:readonly         读群信息（thread/chat 容器，可选但保险）
      //   - im:resource              下载消息中的文件 / 图片资源（im.v1.message_resource.get；读群文件必需）
      scopes: {
        tenant: [
          "im:message:send_as_bot",
          "im:message",
          "im:message.group_msg",
          "im:chat:readonly",
          "im:resource"
        ],
        user: [
          "offline_access",
          "auth:user.id:read",
          "calendar:calendar:read",
          "calendar:calendar.event:read",
          "calendar:calendar.free_busy:read",
          "search:docs:read"
        ]
      },
      events: { items: { tenant: ["im.message.receive_v1"] } }
    },
    onQRCodeReady(info) {
      qr.generate(info.url, { small: true });
      console.error(`如果二维码无法扫描，请打开：${info.url}`);
      console.error(`链接将在 ${info.expireIn} 秒后失效。`);
    }
  });

  const result = {
    appId: credentials.client_id,
    appSecret: credentials.client_secret,
    userOpenId: credentials.user_info?.open_id || null
  };
  writeFileSync(outputPath, JSON.stringify(result), { encoding: "utf8", mode: 0o600 });
  console.error(`飞书应用已创建：${result.appId}`);
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
