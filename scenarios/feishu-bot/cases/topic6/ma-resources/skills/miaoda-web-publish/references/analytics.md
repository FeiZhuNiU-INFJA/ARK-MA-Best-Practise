# 妙搭数据查询与运行观测

所有数据都来自 `lark-cli apps` 对妙搭 API 的调用，环境固定为在线 `online`。不得打开妙搭管理后台页面抓取数字。

## 应用与发布状态

```bash
lark-cli apps +get --as user --format json --app-id "<app_id>"
lark-cli apps +release-list --as user --format json --app-id "<app_id>" --page-size 20
lark-cli apps +release-get --as user --format json --app-id "<app_id>" --release-id "<release_id>"
```

## 访问分析

用户聚合：

```bash
lark-cli apps +analytics-list \
  --as user --format json \
  --app-id "<app_id>" \
  --analytics users \
  --granularity day \
  --since 30d
```

页面浏览：

```bash
lark-cli apps +analytics-list \
  --as user --format json \
  --app-id "<app_id>" \
  --analytics page-view \
  --granularity day \
  --since 30d
```

可选过滤：`--series`、`--page`、`--device-type desktop|mobile`、`--until`。粒度是 `day|week|month`。时间戳可能为纳秒；解析前先识别单位。

用户问题“实际浏览/使用人数”应优先返回接口明确提供的聚合口径，例如总用户、新增用户、活跃用户；不要擅自把 PV 当人数，也不要用请求次数替代 UV。若返回 `null` 或空序列，说明“接口当前无已结算值/无数据”，不能写成 0。

该接口**不会提供访客昵称或具体姓名**。即使日志或 trace 可按 `user-id` 过滤，也不能据此声称获得了访问者目录、昵称或逐人访问清单。

## 请求与运行指标

```bash
lark-cli apps +metric-list \
  --as user --format json \
  --app-id "<app_id>" \
  --metric requests \
  --series total \
  --since 1d
```

指标族：`requests|latency|cpu|memory`。可用 `--series` 选择 total/error 或 p50/p99 等服务端支持序列，用 `--api`、`--page` 过滤，用 `--down-sample 1m|1h|1d` 降采样。指标时间戳使用秒，与 analytics 的纳秒不同。

## 日志与 trace

```bash
lark-cli apps +log-list --as user --format json --app-id "<app_id>" --level ERROR --since 1h
lark-cli apps +log-get --as user --format json --app-id "<app_id>" --log-id "<log_id>"
lark-cli apps +trace-list --as user --format json --app-id "<app_id>" --since 1h
lark-cli apps +trace-get --as user --format json --app-id "<app_id>" --trace-id "<trace_id>"
```

日志支持级别、关键字、模块、API、页面、耗时、trace ID 和 user ID 过滤。trace 支持 trace ID、root span 和 user ID 过滤。返回内容可能含业务数据，交付前只摘取诊断必要字段并做脱敏。

## 访问范围

只读查询：

```bash
lark-cli apps +access-scope-get --as user --format json --app-id "<app_id>"
```

部分创意 HTML 应用可能返回“不支持/请在后台查询”类错误。此时准确报告 API 不可查询；不得打开网页后台替代获取，也不得推测为 public、tenant 或 specific。

`+access-scope-set` 会改变真实访问权限。只有用户明确指定目标范围和必要参数后才执行，并先 `--dry-run` 检查请求。

## 输出建议

查询结果应包含：`app_id`、时间范围、时区、粒度、过滤条件、指标名、值或序列、数据状态、API 限制。不要只返回一个脱离口径的数字。

