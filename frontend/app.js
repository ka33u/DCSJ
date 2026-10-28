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
    author: "",
    series: "",
    model: "",
    source: null,
    baseModelId: null,
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
    project: null,
    selectedCalculations: new Map(), calculationOffset: 0, comparisonOffset: 0,
    quickTemplate: null, quickPreview: null,
    slotBindings: {},
  };
  const viewNames = { workbench: "设计工作台", catalog: "电机型号库", materials: "材料与磁化曲线", projects: "我的设计", about: "分析与实现状态", comparisons: "方案对比", modeling: "快速建模", optimization: "自动寻优" };
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
  async function api(path, options = {}, timeoutMs = 60000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
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
      if (error.name === "AbortError") throw new Error(`请求超过 ${timeoutMs / 1000} 秒未完成。操作可能仍在服务端执行，请刷新状态后再重试。`);
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
    if (view === "resources") { loadResources(); loadLibrary(); }
    if (view === "comparisons") { loadCalculations(); loadSavedComparisons(); }
    if (view === "optimization") { loadOptimizationConfig(); loadOptimizationJobs(); if (selectedJob) watchJob(selectedJob); }
    else clearTimeout(jobPoll);
  }
  function populated() { return Object.keys(state.parameters).length > 0; }
  function setDesignState(text, tone = "") {
    $("#design-state").replaceChildren(el("span", `status-dot ${tone}`), document.createTextNode(text));
  }
  function designChanged() {
    slotPicker?.update();
    ++modelRequest;
    state.revision += 1;
    state.dirty = true;
    $("#raw-parameters").textContent = JSON.stringify(state.parameters, null, 2);
    $("#validation-errors").classList.add("hidden");
    setDesignState("参数已修改", "teal");
    if (state.result) markResultStale();
    updateWireMode();
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
    // Retired 0.7.0 comparison-only inputs are not carried into a new design.
    // The original calculation snapshot/document remains unchanged on the server.
    const retired = new Set(state.schema.retired_input_keys || []);
    const entries = Object.entries(parameters).filter(([key]) => !retired.has(key));
    if (!entries.length) throw new Error("设计参数为空，无法载入。");
    for (const [key, value] of entries) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") throw new Error("设计文件包含不支持的字段名。");
      if (typeof value === "number" && !Number.isFinite(value)) throw new Error(`参数 ${key} 不是有限数值。`);
      if (value !== null && !["string", "number", "boolean"].includes(typeof value)) throw new Error(`参数 ${key} 必须是数字、文本、布尔值或空值。`);
    }
    return Object.fromEntries(entries);
  }
  function acceptDesign(parameters, name, source, model = "", baseModelId = null, author = "", slotBindings = {}, series = "") {
    const accepted = validParameters(parameters);
    series = window.SeriesPicker.validate(series);
    ++modelRequest;
    state.project = null;
    state.baseModelId = baseModelId;
    state.parameters = accepted;
    state.slotBindings = slotBindings || {};
    slotPicker?.update();
    state.name = String(name || "未命名设计");
    state.author = window.DCSJAuth.username();
    state.series = series;
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
    updateWireMode();
  }
  function updateWireMode() {
    const automatic = Number(state.parameters._wire_target) > 0;
    const note = $("#wire-mode-note");
    note.classList.toggle("hidden", !automatic);
    note.textContent = automatic ? "按目标槽满率反算：JJ / Djj / MM / Dmm 输入暂不采用。实际线规和槽满率将在计算结果中显示；将目标槽满率设为 0 可恢复固定线规。" : "";
    $$("#parameter-fields input[data-key]").filter(input => ["JJ", "Djj", "MM", "Dmm"].includes(input.dataset.key)).forEach(input => {
      input.readOnly = automatic;
      input.setAttribute("aria-label", `${input.dataset.key}${automatic ? "，反算模式下输入不采用，只读" : "，固定线规输入"}`);
      const hint = input.closest(".field").querySelector(".field-hint");
      if (hint.dataset.baseHint === undefined) hint.dataset.baseHint = hint.textContent;
      hint.textContent = automatic ? `输入不采用 · ${hint.dataset.baseHint}` : hint.dataset.baseHint;
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
    const curves = Array.isArray(result.curves) ? result.curves.filter(curve => curve.display !== false) : [];
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    const meta = el("div", "result-meta");
    const calculatedAt = result.created_at ? new Date(result.created_at) : null;
    const timeLabel = calculatedAt && !Number.isNaN(calculatedAt.getTime()) ? calculatedAt.toLocaleString("zh-CN") : "未记录";
    meta.append(el("span", "", `引擎：${asText(result.engine?.name || result.engine || "未提供")}`), el("span", "", `计算时间 · ${timeLabel}`));
    container.append(meta);
    if (result.slots && window.SlotPicker) container.append(window.SlotPicker.summary(result.slots));
    if (result.calculation_id) {
      const actions = el("div", "feature-actions");
      actions.append(reportLink(result.calculation_id), button("加入方案对比", "button button-small", () => {
        selectCalculation({ id: result.calculation_id, name: result.name });
        navigate("comparisons");
      }));
      container.append(actions);
    }
    const warningBox = warningsNode(warnings);
    if (warningBox) container.append(warningBox);
    const wire = renderWireSelection(result);
    if (wire) container.append(wire);
    for (const [key,title] of [['refined_torque_peak','加密转矩峰值校核'],['continuous_torque_peak','连续饱和插值峰值（试验）校核']]) {
      const refined = result.curve_metrics?.[key];
      if (!refined) continue;
      const continuous = key === 'continuous_torque_peak';
      const section = el('section','feature-content torque-refinement-result');
      section.dataset.metric=key;section.append(el('h3','',title),el('p','muted',refined.note || ''));
      section.append(el('p','',refined.status === 'available'
        ? `峰值 ${fmt(refined.value,8)} 倍 · s=${fmt(refined.slip,7)} · 相对原采样峰值差 ${fmt(refined.delta_from_legacy,8)} 倍`
        : refined.reason || '加密采样未完成'));
      if (refined.diagnostic) {
        const diagnostic=refined.diagnostic, history=el('details','torque-iteration-history');
        section.append(el('p','muted',diagnostic.note || ''));
        history.append(el('summary','',`查看未收敛采样的末 ${diagnostic.history?.length || 0} 次迭代`));
        history.append(featureTable(['迭代','输入电流倍数','输出电流倍数','电流倍数差','饱和插值输入','饱和系数','插值段'],
          (diagnostic.history || []).map(r=>[fmt(r.iteration,0),fmt(r.input_current,8),fmt(r.current,8),
            fmt(r.residual,7),fmt(r.saturation_input,8),fmt(r.saturation_factor,8),fmt(r.interpolation_window,0)])));
        section.append(history);
      }
      const detail=el('details');detail.append(el('summary','',`查看 ${refined.samples?.length || 0} 个直接计算点与收敛记录`));
      detail.append(featureTable(['转差率 s','转矩倍数','电流倍数','迭代次数','末次电流倍数差',...(continuous?['第二状态差']:[])],(refined.samples || []).map(r=>[
        fmt(r.slip,7),fmt(r.torque,8),fmt(r.current,8),fmt(r.iterations,0),fmt(r.residual,7),...(continuous?[fmt(r.factor_residual,7)]:[])])));
      section.append(detail);container.append(section);
    }
    const manufacturing = result.optimization?.manufacturing;
    const robust = result.optimization?.robustness;
    if (robust) container.append(robustnessView(robust));
    if (manufacturing) {
      const section = el('section','result-section'); section.append(el('h3','result-section-title','寻优规格与尺寸联动'));
      if (manufacturing.numeric_sets?.length) section.append(featureTable(['变量','允许规格','本次输入','规格匹配'], manufacturing.numeric_sets.map(r=>[r.key,r.allowed.map(v=>fmt(v,9)).join(' / '),fmt(r.actual,9),r.passed?'符合':'不符合'])));
      if (manufacturing.rotor_gap) section.append(el('p','muted',gapRecordText(manufacturing.rotor_gap)));
      container.append(section);
    }
    const cycle = result.optimization?.load_cycle;
    if (cycle) {
      const section = el('section','feature-content cycle-result');
      section.append(el('h3','',robust ? '名义尺寸 · 负载周期能耗' : '负载周期能耗'),el('p','muted',cycle.note || ''));
      if (cycle.status === 'available') {
        section.append(el('p','',`运行 ${fmt(cycle.total_hours)} 小时 · 输入 ${fmt(cycle.input_energy_kwh,7)} kWh · 输出 ${fmt(cycle.output_energy_kwh,7)} kWh · 周期效率 ${fmt(cycle.efficiency*100,5)}%`));
        section.append(featureTable(['负载 %','小时','输入 kW','输出 kW','耗电 kWh','取值'],cycle.stages.map(s=>[
          fmt(s.load_fraction*100),fmt(s.hours),fmt(s.input_power_kw,6),fmt(s.output_power_kw,6),fmt(s.input_energy_kwh,6),s.basis==='sample'?'原始采样':'线性插值'])));
      } else section.append(el('p','inline-error',cycle.reason || '没有有效的周期能耗结果'));
      container.append(section);
    }
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
  function renderWireSelection(result) {
    const p = result.inputs || {};
    const automatic = result.wire_selection?.mode === "target_fill" || Number(p._wire_target) > 0;
    const wire = result.wire_selection;
    if (!wire && !automatic) return null;
    const box = el("section", "wire-result");
    box.append(el("h3", "result-section-title", automatic ? "反算采用线规" : "本次采用线规"));
    const used = wire?.used;
    if (!used) {
      box.append(el("p", "small", "旧记录未保存反算后的线规。重新计算后可查看，原输入不能作为实际规格。"));
      return box;
    }
    const scroll = el("div", "table-scroll"), table = el("table", "result-table");
    const head = el("thead"), row = el("tr"), body = el("tbody");
    ["规格", automatic ? "原输入（未采用）" : "输入", "实际采用", "单位"].forEach(label => row.append(el("th", "", label)));
    head.append(row);
    [["JJ", "第一组根数", "根"], ["Djj", "第一组线径", "mm"], ["MM", "第二组根数", "根"], ["Dmm", "第二组线径", "mm"]].forEach(([key, label, unit]) => {
      const line = el("tr"); line.dataset.wireKey = key;
      [label + " " + key, fmt(wire.input?.[key]), fmt(used[key]), unit].forEach(value => line.append(el("td", "", value)));
      body.append(line);
    });
    table.append(head, body); scroll.append(table); box.append(scroll);
    const actual = wire.actual_fill;
    const target = wire.target_fill;
    box.append(el("p", "small", automatic
      ? `目标槽满率 ${fmt(target * 100, 4)}% · 实际 ${fmt(actual * 100, 4)}% · 差值 ${fmt((actual - target) * 100, 4)} 个百分点。反算按可选规格取值，可能偏离目标。`
      : `实际槽满率 ${fmt(actual * 100, 4)}%`));
    if (used.JJ === 0 || used.MM === 0) box.append(el("p", "small muted", "根数为 0 的导线组不参与计算。"));
    return box;
  }
  function svgNode(tag, attrs = {}, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  let curveSequence = 0;
  function renderCurve(curve) {
    const card = el("div", "chart-card");
    card.append(el("h3", "chart-title", curve.title || "数据曲线"));
    if (curve.authoritative === false) card.append(el("p", "chart-explanation", "平滑曲线用于观察趋势；默认校核使用 IST1、TST1、TM1。寻优可另选完整转矩采样的峰值约束，不使用平滑插值值。"));
    const plot = curve.plot || {}, rawSeries = Array.isArray(curve.series) ? curve.series : [];
    let invalidCount = 0;
    const validPoints = points => (Array.isArray(points) ? points : []).filter(point => {
      const valid = point && typeof point.x === "number" && typeof point.y === "number" && Number.isFinite(point.x) && Number.isFinite(point.y);
      if (!valid) invalidCount++;
      return valid;
    });
    const series = (plot.series || rawSeries).map((item, index) => ({
      ...item, name: String(item.name || `曲线 ${index + 1}`),
      color: /^#[0-9a-f]{6}$/i.test(item.color) ? item.color : colors[index % colors.length],
      points: validPoints(item.points),
    }));
    const points = series.flatMap(item => item.points);
    if (!points.length) { card.append(el("p", "chart-empty", "没有可绘制的有效坐标数据。")); return card; }
    const bounds = points.reduce((a, p) => [Math.min(a[0], p.x), Math.max(a[1], p.x), Math.min(a[2], p.y), Math.max(a[3], p.y)], [Infinity, -Infinity, 0, -Infinity]);
    let xMin = plot.x_min ?? bounds[0], xMax = plot.x_max ?? bounds[1];
    if (xMin === xMax) xMax = xMin + 1;
    const axes = (plot.y_axes || [{ id: "y", label: curve.y_label || "y", min: bounds[2], max: bounds[3] }]).map(axis => ({ ...axis, max: axis.max === axis.min ? axis.min + 1 : axis.max }));
    if (!Number.isFinite(xMin) || !Number.isFinite(xMax) || xMax <= xMin || !axes.length || axes.some(a => !Number.isFinite(a.min) || !Number.isFinite(a.max) || a.max <= a.min)) {
      card.append(el("p", "chart-warning", "坐标轴范围无效，请检查曲线数据。")); return card;
    }
    if (curve.id === 'load_characteristic') axes.forEach(axis => {
      // Match the report renderer: transform copies, never saved/API ratios.
      if (axis.id !== 'efficiency' || axis.unit === '%') return;
      Object.assign(axis, { label: '效率 η (%)', short_label: 'η (%)', unit: '%', min: axis.min * 100, max: axis.max * 100 });
      series.filter(item => item.axis === axis.id).forEach(item => {
        item.name = axis.label;
        item.points = item.points.map(point => ({ ...point, y: point.y * 100 }));
      });
    });
    const byAxis = new Map(axes.map(axis => [axis.id, axis]));
    if (series.some(item => item.axis && !byAxis.has(item.axis))) { card.append(el("p", "chart-warning", "曲线引用未知坐标轴。")); return card; }
    const axisFor = item => byAxis.get(item.axis || axes[0].id);
    const axisColor = axis => series.find(item => axisFor(item).id === axis.id)?.color || '#526b5d';
    const legend = el("div", "chart-legend");
    series.forEach(item => {
      const label = el("span", "legend-item"), dot = el("i", "legend-dot"); dot.style.backgroundColor = item.color;
      const axisLabel = axisFor(item).label;
      label.append(dot, document.createTextNode(`${item.name}${axes.length > 1 && item.name !== axisLabel ? ` · ${axisLabel}` : ""}`)); legend.append(label);
    });
    card.append(legend);
    if (plot.note) card.append(el("p", "chart-note muted small", plot.note));
    if (invalidCount) card.append(el("p", "chart-warning", `${invalidCount} 个非数值坐标无法绘制，已从曲线中跳过。`));
    const width = axes.length > 2 ? 760 : 600, left = 20 + 58 * axes.length, right = 28, top = 38, bottom = 54;
    const plotW = width - left - right, plotH = Number.isFinite(plot.aspect_ratio) && plot.aspect_ratio >= .5 && plot.aspect_ratio <= 4 ? plotW / plot.aspect_ratio : 278;
    const height = top + plotH + bottom, xlabel = plot.x_label || curve.x_label || "x";
    const xTicks = Number.isInteger(plot.x_ticks) ? Math.max(2, Math.min(20, plot.x_ticks)) : 5, yTicks = Number.isInteger(plot.y_ticks) ? Math.max(2, Math.min(20, plot.y_ticks)) : 5;
    const px = x => left + (plot.x_reverse ? (xMax - x) : (x - xMin)) / (xMax - xMin) * plotW;
    const py = (y, axis) => top + (axis.max - y) / (axis.max - axis.min) * plotH;
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, class: "curve-svg", role: "img", "aria-label": `${curve.title || "数据曲线"}，横轴 ${xlabel}，纵轴 ${axes.map(a => a.label).join("、")}` });
    svg.append(svgNode("title", {}, `${curve.title || "数据曲线"}；详细原始数值见下方数据表`));
    const clipId = `curve-plot-${++curveSequence}`, defs = svgNode('defs'), clip = svgNode('clipPath', { id: clipId });
    clip.append(svgNode('rect', { x: left, y: top, width: plotW, height: plotH })); defs.append(clip); svg.append(defs);
    for (let tick = 0; tick <= yTicks; tick++) {
      const y = top + plotH * tick / yTicks;
      svg.append(svgNode("line", { x1: left, x2: width - right, y1: y, y2: y, stroke: "#dce5df", "stroke-dasharray": "3 4" }));
      axes.forEach((axis, index) => svg.append(svgNode("text", { x: left - 9 - 58 * (axes.length - 1 - index), y: y + 3, "text-anchor": "end", fill: axisColor(axis), "font-size": 10, "data-axis": axis.id }, fmt(axis.max - (axis.max - axis.min) * tick / yTicks, 4))));
    }
    for (let tick = 0; tick <= xTicks; tick++) {
      const x = left + plotW * tick / xTicks;
      svg.append(svgNode("line", { x1: x, x2: x, y1: top, y2: height - bottom, stroke: "#e8eeea", "stroke-dasharray": "3 4" }));
      svg.append(svgNode("text", { x, y: height - bottom + 20, "text-anchor": "middle", fill: "#526b5d", "font-size": 10, "data-axis": "x" }, fmt(xMin + (xMax - xMin) * (plot.x_reverse ? 1 - tick / xTicks : tick / xTicks), 3)));
    }
    svg.append(svgNode("rect", { x: left, y: top, width: plotW, height: plotH, fill: "none", stroke: "#aabdb0" }));
    axes.forEach((axis, index) => svg.append(svgNode("text", { x: axes.length === 1 ? left : left - 9 - 58 * (axes.length - 1 - index), y: 18, fill: axisColor(axis), "font-size": 11, "text-anchor": axes.length === 1 ? "start" : "end" }, axes.length === 1 ? axis.label : axis.short_label || axis.label)));
    svg.append(svgNode("text", { x: left + plotW / 2, y: height - 8, fill: "#526b5d", "font-size": 11, "text-anchor": "middle" }, xlabel));
    series.forEach(item => {
      const axis = axisFor(item);
      const path = item.points.map((point, index) => `${index ? "L" : "M"}${px(point.x).toFixed(2)},${py(point.y, axis).toFixed(2)}`).join(" ");
      svg.append(svgNode("path", { d: path, fill: "none", stroke: item.color, "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round", "data-series": item.name, "data-axis": axis.id, 'clip-path': `url(#${clipId})` }));
      if (plot.series) return;
      item.points.forEach(point => {
        const marker = svgNode("circle", { cx: px(point.x), cy: py(point.y, axis), r: 2, fill: "#fff", stroke: item.color });
        marker.append(svgNode("title", {}, `${item.name}：${fmt(point.x)}，${fmt(point.y)}`)); svg.append(marker);
      });
    });
    const viewport = el('div', 'curve-viewport'); viewport.append(svg); card.append(viewport);
    const details = el("details", "chart-data"), scroll = el("div", "table-scroll"), table = el("table", "result-table"), head = el("thead"), header = el("tr"), body = el("tbody");
    details.append(el("summary", "", "查看原始采样数据（拟合及显示坐标不改写原始值）"));
    if (curve.id === 'load_characteristic') details.append(el('p', 'muted small', '原始负荷率、效率、功率因数和转差率均为比值；图中负荷率与效率以百分数显示。输入功率为 kW，电流为相电流 A。'));
    ["曲线", curve.x_label || "x", curve.y_label || "y"].forEach(label => header.append(el("th", "", label))); head.append(header);
    rawSeries.forEach(item => validPoints(item.points).forEach(point => { const row = el("tr"); [item.name, fmt(point.x), fmt(point.y)].forEach(value => row.append(el("td", "", value))); body.append(row); }));
    table.append(head, body); scroll.append(table); details.append(scroll); card.append(details);
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
      const result = await api("/calculate", { method: "POST", body: JSON.stringify({ parameters: state.parameters, slot_bindings: state.slotBindings, name: state.name, base_model_id: state.baseModelId, ...(state.project && !state.dirty ? { project_id: state.project.id, project_revision: state.project.revision } : {}) }) });
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
      showNotice(status.engine?.status === "unavailable" ? "尚未安装私有计算资源。请在「资源与用户数据」中导入，完成后即可使用，无需重启服务。" : "");
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
    const params = new URLSearchParams({ q: $("#model-search").value.trim(), series: $("#series-filter").value, source: $("#catalog-source").value, limit, offset });
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
        [item.model || item.id, item.series || "—", item.author || "—", fmt(item.rated_power_kw), fmt(item.poles, 0), fmt(item.voltage_v), fmt(item.frequency_hz)].forEach(value => row.append(el("td", "", value)));
        const source = el("td", "source-cell", item.kind === "saved_design" ? `已保存设计 · v${item.revision}` : sourceText(item.source));
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
      if (model.kind === "saved_design") {
        const design = model.project;
        acceptDesign(design.parameters, design.name, `已保存设计 · v${design.revision} · ${design.author || "未署名"}`, "", design.lineage?.id || null, design.author, design.slot_bindings, design.series);
        state.project = design;
      } else acceptDesign(model.parameters, model.model || String(id), model.source, model.model || "", model.id, "", {}, model.series);
      showToast(`已载入 ${model.model || id}，来源信息显示在参数区。`);
    } catch (error) { showToast(errorText(error), "error"); }
    finally { trigger.disabled = false; trigger.textContent = "载入设计 →"; }
  }
  async function loadSeries() {
    try {
      const data = await api("/series");
      if (!Array.isArray(data.items)) throw new Error("系列接口没有返回 items 数组。");
      const filter = $("#series-filter"), selected = filter.value;
      const names = [...new Set([...data.items, selected].filter(Boolean))];
      filter.replaceChildren(new Option("全部系列", ""), ...names.map(item => new Option(item, item)));
      filter.value = selected; filter.disabled = false;
      seriesPicker.options(data.items);
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
        trigger.append(el("strong", "", material.name || material.id), el("small", "", `B–H ${fmt(material.point_count, 0)} 点 · B–P ${fmt(material.loss_point_count, 0)} 点`));
        trigger.title = sourceText(material.source);
        $("#material-list").append(trigger);
      });
      if (!data.items.length) $("#material-list").append(el("p", "loading-copy", "当前没有材料数据。"));
    } catch (error) {
      $("#material-list").replaceChildren(el("p", "inline-error", `读取失败：${errorText(error)}`));
      state.materialsLoaded = false;
    }
  }
  function materialSourceDocument(material, listSource) {
    const bh = material.curves?.find(curve => curve.id === "bh"), loss = material.curves?.find(curve => curve.id === "loss");
    if (!bh?.series?.[0]?.points?.length || !loss?.series?.[0]?.points?.length) throw new Error("此材料没有完整的两组原始采样数据，无法复制。");
    const hUnit = /A\/cm/.test(bh.y_label) ? "A/cm" : /A\/m/.test(bh.y_label) ? "A/m" : null;
    if (!hUnit || !/\(T\)/.test(bh.x_label) || !/\(T\)/.test(loss.x_label)) throw new Error("此材料的原始 API 单位无法识别，请先核对数据来源。" );
    const lossUnit = material.loss_unit || (/W\/kg/.test(loss.y_label) ? "W/kg" : /W\/m[3³]/.test(loss.y_label) ? "W/m3" : "kernel_native");
    return { source: sourceText(material.source || listSource), notes: "从原始材料复制，两组磁密采样分别保留。" + (material.warnings?.length ? "\n" + material.warnings.join("\n") : ""),
      frequency_hz: material.frequency_hz || "", h_unit: hUnit, loss_unit: lossUnit,
      bh: bh.series[0].points.map(p => [p.x, p.y]), loss: loss.series[0].points.map(p => [p.x, p.y]) };
  }
  function materialSamplingTables(material, listSource) {
    const container = el("div", "material-source-tables");
    let doc;
    try { doc = materialSourceDocument(material, listSource); }
    catch (error) { container.append(el("p", "muted small", errorText(error))); return container; }
    container.append(el("p", "material-editor-note", "原版采样表：B1 与 B2 各自对应本表的 H1 与 Pfe。两张表行号不代表同一磁密，不作同行合并。"));
    for (const key of ["bh", "loss"]) {
      const details = el("details", "material-source-data"), title = key === "bh" ? "B1 (G) / H1 (A/cm)" : `B2 (G) / ${materialLossLabel(doc.loss_unit)}`;
      details.append(el("summary", "", `${title} · ${doc[key].length} 个采样点`));
      const scroll = el("div", "material-table-scroll"), table = el("table", "material-point-table"), head = el("thead"), headRow = el("tr"), body = el("tbody");
      ["行", key === "bh" ? "B1 (G)" : "B2 (G)", key === "bh" ? "H1 (A/cm)" : materialLossLabel(doc.loss_unit)].forEach(label => { const cell = el("th", "", label); cell.scope = "col"; headRow.append(cell); });
      head.append(headRow);
      doc[key].forEach(([b, value], index) => {
        const row = el("tr"); [index + 1, materialNumber(b * 10000), materialNumber(key === "bh" && doc.h_unit === "A/m" ? value / 100 : value)].forEach(v => row.append(el("td", "", String(v)))); body.append(row);
      }); table.append(head, body); table.setAttribute("aria-label", title); scroll.append(table); details.append(scroll); container.append(details);
    }
    return container;
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
      content.append(materialSamplingTables(material, listSource));
      const copy = button("复制到用户材料编辑器", "button button-small", () => {
        try { const document = materialSourceDocument(material, listSource); navigate("resources"); fillEntry({ kind: "materials", name: `${material.name || id} 副本`, document }, true); }
        catch (error) { showToast(errorText(error), "error"); }
      }); header.append(copy);
      if (Array.isArray(material.curves) && material.curves.length) material.curves.forEach(curve => content.append(renderCurve(curve)));
      else content.append(el("p", "chart-empty", "此材料没有可绘制的曲线数据。"));
      $("#material-detail").replaceChildren(header, content);
    } catch (error) {
      if (request === state.materialRequest) $("#material-detail").replaceChildren(el("p", "inline-error", errorText(error)));
    }
  }
  let projectsRequest = 0;
  async function loadProjects() {
    const request = ++projectsRequest;
    $("#projects-feedback").textContent = "正在读取已保存设计…";
    $("#project-table-body").replaceChildren();
    try {
      const data = await api(`/projects?include_archived=${$("#include-archived").checked}`);
      if (request !== projectsRequest) return;
      if (!Array.isArray(data.items)) throw new Error("设计接口没有返回 items 数组。");
      $("#projects-feedback").textContent = data.items.length ? "" : "还没有保存的设计。在工作台中载入参数后，点击「保存设计」。";
      data.items.forEach(project => {
        const row = el("tr");
        const date = new Date(project.updated_at);
        [`${project.name} · ${project.series || "未分类"} · v${project.revision}${project.archived ? " · 已归档" : ""}`, project.author || "未署名", project.id, Number.isNaN(date.getTime()) ? project.updated_at || "—" : date.toLocaleString("zh-CN")].forEach(value => row.append(el("td", "", value)));
        const action = el("td");
        const trigger = button("打开设计 →", "text-button", async () => {
          if (state.busy) return showToast("计算正在进行，请在完成后打开其他设计。", "warning");
          trigger.disabled = true;
          const request = ++modelRequest;
          try {
            const design = await api(`/projects/${encodeURIComponent(project.id)}`);
            if (request !== modelRequest) return;
            acceptDesign(design.parameters, design.name, design.source || `已保存设计 · ${design.id}`, design.model || "", design.lineage?.id || design.base_model_id || null, design.author, design.slot_bindings, design.series);
            state.project = design;
            showToast(`已打开设计：${design.name} · v${design.revision}`);
          } catch (error) { showToast(errorText(error), "error"); }
          finally { trigger.disabled = false; }
        });
        action.append(trigger, button("版本", "text-button", () => showHistory(project)));
        const archive = button(project.archived ? "恢复" : "归档", "text-button", async () => {
          archive.disabled = true;
          try {
            const saved = await api(`/projects/${encodeURIComponent(project.id)}/archive`, { method: "POST", body: JSON.stringify({ archived: !project.archived, expected_revision: project.revision }) });
            if (state.project?.id === saved.id) state.project = { ...state.project, archived: saved.archived };
            state.catalog.loaded = false; ++state.catalog.request;
            await loadProjects();
          } catch (error) { showToast(errorText(error), "error"); }
          finally { archive.disabled = false; }
        });
        action.append(archive);
        row.append(action);
        $("#project-table-body").append(row);
      });
    } catch (error) { if (request === projectsRequest) $("#projects-feedback").textContent = `读取失败：${errorText(error)}`; }
  }
  function openSaveDialog() {
    if (!populated()) return showToast("请先载入设计参数。", "warning");
    $("#design-name").value = state.name;
    seriesPicker.set(state.series);
    $("#design-author").value = window.DCSJAuth.username();
    $("#design-notes").value = state.project?.notes || "";
    $("#save-as-new").checked = !state.project || !!state.project.archived;
    $("#save-as-new").disabled = !state.project;
    $("#save-error").classList.add("hidden");
    $("#save-dialog").showModal();
    $("#design-name").focus();
  }
  async function saveProject(event) {
    event.preventDefault();
    const name = $("#design-name").value.trim();
    if (!name) { $("#design-name").setCustomValidity("请输入设计名称"); $("#design-name").reportValidity(); return; }
    $("#confirm-save").disabled = true;
    seriesPicker.disable(true);
    $("#confirm-save").textContent = "保存中…";
    const revision = state.revision;
    try {
      const updating = state.project && !$("#save-as-new").checked;
      const saved = await api(updating ? `/projects/${state.project.id}` : "/projects", { method: updating ? "PUT" : "POST", body: JSON.stringify({ name, series: seriesPicker.value(), author: window.DCSJAuth.username(), parameters: state.parameters, slot_bindings: state.slotBindings, base_model_id: state.baseModelId, notes: $("#design-notes").value, ...(updating ? { expected_revision: state.project.revision } : {}) }) });
      if (!saved.id) throw new Error("服务未返回设计 ID，无法确认是否保存成功。");
      state.catalog.loaded = false; ++state.catalog.request;
      if (revision === state.revision) {
        state.author = saved.author || "";
        state.series = saved.series || "";
        state.project = saved;
        state.name = saved.name || name;
        state.dirty = false;
        $("#current-design").textContent = state.name;
        $("#current-design").title = state.name;
      }
      $("#save-dialog").close();
      await loadSeries();
      showToast(`设计已保存：${saved.name || name}，可在型号库按名称、系列或作者检索。`);
    } catch (error) {
      $("#save-error").textContent = errorText(error);
      $("#save-error").classList.remove("hidden");
    } finally { seriesPicker.disable(false); $("#confirm-save").disabled = false; $("#confirm-save").textContent = "保存设计"; }
  }
  function exportDesign() {
    if (!populated()) return showToast("请先载入设计参数。", "warning");
    const documentData = { format: "dcsj-design", version: 1, name: state.name, author: state.author, series: state.series, model: state.model, source: state.source, base_model_id: state.baseModelId, parameters: state.parameters, slot_bindings: state.slotBindings, exported_at: new Date().toISOString() };
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
      if (design.author !== undefined && (typeof design.author !== "string" || design.author.length > 120)) throw new Error("作者署名须为不超过 120 字的文本。");
      const params = validParameters(design.parameters);
      if (state.busy) throw new Error("计算正在进行，请在完成后重新导入设计。");
      ++modelRequest;
      acceptDesign(params, design.name || file.name.replace(/\.json$/i, ""), design.source || `导入文件：${file.name}`, design.model || "", design.lineage?.id || design.base_model_id || null, design.author, design.slot_bindings, design.series);
      showToast("设计参数已导入。请重新运行计算检查结果。");
    } catch (error) { showToast(errorText(error), "error"); }
  }
  function reportLink(id, comparison = false) {
    const links = el("span", "report-links");
    const link = el("a", "button button-small", comparison ? "网页打印 ↗" : "新版打印 ↗");
    link.href = `/${comparison ? "comparison-reports" : "reports"}/${encodeURIComponent(id)}`;
    link.target = "_blank"; link.rel = "noopener";
    links.append(link);
    if (!comparison) {
      const legacy = el("a", "button button-small", "旧版打印 ↗");
      legacy.href = `/reports/${encodeURIComponent(id)}?layout=legacy`;
      legacy.target = "_blank"; legacy.rel = "noopener";
      links.append(legacy);
    }
    if (state.status?.reports?.pdf?.available) {
      const pdf = el("a", "button button-small", comparison ? "下载 PDF" : "新版 PDF");
      pdf.href = `/api/v1/${comparison ? "comparison-sets" : "calculations"}/${encodeURIComponent(id)}/report.pdf`;
      links.append(pdf);
    }
    return links;
  }
  async function showHistory(project) {
    const container = $("#history-content");
    container.replaceChildren(el("p", "muted", "读取版本…"));
    $("#history-dialog").showModal();
    try {
      const data = await api(`/projects/${project.id}/revisions`);
      container.replaceChildren();
      data.items.forEach(item => {
        const row = el("div", "history-entry");
        row.append(el("strong", "", `v${item.revision} · ${item.name}`), el("p", "muted", `${item.series || "未分类"} · ${item.author || "未署名"} · ${item.created_at} ${item.notes || ""}`), button("以此版本另建方案", "button button-small", async () => {
          let request, applying = false;
          try {
            if (state.busy) throw new Error("请等待当前计算完成。");
            request = ++modelRequest;
            const revision = await api(`/projects/${project.id}/revisions/${item.revision}`);
            if (request !== modelRequest) return;
            applying = true;
            acceptDesign(revision.parameters, `${revision.name} · v${item.revision} 副本`, `版本 ${project.id} / ${item.revision}`, "", revision.lineage?.id || null, revision.author, revision.slot_bindings, revision.series);
            state.dirty = true;
            $("#history-dialog").close();
          } catch (error) { if (applying || request === undefined || request === modelRequest) showToast(errorText(error), "error"); }
        }));
        container.append(row);
      });
    } catch (error) { container.replaceChildren(el("p", "inline-error", errorText(error))); }
  }
  function selectCalculation(item, selected = true) {
    if (selected && !state.selectedCalculations.has(item.id) && state.selectedCalculations.size >= 8) {
      showToast("最多同时对比 8 个方案。", "warning"); return false;
    }
    if (selected) state.selectedCalculations.set(item.id, item);
    else state.selectedCalculations.delete(item.id);
    const baseline = $("#comparison-baseline");
    const previous = baseline.value;
    baseline.replaceChildren();
    state.selectedCalculations.forEach(scheme => {
      const option = el("option", "", `${scheme.name} · ${scheme.id.slice(0, 8)}`);
      option.value = scheme.id; baseline.append(option);
    });
    if (state.selectedCalculations.has(previous)) baseline.value = previous;
    $("#comparison-count").textContent = `已选 ${state.selectedCalculations.size} / 8`;
    return true;
  }
  let calculationsRequest = 0;
  async function loadCalculations() {
    const request = ++calculationsRequest;
    $("#calculations-feedback").textContent = "读取计算记录…";
    try {
      const data = await api(`/calculations?limit=20&offset=${state.calculationOffset}`);
      if (request !== calculationsRequest) return;
      $("#calculation-table-body").replaceChildren();
      data.items.forEach(item => {
        const row = el("tr"), cell = el("td"), checkbox = el("input");
        checkbox.type = "checkbox"; checkbox.checked = state.selectedCalculations.has(item.id);
        checkbox.setAttribute("aria-label", `选择 ${item.name} ${item.id.slice(0, 8)}`);
        checkbox.addEventListener("change", () => { if (!selectCalculation(item, checkbox.checked)) checkbox.checked = false; });
        cell.append(checkbox); row.append(cell);
        const label = el("td"); label.append(el("strong", "", item.name), el("small", "record-date", new Date(item.created_at).toLocaleString("zh-CN")));
        row.append(label);
        [item.power_kw, item.efficiency, item.material_cost].forEach(value => row.append(el("td", "", fmt(value))));
        const actions = el("td");
        actions.append(reportLink(item.id), button("打开快照", "text-button", () => openSnapshot(item.id)));
        row.append(actions); $("#calculation-table-body").append(row);
      });
      $("#calculations-feedback").textContent = data.total ? "" : "还没有计算记录。运行计算后，结果会自动保存到这里。";
      $("#calculation-page").textContent = `共 ${data.total} 条 · 第 ${Math.floor(state.calculationOffset / 20) + 1} 页`;
      $("#calculation-previous").disabled = state.calculationOffset === 0;
      $("#calculation-next").disabled = state.calculationOffset + data.items.length >= data.total;
    } catch (error) { if (request === calculationsRequest) $("#calculations-feedback").textContent = errorText(error); }
  }
  async function openSnapshot(id) {
    let request, applying = false;
    try {
      if (state.busy) throw new Error("请等待当前计算完成。");
      request = ++modelRequest;
      const item = await api(`/calculations/${encodeURIComponent(id)}`);
      if (request !== modelRequest) return;
      applying = true;
      acceptDesign(item.inputs, item.name, `计算快照 ${id}`, "", item.provenance?.base_model?.id || null, "", item.slot_bindings);
      state.result = item; state.resultRevision = state.revision;
      renderResults(item);
    } catch (error) { if (applying || request === undefined || request === modelRequest) showToast(errorText(error), "error"); }
  }
  function featureTable(headers, rows) {
    const scroll = el("div", "table-scroll"), table = el("table", "data-table");
    const head = el("thead"), tr = el("tr"), body = el("tbody");
    headers.forEach(h => tr.append(el("th", "", h))); head.append(tr);
    rows.forEach(cells => { const row = el("tr"); cells.forEach(c => {const cell=el('td');if(c instanceof Node)cell.append(c);else cell.textContent=c??'';row.append(cell);}); body.append(row); });
    table.append(head, body); scroll.append(table); return scroll;
  }
  let comparisonRequest = 0, savedComparisonsRequest = 0;
  async function runComparison() {
    const ids = [...state.selectedCalculations.keys()];
    if (ids.length < 2) return showToast("请选择至少两个计算方案。", "warning");
    const request = ++comparisonRequest;
    const trigger = $("#run-comparison"); trigger.disabled = true;
    try {
      const data = await api("/comparisons", { method: "POST", body: JSON.stringify({ calculation_ids: ids, baseline_id: $("#comparison-baseline").value }) });
      if (request === comparisonRequest) renderComparison(data);
    } catch (error) { if (request === comparisonRequest) showToast(errorText(error), "error"); }
    finally { trigger.disabled = false; }
  }
  function renderComparison(data) {
    const container = $("#comparison-result"); container.classList.remove("hidden"); container.replaceChildren(el("h2", "", data.name || "性能与参数差异"));
    comparisonControls(container, data);
    container.append(el("p", "muted", "Δ 为相对基准的算术差值，正负方向并不表示性能优劣；基准为 0 时不计算百分比。"));
    const warnings = [...data.warnings, ...data.schemes.flatMap(s => s.warnings.map(w => `${s.name}：${w}`))];
    const box = warningsNode(warnings, "比较条件与计算提示"); if (box) container.append(box);
    const headings = data.schemes.map(s => `${s.name} · ${s.id.slice(0, 8)}${s.id === data.baseline_id ? "（基准）" : ""}`);
    container.append(featureTable(["指标 / 单位", ...headings], data.metrics.map(metric => [
      `${metric.label} ${metric.unit}`, ...metric.cells.map(cell => cell.id === data.baseline_id ? fmt(cell.value) : `${fmt(cell.value)}\nΔ ${fmt(cell.delta)} (${fmt(cell.delta_percent, 3)}%)`),
    ])));
    container.append(el("h3", "result-section-title", "变化的输入参数"));
    if (data.parameter_differences.length) container.append(featureTable(["参数", ...headings], data.parameter_differences.map(field => [field.label, ...field.cells.map(c => fmt(c.value))])));
    else container.append(el("p", "muted", "这些计算使用相同输入。"));
    container.append(el("h3", "result-section-title", "记录来源"));
    data.schemes.forEach(s => { const details = el("details", "trace-details"); details.append(el("summary", "", s.name), el("pre", "", JSON.stringify(s.provenance, null, 2)), reportLink(s.id)); container.append(details); });
    container.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  function comparisonControls(container, data) {
    if (data.id) {
      container.append(el("p", "muted", `已保存 · ${new Date(data.created_at).toLocaleString("zh-CN")} · ${data.id}`));
      if (data.notes) container.append(el("p", "comparison-notes", data.notes));
      container.append(reportLink(data.id, true));
      return;
    }
    const request = comparisonRequest, form = el("form", "comparison-save-form");
    const nameLabel = el("label", "", "对比名称"), name = el("input", "text-input");
    name.name = "comparison-name"; name.required = true; name.maxLength = 120; name.value = "方案对比";
    nameLabel.append(name);
    const notesLabel = el("label", "", "评审说明（可选）"), notes = el("textarea", "text-input");
    notes.name = "comparison-notes"; notes.maxLength = 4000; notes.rows = 3; notesLabel.append(notes);
    const submit = el("button", "button button-primary", "保存对比并生成报告"); submit.type = "submit";
    form.append(nameLabel, notesLabel, submit);
    form.addEventListener("submit", async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        const saved = await api("/comparison-sets", { method: "POST", body: JSON.stringify({
          name: name.value.trim(), notes: notes.value, calculation_ids: data.schemes.map(s => s.id), baseline_id: data.baseline_id }) });
        if (request === comparisonRequest) renderComparison(saved);
        state.comparisonOffset = 0; loadSavedComparisons(); showToast("对比已保存，可重新打开或下载报告。");
      } catch (error) { showToast(errorText(error), "error"); }
      finally { submit.disabled = false; }
    });
    container.append(form);
  }
  async function loadSavedComparisons() {
    const request = ++savedComparisonsRequest;
    $("#saved-comparisons-feedback").textContent = "读取已保存对比…";
    try {
      const data = await api(`/comparison-sets?limit=20&offset=${state.comparisonOffset}`);
      if (request !== savedComparisonsRequest) return;
      const body = $("#saved-comparisons-body"); body.replaceChildren();
      data.items.forEach(item => {
        const row = el("tr");
        row.append(el("td", "", item.name), el("td", "", `${item.scheme_count} 个方案`), el("td", "", new Date(item.created_at).toLocaleString("zh-CN")));
        const actions = el("td");
        actions.append(button("打开对比", "text-button", () => openSavedComparison(item.id)), reportLink(item.id, true));
        row.append(actions); body.append(row);
      });
      $("#saved-comparisons-feedback").textContent = data.total ? "" : "尚未保存对比。选择方案并运行对比后，可保存评审说明与报告。";
      $("#saved-comparisons-page").textContent = `共 ${data.total} 条 · 第 ${Math.floor(state.comparisonOffset / 20) + 1} 页`;
      $("#saved-comparisons-previous").disabled = state.comparisonOffset === 0;
      $("#saved-comparisons-next").disabled = state.comparisonOffset + data.items.length >= data.total;
    } catch (error) { if (request === savedComparisonsRequest) $("#saved-comparisons-feedback").textContent = errorText(error); }
  }
  async function openSavedComparison(id) {
    const request = ++comparisonRequest;
    try {
      const saved = await api(`/comparison-sets/${encodeURIComponent(id)}`);
      if (request !== comparisonRequest) return;
      state.selectedCalculations.clear();
      saved.schemes.forEach(scheme => selectCalculation(scheme));
      $("#comparison-baseline").value = saved.baseline_id;
      renderComparison(saved); loadCalculations();
    } catch (error) { if (request === comparisonRequest) showToast(errorText(error), "error"); }
  }
  let templateRequest = 0;
  async function searchTemplates(event) {
    event.preventDefault(); const request = ++templateRequest;
    const container = $("#template-list"); container.replaceChildren(el("p", "muted", "检索模板…"));
    const params = new URLSearchParams({ power_kw: $("#template-power").value, poles: $("#template-poles").value, frequency_hz: $("#template-frequency").value, phase_voltage: $("#template-voltage").value });
    try {
      const data = await api(`/modeling/templates?${params}`);
      if (request !== templateRequest) return;
      container.replaceChildren();
      const note = warningsNode(data.warnings, data.method); if (note) container.append(note);
      if (!data.items.length) container.append(el("p", "muted", "没有可用模板。请安装本地型号库，或导入自己的完整设计后使用「预览当前设计」。"));
      data.items.forEach(item => {
        const card = el("div", "template-card");
        card.append(el("strong", "", item.model), el("p", "muted", `${fmt(item.power_kw)} kW · ${item.poles} 极 · ${item.frequency_hz} Hz · 相电压 ${item.phase_voltage} V`), el("small", "", `来源：${item.source}`), button("选用模板", "button button-small", () => chooseTemplate(item)));
        container.append(card);
      });
    } catch (error) { if (request === templateRequest) container.replaceChildren(el("p", "inline-error", errorText(error))); }
  }
  let quickRequest = 0;
  function invalidateQuickPreview() {
    ++quickRequest; state.quickPreview = null;
    $("#apply-quick-model").disabled = true;
    const preview = $("#geometry-preview");
    if (!preview.classList.contains("hidden")) preview.replaceChildren(el("p", "muted", "参数已更改，请重新校验并预览。"));
  }
  function syncQuickGap() {
    const gap = $('#quick-fields input[data-key="g"]');
    if (!gap) return;
    const linked = $("#quick-gap-mode").value === "rotor", rotor = $("#quick-rotor-diameter");
    gap.readOnly = linked; rotor.disabled = !linked; rotor.required = linked;
    $("#quick-rotor-field").classList.toggle("hidden", !linked);
    rotor.setCustomValidity("");
    if (!linked) {
      $("#quick-gap-note").textContent = "直接编辑气隙；其他尺寸保持各自输入值。";
      return;
    }
    const bore = $('#quick-fields input[data-key="Di1"]'), shaft = $('#quick-fields input[data-key="Di2"]');
    const hasValues = bore.value !== "" && rotor.value !== "";
    const diameter = Number(rotor.value), boreValue = Number(bore.value), derived = (boreValue - diameter) / 2;
    gap.value = hasValues && Number.isFinite(derived) ? String(derived) : "";
    const valid = hasValues && Number.isFinite(derived) && diameter > 0 && diameter < boreValue &&
      shaft.value !== "" && diameter > Number(shaft.value);
    if (!valid) rotor.setCustomValidity("转子外径须大于轴孔直径、小于定子内径；请检查尺寸。横截面必须保留正气隙。");
    $("#quick-gap-note").textContent = valid
      ? `g = (Di1 − D2) / 2 = (${fmt(boreValue)} − ${fmt(diameter)}) / 2 = ${fmt(derived)} mm。修改定子内径时保持 D2，重新计算气隙。`
      : "输入有效的定子内径与转子外径后，按 g = (Di1 − D2) / 2 计算气隙；轴孔直径须小于转子外径。";
  }
  async function chooseTemplate(item) {
    invalidateQuickPreview();
    const request = quickRequest;
    try {
      const model = await api(`/models/${item.id}`);
      if (request !== quickRequest) return;
      state.quickTemplate = model; state.quickPreview = null;
      $("#apply-quick-model").disabled = true;
      $("#quick-template-name").textContent = `${item.model} · 参数修改`;
      const container = $("#quick-fields"); container.replaceChildren();
      const keys = ["SizeX", "kW", "f", "Ufai", "Jiefa", "Paihao", "D1", "Di1", "Di2", "g", "L", "Q1", "Q2", "ZZ", "a", "JJ", "Djj", "MM", "Dmm"];
      keys.forEach(key => {
        const field = state.schema.fields.find(f => f.key === key) || { label: key };
        const label = el("label", "", `${field.label} (${key})${field.unit ? ` · ${field.unit}` : ""}`), input = el("input", "text-input");
        input.value = model.parameters[key] ?? ""; input.dataset.key = key;
        input.type = ["SizeX", "Jiefa", "Paihao"].includes(key) ? "text" : "number";
        input.step = "any"; input.required = true;
        input.addEventListener("input", () => { syncQuickGap(); invalidateQuickPreview(); });
        label.append(input); container.append(label);
      });
      $("#quick-gap-mode").value = "gap";
      $("#quick-rotor-diameter").value = "";
      syncQuickGap();
      $("#quick-editor").classList.remove("hidden");
      $("#quick-editor").scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (error) { showToast(errorText(error), "error"); }
  }
  function showGeometry(data, changes = [], warnings = [], linkages = []) {
    const container = $("#geometry-preview"); container.classList.remove("hidden"); container.replaceChildren(el("h2", "", "尺寸与变更预览"));
    const image = el("img", "geometry-image");
    image.alt = "铁心尺寸与槽位示意图";
    const url = URL.createObjectURL(new Blob([data.svg], { type: "image/svg+xml" }));
    image.onload = image.onerror = () => URL.revokeObjectURL(url); image.src = url;
    container.append(image);
    const box = warningsNode([...warnings, ...data.warnings], "使用说明"); if (box) container.append(box);
    if (linkages.length) container.append(featureTable(["联动公式", "输入尺寸", "派生值"], linkages.map(link => [
      link.formula, Object.entries(link.inputs).map(([key, value]) => `${key} = ${fmt(value)} ${link.unit}`).join("；"),
      `${link.target} = ${fmt(link.value)} ${link.unit}`])));
    if (changes.length) container.append(featureTable(["参数", "模板值", "当前值"], changes.map(change => [change.label, fmt(change.before), fmt(change.after)])));
    container.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  async function previewQuickModel(event) {
    event.preventDefault(); if (!state.quickTemplate) return;
    const request = ++quickRequest;
    try {
      const changes = Object.fromEntries($$("#quick-fields input").map(input => [input.dataset.key, input.type === "number" ? Number(input.value) : input.value]));
      const body = { model_id: state.quickTemplate.id, changes };
      if ($("#quick-gap-mode").value === "rotor") {
        delete changes.g;
        body.rotor_outer_mm = Number($("#quick-rotor-diameter").value);
      }
      const item = await api("/modeling/preview", { method: "POST", body: JSON.stringify(body) });
      if (request !== quickRequest) return;
      state.quickPreview = item; $("#apply-quick-model").disabled = false;
      showGeometry(item.geometry, item.changes, item.warnings, item.linkages);
    } catch (error) { if (request === quickRequest) showToast(errorText(error), "error"); }
  }

  let optimizationConfig = null, optimizationBase = null, selectedJob = null, jobPoll = null, jobRequest = 0;
  const optimizationPriceDraft = new Map();
  const jobStatus = { queued: "排队中", running: "计算中", cancelling: "正在取消", cancelled: "已取消", succeeded: "已完成", failed: "失败", interrupted: "执行中断" };
  const terminalJobs = new Set(["cancelled", "succeeded", "failed", "interrupted"]);
  async function loadOptimizationConfig() {
    if (optimizationConfig) return;
    try {
      optimizationConfig = await api("/optimization/config");
      $("#optimization-method").textContent = optimizationConfig.method;
      if(optimizationConfig.slot_geometry?.note)$('#optimization-method').append(el('span','slot-search-note',` ${optimizationConfig.slot_geometry.note}`));
      $("#optimization-objective").replaceChildren();
      optimizationConfig.objectives.forEach(item => { const option = el("option", "", item.label); option.value = item.id; $("#optimization-objective").append(option); });
      $("#optimization-secondary").replaceChildren(new Option("不启用，使用单目标", ""), ...optimizationConfig.objectives.map(item => new Option(item.label, item.id)));
      $("#optimization-algorithm").replaceChildren(...(optimizationConfig.algorithms || [{id: 'stratified_v1', label: '分层采样 + 局部搜索'}]).map(item => new Option(item.label, item.id)));
      addOptimizationVariable("L");
      if (!$('#optimization-cycle-stages').children.length) [[50,4],[75,2],[100,2]].forEach(([load,hours]) => addCycleStage(load,hours));
      updateCycleForm();
      if (!$('#optimization-tolerance-rows').children.length) addTolerance();
      updateToleranceForm();
    } catch (error) { showToast(errorText(error), "error"); }
  }
  function useOptimizationBase() {
    if (!populated()) return showToast("请先在工作台载入完整设计。", "warning");
    optimizationBase = JSON.parse(JSON.stringify(state.parameters));
    $("#optimization-base").textContent = `基准：${state.name} · ${fmt(optimizationBase.kW)} kW · ${fmt(optimizationBase.p)} 极；后续修改工作台不会改变此任务基准。`;
    $("#optimization-name").value = `${state.name} · 寻优`.slice(0, 120);
    $$("#optimization-variables .search-variable").forEach(row => suggestVariableBounds(row));
    updatePricingForm();
  }
  function inputField(label, type, name, value = "") {
    const wrapper = el("label", "", label), input = el("input", "text-input");
    input.type = type; input.name = name; input.value = value; if (type === "number") input.step = "any";
    wrapper.append(input); return wrapper;
  }
  function suggestVariableBounds(row) {
    const key = $("select[name=key]", row).value;
    const isMaterial = key === "Paihao";
    updateVariableMode(row); updateGapForm();
    if (isMaterial) { $("input[name=choices]", row).value = optimizationConfig.materials.join(", "); updatePricingForm(); return; }
    const base = Number(optimizationBase?.[key]);
    const integer = optimizationConfig.variables.find(v => v.key === key)?.integer;
    if (Number.isFinite(base) && base > 0) {
      $("input[name=minimum]", row).value = integer ? Math.max(1, Math.floor(base * .9)) : Number((base * .9).toPrecision(6));
      $("input[name=maximum]", row).value = integer ? Math.ceil(base * 1.1) : Number((base * 1.1).toPrecision(6));
    } else { $("input[name=minimum]", row).value = ""; $("input[name=maximum]", row).value = ""; }
    $("input[name=step]", row).value = integer ? 1 : "";
  }
  function updateVariableMode(row) {
    const material=$('select[name=key]',row).value==='Paihao', mode=$('select[name=mode]',row);
    mode.disabled=material;mode.parentElement.hidden=material;
    const list=!material&&mode.value==='values';
    row.classList.toggle('list-mode',list);row.classList.toggle('material-mode',material);
    for(const name of ['minimum','maximum','step','values','choices']) {
      const input=$(`input[name=${name}]`,row);
      const enabled=name==='choices'?material:name==='values'?list:!material&&!list;
      input.disabled=!enabled;input.required=enabled&&name!=='step';input.parentElement.hidden=!enabled;
    }
  }
  function updateGapForm() {
    const enabled=$$('#optimization-variables select[name=key]').some(s=>s.value==='g');
    $('#optimization-gap-mode').disabled=!enabled;$('#optimization-gap-field').hidden=!enabled;
    updatePricingForm();
  }
  function pricingMaterials() {
    const rows=$$('#optimization-variables .search-variable').filter(row=>$('select[name=key]',row).value==='Paihao');
    if(!rows.length)return [];
    return [...new Set([optimizationBase?.Paihao,...rows.flatMap(row=>$('input[name=choices]',row).value.split(/[,，]/).map(s=>s.trim()))].filter(Boolean))];
  }
  function updatePricingForm() {
    const materials=pricingMaterials(),panel=$('#optimization-price-panel'),toggle=$('#optimization-price-enabled'),rows=$('#optimization-price-rows');
    panel.hidden=!materials.length;toggle.disabled=!materials.length;
    const enabled=materials.length>0&&toggle.checked;
    rows.hidden=!enabled;rows.replaceChildren();
    $('#optimization-price-note').textContent=enabled?'按本次任务填写的固定单价计算；每 kg，与铜、铝单价使用同一币种。初始值沿用基准 Cfe，请按自己的报价修改。':`全部候选牌号使用基准硅钢单价 Cfe = ${fmt(optimizationBase?.Cfe,8)} /kg。需要区分价格时勾选分别设置。`;
    for(const material of materials){
      const field=inputField(material+(material===optimizationBase?.Paihao?'（基准）':''),'number','material-price');
      const input=$('input',field);input.dataset.material=material;input.min='0';input.max='1000000';input.required=true;input.disabled=!enabled;
      input.value=optimizationPriceDraft.has(material)?optimizationPriceDraft.get(material):String(optimizationBase?.Cfe??'');
      input.addEventListener('input',()=>optimizationPriceDraft.set(material,input.value));rows.append(field);
    }
  }
  function gapRecordText(gap) {
    return `保持槽颈底直径 ${fmt(gap.reference_neck_bottom_mm,8)} mm；本次 g=${fmt(gap.gap_mm,8)} mm，D2=${fmt(gap.rotor_outer_mm,8)} mm，hr0=${fmt(gap.hr0_mm,8)} mm；${gap.passed?'联动符合':'联动不符合'}。`;
  }
  function addOptimizationVariable(key = "g") {
    if (!optimizationConfig) return;
    if ($$(".search-variable").length >= 6) return showToast("最多配置 6 个搜索变量。", "warning");
    const row = el("div", "quick-form search-variable"), label = el("label", "", "变量"), select = el("select", "text-input"); select.name = "key";
    optimizationConfig.variables.forEach(item => { const option = el("option", "", `${item.key} · ${item.label}`); option.value = item.key; select.append(option); });
    const modeLabel=el('label','','取值方式'),mode=el('select','text-input');mode.name='mode';mode.append(new Option('范围与步长','range'),new Option('指定规格','values'));modeLabel.append(mode);
    mode.addEventListener('change',()=>updateVariableMode(row));
    select.value = key; label.append(select); row.append(label,modeLabel, inputField("下限", "number", "minimum"), inputField("上限", "number", "maximum"), inputField("制造步长（可选）", "number", "step"),inputField('允许规格，逗号或空格分隔','text','values'), inputField("材料候选，逗号分隔", "text", "choices"), button("移除", "text-button", () => {row.remove();updateGapForm();}));
    select.addEventListener("change", () => { $("input[name=values]",row).value=""; suggestVariableBounds(row); });
    $('input[name=choices]',row).addEventListener('input',updatePricingForm);
    $("#optimization-variables").append(row); suggestVariableBounds(row);
  }
  function addOptimizationConstraint() {
    if (!optimizationConfig) return;
    if ($$(".search-constraint").length >= 15) return;
    const row = el("div", "quick-form search-constraint"), label = el("label", "", "指标"), select = el("select", "text-input"); select.name = "metric";
    optimizationConfig.constraint_metrics.forEach(key => { const field = state.schema.fields.find(f => f.key === key); const option = el("option", "", `${key} · ${optimizationConfig.constraint_labels?.[key] || field?.label || (key === "total_loss" ? "总损耗 W" : "")}`); option.value = key; select.append(option); });
    label.append(select); row.append(label, inputField("最小值（可选）", "number", "minimum"), inputField("最大值（可选）", "number", "maximum"), button("移除", "text-button", () => { row.remove(); updateCurveConstraintNote(); }));
    select.addEventListener('change', updateCurveConstraintNote);
    $("#optimization-constraints").append(row);
    updateCurveConstraintNote();
  }
  function updateCurveConstraintNote() {
    const selected=$$('.search-constraint select[name=metric]').map(input=>input.value), note=$('#optimization-curve-note');
    note.hidden = !selected.some(value=>['TM_curve','TM_refined','TM_continuous'].includes(value));
    note.textContent = selected.includes('TM_refined')
      ? '已选择加密转矩峰值：在 s=0.01–1 内直接调用原核，收紧迭代并加密采样峰值，最多增加 257 个直接计算点，计算时间会增加。未收敛或采样不完整的候选不满足约束。有限采样不保证连续全局峰值，不替代原公式 TM1 或原 51 点峰值。'
      : '已选择转矩曲线采样峰值约束：将自动计算每个候选的 51 个转差率采样点，可能增加计算时间。采样范围 s=0.01–1，峰值不使用平滑插值，也不替代原公式 TM1。';
    if (selected.includes('TM_continuous')) note.textContent = '已选择连续饱和插值峰值（试验）：仅对独立转矩采样改用 PCHIP 饱和系数插值，表外不外推；电流与第二状态差均须小于 0.00001。此模型与原软件插值不同，常规效率、损耗和原曲线仍用原模型。最多增加 257 个直接计算点；未完成的结果不满足约束。' + (selected.includes('TM_refined')?' 同时选择原加密峰值时，两种模型分别计算并均须满足约束。':'');
    updateCycleForm();
  }
  function needsCycle() {
    return [$('#optimization-objective').value, $('#optimization-secondary').value,
      ...$$('.search-constraint select[name=metric]').map(input => input.value)].some(value => ['cycle_energy','cycle_efficiency'].includes(value));
  }
  function updateCycleForm() {
    const enabled = needsCycle();
    $('#optimization-cycle').hidden = !enabled; $('#optimization-cycle').disabled = !enabled;
  }
  function addCycleStage(load = '', hours = '') {
    const host = $('#optimization-cycle-stages');
    if (host.children.length >= 32) return showToast('最多配置 32 个负载段。', 'warning');
    const row = el('div', 'quick-form cycle-stage');
    row.append(inputField('负载（额定输出功率 %）','number','load',load),inputField('运行小时','number','hours',hours),
      button('移除负载段','text-button',()=>{if(host.children.length>1)row.remove();else showToast('至少保留一个负载段。','warning');}));
    const loadInput = $('input[name=load]',row), hoursInput = $('input[name=hours]',row);
    loadInput.min=30;loadInput.max=120;loadInput.required=true;
    hoursInput.min='0.000001';hoursInput.max=1000000;hoursInput.required=true;
    host.append(row);
  }
  function readBounds(row) {
    const result = {};
    ["minimum", "maximum", "step"].forEach(name => { const input = $(`input[name=${name}]`, row); if (input && !input.disabled && input.value !== "") result[name] = Number(input.value); });
    return result;
  }
  function updateToleranceForm() {
    const enabled=$('#optimization-tolerance-enabled').checked, rows=$$('#optimization-tolerance-rows .tolerance-row');
    $('#optimization-tolerances').hidden=!enabled;$('#optimization-tolerances').disabled=!enabled;
    const gap=rows.some(row=>$('[name=key]',row).value==='g');
    $('#tolerance-gap-field').hidden=!gap;$('#tolerance-gap-mode').disabled=!enabled||!gap;
    const interior=Number($('#tolerance-interior-samples').value),count=1+2**rows.length+interior,budget=Number($('#optimization-budget').value);
    $('#tolerance-budget-note').textContent=`每个名义方案计算 ${count} 次（含名义尺寸、角点和 ${interior} 个内部点）；当前预算最多评估 ${Math.floor(budget/count)} 组。失败情景也计入预算，至少需 ${count*2} 次以评估基准和一个候选。`;
    $('#add-tolerance').disabled=rows.length>=3;
  }
  function addTolerance() {
    const host=$('#optimization-tolerance-rows');if(host.children.length>=3)return;
    const row=el('div','quick-form tolerance-row'),label=el('label','','公差尺寸'),select=el('select','text-input');select.name='key';
    const used=$$('[name=key]',host).map(s=>s.value),defaults={g:'气隙',L:'铁心长度',Djj:'第一组线径',Dmm:'第二组线径'};
    const items=Object.fromEntries((optimizationConfig?.tolerances?.keys||Object.keys(defaults)).map(key=>[key,defaults[key]||optimizationConfig.variables.find(v=>v.key===key)?.label||key]));
    Object.entries(items).forEach(([key,name])=>select.append(new Option(`${key} · ${name}`,key)));
    select.value=Object.keys(items).find(key=>!used.includes(key));label.append(select);
    row.append(label,inputField('允许偏差 ± mm','number','delta'),button('移除公差','text-button',()=>{if(host.children.length>1){row.remove();updateToleranceForm();}}));
    const delta=$('[name=delta]',row);delta.required=true;delta.min='0';delta.max=1000000;
    select.addEventListener('change',updateToleranceForm);host.append(row);updateToleranceForm();
  }
  function robustnessView(record) {
    const section=el('section','feature-content tolerance-result');
    section.append(el('h3','',record.verification?'补充采样复核':record.feedback?'复核情景参与搜索':'公差情景评估'),el('p','source-box','下方常规指标、曲线与负载周期均为名义尺寸结果；本节目标值取最不利采样。'),el('p','muted',record.note||''));
    section.append(el('p','',`已评估 ${record.evaluated_samples}/${record.expected_samples} 个情景 · ${record.status==='incomplete'?'未完成':record.status==='failed'?'存在计算失败':record.feasible?'全部采样满足约束':'存在约束不满足'}`));
    section.append(featureTable(['目标','最不利采样值','情景编号'],Object.entries(record.objectives||{}).map(([key,value])=>[record.objective_specs?.[key]?.label||key,fmt(value,8),record.worst_samples?.[key]??'—'])));
    section.append(featureTable(['指标','采样最小值','采样最大值','完整'],Object.entries(record.metric_ranges||{}).map(([key,r])=>[key,fmt(r.minimum,8),fmt(r.maximum,8),r.complete?'是':'否'])));
    section.append(sampleTable(record.samples||[],record.tolerances||[],record.objective_specs||{}));
    return section;
  }
  function sampleTable(samples,tolerances,specs={}) {
    const rows=samples.map(s=>{
      const link=s.calculation_id?button('打开采样','text-button',()=>openSnapshot(s.calculation_id)):'—';
      const fields=[...new Set([...tolerances.map(t=>t.key),'hr0'])];
      return [`${s.ordinal?'候选 '+s.ordinal+' / ':''}${s.index} · ${{recheck:'复算 · ',retained:'继承情景 · '}[s.phase]||''}${s.label}`,fields.map(k=>`${k}=${fmt(s.parameters?.[k],8)}`).join('；'),
        Object.values(specs).map(o=>`${o.metric}=${fmt(s.values?.[o.metric],8)}`).join('；')||'—',s.error||s.violations?.join('；')||'满足约束',link];
    });
    return featureTable(['情景','输入尺寸 mm','采样目标指标','校核结果','原始快照'],rows);
  }
  async function startOptimization(event) {
    event.preventDefault();
    const trigger = $("#start-optimization"), errors = $("#optimization-error");
    errors.classList.add("hidden"); trigger.disabled = true;
    try {
      if (!optimizationBase) throw new Error("请先点击「使用工作台当前设计」。");
      const variables = $$(".search-variable").map(row => {
        const key = $("select[name=key]", row).value;
        if(key==='Paihao')return {key,choices:$('input[name=choices]',row).value.split(/[,，]/).map(s=>s.trim()).filter(Boolean)};
        if($('select[name=mode]',row).value==='values') {
          const tokens=$('input[name=values]',row).value.trim().split(/[,，\s]+/).filter(Boolean), values=tokens.map(Number);
          if(!values.length||values.length>64||values.some(v=>!Number.isFinite(v))||new Set(values).size!==values.length) throw new Error(`${key}：请填写 1–64 个不重复的有限数值规格。`);
          return {key,values};
        }
        return {key,...readBounds(row)};
      });
      const constraints = $$(".search-constraint").map(row => ({ metric: $("select[name=metric]", row).value, ...readBounds(row) }));
      const request = { name: $("#optimization-name").value, parameters: optimizationBase, variables, constraints, objective: $("#optimization-objective").value, max_evaluations: Number($("#optimization-budget").value), seed: Number($("#optimization-seed").value) };
      request.secondary_objective = $("#optimization-secondary").value || null;
      request.algorithm = $("#optimization-algorithm").value;
      if(variables.some(v=>v.key==='Paihao')&&$('#optimization-price-enabled').checked){
        const prices=$$('#optimization-price-rows input').map(input=>[input.dataset.material,input.value.trim()]);
        if(prices.some(([,value])=>!value||!Number.isFinite(Number(value))||Number(value)<0||Number(value)>1000000))throw new Error('请为基准和每个候选牌号填写 0–1000000 的硅钢单价。');
        request.material_prices=Object.fromEntries(prices.map(([material,value])=>[material,Number(value)]));
      }
      request.rotor_gap_mode = $('#optimization-gap-mode').disabled?'independent':$('#optimization-gap-mode').value;
      if ($('#optimization-tolerance-enabled').checked) {
        request.tolerances=$$('#optimization-tolerance-rows .tolerance-row').map(row=>({key:$('[name=key]',row).value,delta:Number($('[name=delta]',row).value)}));
        if(!request.tolerances.length||new Set(request.tolerances.map(t=>t.key)).size!==request.tolerances.length||request.tolerances.some(t=>!Number.isFinite(t.delta)||t.delta<=0||t.delta>1000000))throw new Error('请选择不重复的公差尺寸，偏差须大于 0 且不超过 1000000 mm。');
        request.tolerance_interior_samples=Number($('#tolerance-interior-samples').value);
        if(![0,4,8,16,32].includes(request.tolerance_interior_samples))throw new Error('请选择有效的内部采样数。');
        if(request.max_evaluations<2*(1+2**request.tolerances.length+request.tolerance_interior_samples))throw new Error('计算预算不足以评估基准和一个完整公差候选，请增加预算。');
        request.tolerance_gap_mode=$('#tolerance-gap-mode').disabled?'independent':$('#tolerance-gap-mode').value;
      }
      if (request.secondary_objective === request.objective) throw new Error("第二目标请选择不同的指标。");
      if ([request.objective,request.secondary_objective].every(key=>['cycle_energy','cycle_efficiency'].includes(key))) throw new Error('周期耗电与周期效率在同一负载周期下等价，第二目标请选择其他指标。');
      if (needsCycle()) {
        request.load_cycle = $$('#optimization-cycle-stages .cycle-stage').map(row=>({load_fraction:Number($('input[name=load]',row).value)/100,hours:Number($('input[name=hours]',row).value)}));
        if (!request.load_cycle.length || request.load_cycle.some(s=>!Number.isFinite(s.load_fraction)||s.load_fraction<.3||s.load_fraction>1.2||!Number.isFinite(s.hours)||s.hours<=0||s.hours>1000000)) throw new Error('负载须为 30%–120%，运行小时须大于 0。');
      }
      const job = await api("/optimization/jobs", { method: "POST", body: JSON.stringify(request) });
      selectedJob = job.id; showJob(job); loadOptimizationJobs(); watchJob(job.id);
      showToast("寻优任务已排队，可离开此页继续设计。");
    } catch (error) { errors.textContent = errorText(error); errors.classList.remove("hidden"); }
    finally { trigger.disabled = false; }
  }
  async function loadOptimizationJobs() {
    try {
      const data = await api("/optimization/jobs"), container = $("#optimization-jobs"); container.replaceChildren();
      if (!data.items.length) container.append(el("p", "muted", "尚无寻优任务。"));
      data.items.forEach(job => { const row = el("div", "history-entry"); row.append(el("strong", "", job.name), el("span", "muted", ` · ${jobStatus[job.status] || job.status} · ${job.evaluated}/${job.budget} `), button("查看任务", "text-button", () => { selectedJob = job.id; watchJob(job.id); })); container.append(row); });
    } catch (error) { $("#optimization-jobs").replaceChildren(el("p", "inline-error", errorText(error))); }
  }
  async function watchJob(id) {
    clearTimeout(jobPoll); const request = ++jobRequest;
    try {
      const job = await api(`/optimization/jobs/${id}`);
      if (request !== jobRequest || selectedJob !== id) return;
      showJob(job);
      if (!terminalJobs.has(job.status) && $("#view-optimization").classList.contains("active")) jobPoll = setTimeout(() => watchJob(id), 2000);
      else loadOptimizationJobs();
    } catch (error) { showToast(errorText(error), "error"); }
  }
  function showJob(job) {
    const container = $("#optimization-detail"); container.classList.remove("hidden");
    container.replaceChildren(el("h2", "", `${job.name} · ${jobStatus[job.status] || job.status}`));
    const methodId = job.request.algorithm || 'stratified_v1';
    const methodLabel = optimizationConfig?.algorithms?.find(item => item.id === methodId)?.label || ({stratified_v1:'分层采样 + 局部搜索',adaptive_poll_v1:'自适应邻域搜索',coupled_poll_v1:'联合变量搜索（邻点筛选）',ranked_poll_v1:'邻域预测排序（兼顾折中）',curvature_rescue_v1:'曲率补救排序（试验）',hybrid_de_v1:'混合差分进化'}[methodId] || '历史搜索方法');
    const verification=job.request.verification,feedback=job.request.feedback,continuation=job.request.continuation;
    container.append(el('p', 'muted', verification?`补充采样复核 · 固定候选 #${verification.source_candidate_ordinal} · 新点种子 ${verification.seed}`:`本任务方法：${methodLabel} · 随机种子 ${job.request.seed ?? '—'}`));
    if(verification){
      container.append(el('p','source-box',`重新计算 ${verification.prior_samples} 个已有情景，增加 ${verification.interior_samples} 个未参与原搜索的分层点；共 ${job.budget} 次。原搜索记录与本次复核结论独立保留。有限采样不代表整个公差区间都满足约束。`),button('查看源任务','button',()=>{selectedJob=verification.source_job_id;watchJob(selectedJob);}),reportLink(verification.source_calculation_id));
    }
    if(feedback){
      container.append(el('p','source-box',`复核情景参与搜索：保留源复核的全部 ${feedback.retained_samples} 个相对偏差情景（其中 ${feedback.source_failed_samples} 个曾超限或计算失败）。每个新候选使用相同偏差；这些点已参与选择，选定结果后应另做补充采样复核。`),button('查看源复核','button',()=>{selectedJob=feedback.source_job_id;watchJob(selectedJob);}));
    }
    if(continuation){
      container.append(el('p','source-box',`继承原任务 ${continuation.inherited_attempts} 个已记录候选（含失败记录），本次追加 ${continuation.additional_evaluations} 次预算。原目标、约束、材料价格和随机种子保持固定；仅计算新候选。`),button('查看续算源任务','button',()=>{selectedJob=continuation.source_job_id;watchJob(selectedJob);}));
    }
    const constraints = job.request.constraints || [];
    const robust=!!job.request.tolerances?.length;
    if(job.request.variables.some(v=>v.key==='Paihao'))container.append(el('p','source-box',job.request.material_prices?`硅钢分别定价（每 kg，同一币种）：${Object.entries(job.request.material_prices).map(([material,price])=>`${material} = ${fmt(price,8)}`).join('；')}。单价按任务保存。`:`本任务所有候选牌号统一使用硅钢单价 Cfe = ${fmt(job.request.parameters.Cfe,8)} /kg。`));
    if(methodId==='ranked_poll_v1'&&!verification)container.append(el('p','muted',robust?'本任务设置了公差，使用自适应邻域；不对跨情景的最不利值作邻点预测排序。':'利用已算邻点安排现有候选的计算顺序；双目标优先探索可能改善折中的方案。所有成绩和约束仍由真实计算确认。'));
    if(methodId==='curvature_rescue_v1'&&!verification)container.append(el('p','muted',robust?'本任务设置了公差，使用自适应邻域；不对跨情景的最不利值作曲率预测。':'在约束边界附近探索可能被线性预测遗漏的候选；所有成绩和约束仍由真实计算确认。试验方法在不同预算下可能优于或逊于邻域预测排序。'));
    if(methodId==='coupled_poll_v1'&&!verification)container.append(el('p','muted',robust?'本任务设置了公差，使用自适应邻域；不对跨情景的最不利值作邻点预测筛选。':'在邻域停滞时，利用已算邻点筛选成对变量调整；预测只用于安排试探顺序，候选结果均由真实计算确认。'));
    if(robust&&!verification&&!feedback&&job.request.tolerance_interior_samples)container.append(el('p','source-box',`区间内部采样：每组增加 ${job.request.tolerance_interior_samples} 个分层点${job.request.tolerance_sampling==='nested_lhs_v1'?'（逐级保留已有点）':'（独立分层，旧版方法）'} · 种子 ${job.request.seed}；与名义尺寸、全部角点一起参与约束和最不利目标评估。`));
    if(robust)container.append(el('p','source-box',`公差情景：${job.request.tolerances.map(t=>`${t.key} ± ${fmt(t.delta,8)} mm`).join('；')}。目标值与约束范围取全部采样，常规快照与曲线保留名义结果。气隙偏差时${job.request.tolerance_gap_mode==='fixed_neck_bottom'?'槽颈底位置固定，联动 hr0':'保持名义 hr0'}。`));
    if (constraints.some(c => c.metric === 'TM_curve')) container.append(el('p', 'muted', '本任务已启用转矩曲线计算；峰值约束取完整 51 个计算采样的最大值，采样不完整的候选不满足约束。'));
    if (constraints.some(c => c.metric === 'TM_refined')) container.append(el('p','muted','本任务使用加密转矩峰值约束：各点独立迭代，末次电流倍数差小于 0.00001；在 s=0.01–1 的采样局部峰值附近加密。未收敛的候选不满足约束，原计算输出保持独立。'));
    if (constraints.some(c=>c.metric==='TM_continuous')) container.append(el('p','muted','本任务使用连续饱和插值峰值（试验）：改变独立转矩采样的饱和系数插值，常规效率与损耗仍按原模型计算。表外不外推，未收敛结果不满足约束。'));
    if (job.request.load_cycle?.length) container.append(el('p','muted',`本任务负载周期：${job.request.load_cycle.map(s=>`${fmt(s.load_fraction*100)}% × ${fmt(s.hours)} 小时`).join('；')}。耗电单位 kWh；效率为总输出电量 / 总输入电量。`));
    const explicit=job.request.variables.filter(v=>v.values?.length);
    if(explicit.length)container.append(el('p','muted',`指定规格：${explicit.map(v=>`${v.key} ∈ {${v.values.map(x=>fmt(x,9)).join(', ')}}`).join('；')}。基准不在清单中时仅供对照。`));
    if(job.request.rotor_gap_mode==='fixed_neck_bottom')container.append(el('p','muted','本任务保持转子槽颈底位置：hr0 = hr0基准 + g基准 − g；转子外径 D2 = Di1 − 2g。'));
    const progress = el("progress"); progress.max = continuation?continuation.additional_evaluations:job.budget; progress.value = continuation?Math.max(0,job.evaluated-continuation.inherited_attempts):job.evaluated;
    progress.setAttribute("aria-label", continuation?`新增已评估 ${progress.value}，追加预算 ${progress.max}`:`已评估 ${job.evaluated}，预算 ${job.budget}`);
    container.append(progress, el("p", "muted", `${continuation?`本次新增 ${progress.value} / ${progress.max}；继承 ${continuation.inherited_attempts}；累计`:'已评估'} ${job.evaluated} / ${job.budget}${robust?' 次情景计算':''}；满足约束 ${job.feasible_count ?? job.candidates.filter(c => c.feasible).length} 个${robust?'完整候选组':''}；失败与基准计算均计入预算。`), el("p", "", job.message));
    if(continuation&&!robust&&!verification&&!feedback)container.append(continuationBenefit(job));
    if(!robust&&!verification&&!feedback)container.append(objectiveProgress(job));
    if(!job.best_id&&terminalJobs.has(job.status)) {
      const nearest=job.candidates.filter(c=>!c.feasible&&!c.error&&c.calculation_id&&Number.isFinite(c.violation_score)&&c.violation_score<1e6)
        .sort((a,b)=>a.violation_score-b.violation_score||a.ordinal-b.ordinal)[0];
      if(nearest){const box=el('div','source-box optimization-nearest');box.append(el('p','',`本轮未找到满足全部约束的方案。按超限量排序，最接近约束的是 #${nearest.ordinal}，仍未达标：${nearest.violations.join('；')}`),button('查看未达标候选','text-button',()=>openSnapshot(nearest.calculation_id)));container.append(box);}
    }
    const actions = el("div", "feature-actions");
    if (!terminalJobs.has(job.status)) {
      const cancel = button(job.status === "cancelling" ? "当前计算结束后停止" : "取消任务", "button", async () => { try { showJob(await api(`/optimization/jobs/${job.id}/cancel`, { method: "POST" })); watchJob(job.id); } catch (error) { showToast(errorText(error), "error"); } });
      cancel.disabled = job.status === "cancelling"; actions.append(cancel);
    } else actions.append(button(continuation?"从同一源任务重试追加预算":"按原配置重新运行", "button", async () => { try { const next = await api(`/optimization/jobs/${job.id}/retry`, { method: "POST" }); selectedJob = next.id; watchJob(next.id); loadOptimizationJobs(); } catch (error) { showToast(errorText(error), "error"); } }));
    if (job.best_id) {
      actions.append(button("打开最佳候选", "button button-primary", () => openSnapshot(job.best_id)), reportLink(job.best_id));
      const baseline = job.candidates.find(c => c.ordinal === 1)?.calculation_id;
      if (baseline && baseline !== job.best_id) actions.append(button("与基准对比", "button", () => { state.selectedCalculations.clear(); selectCalculation({ id: baseline, name: `${job.name} / 基准` }); selectCalculation({ id: job.best_id, name: `${job.name} / 最佳候选` }); navigate("comparisons"); runComparison(); }));
    }
    container.append(actions);
    if(terminalJobs.has(job.status)&&robust)verificationControls(container,job);
    if(verification&&['succeeded','failed'].includes(job.status))feedbackControls(container,job);
    if(terminalJobs.has(job.status)&&!robust&&!verification&&!feedback&&job.evaluated>0)continuationControls(container,job);
    if(job.verifications?.length){
      const links=el('div','feature-actions');links.append(el('strong','','关联复核：'));
      for(const child of job.verifications)links.append(button(`${child.name} · ${jobStatus[child.status]||child.status} · ${child.evaluated}/${child.budget}`,'button button-small',()=>{selectedJob=child.id;watchJob(child.id);}));
      container.append(links);
    }
    if(job.continuations?.length){
      const links=el('div','feature-actions');links.append(el('strong','','后续搜索：'));
      for(const child of job.continuations)links.append(button(`${child.name} · ${jobStatus[child.status]||child.status} · ${child.evaluated}/${child.budget}`,'button button-small',()=>{selectedJob=child.id;watchJob(child.id);}));
      container.append(links);
    }
    if(job.extensions?.length){
      const links=el('div','feature-actions');links.append(el('strong','','追加预算任务：'));
      for(const child of job.extensions)links.append(button(`${child.name} · ${jobStatus[child.status]||child.status} · 累计 ${child.evaluated}/${child.budget}`,'button button-small',()=>{selectedJob=child.id;watchJob(child.id);}));
      container.append(links);
    }
    if (job.request.load_cycle?.length && job.best_id) {
      const baseCycle=job.candidates.find(c=>c.ordinal===1)?.load_cycle;
      const bestCycle=job.candidates.find(c=>c.calculation_id===job.best_id)?.load_cycle;
      if(baseCycle?.status==='available'&&bestCycle?.status==='available') {
        const saved=baseCycle.input_energy_kwh-bestCycle.input_energy_kwh;
        container.append(el('p','source-box',`${robust?'名义尺寸 · ':''}同一负载周期下，第一目标最佳候选比基准${saved>=0?'少':'多'}耗电 ${fmt(Math.abs(saved),7)} kWh（${fmt(Math.abs(saved)/baseCycle.input_energy_kwh*100,5)}%）；仅比较本次保存的计算结果。`));
      }
    }
    const settings = el("details", "trace-details"); settings.append(el("summary", "", "固定的任务配置"), el("pre", "", JSON.stringify({ algorithm: job.request.algorithm || 'stratified_v1', objective: job.request.objective, secondary_objective: job.request.secondary_objective, material_prices: job.request.material_prices, variables: job.request.variables, constraints: job.request.constraints, load_cycle: job.request.load_cycle, rotor_gap_mode:job.request.rotor_gap_mode, tolerances:job.request.tolerances, tolerance_gap_mode:job.request.tolerance_gap_mode, tolerance_interior_samples:job.request.tolerance_interior_samples||0, tolerance_sampling:job.request.tolerance_sampling||'centered_lhs_v1', seed: job.request.seed, baseline: job.request.parameters.SizeX, verification:verification?Object.fromEntries(Object.entries(verification).filter(([key])=>key!=='plan')):undefined }, null, 2))); container.append(settings);
    if (!job.candidates.length) { container.append(el("p", "muted", "尚无完成的候选。")); return; }
    const pareto = new Set(job.pareto_ids || []);
    if (job.request.secondary_objective) {
      const section = el('section', 'feature-content'); section.append(el('h3', '', `折中候选 · ${pareto.size} 个`), el('p', 'muted', '在本次已评估且满足约束的方案中，这些方案没有被其他方案同时超越两个目标。★ 仍表示第一目标最佳；可选择任意折中方案继续设计。'));
      const keys = [job.request.objective, job.request.secondary_objective];
      const labels = keys.map(key => (robust?'最不利采样 · ':'')+(optimizationConfig?.objectives.find(o => o.id === key)?.label || key));
      const points = job.candidates.filter(c => c.feasible && keys.every(k => Number.isFinite(c.objectives?.[k])));
      if (points.length) {
        const ranges = keys.map(k => { const values = points.map(c => c.objectives[k]), lo = Math.min(...values), hi = Math.max(...values); const pad = (hi-lo || Math.abs(lo)*.01 || 1)*.08; return [lo-pad, hi+pad]; });
        const svg = svgNode('svg', {viewBox:'0 0 640 290',class:'curve-svg optimization-pareto-chart',role:'img','aria-label':`${labels[0]}与${labels[1]}折中图`});
        svg.append(svgNode('title', {}, '绿色表示折中候选，灰色表示被支配的可行方案；数值与操作见下方表格'));
        svg.append(svgNode('rect',{x:80,y:30,width:520,height:200,fill:'none',stroke:'#aabdb0'}));
        for(let i=0;i<=4;i++) {
          svg.append(svgNode('text',{x:80+130*i,y:248,'text-anchor':'middle','font-size':11},fmt(ranges[0][0]+(ranges[0][1]-ranges[0][0])*i/4,5)));
          svg.append(svgNode('text',{x:73,y:234-50*i,'text-anchor':'end','font-size':11},fmt(ranges[1][0]+(ranges[1][1]-ranges[1][0])*i/4,5)));
        }
        svg.append(svgNode('text',{x:340,y:278,'text-anchor':'middle','font-size':12},labels[0]),svgNode('text',{x:80,y:17,'font-size':12},labels[1]));
        for(const c of [...points].sort((a,b)=>Number(pareto.has(a.calculation_id))-Number(pareto.has(b.calculation_id)))) {
          const circle=svgNode('circle',{cx:80+(c.objectives[keys[0]]-ranges[0][0])/(ranges[0][1]-ranges[0][0])*520,cy:230-(c.objectives[keys[1]]-ranges[1][0])/(ranges[1][1]-ranges[1][0])*200,r:pareto.has(c.calculation_id)?5:3,fill:pareto.has(c.calculation_id)?'#18766c':'#bac7c2'});
          circle.append(svgNode('title',{},`#${c.ordinal} · ${keys.map((k,i)=>labels[i]+': '+fmt(c.objectives[k],7)).join('；')}`)); svg.append(circle);
        }
        section.append(svg);
      }
      const choices = el('div', 'feature-actions');
      job.candidates.filter(c => pareto.has(c.calculation_id)).forEach(c => choices.append(button(`#${c.ordinal} · ${keys.map((k,i)=>labels[i]+' '+fmt(c.objectives[k],6)).join(' / ')}`, 'button button-small', () => openSnapshot(c.calculation_id))));
      section.append(choices);
      container.append(section);
    }
    const scroll = el("div", "table-scroll"), table = el("table", "data-table optimization-candidates"), head = el("thead"), heading = el("tr"), body = el("tbody");
    ["序号", "搜索变量", robust?'最不利采样目标值':'目标值', ...(job.request.load_cycle?.length ? [robust?'名义尺寸 · 负载周期':'负载周期结果'] : []), ...(constraints.length ? [robust?'约束采样范围':'约束计算值'] : []), "约束与诊断", "操作"].forEach(title => heading.append(el("th", "", title))); head.append(heading);
    job.candidates.forEach(candidate => {
      const row = el("tr"); row.append(el("td", "", `${candidate.ordinal}${verification?' 固定方案':candidate.ordinal === 1 ? " 基准" : ""}${continuation?(candidate.ordinal<=continuation.inherited_attempts?' · 继承':' · 新增'):''}${candidate.calculation_id && candidate.calculation_id === job.best_id ? " ★" : ""}`));
      const secondary = job.request.secondary_objective;
      const objectiveText = `${fmt(candidate.objective, 7)}${secondary ? '\n第二目标：' + fmt(candidate.objectives?.[secondary], 7) : ''}`;
      row.append(el("td", "", job.request.variables.map(v => `${v.key}=${fmt(candidate.parameters[v.key])}`).join("\n")), el("td", "", objectiveText));
      if(candidate.material_pricing)row.children[1].append(el('p','small',`Cfe=${fmt(candidate.material_pricing.cfe,8)} /kg`));
      if(candidate.manufacturing?.rotor_gap)row.children[1].append(el('p','small',gapRecordText(candidate.manufacturing.rotor_gap)));
      if(candidate.slot_geometry) {
        const details=el('details','slot-search-geometry');details.append(el('summary','',`${robust?'名义尺寸 · ':''}槽型面积与余量`));
        candidate.slot_geometry.slots.forEach(s=>{
          details.append(el('strong','',s.side==='stator'?'定子':'转子'));
          const values=el('dl','slot-search-values');
          [['面积 mm²',s.area_mm2],['槽间余量 mm',s.sector_clearance_mm],['径向余量 mm',s.radial_reserve_mm]].forEach(([label,value])=>
            values.append(el('dt','',label),el('dd','',fmt(value,4))));
          details.append(values);
        });
        row.children[1].append(details);
      }
      if (job.request.load_cycle?.length) row.append(el('td','',candidate.load_cycle?.status==='available'
        ? `${fmt(candidate.load_cycle.input_energy_kwh,7)} kWh\n周期效率 ${fmt(candidate.load_cycle.efficiency*100,5)}%`
        : candidate.load_cycle?.reason || '此记录没有保存负载周期结果'));
      if (constraints.length) {
        const readings = constraints.map(c => {const r=candidate.robustness?.metric_ranges?.[c.metric];return robust?`${c.metric}=${fmt(r?.minimum,7)}–${fmt(r?.maximum,7)}${r?.complete?'':'（未完整）'}`:`${c.metric}=${fmt(candidate.constraint_values?.[c.metric], 7)}${c.metric === 'TM_curve' && Number.isFinite(candidate.torque_peak?.slip) ? ' · s=' + fmt(candidate.torque_peak.slip, 5) : ''}`;});
        if (!robust && candidate.torque_peak?.reason && constraints.some(c => c.metric === 'TM_curve')) readings.push(candidate.torque_peak.reason);
        for (const [metric,key,label] of [['TM_refined','refined_torque_peak','加密峰值'],['TM_continuous','continuous_torque_peak','连续插值峰值（试验）']]) {
          if (robust || !constraints.some(c=>c.metric===metric)) continue;
          const peak=candidate[key];
          if (Number.isFinite(peak?.slip)) readings.push(`${label} s=${fmt(peak.slip,6)} · ${peak.sample_count} 点`);
          if (peak?.reason) readings.push(label+'：'+peak.reason);
        }
        row.append(el('td', '', readings.join('\n')));
      }
      row.append(el("td", "", candidate.error || (candidate.feasible ? (pareto.has(candidate.calculation_id) ? "满足约束 · 折中候选" : "满足约束") : candidate.violations.join("；"))));
      const action = el("td"); if (candidate.calculation_id) action.append(button("打开", "text-button", () => openSnapshot(candidate.calculation_id)), reportLink(candidate.calculation_id)); row.append(action); body.append(row);
    });
    table.append(head, body); scroll.append(table); container.append(scroll);
    if(robust&&job.samples?.length) {
      const details=el('details','trace-details');details.append(el('summary','',`已保存的公差采样 · ${job.samples.length} 次（含未完成组）`),sampleTable(job.samples,job.request.tolerances,job.candidates.find(c=>c.robustness)?.robustness?.objective_specs));container.append(details);
    }
  }

  function optimizationObjectiveSpec(key){
    const specs={
      efficiency:{label:'效率',unit:'%',factor:100,deltaUnit:'个百分点',direction:'max'},
      cycle_efficiency:{label:'负载周期效率',unit:'%',factor:100,deltaUnit:'个百分点',direction:'max'},
      power_factor:{label:'功率因数',unit:'',factor:1,deltaUnit:'',direction:'max'},
      material_cost:{label:'材料成本',unit:'报价币种',factor:1,deltaUnit:'报价币种',direction:'min'},
      copper_mass:{label:'铜重',unit:'kg',factor:1,deltaUnit:'kg',direction:'min'},
      total_loss:{label:'总损耗',unit:'W',factor:1,deltaUnit:'W',direction:'min'},
      cycle_energy:{label:'负载周期耗电',unit:'kWh',factor:1,deltaUnit:'kWh',direction:'min'},
    };
    const config=optimizationConfig?.objectives?.find(o=>o.id===key);
    return specs[key]||{label:config?.label||key,unit:'',factor:1,deltaUnit:'',direction:config?.direction};
  }

  function objectiveProgress(job){
    const section=el('section','feature-content objective-progress');section.id='objective-progress';
    const heading=el('h3','','已算方案的目标进展');heading.id='objective-progress-title';
    section.setAttribute('aria-labelledby',heading.id);section.append(heading);
    const ordered=[...job.candidates].sort((a,b)=>a.ordinal-b.ordinal);
    if(!Number.isInteger(job.evaluated)||job.evaluated<0||ordered.length!==job.evaluated||ordered.some((c,i)=>c.ordinal!==i+1)){
      section.append(el('p','muted','候选记录尚未完整，暂无法绘制目标进展。'));return section;
    }
    if(!ordered.length){section.append(el('p','muted','尚无已完成候选。'));return section;}
    const keys=[job.request.objective,...(job.request.secondary_objective?[job.request.secondary_objective]:[])];
    section.append(el('p','muted small','各目标分别取已算可行方案的最佳值，可能对应不同方案；横轴包含失败和不满足约束的计算。'));
    const charts=el('div','objective-progress-grid');
    for(const key of keys){
      const spec=optimizationObjectiveSpec(key),card=el('div','objective-progress-card');card.dataset.objective=key;
      card.append(el('h4','',`${spec.label}${spec.unit?'（'+spec.unit+'）':''}${spec.direction==='min'?' · 越低越好':spec.direction==='max'?' · 越高越好':''}`));
      charts.append(card);
      if(!['max','min'].includes(spec.direction)){card.append(el('p','muted','此记录缺少目标方向，暂无法绘制。'));continue;}
      const value=c=>c.objectives?.[key]===undefined&&key===job.request.objective?c.objective:c.objectives?.[key];
      if(ordered.some(c=>c.feasible===true&&(!c.calculation_id||c.error||!Number.isFinite(value(c))||!Number.isFinite(value(c)*spec.factor)))){
        card.append(el('p','muted','有可行候选缺少已保存的有效目标值，暂无法完整绘制。'));continue;
      }
      const points=[],improvements=[];let best=null;
      for(const c of ordered){
        if(c.feasible===true&&c.calculation_id&&!c.error&&Number.isFinite(value(c))){
          const better=best===null||(spec.direction==='max'?value(c)>value(best):value(c)<value(best));
          if(better){best=c;improvements.push({attempt:c.ordinal,best:c,value:value(c)*spec.factor});}
        }
        if(best)points.push({attempt:c.ordinal,best,value:value(best)*spec.factor});
      }
      if(!best){card.append(el('p','muted','尚无满足约束的已算方案。'));continue;}
      const format=v=>`${fmt(v,6)}${spec.unit?' '+spec.unit:''}`;
      card.append(el('p','progress-current',`当前最佳 ${format(value(best)*spec.factor)} · 方案 #${best.ordinal}`),
        el('p','muted small',`最近 ${job.evaluated-best.ordinal} 次评估未更新此目标最佳值。`));
      const low=Math.min(...points.map(p=>p.value)),high=Math.max(...points.map(p=>p.value));
      const pad=Math.max((high-low)*.12,Math.abs(high)*.00001,1e-6),minimum=low>=0?Math.max(0,low-pad):low-pad,maximum=high+pad;
      const left=92,right=536,top=35,bottom=207,px=n=>left+n/job.evaluated*(right-left),py=v=>bottom-(v-minimum)/(maximum-minimum)*(bottom-top);
      const svg=svgNode('svg',{viewBox:'0 0 560 252',class:'curve-svg objective-progress-chart',role:'img','aria-label':`${spec.label}最佳值随累计已评估次数变化，单位${spec.unit||'比值'}`});
      svg.append(svgNode('title',{},`${spec.label}目标进展；每个台阶对应实际计算，详情见改善记录。`));
      for(let i=0;i<=4;i++){
        const y=top+(bottom-top)*i/4;
        svg.append(svgNode('line',{x1:left,x2:right,y1:y,y2:y,stroke:'#dce5df','stroke-dasharray':'3 4'}),
          svgNode('text',{x:left-8,y:y+4,'text-anchor':'end','font-size':11},fmt(maximum-(maximum-minimum)*i/4,6)));
      }
      const ticks=[...new Set(Array.from({length:5},(_,i)=>Math.round(job.evaluated*i/4)))];
      for(const n of ticks)svg.append(svgNode('text',{x:px(n),y:bottom+20,'text-anchor':'middle','font-size':11,'data-attempt-tick':n},String(n)));
      svg.append(svgNode('rect',{x:left,y:top,width:right-left,height:bottom-top,fill:'none',stroke:'#aabdb0'}),
        svgNode('text',{x:(left+right)/2,y:246,'text-anchor':'middle','font-size':12},'累计已评估次数'));
      const boundary=job.request.continuation?.inherited_attempts;
      if(Number.isInteger(boundary)&&boundary>=1&&boundary<=job.evaluated){
        svg.append(svgNode('line',{x1:px(boundary),x2:px(boundary),y1:top,y2:bottom,stroke:'#a36b26','stroke-dasharray':'5 4','data-inherited-boundary':boundary}),
          svgNode('text',{x:px(boundary),y:23,'text-anchor':boundary>job.evaluated/2?'end':'start','font-size':11,fill:'#895b23'},'本次追加起点'));
      }
      const path=points.map((p,i)=>i?`H${px(p.attempt).toFixed(2)} V${py(p.value).toFixed(2)}`:`M${px(p.attempt).toFixed(2)},${py(p.value).toFixed(2)}`).join(' ');
      svg.append(svgNode('path',{d:path,fill:'none',stroke:'#18766c','stroke-width':2,'data-progress-objective':key}));
      for(const p of improvements){
        const marker=svgNode('circle',{cx:px(p.attempt),cy:py(p.value),r:3,fill:'#18766c','data-improvement-attempt':p.attempt});
        marker.append(svgNode('title',{},`已评估 ${p.attempt} 次 · 方案 #${p.best.ordinal} · ${format(p.value)}`));svg.append(marker);
      }
      card.append(svg,button(`打开此目标最佳 #${best.ordinal}`,'button button-small',()=>openSnapshot(best.calculation_id)));
      const details=el('details','trace-details');details.append(el('summary','',`查看改善记录 · ${improvements.length} 次（含首次可行）`));
      const scroll=el('div','table-scroll'),table=el('table','data-table'),head=el('thead'),tr=el('tr'),body=el('tbody');
      ['已评估次数',spec.label,'来自方案'].forEach(text=>tr.append(el('th','',text)));head.append(tr);
      for(const p of improvements){const row=el('tr');row.dataset.attempt=p.attempt;row.append(el('td','',String(p.attempt)),el('td','',format(p.value)),el('td','',`#${p.best.ordinal}`));body.append(row);}
      table.append(head,body);scroll.append(table);details.append(scroll);card.append(details);
    }
    section.append(charts);return section;
  }

  function continuationBenefit(job){
    const section=el('section','feature-content continuation-benefit');section.id='continuation-benefit';
    const title=el('h3','','本次追加的实际收益');title.id='continuation-benefit-title';
    section.setAttribute('aria-labelledby',title.id);section.append(title);
    const meta=job.request.continuation, boundary=meta.inherited_attempts;
    const ordered=[...job.candidates].sort((a,b)=>a.ordinal-b.ordinal);
    if(!Number.isInteger(boundary)||boundary<1||ordered.length!==job.evaluated||boundary>ordered.length||ordered.some((c,i)=>c.ordinal!==i+1)){
      section.append(el('p','muted','候选记录尚未完整，暂无法比较追加前后的结果。'));return section;
    }
    const inherited=ordered.slice(0,boundary), added=ordered.slice(boundary);
    const primary=job.request.objective, keys=[primary,...(job.request.secondary_objective?[job.request.secondary_objective]:[])];
    const saved=c=>!!c?.calculation_id&&c.feasible===true&&!c.error;
    const value=(c,key)=>{
      if(!saved(c))return null;
      const raw=c.objectives?.[key]===undefined&&key===primary?c.objective:c.objectives?.[key];
      return Number.isFinite(raw)?raw:null;
    };
    const prior=inherited.find(c=>c.calculation_id===meta.inherited_best_id&&saved(c));
    const current=ordered.find(c=>c.calculation_id===job.best_id&&saved(c));
    const stats=el('div','metric-grid continuation-counts');
    function count(label,amount){const card=el('div','metric-card');card.append(el('span','metric-label',label),el('div','metric-value',amount));stats.append(card);}
    count('新增已评估',String(added.length));
    count('新增可行方案',String(added.filter(saved).length));
    count('新增计算失败',String(added.filter(c=>!!c.error).length));
    if(keys.length===2){
      const vector=c=>keys.every(k=>value(c,k)!==null)?JSON.stringify(keys.map(k=>value(c,k))):null;
      const oldVectors=new Set(inherited.filter(saved).map(vector).filter(Boolean));
      const front=new Set(Array.isArray(job.pareto_ids)?job.pareto_ids:[]);
      const freshVectors=new Set(added.filter(c=>saved(c)&&front.has(c.calculation_id)).map(vector).filter(v=>v&&!oldVectors.has(v)));
      count('新增折中结果',Array.isArray(job.pareto_ids)?String(freshVectors.size):'—');
    }
    section.append(stats);
    if(!added.length)section.append(el('p','muted','尚无新增候选完成计算。'));
    else if(current&&prior)section.append(el('p','muted',current.calculation_id===prior.calculation_id?'当前第一目标最佳仍为继承方案。':'当前第一目标最佳已由新增候选更新。'));
    else if(current&&!meta.inherited_best_id)section.append(el('p','muted',`本次首次找到满足全部约束的方案 #${current.ordinal}。`));
    else if(meta.inherited_best_id||job.best_id)section.append(el('p','muted','最佳方案记录不完整，缺失的目标值暂不比较。'));
    else section.append(el('p','muted','当前尚无满足全部约束的方案。'));
    const scroll=el('div','table-scroll'),table=el('table','data-table continuation-comparison'),head=el('thead'),heading=el('tr'),body=el('tbody');
    ['目标',`追加前最佳${prior?' #'+prior.ordinal:''}`,`当前最佳${current?' #'+current.ordinal:''}`,'实际变化'].forEach(text=>heading.append(el('th','',text)));
    head.append(heading);
    keys.forEach((key,index)=>{
      const spec=optimizationObjectiveSpec(key);
      const before=value(prior,key),after=value(current,key);
      const format=x=>x===null?'—':`${fmt(x*spec.factor,6)}${spec.unit?' '+spec.unit:''}`;
      const row=el('tr');row.dataset.objective=key;
      const label=el('th','',`${index?'第二目标':'第一目标'} · ${spec.label}`);label.scope='row';
      row.append(label,el('td','',format(before)),el('td','',format(after)));
      const change=el('td','','—');
      if(before!==null&&after!==null){
        const delta=after-before,improvement=delta*(spec.direction==='min'?-1:1);
        if(delta===0){change.textContent='未变化';change.dataset.change='unchanged';}
        else{
          const direction=spec.direction==='max'||spec.direction==='min';
          const outcome=direction?(improvement>0?'改善':'退步'):'变化';
          change.textContent=`${delta>0?'增加':'减少'} ${fmt(Math.abs(delta)*spec.factor,6)}${spec.deltaUnit?' '+spec.deltaUnit:''}（${outcome}）`;
          if(direction){change.dataset.change=improvement>0?'improved':'regressed';change.className='benefit-'+change.dataset.change;}
        }
      }
      row.append(change);body.append(row);
    });
    table.append(head,body);scroll.append(table);section.append(scroll);
    section.append(el('p','muted small',keys.length===2?'两列各对应一个第一目标最佳完整方案；第二目标可能随第一目标改善而退步。新增折中结果按目标值去重。':'依据本次已完成的真实计算；运行中的任务会随新增结果更新。'));
    if(prior&&current&&prior.calculation_id!==current.calculation_id)section.append(button('对比追加前后方案','button button-small',()=>{
      state.selectedCalculations.clear();
      selectCalculation({id:prior.calculation_id,name:`${job.name} / 追加前最佳`});
      selectCalculation({id:current.calculation_id,name:`${job.name} / 当前最佳`});
      navigate('comparisons');runComparison();
    }));
    return section;
  }

  function continuationControls(container,job){
    const form=el('form','feature-content verification-form');form.id='continuation-form';
    form.append(el('h3','','追加预算继续寻优'),el('p','muted','保留已算候选、最佳方案及折中方案，从原搜索状态继续。每次可追加 1–128 次，累计最多 1024 次；新增计算失败也计入预算，未必得到更优方案。'));
    const maximum=Math.min(128,1024-job.evaluated);
    if(maximum<1){form.append(el('p','muted','已达到累计 1024 次上限。'));container.append(form);return;}
    const label=el('label','','追加计算次数'),budget=el('input','text-input');budget.id='continuation-budget';label.htmlFor=budget.id;
    budget.type='number';budget.step='1';budget.min='1';budget.max=String(maximum);budget.required=true;budget.value=String(Math.min(24,maximum));label.append(budget);
    const cost=el('p','muted'),submit=el('button','button button-primary','追加预算并继续');submit.type='submit';
    function update(){cost.textContent=`继承 ${job.evaluated} + 新增预算 ${budget.value||'—'} = 累计最多 ${job.evaluated+Number(budget.value)} 次。原任务与报告仍可独立查看。`;}
    budget.oninput=update;update();form.append(label,cost,submit);let pending=false;
    form.onsubmit=async event=>{event.preventDefault();if(pending||!form.reportValidity())return;pending=true;submit.disabled=true;budget.disabled=true;try{
      const next=await api(`/optimization/jobs/${job.id}/extend`,{method:'POST',body:JSON.stringify({additional_evaluations:Number(budget.value)})});
      selectedJob=next.id;showJob(next);watchJob(next.id);loadOptimizationJobs();
    }catch(error){showToast(errorText(error),'error');}finally{pending=false;submit.disabled=false;budget.disabled=false;}};
    container.append(form);
  }

  function verificationControls(container,job){
    const choices=job.candidates.filter(c=>c.feasible&&c.calculation_id&&c.robustness?.status==='complete');
    if(!choices.length)return;
    const form=el('form','feature-content verification-form');form.id='verification-form';
    form.append(el('h3','','搜索后的补充复核'),el('p','muted','固定所选方案，复算已有情景并加入新的分层点。旧情景或新点超限、计算失败时，本次复核不通过。'));
    const fields=el('div','feature-actions');
    function field(title,id,control){control.id=id;const label=el('label','',title);label.htmlFor=id;label.append(control);fields.append(label);return control;}
    const candidate=field('待复核候选','verification-candidate',el('select','text-input'));
    for(const c of choices)candidate.append(new Option(`#${c.ordinal}${c.calculation_id===job.best_id?' · 第一目标最佳':''}`,String(c.ordinal)));
    candidate.value=String(choices.find(c=>c.calculation_id===job.best_id)?.ordinal||choices[0].ordinal);
    const count=field('新增采样点','verification-count',el('select','text-input'));
    for(const n of [4,8,16,32])count.append(new Option(`${n} 点`,String(n)));count.value='8';
    const seed=field('新点种子','verification-seed',el('input','text-input'));
    seed.type='number';seed.min='0';seed.max=String(2**32-1);seed.step='1';seed.required=true;seed.value=String(((job.request.verification?.seed??job.request.seed??42)+1)%2**32);
    const cost=el('p','muted'),submit=el('button','button button-primary','开始补充复核');submit.type='submit';
    function update(){const c=choices.find(c=>c.ordinal===Number(candidate.value));const previous=c.robustness.expected_samples,total=previous+Number(count.value);cost.textContent=`复算 ${previous} + 新增 ${count.value} = ${total} 次计算${total>128?'，超过单次 128 次上限':''}`;submit.disabled=total>128;}
    candidate.onchange=count.onchange=update;update();
    form.append(fields,cost,submit);
    form.onsubmit=async event=>{event.preventDefault();if(!form.reportValidity())return;submit.disabled=true;try{
      const next=await api(`/optimization/jobs/${job.id}/verify`,{method:'POST',body:JSON.stringify({candidate_ordinal:Number(candidate.value),interior_samples:Number(count.value),seed:Number(seed.value)})});
      selectedJob=next.id;showJob(next);watchJob(next.id);loadOptimizationJobs();
    }catch(error){showToast(errorText(error),'error');update();}};
    container.append(form);
  }

  function feedbackControls(container,job){
    const plan=job.request.verification.plan;
    if(!plan?.length||job.evaluated!==plan.length||job.samples?.length!==plan.length||job.candidates.length!==1)return;
    const size=plan.length,minimum=2*size;
    const form=el('form','feature-content verification-form');form.id='feedback-form';
    form.append(el('h3','','纳入复核情景再寻优'),el('p','muted',`沿用原变量范围、目标和约束，以本次复核方案为基准。全部 ${size} 个情景参与后续每个候选的检查，不丢弃已发现的超限或计算失败情景。`));
    if(minimum>128){form.append(el('p','inline-error',`至少需 ${minimum} 次计算，超过单次 128 次上限，暂不能继续搜索。`));container.append(form);return;}
    const fields=el('div','feature-actions');
    function field(title,id,value,min,max){const label=el('label','',title),input=el('input','text-input');input.id=id;label.htmlFor=id;input.type='number';input.step='1';input.min=String(min);input.max=String(max);input.required=true;input.value=String(value);label.append(input);fields.append(label);return input;}
    const budget=field('计算预算','feedback-budget',Math.max(minimum,job.budget),minimum,128);
    const seed=field('搜索种子','feedback-seed',(job.request.verification.seed+1)%2**32,0,2**32-1);
    const cost=el('p','muted'),submit=el('button','button button-primary','纳入情景并开始搜索');submit.type='submit';
    function update(){const n=Number(budget.value);cost.textContent=`每个候选 ${size} 次；此预算可评估 ${Math.floor(n/size)} 个完整候选（含基准），余 ${n%size} 次不启动不完整组。选定结果后需重新进行补充采样复核。`;}
    budget.oninput=update;update();form.append(fields,cost,submit);let pending=false;
    form.onsubmit=async event=>{event.preventDefault();if(pending||!form.reportValidity())return;pending=true;submit.disabled=true;try{
      const next=await api(`/optimization/jobs/${job.id}/resume-search`,{method:'POST',body:JSON.stringify({max_evaluations:Number(budget.value),seed:Number(seed.value)})});
      selectedJob=next.id;showJob(next);watchJob(next.id);loadOptimizationJobs();
    }catch(error){showToast(errorText(error),'error');}finally{pending=false;submit.disabled=false;}};
    container.append(form);
  }

  let resourceState = null, resourceBusy = false, resourceRequest = 0;
  let libraryOffset = 0, libraryRequest = 0, libraryAction = 0, entryEditing = null;
  viewNames.resources = "资源与用户数据";
  function resourceControls() {
    $("#import-resources").disabled = resourceBusy || !resourceState?.remote_configured;
    $("#activate-resources").disabled = resourceBusy || !$("#resource-version").value;
    $("#resource-version").disabled = resourceBusy;
  }
  async function loadResources() {
    const request = ++resourceRequest;
    try {
      const data = await api("/resources");
      if (request !== resourceRequest) return;
      resourceState = data;
      const active = data.active;
      $("#resource-status").textContent = active ? `当前版本 ${active.version} · ${fmt(active.counts.model_versions, 0)} 条型号版本 · ${fmt(active.counts.materials, 0)} 种材料` : "尚未启用版本化资源包；已有本地资源仍可使用。";
      $("#resource-policy").textContent = `${data.user_data_policy} 仓库：${data.repository} / ${data.ref}。${data.remote_configured ? "服务端已配置读取凭据。" : "一键导入需要管理员先在服务端配置私有仓库只读令牌，详见部署说明。"}${data.catalog_override ? "当前 DCSJ_CATALOG_PATH 覆盖了型号库路径，导入不会替换该路径。" : ""}`;
      const select = $("#resource-version"); select.replaceChildren();
      data.installed.forEach(item => { const option = el("option", "", `${item.version} · ${item.package_id.slice(0, 10)}${active?.package_id === item.package_id ? " · 当前" : ""}`); option.value = item.package_id; select.append(option); });
      if (active) select.value = active.package_id;
    } catch (error) { $("#resource-feedback").textContent = errorText(error); }
    resourceControls();
  }
  async function changeResources(activate = false) {
    if (!resourceState || resourceBusy) return;
    resourceBusy = true; resourceControls();
    $("#resource-feedback").textContent = activate ? "正在校验并切换版本…" : "正在从私有仓库下载、校验并导入，请稍候…";
    try {
      const data = await api(activate ? "/resources/activate" : "/resources/import", { method: "POST", body: JSON.stringify({ expected_package_id: resourceState.active?.package_id || null, ...(activate ? { package_id: $("#resource-version").value } : {}) }) }, 600000);
      state.catalog.loaded = false; state.materialsLoaded = false; optimizationConfig = null;
      ++state.catalog.request; ++state.materialRequest;
      const schema = await api("/schema");
      state.schema = { ...schema, fields: schema.fields.map(field => ({ ...field, role: field.role === "historical_result" ? "output" : field.role === "unclassified" ? "unknown" : field.role })) };
      renderEditor(); await Promise.all([loadStatus(), loadSeries()]);
      $("#resource-feedback").textContent = `${data.changed ? "已启用" : "已是当前版本，无需重复导入"}：${data.version}。手工记录与计算快照均已保留；后续计算使用此资源版本。`;
    } catch (error) { $("#resource-feedback").textContent = errorText(error); }
    finally { resourceBusy = false; await loadResources(); }
  }
  const materialTables = {
    bh: { title: "B1–H1 磁化采样表", b: "B1 (G)", value: "H1 (A/cm)" },
    loss: { title: "B2–Pfe 损耗采样表", b: "B2 (G)", value: "Pfe" },
  };
  const materialNumber = value => Number(Number(value).toPrecision(15));
  function materialLossLabel(unit = $("#entry-loss-unit").value) {
    return unit === "kernel_native" ? "Pfe（原表数值，单位待核验）" : `Pfe (${unit === "W/m3" ? "W/m³" : unit})`;
  }
  function materialDraftChanged() {
    $("#material-draft-preview").classList.add("hidden");
    $("#material-draft-error").classList.add("hidden");
    $("#material-draft-status").textContent = "参数已修改，请重新预览。";
  }
  function updateMaterialTable(key) {
    const rows = $$("tr", $("#material-rows-" + key));
    rows.forEach((row, index) => {
      $("th", row).textContent = index + 1;
      const inputs = $$("input", row);
      inputs[0].setAttribute("aria-label", `${materialTables[key].b} 第 ${index + 1} 行`);
      inputs[1].setAttribute("aria-label", `${materialTables[key].value} 第 ${index + 1} 行`);
      $("button", row).setAttribute("aria-label", `删除${key === "bh" ? "磁化" : "损耗"}第 ${index + 1} 行`);
    });
    $("#material-count-" + key).textContent = `${rows.length} 行 · 独立采样`;
    $("#material-add-" + key).disabled = rows.length >= 2000;
  }
  function addMaterialRow(key, values = ["", ""], refresh = true) {
    const body = $("#material-rows-" + key);
    if (body.children.length >= 2000) throw new Error("每张材料表最多支持 2000 行。");
    const row = el("tr"), index = el("th"); index.scope = "row"; row.append(index);
    values.forEach(value => {
      const cell = el("td"), input = el("input", "text-input");
      input.type = "number"; input.step = "any"; input.min = "0"; input.value = value;
      input.addEventListener("input", materialDraftChanged); cell.append(input); row.append(cell);
    });
    const action = el("td"); action.append(button("删除", "text-button", () => {
      row.remove(); updateMaterialTable(key); materialDraftChanged();
    })); row.append(action); body.append(row); if (refresh) updateMaterialTable(key);
  }
  function setMaterialRows(key, points, apiPoints = null, hUnit = null) {
    $("#material-rows-" + key).replaceChildren();
    (points.length ? points : [["", ""], ["", ""]]).forEach(row => addMaterialRow(key, row, false));
    if (apiPoints?.length) $$("tr", $("#material-rows-" + key)).forEach((row, index) => {
      $$("input", row).forEach((input, column) => {
        input.dataset.apiValue = String(apiPoints[index][column]); input.dataset.displayValue = input.value;
        if (key === "bh" && column === 1) input.dataset.apiUnit = hUnit;
      });
    });
    updateMaterialTable(key);
  }
  function bulkMaterialLabels(key) {
    const native = $("#material-format-" + key).value === "native";
    const h = native ? "A/cm" : $("#entry-h-unit").value;
    $("#material-bulk-label-" + key).textContent = `${key === "bh" ? "B1" : "B2"} (${native ? "G" : "T"})，${key === "bh" ? `H1 (${h})` : materialLossLabel()}`;
    $("#entry-" + key).placeholder = key === "bh" ? (native ? "5000, 1.2\n5500, 2.4" : `0.5, ${h === "A/m" ? "120" : "1.2"}\n0.55, ${h === "A/m" ? "240" : "2.4"}`) : (native ? "5000, 1\n6000, 2" : "0.5, 1\n0.6, 2");
  }
  function materialBulkPending(key) {
    const pending = Boolean($("#entry-" + key).value.trim());
    $$("input,button", $("#material-table-" + key)).forEach(node => { node.disabled = pending; });
    $("#material-add-" + key).disabled = pending || $("#material-rows-" + key).children.length >= 2000;
    $("#material-bulk-status-" + key).textContent = pending ? "待应用：预览或保存时也会用此批量内容替换本表；另一张表保持独立。" : "每行两列，可用空格、逗号或制表符分隔；不包含表头。";
  }
  function makeMaterialEditors() {
    for (const [key, spec] of Object.entries(materialTables)) {
      const section = el("section", "material-point-editor"), heading = el("div", "material-table-heading");
      const title = el("h3", "", spec.title); title.id = "material-heading-" + key;
      const count = el("span", "muted small"); count.id = "material-count-" + key; heading.append(title, count);
      const scroll = el("div", "material-table-scroll"), table = el("table", "material-point-table"); table.id = "material-table-" + key;
      table.setAttribute("aria-labelledby", title.id);
      const head = el("thead"), headRow = el("tr");
      ["行", spec.b, key === "loss" ? materialLossLabel() : spec.value, "操作"].forEach((label, index) => {
        const cell = el("th", "", label); cell.scope = "col"; if (index === 2) cell.id = "material-value-label-" + key; headRow.append(cell);
      }); head.append(headRow);
      const body = el("tbody"); body.id = "material-rows-" + key; table.append(head, body); scroll.append(table);
      const add = button("添加一行", "button button-small", () => { addMaterialRow(key); materialDraftChanged(); }); add.id = "material-add-" + key;
      const bulk = el("details", "material-bulk"), summary = el("summary", "", "批量替换此表");
      const formatLabel = el("label", "entry-label", "输入单位"), format = el("select", "text-input"); format.id = "material-format-" + key;
      [["api", "标准单位格式：B (T)，H 使用所选单位"], ["native", "原版表格格式：B (G)，H (A/cm)"]].forEach(([value, label]) => {
        const option = el("option", "", key === "loss" ? label.replace(/，H.*/, "，Pfe 保留当前单位") : label); option.value = value; format.append(option);
      }); format.addEventListener("change", () => { bulkMaterialLabels(key); materialDraftChanged(); }); formatLabel.append(format);
      const label = el("label", "entry-label"), labelText = el("span"); labelText.id = "material-bulk-label-" + key;
      const input = el("textarea", "text-input code-input"); input.id = "entry-" + key; input.rows = 5; input.spellcheck = false;
      input.addEventListener("input", () => { materialBulkPending(key); materialDraftChanged(); }); label.append(labelText, input);
      const status = el("p", "muted small"); status.id = "material-bulk-status-" + key;
      const actions = el("div", "feature-actions");
      actions.append(button("应用到此表", "button button-small", () => {
        try { applyMaterialBulk(key); $("#material-draft-status").textContent = "批量内容已应用，请预览或保存。"; }
        catch (error) { materialDraftError(error); }
      }), button("取消批量输入", "button button-small", () => { input.value = ""; materialBulkPending(key); materialDraftChanged(); }));
      bulk.append(summary, formatLabel, label, status, actions); section.append(heading, scroll, add, bulk);
      $("#material-point-editors").append(section); setMaterialRows(key, []); bulkMaterialLabels(key); materialBulkPending(key);
    }
  }
  function validateMaterialPoints(key, points) {
    const spec = materialTables[key];
    if (points.length < 2 || points.length > 2000) throw new Error(`${spec.title}须有 2–2000 行。`);
    points.forEach((row, index) => {
      if (row.length !== 2 || row.some(value => value === "" || value === null || !Number.isFinite(Number(value)) || Number(value) < 0)) throw new Error(`${spec.title}第 ${index + 1} 行须填入两个非负有限数值。`);
      if (index && Number(row[0]) <= Number(points[index - 1][0])) throw new Error(`${spec.title}第 ${index + 1} 行的 ${spec.b} 必须严格递增，不能重复。`);
      if (index && Number(row[1]) < Number(points[index - 1][1])) throw new Error(`${spec.title}第 ${index + 1} 行的 ${spec.value} 必须单调不减。`);
    });
    return points.map(row => row.map(Number));
  }
  function applyMaterialBulk(key) {
    const input = $("#entry-" + key);
    if (!input.value.trim()) return;
    const native = $("#material-format-" + key).value === "native";
    const lines = input.value.trim().split(/\r?\n/).filter(line => line.trim());
    const points = lines.map((line, index) => {
      const values = line.trim().split(/[\s,，;；]+/);
      if (values.length !== 2 || values.some(v => !Number.isFinite(Number(v)))) throw new Error(`${materialTables[key].title}批量第 ${index + 1} 行须包含两个有效数字。`);
      return values.map(Number);
    });
    validateMaterialPoints(key, points);
    const rows = points.map(([b, value]) => [materialNumber(native ? b : b * 10000), materialNumber(key === "bh" && !native && $("#entry-h-unit").value === "A/m" ? value / 100 : value)]);
    setMaterialRows(key, rows, native ? null : points, $("#entry-h-unit").value); input.value = ""; materialBulkPending(key); materialDraftChanged();
  }
  function materialDraftError(error) {
    $("#material-draft-error").textContent = errorText(error); $("#material-draft-error").classList.remove("hidden");
    $("#material-draft-preview").classList.add("hidden"); $("#material-draft-status").textContent = "请修正材料数据后重试。";
  }
  function resetMaterialEditor(doc = null) {
    for (const key of Object.keys(materialTables)) {
      const points = doc?.[key] || [];
      if (!Array.isArray(points) || points.length > 2000 || points.some(row => !Array.isArray(row) || row.length !== 2 || row.some(v => typeof v !== "number" || !Number.isFinite(v)))) throw new Error("材料 JSON 曲线须为最多 2000 个数值对。");
      setMaterialRows(key, points.map(([b, value]) => [materialNumber(b * 10000), materialNumber(key === "bh" && doc.h_unit === "A/m" ? value / 100 : value)]), points, doc?.h_unit);
      $("#entry-" + key).value = ""; $("#material-format-" + key).value = "api"; bulkMaterialLabels(key); materialBulkPending(key);
    }
    $("#material-value-label-loss").textContent = materialLossLabel();
    $("#material-draft-preview").replaceChildren(); $("#material-draft-preview").classList.add("hidden");
    $("#material-draft-error").classList.add("hidden"); $("#material-draft-status").textContent = "";
  }
  function readMaterialDocument() {
    const result = { frequency_hz: Number($("#entry-frequency").value), h_unit: $("#entry-h-unit").value, loss_unit: $("#entry-loss-unit").value };
    if (!Number.isFinite(result.frequency_hz) || result.frequency_hz <= 0 || result.frequency_hz > 100000) throw new Error("请填写大于 0 且不超过 100000 Hz 的材料频率。");
    for (const key of Object.keys(materialTables)) {
      applyMaterialBulk(key);
      const rows = validateMaterialPoints(key, $$("tr", $("#material-rows-" + key)).map(row => $$("input", row).map(input => input.value)));
      const controls = $$("tr", $("#material-rows-" + key)).map(row => $$("input", row));
      result[key] = rows.map(([b, value], index) => [b / 10000, key === "bh" && result.h_unit === "A/m" ? value * 100 : value].map((converted, column) => {
        const input = controls[index][column], original = input.dataset;
        return original.apiValue !== undefined && Number(input.value) === Number(original.displayValue) && (!original.apiUnit || original.apiUnit === result.h_unit)
          ? Number(original.apiValue) : materialNumber(converted);
      }));
      if (result[key].some(row => row.some(value => value > 1e9))) throw new Error(`${materialTables[key].title}换算后超过数值上限 10⁹，请核对输入单位。`);
    }
    return result;
  }
  function previewMaterialDraft() {
    try {
      const doc = readMaterialDocument(), preview = $("#material-draft-preview"); preview.replaceChildren();
      preview.append(el("p", "material-editor-note", "待保存预览：分别连接每张表的采样点；不增加原点、不外推。"));
      for (const key of Object.keys(materialTables)) {
        const unit = key === "bh" ? `H (${doc.h_unit})` : materialLossLabel(doc.loss_unit);
        const nativePoints = doc[key].map(([b, value]) => ({ x: key === "bh" && doc.h_unit === "A/m" ? value / 100 : value, y: b * 10000 }));
        preview.append(renderCurve({ id: key, title: materialTables[key].title, x_label: "磁密 B (T)", y_label: unit,
          series: [{ name: $("#entry-name").value || "待保存材料", points: doc[key].map(([x, y]) => ({ x, y })) }],
          plot: { x_min: 0, x_max: Math.max(...nativePoints.map(p => p.x), 1), x_label: key === "bh" ? "H1 (A/cm)" : materialLossLabel(doc.loss_unit),
            y_axes: [{ id: "b", label: materialTables[key].b, min: 0, max: Math.max(...nativePoints.map(p => p.y), 1), color: colors[0] }],
            series: [{ name: $("#entry-name").value || "待保存材料", axis: "b", color: colors[0], points: nativePoints }], note: "两组采样分别保留；Pfe 不作单位换算。" } }));
      }
      preview.classList.remove("hidden"); $("#material-draft-error").classList.add("hidden");
      $("#material-draft-status").textContent = `磁化 ${doc.bh.length} 点 · 损耗 ${doc.loss.length} 点，尚未保存。`;
    } catch (error) { materialDraftError(error); }
  }
  function syncEntryKind() {
    const material = $("#entry-kind").value === "materials";
    $("#entry-model-fields").classList.toggle("hidden", material);
    $("#entry-material-fields").classList.toggle("hidden", !material);
    $$("input,select,textarea,button", $("#entry-material-fields")).forEach(node => { node.disabled = !material; });
    if (material) Object.keys(materialTables).forEach(materialBulkPending);
  }
  function fillEntry(entry = null, copy = false) {
    ++libraryAction;
    entryEditing = entry && !copy ? entry : null;
    $("#entry-form").reset();
    $("#entry-kind").value = entry?.kind || $("#library-kind").value;
    $("#entry-kind").disabled = Boolean(entryEditing);
    $("#entry-heading").textContent = entryEditing ? `编辑 ${entry.name} · v${entry.revision}` : "新增用户记录";
    $("#save-entry").textContent = entryEditing ? "保存为下一版本" : "保存为新记录";
    if (entry) {
      const doc = entry.document;
      $("#entry-name").value = entry.name;
      $("#entry-source").value = doc.source;
      $("#entry-notes").value = doc.notes || "";
      if (entry.kind === "models") $("#entry-parameters").value = JSON.stringify(entry.input_parameters || doc.parameters, null, 2);
      else {
        $("#entry-frequency").value = doc.frequency_hz;
        $("#entry-h-unit").value = doc.h_unit; $("#entry-loss-unit").value = doc.loss_unit;
      }
    }
    resetMaterialEditor(entry?.kind === "materials" ? entry.document : null);
    syncEntryKind(); $("#entry-feedback").textContent = "";
    $("#entry-heading").scrollIntoView({ block: "start", behavior: "smooth" });
  }
  async function saveEntry(event) {
    event.preventDefault();
    const kind = $("#entry-kind").value, editing = entryEditing;
    $("#entry-fields").disabled = true;
    try {
      const document = { source: $("#entry-source").value.trim(), notes: $("#entry-notes").value };
      if (kind === "models") document.parameters = validParameters(JSON.parse($("#entry-parameters").value));
      else Object.assign(document, readMaterialDocument());
      const saved = await api(`/library/${kind}${editing ? "/" + editing.id : ""}`, { method: editing ? "PUT" : "POST", body: JSON.stringify({ name: $("#entry-name").value.trim(), document, ...(editing ? { expected_revision: editing.revision } : {}) }) });
      fillEntry(saved); optimizationConfig = null;
      $("#entry-feedback").textContent = `已保存 v${saved.revision}。${saved.compatibility ? saved.compatibility.compatible ? "曲线满足当前计算接口要求。" : saved.compatibility.reasons.join("；") : ""}`;
      $("#library-kind").value = kind; libraryOffset = 0; await loadLibrary();
    } catch (error) { $("#entry-feedback").textContent = errorText(error); if (kind === "materials") materialDraftError(error); }
    finally { $("#entry-fields").disabled = false; }
  }
  function exportEntry(entry) {
    const data = { format: "dcsj-user-entry", format_version: 1, kind: entry.kind, name: entry.name, document: entry.document, origin: { reference: entry.reference, document_sha256: entry.document_sha256 } };
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = el("a"); link.href = url; link.download = `${entry.name.replace(/[^\p{L}\p{N}._-]/gu, "_")}-v${entry.revision}.json`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function importEntry(event) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    try {
      if (file.size > 512 * 1024) throw new Error("用户记录文件不能超过 512 KiB。");
      const entry = JSON.parse(await file.text());
      if (entry.format !== "dcsj-user-entry" || entry.format_version !== 1 || !["models", "materials"].includes(entry.kind) || typeof entry.name !== "string" || !entry.document) throw new Error("请选择从用户库导出的 dcsj-user-entry v1 JSON 文件。");
      fillEntry(entry, true); $("#entry-feedback").textContent = "已读入待保存记录。核对内容后保存为新记录，同名数据不会被覆盖。";
    } catch (error) { $("#entry-feedback").textContent = errorText(error); }
  }
  function useEntry(entry) {
    if (state.busy) return showToast("请等待当前计算完成。", "warning");
    if (entry.kind === "models") acceptDesign(entry.input_parameters || entry.document.parameters, entry.name, { path: "用户型号库", description: `${entry.document.source} · ${entry.reference}` }, entry.name, entry.reference);
    else {
      if (!entry.compatibility?.compatible) return showToast("此材料暂不兼容当前计算核。", "warning");
      if (!populated()) return showToast("请先在工作台载入设计。", "warning");
      if (Number(state.parameters.f) !== entry.document.frequency_hz) return showToast("设计频率与材料曲线频率不一致，请先调整设计频率。", "warning");
      state.parameters.Paihao = entry.reference; state.invalidInputs.delete("Paihao"); designChanged(); renderEditor(); navigate("workbench");
      showToast(`已选用 ${entry.name} · v${entry.revision}，请重新计算。`);
    }
  }
  async function previewUserMaterial(entry) {
    const preview = $("#user-material-preview"), request = ++libraryAction;
    try {
      const material = await api(`/materials/${encodeURIComponent(entry.reference)}`);
      if (request !== libraryAction) return;
      preview.classList.remove("hidden"); preview.replaceChildren(el("h3", "", `${entry.name} · v${entry.revision}`));
      const warning = warningsNode(material.warnings, "材料数据说明"); if (warning) preview.append(warning);
      preview.append(materialSamplingTables({ ...material, loss_unit: entry.document.loss_unit }, entry.document.source));
      material.curves.forEach(curve => preview.append(renderCurve(curve)));
      preview.scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (error) { if (request === libraryAction) showToast(errorText(error), "error"); }
  }
  async function entryHistory(entry) {
    const container = $("#history-content"), request = ++libraryAction;
    container.replaceChildren(el("p", "", "正在加载版本…")); $("#history-dialog").showModal();
    try {
      const data = await api(`/library/${entry.kind}/${entry.id}/revisions`);
      if (request !== libraryAction) return;
      container.replaceChildren();
      data.items.forEach(item => {
        const row = el("div", "history-row");
        row.append(el("strong", "", `v${item.revision} · ${item.name}`), el("p", "muted", item.created_at));
        const open = async (use) => {
          const action = ++libraryAction;
          try {
            const old = await api(`/library/${entry.kind}/${entry.id}?revision=${item.revision}`);
            if (action !== libraryAction) return;
            if (use) useEntry(old); else fillEntry(old, true);
            $("#history-dialog").close();
          } catch (error) { showToast(errorText(error), "error"); }
        };
        row.append(button("查看 / 复制此版本", "button button-small", () => open(false)), button("使用此版本", "button button-small", () => open(true))); container.append(row);
      });
    } catch (error) { if (request === libraryAction) container.replaceChildren(el("p", "inline-error", errorText(error))); }
  }
  async function loadLibrary() {
    const request = ++libraryRequest, kind = $("#library-kind").value;
    $("#library-feedback").textContent = "正在读取…";
    try {
      const data = await api(`/library/${kind}?limit=20&offset=${libraryOffset}&include_archived=${$("#library-archived").checked}`);
      if (request !== libraryRequest) return;
      if (libraryOffset && !data.items.length) { libraryOffset = Math.max(0, libraryOffset - 20); return loadLibrary(); }
      const container = $("#library-list"); container.replaceChildren();
      data.items.forEach(entry => {
        const card = el("article", "user-entry");
        card.append(el("h3", "", `${entry.name} · v${entry.revision}${entry.archived ? " · 已归档" : ""}`), el("small", "entry-reference", entry.reference), el("p", "muted small", entry.document.source));
        if (entry.compatibility) card.append(el("p", entry.compatibility.compatible ? "small" : "small field-warning", entry.compatibility.compatible ? "可用于当前计算核（50 Hz）" : entry.compatibility.reasons.join("；")));
        const actions = el("div", "feature-actions");
        if (!entry.archived) actions.append(button("编辑", "button button-small", () => fillEntry(entry)));
        actions.append(button("版本", "button button-small", () => entryHistory(entry)), button("导出 JSON", "button button-small", () => exportEntry(entry)));
        if (kind === "models" || entry.compatibility?.compatible) actions.append(button(kind === "models" ? "载入工作台" : "用于当前设计", "button button-small", () => useEntry(entry)));
        if (kind === "models") actions.append(button("快速建模", "button button-small", () => { navigate("modeling"); chooseTemplate({ id: entry.reference, model: entry.name }); }));
        else actions.append(button("查看曲线", "button button-small", () => previewUserMaterial(entry)));
        const archiveButton = button(entry.archived ? "恢复" : "归档", "button button-small", async () => {
          archiveButton.disabled = true;
          try { await api(`/library/${kind}/${entry.id}/archive`, { method: "POST", body: JSON.stringify({ archived: !entry.archived, expected_revision: entry.revision }) }); optimizationConfig = null; await loadLibrary(); }
          catch (error) { showToast(errorText(error), "error"); }
          finally { archiveButton.disabled = false; }
        });
        actions.append(archiveButton); card.append(actions); container.append(card);
      });
      $("#library-feedback").textContent = data.total ? "" : "暂无用户记录，可在左侧新增或导入。";
      $("#library-page").textContent = `共 ${data.total} 条 · 第 ${Math.floor(libraryOffset / 20) + 1} 页`;
      $("#library-previous").disabled = libraryOffset === 0; $("#library-next").disabled = libraryOffset + 20 >= data.total;
    } catch (error) { if (request === libraryRequest) $("#library-feedback").textContent = errorText(error); }
  }
  makeMaterialEditors();
  $("#preview-material-draft").addEventListener("click", previewMaterialDraft);
  $("#entry-h-unit").addEventListener("change", () => { Object.keys(materialTables).forEach(bulkMaterialLabels); materialDraftChanged(); });
  $("#entry-loss-unit").addEventListener("change", () => { $("#material-value-label-loss").textContent = materialLossLabel(); bulkMaterialLabels("loss"); materialDraftChanged(); });
  $("#entry-frequency").addEventListener("input", materialDraftChanged);
  $("#refresh-resources").addEventListener("click", loadResources);
  $("#import-resources").addEventListener("click", () => changeResources());
  $("#activate-resources").addEventListener("click", () => changeResources(true));
  $("#entry-kind").addEventListener("change", syncEntryKind);
  $("#entry-form").addEventListener("submit", saveEntry);
  $("#new-entry").addEventListener("click", () => fillEntry());
  $("#copy-current-entry").addEventListener("click", () => {
    if (!populated()) return showToast("请先在工作台载入设计。", "warning");
    $("#entry-parameters").value = JSON.stringify(state.parameters, null, 2);
    if (!$("#entry-name").value) $("#entry-name").value = state.name;
    $("#entry-source").value = sourceText(state.source);
  });
  $("#open-entry-import").addEventListener("click", () => $("#entry-file").click());
  $("#entry-file").addEventListener("change", importEntry);
  $("#refresh-library").addEventListener("click", loadLibrary);
  $("#library-kind").addEventListener("change", () => { libraryOffset = 0; ++libraryAction; $("#user-material-preview").classList.add("hidden"); loadLibrary(); });
  $("#library-archived").addEventListener("change", () => { libraryOffset = 0; loadLibrary(); });
  $("#library-previous").addEventListener("click", () => { libraryOffset = Math.max(0, libraryOffset - 20); loadLibrary(); });
  $("#library-next").addEventListener("click", () => { libraryOffset += 20; loadLibrary(); });
  syncEntryKind();

  async function boot() {
    const user = await window.DCSJAuth.ready;
    state.author = user.username;
    window.DCSJAuth.beforeLogout = () => {
      if (state.busy) { showToast("请等待当前操作完成后退出登录。", "warning"); return false; }
      if (state.dirty && !confirm("当前设计尚未保存，确定退出登录？")) return false;
      state.dirty = false; return true;
    };
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
  $("#catalog-source").addEventListener("change", () => { $("#series-filter").value = ""; $("#series-filter").disabled = false; state.catalog.offset = 0; loadCatalog(); });
  $("#series-filter").addEventListener("change", () => { state.catalog.offset = 0; loadCatalog(); });
  $("#page-previous").addEventListener("click", () => { state.catalog.offset = Math.max(0, state.catalog.offset - state.catalog.limit); loadCatalog(); });
  $("#page-next").addEventListener("click", () => { state.catalog.offset += state.catalog.limit; loadCatalog(); });
  $("#refresh-materials").addEventListener("click", loadMaterials);
  $("#refresh-projects").addEventListener("click", loadProjects);
  $("#optimization-use-current").addEventListener("click", useOptimizationBase);
  $("#add-optimization-variable").addEventListener("click", () => addOptimizationVariable());
  $("#add-optimization-constraint").addEventListener("click", () => addOptimizationConstraint());
  $('#optimization-objective').addEventListener('change',updateCycleForm);
  $('#optimization-secondary').addEventListener('change',updateCycleForm);
  $('#optimization-price-enabled').addEventListener('change',updatePricingForm);
  $('#optimization-tolerance-enabled').addEventListener('change',updateToleranceForm);
  $('#tolerance-interior-samples').addEventListener('change',updateToleranceForm);
  $('#optimization-budget').addEventListener('input',updateToleranceForm);
  $('#add-tolerance').addEventListener('click',addTolerance);
  $('#add-cycle-stage').addEventListener('click',()=>addCycleStage());
  $("#optimization-form").addEventListener("submit", startOptimization);
  $("#refresh-optimization-jobs").addEventListener("click", loadOptimizationJobs);
  $("#include-archived").addEventListener("change", loadProjects);
  $("#close-history").addEventListener("click", () => $("#history-dialog").close());
  $("#refresh-calculations").addEventListener("click", () => { loadCalculations(); loadSavedComparisons(); });
  $("#saved-comparisons-previous").addEventListener("click", () => { state.comparisonOffset = Math.max(0, state.comparisonOffset - 20); loadSavedComparisons(); });
  $("#saved-comparisons-next").addEventListener("click", () => { state.comparisonOffset += 20; loadSavedComparisons(); });
  $("#run-comparison").addEventListener("click", runComparison);
  $("#calculation-previous").addEventListener("click", () => { state.calculationOffset = Math.max(0, state.calculationOffset - 20); loadCalculations(); });
  $("#calculation-next").addEventListener("click", () => { state.calculationOffset += 20; loadCalculations(); });
  $("#template-search").addEventListener("submit", searchTemplates);
  $("#quick-model-form").addEventListener("submit", previewQuickModel);
  $("#quick-gap-mode").addEventListener("change", () => {
    if ($("#quick-gap-mode").value === "rotor") {
      const bore = $('#quick-fields input[data-key="Di1"]'), gap = $('#quick-fields input[data-key="g"]');
      $("#quick-rotor-diameter").value = bore?.value && gap?.value ? String(Number(bore.value) - 2 * Number(gap.value)) : "";
    }
    syncQuickGap(); invalidateQuickPreview();
  });
  $("#quick-rotor-diameter").addEventListener("input", () => { syncQuickGap(); invalidateQuickPreview(); });
  $("#apply-quick-model").addEventListener("click", () => {
    if (!state.quickPreview || state.busy) return;
    const item = state.quickPreview;
    acceptDesign(item.parameters, item.name, item.source, "", item.template_id || null);
    state.dirty = true;
    showToast("已载入模板修改，请重新计算。");
  });
  $("#preview-current-geometry").addEventListener("click", async () => {
    try {
      if (!populated()) throw new Error("请先载入设计参数。");
      showGeometry(await api("/geometry/preview", { method: "POST", body: JSON.stringify({ parameters: state.parameters }) }));
    } catch (error) { showToast(errorText(error), "error"); }
  });
  $("#refresh-status").addEventListener("click", loadStatus);
  $("#save-design").addEventListener("click", openSaveDialog);
  $("#save-form").addEventListener("submit", saveProject);
  $("#design-name").addEventListener("input", () => $("#design-name").setCustomValidity(""));
  $("#close-save").addEventListener("click", () => $("#save-dialog").close());
  $("#cancel-save").addEventListener("click", () => $("#save-dialog").close());
  $("#open-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", importDesign);
  $("#export-design").addEventListener("click", exportDesign);
  const seriesPicker = window.SeriesPicker.mount($("#design-series-control"));
  const slotPicker = window.SlotPicker?.mount($("#slot-workbench"), {
    module: "main", read: () => {
      if (state.invalidInputs.size) throw new Error("请先完成当前参数输入");
      return state.parameters;
    }, bindings: () => state.slotBindings, busy: () => state.busy,
    apply: (parameters, binding) => {
      state.parameters = parameters; state.slotBindings = { ...state.slotBindings, ...binding };
      designChanged(); renderEditor(); showToast("槽型已带入，请重新计算。");
    }, detach: side => { delete state.slotBindings[side]; designChanged(); },
  });
  boot().catch(error => showNotice(`初始化失败：${errorText(error)}`));
})();
