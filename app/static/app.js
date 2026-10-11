const state = { products: [], selected: null, apiKey: "", user: null, currentView: "overview" };
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("X-API-Key", state.apiKey);
  options.headers = headers;
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || `请求失败：${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function hasRole(...roles) {
  return Boolean(state.user && roles.includes(state.user.role));
}

function applyPermissions() {
  document.querySelectorAll("[data-roles]").forEach(element => {
    const roles = element.dataset.roles.split(",");
    element.classList.toggle("permission-hidden", !state.user || !roles.includes(state.user.role));
  });
}

function updateIdentity(user) {
  state.user = user;
  $("#login-trigger").classList.toggle("hidden", Boolean(user));
  $("#user-pill").classList.toggle("hidden", !user);
  if (user) {
    $("#current-user").textContent = user.actor;
    $("#current-role").textContent = user.role.toUpperCase();
    $("#user-avatar").textContent = user.actor.slice(0, 1).toUpperCase();
  }
  applyPermissions();
}

function switchView(view) {
  state.currentView = view;
  document.querySelectorAll("[data-page]").forEach(page => page.classList.toggle("active", page.dataset.page === view));
  document.querySelectorAll(".nav-button").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  if (view === "quality") loadQualityAnalytics().catch(error => toast(error.message, true));
  if (view === "experiments") loadExperimentCenter().catch(error => toast(error.message, true));
  window.scrollTo({top: 0, behavior: "smooth"});
}

function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.style.borderColor = isError ? "var(--danger)" : "var(--mint)";
  element.classList.remove("hidden");
  window.setTimeout(() => element.classList.add("hidden"), 3200);
}

async function loadDashboard() {
  const [data, alerts] = await Promise.all([api("/api/dashboard"), api("/api/quality-alerts?status=open")]);
  $("#metric-products").textContent = data.product_count;
  $("#metric-inspections").textContent = data.inspection_count;
  $("#metric-anomalies").textContent = data.anomaly_count;
  $("#metric-pending").textContent = data.pending_reviews;
  $("#metric-alerts").textContent = alerts.length;
}

async function loadProducts(selectId = null) {
  state.products = await api("/api/products");
  const container = $("#product-list");
  if (!state.products.length) {
    container.innerHTML = '<p class="muted">还没有产品型号。</p>';
    return;
  }
  container.innerHTML = state.products.map(product => `
    <button class="product-item ${state.selected?.id === product.id ? "active" : ""}" data-id="${product.id}">
      <strong>${escapeHtml(product.name)}</strong>
      <span>${escapeHtml(product.code)} · ${product.reference_count} 张参考图</span>
    </button>`).join("");
  container.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => selectProduct(Number(button.dataset.id)));
  });
  $("#analytics-product-select").innerHTML = '<option value="">请选择产品</option>' + state.products.map(product => `<option value="${product.id}">${escapeHtml(product.code)} · ${escapeHtml(product.name)}</option>`).join("");
  $("#experiment-product-select").innerHTML = '<option value="">请选择产品</option>' + state.products.map(product => `<option value="${product.id}">${escapeHtml(product.code)} · ${escapeHtml(product.name)}</option>`).join("");
  if (state.selected) $("#analytics-product-select").value = String(state.selected.id);
  if (state.selected) $("#experiment-product-select").value = String(state.selected.id);
  if (selectId) await selectProduct(selectId);
}

async function selectProduct(id) {
  state.selected = await api(`/api/products/${id}`);
  $("#empty-state").classList.add("hidden");
  $("#product-workspace").classList.remove("hidden");
  $("#selected-code").textContent = state.selected.code;
  $("#selected-name").textContent = state.selected.name;
  $("#selected-description").textContent = state.selected.description || "暂无产品说明";
  $("#threshold").value = state.selected.threshold;
  $("#reference-count").textContent = `已登记 ${state.selected.references.length} 张`;
  $("#result-view").classList.add("hidden");
  await loadModels();
  await loadTrainingDatasets();
  await loadEvaluation();
  await loadSpc();
  await loadProducts();
  await loadQualityAnalytics();
}

async function loadSpc() {
  if (!state.selected) return;
  const result = await api(`/api/products/${state.selected.id}/spc?limit=100`);
  $("#spc-message").textContent = result.ready ? `${result.sample_count} 个样本的三西格玛控制限` : (result.message || `当前 ${result.sample_count} 个样本，至少建议5个`);
  $("#spc-mean").textContent = result.mean?.toFixed(4) ?? "—";
  $("#spc-sigma").textContent = result.sigma?.toFixed(4) ?? "—";
  $("#spc-ucl").textContent = result.ucl?.toFixed(4) ?? "—";
  $("#spc-ooc").textContent = result.out_of_control_count ?? "—";
}

function chartEmpty(selector, message) {
  $(selector).setAttribute("viewBox", "0 0 700 250");
  $(selector).innerHTML = `<text class="empty-chart" x="350" y="125">${escapeHtml(message)}</text>`;
}

function linePath(points) {
  return points.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`).join(" ");
}

function renderQualityTrend(rows) {
  const svg = $("#quality-trend-chart");
  if (!rows.length) { chartEmpty("#quality-trend-chart", "当前产品暂无趋势数据"); return; }
  const data = [...rows].reverse();
  const width = 1000, height = 260, left = 52, right = 46, top = 18, bottom = 42;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maxCount = Math.max(1, ...data.map(row => Number(row.inspection_count)));
  const x = index => left + (data.length === 1 ? plotWidth / 2 : index * plotWidth / (data.length - 1));
  const yCount = value => top + plotHeight - Number(value) / maxCount * plotHeight;
  const yRate = value => top + plotHeight - value * plotHeight;
  const rates = data.map(row => Number(row.anomaly_count || 0) / Math.max(1, Number(row.inspection_count)));
  const ratePoints = rates.map((value, index) => [x(index), yRate(value)]);
  const barWidth = Math.min(34, plotWidth / Math.max(1, data.length) * .55);
  const grids = [0, .25, .5, .75, 1].map(ratio => {
    const y = top + plotHeight * (1 - ratio);
    return `<line class="grid-line" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"/><text class="axis-text" x="${left - 10}" y="${y + 4}" text-anchor="end">${Math.round(maxCount * ratio)}</text><text class="axis-text" x="${width - right + 9}" y="${y + 4}">${Math.round(ratio * 100)}%</text>`;
  }).join("");
  const bars = data.map((row, index) => `<rect class="bar" x="${x(index) - barWidth / 2}" y="${yCount(row.inspection_count)}" width="${barWidth}" height="${top + plotHeight - yCount(row.inspection_count)}" rx="3"/>`).join("");
  const labels = data.map((row, index) => index % Math.max(1, Math.ceil(data.length / 7)) === 0 ? `<text class="axis-text" x="${x(index)}" y="${height - 15}" text-anchor="middle">${escapeHtml(row.day.slice(5))}</text>` : "").join("");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `${grids}${bars}<path class="series-secondary" d="${linePath(ratePoints)}"/>${ratePoints.map(point => `<circle class="point out" cx="${point[0]}" cy="${point[1]}" r="3.5"/>`).join("")}${labels}<text class="axis-title" x="${left}" y="12">检测量</text><text class="axis-title" x="${width - right}" y="12" text-anchor="end">异常率</text>`;
  const total = data.reduce((sum, row) => sum + Number(row.inspection_count), 0);
  const anomalies = data.reduce((sum, row) => sum + Number(row.anomaly_count || 0), 0);
  $("#trend-summary").textContent = `${total} 件 · 异常率 ${total ? (anomalies / total * 100).toFixed(1) : 0}%`;
}

function renderSpcChart(result) {
  const svg = $("#spc-control-chart");
  if (!result.points?.length) { chartEmpty("#spc-control-chart", "检测后生成SPC控制图"); return; }
  const data = result.points, width = 650, height = 260, left = 50, right = 20, top = 20, bottom = 40;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const maxValue = Math.max(.01, result.ucl, ...data.map(row => Number(row.anomaly_score))) * 1.08;
  const x = index => left + (data.length === 1 ? plotWidth / 2 : index * plotWidth / (data.length - 1));
  const y = value => top + plotHeight - Number(value) / maxValue * plotHeight;
  const points = data.map((row, index) => [x(index), y(row.anomaly_score)]);
  const grids = [0, .25, .5, .75, 1].map(ratio => `<line class="grid-line" x1="${left}" y1="${top + plotHeight * ratio}" x2="${width - right}" y2="${top + plotHeight * ratio}"/><text class="axis-text" x="${left - 8}" y="${top + plotHeight * ratio + 4}" text-anchor="end">${(maxValue * (1 - ratio)).toFixed(2)}</text>`).join("");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = `${grids}<line class="control-line" x1="${left}" y1="${y(result.ucl)}" x2="${width - right}" y2="${y(result.ucl)}"/><text class="axis-text" x="${width - right}" y="${y(result.ucl) - 6}" text-anchor="end">UCL ${Number(result.ucl).toFixed(3)}</text><path class="series-primary" d="${linePath(points)}"/>${data.map((row, index) => `<circle class="point ${row.out_of_control ? "out" : ""}" cx="${points[index][0]}" cy="${points[index][1]}" r="4"><title>#${row.id} · ${Number(row.anomaly_score).toFixed(4)}</title></circle>`).join("")}<text class="axis-title" x="${left}" y="12">异常分数</text><text class="axis-text" x="${left}" y="${height - 13}">早</text><text class="axis-text" x="${width - right}" y="${height - 13}" text-anchor="end">近</text>`;
  $("#spc-chart-summary").textContent = `${result.sample_count} 样本 · ${result.out_of_control_count} 个失控点`;
}

function renderConfusionMatrix(result) {
  const values = result ? [result.true_positive, result.false_positive, result.false_negative, result.true_negative] : ["—", "—", "—", "—"];
  document.querySelectorAll("#confusion-matrix .matrix-cell strong").forEach((element, index) => element.textContent = values[index]);
  $("#matrix-summary").textContent = result ? `准确率 ${(result.accuracy * 100).toFixed(1)}% · ${result.sample_count} 样本` : "待复核样本";
}

async function loadQualityAnalytics() {
  if (!state.selected) {
    chartEmpty("#quality-trend-chart", "请先在检测中心选择产品");
    chartEmpty("#spc-control-chart", "请先选择产品");
    renderConfusionMatrix(null);
    return;
  }
  $("#analytics-product-select").value = String(state.selected.id);
  const [trend, spc, evaluation] = await Promise.all([
    api(`/api/products/${state.selected.id}/quality-trend?days=30`),
    api(`/api/products/${state.selected.id}/spc?limit=100`),
    api(`/api/products/${state.selected.id}/evaluation`),
  ]);
  renderQualityTrend(trend);
  renderSpcChart(spc);
  renderConfusionMatrix(evaluation.ready ? evaluation : null);
}

async function loadManufacturing() {
  const [lines, alerts] = await Promise.all([api("/api/production-lines"), api("/api/quality-alerts?limit=100")]);
  $("#station-line").innerHTML = lines.length ? lines.map(line => `<option value="${line.id}">${escapeHtml(line.code)} · ${escapeHtml(line.name)}</option>`).join("") : '<option value="">请先创建产线</option>';
  $("#alerts-body").innerHTML = alerts.length ? alerts.map(alert => `<tr><td>${new Date(alert.created_at).toLocaleString()}</td><td>${escapeHtml(alert.product_code)}</td><td>${escapeHtml(alert.rule_name)}</td><td>${alert.severity}</td><td>${escapeHtml(alert.message)}</td><td>${alert.status === "open" ? "待处理" : "已确认"}</td><td>${alert.status === "open" && hasRole("admin", "engineer", "reviewer") ? `<button class="review-button alert-ack" data-id="${alert.id}">确认</button>` : "—"}</td></tr>`).join("") : '<tr><td colspan="7">暂无质量告警</td></tr>';
  document.querySelectorAll(".alert-ack").forEach(button => button.addEventListener("click", () => acknowledgeAlert(button.dataset.id)));
}

async function acknowledgeAlert(id) {
  const note = window.prompt("请输入告警处置说明", "已确认并安排排查") || "";
  await api(`/api/quality-alerts/${id}/acknowledge`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({note})});
  toast("质量告警已确认");
  await Promise.all([loadManufacturing(), loadDashboard()]);
}

async function loadModels() {
  if (!state.selected) return;
  const models = await api(`/api/products/${state.selected.id}/models`);
  const active = models.find(model => model.is_active);
  $("#model-status").textContent = active
    ? `当前模型 v${active.version} · ${active.backend}`
    : models.length ? `最新模型状态：${models[0].status}` : "尚未训练正式模型";
}

async function loadTrainingDatasets() {
  if (!state.selected) return;
  const datasets = await api(`/api/products/${state.selected.id}/datasets`);
  $("#training-dataset").innerHTML = '<option value="">当前参考图（未冻结）</option>' + datasets.map(item => `<option value="${item.dataset_version_id}">${escapeHtml(item.name)} v${item.version} · ${item.sample_count} 张</option>`).join("");
}

function metricValue(model, key) {
  const metrics = model.metrics?.release_gate?.metrics || model.metrics || {};
  const value = metrics[key];
  return value === undefined || value === null ? "—" : Number(value).toFixed(3);
}

async function loadExperimentCenter() {
  const productId = Number($("#experiment-product-select").value || state.selected?.id || 0);
  if (!productId) return;
  $("#experiment-product-select").value = String(productId);
  const [datasets, models, allExperiments] = await Promise.all([
    api(`/api/products/${productId}/datasets`),
    api(`/api/products/${productId}/models`),
    api("/api/experiments?limit=500"),
  ]);
  const experiments = allExperiments.filter(item => Number(item.product_id) === productId);
  $("#lineage-dataset-count").textContent = datasets.length;
  $("#lineage-experiment-count").textContent = experiments.length;
  $("#lineage-passed-count").textContent = datasets.filter(item => item.stats?.quality_gate?.passed).length;
  const active = models.find(item => item.is_active);
  $("#lineage-active-model").textContent = active ? `v${active.version} ${active.backend}` : "—";
  $("#dataset-version-grid").innerHTML = datasets.length ? datasets.map(item => {
    const gate = item.stats?.quality_gate || {};
    return `<article class="dataset-card"><div><span class="tag ${gate.passed ? "normal" : "anomaly"}">${gate.passed ? "门禁通过" : "门禁阻断"}</span><strong>${escapeHtml(item.name)} · v${item.version}</strong></div><p>${item.sample_count} 个样本 · ${escapeHtml(item.status)} · ${new Date(item.created_at).toLocaleString()}</p><code title="完整清单哈希">${escapeHtml(item.manifest_sha256)}</code><small>错误 ${gate.errors?.length || 0} · 警告 ${gate.warnings?.length || 0} · 创建人 ${escapeHtml(item.created_by)}</small></article>`;
  }).join("") : '<p class="muted">尚未冻结数据版本。</p>';
  $("#model-comparison-body").innerHTML = models.length ? models.map(model => `<tr><td>v${model.version}${model.is_active ? ' · ACTIVE' : ''}</td><td>${escapeHtml(model.backend)}</td><td>${model.dataset_version_id || "未冻结"}</td><td>${escapeHtml(model.status)}</td><td>${escapeHtml(model.gate_status || "pending")}</td><td>${metricValue(model, "f1")}</td><td>${metricValue(model, "recall")}</td><td>${model.parameters?.tiling_enabled ? `${model.parameters.tile_size}px / ${Math.round(model.parameters.tile_overlap * 100)}% / B${model.parameters.tile_batch_size || 8}` : "关闭"}</td></tr>`).join("") : '<tr><td colspan="8">暂无模型版本</td></tr>';
  $("#experiment-lineage-body").innerHTML = experiments.length ? experiments.map(item => `<tr><td>${new Date(item.created_at).toLocaleString()}</td><td>#${item.id}</td><td>${item.model_version ? `v${item.model_version} ${escapeHtml(item.backend)}` : escapeHtml(item.backend)}</td><td><code>${escapeHtml((item.dataset_manifest_sha256 || "未冻结").slice(0, 16))}</code></td><td><code>${escapeHtml(item.code_version || "unknown")}</code></td><td><span class="tag ${item.status === "completed" ? "normal" : "anomaly"}">${escapeHtml(item.status)}</span></td><td><code>${escapeHtml(JSON.stringify(item.parameters || {}))}</code></td></tr>`).join("") : '<tr><td colspan="7">暂无实验记录</td></tr>';
}

async function loadEvaluation() {
  if (!state.selected) return;
  const result = await api(`/api/products/${state.selected.id}/evaluation`);
  if (!result.ready) {
    $("#evaluation-message").textContent = result.message;
    ["#eval-precision", "#eval-recall", "#eval-f1", "#eval-fpr"].forEach(id => $(id).textContent = "—");
    renderConfusionMatrix(null);
    return;
  }
  $("#evaluation-message").textContent = `${result.sample_count} 个已复核样本 · 阈值 ${result.threshold}`;
  $("#eval-precision").textContent = result.precision.toFixed(2);
  $("#eval-recall").textContent = result.recall.toFixed(2);
  $("#eval-f1").textContent = result.f1.toFixed(2);
  $("#eval-fpr").textContent = result.false_positive_rate.toFixed(2);
  renderConfusionMatrix(result);
}

async function loadRecords() {
  const params = new URLSearchParams({limit: "30"});
  new FormData($("#record-filters")).forEach((value, key) => { if (value) params.set(key, value); });
  const records = await api(`/api/inspections?${params}`);
  const body = $("#records-body");
  if (!records.length) {
    body.innerHTML = '<tr><td colspan="7">暂无检测记录</td></tr>';
    return;
  }
  body.innerHTML = records.map(record => `
    <tr>
      <td>${new Date(record.created_at).toLocaleString()}</td>
      <td>${escapeHtml(record.product_code)}</td>
      <td>${escapeHtml(record.filename)}</td>
      <td>${record.anomaly_score.toFixed(4)}</td>
      <td><span class="tag ${record.decision}">${record.decision === "anomaly" ? "异常" : "正常"}</span></td>
      <td>${record.review_status === "reviewed" ? "已复核" : "待复核"}</td>
      <td><button class="review-button detail-button" data-id="${record.id}">详情</button> ${hasRole("admin", "engineer", "reviewer") ? `<button class="review-button review-action" data-id="${record.id}" data-decision="${record.decision}">复核</button>` : ""}</td>
    </tr>`).join("");
  body.querySelectorAll(".review-action").forEach(button => {
    button.addEventListener("click", () => reviewRecord(button));
  });
  body.querySelectorAll(".detail-button").forEach(button => button.addEventListener("click", () => showInspectionDetail(button.dataset.id)));
}

async function showInspectionDetail(id) {
  const record = await api(`/api/inspections/${id}`);
  $("#detail-title").textContent = `${record.product_code} · #${record.id}`;
  $("#inspection-detail-content").innerHTML = `<div class="detail-grid"><div><span>系统判定</span><strong>${record.decision === "anomaly" ? "异常" : "正常"}</strong></div><div><span>异常分数</span><strong>${record.anomaly_score.toFixed(4)}</strong></div><div><span>批次 / SN</span><strong>${escapeHtml(record.lot_number || "—")} / ${escapeHtml(record.serial_number || "—")}</strong></div><div><span>产线 / 工位</span><strong>${escapeHtml(record.line_code || "—")} / ${escapeHtml(record.station_code || "—")}</strong></div><div><span>推理耗时</span><strong>${record.latency_ms.toFixed(1)} ms</strong></div><div><span>复核状态</span><strong>${record.review_status}</strong></div><div><span>缺陷区域</span><strong>${record.defect_regions.length}</strong></div><div><span>采集质量</span><strong>${record.quality.accepted ? "合格" : "需检查"}</strong></div></div><div class="detail-images"><img src="${record.image_url}" alt="检测原图"><img src="${record.heatmap_url}" alt="异常热力图"></div>`;
  $("#inspection-detail-dialog").showModal();
}

async function reviewRecord(button) {
  const current = button.dataset.decision;
  const answer = window.prompt("输入复核结果：normal 或 anomaly", current);
  if (!answer || !["normal", "anomaly"].includes(answer)) return;
  const note = window.prompt("复核备注（可选）", "") || "";
  await api(`/api/inspections/${button.dataset.id}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision: answer, note }),
  });
  toast("复核结果已保存");
  await Promise.all([loadRecords(), loadDashboard()]);
}

document.querySelectorAll(".nav-button").forEach(button => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
$("#record-filters").addEventListener("submit", event => { event.preventDefault(); loadRecords().catch(error => toast(error.message, true)); });
$("#record-filters").addEventListener("reset", () => window.setTimeout(() => loadRecords().catch(error => toast(error.message, true)), 0));
$("#close-inspection-detail").addEventListener("click", () => $("#inspection-detail-dialog").close());

$("#open-product-dialog").addEventListener("click", () => $("#product-dialog").showModal());
$("#close-dialog").addEventListener("click", () => $("#product-dialog").close());
$("#refresh-products").addEventListener("click", () => loadProducts());

$("#product-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  values.threshold = Number(values.threshold);
  try {
    const created = await api("/api/products", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    event.target.reset();
    $("#product-dialog").close();
    toast("产品型号创建成功");
    await Promise.all([loadProducts(created.id), loadDashboard()]);
    switchView("inspection");
  } catch (error) { toast(error.message, true); }
});

$("#reference-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selected) return;
  const files = $("#reference-files").files;
  const form = new FormData();
  Array.from(files).forEach(file => form.append("files", file));
  try {
    await api(`/api/products/${state.selected.id}/references`, { method: "POST", body: form });
    toast(`成功上传 ${files.length} 张正常参考图`);
    $("#reference-files").value = "";
    await selectProduct(state.selected.id);
  } catch (error) { toast(error.message, true); }
});

$("#inspection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selected) return;
  const file = $("#inspection-file").files[0];
  const form = new FormData();
  form.append("file", file);
  form.append("lot_number", $("#inspection-lot").value);
  form.append("serial_number", $("#inspection-serial").value);
  form.append("station_code", $("#inspection-station").value);
  form.append("shift", $("#inspection-shift").value);
  const submitButton = event.target.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = "AI 检测中…";
  $("#inspection-status").textContent = "正在进行图像质量分析与异常推理";
  try {
    const result = await api(`/api/products/${state.selected.id}/inspect`, { method: "POST", body: form });
    $("#result-decision").textContent = result.decision === "anomaly" ? "检测到异常" : "判定正常";
    $("#result-decision").className = `status-badge ${result.decision}`;
    $("#result-score").textContent = result.anomaly_score.toFixed(4);
    $("#result-threshold").textContent = Number(state.selected.threshold).toFixed(2);
    $("#result-quality").textContent = result.quality.accepted
      ? "合格"
      : result.quality.warnings.join("、");
    $("#result-image").src = `${result.image_url}?t=${Date.now()}`;
    $("#result-heatmap").src = `${result.heatmap_url}?t=${Date.now()}`;
    $("#result-view").classList.remove("hidden");
    $("#inspection-status").textContent = `检测完成 · ${result.latency_ms.toFixed(0)} ms`;
    toast("检测完成");
    await Promise.all([loadRecords(), loadDashboard(), loadSpc(), loadManufacturing(), loadQualityAnalytics()]);
  } catch (error) {
    $("#inspection-status").textContent = error.message;
    toast(error.message, true);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "开始检测";
  }
});

$("#training-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selected) return;
  try {
    const result = await api(`/api/products/${state.selected.id}/models/train`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        backend: $("#training-backend").value,
        dataset_version_id: Number($("#training-dataset").value) || null,
        tiling_enabled: $("#training-tiling").checked,
        tile_size: Number($("#training-tile-size").value),
        tile_overlap: Number($("#training-tile-overlap").value),
        tile_batch_size: Number($("#training-batch-size").value),
      }),
    });
    $("#model-status").textContent = "模型训练中…";
    toast(`训练任务 #${result.job_id} 已创建`);
    await pollTrainingJob(result.job_id);
  } catch (error) { toast(error.message, true); }
});

async function pollTrainingJob(jobId) {
  for (let index = 0; index < 90; index += 1) {
    const job = await api(`/api/training-jobs/${jobId}`);
    $("#model-status").textContent = `${job.message} · ${job.progress}%`;
    if (job.status === "completed") {
      toast("模型训练完成并已自动发布");
      await loadModels();
      return;
    }
    if (job.status === "failed") {
      toast(job.message, true);
      return;
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }
  toast("训练仍在后台执行，请稍后刷新", true);
}

$("#batch-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selected) return;
  const files = $("#batch-files").files;
  const form = new FormData();
  form.append("lot_number", $("#batch-lot").value);
  form.append("station_code", $("#batch-station").value);
  form.append("shift", $("#batch-shift").value);
  Array.from(files).forEach(file => form.append("files", file));
  $("#batch-status").textContent = `正在处理 ${files.length} 张图片…`;
  try {
    const result = await api(`/api/products/${state.selected.id}/batch-inspect`, {
      method: "POST", body: form,
    });
    $("#batch-status").textContent = `完成 ${result.processed} 张，失败 ${result.failed} 张`;
    toast("批次检测完成");
    await Promise.all([loadRecords(), loadDashboard()]);
  } catch (error) { toast(error.message, true); }
});

$("#save-threshold").addEventListener("click", async () => {
  if (!state.selected) return;
  try {
    const threshold = Number($("#threshold").value);
    await api(`/api/products/${state.selected.id}/threshold`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threshold }),
    });
    state.selected.threshold = threshold;
    toast("判定阈值已更新");
  } catch (error) { toast(error.message, true); }
});

$("#refresh-evaluation").addEventListener("click", async () => {
  try { await loadEvaluation(); toast("评估指标已刷新"); }
  catch (error) { toast(error.message, true); }
});

$("#calibrate-threshold").addEventListener("click", async () => {
  if (!state.selected) return;
  try {
    const result = await api(`/api/products/${state.selected.id}/calibrate-threshold`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apply_threshold: true }),
    });
    state.selected.threshold = result.threshold;
    $("#threshold").value = result.threshold;
    toast(`阈值已校准为 ${result.threshold}`);
    await loadEvaluation();
  } catch (error) { toast(error.message, true); }
});

$("#refresh-spc").addEventListener("click", () => loadSpc().catch(error => toast(error.message, true)));
$("#analytics-product-select").addEventListener("change", async event => {
  if (event.target.value) await selectProduct(Number(event.target.value));
});

$("#inspection-file").addEventListener("change", event => {
  const file = event.target.files[0];
  if (!file) return;
  const preview = $("#inspection-preview");
  const image = preview.querySelector("img");
  if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
  image.dataset.objectUrl = URL.createObjectURL(file);
  image.src = image.dataset.objectUrl;
  preview.classList.remove("hidden");
});

$("#clear-inspection-file").addEventListener("click", () => {
  const image = $("#inspection-preview img");
  if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
  image.removeAttribute("src");
  image.dataset.objectUrl = "";
  $("#inspection-file").value = "";
  $("#inspection-preview").classList.add("hidden");
});

$("#heatmap-opacity").addEventListener("input", event => {
  const value = Number(event.target.value);
  $("#result-heatmap").style.opacity = String(value / 100);
  $("#heatmap-opacity-value").textContent = `${value}%`;
});

$("#login-trigger").addEventListener("click", () => $("#login-dialog").showModal());
$("#close-login").addEventListener("click", () => $("#login-dialog").close());
$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  state.apiKey = $("#login-api-key").value.trim();
  const errorElement = $("#login-error");
  errorElement.classList.add("hidden");
  try {
    await api("/api/auth/session", {method: "POST"});
    const user = await api("/api/auth/me");
    state.apiKey = "";
    updateIdentity(user);
    $("#login-api-key").value = "";
    $("#login-dialog").close();
    await loadApplicationData();
    toast(`欢迎，${user.actor}`);
  } catch (error) {
    state.apiKey = "";
    errorElement.textContent = error.message;
    errorElement.classList.remove("hidden");
  }
});

$("#logout-button").addEventListener("click", async () => {
  await api("/api/auth/session", {method: "DELETE"});
  updateIdentity(null);
  $("#login-dialog").showModal();
});

$("#line-form").addEventListener("submit", async event => {
  event.preventDefault(); const values = Object.fromEntries(new FormData(event.target));
  try { await api("/api/production-lines", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)}); event.target.reset(); toast("产线已创建"); await loadManufacturing(); } catch (error) { toast(error.message, true); }
});

$("#station-form").addEventListener("submit", async event => {
  event.preventDefault(); const values = Object.fromEntries(new FormData(event.target)); values.line_id = Number(values.line_id);
  try { await api("/api/stations", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)}); event.target.reset(); toast("工位已创建"); await loadManufacturing(); } catch (error) { toast(error.message, true); }
});

$("#rule-form").addEventListener("submit", async event => {
  event.preventDefault(); const values = Object.fromEntries(new FormData(event.target)); values.window_size = Number(values.window_size); values.max_anomaly_rate = Number(values.max_anomaly_rate); values.max_consecutive_anomalies = Number(values.max_consecutive_anomalies); values.product_id = state.selected?.id || null;
  try { await api("/api/quality-rules", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)}); toast("质量规则已创建"); } catch (error) { toast(error.message, true); }
});

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);
}

async function bootstrap() {
  try {
    updateIdentity(await api("/api/auth/me"));
  } catch (error) {
    if (error.status === 401) {
      updateIdentity(null);
      $("#login-dialog").showModal();
      return;
    }
    throw error;
  }
  await loadApplicationData();
}

async function loadApplicationData() {
  await Promise.all([loadDashboard(), loadProducts(), loadRecords(), loadManufacturing()]);
  await loadQualityAnalytics();
}

$("#experiment-product-select").addEventListener("change", () => loadExperimentCenter().catch(error => toast(error.message, true)));
$("#refresh-experiments").addEventListener("click", () => loadExperimentCenter().catch(error => toast(error.message, true)));
$("#create-snapshot").addEventListener("click", async () => {
  const productId = Number($("#experiment-product-select").value || state.selected?.id || 0);
  if (!productId) return toast("请先选择产品", true);
  try {
    const result = await api(`/api/products/${productId}/datasets/snapshot`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: $("#snapshot-name").value, description: "前端创建的受控正常样本快照"})});
    toast(`数据版本 v${result.version} 已冻结`);
    await loadExperimentCenter();
    if (state.selected?.id === productId) await loadTrainingDatasets();
  } catch (error) { toast(error.message, true); }
});

if (window.location.protocol === "file:") {
  $("#runtime-warning").classList.remove("hidden");
} else {
  bootstrap().catch(error => toast(error.message, true));
}
