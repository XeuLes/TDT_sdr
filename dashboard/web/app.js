// Layout, spectrum, waterfall and map rendering derived from SHARK Match RX (6835547, MIT).
// The RM_sdr adapter below feeds these views from one E200 and the existing decoder.
let activePage = location.hash === "#radio" ? "radio" : "radar";
let radarLatest = null;
let radarDirty = true;
let selectedRobot = null;
let radarMapOwnSide = "red";
const radarDisplayPositions = {};
const radarHitTargets = [];
const spectrumTarget = { rx1: null, rx2: null };
const spectrumSmooth = { rx1: null, rx2: null };
const spectrumArrivals = { rx1: [], rx2: [] };
const waterfallQueues = { rx1: [], rx2: [] };
const waterfallStamps = { rx1: null, rx2: null };
const displayPerf = { droppedWaterfallRows: 0 };
const branchLabels = { info: "信息波", jam1: "一级干扰", jam2: "二级干扰", jam3: "三级干扰" };
const positionFields = ["hero", "engineer", "standard_3", "standard_4", "aerial", "sentry"];
const buffFields = ["hero", "engineer", "standard_3", "standard_4", "sentry"];
let spectrumDirty = true;
let lastSpectrumRenderMs = 0;
let lastDetailMs = -Infinity;
let snapshot = null;
let runId = null;
let rxChangePending = false;
let rxChangeError = "";
const robotLabels = {
  opponent_hero: "英雄", opponent_engineer: "工程", opponent_infantry_3: "步兵3",
  opponent_infantry_4: "步兵4", opponent_aerial: "空中", opponent_sentry: "哨兵",
  hero: "英雄", engineer: "工程", standard_3: "步兵3", standard_4: "步兵4",
  aerial: "空中", sentry: "哨兵", reserved: "保留",
};

const mainStatusLabels = {
  alive: "存活",
  destroyed: "战亡",
  invincible_not_weakened: "无敌但不虚弱",
  invincible_weakened: "无敌且虚弱",
};

const sentryModeLabels = {
  offensive: "进攻姿态",
  defensive: "防御姿态",
  mobile: "移动姿态",
  enhanced_offensive: "强化进攻姿态",
  enhanced_defensive: "强化防御姿态",
  enhanced_mobile: "强化移动姿态",
};

const broadcastFrames = [
  ["0x0A01", "坐标"],
  ["0x0A02", "血量"],
  ["0x0A03", "发弹量"],
  ["0x0A04", "经济"],
  ["0x0A05", "增益状态"],
];

const gameProgressLabels = ["未开始", "准备阶段", "15 秒自检", "5 秒倒计时", "比赛进行中", "比赛结算"];
const dartTargets = ["前哨站", "基地固定目标", "基地随机固定目标", "基地随机移动目标", "基地末端移动目标"];
const INPUT_TIMEOUT_SEC = 3;

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function fmtAge(timestamp) {
  if (!timestamp) return "—";
  const age = Math.max(0, Date.now() / 1000 - Number(timestamp));
  if (age < 1) return "刚刚";
  if (age < 60) return `${age.toFixed(0)} 秒前`;
  return `${(age / 60).toFixed(1)} 分钟前`;
}

function fmtRecentArrival(timestamp, now = Date.now() / 1000) {
  if (!timestamp) return "未到达";
  const age = Math.max(0, now - Number(timestamp));
  if (age < 0.05) return "刚刚";
  if (age < 10) return `${age.toFixed(1)} 秒前`;
  if (age < 60) return `${age.toFixed(0)} 秒前`;
  return `${(age / 60).toFixed(1)} 分钟前`;
}

function fmtHz(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (Math.abs(number) >= 1e6) return `${(number / 1e6).toFixed(3)} MHz`;
  if (Math.abs(number) >= 1e3) return `${(number / 1e3).toFixed(1)} kHz`;
  return `${number.toFixed(0)} Hz`;
}

function renderKeyValues(id, values) {
  $(id).innerHTML = values.map(([label, value]) => `<span>${label}</span><strong>${escapeHtml(value)}</strong>`).join("");
}

function get(obj, path, fallback = undefined) {
  let cur = obj;
  for (const key of path) {
    if (!cur || typeof cur !== "object" || !(key in cur)) return fallback;
    cur = cur[key];
  }
  return cur;
}

function stateClass(value) {
  return ["ok", "warn", "bad", "unknown"].includes(value) ? value : "unknown";
}

function stateLabel(value) {
  return { ok: "正常", warn: "WARN", bad: "ERROR", unknown: "待检测" }[stateClass(value)];
}

function drawSpectrum() {
  const canvas = $("spectrumCanvas");
  const ctx = canvas.getContext("2d");
  // 高 DPI 全尺寸 Canvas 是页面 GPU/合成开销的主要来源之一；频谱线无需
  // 使用 3x/4x 像素密度，1.5x 已足够清晰。
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const cssW = canvas.clientWidth || 640;
  const cssH = canvas.clientHeight || 260;
  const pxW = Math.round(cssW * dpr);
  const pxH = Math.round(cssH * dpr);
  if (canvas.width !== pxW || canvas.height !== pxH) { canvas.width = pxW; canvas.height = pxH; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const padL = 46, padR = 14, padT = 16, padB = 30;
  const plotW = Math.max(10, cssW - padL - padR);
  const plotH = Math.max(10, cssH - padT - padB);

  const datasets = [["rx1", "#3a8fd8", $("spectrumPrimary").textContent], ["rx2", "#2fbf8f", $("spectrumJamName").textContent]];

  // 汇总频率范围与 dB 范围（两路合并，用真实 Hz 频率轴）；读取平滑后的显示缓冲。
  let fMin = Infinity, fMax = -Infinity, dbMin = Infinity, dbMax = -Infinity, latest = 0;
  const traces = [];
  for (const [role, color, label] of datasets) {
    const sm = spectrumSmooth[role];
    if (!sm || !sm.freqs || sm.freqs.length < 2) continue;
    latest = Math.max(latest, Number(sm.timestamp || 0));
    const freqs = sm.freqs;
    const powers = sm.power;
    for (const f of freqs) { if (f < fMin) fMin = f; if (f > fMax) fMax = f; }
    for (const p of powers) { const v = Number(p); if (Number.isFinite(v)) { if (v < dbMin) dbMin = v; if (v > dbMax) dbMax = v; } }
    traces.push({ role, color, label, freqs, powers, spec: sm.meta });
  }

  if (!traces.length) {
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.font = "13px Segoe UI, sans-serif";
    ctx.fillText("等待频谱数据…", padL + 8, padT + plotH / 2);
    $("spectrumAge").textContent = "无数据";
    return;
  }

  // dB 轴自适应留边并对齐到 10
  dbMax = Math.min(0, Math.ceil((dbMax + 5) / 10) * 10);
  dbMin = Math.floor((dbMin - 5) / 10) * 10;
  if (dbMax - dbMin < 30) dbMin = dbMax - 30;
  const xOf = (f) => padL + ((f - fMin) / Math.max(1, fMax - fMin)) * plotW;
  const yOf = (db) => padT + ((dbMax - Math.min(dbMax, Math.max(dbMin, db))) / (dbMax - dbMin)) * plotH;

  // 网格 + dB 标签
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.fillStyle = "rgba(255,255,255,0.45)";
  ctx.lineWidth = 1;
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("dBFS", 6, padT - 5);
  const dbSteps = 4;
  for (let i = 0; i <= dbSteps; i += 1) {
    const db = dbMax - ((dbMax - dbMin) * i) / dbSteps;
    const y = yOf(db);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.fillText(`${db}`, 6, y + 3);
  }
  // 频率轴标签（MHz）
  ctx.textAlign = "center";
  const fSteps = 5;
  for (let i = 0; i <= fSteps; i += 1) {
    const f = fMin + ((fMax - fMin) * i) / fSteps;
    const x = xOf(f);
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + plotH); ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.fillText(`${(f / 1e6).toFixed(2)}`, x, cssH - 16);
  }
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.fillText("MHz", padL + plotW / 2, cssH - 3);
  ctx.textAlign = "left";

  // 画每条谱线（填充 + 描边 + 峰值标记）
  for (const t of traces) {
    ctx.beginPath();
    t.freqs.forEach((f, i) => {
      const x = xOf(f), y = yOf(Number(t.powers[i]));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    // 填充到底
    ctx.lineTo(xOf(t.freqs[t.freqs.length - 1]), padT + plotH);
    ctx.lineTo(xOf(t.freqs[0]), padT + plotH);
    ctx.closePath();
    ctx.fillStyle = t.color + "22";
    ctx.fill();
    // 描边
    ctx.beginPath();
    t.freqs.forEach((f, i) => {
      const x = xOf(f), y = yOf(Number(t.powers[i]));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = t.color;
    ctx.lineWidth = 1.6;
    ctx.stroke();
    // 峰值竖线 + 读数
    const peakF = Number(t.spec.peak_frequency_hz);
    const peakDb = Number(t.spec.peak_dbfs);
    if (Number.isFinite(peakF) && peakF >= fMin && peakF <= fMax) {
      const px = xOf(peakF);
      ctx.strokeStyle = t.color;
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(px, padT); ctx.lineTo(px, padT + plotH); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = t.color;
      ctx.font = "11px Segoe UI, sans-serif";
      const txt = `${t.label} ${(peakF / 1e6).toFixed(3)}MHz ${Number.isFinite(peakDb) ? peakDb.toFixed(0) : "?"} dBFS`;
      ctx.fillText(txt, Math.min(px + 4, padL + plotW - 130), padT + 12 + (t.role === "rx2" ? 14 : 0));
    }
  }

  $("spectrumAge").textContent = latest ? `更新 ${fmtAge(latest)}` : "无数据";
}

const waterfallLut = (() => {
  const stops = [
    [0.00, [2, 5, 12]],
    [0.18, [18, 31, 83]],
    [0.38, [18, 112, 155]],
    [0.58, [36, 190, 128]],
    [0.76, [235, 218, 72]],
    [0.90, [239, 92, 42]],
    [1.00, [255, 244, 225]],
  ];
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    let left = stops[0], right = stops[stops.length - 1];
    for (let j = 1; j < stops.length; j += 1) {
      if (t <= stops[j][0]) { left = stops[j - 1]; right = stops[j]; break; }
    }
    const u = Math.max(0, Math.min(1, (t - left[0]) / Math.max(1e-9, right[0] - left[0])));
    for (let c = 0; c < 3; c += 1) lut[i * 3 + c] = Math.round(left[1][c] + (right[1][c] - left[1][c]) * u);
  }
  return lut;
})();

// 绝对 dBFS 色标：两路接收机及 raw/filtered 观察点严格共用，颜色可直接横向比较。
const WATERFALL_DBFS_MIN = -120;
const WATERFALL_DBFS_MAX = 0;

function enqueueWaterfall(role, target) {
  const queue = waterfallQueues[role];
  queue.push({
    freqs: target.freqs.slice(),
    power: target.power.slice(),
    meta: target.meta,
  });
  while (queue.length > 8) {
    queue.shift();
    displayPerf.droppedWaterfallRows += 1;
  }
}

function drawWaterfallRow(role, row) {
  const canvas = $(`${role}Waterfall`);
  if (!canvas || !row.power.length) return;
  const width = Math.max(64, Math.round(canvas.clientWidth || 480));
  const height = Math.max(48, Math.round(canvas.clientHeight || 120));
  const ctx = canvas.getContext("2d", { alpha: false });
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
    ctx.fillStyle = "#02060a";
    ctx.fillRect(0, 0, width, height);
  } else if (height > 1) {
    ctx.drawImage(canvas, 0, 1, width, height - 1, 0, 0, width, height - 1);
  }

  const image = ctx.createImageData(width, 1);
  for (let x = 0; x < width; x += 1) {
    const index = Math.min(row.power.length - 1, Math.floor((x / Math.max(1, width - 1)) * row.power.length));
    const value = Number(row.power[index]);
    const normalized = Number.isFinite(value)
      ? Math.max(0, Math.min(1, (value - WATERFALL_DBFS_MIN) / (WATERFALL_DBFS_MAX - WATERFALL_DBFS_MIN)))
      : 0;
    const color = Math.round(normalized * 255);
    image.data[x * 4] = waterfallLut[color * 3];
    image.data[x * 4 + 1] = waterfallLut[color * 3 + 1];
    image.data[x * 4 + 2] = waterfallLut[color * 3 + 2];
    image.data[x * 4 + 3] = 255;
  }
  ctx.putImageData(image, 0, height - 1);

  const first = row.freqs[0], last = row.freqs[row.freqs.length - 1];
  $(`${role}WaterfallRange`).textContent = `${(first / 1e6).toFixed(3)} — ${(last / 1e6).toFixed(3)} MHz · ${WATERFALL_DBFS_MIN}…${WATERFALL_DBFS_MAX} dBFS`;
}

function flushWaterfalls() {
  for (const role of ["rx1", "rx2"]) {
    const queue = waterfallQueues[role];
    while (queue.length) drawWaterfallRow(role, queue.shift());
  }
}

function cell(label, value) {
  return `<div class="decode-cell"><div class="dc-label">${escapeHtml(label)}</div><div class="dc-value">${escapeHtml(value)}</div></div>`;
}

function robotGrid(map, fmt = (v) => v) {
  if (!map || typeof map !== "object") return "";
  return `<div class="decode-grid">${Object.entries(map)
    .map(([key, value]) => cell(robotLabels[key] || key, fmt(value)))
    .join("")}</div>`;
}

// 把单帧 parsed 渲染成对应命令的业务区块（不含原始 hex，交给外层统一折叠）。
function sectionForFrame(cmd, entry) {
  const frame = entry.frame || {};
  const parsed = frame.parsed || {};
  const age = fmtAge(entry.timestamp);
  const head = (title) => `<h3>${title} <span class="sec-age">· ${age}</span></h3>`;

  // 0x0A06 密钥（干扰波）
  const password = get(parsed, ["radar_command", "password"], get(parsed, ["radar_decision_sync", "password"], parsed.password));
  if (password) {
    return `<div class="decode-section"><div class="password-hero"><span class="ph-label">0x0A06 密钥 · ${age}</span><span class="ph-value">${escapeHtml(password)}</span></div></div>`;
  }
  const positions = parsed.positions_cm || parsed.official_positions_cm;
  if (positions) {
    return `<div class="decode-section">${head("坐标 (cm)")}${robotGrid(positions, (p) => `X ${p?.x ?? "—"} / Y ${p?.y ?? "—"}`)}</div>`;
  }
  const hp = parsed.hp || parsed.official_hp;
  if (hp) return `<div class="decode-section">${head("血量")}${robotGrid(hp)}</div>`;
  const bullets = parsed.bullet_allowance || parsed.official_bullet_allowance;
  if (bullets) return `<div class="decode-section">${head("发弹余量")}${robotGrid(bullets)}</div>`;
  if (parsed.remaining_coins != null || parsed.total_coins != null) {
    return `<div class="decode-section">${head("经济")}<div class="decode-grid">${
      cell("剩余金币", parsed.remaining_coins ?? "—") +
      cell("总金币", parsed.total_coins ?? "—") +
      cell("占领位图", parsed.occupation_bits != null ? `0x${Number(parsed.occupation_bits).toString(16)}` : "—")
    }</div></div>`;
  }
  const buffs = parsed.buff_status;
  if (buffs && typeof buffs === "object") {
    const rows = Object.entries(buffs)
      .map(([key, b]) => cell(robotLabels[key] || key, `回血${b.hp_recovery_percent ?? 0}% 攻${b.attack_percent ?? 0}% 防${b.defense_percent ?? 0}%`))
      .join("");
    const sentryName = sentryModeLabels[parsed.sentry_mode_name] || parsed.sentry_mode_name || "未知";
    const sentry = parsed.sentry_mode != null ? cell("哨兵姿态", `${parsed.sentry_mode} · ${sentryName}`) : "";
    const mainStatuses = parsed.robot_main_status || {};
    const mainStatusNames = parsed.robot_main_status_names || {};
    const statusRows = Object.entries(mainStatuses)
      .map(([key, value]) => {
        const statusName = mainStatusLabels[mainStatusNames[key]] || mainStatusNames[key] || "未知";
        return cell(`${robotLabels[key] || key}主要状态`, `${value} · ${statusName}`);
      })
      .join("");
    return `<div class="decode-section">${head("增益与主要状态")}<div class="decode-grid">${rows}${sentry}${statusRows}</div></div>`;
  }
  // 未识别命令：显示命令号 + 原始数据
  return `<div class="decode-section">${head(cmd)}<div class="decode-grid">${cell("数据", frame.data_hex || "—")}</div></div>`;
}

// 渲染一路的所有命令帧（每命令最近一帧），按命令号排序，稳定同时展示。
function renderDecodedRole(container, byCmd, emptyText) {
  const cmds = Object.keys(byCmd).sort();
  if (!cmds.length) {
    container.innerHTML = `<p class="empty">${escapeHtml(emptyText)}</p>`;
    return;
  }
  // 密钥优先置顶
  cmds.sort((a, b) => (b === "0x0A06" ? 1 : 0) - (a === "0x0A06" ? 1 : 0));
  const sections = cmds.map((cmd) => sectionForFrame(cmd, byCmd[cmd]));
  const lastEntry = cmds.map((c) => byCmd[c]).sort((a, b) => b.timestamp - a.timestamp)[0];
  const rawHex = lastEntry?.frame?.raw_hex || lastEntry?.frame?.data_hex;
  const rawBlock = rawHex
    ? `<details class="raw-frame"><summary>最近有效帧的数据段</summary><code>${escapeHtml(rawHex)}</code></details>`
    : "";
  container.innerHTML = sections.join("") + rawBlock;
}

const radarRoleIds = ["1", "2", "3", "4", "6", "7"];
const radarMarkIds = [
  "opponent_hero", "opponent_engineer", "opponent_infantry_3",
  "opponent_infantry_4", "opponent_aerial", "opponent_sentry",
];

function normalizeRadarSide(value) {
  if (typeof value !== "string") return null;
  const side = value.toLowerCase();
  if (side === "b" || side === "blue") return "blue";
  return side === "r" || side === "red" ? "red" : null;
}

function radarRobotOrderForSide(side) {
  const allyPrefix = normalizeRadarSide(side) === "red" ? "R" : "B";
  const opponentPrefix = allyPrefix === "R" ? "B" : "R";
  return [opponentPrefix, allyPrefix].flatMap((prefix) => radarRoleIds.map((role) => `${prefix}${role}`));
}

function updateRadarMapOrientation(side) {
  const cleanSide = normalizeRadarSide(side) || "red";
  radarMapOwnSide = cleanSide;
  const label = cleanSide === "red" ? "红方" : "蓝方";
  const image = $("radarMapImage");
  const expectedSource = cleanSide === "red" ? "/field-red.jpg" : "/field-blue.jpg";
  if (image && image.getAttribute("src") !== expectedSource) image.setAttribute("src", expectedSource);
  if (image) image.alt = `RoboMaster 2026 赛场地图，${label}在下`;
  if ($("radarMapOrientation")) $("radarMapOrientation").textContent = "赛场坐标";
  fitRadarMapStage();
  return cleanSide;
}

function radarCanvasPoint(position) {
  if (!position) return null;
  return radarMapOwnSide === "red"
    ? { x: 1500 - position.y, y: 2800 - position.x }
    : { x: position.y, y: position.x };
}

function radarPosition(entry) {
  if (entry?.valid === false) return null;
  const { x, y } = entry?.position_cm || {};
  return Number.isFinite(x) && Number.isFinite(y) && (x !== 0 || y !== 0) ? { x, y } : null;
}

function coordText(position) {
  return position ? `(${position.x.toFixed(0)}, ${position.y.toFixed(0)})` : "—";
}

function ageText(age) {
  return Number.isFinite(age) ? `${age.toFixed(age < 1 ? 2 : 1)}s` : "—";
}

function drawRadarMap() {
  if (activePage !== "radar" || !radarLatest) return false;
  const fusion = radarLatest;
  const ownSide = radarMapOwnSide;
  const radarRobotOrder = radarRobotOrderForSide(ownSide);
  const robots = fusion.robots || {};
  const canvas = $("radarMapCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  radarHitTargets.length = 0;
  let moving = false;
  const colorFor = (name) => name.startsWith("R") ? "#d93d4a" : "#2c7fd1";

  for (const name of radarRobotOrder) {
    const item = robots[name] || {};
    const visual = radarPosition(item.visual);
    const radio = radarPosition(item.radio);
    const finalTarget = radarPosition(item.final);
    let final = null;
    if (finalTarget) {
      const shown = radarDisplayPositions[name] || { ...finalTarget };
      const dx = finalTarget.x - shown.x, dy = finalTarget.y - shown.y;
      if (Math.abs(dx) + Math.abs(dy) > 0.3) {
        shown.x += dx * 0.38; shown.y += dy * 0.38; moving = true;
      } else { shown.x = finalTarget.x; shown.y = finalTarget.y; }
      radarDisplayPositions[name] = shown;
      final = shown;
    } else {
      delete radarDisplayPositions[name];
    }
    const color = colorFor(name);
    if ($("showVision").checked && visual) {
      const p = radarCanvasPoint(visual);
      ctx.fillStyle = color + "38"; ctx.strokeStyle = color + "aa"; ctx.lineWidth = 5;
      ctx.fillRect(p.x - 17, p.y - 17, 34, 34); ctx.strokeRect(p.x - 17, p.y - 17, 34, 34);
      radarHitTargets.push({ name, layer: "vision", x: p.x, y: p.y });
    }
    if ($("showRadio").checked && radio && (item.radio?.fresh || item.radio?.ghost_visible)) {
      const p = radarCanvasPoint(radio);
      const expired = item.radio?.expired;
      ctx.beginPath(); ctx.moveTo(p.x, p.y - 22); ctx.lineTo(p.x + 21, p.y + 18); ctx.lineTo(p.x - 21, p.y + 18); ctx.closePath();
      ctx.fillStyle = expired ? "rgba(133,91,91,.58)" : color + "70";
      ctx.strokeStyle = expired ? "#b34b4b" : color; ctx.lineWidth = 5; ctx.fill(); ctx.stroke();
      radarHitTargets.push({ name, layer: expired ? "radio-expired" : "radio", x: p.x, y: p.y });
    }
    if ($("showFinal").checked && final) {
      const p = radarCanvasPoint(final);
      if (selectedRobot === name) { ctx.beginPath(); ctx.arc(p.x, p.y, 34, 0, Math.PI * 2); ctx.strokeStyle = "#ffd54f"; ctx.lineWidth = 8; ctx.stroke(); }
      ctx.beginPath(); ctx.arc(p.x, p.y, 21, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 5; ctx.stroke();
      radarHitTargets.unshift({ name, layer: "final", x: p.x, y: p.y });
    }
    const labelPosition = ($("showFinal").checked && final) || ($("showVision").checked && visual) ||
      ($("showRadio").checked && (item.radio?.fresh || item.radio?.ghost_visible) && radio);
    if ($("showLabels").checked && labelPosition) {
      const p = radarCanvasPoint(labelPosition);
      ctx.font = "bold 48px Segoe UI, Microsoft YaHei, sans-serif";
      ctx.lineWidth = 10; ctx.strokeStyle = "rgba(255,255,255,.94)"; ctx.strokeText(name, p.x + 34, p.y - 10);
      ctx.fillStyle = color; ctx.fillText(name, p.x + 34, p.y - 10);
      ctx.font = "32px Segoe UI, Microsoft YaHei, sans-serif";
      ctx.lineWidth = 7; ctx.strokeStyle = "rgba(255,255,255,.9)";
      const coordinates = `${labelPosition.x.toFixed(0)},${labelPosition.y.toFixed(0)}`;
      ctx.strokeText(coordinates, p.x + 34, p.y + 29);
      ctx.fillStyle = "#17212b"; ctx.fillText(coordinates, p.x + 34, p.y + 29);
    }
  }
  return moving;
}

const RADAR_MAP_ASPECT = 1500 / 2800;
function fitRadarMapStage() {
  const viewport = $("radarMapViewport");
  const stage = $("radarMapStage");
  if (!viewport || !stage) return;
  const availableWidth = viewport.clientWidth;
  const availableHeight = viewport.clientHeight;
  if (availableWidth <= 0 || availableHeight <= 0) return;
  const width = Math.min(availableWidth, availableHeight * RADAR_MAP_ASPECT);
  const height = width / RADAR_MAP_ASPECT;
  stage.style.width = `${width}px`;
  stage.style.height = `${height}px`;
}

function stepSmoothing() {
  const EASE = 0.55;
  let moving = false;
  for (const role of ["rx1", "rx2"]) {
    const tgt = spectrumTarget[role];
    if (!tgt) continue;
    let sm = spectrumSmooth[role];
    // 频点数组变化（等级切换/首帧）时直接对齐，避免插值错位
    if (!sm || sm.power.length !== tgt.power.length) {
      sm = { freqs: tgt.freqs.slice(), power: tgt.power.slice(), timestamp: tgt.timestamp, meta: tgt.meta };
      spectrumSmooth[role] = sm;
      moving = true;
      continue;
    }
    sm.freqs = tgt.freqs;
    sm.timestamp = tgt.timestamp;
    sm.meta = tgt.meta;
    for (let i = 0; i < sm.power.length; i += 1) {
      const d = tgt.power[i] - sm.power[i];
      if (Math.abs(d) > 0.05) { sm.power[i] += d * EASE; moving = true; }
      else sm.power[i] = tgt.power[i];
    }
  }
  return moving;
}

/* ---------- RM_sdr snapshot adapter ---------- */

function fields(names, values, format = (value) => value) {
  return Object.fromEntries(values.map((value, index) => [names[index], format(value)]));
}

function decodedEntries(frames) {
  const entries = {};
  for (const [command, item] of Object.entries(frames)) {
    const parsed = {};
    if (item.key !== undefined) parsed.password = item.key;
    if (item.positions_cm) parsed.positions_cm = fields(positionFields, item.positions_cm, ([x, y]) => ({ x, y }));
    if (item.hp) parsed.hp = fields(buffFields, [...item.hp.slice(0, 4), item.hp[5]]);
    if (item.ammo) parsed.bullet_allowance = fields(["hero", "standard_3", "standard_4", "aerial", "sentry"], item.ammo);
    if (item.total_gold !== undefined) {
      parsed.remaining_coins = item.remaining_gold;
      parsed.total_coins = item.total_gold;
      parsed.occupation_bits = item.flags & 0xffff;
    }
    if (item.buffs) {
      parsed.buff_status = fields(buffFields, item.buffs, ([recovery, cooling, defense, negative, attack]) => ({
        hp_recovery_percent: recovery, shooting_heat_cooling: cooling, defense_percent: defense,
        negative_defense_percent: negative, attack_percent: attack,
      }));
      parsed.sentry_mode = item.sentry_stance;
      parsed.sentry_mode_name = Object.keys(sentryModeLabels)[item.sentry_stance - 1];
      if (item.robot_states) {
        parsed.robot_main_status = fields(buffFields, item.robot_states);
        parsed.robot_main_status_names = fields(buffFields, item.robot_states, (code) => Object.keys(mainStatusLabels)[code]);
      }
    }
    entries[command] = { timestamp: item.stamp, frame: { parsed, data_hex: item.payload_hex } };
  }
  return entries;
}

function updateStatus(id, state, label = stateLabel(state)) {
  const node = $(id);
  const badge = node.classList.contains("recv-badge") ? "recv-badge" : "badge";
  node.className = badge + " " + stateClass(state);
  node.textContent = label;
}

function branchData(data, name) {
  const metadata = data.telemetry.rx?.channels || {};
  const defaults = { info: 5555, jam1: 6666, jam2: 6667 };
  return data.channels[metadata[name]?.zmq_port ?? defaults[name]];
}

function updateJamOptions(rx) {
  const keys = Object.keys(rx?.channels || {}).filter((name) => name.startsWith("jam"));
  const names = keys.length ? keys : ["jam1", "jam2"];
  const select = $("jamTap");
  const changed = [...select.options].map((option) => option.value).join(",") !== names.join(",");
  if (changed) {
    const previous = select.value;
    select.replaceChildren(...names.map((name) => new Option(branchLabels[name] || name, name)));
    select.value = names.includes(previous) ? previous : names[0];
  }
}

function cleanSpectrumHistory() {
  for (const role of ["rx1", "rx2"]) {
    spectrumTarget[role] = null;
    spectrumSmooth[role] = null;
    spectrumArrivals[role].length = 0;
    waterfallStamps[role] = null;
    waterfallQueues[role].length = 0;
    const canvas = $(role + "Waterfall");
    canvas.width = canvas.width;
    $(role + "WaterfallRange").textContent = "—";
  }
  spectrumDirty = true;
}

function spectrumTargetFor(spec, latestStamp) {
  if (!spec || latestStamp - spec.stamp >= 2 || spec.bins_dbfs.length < 2) return null;
  const bins = spec.bins_dbfs;
  const spacing = spec.rate_hz / bins.length;
  return {
    freqs: bins.map((_, index) => spec.center_hz + (index - bins.length / 2) * spacing),
    power: bins, timestamp: spec.stamp,
    meta: { peak_frequency_hz: spec.peak_hz, peak_dbfs: spec.peak_dbfs },
  };
}

function refreshSpectra(data) {
  const filtered = $("spectrumTap").value === "filtered";
  const spectra = data.telemetry.spectra || {};
  const jam = $("jamTap").value;
  const info = spectrumTargetFor(spectra.info, data.stamp);
  const interference = spectrumTargetFor(spectra[jam], data.stamp);
  const next = filtered
    ? { rx1: info, rx2: interference }
    : { rx1: spectrumTargetFor(spectra.raw, data.stamp), rx2: null };
  for (const [role, target] of Object.entries(next)) {
    const arrivals = spectrumArrivals[role];
    while (arrivals.length && arrivals[0] < data.stamp - 1) arrivals.shift();
    if (spectrumTarget[role]?.timestamp === target?.timestamp) continue;
    spectrumTarget[role] = target;
    if (target) arrivals.push(target.timestamp);
    else spectrumSmooth[role] = null;
    spectrumDirty = true;
  }
  for (const [role, target] of [["rx1", info], ["rx2", interference]]) {
    if (!target || waterfallStamps[role] === target.timestamp) continue;
    waterfallStamps[role] = target.timestamp;
    enqueueWaterfall(role, target);
  }
  $("spectrumPrimary").textContent = filtered ? "信息波" : "E200 宽带 IQ";
  $("spectrumInterference").hidden = !filtered;
  $("spectrumJamName").textContent = branchLabels[jam] || "干扰波";
  $("jamWaterfallLabel").textContent = branchLabels[jam] || "干扰波";
  const rates = [["rx1", $("spectrumPrimary").textContent], ["rx2", $("spectrumJamName").textContent]]
    .filter(([role]) => next[role])
    .map(([role, label]) => label + " " + spectrumArrivals[role].length.toFixed(1) + " Hz");
  $("spectrumPerf").textContent = rates.join(" · ") || "等待 IQ";
  $("spectrumPerf").className = "perf-badge " + (rates.length ? "ok" : "unknown");
}

function renderSignalQuality(data) {
  const fresh = (entry) => entry && data.stamp - entry.stamp < 4 ? entry : {};
  const spectra = data.telemetry.spectra || {};
  const gain = fresh(data.telemetry.gain);
  const raw = fresh(spectra.raw);
  const info = fresh(spectra.info);
  const jamTap = $("jamTap").value;
  const jam = fresh(spectra[jamTap]);
  const fmt = (x, unit, digits = 1) => Number.isFinite(x) ? x.toFixed(digits) + " " + unit : "—";
  $("rx1GainValue").textContent = fmt(gain.gain_db, "dB");
  $("rxGainMode").textContent = gain.mode || (gain.error ? "回读失败" : "等待回读");
  $("rx1GainValue").title = gain.error || "E200 RX1 实际硬件增益";
  const card = (label, value, description) =>
    '<article class="q-card"><div class="q-label">' + escapeHtml(label) + '</div><div class="q-value">' +
    escapeHtml(value) + '</div><div class="q-sub">' + escapeHtml(description) + '</div></article>';
  $("qualityGrid").innerHTML =
    card("原始 IQ", fmt(raw.rms_dbfs, "dBFS"), "宽带 RMS") +
    card("削顶估计", fmt(raw.clip_percent, "%", 3), "原始 I 或 Q 达满量程 99%") +
    card("信息波分路", fmt(info.rms_dbfs, "dBFS"), "滤波后 RMS") +
    card(branchLabels[jamTap], fmt(jam.rms_dbfs, "dBFS"), "滤波后 RMS") +
    card("信息波有效帧率", data.channels[5555].frames_per_sec + " 帧/s", "最近 1 秒");
}

function renderReception(data) {
  const broadcast = data.channels[5555];
  const jam = branchData(data, $("jamTap").value);
  const frames = broadcast.frames;
  $("rx1Arrivals").innerHTML = broadcastFrames.map(([command, label]) => {
    const item = frames[command];
    const freshness = !item ? "missing" : data.stamp - item.stamp < 3 ? "fresh" : "stale";
    return '<div class="arrival-cell ' + freshness + '"><span class="arrival-label">' + command + ' · ' + label +
      '</span><div class="arrival-metrics"><span><em>最近到达</em><strong>' + fmtRecentArrival(item?.stamp, data.stamp) +
      '</strong></span><span><em>1s 平均帧率</em><strong>' + (item?.frames_per_sec ?? 0).toFixed(1) + ' 帧/s</strong></span></div></div>';
  }).join("");
  const key = jam?.frames["0x0A06"];
  $("rx2Age").textContent = fmtRecentArrival(key?.stamp, data.stamp);
  $("rx2FrameRate").textContent = (jam?.frames_per_sec ?? 0).toFixed(1) + " 帧/s";
  const arrived = Object.keys(frames).length;
  $("rx1Cmd").textContent = `已到达 ${arrived} / ${broadcastFrames.length} 类帧`;
  $("rx1Cmd").title = "ZeroMQ 5555";
  $("rx2Cmd").textContent = jam ? `0x0A06 · ${branchLabels[$("jamTap").value]} · ZeroMQ ${jam.port}` : "等待分路";
  for (const [id, channel] of [["rx1Status", broadcast], ["rx2Status", jam]]) {
    const received = Object.values(channel?.frames || {});
    const live = received.some((frame) => data.stamp - frame.stamp < 3);
    if (live) updateStatus(id, "ok", "接收中");
    else updateStatus(id, received.length ? "warn" : "unknown", received.length ? "接收已停止" : "等待帧");
  }

  for (const [id, source, empty] of [
    ["rx1Decoded", broadcast, "尚未解析到信息波应用帧。"],
    ["rx2Decoded", jam, "尚未解析到干扰波密钥帧。"],
  ]) {
    const root = $(id);
    const open = root.querySelector("details.raw-frame")?.open;
    renderDecodedRole(root, decodedEntries(source?.frames || {}), empty);
    const raw = root.querySelector("details.raw-frame");
    if (raw) raw.open = Boolean(open);
  }

  const fields = [["crc_valid_frames", "CRC 有效"], ["bad_header_crc", "CRC8 失败"],
    ["bad_frame_crc", "CRC16 失败"], ["invalid_length", "长度异常"]];
  const names = Object.keys(data.telemetry.rx?.channels || { info: {}, jam1: {}, jam2: {} });
  $("lossDiagnostics").innerHTML = names.map((name) => {
    const ch = branchData(data, name);
    return '<div class="loss-card"><strong>' + escapeHtml(branchLabels[name] || name) + '</strong><div class="decode-grid">' +
      fields.map(([key, label]) => cell(label, ch?.counters[key] ?? 0)).join("") + '</div></div>';
  }).join("");
  updateStatus("lossBadge", broadcast.last_fragment ? "ok" : "unknown", broadcast.last_fragment ? "应用层统计" : "等待分片");
}

function reportState(entry, now) {
  if (!entry) return "待接入";
  return now - entry.stamp <= INPUT_TIMEOUT_SEC ? "已接入" : "已超时";
}

function updateRadar(data, rxSide, vision, fusion, referee) {
  const visionStatus = reportState(vision, data.stamp);
  const fusionStatus = reportState(fusion, data.stamp);
  const liveVision = visionStatus === "已接入";
  const liveFusion = fusionStatus === "已接入";
  const side = rxSide || normalizeRadarSide(referee?.data.effective_radio_side) ||
    normalizeRadarSide(fusion?.data.side) || normalizeRadarSide(vision?.data.side);
  const robots = {};
  const frame = data.channels[5555].frames["0x0A01"];
  const age = frame ? Math.max(0, data.stamp - frame.stamp) : Infinity;
  const radios = {};
  if (rxSide && frame?.positions_cm) {
    const opponent = rxSide === "red" ? "B" : "R";
    for (const [index, [x, y]] of frame.positions_cm.entries()) {
      if ((x === 0 && y === 0) || x > 2800 || y > 1500) continue;
      radios[opponent + radarRoleIds[index]] = {
        position_cm: { x, y }, fresh: age <= 1, expired: age > 1, ghost_visible: age <= 7, age_sec: age,
      };
    }
  }
  const mapSide = updateRadarMapOrientation(side);
  $("visionLayerLegend").textContent = "视觉定位 · " + visionStatus;
  $("finalLayerLegend").textContent = "融合决策上报 · " + fusionStatus;
  const cellFor = (position, detail) => `<td><div class="coord-line">${coordText(radarPosition(position))}</div>
    <div class="state-line">${escapeHtml(detail)}</div></td>`;
  $("radarRobotRows").innerHTML = radarRobotOrderForSide(mapSide).map((name) => {
    const visual = vision?.data.robots?.[name];
    const radio = radios[name];
    const final = fusion?.data.robots?.[name]?.final;
    robots[name] = { visual: liveVision ? visual : null, radio, final: liveFusion ? final : null };
    const radioStatus = radio ? `${radio.expired ? "已过期 · " : ""}${ageText(radio.age_sec)}` : "—";
    const classes = [selectedRobot === name ? "selected" : "", radio?.expired ? "radio-expired" : ""].join(" ");
    return `<tr data-robot="${name}" class="${classes}"><th>${name}</th>
      ${cellFor(visual, liveVision ? visual?.state || "—" : visionStatus)}
      ${cellFor(radio, radioStatus)}
      ${cellFor(final, liveFusion ? final?.source || "—" : fusionStatus)}
    </tr>`;
  }).join("");
  radarLatest = { robots };
  radarDirty = true;
}

function renderRates(data, vision) {
  const info = data.channels[5555];
  const jam1 = data.channels[6666];
  const jam2 = data.channels[6667];
  const status = reportState(vision, data.stamp);
  const metrics = vision?.data.vision || {};
  const radioCards = [
    ["信息波", info.frames_per_sec, info.valid_frames],
    ["干扰波", jam1.frames_per_sec + jam2.frames_per_sec, jam1.valid_frames + jam2.valid_frames],
  ].map(([label, rate, count]) => [label, rate.toFixed(1) + " 帧/s", `累计 ${count} 帧`]);
  const visionCards = [
    ["相机采集", metrics.camera_fps, metrics.camera_frame_count],
    ["视觉处理", metrics.processing_fps ?? metrics.fps, metrics.processed_frame_count],
  ].map(([label, rate, count]) => [
    label, status === "已接入" && Number.isFinite(rate) && rate >= 0 ? rate.toFixed(1) + " FPS" : "— FPS",
    status !== "已接入" ? status : Number.isSafeInteger(count) && count >= 0 ? `累计 ${count} 帧` : "累计 — 帧",
  ]);
  $("radarPerformanceGrid").innerHTML = [...radioCards, ...visionCards].map(([label, rate, total]) =>
    `<div class="radar-title-rate"><span>${label}</span><strong>${rate}</strong><small>${total}</small></div>`
  ).join("");
}

function renderReferee(data, referee, fusionEntry) {
  const fusion = fusionEntry?.data || {};
  const serial = referee?.data || {};
  const bridge = data.inputs?.["referee-bridge"] || {};
  const strategy = (name, serialKey) =>
    bridge[name]?.data.payload || (serialKey && serial[serialKey]) || fusion.strategy?.[name]?.payload || {};
  const marks = strategy("RadarMarkProgress").enemy || {};
  const game = strategy("GameStatus", "last_game_status");
  const decision = strategy("RadarDecisionSync", "last_radar_decision_sync");
  const dart = strategy("DartStatus");
  const passwordCommand = strategy("RadarCommand0121");
  const password = passwordCommand.password_cmd === 1 ? null : passwordCommand.password;
  const keys = [data.channels[6666], data.channels[6667]]
    .map((channel) => channel.frames["0x0A06"]).filter(Boolean).sort((a, b) => b.stamp - a.stamp);
  const key = keys[0];
  const tx = fusion.tx_0305 || {};
  const ack = fusion.auto_double_vulnerability?.last_ack || serial.last_algorithm_ack || {};
  const request = serial.last_algorithm_request || {};
  const pending = request.request_id && request.request_id !== ack.request_id;
  const haveStrategy = [marks, game, decision, dart].some((value) => Object.keys(value).length);
  const synced = [serial.frame_timed_out ? null : referee, ...Object.values(bridge),
    fusionEntry?.data.strategy ? fusionEntry : null]
    .some((entry) => reportState(entry, data.stamp) === "已接入");
  updateStatus("strategyBadge", haveStrategy ? synced ? "ok" : "warn" : "unknown",
    haveStrategy ? synced ? "裁判已同步" : "裁判数据超时" : "等待裁判");

  const stage = game.game_progress == null ? "—" : gameProgressLabels[Number(game.game_progress)] || "—";
  const yesNo = (value, yes, no) => value === true ? yes : value === false ? no : "—";
  const encryption = Number.isInteger(decision.own_encryption_level) ? "L" + decision.own_encryption_level : "—";
  const cooldown = serial.password_verify_cooldown_remaining_sec;
  const lastVerify = serial.last_password_verify
    ? `${serial.last_password_verify} · ${Number.isFinite(cooldown) && cooldown > 0
      ? cooldown.toFixed(1) + "s 后可再次验证" : yesNo(serial.password_verify_ready, "可再次验证", "冷却中")}`
    : "—";
  const range = (value, unit) => Number.isFinite(value) ? `${value} ${unit}` : "—";
  const target = dart.selected_target === 0 ? "未选定 / 前哨站" : dartTargets[dart.selected_target];
  const hit = dart.recent_hit_target === 0 ? "尚未命中" : dartTargets[Number(dart.recent_hit_target) - 1];
  const markRows = radarMarkIds.map((robot) => {
    const active = marks[robot];
    const aerial = robot === "opponent_aerial";
    const label = aerial ? yesNo(active, "已特殊标识", "未特殊标识") : yesNo(active, "已达到（≥100）", "未达到（<100）");
    return `<div class="mark-switch-row ${active === true ? "on" : ""}"><span>${robotLabels[robot]}</span>
      <i aria-hidden="true"><b></b></i><strong>${label}</strong></div>`;
  }).join("");
  const cells = [
    ["0x0001 · 比赛阶段", `${stage} · 剩余 ${range(game.stage_remain_time, "s")}`],
    ["0x020E · 双倍易伤", `剩余 ${range(decision.double_vulnerability_count, "次")} · ${yesNo(decision.is_double_vulnerability, "正在生效", "未生效")}`],
    ["0x0105 · 飞镖选定目标", `${target || "—"} · 发射剩余 ${range(dart.dart_remaining_time, "s")}`],
    ["0x0105 · 最近命中", `${hit || "—"} · 累计 ${range(dart.accumulated_hit_count, "次")}`],
    ["0x020E · 己方加密状态", `${encryption} · ${yesNo(decision.can_change_password, "允许更新密钥", "暂不允许更新")}`],
    ["0x0A06 / 0x0121 · 当前破解密钥", password ? password + " · 提交候选" : key ? key.key + " · " + fmtRecentArrival(key.stamp, data.stamp) : "等待接收"],
    ["0x0121 · 最近实际提交验证", lastVerify],
    ["双倍易伤请求", pending ? "等待 ACK · " + request.request_id : ack.request_id ? "收到 ACK · " + ack.request_id : "—"],
    ["双倍易伤串口结果", ack.written === true ? `写入成功 · radar_cmd ${ack.radar_cmd ?? "—"}` :
      ack.dry_run === true ? "试运行" : ack.request_id ? "写入失败 · " + (ack.error || "未写入") : "—"],
    ["0x0305 · 坐标发送", `${Number.isFinite(tx.actual_rate_hz) ? tx.actual_rate_hz.toFixed(2) + " Hz" : "—"} · 成功 ${tx.success_count ?? "—"} / 请求 ${tx.request_count ?? "—"}`],
    ["0x0305 · 当前发送条件", yesNo(fusion.send_allowed, "允许发送", "已暂停 · " + (fusion.send_block_reason || "等待有效坐标"))],
  ];
  $("radarStrategy").innerHTML = `<div class="strategy-cell wide"><span>0x020C · 对方标记状态</span>
    <div class="mark-switch-list">${markRows}</div></div>` + cells.map(([label, value]) =>
    `<div class="strategy-cell"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");

  const status = reportState(referee, data.stamp);
  const serialStatus = serial.dry_run === true ? "试运行" : yesNo(serial.serial_open, "打开", "关闭");
  const serialLevel = status === "已超时" || serial.frame_timed_out ? "warn" : serial.last_error ? "bad" :
    serial.serial_open && referee ? "ok" : "unknown";
  for (const id of ["serialBadge", "radarSerialBadge"]) {
    updateStatus(id, serialLevel, status === "已接入" ? stateLabel(serialLevel) : status);
  }
  const robotId = serial.detected_robot_id ?? serial.last_robot_status?.robot_id ?? "—";
  const side = normalizeRadarSide(serial.effective_radio_side ?? serial.last_robot_status?.radio_side);
  const txCommand = serial.last_tx?.data_cmd_hex || serial.last_tx?.cmd_hex || "—";
  const common = [
    ["串口", serialStatus], ["端口", serial.port || "—"],
    ["机器人 / 阵营", `${robotId} / ${side === "red" ? "红方" : side === "blue" ? "蓝方" : "—"}`],
  ];
  renderKeyValues("refereeState", [...common, ["加密等级", encryption],
    ["可改密钥", yesNo(decision.can_change_password, "是", "否")], ["比赛阶段", stage],
    ["最近发送", txCommand], ["错误", serial.last_error || "—"]]);
  renderKeyValues("radarSerialSummary", [...common,
    ["收 / 发 / 试运行", `${serial.rx_count ?? "—"} / ${serial.tx_count ?? "—"} / ${serial.dry_run_count ?? "—"}`],
    ["最近接收 / 发送", `${fmtAge(serial.last_rx?.timestamp)} / ${fmtAge(serial.last_tx?.timestamp)}`],
    ["0x0305 限频拦截", serial.blocked_0305_rate_count ?? "—"],
    ["密码冷却拦截", serial.password_verify_suppressed_count ?? "—"], ["最后错误", serial.last_error || "—"]]);
}

function renderRxControls(data) {
  const control = data.receiver;
  const switching = rxChangePending || control?.status === "starting";
  for (const [side, id] of [["red", "rxRedBtn"], ["blue", "rxBlueBtn"]]) {
    const button = $(id);
    button.disabled = !control || switching;
    button.setAttribute("aria-pressed", String(control?.status === "running" && control.side === side));
  }
  const live = data.stamp - (data.telemetry.spectra?.raw?.stamp ?? 0) < 2;
  $("rxControlState").textContent = switching ? "等待新流图的 IQ" :
    control?.error || rxChangeError || (live ? "接收中" :
      control?.status === "idle" ? "等待选择阵营" : control ? "等待宽带 IQ" : "预览模式");

  const node = data.receive_node;
  if (node?.status === "running") updateStatus("receiveNodeBadge", "ok", "接收节点运行中");
  else if (node?.status === "error") updateStatus("receiveNodeBadge", "bad", "接收节点异常");
  else updateStatus("receiveNodeBadge", "unknown", node ? "接收节点已停止" : "接收节点未启动");
  $("receiveNodeBadge").title = node?.error || (node?.pid ? "radio_recive_node · PID " + node.pid : "radio_recive_node");
}

function renderSdrCheck(data) {
  const { rx, spectra, gain } = data.telemetry;
  const gainCurrent = data.stamp - (gain?.stamp ?? 0) < 4;
  const iqCurrent = data.stamp - (spectra?.raw?.stamp ?? 0) < 2;
  renderKeyValues("sdrCheckFields", [
    ["E200 URI", rx?.uri || "—"],
    ["接收 LO", fmtHz(rx?.lo_hz)],
    ["采样率", rx ? (rx.sample_rate_hz / 1e6).toFixed(2) + " MS/s" : "—"],
    ["宽带 IQ", iqCurrent ? "接收中 · " + fmtRecentArrival(spectra.raw.stamp, data.stamp) : "等待 IQ"],
    ["增益模式", gainCurrent ? gain.mode || "回读失败" : "等待回读"],
    ["硬件增益", gainCurrent && Number.isFinite(gain.gain_db) ? gain.gain_db.toFixed(1) + " dB" : "—"],
    ["IIO 错误", gainCurrent ? gain.error || "—" : "—"],
  ]);
}

function renderHealth(data, side) {
  const rx = data.telemetry.rx;
  const hasRaw = data.telemetry.spectra?.raw && data.stamp - data.telemetry.spectra.raw.stamp < 2;
  const hasPositions = data.channels[5555].frames["0x0A01"];
  const coordsLive = hasPositions && data.stamp - hasPositions.stamp <= 1 && side;
  const level = data.error ? "bad" : hasRaw && coordsLive ? "ok" : hasRaw ? "warn" : "unknown";
  updateStatus("overallBadge", level, "总体 " + stateLabel(level));
  updateStatus("radarOverallBadge", level);
  $("sideBadge").textContent = side ? (side === "red" ? "红方" : "蓝方") : "等待阵营";
  $("summary").textContent = rx
    ? [rx.uri, fmtHz(rx.lo_hz), (rx.sample_rate_hz / 1e6).toFixed(1) + " MS/s", "SPS " + rx.sps].join(" · ")
    : "等待 E200 接收遥测";
  const items = [];
  if (data.error) items.push(["ERROR", "ZeroMQ 订阅", data.error]);
  if (!hasRaw) items.push(["待检测", "E200 宽带 IQ", "等待接收流图监控数据"]);
  if (!coordsLive) items.push(["WARN", "无线电坐标源", hasPositions ? "等待更新坐标或阵营" : "等待有效 0x0A01"]);
  $("radarHealthCounts").innerHTML = [
    ["ERROR", data.error ? 1 : 0, "error"], ["WARN", coordsLive ? 0 : 1, "warn"],
    ["正常", Number(Boolean(hasRaw)) + Number(Boolean(coordsLive)), ""], ["待检测", hasRaw ? 0 : 1, "unknown"],
  ].map(([label, value, kind]) => '<div class="health-count ' + kind + '"><span>' + label +
    '</span><strong>' + value + '</strong></div>').join("");
  $("radarIssueList").innerHTML = items.length ? items.map(([badge, label, message]) =>
    '<article class="radar-issue-card ' + (badge === "ERROR" ? "error" : badge === "WARN" ? "warn" : "unknown") +
    '"><header><strong>' + escapeHtml(label) + '</strong><span>' + badge + '</span></header><p>' +
    escapeHtml(message) + '</p></article>').join("") : '<div class="radar-issue-empty">链路正常</div>';
}

function renderSnapshot(data, force = false) {
  snapshot = data;
  const nextRunId = data.telemetry.run_id;
  if (runId !== nextRunId) {
    cleanSpectrumHistory();
    runId = nextRunId;
  }
  const rx = data.telemetry.rx;
  updateJamOptions(rx);
  if (activePage === "radio") refreshSpectra(data);
  if (!force && performance.now() - lastDetailMs < 200) return;
  lastDetailMs = performance.now();
  const side = normalizeRadarSide(rx?.side);
  const { vision, fusion, referee } = data.inputs || {};
  const serial = referee || (fusion?.data.referee_serial && Object.keys(fusion.data.referee_serial).length
    ? { stamp: fusion.stamp, data: fusion.data.referee_serial } : null);
  renderHealth(data, side);
  renderRxControls(data);
  if ($("sdrCheckDialog").open) renderSdrCheck(data);
  renderRates(data, vision);
  renderReferee(data, serial, fusion);
  if (activePage === "radio") {
    renderSignalQuality(data);
    renderReception(data);
  } else {
    updateRadar(data, side, vision, fusion, serial);
  }
}

function setActivePage(page) {
  activePage = page;
  const radio = page === "radio";
  $("radioPage").hidden = !radio;
  $("radarPage").hidden = radio;
  $("radioPage").classList.toggle("active", radio);
  $("radarPage").classList.toggle("active", !radio);
  $("radioTab").classList.toggle("active", radio);
  $("radarTab").classList.toggle("active", !radio);
  history.replaceState(null, "", radio ? "#radio" : location.pathname + location.search);
  if (radio) spectrumDirty = true;
  else { fitRadarMapStage(); radarDirty = true; }
  if (snapshot) renderSnapshot(snapshot, true);
}

function renderLoop(nowMs) {
  if (activePage === "radio") {
    flushWaterfalls();
    if (spectrumDirty && nowMs - lastSpectrumRenderMs >= 32) {
      spectrumDirty = stepSmoothing();
      drawSpectrum();
      lastSpectrumRenderMs = nowMs;
    }
  } else if (radarDirty) {
    radarDirty = drawRadarMap();
  }
  requestAnimationFrame(renderLoop);
}

function selectRadarRobot(name, scrollTable = false) {
  selectedRobot = name;
  radarDirty = true;
  document.querySelectorAll("#radarRobotRows tr").forEach((row) => row.classList.toggle("selected", row.dataset.robot === name));
  if (scrollTable) document.querySelector('#radarRobotRows tr[data-robot="' + name + '"]')?.scrollIntoView({ block: "nearest" });
}

$("radarTab").addEventListener("click", () => setActivePage("radar"));
$("radioTab").addEventListener("click", () => setActivePage("radio"));
$("radarRobotRows").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-robot]");
  if (row) selectRadarRobot(row.dataset.robot);
});
async function refreshSnapshot() {
  const response = await fetch("/api/state", { cache: "no-store" });
  if (!response.ok) throw new Error(String(response.status));
  const data = await response.json();
  renderSnapshot(data, true);
  return data;
}

$("refreshBtn").addEventListener("click", async () => {
  try {
    await refreshSnapshot();
  } catch (error) {
    $("summary").textContent = "读取状态失败 · " + error.message;
  }
});
$("checkSdrBtn").addEventListener("click", async () => {
  $("sdrCheckDialog").showModal();
  if (snapshot) renderSdrCheck(snapshot);
  try {
    await refreshSnapshot();
  } catch (error) {
    $("sdrCheckFields").textContent = "读取状态失败 · " + error.message;
  }
});
async function selectRxSide(side) {
  rxChangePending = true;
  rxChangeError = "";
  renderRxControls(snapshot);
  try {
    const response = await fetch("/api/rx/side", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ side }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || String(response.status));
  } catch (error) {
    rxChangeError = error.message;
  } finally {
    rxChangePending = false;
    try { await refreshSnapshot(); }
    catch { renderRxControls(snapshot); }
  }
}
$("rxRedBtn").addEventListener("click", () => selectRxSide("red"));
$("rxBlueBtn").addEventListener("click", () => selectRxSide("blue"));
for (const id of ["spectrumTap", "jamTap"]) {
  $(id).addEventListener("change", () => {
    cleanSpectrumHistory();
    if (snapshot) renderSnapshot(snapshot, true);
  });
}
for (const id of ["showFinal", "showVision", "showRadio", "showLabels"]) {
  $(id).addEventListener("change", () => { radarDirty = true; $("radarTooltip").hidden = true; });
}
if (typeof ResizeObserver !== "undefined") {
  const observer = new ResizeObserver(fitRadarMapStage);
  observer.observe($("radarMapViewport"));
}
window.addEventListener("resize", () => { fitRadarMapStage(); spectrumDirty = true; });
setActivePage(activePage);
requestAnimationFrame(renderLoop);
const events = new EventSource("/api/events");
events.onopen = () => {
  $("radarStreamStatus").textContent = "SSE latest-only 实时推送";
  $("waterfallStatus").textContent = "SSE 实时推送";
  $("spectrumPerf").textContent = "已连接";
  $("spectrumPerf").className = "perf-badge ok";
};
events.onmessage = (event) => renderSnapshot(JSON.parse(event.data));
events.onerror = () => {
  $("radarStreamStatus").textContent = "SSE 重连中";
  $("waterfallStatus").textContent = "SSE 重连中";
  $("spectrumPerf").textContent = "等待重连";
  $("spectrumPerf").className = "perf-badge warn";
};
window.addEventListener("beforeunload", () => events.close());

$("radarMapCanvas").addEventListener("mousemove", (event) => {
  if (!radarLatest) return;
  const canvas = $("radarMapCanvas");
  const rect = canvas.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / Math.max(rect.width, 1)) * canvas.width;
  const y = ((event.clientY - rect.top) / Math.max(rect.height, 1)) * canvas.height;
  let closest = null;
  let distance = Infinity;
  for (const hit of radarHitTargets) {
    const current = Math.hypot(hit.x - x, hit.y - y);
    if (current < 42 && current < distance) { closest = hit; distance = current; }
  }
  const tooltip = $("radarTooltip");
  if (!closest) {
    tooltip.hidden = true;
    canvas.dataset.hoverRobot = "";
    return;
  }
  const fusion = radarLatest;
  const item = get(fusion, ["robots", closest.name], {});
  const allyPrefix = radarMapOwnSide === "red" ? "R" : "B";
  tooltip.innerHTML = `<strong>${closest.name} · ${closest.name.startsWith(allyPrefix) ? "己方" : "对方"} · ${escapeHtml(closest.layer)}</strong><br>
    最终 ${coordText(radarPosition(item.final))} · ${escapeHtml(item.final?.source || "missing")}<br>
    视觉 ${coordText(radarPosition(item.visual))} · ${escapeHtml(item.visual?.state || "missing")} · 置信度 ${item.visual?.confidence == null ? "—" : Number(item.visual.confidence).toFixed(2)}<br>
    无线电 ${coordText(radarPosition(item.radio))} · 年龄 ${ageText(item.radio?.age_sec)}`;
  tooltip.style.left = `${Math.max(4, Math.min(rect.width - 260, event.clientX - rect.left + 14))}px`;
  tooltip.style.top = `${Math.min(rect.height - 105, Math.max(8, event.clientY - rect.top + 14))}px`;
  tooltip.hidden = false;
  canvas.dataset.hoverRobot = closest.name;
});
$("radarMapCanvas").addEventListener("mouseleave", () => {
  $("radarTooltip").hidden = true;
  $("radarMapCanvas").dataset.hoverRobot = "";
});
$("radarMapCanvas").addEventListener("click", () => {
  const name = $("radarMapCanvas").dataset.hoverRobot;
  if (name) selectRadarRobot(name, true);
});

