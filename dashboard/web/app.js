const state = {
  dashboard: null,
  viewId: "market",
  selected: { market: "000985", industry: "801730" },
  rankMetric: "return1y",
  chartMetric: "close",
  query: "",
  selectedFactor: "size",
  selectedComparison: 0,
  selectedFactorSeries: null,
  factorChartMode: "relative",
  selectedEnhancedIndex: null,
};

const metricLabels = {
  return1y: "近一年",
  return3y: "近三年",
  return5y: "近五年",
  pePercentile: "PE长期分位",
  pbPercentile: "PB长期分位",
};
const chartLabels = { close: "指数", pe: "PE", pb: "PB" };
const allocationColors = ["#b23b32", "#c46b38", "#98721f", "#6b7c37", "#167457", "#2d8b8b", "#3e6f9d", "#5863a3", "#76558e", "#9a5272", "#7d8a89", "#ac9d7a"];
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function currentView() {
  return state.dashboard.views.find((view) => view.id === state.viewId);
}

function currentItem() {
  const view = currentView();
  return view.items.find((item) => item.code === state.selected[state.viewId]) || view.items[0];
}

function number(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : "--";
}

function percent(value, digits = 1, sign = true) {
  if (!Number.isFinite(value)) return "--";
  return `${sign && value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

function percentile(value) {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : "--";
}

function valueClass(value) {
  return Number.isFinite(value) ? (value > 0 ? "positive" : value < 0 ? "negative" : "") : "";
}

function rankFormat(value) {
  return state.rankMetric.endsWith("Percentile") ? percentile(value) : percent(value);
}

function filteredItems() {
  const query = state.query.trim().toLowerCase();
  const items = currentView().items;
  return query ? items.filter((item) => `${item.name} ${item.code}`.toLowerCase().includes(query)) : items;
}

function renderHeader() {
  const view = currentView();
  $("#as-of").textContent = view.asOf || "--";
  $("#view-source").textContent = `数据源 · ${view.source || "--"}`;
  $("#view-title").textContent = view.label;
  $("#view-description").textContent = view.description;
  $("#footer-source").textContent = `数据源：${view.source || "--"}`;
  $$("[data-view]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.view === state.viewId)));

  const returns = view.items.map((item) => item.return1y).filter(Number.isFinite).sort((a, b) => a - b);
  const best = [...view.items].filter((item) => Number.isFinite(item.return1y)).sort((a, b) => b.return1y - a.return1y)[0];
  const worst = [...view.items].filter((item) => Number.isFinite(item.return1y)).sort((a, b) => a.return1y - b.return1y)[0];
  const median = returns.length ? returns[Math.floor(returns.length / 2)] : null;
  $("#snapshot").innerHTML = `
    <div class="snapshot-item"><span>一年最强</span><strong class="${valueClass(best?.return1y)}">${best ? best.name : "--"}</strong></div>
    <div class="snapshot-item"><span>一年中位数</span><strong class="${valueClass(median)}">${percent(median)}</strong></div>
    <div class="snapshot-item"><span>一年最弱</span><strong class="${valueClass(worst?.return1y)}">${worst ? worst.name : "--"}</strong></div>
  `;
}

function renderRankControls() {
  const view = currentView();
  if (!view.metrics.includes(state.rankMetric)) state.rankMetric = view.metrics[0];
  const container = $("#rank-controls");
  container.innerHTML = "";
  view.metrics.forEach((metric) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = metricLabels[metric];
    button.dataset.metric = metric;
    button.setAttribute("aria-pressed", String(metric === state.rankMetric));
    button.addEventListener("click", () => {
      state.rankMetric = metric;
      renderRankControls();
      renderRanking();
    });
    container.appendChild(button);
  });
}

function renderRanking() {
  const items = filteredItems().sort((a, b) => (b[state.rankMetric] ?? -Infinity) - (a[state.rankMetric] ?? -Infinity));
  const values = items.map((item) => item[state.rankMetric]).filter(Number.isFinite);
  const neutral = state.rankMetric.endsWith("Percentile");
  const maxAbs = Math.max(...values.map(Math.abs), 0.01);
  $("#ranking-title").textContent = metricLabels[state.rankMetric];
  $("#item-count").textContent = `${items.length} 个指数`;
  const container = $("#ranking");
  container.innerHTML = "";
  if (!items.length) {
    container.innerHTML = '<div class="empty-state">没有匹配的指数</div>';
    return;
  }
  items.forEach((item, index) => {
    const value = item[state.rankMetric];
    const width = neutral ? Math.max(0, Math.min(100, (value ?? 0) * 100)) : Math.min(50, Math.abs(value ?? 0) / maxAbs * 50);
    const barClass = neutral ? "neutral" : value >= 0 ? "up" : "down";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `rank-row${item.code === state.selected[state.viewId] ? " is-selected" : ""}`;
    button.setAttribute("aria-label", `${item.name}，${metricLabels[state.rankMetric]} ${rankFormat(value)}`);
    button.innerHTML = `
      <span class="rank-number">${String(index + 1).padStart(2, "0")}</span>
      <span class="rank-name">${item.name}</span>
      <span class="rank-track"><span class="rank-bar ${barClass}" style="width:${width}%"></span></span>
      <span class="rank-value ${neutral ? "" : valueClass(value)}">${rankFormat(value)}</span>
    `;
    button.addEventListener("click", () => selectItem(item.code));
    container.appendChild(button);
  });
}

function renderMetrics(item) {
  const metrics = state.viewId === "market"
    ? [
        ["指数点位", number(item.close), Number.isFinite(item.changePct) ? `当日 ${item.changePct > 0 ? "+" : ""}${number(item.changePct)}%` : "价格指数"],
        ["近一年", percent(item.return1y), "价格收益"],
        ["近五年", percent(item.return5y), "价格收益"],
        ["滚动 PE", number(item.pe), `长期分位 ${percentile(item.pePercentile)}`],
        ["样本口径", item.subtitle || "--", "官方定期调整"],
      ]
    : [
        ["近一年", percent(item.return1y), "价格收益"],
        ["近五年", percent(item.return5y), "价格收益"],
        ["PE", number(item.pe), `长期分位 ${percentile(item.pePercentile)}`],
        ["PB", number(item.pb), `长期分位 ${percentile(item.pbPercentile)}`],
        ["股息率", Number.isFinite(item.dividendYield) ? `${number(item.dividendYield)}%` : "--", "申万行业口径"],
      ];
  $("#metric-strip").innerHTML = metrics.map(([label, value, note], index) => `
    <div class="metric"><span>${label}</span><strong class="${index < 2 ? valueClass(index === 0 && state.viewId === "market" ? null : index === 0 ? item.return1y : item.return5y) : ""}">${value}</strong><small>${note}</small></div>
  `).join("");
}

function renderChartControls(item) {
  const available = ["close", "pe", "pb"].filter((metric) => item.history.some((point) => Number.isFinite(point[metric])));
  if (!available.includes(state.chartMetric)) state.chartMetric = available[0] || "close";
  const container = $("#chart-controls");
  container.innerHTML = "";
  available.forEach((metric) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = chartLabels[metric];
    button.setAttribute("aria-pressed", String(metric === state.chartMetric));
    button.addEventListener("click", () => {
      state.chartMetric = metric;
      renderChartControls(item);
      renderChart(item);
    });
    container.appendChild(button);
  });
}

function renderChart(item) {
  const svg = $("#history-chart");
  const tooltip = $("#chart-tooltip");
  const history = item.history.filter((point) => Number.isFinite(point[state.chartMetric]));
  svg.replaceChildren();
  tooltip.hidden = true;
  if (history.length < 2) {
    $("#chart-value").textContent = "该指标暂无足够历史";
    return;
  }

  const width = 820;
  const height = 330;
  const margin = { top: 18, right: 18, bottom: 34, left: 55 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const values = history.map((point) => point[state.chartMetric]);
  let min = Math.min(...values);
  let max = Math.max(...values);
  const pad = Math.max((max - min) * 0.12, Math.abs(max) * 0.02, 0.1);
  min -= pad;
  max += pad;
  const timestamps = history.map((point) => Date.parse(point.date));
  const first = timestamps[0];
  const last = timestamps.at(-1);
  const x = (index) => margin.left + (timestamps[index] - first) / (last - first) * innerWidth;
  const y = (value) => margin.top + (max - value) / (max - min) * innerHeight;
  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    svg.appendChild(node);
    return node;
  };

  for (let index = 0; index <= 4; index += 1) {
    const gridY = margin.top + index / 4 * innerHeight;
    make("line", { x1: margin.left, y1: gridY, x2: width - margin.right, y2: gridY, stroke: "#e3e7e3", "stroke-width": 1 });
    const label = make("text", { x: margin.left - 9, y: gridY + 4, fill: "#66706a", "font-size": 10, "text-anchor": "end" });
    label.textContent = (max - index / 4 * (max - min)).toFixed(state.chartMetric === "close" ? 0 : 1);
  }
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.floor((history.length - 1) * ratio));
  [...new Set(ticks)].forEach((index) => {
    const label = make("text", { x: x(index), y: height - 10, fill: "#66706a", "font-size": 10, "text-anchor": "middle" });
    label.textContent = history[index].date.slice(0, 7);
  });
  const d = history.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(point[state.chartMetric]).toFixed(2)}`).join(" ");
  make("path", { d, fill: "none", stroke: "#b23b32", "stroke-width": 2.1, "stroke-linejoin": "round", "stroke-linecap": "round" });
  const cursor = make("line", { x1: 0, y1: margin.top, x2: 0, y2: height - margin.bottom, stroke: "#98721f", "stroke-width": 1, visibility: "hidden" });
  const dot = make("circle", { cx: 0, cy: 0, r: 4, fill: "#fff", stroke: "#b23b32", "stroke-width": 2, visibility: "hidden" });
  const overlay = make("rect", { x: margin.left, y: margin.top, width: innerWidth, height: innerHeight, fill: "transparent" });
  const latest = history.at(-1);
  $("#chart-value").textContent = `${latest.date} · ${chartLabels[state.chartMetric]} ${number(latest[state.chartMetric], state.chartMetric === "close" ? 0 : 2)}`;

  overlay.addEventListener("pointermove", (event) => {
    const bounds = svg.getBoundingClientRect();
    const localX = (event.clientX - bounds.left) / bounds.width * width;
    const target = first + Math.max(0, Math.min(1, (localX - margin.left) / innerWidth)) * (last - first);
    let closest = 0;
    let distance = Infinity;
    timestamps.forEach((timestamp, index) => {
      const candidate = Math.abs(timestamp - target);
      if (candidate < distance) { closest = index; distance = candidate; }
    });
    const point = history[closest];
    const cursorX = x(closest);
    const cursorY = y(point[state.chartMetric]);
    cursor.setAttribute("x1", cursorX);
    cursor.setAttribute("x2", cursorX);
    cursor.setAttribute("visibility", "visible");
    dot.setAttribute("cx", cursorX);
    dot.setAttribute("cy", cursorY);
    dot.setAttribute("visibility", "visible");
    tooltip.innerHTML = `<strong>${point.date}</strong><br>${chartLabels[state.chartMetric]} ${number(point[state.chartMetric], state.chartMetric === "close" ? 0 : 2)}`;
    tooltip.hidden = false;
    tooltip.style.left = `${Math.min(bounds.width - 130, Math.max(8, cursorX / width * bounds.width + 10))}px`;
    tooltip.style.top = `${Math.max(6, cursorY / height * bounds.height - 48)}px`;
  });
  overlay.addEventListener("pointerleave", () => {
    cursor.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tooltip.hidden = true;
  });
}

function renderMethodology(item) {
  const method = item.methodology || {};
  $("#method-status").textContent = method.statusLabel || "官方指数口径";
  $("#method-summary").textContent = method.summary || "暂无补充说明。";
  const events = Array.isArray(method.events) ? method.events : [];
  $("#method-events").innerHTML = events.map((event) => `
    <li><time>${event.date || "--"}</time><strong>${event.label || "记录"}</strong><span>${event.description || ""}</span></li>
  `).join("");
}

function renderComponents(item) {
  const components = Array.isArray(item.components) ? item.components.slice(0, 10) : [];
  $("#component-summary").textContent = components.length
    ? `${item.name} · 权重前 10 · 口径日期 ${item.componentDate || components[0]?.date || "--"}`
    : `${item.name}暂无成分权重数据`;
  $("#component-body").innerHTML = components.length
    ? components.map((component, index) => `<tr><td>${index + 1}</td><td>${component.code}</td><td>${component.name}</td><td>${Number.isFinite(component.weight) ? `${component.weight.toFixed(4)}%` : "--"}</td><td>${component.date || item.componentDate || "--"}</td></tr>`).join("")
    : '<tr><td class="component-empty" colspan="5">暂无成分权重数据</td></tr>';
}

function renderAllocation(item) {
  const section = $("#allocation-section");
  if (state.viewId !== "market") {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const allocation = Array.isArray(item.sectorAllocation) ? item.sectorAllocation.filter((entry) => Number.isFinite(entry.weight) && entry.weight > 0) : [];
  const pie = $("#allocation-pie");
  const legend = $("#allocation-legend");
  if (!allocation.length) {
    pie.style.background = "var(--line-soft)";
    legend.innerHTML = '<span class="allocation-empty">当前没有可核实的行业归属权重。</span>';
    $("#allocation-note").textContent = "当前成分权重未提供可核实的行业归属";
    return;
  }
  const total = allocation.reduce((sum, entry) => sum + entry.weight, 0);
  let cursor = 0;
  const stops = allocation.map((entry, index) => {
    const start = cursor / total * 100;
    cursor += entry.weight;
    const end = cursor / total * 100;
    return `${allocationColors[index % allocationColors.length]} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
  });
  pie.style.background = `conic-gradient(${stops.join(", ")})`;
  legend.innerHTML = allocation.map((entry, index) => `
    <div class="allocation-entry" title="${entry.name} ${entry.weight.toFixed(2)}%">
      <i class="allocation-swatch" style="background:${allocationColors[index % allocationColors.length]}"></i>
      <strong>${entry.name}</strong><span>${entry.weight.toFixed(1)}%</span>
    </div>
  `).join("");
  $("#allocation-note").textContent = `当前成分股权重归集 · 合计 ${total.toFixed(1)}% · 行业归属采用申万当前一级行业`;
}

function renderTable() {
  const industry = state.viewId === "industry";
  $("#comparison-head").innerHTML = `<tr><th>名称</th><th>定位</th><th>年初至今</th><th>近一年</th><th>近三年</th><th>近五年</th><th>PE</th><th>PE长期分位</th>${industry ? "<th>PB</th><th>PB长期分位</th><th>股息率</th>" : ""}</tr>`;
  $("#comparison-body").innerHTML = currentView().items.map((item) => `
    <tr data-code="${item.code}" class="${item.code === state.selected[state.viewId] ? "is-selected" : ""}">
      <td>${item.name}<br><small>${item.code}</small></td><td>${item.subtitle || "--"}</td>
      <td class="${valueClass(item.returnYtd)}">${percent(item.returnYtd)}</td><td class="${valueClass(item.return1y)}">${percent(item.return1y)}</td>
      <td class="${valueClass(item.return3y)}">${percent(item.return3y)}</td><td class="${valueClass(item.return5y)}">${percent(item.return5y)}</td>
      <td>${number(item.pe)}</td><td>${percentile(item.pePercentile)}</td>
      ${industry ? `<td>${number(item.pb)}</td><td>${percentile(item.pbPercentile)}</td><td>${Number.isFinite(item.dividendYield) ? `${number(item.dividendYield)}%` : "--"}</td>` : ""}
    </tr>
  `).join("");
  $$("#comparison-body tr[data-code]").forEach((row) => row.addEventListener("click", () => selectItem(row.dataset.code)));
}

function renderDetail() {
  const item = currentItem();
  state.selected[state.viewId] = item.code;
  $("#detail-code").textContent = item.code;
  $("#detail-name").textContent = item.name;
  $("#detail-subtitle").textContent = item.subtitle || "--";
  $("#coverage").textContent = item.comparableStart && item.comparableStart !== item.coverageStart
    ? `可比历史 ${item.comparableStart} 起 · 原始记录 ${item.coverageStart} 起`
    : `官方历史计算 ${item.coverageStart || "--"} 起`;
  renderMetrics(item);
  renderChartControls(item);
  renderChart(item);
  renderAllocation(item);
  renderMethodology(item);
  renderComponents(item);
}

function selectItem(code) {
  state.selected[state.viewId] = code;
  renderRanking();
  renderTable();
  renderDetail();
}

function renderAll() {
  $("#standard-dashboard").hidden = false;
  $("#factor-dashboard").hidden = true;
  $("#enhanced-dashboard").hidden = true;
  renderHeader();
  renderRankControls();
  renderRanking();
  renderTable();
  renderDetail();
}

function factorData() {
  return state.dashboard.factors;
}

function currentFactor() {
  const data = factorData();
  return data.factors.find((factor) => factor.id === state.selectedFactor) || data.factors[0];
}

function currentComparison(factor = currentFactor()) {
  return factor.comparisons[state.selectedComparison] || factor.comparisons[0];
}

function factorCoverage(factor) {
  const starts = factor.lines.map((line) => line.coverageStart).filter(Boolean).sort();
  if (!starts.length) return "--";
  const commonStart = starts.at(-1);
  if (starts[0] === commonStart) return `官方可比历史 ${commonStart} 起`;
  return factor.id === "size"
    ? `四档共同历史 ${commonStart} 起`
    : `共同可比历史 ${commonStart} 起`;
}

function renderFactorDashboard() {
  const data = factorData();
  $("#standard-dashboard").hidden = true;
  $("#factor-dashboard").hidden = false;
  $("#enhanced-dashboard").hidden = true;
  $$("[data-view]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.view === "factor")));
  $("#as-of").textContent = data?.asOf || "--";
  $("#footer-source").textContent = `数据源：${data?.source || "--"}`;
  $("#factor-asof").textContent = data?.asOf || "--";
  $("#factor-source").textContent = `数据源 · ${data?.source || "--"}`;
  if (!data || !Array.isArray(data.factors) || !data.factors.length) {
    $("#factor-title").textContent = "因子数据尚未生成";
    return;
  }
  if (!data.factors.some((factor) => factor.id === state.selectedFactor)) state.selectedFactor = data.factors[0].id;
  renderFactorCards();
  renderFactorDetail();
}

function renderFactorCards() {
  const container = $("#factor-cards");
  container.innerHTML = "";
  factorData().factors.forEach((factor) => {
    const comparison = factor.comparisons[0];
    const metrics = comparison.metrics;
    const card = document.createElement("button");
    card.type = "button";
    card.className = `factor-card${factor.id === state.selectedFactor ? " is-selected" : ""}`;
    card.innerHTML = `
      <span class="factor-card-top"><span><span class="factor-card-category">${factor.category}</span><h3>${factor.name}</h3></span><span class="factor-card-label">${comparison.label}</span></span>
      <strong class="factor-card-value ${valueClass(metrics.return1y)}">${percent(metrics.return1y)}</strong>
      <span class="factor-card-note">近一年相对收益 · 历史分位 ${percentile(metrics.percentile)}</span>
    `;
    card.addEventListener("click", () => {
      state.selectedFactor = factor.id;
      state.selectedComparison = 0;
      state.selectedFactorSeries = null;
      renderFactorCards();
      renderFactorDetail();
    });
    container.appendChild(card);
  });
}

function renderFactorDetail() {
  const factor = currentFactor();
  if (!factor) return;
  if (state.selectedComparison >= factor.comparisons.length) state.selectedComparison = 0;
  const comparison = currentComparison(factor);
  const primary = factor.lines.find((line) => line.code === comparison.primaryCode);
  const comparator = factor.lines.find((line) => line.code === comparison.comparatorCode);
  $("#factor-category").textContent = factor.category;
  $("#factor-detail-title").textContent = factor.id === "size" ? factor.name : `${factor.name}风格`;
  $("#factor-detail-description").textContent = factor.description;
  $("#factor-coverage").textContent = factorCoverage(factor);
  const comparisonControls = $("#factor-comparisons");
  comparisonControls.innerHTML = "";
  factor.comparisons.forEach((entry, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = entry.label;
    button.setAttribute("aria-pressed", String(index === state.selectedComparison));
    button.addEventListener("click", () => {
      state.selectedComparison = index;
      renderFactorDetail();
    });
    comparisonControls.appendChild(button);
  });
  const metrics = comparison.metrics;
  const comparisonName = `${primary?.name || "前者"} 相对 ${comparator?.name || "后者"}`;
  $("#factor-metrics").innerHTML = [
    ["近一年相对收益", percent(metrics.return1y), comparisonName],
    ["近三年相对收益", percent(metrics.return3y), comparisonName],
    ["近五年相对收益", percent(metrics.return5y), comparisonName],
    ["当前历史位置", percentile(metrics.percentile), "当前相对水平的长期分位"],
    ["最大相对回撤", percent(metrics.maxDrawdown), "相对净值历史回撤"],
  ].map(([label, value, note], index) => `<div class="metric"><span>${label}</span><strong class="${index < 3 ? valueClass([metrics.return1y, metrics.return3y, metrics.return5y][index]) : ""}">${value}</strong><small>${note}</small></div>`).join("");
  const controls = $("#factor-chart-controls");
  controls.innerHTML = "";
  [["relative", "相对表现"], ["absolute", "同起点净值"]].forEach(([mode, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed", String(mode === state.factorChartMode));
    button.addEventListener("click", () => {
      state.factorChartMode = mode;
      renderFactorDetail();
    });
    controls.appendChild(button);
  });
  renderFactorChart(factor, comparison);
  $("#factor-series-title").textContent = factor.id === "size" ? "四档市值指数" : "官方指数对照";
  const roleLabel = factor.id === "size" ? "市值层级" : "角色";
  $("#factor-table-head").innerHTML = `<tr><th>官方指数</th><th>${roleLabel}</th><th>年初至今</th><th>近一年</th><th>近三年</th><th>近五年</th><th>滚动PE</th><th>PE长期分位</th></tr>`;
  $("#factor-table-body").innerHTML = factor.lines.map((line) => `
    <tr><td>${line.name}<br><small>${line.code}</small></td><td>${line.role}</td><td class="${valueClass(line.returnYtd)}">${percent(line.returnYtd)}</td><td class="${valueClass(line.return1y)}">${percent(line.return1y)}</td><td class="${valueClass(line.return3y)}">${percent(line.return3y)}</td><td class="${valueClass(line.return5y)}">${percent(line.return5y)}</td><td>${number(line.pe)}</td><td>${percentile(line.pePercentile)}</td></tr>
  `).join("");
  renderFactorHoldings(factor, comparison);
  const baseDate = comparison.history[0]?.date || "--";
  $("#factor-chart-value").textContent = state.factorChartMode === "relative"
    ? `${comparisonName} · ${baseDate} = 100，当前 ${number(metrics.currentLevel, 1)}`
    : `${factor.lines.length} 条曲线均以共同起点 ${commonStart} = 100`;
}

function renderFactorHoldings(factor, comparison) {
  const preferredCode = comparison.primaryCode;
  if (!factor.lines.some((line) => line.code === state.selectedFactorSeries)) {
    state.selectedFactorSeries = preferredCode;
  }
  const line = factor.lines.find((entry) => entry.code === state.selectedFactorSeries) || factor.lines[0];
  const controls = $("#factor-series-controls");
  controls.innerHTML = "";
  factor.lines.forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = entry.name;
    button.setAttribute("aria-pressed", String(entry.code === line.code));
    button.addEventListener("click", () => {
      state.selectedFactorSeries = entry.code;
      renderFactorHoldings(factor, comparison);
    });
    controls.appendChild(button);
  });

  const components = Array.isArray(line.components) ? line.components.slice(0, 10) : [];
  const allocation = Array.isArray(line.sectorAllocation)
    ? line.sectorAllocation.filter((entry) => Number.isFinite(entry.weight) && entry.weight > 0)
    : [];
  $("#factor-holdings-title").textContent = `${line.name}持仓结构`;
  $("#factor-holdings-note").textContent = components.length
    ? `权重前 10 · 口径日期 ${line.componentDate || components[0]?.date || "--"} · 行业归属采用申万当前一级行业`
    : "当前没有可核实的官方成分权重。";
  $("#factor-component-body").innerHTML = components.length
    ? components.map((component, index) => `<tr><td>${index + 1}</td><td>${component.code}</td><td>${component.name}</td><td>${Number.isFinite(component.weight) ? `${component.weight.toFixed(4)}%` : "--"}</td><td>${component.date || line.componentDate || "--"}</td></tr>`).join("")
    : '<tr><td class="component-empty" colspan="5">暂无成分权重数据</td></tr>';

  const pie = $("#factor-allocation-pie");
  const legend = $("#factor-allocation-legend");
  if (!allocation.length) {
    pie.style.background = "var(--line-soft)";
    legend.innerHTML = '<span class="allocation-empty">当前没有可核实的行业归属权重。</span>';
    return;
  }
  const total = allocation.reduce((sum, entry) => sum + entry.weight, 0);
  let cursor = 0;
  const stops = allocation.map((entry, index) => {
    const start = cursor / total * 100;
    cursor += entry.weight;
    const end = cursor / total * 100;
    return `${allocationColors[index % allocationColors.length]} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
  });
  pie.style.background = `conic-gradient(${stops.join(", ")})`;
  legend.innerHTML = allocation.map((entry, index) => `
    <div class="allocation-entry" title="${entry.name} ${entry.weight.toFixed(2)}%">
      <i class="allocation-swatch" style="background:${allocationColors[index % allocationColors.length]}"></i>
      <strong>${entry.name}</strong><span>${entry.weight.toFixed(1)}%</span>
    </div>
  `).join("");
}

function normalizedHistory(line, baseDate) {
  const first = line.history.find((point) => point.date >= baseDate && Number.isFinite(point.close));
  if (!first) return [];
  return line.history
    .filter((point) => point.date >= baseDate && Number.isFinite(point.close))
    .map((point) => ({ date: point.date, value: point.close / first.close * 100 }));
}

function renderFactorChart(factor, comparison) {
  const palette = ["#b23b32", "#167457", "#98721f", "#3e6f9d"];
  const commonStart = factor.lines.map((line) => line.coverageStart).filter(Boolean).sort().at(-1);
  const series = state.factorChartMode === "relative"
    ? [{ name: comparison.label, points: comparison.history.map((point) => ({ date: point.date, value: point.level })), color: palette[0] }]
    : factor.lines.map((line, index) => ({ name: line.name, points: normalizedHistory(line, commonStart), color: palette[index % palette.length] }));
  const svg = $("#factor-chart");
  const tooltip = $("#factor-chart-tooltip");
  svg.replaceChildren();
  tooltip.hidden = true;
  const usable = series.filter((line) => line.points.length > 1);
  $("#factor-legend").innerHTML = usable.map((line) => `<span><i style="background:${line.color}"></i>${line.name}</span>`).join("");
  if (!usable.length) return;
  const width = 980, height = 360, margin = { top: 20, right: 20, bottom: 34, left: 55 };
  const innerWidth = width - margin.left - margin.right, innerHeight = height - margin.top - margin.bottom;
  const allPoints = usable.flatMap((line) => line.points);
  const timestamps = allPoints.map((point) => Date.parse(point.date));
  const first = Math.min(...timestamps), last = Math.max(...timestamps);
  const values = allPoints.map((point) => point.value);
  let min = Math.min(...values), max = Math.max(...values);
  const pad = Math.max((max - min) * 0.12, Math.abs(max) * 0.02, 0.1); min -= pad; max += pad;
  const x = (date) => margin.left + (Date.parse(date) - first) / (last - first) * innerWidth;
  const y = (value) => margin.top + (max - value) / (max - min) * innerHeight;
  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value)); svg.appendChild(node); return node; };
  for (let index = 0; index <= 4; index += 1) {
    const gridY = margin.top + index / 4 * innerHeight;
    make("line", { x1: margin.left, y1: gridY, x2: width - margin.right, y2: gridY, stroke: "#e3e7e3", "stroke-width": 1 });
    const label = make("text", { x: margin.left - 9, y: gridY + 4, fill: "#66706a", "font-size": 10, "text-anchor": "end" });
    label.textContent = (max - index / 4 * (max - min)).toFixed(0);
  }
  [0, .25, .5, .75, 1].forEach((ratio) => { const time = first + (last - first) * ratio; const label = make("text", { x: margin.left + innerWidth * ratio, y: height - 10, fill: "#66706a", "font-size": 10, "text-anchor": "middle" }); label.textContent = new Date(time).toISOString().slice(0, 7); });
  usable.forEach((line) => {
    const path = line.points.map((point, index) => `${index ? "L" : "M"}${x(point.date).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
    make("path", { d: path, fill: "none", stroke: line.color, "stroke-width": 2.1, "stroke-linejoin": "round", "stroke-linecap": "round" });
  });
  const cursor = make("line", { x1: 0, y1: margin.top, x2: 0, y2: height - margin.bottom, stroke: "#98721f", "stroke-width": 1, visibility: "hidden" });
  const overlay = make("rect", { x: margin.left, y: margin.top, width: innerWidth, height: innerHeight, fill: "transparent" });
  overlay.addEventListener("pointermove", (event) => {
    const bounds = svg.getBoundingClientRect();
    const localX = (event.clientX - bounds.left) / bounds.width * width;
    const target = first + Math.max(0, Math.min(1, (localX - margin.left) / innerWidth)) * (last - first);
    const reference = usable[0].points;
    let closest = reference[0];
    reference.forEach((point) => { if (Math.abs(Date.parse(point.date) - target) < Math.abs(Date.parse(closest.date) - target)) closest = point; });
    const selectedDate = closest.date;
    const rows = usable.map((line) => {
      const point = line.points.reduce((best, candidate) => Math.abs(Date.parse(candidate.date) - Date.parse(selectedDate)) < Math.abs(Date.parse(best.date) - Date.parse(selectedDate)) ? candidate : best);
      return `<span style="color:${line.color}">${line.name} ${number(point.value, 1)}</span>`;
    });
    const cursorX = x(selectedDate); cursor.setAttribute("x1", cursorX); cursor.setAttribute("x2", cursorX); cursor.setAttribute("visibility", "visible");
    tooltip.innerHTML = `<strong>${selectedDate}</strong><br>${rows.join("<br>")}`; tooltip.hidden = false;
    tooltip.style.left = `${Math.min(bounds.width - 180, Math.max(8, cursorX / width * bounds.width + 10))}px`; tooltip.style.top = "12px";
  });
  overlay.addEventListener("pointerleave", () => { cursor.setAttribute("visibility", "hidden"); tooltip.hidden = true; });
}

function showToast(message, timeout = 4500) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.hidden = true; }, timeout);
}

function enhancedData() {
  return state.dashboard.enhanced;
}

function currentEnhancedGroup() {
  const data = enhancedData();
  if (!data || !Array.isArray(data.groups) || !data.groups.length) return null;
  return data.groups.find((group) => group.indexCode === state.selectedEnhancedIndex) || data.groups[0];
}

function irCell(value) {
  if (!Number.isFinite(value)) return "<td>--</td>";
  const tone = value > 0 ? "positive" : value < 0 ? "negative" : "";
  return `<td class="${tone}">${value.toFixed(2)}</td>`;
}

function starCell(value) {
  if (!Number.isFinite(value)) return "--";
  const n = Math.round(value);
  return "★".repeat(n) + "☆".repeat(Math.max(0, 5 - n));
}

function renderEnhancedRankTable(group) {
  const head = "<tr><th>基金</th><th>晨星评级(5年)</th><th>晨星评级(3年)</th></tr>";
  $("#enhanced-rank-head").innerHTML = head;
  const rows = group.ranked.map((fund) => {
    return `<tr>
      <td class="enhanced-name">${fund.name}<small>${fund.code}${fund.benchmark ? ` · ${fund.benchmark}` : ""}</small></td>
      <td class="enhanced-score" title="晨星五年评级">${starCell(fund.rating?.y5)}</td>
      <td class="enhanced-score" title="晨星三年评级">${starCell(fund.rating?.y3)}</td>
    </tr>`;
  }).join("");
  $("#enhanced-rank-body").innerHTML = rows || '<tr><td colspan="3">暂无具有五年晨星评级的基金</td></tr>';
}

function renderEnhancedWatchTable(group) {
  $("#enhanced-watch-head").innerHTML = "<tr><th>基金</th><th>成立日期</th><th>未入榜原因</th></tr>";
  const watch = [...group.watch].sort((a, b) => (b.inceptionDate || "").localeCompare(a.inceptionDate || ""));
  const rows = watch.map((fund) => {
    return `<tr>
      <td class="enhanced-name">${fund.name}<small>${fund.code}</small></td>
      <td>${fund.inceptionDate || "--"}</td>
      <td style="color:var(--muted)">${fund.reason || "--"}</td>
    </tr>`;
  }).join("");
  $("#enhanced-watch-body").innerHTML = rows || '<tr><td colspan="3">无</td></tr>';
}

function renderEnhancedIndexControls(data) {
  const container = $("#enhanced-index-controls");
  if (!data || data.groups.length <= 1) { container.innerHTML = ""; return; }
  container.innerHTML = data.groups.map((group) =>
    `<button type="button" data-enhanced-index="${group.indexCode}" aria-pressed="${group.indexCode === state.selectedEnhancedIndex}">${group.indexName}</button>`
  ).join("");
  $$("[data-enhanced-index]").forEach((button) => button.addEventListener("click", () => {
    state.selectedEnhancedIndex = button.dataset.enhancedIndex;
    renderEnhancedDashboard();
  }));
}

function renderEnhancedDashboard() {
  const data = enhancedData();
  $("#standard-dashboard").hidden = true;
  $("#factor-dashboard").hidden = true;
  $("#enhanced-dashboard").hidden = false;
  $$("[data-view]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.view === "enhanced")));
  $("#as-of").textContent = data?.asOf || "--";
  $("#footer-source").textContent = `数据源：${data?.source || "--"}`;
  $("#enhanced-asof").textContent = data?.asOf || "--";
  $("#enhanced-source").textContent = `数据源 · ${data?.source || "--"}`;
  $("#enhanced-methodology").textContent = data?.scoreNote || "--";
  if (!data || !Array.isArray(data.groups) || !data.groups.length) {
    $("#enhanced-title").textContent = "指数增强数据尚未生成";
    $("#enhanced-rank-body").innerHTML = "";
    $("#enhanced-watch-body").innerHTML = "";
    return;
  }
  if (!state.selectedEnhancedIndex) state.selectedEnhancedIndex = data.groups[0].indexCode;
  const group = currentEnhancedGroup();
  $("#enhanced-title").textContent = `${group.indexName} · 指数增强评价`;
  $("#enhanced-rank-note").textContent = `${group.indexName}共 ${group.ranked.length} 只具有五年晨星评级 · ${data.benchmark || "五年评级优先"}`;
  renderEnhancedIndexControls(data);
  renderEnhancedRankTable(group);
  renderEnhancedWatchTable(group);
}

async function loadData() {
  const response = await fetch(`../data/dashboard.json?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error("无法读取静态看板数据，请等待下一次自动构建");
  state.dashboard = await response.json();
  if (state.viewId === "factor") renderFactorDashboard();
  else if (state.viewId === "enhanced") renderEnhancedDashboard();
  else renderAll();
}

$$("[data-view]").forEach((button) => button.addEventListener("click", () => {
  state.viewId = button.dataset.view;
  if (state.viewId === "factor") {
    renderFactorDashboard();
    return;
  }
  if (state.viewId === "enhanced") {
    renderEnhancedDashboard();
    return;
  }
  state.query = "";
  $("#search-input").value = "";
  state.chartMetric = "close";
  renderAll();
}));
$("#search-input").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderRanking();
});
loadData().catch((error) => {
  $("#view-title").textContent = "数据读取失败";
  $("#view-description").textContent = error.message;
});
