/* DCSJ workbench. No external runtime or network assets required. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    schema: { groups: [], fields: [] },
    status: null,
    parameters: {},
    name: "",
    model: "",
    source: null,
    group: "",
    result: null,
    revision: 0,
    resultRevision: -1,
    invalidInputs: new Set(),
    catalog: { offset: 0, limit: 30, total: 0, loaded: false, request: 0 },
    materialsLoaded: false,
    materialRequest: 0,
    busy: false,
    dirty: false,
  };
  const viewNames = { workbench: "设计工作台", catalog: "电机型号库", materials: "材料与磁化曲线", projects: "我的设计", about: "分析与实现状态" };
  const colors = ["#3a8d70", "#668fc0", "#d59b55", "#a37bad", "#6aa9ad", "#bd7771"];
  let toastTimer;
  let modelRequest = 0;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function button(text, className, handler) {
    const node = el("button", className, text);
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  }
  function fmt(value, digits = 5) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value !== "number") return String(value);
    if (!Number.isFinite(value)) return "无有效数值";
    if (value !== 0 && (Math.abs(value) < 0.0001 || Math.abs(value) >= 1e8)) return value.toExponential(3);
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
  }
  function asText(value) {
    return typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "");
  }
  function sourceText(source) {
    if (!source) return "未提供来源";
    if (typeof source === "string") return source;
    const parts = [];
    if (source.path) parts.push(source.path);
    if (source.table) parts.push(`表：${source.table}`);
    if (source.row !== undefined && source.row !== null) parts.push(`记录：${source.row}`);
    if (source.description) parts.push(source.description);
    return parts.join(" · ") || asText(source);
  }
  function showToast(message, level = "success") {
    clearTimeout(toastTimer);
    $("#toast").textContent = message;
    $("#toast").className = `toast${level === "success" ? "" : ` ${level}`}`;
    toastTimer = setTimeout(() => $("#toast").classList.add("hidden"), level === "error" ? 10000 : 5000);
  }
  function showNotice(message) {
    $("#global-notice").textContent = message;
    $("#global-notice").classList.toggle("hidden", !message);
  }
  function errorText(error) {
    return error instanceof Error ? error.message : asText(error);
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch(`/api/v1${path}`, {
        ...options,
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
        signal: controller.signal,
      });
      const text = await response.text();
      let body;
      try { body = text ? JSON.parse(text) : {}; }
      catch { throw new Error(`服务返回的内容不是有效 JSON（HTTP ${response.status}）。请检查后端服务。`); }
      if (!response.ok) {
        const detail = body.detail ?? body.message ?? body.error ?? `HTTP ${response.status}`;
        if (Array.isArray(detail)) {
          throw new Error(detail.map(item => {
            if (typeof item === "string") return item;
            const location = item.field || (Array.isArray(item.loc) ? item.loc.filter(p => p !== "body").join(".") : "参数");
            return `${location}：${item.msg ?? item.message ?? asText(item)}`;
          }).join("\n"));
        }
        throw new Error(asText(detail));
      }
      return body;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超过 60 秒未完成，请检查服务后重试。");
      if (error instanceof TypeError) throw new Error("无法连接后端服务。请确认服务已启动，并通过它打开工作台。");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function navigate(view, updateHash = true) {
    if (!Object.hasOwn(viewNames, view)) view = "workbench";
    $$(".page").forEach(page => page.classList.toggle("active", page.id === `view-${view}`));
    $$(".nav-item[data-view]").forEach(item => {
      const active = item.dataset.view === view;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    $("#breadcrumb-current").textContent = viewNames[view];
    document.title = `${viewNames[view]} · DCSJ`;
    if (updateHash && location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
    if (view === "catalog" && !state.catalog.loaded) loadCatalog();
    if (view === "materials" && !state.materialsLoaded) loadMaterials();
    if (view === "projects") loadProjects();
  }
  function populated() { return Object.keys(state.parameters).length > 0; }
  function setDesignState(text, tone = "") {
    $("#design-state").replaceChildren(el("span", `status-dot ${tone}`), document.createTextNode(text));
  }
  function designChanged() {
    state.revision += 1;
    state.dirty = true;
    $("#raw-parameters").textContent = JSON.stringify(state.parameters, null, 2);
    $("#validation-errors").classList.add("hidden");
    setDesignState("参数已修改", "teal");
    if (state.result) markResultStale();
  }
  function markResultStale() {
    $("#result-badge").className = "badge warning";
    $("#result-badge").textContent = "参数已变更";
    if (!$("#stale-result")) {
      const note = el("div", "stale-note", "参数已修改。下方是上一次计算的结果，请重新运行计算。");
      note.id = "stale-result";
      $("#result-content").prepend(note);
    }
  }
  function validParameters(parameters) {
    if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) throw new Error("设计中的 parameters 必须是参数对象。");
    const entries = Object.entries(parameters);
    if (!entries.length) throw new Error("设计参数为空，无法载入。");
    for (const [key, value] of entries) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") throw new Error("设计文件包含不支持的字段名。");
      if (typeof value === "number" && !Number.isFinite(value)) throw new Error(`参数 ${key} 不是有限数值。`);
      if (value !== null && !["string", "number", "boolean"].includes(typeof value)) throw new Error(`参数 ${key} 必须是数字、文本、布尔值或空值。`);
    }
    return Object.fromEntries(entries);
  }
  function acceptDesign(parameters, name, source, model = "") {
    state.parameters = validParameters(parameters);
    state.name = String(name || "未命名设计");
    state.model = String(model || parameters.SizeX || parameters.Model || parameters.model || "");
    state.source = source || null;
    state.revision += 1;
    state.result = null;
    state.resultRevision = -1;
    state.invalidInputs.clear();
    state.group = "";
    state.dirty = false;
    $("#current-design").textContent = state.name;
    $("#current-design").title = state.name;
    $("#current-model").textContent = state.model || "自定义参数";
    $("#parameter-source").textContent = sourceText(state.source);
    $("#parameter-source").title = sourceText(state.source);
    $("#calculate-button").disabled = state.busy;
    setDesignState("参数已载入", "teal");
    $("#validation-errors").classList.add("hidden");
    $("#parameter-search").value = "";
    renderEditor();
    resetResults();
    navigate("workbench");
  }
  function allFields() {
    const defined = new Set(state.schema.fields.map(field => field.key));
    const extra = Object.keys(state.parameters).filter(key => !defined.has(key)).map(key => ({
      key, label: key, group: "_unknown", type: typeof state.parameters[key] === "number" ? "number" : "text",
      role: "unknown", description: "原始数据字段，含义尚未确认；已完整保留。",
    }));
    return [...state.schema.fields, ...extra];
  }
  function effectiveGroups(fields) {
    const groups = state.schema.groups.map(group => ({ ...group }));
    const ids = new Set(groups.map(group => group.id));
    fields.forEach(field => {
      const id = field.group || "_general";
      if (!ids.has(id)) {
        groups.push({ id, label: id === "_unknown" ? "未识别字段" : id === "_general" ? "其他参数" : id });
        ids.add(id);
      }
    });
    return groups.filter(group => fields.some(field => (field.group || "_general") === group.id));
  }
  function renderEditor() {
    const fields = allFields();
    const groups = effectiveGroups(fields);
    if (!groups.some(group => group.id === state.group)) state.group = groups[0]?.id || "";
    const tabs = $("#parameter-tabs");
    tabs.replaceChildren();
    groups.forEach(group => {
      const count = fields.filter(field => (field.group || "_general") === group.id).length;
      const tab = button(group.label || group.id, `parameter-tab${state.group === group.id ? " active" : ""}`, () => {
        state.group = group.id;
        $("#parameter-search").value = "";
        renderEditor();
      });
      tab.setAttribute("aria-pressed", String(state.group === group.id));
      tab.append(el("span", "", count));
      tabs.append(tab);
    });
    $("#parameter-count").textContent = `${Object.keys(state.parameters).length} 个已载入字段`;
    $("#raw-parameters").textContent = JSON.stringify(state.parameters, null, 2);
    renderFields();
  }
  function renderFields() {
    const container = $("#parameter-fields");
    container.replaceChildren();
    if (!populated()) {
      const empty = el("div", "empty-state");
      empty.append(el("span", "empty-symbol", "▦"), el("h3", "", "载入设计参数"), el("p", "", "选择一个原始型号，或载入可运行的示例。"));
      container.append(empty);
      return;
    }
    const search = $("#parameter-search").value.trim().toLocaleLowerCase();
    const fields = allFields().filter(field => search
      ? [field.key, field.label, field.description].some(value => String(value || "").toLocaleLowerCase().includes(search))
      : (field.group || "_general") === state.group);
    if (!fields.length) {
      container.append(el("p", "group-description", "没有匹配的参数。请尝试其他名称或符号。"));
      return;
    }
    if (fields.some(field => field.role === "unknown")) container.append(el("p", "group-description warning", "部分原始字段的含义尚未确认；修改前请核对原设计资料。"));
    if (fields.some(field => field.role === "output")) container.append(el("p", "group-description", "标记为「历史输出」的字段来自原始数据，仅供核对，不是本次计算结果。"));
    fields.forEach((field, index) => {
      const card = el("div", "field");
      const label = el("label", "field-label");
      const id = `parameter-${index}`;
      label.htmlFor = id;
      label.append(el("span", "", field.label || field.key), el("span", "field-symbol", field.key));
      const wrap = el("div", "input-wrap");
      const value = state.parameters[field.key];
      const input = el(field.type === "select" && field.role !== "output" ? "select" : "input");
      input.id = id;
      input.dataset.key = field.key;
      if (input.tagName === "SELECT") {
        const options = Array.isArray(field.options) ? field.options : [];
        const blank = el("option", "", "未设置");
        blank.value = "";
        input.append(blank);
        options.forEach(option => {
          const node = el("option", "", option.label ?? option.value);
          node.value = String(option.value);
          input.append(node);
        });
        if (value !== null && value !== undefined && !options.some(option => String(option.value) === String(value))) {
          const original = el("option", "", `${value}（原始值）`);
          original.value = String(value);
          input.append(original);
        }
      } else {
        input.type = field.type === "number" || field.type === "integer" ? "number" : "text";
        if (input.type === "number") {
          input.step = field.type === "integer" ? "1" : "any";
          if (field.min !== undefined && field.min !== null) input.min = field.type === "integer" ? Math.ceil(Number(field.min)) : field.min;
          if (field.max !== undefined && field.max !== null) input.max = field.type === "integer" ? Math.floor(Number(field.max)) : field.max;
        }
        input.readOnly = field.role === "output";
        if (field.role === "output") input.setAttribute("aria-label", `${field.label || field.key}，历史输出，只读`);
      }
      input.value = value ?? "";
      if (state.invalidInputs.has(field.key)) {
        wrap.classList.add("invalid");
        input.setAttribute("aria-invalid", "true");
      }
      input.placeholder = field.default !== undefined ? `默认参考 ${field.default}` : "未设置";
      const hintId = `hint-${index}`;
      input.setAttribute("aria-describedby", hintId);
      const hintParts = [];
      if (field.role === "output") hintParts.push("历史输出 · 只读");
      if (field.description) hintParts.push(field.description);
      if (field.min !== undefined && field.min !== null) hintParts.push(`≥ ${field.min}`);
      if (field.max !== undefined && field.max !== null) hintParts.push(`≤ ${field.max}`);
      const hint = el("p", `field-hint${field.role === "unknown" ? " field-warning" : ""}`, hintParts.join(" · "));
      hint.id = hintId;
      input.addEventListener("input", () => {
        const newValue = input.value;
        if (input.type === "number") {
          state.parameters[field.key] = newValue === "" ? null : Number(newValue);
        } else if (input.tagName === "SELECT") {
          const option = (field.options || []).find(item => String(item.value) === newValue);
          state.parameters[field.key] = newValue === "" ? null : option ? option.value : newValue;
        } else state.parameters[field.key] = newValue;
        const valid = input.checkValidity();
        if (valid) state.invalidInputs.delete(field.key);
        else state.invalidInputs.add(field.key);
        wrap.classList.toggle("invalid", !valid);
        input.setAttribute("aria-invalid", String(!valid));
        designChanged();
      });
      wrap.append(input);
      if (field.unit) wrap.append(el("span", "unit", field.unit));
      card.append(label, wrap, hint);
      container.append(card);
    });
  }
  function validateInputs() {
    const errors = [];
    allFields().forEach(field => {
      if (field.role === "output") return;
      const value = state.parameters[field.key];
      if (value === "" || value === null || value === undefined) return;
      if (field.type === "number" || field.type === "integer") {
        const number = Number(value);
        if (!Number.isFinite(number)) errors.push(`${field.label || field.key}（${field.key}）必须是有效数字。`);
        else if (field.type === "integer" && !Number.isInteger(number)) errors.push(`${field.label || field.key}（${field.key}）必须是整数。`);
        else if (field.min !== undefined && field.min !== null && number < field.min) errors.push(`${field.label || field.key}（${field.key}）不得小于 ${field.min}。`);
        else if (field.max !== undefined && field.max !== null && number > field.max) errors.push(`${field.label || field.key}（${field.key}）不得大于 ${field.max}。`);
      }
    });
    $$("input", $("#parameter-fields")).forEach(input => {
      if (input.readOnly) return;
      const valid = input.checkValidity();
      input.closest(".input-wrap").classList.toggle("invalid", !valid);
      input.setAttribute("aria-invalid", String(!valid));
      if (!valid && !errors.some(error => error.includes(`（${input.dataset.key}）`))) errors.push(`${input.dataset.key} 的输入格式无效。`);
    });
    state.invalidInputs.forEach(key => {
      if (!errors.some(error => error.includes(`（${key}）`) || error.startsWith(`${key} `))) errors.push(`${key} 的输入格式无效，请在对应参数分组中修正。`);
    });
    $("#validation-errors").textContent = errors.join("\n");
    $("#validation-errors").classList.toggle("hidden", !errors.length);
    if (errors.length) $("#validation-errors").scrollIntoView({ behavior: "smooth", block: "nearest" });
    return !errors.length;
  }
  function resetResults() {
    $("#result-badge").className = "badge neutral";
    $("#result-badge").textContent = "等待计算";
    const empty = el("div", "empty-state results-empty");
    const chart = el("div", "empty-chart");
    chart.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 5; index++) chart.append(el("i"));
    empty.append(chart, el("h3", "", "准备好后，运行一次计算"), el("p", "", "这里将展示新引擎计算的性能指标、曲线和计算过程。"), el("span", "empty-footnote", "历史缓存不会自动替代本次结果"));
    $("#result-content").replaceChildren(empty);
  }
  function warningsNode(warnings, title = "计算提示与适用限制") {
    if (!Array.isArray(warnings) || !warnings.length) return null;
    const box = el("div", "warning-list");
    box.append(el("strong", "", title));
    const list = el("ul");
    warnings.forEach(warning => list.append(el("li", "", asText(warning))));
    box.append(list);
    return box;
  }
  function renderResults(result) {
    const container = $("#result-content");
    container.replaceChildren();
    const metrics = Array.isArray(result.metrics) ? result.metrics : [];
    const curves = Array.isArray(result.curves) ? result.curves : [];
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    const meta = el("div", "result-meta");
    meta.append(el("span", "", `引擎：${asText(result.engine?.name || result.engine || "未提供")}`), el("span", "", `本次计算 · ${new Date().toLocaleTimeString("zh-CN")}`));
    container.append(meta);
    const warningBox = warningsNode(warnings);
    if (warningBox) container.append(warningBox);
    if (metrics.length) {
      const cards = el("div", "metric-grid");
      metrics.slice(0, 6).forEach(metric => {
        const card = el("div", "metric-card");
        card.append(el("span", "metric-label", metric.label || metric.key));
        const value = el("div", "metric-value", fmt(metric.value, typeof metric.value === "number" && Math.abs(metric.value) < 1 ? 5 : 3));
        if (metric.unit) value.append(el("span", "metric-unit", metric.unit));
        card.append(value);
        cards.append(card);
      });
      container.append(cards);
    }
    curves.forEach(curve => container.append(renderCurve(curve)));
    if (metrics.length) {
      container.append(el("h3", "result-section-title", "详细计算指标"));
      const scroll = el("div", "table-scroll");
      const table = el("table", "result-table");
      const head = el("thead");
      const heading = el("tr");
      ["指标", "符号", "结果", "单位"].forEach(text => heading.append(el("th", "", text)));
      head.append(heading);
      const body = el("tbody");
      metrics.forEach(metric => {
        const row = el("tr");
        [metric.label || metric.key, metric.key, fmt(metric.value), metric.unit || "—"].forEach(value => row.append(el("td", "", value)));
        body.append(row);
      });
      table.append(head, body);
      scroll.append(table);
      container.append(scroll);
    }
    if (!metrics.length && !curves.length) container.append(el("p", "group-description warning", "接口没有返回可展示的计算指标或曲线，请检查计算提示及引擎状态。"));
    if (Array.isArray(result.trace) && result.trace.length) {
      const trace = el("details", "trace-details");
      trace.append(el("summary", "", `计算过程与依据 · ${result.trace.length} 项`));
      const list = el("ol");
      result.trace.forEach(item => list.append(el("li", "", asText(item))));
      trace.append(list);
      container.append(trace);
    }
    $("#result-badge").className = `badge ${warnings.length ? "warning" : metrics.length || curves.length ? "success" : "neutral"}`;
    $("#result-badge").textContent = warnings.length ? "完成 · 请核对提示" : metrics.length || curves.length ? "计算完成" : "未返回结果";
    setDesignState("计算完成", "teal");
  }
  function svgNode(tag, attrs = {}, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function renderCurve(curve) {
    const card = el("div", "chart-card");
    card.append(el("h3", "chart-title", curve.title || "数据曲线"));
    const rawSeries = Array.isArray(curve.series) ? curve.series : [];
    let invalidCount = 0;
    const series = rawSeries.map((item, index) => ({
      name: String(item.name || `曲线 ${index + 1}`),
      color: colors[index % colors.length],
      points: (Array.isArray(item.points) ? item.points : []).filter(point => {
        const valid = point !== null && typeof point === "object" && typeof point.x === "number" && typeof point.y === "number" && Number.isFinite(point.x) && Number.isFinite(point.y);
        if (!valid) invalidCount++;
        return valid;
      }),
    }));
    const points = series.flatMap(item => item.points);
    if (!points.length) {
      card.append(el("p", "chart-empty", "没有可绘制的有效坐标数据。"));
      return card;
    }
    const legend = el("div", "chart-legend");
    series.forEach(item => {
      const label = el("span", "legend-item");
      const dot = el("i", "legend-dot");
      dot.style.backgroundColor = item.color;
      label.append(dot, document.createTextNode(item.name));
      legend.append(label);
    });
    card.append(legend);
    if (invalidCount) card.append(el("p", "chart-warning", `${invalidCount} 个非数值坐标无法绘制，已从曲线中跳过。`));
    let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
    points.forEach(point => { xMin = Math.min(xMin, point.x); xMax = Math.max(xMax, point.x); yMin = Math.min(yMin, point.y); yMax = Math.max(yMax, point.y); });
    if (xMin === xMax) { const pad = Math.abs(xMin) * .1 || 1; xMin -= pad; xMax += pad; }
    if (yMin === yMax) { const pad = Math.abs(yMin) * .1 || 1; yMin -= pad; yMax += pad; }
    const yPad = (yMax - yMin) * .07;
    yMin = yMin >= 0 ? Math.max(0, yMin - yPad) : yMin - yPad;
    yMax += yPad;
    const width = 540, height = 300, left = 67, right = 22, top = 34, bottom = 54;
    const plotW = width - left - right, plotH = height - top - bottom;
    const px = x => left + (x - xMin) / (xMax - xMin) * plotW;
    const py = y => top + (yMax - y) / (yMax - yMin) * plotH;
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, class: "curve-svg", role: "img", "aria-label": `${curve.title || "数据曲线"}，横轴 ${curve.x_label || "x"}，纵轴 ${curve.y_label || "y"}` });
    svg.append(svgNode("title", {}, `${curve.title || "数据曲线"}；详细数值见下方数据表`));
    for (let tick = 0; tick <= 4; tick++) {
      const y = top + plotH * tick / 4;
      svg.append(svgNode("line", { x1: left, x2: width - right, y1: y, y2: y, stroke: "#e8eeea", "stroke-dasharray": "3 4", "stroke-width": 1 }));
      svg.append(svgNode("text", { x: left - 9, y: y + 3, "text-anchor": "end", fill: "#96a69d", "font-size": 9 }, fmt(yMax - (yMax - yMin) * tick / 4, 3)));
      const x = left + plotW * tick / 4;
      svg.append(svgNode("text", { x, y: height - bottom + 20, "text-anchor": "middle", fill: "#96a69d", "font-size": 9 }, fmt(xMin + (xMax - xMin) * tick / 4, 3)));
    }
    svg.append(svgNode("line", { x1: left, x2: width - right, y1: height - bottom, y2: height - bottom, stroke: "#ccd9d0" }));
    svg.append(svgNode("line", { x1: left, x2: left, y1: top, y2: height - bottom, stroke: "#ccd9d0" }));
    svg.append(svgNode("text", { x: left, y: 15, fill: "#7c9486", "font-size": 10 }, curve.y_label || "y"));
    svg.append(svgNode("text", { x: left + plotW / 2, y: height - 8, fill: "#7c9486", "font-size": 10, "text-anchor": "middle" }, curve.x_label || "x"));
    series.forEach(item => {
      if (!item.points.length) return;
      const path = item.points.map((point, index) => `${index ? "L" : "M"}${px(point.x).toFixed(2)},${py(point.y).toFixed(2)}`).join(" ");
      svg.append(svgNode("path", { d: path, fill: "none", stroke: item.color, "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const stride = Math.max(1, Math.ceil(item.points.length / 60));
      item.points.forEach((point, index) => {
        if (index % stride && index !== item.points.length - 1) return;
        const marker = svgNode("circle", { cx: px(point.x), cy: py(point.y), r: item.points.length === 1 ? 3 : 2, fill: "#fff", stroke: item.color, "stroke-width": 1.4 });
        marker.append(svgNode("title", {}, `${item.name}：${curve.x_label || "x"} ${fmt(point.x)}，${curve.y_label || "y"} ${fmt(point.y)}`));
        svg.append(marker);
      });
    });
    card.append(svg);
    const details = el("details", "chart-data");
    details.append(el("summary", "", `查看曲线数据 · ${points.length} 个有效坐标`));
    const scroll = el("div", "table-scroll");
    const table = el("table", "result-table");
    const head = el("thead"), header = el("tr");
    ["曲线", curve.x_label || "x", curve.y_label || "y"].forEach(label => header.append(el("th", "", label)));
    head.append(header);
    const body = el("tbody");
    series.forEach(item => item.points.forEach(point => {
      const row = el("tr");
      [item.name, fmt(point.x), fmt(point.y)].forEach(value => row.append(el("td", "", value)));
      body.append(row);
    }));
    table.append(head, body);
    scroll.append(table);
    details.append(scroll);
    card.append(details);
    return card;
  }
  async function calculate(event) {
    event.preventDefault();
    if (state.busy || !populated() || !validateInputs()) return;
    const revision = state.revision;
    ++modelRequest;
    state.busy = true;
    $("#calculate-button").disabled = true;
    $("#calculate-button").textContent = "计算中…";
    $("#result-badge").textContent = "计算中";
    $("#result-badge").className = "badge neutral";
    try {
      const result = await api("/calculate", { method: "POST", body: JSON.stringify({ parameters: state.parameters }) });
      if (!result || !Array.isArray(result.metrics)) throw new Error("计算接口没有返回约定的 metrics 数组，请检查后端接口。");
      state.result = result;
      state.resultRevision = revision;
      renderResults(result);
      if (revision !== state.revision) { markResultStale(); setDesignState("参数已修改", "teal"); }
      else showToast("计算完成。请结合计算提示检查结果。");
    } catch (error) {
      $("#result-badge").textContent = "计算失败";
      $("#result-badge").className = "badge error";
      $("#validation-errors").textContent = errorText(error);
      $("#validation-errors").classList.remove("hidden");
      setDesignState("计算失败", "error");
      if (state.result) markResultStale();
      showToast(errorText(error), "error");
    } finally {
      state.busy = false;
      $("#calculate-button").disabled = !populated();
      $("#calculate-button").textContent = "▷ 运行计算";
    }
  }
  async function loadExample() {
    const trigger = $("#load-example");
    trigger.disabled = true;
    trigger.textContent = "载入中…";
    const request = ++modelRequest;
    try {
      const example = await api("/example");
      if (request !== modelRequest) return;
      acceptDesign(example.parameters, example.name || "示例设计", example.source || "服务内置示例参数", example.model || "");
      showToast("已载入示例参数，可修改后运行计算。");
    } catch (error) { showToast(errorText(error), "error"); }
    finally { trigger.disabled = false; trigger.textContent = "载入示例"; }
  }
  async function loadStatus() {
    try {
      const status = await api("/status");
      state.status = status;
      $("#connection-dot").className = "status-dot teal";
      $("#connection-text").textContent = "服务已连接";
      $("#sidebar-version").textContent = `${status.name || "DCSJ"} · ${status.version || ""}`;
      const engineName = status.engine?.name || "未提供";
      $("#current-engine").textContent = engineName;
      $("#current-engine").title = engineName;
      if (status.catalog?.model_count !== undefined) $("#catalog-total").textContent = fmt(status.catalog.model_count, 0);
      if (status.catalog?.material_count !== undefined) $("#material-total").textContent = fmt(status.catalog.material_count, 0);
      const limitations = Array.isArray(status.engine?.limitations) ? status.engine.limitations : [];
      $("#engine-note p").textContent = limitations.length ? `${limitations.map(item => asText(item).replace(/[。；;\s]+$/u, "")).join("；")}。` : `当前引擎：${engineName}。详细能力以接口返回值与计算提示为准。`;
      renderStatus(status);
      showNotice(status.engine?.status === "unavailable" ? "当前为源码运行模式：尚未安装私有计算资源。可导入和管理设计，实际计算需要在本机安装授权资源后重启服务。" : "");
    } catch (error) {
      $("#connection-dot").className = "status-dot error";
      $("#connection-text").textContent = "服务连接失败";
      $("#current-engine").textContent = "服务未连接";
      $("#engine-note p").textContent = "无法读取计算引擎状态，请检查后端服务后刷新。";
      $("#status-detail").replaceChildren(el("p", "inline-error", errorText(error)));
      showNotice(errorText(error));
    }
  }
  function renderStatus(status) {
    const container = $("#status-detail");
    const list = el("dl", "definition-list");
    const entries = [
      ["服务名称", status.name], ["版本", status.version], ["引擎", status.engine?.name], ["引擎状态", status.engine?.status],
      ["原始型号", fmt(status.catalog?.model_count, 0)], ["材料数量", fmt(status.catalog?.material_count, 0)],
    ];
    entries.forEach(([key, value]) => list.append(el("dt", "", key), el("dd", "", asText(value ?? "未提供"))));
    container.replaceChildren(list, el("h3", "", "已声明的限制"));
    const warnings = warningsNode(status.engine?.limitations, "请在这些边界内使用计算结果");
    container.append(warnings || el("p", "muted small", "接口未声明限制。请同时检查每次计算返回的提示。"));
  }
  async function loadCatalog() {
    const request = ++state.catalog.request;
    const { offset, limit } = state.catalog;
    const params = new URLSearchParams({ q: $("#model-search").value.trim(), series: $("#series-filter").value, limit, offset });
    $("#catalog-feedback").textContent = "正在读取型号…";
    $("#model-table-body").replaceChildren();
    $("#page-previous").disabled = true;
    $("#page-next").disabled = true;
    try {
      const data = await api(`/models?${params}`);
      if (request !== state.catalog.request) return;
      if (!Array.isArray(data.items)) throw new Error("型号接口没有返回 items 数组。");
      state.catalog.total = Number(data.total) || 0;
      state.catalog.loaded = true;
      $("#catalog-total").textContent = fmt(state.catalog.total, 0);
      $("#catalog-feedback").textContent = data.items.length ? "" : "没有符合条件的型号，请调整关键词或系列。";
      data.items.forEach(item => {
        const row = el("tr");
        [item.model || item.id, item.series || "—", fmt(item.rated_power_kw), fmt(item.poles, 0), fmt(item.voltage_v), fmt(item.frequency_hz)].forEach(value => row.append(el("td", "", value)));
        const source = el("td", "source-cell", sourceText(item.source));
        source.title = sourceText(item.source);
        row.append(source);
        const action = el("td");
        const trigger = button("载入设计 →", "text-button", () => loadModel(item.id, trigger));
        trigger.setAttribute("aria-label", `载入型号 ${item.model || item.id}`);
        action.append(trigger);
        row.append(action);
        $("#model-table-body").append(row);
      });
      const end = Math.min(offset + data.items.length, state.catalog.total);
      $("#page-description").textContent = state.catalog.total ? `显示 ${offset + 1}–${end} / 共 ${fmt(state.catalog.total, 0)} 个型号` : "共 0 个型号";
      $("#page-number").textContent = state.catalog.total ? `${Math.floor(offset / limit) + 1} / ${Math.ceil(state.catalog.total / limit)}` : "0 / 0";
      $("#page-previous").disabled = offset === 0;
      $("#page-next").disabled = offset + limit >= state.catalog.total;
    } catch (error) {
      if (request !== state.catalog.request) return;
      $("#catalog-feedback").textContent = `读取失败：${errorText(error)}`;
      $("#page-description").textContent = "读取失败，可重新检索";
      state.catalog.loaded = false;
    }
  }
  async function loadModel(id, trigger) {
    if (state.busy) return showToast("计算正在进行，请在完成后载入其他设计。", "warning");
    trigger.disabled = true;
    trigger.textContent = "载入中…";
    const request = ++modelRequest;
    try {
      const model = await api(`/models/${encodeURIComponent(id)}`);
      if (request !== modelRequest) return;
      acceptDesign(model.parameters, model.model || String(id), model.source, model.model || "");
      showToast(`已载入 ${model.model || id}，来源信息显示在参数区。`);
    } catch (error) { showToast(errorText(error), "error"); }
    finally { trigger.disabled = false; trigger.textContent = "载入设计 →"; }
  }
  async function loadSeries() {
    try {
      const data = await api("/series");
      if (!Array.isArray(data.items)) throw new Error("系列接口没有返回 items 数组。");
      data.items.forEach(item => {
        const option = el("option", "", item);
        option.value = item;
        $("#series-filter").append(option);
      });
    } catch (error) {
      showToast(`系列筛选暂不可用：${errorText(error)}`, "error");
      $("#series-filter").disabled = true;
    }
  }
  async function loadMaterials() {
    $("#material-list").replaceChildren(el("p", "loading-copy", "正在读取材料…"));
    try {
      const data = await api("/materials");
      if (!Array.isArray(data.items)) throw new Error("材料接口没有返回 items 数组。");
      state.materialsLoaded = true;
      $("#material-total").textContent = fmt(data.items.length, 0);
      $("#material-list").replaceChildren();
      data.items.forEach(material => {
        const trigger = button("", "material-item", () => loadMaterial(material.id, trigger, material.source));
        trigger.append(el("strong", "", material.name || material.id), el("small", "", `${fmt(material.point_count, 0)} 个数据点`));
        trigger.title = sourceText(material.source);
        $("#material-list").append(trigger);
      });
      if (!data.items.length) $("#material-list").append(el("p", "loading-copy", "当前没有材料数据。"));
    } catch (error) {
      $("#material-list").replaceChildren(el("p", "inline-error", `读取失败：${errorText(error)}`));
      state.materialsLoaded = false;
    }
  }
  async function loadMaterial(id, trigger, listSource) {
    const request = ++state.materialRequest;
    $$(".material-item").forEach(item => {
      item.classList.toggle("active", item === trigger);
      item.setAttribute("aria-pressed", String(item === trigger));
    });
    $("#material-detail").replaceChildren(el("p", "loading-copy", "正在读取材料曲线…"));
    try {
      const material = await api(`/materials/${encodeURIComponent(id)}`);
      if (request !== state.materialRequest) return;
      const header = el("div", "material-detail-header");
      header.append(el("div", "eyebrow", "MAGNETIZATION DATA"), el("h2", "", material.name || id), el("p", "", `来源：${sourceText(material.source || listSource)}`));
      const content = el("div", "material-curves");
      const warningBox = warningsNode(material.warnings, "材料数据说明");
      if (warningBox) content.append(warningBox);
      if (Array.isArray(material.curves) && material.curves.length) material.curves.forEach(curve => content.append(renderCurve(curve)));
      else content.append(el("p", "chart-empty", "此材料没有可绘制的曲线数据。"));
      $("#material-detail").replaceChildren(header, content);
    } catch (error) {
      if (request === state.materialRequest) $("#material-detail").replaceChildren(el("p", "inline-error", errorText(error)));
    }
  }
  async function loadProjects() {
    $("#projects-feedback").textContent = "正在读取已保存设计…";
    $("#project-table-body").replaceChildren();
    try {
      const data = await api("/projects");
      if (!Array.isArray(data.items)) throw new Error("设计接口没有返回 items 数组。");
      $("#projects-feedback").textContent = data.items.length ? "" : "还没有保存的设计。在工作台中载入参数后，点击「保存设计」。";
      data.items.forEach(project => {
        const row = el("tr");
        const date = new Date(project.updated_at);
        [project.name, project.id, Number.isNaN(date.getTime()) ? project.updated_at || "—" : date.toLocaleString("zh-CN")].forEach(value => row.append(el("td", "", value)));
        const action = el("td");
        const trigger = button("打开设计 →", "text-button", async () => {
          if (state.busy) return showToast("计算正在进行，请在完成后打开其他设计。", "warning");
          trigger.disabled = true;
          const request = ++modelRequest;
          try {
            const design = await api(`/projects/${encodeURIComponent(project.id)}`);
            if (request !== modelRequest) return;
            acceptDesign(design.parameters, design.name, design.source || `已保存设计 · ${design.id}`, design.model || "");
            showToast(`已打开设计：${design.name}`);
          } catch (error) { showToast(errorText(error), "error"); }
          finally { trigger.disabled = false; }
        });
        action.append(trigger);
        row.append(action);
        $("#project-table-body").append(row);
      });
    } catch (error) { $("#projects-feedback").textContent = `读取失败：${errorText(error)}`; }
  }
  function openSaveDialog() {
    if (!populated()) return showToast("请先载入设计参数。", "warning");
    $("#design-name").value = state.name;
    $("#save-error").classList.add("hidden");
    $("#save-dialog").showModal();
    $("#design-name").focus();
  }
  async function saveProject(event) {
    event.preventDefault();
    const name = $("#design-name").value.trim();
    if (!name) { $("#design-name").setCustomValidity("请输入设计名称"); $("#design-name").reportValidity(); return; }
    $("#confirm-save").disabled = true;
    $("#confirm-save").textContent = "保存中…";
    const revision = state.revision;
    try {
      const saved = await api("/projects", { method: "POST", body: JSON.stringify({ name, parameters: state.parameters }) });
      if (!saved.id) throw new Error("服务未返回设计 ID，无法确认是否保存成功。");
      if (revision === state.revision) {
        state.name = saved.name || name;
        state.dirty = false;
        $("#current-design").textContent = state.name;
        $("#current-design").title = state.name;
      }
      $("#save-dialog").close();
      showToast(`设计已保存：${saved.name || name}`);
    } catch (error) {
      $("#save-error").textContent = errorText(error);
      $("#save-error").classList.remove("hidden");
    } finally { $("#confirm-save").disabled = false; $("#confirm-save").textContent = "保存设计"; }
  }
  function exportDesign() {
    if (!populated()) return showToast("请先载入设计参数。", "warning");
    const documentData = { format: "dcsj-design", version: 1, name: state.name, model: state.model, source: state.source, parameters: state.parameters, exported_at: new Date().toISOString() };
    const blob = new Blob([JSON.stringify(documentData, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = el("a");
    link.href = url;
    link.download = `${(state.name || "DCSJ-design").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 100)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast("当前参数已导出为 JSON。");
  }
  async function importDesign(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (state.busy) return showToast("计算正在进行，请在完成后导入设计。", "warning");
    if (file.size > 10 * 1024 * 1024) return showToast("文件超过 10 MB，请选择设计参数 JSON 文件。", "error");
    try {
      let design;
      try { design = JSON.parse(await file.text()); }
      catch { throw new Error("文件不是有效的 JSON，无法导入。"); }
      if (!design || typeof design !== "object" || Array.isArray(design)) throw new Error("设计 JSON 的顶层必须是对象。");
      if (design.format && design.format !== "dcsj-design") throw new Error(`不支持的设计文件格式：${asText(design.format)}`);
      if (design.format === "dcsj-design" && design.version !== 1) throw new Error(`不支持的设计版本：${asText(design.version)}`);
      const params = validParameters(design.parameters);
      if (state.busy) throw new Error("计算正在进行，请在完成后重新导入设计。");
      ++modelRequest;
      acceptDesign(params, design.name || file.name.replace(/\.json$/i, ""), design.source || `导入文件：${file.name}`, design.model || "");
      showToast("设计参数已导入。请重新运行计算检查结果。");
    } catch (error) { showToast(errorText(error), "error"); }
  }
  async function boot() {
    const tasks = await Promise.allSettled([loadStatus(), api("/schema"), loadSeries()]);
    const schemaResult = tasks[1];
    if (schemaResult.status === "fulfilled" && Array.isArray(schemaResult.value.fields) && Array.isArray(schemaResult.value.groups)) {
      state.schema = {
        ...schemaResult.value,
        fields: schemaResult.value.fields.map(field => ({
          ...field,
          role: field.role === "historical_result" ? "output" : field.role === "unclassified" ? "unknown" : field.role,
        })),
      };
      renderEditor();
    } else {
      const message = schemaResult.status === "rejected" ? errorText(schemaResult.reason) : "参数结构接口返回格式无效。";
      showNotice(`参数定义读取失败：${message} 未识别参数仍可从原始数据中查看。`);
    }
    navigate(location.hash.slice(1) || "workbench", false);
  }
  $$(".nav-item[data-view]").forEach(item => item.addEventListener("click", () => navigate(item.dataset.view)));
  $$("[data-go]").forEach(item => item.addEventListener("click", () => navigate(item.dataset.go)));
  $(".brand").addEventListener("click", event => { event.preventDefault(); navigate("workbench"); });
  window.addEventListener("hashchange", () => navigate(location.hash.slice(1), false));
  $("#parameter-search").addEventListener("input", renderFields);
  $("#parameter-form").addEventListener("submit", calculate);
  $("#load-example").addEventListener("click", () => {
    if (state.busy) return showToast("计算正在进行，请在完成后载入示例。", "warning");
    loadExample();
  });
  $("#catalog-filter").addEventListener("submit", event => { event.preventDefault(); state.catalog.offset = 0; loadCatalog(); });
  $("#series-filter").addEventListener("change", () => { state.catalog.offset = 0; loadCatalog(); });
  $("#page-previous").addEventListener("click", () => { state.catalog.offset = Math.max(0, state.catalog.offset - state.catalog.limit); loadCatalog(); });
  $("#page-next").addEventListener("click", () => { state.catalog.offset += state.catalog.limit; loadCatalog(); });
  $("#refresh-materials").addEventListener("click", loadMaterials);
  $("#refresh-projects").addEventListener("click", loadProjects);
  $("#refresh-status").addEventListener("click", loadStatus);
  $("#save-design").addEventListener("click", openSaveDialog);
  $("#save-form").addEventListener("submit", saveProject);
  $("#design-name").addEventListener("input", () => $("#design-name").setCustomValidity(""));
  $("#close-save").addEventListener("click", () => $("#save-dialog").close());
  $("#cancel-save").addEventListener("click", () => $("#save-dialog").close());
  $("#open-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", importDesign);
  $("#export-design").addEventListener("click", exportDesign);
  boot().catch(error => showNotice(`初始化失败：${errorText(error)}`));
})();
