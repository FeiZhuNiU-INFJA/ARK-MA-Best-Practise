(function () {
  "use strict";

  var rootStyle = getComputedStyle(document.documentElement);
  var theme = {
    bg: rootStyle.getPropertyValue("--bg2").trim(),
    surface: rootStyle.getPropertyValue("--surface").trim(),
    ink: rootStyle.getPropertyValue("--ink").trim(),
    muted: rootStyle.getPropertyValue("--muted").trim(),
    rule: rootStyle.getPropertyValue("--rule").trim(),
    accent: rootStyle.getPropertyValue("--accent").trim(),
    accent2: rootStyle.getPropertyValue("--accent2").trim()
  };

  if (window.mermaid) {
    window.mermaid.initialize({
      startOnLoad: true,
      securityLevel: "loose",
      theme: "base",
      themeVariables: {
        background: theme.bg,
        primaryColor: theme.surface,
        primaryTextColor: theme.ink,
        primaryBorderColor: theme.rule,
        lineColor: theme.muted,
        secondaryColor: theme.bg,
        tertiaryColor: theme.surface,
        noteBkgColor: theme.surface,
        noteTextColor: theme.ink,
        noteBorderColor: theme.rule,
        actorBkg: theme.bg,
        actorBorder: theme.rule,
        actorTextColor: theme.ink,
        signalColor: theme.muted,
        signalTextColor: theme.ink,
        labelBoxBkgColor: theme.bg,
        labelBoxBorderColor: theme.rule,
        labelTextColor: theme.ink,
        activationBkgColor: theme.surface,
        activationBorderColor: theme.accent,
        sequenceNumberColor: theme.bg
      },
      flowchart: {
        curve: "basis",
        htmlLabels: true
      },
      sequence: {
        diagramMarginX: 20,
        diagramMarginY: 20,
        actorMargin: 40,
        width: 140,
        height: 56,
        messageMargin: 34
      }
    });
  }

  var details = {
    feishu: {
      kicker: "INGRESS",
      title: "飞书 Bot / WebSocket",
      summary: "同一个飞书 App 只运行一个 Gateway WebSocket。根消息创建任务，后续进度、审核、OAuth 和终态都回到同一话题。",
      input: "根消息、话题回复、卡片 action",
      output: "IncomingMessage 与标准化回调对象",
      guard: "同一 App 只允许一个 Gateway 进程持有锁",
      href: "../../../arkagent/cli.py#L80-L130",
      label: "arkagent/cli.py · Gateway 启动"
    },
    gateway: {
      kicker: "CONTROL PLANE",
      title: "Topicx Gateway",
      summary: "统一接收消息和卡片动作，实例化 Topic5/Topic6 的 Runner、Store 与 HITL，并把后续事件重新分派到正确业务。",
      input: "IncomingMessage、卡片 action",
      output: "Router 调度、业务调度、飞书回复",
      guard: "不把身份与租约权威交给模型",
      href: "../gateway.py#L33-L217",
      label: "gateway.py · build_gateway"
    },
    router: {
      kicker: "ROUTING",
      title: "Router Session",
      summary: "每条根消息创建独立轻量 Session，只完成 Topic5、Topic6 或 unsupported 分类，不挂载业务 Memory、Skill、MCP 或用户 Vault。",
      input: "用户原始请求文本",
      output: "intent、mode、reason",
      guard: "禁止提交 task/session/job/open_id/lease_token",
      href: "../topicx_runner.py#L70-L169",
      label: "topicx_runner.py · Router"
    },
    control: {
      kicker: "DURABLE STATE",
      title: "ControlStore",
      summary: "以 SQLite/WAL 持久化跨 Session 的 TaskRun、控制事件和 Topic6 单例 Lease，并提供幂等绑定与恢复查询。",
      input: "飞书事件、路由结果、Session/Job 回执",
      output: "TaskRun 状态、correlation、Lease ownership",
      guard: "路由只提交一次；Lease 使用 BEGIN IMMEDIATE",
      href: "../control_store.py#L12-L492",
      label: "control_store.py · durable control"
    },
    stop: {
      kicker: "LIFECYCLE",
      title: "StopHandler",
      summary: "统一校验停止权限，只中断 Business Session；观察到 interrupt 之后的终态才落 stopped，并在 Topic6 终态后释放 Lease。",
      input: "停止按钮 action",
      output: "interrupt、状态更新、卡片 patch",
      guard: "超时或 API 错误保持 stopping 与 Lease",
      href: "../stop_handler.py#L18-L183",
      label: "stop_handler.py · stop authority"
    },
    topic5: {
      kicker: "BUSINESS PLANE",
      title: "Topic5 Business",
      summary: "营销活动复盘业务包，拥有独立 Coordinator、Validator、PipelineStore、HITL 和交付 Skill。不同飞书话题允许并行。",
      input: "task_id、mode、用户消息、活动文件",
      output: "进度/HITL、报告产物、飞书文档",
      guard: "按话题防重，不使用 Topic6 全局 Lease",
      href: "../../topic5/topic5_runner.py#L450-L585",
      label: "topic5_runner.py · start_routed_job"
    },
    topic6: {
      kicker: "BUSINESS PLANE",
      title: "Topic6 Business",
      summary: "社媒热点周报业务包，拥有独立 Coordinator、Annotator、PipelineStore、HITL、Artifact Sync 与妙搭发布流程。",
      input: "task_id、lease_token、用户 Vault、mode",
      output: "进度/HITL、周报产物、Miaoda online_url",
      guard: "必须验证当前 task 是 Lease owner",
      href: "../../topic6/topic6_runner.py#L377-L520",
      label: "topic6_runner.py · start_routed_job"
    },
    agents: {
      kicker: "MANAGED AGENTS",
      title: "Coordinator / Sub-agent",
      summary: "Router、Topic5 和 Topic6 使用不同 Agent/Environment。Business Session 只挂载自身 Memory 与 Skill，避免跨业务资源污染。",
      input: "Session 配置、Environment、Memory、Vault",
      output: "SSE 事件、工具调用、结构化产物",
      guard: "平台仅一层 MA；跨业务编排留在 Gateway",
      href: "../ma-resources/agents/router.json",
      label: "ma-resources/agents/router.json"
    },
    vault: {
      kicker: "CREDENTIAL BOUNDARY",
      title: "Vault / User Vault",
      summary: "系统级凭据由 Ark Vault 注入；Topic6 还按任务发起人解析用户 Vault，用于 Miaoda 发布并在运行期间做 keepalive。",
      input: "Gateway 可信用户 open_id",
      output: "Session vault_ids",
      guard: "用户 token 不写入 Session config、Memory 或仓库",
      href: "../../topic6/topic6_user_oauth.py",
      label: "topic6_user_oauth.py"
    }
  };

  var nodes = Array.prototype.slice.call(document.querySelectorAll(".arch-node"));
  var detailKicker = document.getElementById("detail-kicker");
  var detailTitle = document.getElementById("detail-title");
  var detailSummary = document.getElementById("detail-summary");
  var detailInput = document.getElementById("detail-input");
  var detailOutput = document.getElementById("detail-output");
  var detailGuard = document.getElementById("detail-guard");
  var detailLink = document.getElementById("detail-link");

  function selectNode(id) {
    var detail = details[id];
    if (!detail) return;
    nodes.forEach(function (node) {
      node.setAttribute("aria-pressed", String(node.getAttribute("data-node") === id));
    });
    detailKicker.textContent = detail.kicker;
    detailTitle.textContent = detail.title;
    detailSummary.textContent = detail.summary;
    detailInput.textContent = detail.input;
    detailOutput.textContent = detail.output;
    detailGuard.textContent = detail.guard;
    detailLink.href = detail.href;
    detailLink.textContent = detail.label;
  }

  nodes.forEach(function (node) {
    node.addEventListener("click", function () {
      selectNode(node.getAttribute("data-node"));
    });
  });

  var navLinks = Array.prototype.slice.call(document.querySelectorAll(".side-nav nav a"));
  var sections = navLinks.map(function (link) {
    return document.querySelector(link.getAttribute("href"));
  }).filter(Boolean);

  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        navLinks.forEach(function (link) {
          link.classList.toggle("active", link.getAttribute("href") === "#" + entry.target.id);
        });
      });
    }, { rootMargin: "-20% 0px -70% 0px", threshold: 0 });
    sections.forEach(function (section) { observer.observe(section); });
  }
})();
