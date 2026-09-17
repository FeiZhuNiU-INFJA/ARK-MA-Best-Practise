# Debug Session: oauth-vault-token-limit
- **Status**: [OPEN]
- **Issue**: 文档 OAuth 授权成功，但用户 access token 无法写入 Ark Vault environment_variable，导致授权后无法续跑。
- **Debug Server**: Pending startup
- **Log File**: .dbg/trae-debug-log-oauth-vault-token-limit.ndjson

## Reproduction Steps
1. 单聊发送“查一下我最近写了哪些文档”。
2. 点击文档搜索授权卡片并完成飞书授权。
3. 观察授权完成后的 Credential 更新和原请求续跑。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | 文档 OAuth access token 字节数超过 Vault 4096 限制 | High | Low | Pending |
| B | 限制只适用于 environment_variable，其他 Vault 传递类型可用 | Medium | Medium | Pending |
| C | lark-cli 可使用受控文件中的 token，避免环境变量注入 | Medium | Medium | Pending |
| D | 应用未发布 `search:docs:read` 导致授权后仍缺权限 | Low | Low | Pending |

## Log Evidence
Pending instrumentation and reproduction.

## Verification Conclusion
Pending.
