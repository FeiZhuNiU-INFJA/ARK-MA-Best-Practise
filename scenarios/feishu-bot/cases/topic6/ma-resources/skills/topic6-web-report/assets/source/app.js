(() => {
  const links = [...document.querySelectorAll(".toc-link")];
  const sections = [...document.querySelectorAll("[data-section]")];
  const navShell = document.querySelector("[data-sticky-nav]");
  const backToTop = document.querySelector(".back-to-top");
  const progress = document.querySelector(".reading-progress");

  const setActive = (id) => {
    links.forEach((link) => {
      const active = link.dataset.target === id;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  };

  links.forEach((link) => {
    link.addEventListener("click", () => setActive(link.dataset.target));
  });

  document.querySelectorAll("[data-tab-group]").forEach((group) => {
    const tabs = [...group.querySelectorAll('[role="tab"]')];
    const panels = [...group.querySelectorAll('[role="tabpanel"]')];
    const tablist = group.querySelector('[role="tablist"]');

    const revealPanelStart = (panel) => {
      window.requestAnimationFrame(() => {
        const mainNavHeight = navShell?.getBoundingClientRect().height ?? 0;
        const tablistHeight = tablist?.getBoundingClientRect().height ?? 0;
        const targetTop = window.scrollY + panel.getBoundingClientRect().top - mainNavHeight - tablistHeight - 12;
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({ top: Math.max(0, targetTop), behavior: reducedMotion ? "auto" : "smooth" });
      });
    };

    const activateTab = (tab, moveFocus = false) => {
      let activePanel;
      tabs.forEach((item) => {
        const active = item === tab;
        item.setAttribute("aria-selected", String(active));
        item.tabIndex = active ? 0 : -1;
      });
      panels.forEach((panel) => {
        const active = panel.id === tab.getAttribute("aria-controls");
        panel.hidden = !active;
        panel.classList.toggle("is-active", active);
        if (active) activePanel = panel;
      });
      if (moveFocus) tab.focus({ preventScroll: true });
      if (activePanel) revealPanelStart(activePanel);
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateTab(tab));
      tab.addEventListener("keydown", (event) => {
        let nextIndex = index;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") nextIndex = 0;
        else if (event.key === "End") nextIndex = tabs.length - 1;
        else return;
        event.preventDefault();
        activateTab(tabs[nextIndex], true);
      });
    });
  });

  document.querySelectorAll("[data-bubble-chart]").forEach((chart) => {
    const stage = chart.querySelector(".industry-bubble-stage");
    const tooltip = chart.querySelector(".industry-bubble-tooltip");
    const points = [...chart.querySelectorAll(".industry-bubble")];
    if (!stage || !tooltip || !points.length) return;

    const placeTooltip = (point, pointerEvent) => {
      const stageRect = stage.getBoundingClientRect();
      const pointRect = point.getBoundingClientRect();
      const rawX = pointerEvent ? pointerEvent.clientX - stageRect.left : pointRect.left + pointRect.width / 2 - stageRect.left;
      const rawY = pointerEvent ? pointerEvent.clientY - stageRect.top : pointRect.top - stageRect.top;
      const x = Math.min(stage.clientWidth - 96, Math.max(96, rawX));
      const y = Math.max(76, rawY);
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    };

    const showTooltip = (point, pointerEvent) => {
      tooltip.replaceChildren();
      const title = document.createElement("strong");
      const detail = document.createElement("span");
      title.textContent = point.dataset.industry;
      detail.textContent = `热点数量 ${point.dataset.count} · 中位热度分 ${point.dataset.score}`;
      tooltip.append(title, detail);
      tooltip.hidden = false;
      placeTooltip(point, pointerEvent);
    };

    const hideTooltip = () => { tooltip.hidden = true; };

    points.forEach((point) => {
      point.addEventListener("pointerenter", (event) => showTooltip(point, event));
      point.addEventListener("pointermove", (event) => placeTooltip(point, event));
      point.addEventListener("pointerleave", hideTooltip);
      point.addEventListener("focus", () => showTooltip(point));
      point.addEventListener("blur", hideTooltip);
    });
  });

  const updateViewportState = () => {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    if (progress) progress.style.transform = `scaleX(${Math.min(1, scrollTop / maxScroll)})`;
    if (backToTop) backToTop.classList.toggle("visible", scrollTop > window.innerHeight * 0.72);
    if (navShell) navShell.classList.toggle("is-stuck", navShell.getBoundingClientRect().top <= 1 && scrollTop > 120);

    const activationLine = (navShell?.getBoundingClientRect().height ?? 0) + 140;
    let current = sections[0];
    sections.forEach((section) => {
      if (section.getBoundingClientRect().top <= activationLine) current = section;
    });
    if (current) setActive(current.id);
  };

  window.addEventListener("scroll", updateViewportState, { passive: true });
  window.addEventListener("resize", updateViewportState, { passive: true });
  updateViewportState();
  if (sections[0]) setActive(sections[0].id);
})();
