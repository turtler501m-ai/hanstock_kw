const state = {
    signals: [],
    selectedId: null,
    chart: null,
    mockTrades: [],
};

const endpoints = {
    summary: "/api/futures-signals/summary",
    signals: "/api/futures-signals",
    futuresBalance: "/api/futures/balance",
    futuresPositions: "/api/futures/positions",
    futuresOrders: "/api/futures/orders",
    futuresQuote: "/api/futures/quote/",
    futuresOrder: "/api/futures/order",
    futuresCancel: "/api/futures/order/cancel",
    quantConnectMnq: "/api/quantconnect/mnq/status",
    quantConnectMnqOrder: "/api/quantconnect/mnq/order",
    quantConnectMnqDeploy: "/api/quantconnect/mnq/deploy",
};

function iconize() {
    document.querySelectorAll("[data-icon]").forEach((element) => {
        if (element.querySelector("svg")) {
            return;
        }
        const icon = document.createElement("i");
        icon.setAttribute("data-lucide", element.dataset.icon);
        element.prepend(icon);
    });
    if (window.lucide) {
        window.lucide.createIcons();
    }
}

function valueFrom(object, keys, fallback = null) {
    for (const key of keys) {
        if (object && object[key] !== undefined && object[key] !== null) {
            return object[key];
        }
    }
    return fallback;
}

function normalizeList(payload) {
    if (Array.isArray(payload)) {
        return payload;
    }
    return payload?.signals || payload?.items || payload?.data || payload?.results || [];
}

function formatNumber(value, suffix = "") {
    if (value === null || value === undefined || value === "") {
        return "-";
    }
    const numeric = Number(value);
    if (Number.isNaN(numeric)) {
        return `${value}${suffix}`;
    }
    return `${numeric.toLocaleString("ko-KR")}${suffix}`;
}

function formatRate(value) {
    if (value === null || value === undefined || value === "") {
        return "-";
    }
    const numeric = Number(value);
    if (Number.isNaN(numeric)) {
        return String(value);
    }
    const percent = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
    return `${percent.toFixed(1)}%`;
}

function formatTime(value) {
    if (!value) {
        return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return new Intl.DateTimeFormat("ko-KR", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(date);
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    }[char]));
}

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}

function normalizeStatus(value, fallback = "pending") {
    return String(value || fallback).trim().toLowerCase();
}

function confidenceValue(signal, result) {
    return valueFrom(signal, ["confidence", "parse_confidence", "parser_confidence"], valueFrom(signal.parser || {}, ["confidence", "parse_confidence"], valueFrom(signal.parsed || {}, ["confidence"], valueFrom(result, ["parse_confidence", "confidence"], null))));
}

function normalizeSignal(signal, index) {
    const result = signal.result || signal.verification || {};
    const rawDirection = String(valueFrom(signal, ["direction", "side", "action"], "-")).toLowerCase();
    const direction = ["sell", "short"].includes(rawDirection) ? "short" : (["exit", "close", "liquidate"].includes(rawDirection) ? "exit" : "long");
    const parseStatus = normalizeStatus(valueFrom(signal, ["parse_status", "parser_status"], valueFrom(signal.parser || {}, ["status"], "parsed")));
    const verificationStatus = normalizeStatus(valueFrom(result, ["verification_status", "status"], valueFrom(signal, ["verification_status", "status"], "pending")));
    const resultStatus = normalizeStatus(valueFrom(result, ["exit_reason", "result"], verificationStatus));
    const targetValue = valueFrom(signal, ["take_profit_1", "tp1", "target", "take_profit"], null);
    const tp = Array.isArray(signal.targets) ? signal.targets.join(" / ") : (targetValue ?? "-");

    return {
        id: valueFrom(signal, ["id", "signal_id"], index),
        provider: valueFrom(signal, ["provider", "channel", "channel_name", "source"], "-"),
        channel: valueFrom(signal, ["channel_key", "channel", "source"], "-"),
        symbol: valueFrom(signal, ["symbol", "ticker", "contract"], "-"),
        exchange: valueFrom(signal, ["exchange", "market"], ""),
        direction,
        directionType: direction === "exit" ? "exit" : "entry",
        entry: valueFrom(signal, ["entry_price", "entry", "entry_low"], "-"),
        stopLoss: valueFrom(signal, ["stop_loss", "stop", "sl"], "-"),
        takeProfit: tp,
        parseStatus,
        verificationStatus,
        status: verificationStatus,
        resultStatus,
        pnlPoints: valueFrom(result, ["pnl_points", "pnl"], valueFrom(signal, ["pnl_points"], null)),
        signalTime: valueFrom(signal, ["signal_time", "received_at", "created_at", "time"], null),
        rawText: valueFrom(signal, ["raw_text", "raw_message", "message"], valueFrom(signal.message || {}, ["raw_text"], "")),
        rawPayload: valueFrom(signal, ["raw_payload_json", "parsed", "payload"], signal),
        confidence: confidenceValue(signal, result),
    };
}

function statusBucket(status) {
    if (["verified", "success", "hit_tp", "hit_sl", "closed", "win", "loss", "tp1", "tp2", "tp3", "sl"].includes(status)) {
        return "verified";
    }
    if (["parsed"].includes(status)) {
        return "pending";
    }
    if (["rejected", "invalid", "duplicate", "failed", "parse_failed"].includes(status)) {
        return "rejected";
    }
    return "pending";
}

function badgeClass(status) {
    const bucket = statusBucket(status);
    return `status-badge status-${bucket}`;
}

function resultClass(status) {
    if (["hit_tp", "tp1", "tp2", "tp3", "win", "verified"].includes(status)) {
        return "result-win";
    }
    if (["hit_sl", "sl", "loss", "rejected"].includes(status)) {
        return "result-loss";
    }
    if (["ambiguous", "needs_review"].includes(status)) {
        return "result-ambiguous";
    }
    if (["pending", "open"].includes(status)) {
        return "result-pending";
    }
    return "result-neutral";
}

function resultLabel(status) {
    const labels = {
        hit_tp: "TP",
        hit_sl: "SL",
        parsed: "파싱",
        success: "성공",
        verified: "검증",
        needs_review: "검토",
        rejected: "거부",
        duplicate: "중복",
        ambiguous: "모호",
        pending: "대기",
        open: "진행",
        expired: "만료",
        invalid: "무효",
        closed: "종료",
    };
    return labels[status] || status || "-";
}

function normalizeQCSymbol(symbol) {
    const text = String(symbol || "").trim().toUpperCase();
    if (!text) return "";
    if (["MNQ", "NQ", "NAS100", "NASDAQ", "나스닥"].some((token) => text.includes(token))) {
        return "MNQ";
    }
    return text;
}

function qcSignalEligibility(signal) {
    if (!signal) {
        return { allowed: false, reason: "신호 없음", side: "", symbol: "" };
    }
    if (signal.directionType === "exit") {
        return { allowed: false, reason: "청산 신호", side: "", symbol: normalizeQCSymbol(signal.symbol) };
    }
    const symbol = normalizeQCSymbol(signal.symbol);
    if (symbol !== "MNQ") {
        return { allowed: false, reason: `${symbol || signal.symbol || "-"} 미지원`, side: "", symbol };
    }
    const side = signal.direction === "short" ? "sell" : "buy";
    return {
        allowed: true,
        reason: side === "buy" ? "MNQ 매수 가능" : "MNQ 매도 가능",
        side,
        symbol,
    };
}

function qcSideLabel(side) {
    return side === "sell" ? "매도" : "매수";
}

function qcSignalOrderCell(signal) {
    const qc = qcSignalEligibility(signal);
    if (!qc.allowed) {
        return `<span class="status-badge status-pending">${escapeHtml(qc.reason)}</span>`;
    }
    const buttonClass = qc.side === "sell" ? "btn-sell" : "btn-buy";
    return `
        <button type="button" class="btn-qc-signal ${buttonClass}" data-qc-signal-id="${escapeHtml(signal.id)}" data-qc-side="${escapeHtml(qc.side)}">
            QC ${escapeHtml(qcSideLabel(qc.side))}
        </button>
    `;
}

function countStatuses(summary = {}, signals = []) {
    const fallback = signals.reduce((counts, signal) => {
        counts[statusBucket(signal.verificationStatus)] += 1;
        return counts;
    }, { verified: 0, pending: 0, rejected: 0 });

    return {
        verified: valueFrom(summary, ["verified_count", "verification_completed", "completed_count", "verified"], fallback.verified),
        pending: valueFrom(summary, ["pending_count", "verification_pending", "needs_review"], fallback.pending),
        rejected: valueFrom(summary, ["rejected_count", "rejected", "invalid_count", "duplicate_count"], fallback.rejected),
    };
}

function averageConfidence(summary = {}, signals = []) {
    const summaryValue = valueFrom(summary, ["avg_parse_confidence", "average_parse_confidence", "parse_confidence", "confidence"], null);
    if (summaryValue !== null) {
        return summaryValue;
    }
    const values = signals
        .map((signal) => signal.confidence)
        .filter((value) => value !== null && value !== undefined && value !== "")
        .map((value) => Number(value))
        .filter((value) => !Number.isNaN(value));
    if (values.length === 0) {
        return null;
    }
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function renderSummary(summary = {}, signals = []) {
    const total = valueFrom(summary, ["collected_today", "today_count", "messages_today", "total"]);
    const counts = countStatuses(summary, signals);
    const verified = counts.verified;
    const needsReview = counts.pending;
    const parseRate = valueFrom(summary, ["parse_success_rate", "parse_rate"], total ? Number(verified || 0) / Number(total) : null);

    setText("summary-collected", formatNumber(total));
    setText("summary-parse-rate", formatRate(parseRate));
    setText("summary-pending", formatNumber(needsReview));
    setText("summary-win-rate", formatRate(valueFrom(summary, ["win_rate", "recent_win_rate"])));
    setText("summary-verified", formatNumber(verified));
    setText("summary-rejected", formatNumber(counts.rejected));
    setText("summary-confidence", formatRate(averageConfidence(summary, signals)));
    setText("summary-avg-pnl", formatNumber(valueFrom(summary, ["avg_pnl_points", "average_pnl_points"], null), " pt"));
    setText("count-verified", formatNumber(verified));
    setText("count-pending", formatNumber(needsReview));
    setText("count-rejected", formatNumber(counts.rejected));
}

function renderMockTrading(mockData = {}) {
    if (!mockData) return;

    // Total PnL (overview tab uses overview- prefix to avoid ID collision with mock-perf tab)
    const pnl = mockData.total_pnl || 0;
    const pnlEl = document.getElementById("overview-mock-pnl");
    if (pnlEl) {
        pnlEl.textContent = (pnl >= 0 ? "+" : "") + "$" + pnl.toFixed(2);
        pnlEl.style.color = pnl >= 0 ? "#22c55e" : "#ef4444";
    }

    // Win rate
    setText("overview-mock-win-rate", (mockData.win_rate || 0) + "%");

    // Closed trades
    setText("overview-mock-closed", mockData.closed_trades || 0);

    // Open positions
    setText("overview-mock-open", mockData.open_positions || 0);

    // Trades list
    const listEl = document.getElementById("overview-mock-trades-list");
    if (listEl) {
        const trades = mockData.recent_trades || [];
        if (trades.length === 0) {
            listEl.innerHTML = '<div style="color: #94a3b8;">거래 내역이 없습니다.</div>';
        } else {
            listEl.innerHTML = trades.map(t => {
                const tradePnl = t.pnl || 0;
                const pnlClass = tradePnl >= 0 ? "color:#22c55e" : "color:#ef4444";
                return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #334155;">
                    <span>${t.side} ${t.symbol} @ $${t.entry_price?.toFixed(0)}</span>
                    <span style="${pnlClass}">${tradePnl >= 0 ? "+" : ""}$${tradePnl.toFixed(2)} (${t.exit_reason})</span>
                </div>`;
            }).join("");
        }
    }
}

function formatMoney(value, currency = "USD") {
    if (value === null || value === undefined || value === "") {
        return "-";
    }
    const numeric = Number(value);
    if (Number.isNaN(numeric)) {
        return String(value);
    }
    return new Intl.NumberFormat("ko-KR", {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
    }).format(numeric);
}

function setStatusItem(id, ok, label) {
    const item = document.getElementById(id);
    if (!item) return;
    item.classList.remove("ok", "warn");
    item.classList.add(ok ? "ok" : "warn");
    if (label) {
        const strong = item.querySelector("strong");
        if (strong) strong.textContent = label;
    }
}

function renderQuantConnectOrders(orders = []) {
    const body = document.getElementById("qc-orders-body");
    const empty = document.getElementById("qc-orders-empty");
    if (!body) return;

    body.innerHTML = "";
    if (!orders.length) {
        if (empty) empty.classList.add("visible");
        return;
    }

    if (empty) empty.classList.remove("visible");
    body.innerHTML = orders.slice(-20).reverse().map((order) => {
        const side = String(order.side || order.direction || "-").toUpperCase();
        const sideClass = side.includes("SELL") || side.includes("SHORT") ? "short" : "long";
        const sideLabel = side.includes("SELL") || side.includes("SHORT") ? "매도" : (side.includes("BUY") || side.includes("LONG") ? "매수" : side);
        const statusLabel = quantConnectStatusLabel(order.status || "pending");
        return `
            <tr>
                <td>${escapeHtml(order.time || order.created_at || "-")}</td>
                <td><strong>${escapeHtml(order.symbol || "MNQ")}</strong></td>
                <td><span class="direction-pill direction-${sideClass}">${escapeHtml(sideLabel)}</span></td>
                <td>${escapeHtml(order.quantity ?? order.qty ?? "-")}</td>
                <td>${escapeHtml(order.price ?? order.fill_price ?? "-")}</td>
                <td><span class="status-badge status-${String(order.status || "pending").toLowerCase()}">${escapeHtml(statusLabel)}</span></td>
            </tr>
        `;
    }).join("");
}

function quantConnectStatusLabel(value) {
    const key = String(value || "").toLowerCase();
    const labels = {
        ready: "준비됨",
        ready_to_sync: "동기화 준비",
        not_connected: "연결 필요",
        missing: "없음",
        configured: "설정됨",
        ok: "정상",
        found: "있음",
        waiting: "대기",
        pending: "대기",
        submitted: "제출",
        filled: "체결",
        canceled: "취소",
        cancelled: "취소",
        invalid: "오류",
        error: "오류",
    };
    return labels[key] || value || "-";
}

function quantConnectMessage(payload = {}) {
    const deployment = payload.deployment || {};
    if (deployment.message && !String(deployment.message).includes("QuantConnect")) {
        return deployment.message;
    }
    if (!payload.auth?.configured) {
        return "QuantConnect 사용자 ID와 API 토큰을 .env에 설정해야 클라우드 상태를 확인할 수 있습니다.";
    }
    if (!payload.auth?.project_configured) {
        return "QUANTCONNECT_PROJECT_ID가 없어 프로젝트 주문/결과 동기화는 대기 중입니다.";
    }
    if (payload.auth?.success) {
        return "QuantConnect API 인증이 정상입니다. 결과 파일이 동기화되면 주문과 성과가 표시됩니다.";
    }
    return deployment.status ? quantConnectStatusLabel(deployment.status) : "QuantConnect 상태를 확인했습니다.";
}

function renderQuantConnectFiles(payload = {}) {
    const list = document.getElementById("qc-file-list");
    if (!list) return;
    const files = payload.files || {};
    const algorithm = payload.algorithm || {};
    const rows = [
        ["전략 파일", algorithm.path || "-"],
        ["설정 파일", files.config?.path || "-"],
        ["설명 문서", files.documentation?.path || "-"],
        ["결과 파일", files.results?.path || "-"],
    ];
    list.innerHTML = rows.map(([label, value]) => `
        <div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>
    `).join("");
}

function renderQuantConnectAlerts(payload = {}) {
    const container = document.getElementById("qc-alert-list");
    if (!container) return;
    const errors = payload.cloud?.api_errors || [];
    container.innerHTML = errors.slice(0, 4).map((error) => `
        <div class="qc-alert">${escapeHtml(error)}</div>
    `).join("");
}

function renderQuantConnectPositions(positions = []) {
    const body = document.getElementById("qc-positions-body");
    const empty = document.getElementById("qc-positions-empty");
    if (!body) return;
    body.innerHTML = "";
    if (!positions.length) {
        if (empty) empty.classList.add("visible");
        return;
    }
    if (empty) empty.classList.remove("visible");
    body.innerHTML = positions.map((position) => `
        <tr>
            <td><strong>${escapeHtml(position.symbol || "-")}</strong></td>
            <td>${escapeHtml(position.quantity ?? "-")}</td>
            <td>${escapeHtml(position.average_price ?? "-")}</td>
            <td>${escapeHtml(position.market_price ?? "-")}</td>
            <td>${escapeHtml(position.market_value ?? "-")}</td>
        </tr>
    `).join("");
}

function quantConnectCashValue(cash = {}) {
    if (!cash || typeof cash !== "object") return null;
    const usd = cash.USD || cash.usd || cash["$"];
    if (usd && typeof usd === "object") {
        return usd.amount ?? usd.Amount ?? usd.value ?? usd.Value;
    }
    if (typeof usd === "number" || typeof usd === "string") {
        return usd;
    }
    const first = Object.values(cash)[0];
    if (first && typeof first === "object") {
        return first.amount ?? first.Amount ?? first.value ?? first.Value;
    }
    return null;
}

async function sendQuantConnectOrder(side) {
    const qtyInput = document.getElementById("qc-order-qty");
    const resultEl = document.getElementById("qc-order-result");
    const buttons = [document.getElementById("qc-btn-buy"), document.getElementById("qc-btn-sell")].filter(Boolean);
    const quantity = Number(qtyInput?.value || 1);

    buttons.forEach((button) => { button.disabled = true; });
    if (resultEl) resultEl.textContent = `MNQ ${qcSideLabel(side)} 모의주문을 전송하는 중입니다...`;

    try {
        const result = await postJson(endpoints.quantConnectMnqOrder, { side, quantity });
        if (result.success) {
            if (resultEl) resultEl.textContent = `MNQ ${qcSideLabel(side)} 명령을 QuantConnect Paper에 전송했습니다.`;
        } else {
            if (resultEl) resultEl.textContent = result.error || "QuantConnect 주문 명령 전송에 실패했습니다.";
        }
        loadQuantConnectTab();
    } catch (error) {
        if (resultEl) resultEl.textContent = error.message;
    } finally {
        buttons.forEach((button) => { button.disabled = false; });
    }
}

async function deployQuantConnectPaper() {
    const resultEl = document.getElementById("qc-order-result");
    const deployButton = document.getElementById("qc-btn-deploy");
    const orderButtons = [document.getElementById("qc-btn-buy"), document.getElementById("qc-btn-sell")].filter(Boolean);
    if (deployButton) deployButton.disabled = true;
    orderButtons.forEach((button) => { button.disabled = true; });
    if (resultEl) resultEl.textContent = "QuantConnect Paper Live 배포를 시작합니다. 컴파일과 노드 할당에 시간이 걸릴 수 있습니다...";

    try {
        const result = await postJson(endpoints.quantConnectMnqDeploy, {});
        if (result.success) {
            const deployId = result.deploy_id ? ` Deploy ID: ${result.deploy_id}` : "";
            if (resultEl) resultEl.textContent = `QuantConnect Paper Live 배포 요청이 완료됐습니다.${deployId}`;
        } else if (resultEl) {
            resultEl.textContent = result.error || "QuantConnect Paper Live 배포에 실패했습니다.";
        }
        loadQuantConnectTab();
    } catch (error) {
        if (resultEl) resultEl.textContent = error.message;
    } finally {
        if (deployButton) deployButton.disabled = false;
        orderButtons.forEach((button) => { button.disabled = false; });
    }
}

async function sendQuantConnectSignalOrder(signal, requestedSide = "") {
    const resultEl = document.getElementById("qc-signal-order-result");
    const qc = qcSignalEligibility(signal);
    if (!qc.allowed) {
        if (resultEl) resultEl.textContent = `주문 불가: ${qc.reason}`;
        return;
    }
    const side = requestedSide || qc.side;
    if (side !== qc.side) {
        if (resultEl) resultEl.textContent = `주문 불가: 이 신호는 ${qcSideLabel(qc.side)} 방향입니다.`;
        return;
    }
    const buttons = Array.from(document.querySelectorAll("[data-qc-signal-id], [data-qc-detail-side]"));
    buttons.forEach((button) => { button.disabled = true; });
    if (resultEl) resultEl.textContent = `${signal.provider} 신호 #${signal.id}를 MNQ ${qcSideLabel(side)} 주문으로 전송하는 중입니다...`;
    let finalMessage = "";

    try {
        const result = await postJson(endpoints.quantConnectMnqOrder, {
            side,
            quantity: 1,
            signal_id: signal.id,
            provider: signal.provider,
            signal_time: signal.signalTime,
        });
        if (result.success) {
            finalMessage = `신호 #${signal.id} MNQ ${qcSideLabel(side)} 명령을 QuantConnect Paper에 전송했습니다.`;
        } else {
            finalMessage = result.error || "QuantConnect 신호 주문 전송에 실패했습니다.";
        }
        loadQuantConnectTab();
    } catch (error) {
        finalMessage = error.message;
    } finally {
        renderSignals();
        renderDetails(state.signals.find((item) => item.id === state.selectedId) || null);
        if (resultEl && finalMessage) resultEl.textContent = finalMessage;
    }
}

async function loadQuantConnectTab() {
    try {
        const payload = await fetchJson(endpoints.quantConnectMnq);
        const algorithm = payload.algorithm || {};
        const account = payload.account || {};
        const metrics = payload.metrics || {};
        const positions = payload.positions || [];
        const orders = payload.orders || [];
        const deployment = payload.deployment || {};
        const cloud = payload.cloud || {};
        const live = cloud.live || {};
        const project = cloud.project || {};
        const portfolio = cloud.portfolio || {};

        setStatusItem("qc-ready-item", Boolean(payload.project_ready), payload.project_ready ? "준비됨" : "파일 없음");
        setStatusItem("qc-auth-item", Boolean(payload.auth?.success), payload.auth?.success ? "정상" : "설정 필요");
        setStatusItem("qc-cloud-item", Boolean(payload.auth?.project_configured), payload.auth?.project_configured ? "설정됨" : "대기");
        setText("qc-auth", payload.auth?.success ? "정상" : "설정 필요");
        setText("qc-cloud-sync", payload.auth?.project_configured ? "설정됨" : "대기");
        setStatusItem("qc-live-status-item", live.status === "Running", live.status || "-");
        setText("qc-live-status", live.status || "-");
        setText("qc-project-name", project.name || project.id || "-");
        setText("qc-deploy-id", live.deploy_id || "-");
        setText("qc-as-of", formatTime(payload.as_of));
        setText("qc-symbol", algorithm.symbol || "MNQ");
        setText("qc-max-contracts", algorithm.max_contracts || "1");
        setText("qc-portfolio-value", formatMoney(account.portfolio_value ?? account.total_portfolio_value ?? portfolio.total_portfolio_value));
        setText("qc-cash", formatMoney(quantConnectCashValue(portfolio.cash)));
        setText("qc-pnl", formatMoney(metrics.pnl ?? metrics.total_pnl));
        setText("qc-positions", positions.length);
        setText("qc-orders", orders.length);
        setText("qc-algo-file", algorithm.exists ? "준비됨" : "없음");
        setText("qc-config-file", payload.files?.config?.exists ? "준비됨" : "없음");
        setText("qc-results-file", payload.files?.results?.exists ? "있음" : "대기");
        setText("qc-message", quantConnectMessage(payload));

        renderQuantConnectOrders(orders);
        renderQuantConnectPositions(positions);
        renderQuantConnectAlerts(payload);
        renderQuantConnectFiles(payload);

        const buyButton = document.getElementById("qc-btn-buy");
        const sellButton = document.getElementById("qc-btn-sell");
        const deployButton = document.getElementById("qc-btn-deploy");
        const liveRunning = live.status === "Running";
        [buyButton, sellButton].filter(Boolean).forEach((button) => {
            button.disabled = !liveRunning;
            button.title = liveRunning ? "" : "Paper Live 실행 후 주문할 수 있습니다.";
        });
        if (deployButton) {
            deployButton.disabled = liveRunning;
            deployButton.textContent = liveRunning ? "Paper Live 실행 중" : "Paper Live 시작";
        }
        if (buyButton && !buyButton.dataset.bound) {
            buyButton.dataset.bound = "1";
            buyButton.addEventListener("click", () => sendQuantConnectOrder("buy"));
        }
        if (sellButton && !sellButton.dataset.bound) {
            sellButton.dataset.bound = "1";
            sellButton.addEventListener("click", () => sendQuantConnectOrder("sell"));
        }
        if (deployButton && !deployButton.dataset.bound) {
            deployButton.dataset.bound = "1";
            deployButton.addEventListener("click", deployQuantConnectPaper);
        }
    } catch (error) {
        setText("qc-message", `QuantConnect 상태를 불러오지 못했습니다: ${error.message}`);
        console.warn("Failed to load QuantConnect MNQ status", error);
    }
}

function renderSignals() {
    const container = document.getElementById("signal-table-body");
    const empty = document.getElementById("signal-empty");
    if (!container || !empty) return;
    container.innerHTML = "";
    empty.classList.toggle("visible", state.signals.length === 0);

    const channels = {};
    state.signals.forEach(signal => {
        const ch = signal.channel || signal.provider || "unknown";
        if (!channels[ch]) channels[ch] = { entry: [], exit: [] };
        if (signal.directionType === "exit") {
            channels[ch].exit.push(signal);
        } else {
            channels[ch].entry.push(signal);
        }
    });

    const channelLabels = { goldmoon: "GoldMoon", chart_leader: "차트리더", jurin_6: "주린스쿨" };
    
    // Mock trades lookup
    const mockTrades = state.mockTrades || [];
    const getMockResult = (signal) => {
        const rawText = signal.rawText || "";
        for (const trade of mockTrades) {
            if (trade.raw_signal && rawText.includes(trade.raw_signal.substring(0, 20))) {
                const pnl = trade.pnl || 0;
                const pnlClass = pnl >= 0 ? "color:#22c55e" : "color:#ef4444";
                return `<span style="${pnlClass};font-weight:bold;">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</span>`;
            }
        }
        return "-";
    };

    Object.keys(channels).sort().forEach(ch => {
        const label = channelLabels[ch] || ch;

        const headerRow = document.createElement("tr");
        headerRow.className = "channel-header-row";
        headerRow.innerHTML = `
            <td colspan="11">
                <strong>${escapeHtml(label)}</strong>
            </td>
        `;
        container.appendChild(headerRow);

        if (channels[ch].entry.length > 0) {
            const entryRow = document.createElement("tr");
            entryRow.className = "direction-header-row entry";
            entryRow.innerHTML = `<td colspan="11">진입 (${channels[ch].entry.length})</td>`;
            container.appendChild(entryRow);

            channels[ch].entry.forEach(signal => {
                const row = document.createElement("tr");
                const directionLabel = signal.direction === "short" ? "SHORT" : "LONG";
                const directionClass = signal.direction === "short" ? "short" : "long";
                row.className = signal.id === state.selectedId ? "selected" : "";
                const mockResult = getMockResult(signal);
                row.innerHTML = `
                    <td>${escapeHtml(formatTime(signal.signalTime))}</td>
                    <td>${escapeHtml(label)}</td>
                    <td><strong>${escapeHtml(signal.symbol)}</strong></td>
                    <td><span class="direction-pill direction-${directionClass}">${directionLabel}</span></td>
                    <td>${escapeHtml(signal.entry)}</td>
                    <td>${escapeHtml(signal.stopLoss)}</td>
                    <td>${escapeHtml(signal.takeProfit)}</td>
                    <td>${escapeHtml(formatRate(signal.confidence))}</td>
                    <td>${mockResult}</td>
                    <td><span class="status-badge status-${signal.parseStatus}">${escapeHtml(signal.parseStatus)}</span></td>
                    <td>${qcSignalOrderCell(signal)}</td>
                `;
                row.addEventListener("click", () => selectSignal(signal.id));
                container.appendChild(row);
            });
        }

        if (channels[ch].exit.length > 0) {
            const exitRow = document.createElement("tr");
            exitRow.className = "direction-header-row exit";
            exitRow.innerHTML = `<td colspan="11">청산 (${channels[ch].exit.length})</td>`;
            container.appendChild(exitRow);

            channels[ch].exit.forEach(signal => {
                const row = document.createElement("tr");
                row.className = signal.id === state.selectedId ? "selected" : "";
                row.innerHTML = `
                    <td>${escapeHtml(formatTime(signal.signalTime))}</td>
                    <td>${escapeHtml(label)}</td>
                    <td><strong>${escapeHtml(signal.symbol)}</strong></td>
                    <td><span class="direction-pill direction-exit">EXIT</span></td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td>${escapeHtml(formatRate(signal.confidence))}</td>
                    <td>-</td>
                    <td><span class="status-badge status-verified">${escapeHtml(signal.direction)}</span></td>
                    <td><span class="status-badge status-pending">청산 미지원</span></td>
                `;
                row.addEventListener("click", () => selectSignal(signal.id));
                container.appendChild(row);
            });
        }
    });
}

function selectSignal(id) {
    state.selectedId = id;
    const signal = state.signals.find((item) => item.id === id) || state.signals[0];
    renderSignals();
    renderDetails(signal);
}

function renderDetails(signal) {
    const raw = document.getElementById("raw-message");
    const details = document.getElementById("parsed-detail");
    const badges = document.getElementById("detail-badges");
    const qcEligibilityEl = document.getElementById("qc-signal-eligibility");
    const qcResultEl = document.getElementById("qc-signal-order-result");
    const qcBuyButton = document.getElementById("btn-qc-signal-buy");
    const qcSellButton = document.getElementById("btn-qc-signal-sell");
    if (!raw || !details || !badges) return;
    if (!signal) {
        raw.textContent = "신호를 선택하면 Telegram 원문이 표시됩니다.";
        details.innerHTML = "";
        badges.innerHTML = "";
        if (qcEligibilityEl) qcEligibilityEl.textContent = "신호를 선택하면 주문 가능 여부를 표시합니다.";
        if (qcResultEl) qcResultEl.textContent = "MNQ 진입 신호만 QuantConnect Paper 주문으로 보낼 수 있습니다.";
        [qcBuyButton, qcSellButton].filter(Boolean).forEach((button) => {
            button.disabled = true;
            delete button.dataset.signalId;
        });
        return;
    }

    raw.textContent = signal.rawText || "원문 메시지가 API 응답에 포함되지 않았습니다.";
    badges.innerHTML = `
        <span class="status-badge status-${statusBucket(signal.parseStatus)}">Parse ${escapeHtml(resultLabel(signal.parseStatus))}</span>
        <span class="${badgeClass(signal.verificationStatus)}">Verification ${escapeHtml(resultLabel(signal.verificationStatus))}</span>
        <span class="confidence-badge">Confidence ${escapeHtml(formatRate(signal.confidence))}</span>
    `;
    const fields = [
        ["제공자", signal.provider],
        ["종목", `${signal.symbol}${signal.exchange ? ` / ${signal.exchange}` : ""}`],
        ["방향", signal.direction.toUpperCase()],
        ["진입가", signal.entry],
        ["손절가", signal.stopLoss],
        ["목표가", signal.takeProfit],
        ["파싱 상태", resultLabel(signal.parseStatus)],
        ["검증 상태", resultLabel(signal.verificationStatus)],
        ["신뢰도", formatRate(signal.confidence)],
        ["손익", signal.pnlPoints === null ? "-" : `${signal.pnlPoints} pt`],
    ];
    details.innerHTML = fields.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");

    const qc = qcSignalEligibility(signal);
    if (qcEligibilityEl) {
        qcEligibilityEl.textContent = qc.allowed
            ? `${signal.provider} #${signal.id}: ${qc.reason}, 수량 1`
            : `${signal.provider} #${signal.id}: ${qc.reason}`;
    }
    if (qcResultEl && !qcResultEl.dataset.busy) {
        qcResultEl.textContent = qc.allowed
            ? "버튼을 누르면 선택한 신호 ID가 포함된 QC 모의주문 명령을 보냅니다."
            : "이 신호는 현재 MNQ QC 주문 대상이 아닙니다.";
    }
    [qcBuyButton, qcSellButton].filter(Boolean).forEach((button) => {
        const side = button.dataset.qcDetailSide;
        button.dataset.signalId = signal.id;
        button.disabled = !qc.allowed || side !== qc.side;
    });
}

function renderChart(summary = {}) {
    const canvas = document.getElementById("futures-performance-chart");
    const placeholder = document.getElementById("chart-placeholder");
    const series = summary.performance || summary.chart || {};
    const labels = series.labels || [];
    const pnl = series.pnl || series.cumulative_pnl || [];
    const winRate = series.win_rate || series.winRate || [];

    if (!canvas || !window.Chart || labels.length === 0 || pnl.length === 0) {
        if (placeholder) placeholder.classList.add("visible");
        if (state.chart) {
            state.chart.destroy();
            state.chart = null;
        }
        return;
    }

    if (placeholder) placeholder.classList.remove("visible");
    if (state.chart) {
        state.chart.destroy();
    }
    state.chart = new Chart(canvas, {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "누적 손익(pt)",
                    data: pnl,
                    borderColor: "#22c55e",
                    backgroundColor: "rgba(34, 197, 94, 0.14)",
                    fill: true,
                    tension: 0.35,
                    yAxisID: "y",
                },
                {
                    label: "승률(%)",
                    data: winRate,
                    borderColor: "#22d3ee",
                    backgroundColor: "rgba(34, 211, 238, 0.08)",
                    fill: false,
                    tension: 0.35,
                    yAxisID: "y1",
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: "#c5d1df" } } },
            scales: {
                x: { ticks: { color: "#96a4b6" }, grid: { color: "rgba(255,255,255,0.05)" } },
                y: { ticks: { color: "#96a4b6" }, grid: { color: "rgba(255,255,255,0.05)" } },
                y1: { position: "right", ticks: { color: "#96a4b6" }, grid: { drawOnChartArea: false } },
            },
        },
    });
}

let mockChart = null;

function renderMockChart(mockData = {}) {
    const canvas = document.getElementById("mock-trading-chart");
    const placeholder = document.getElementById("mock-chart-placeholder");
    
    if (!mockData || !canvas || !window.Chart) {
        if (placeholder) placeholder.classList.add("visible");
        return;
    }
    
    const trades = mockData.recent_trades || [];
    if (trades.length === 0) {
        if (placeholder) placeholder.classList.add("visible");
        return;
    }
    
    if (placeholder) placeholder.classList.remove("visible");
    
    // Build cumulative PnL data
    let cumulative = 0;
    const labels = [];
    const pnlData = [];
    
    trades.forEach((trade, idx) => {
        cumulative += trade.pnl || 0;
        labels.push(`#${idx + 1}`);
        pnlData.push(cumulative);
    });
    
    if (mockChart) {
        mockChart.destroy();
    }
    
    mockChart = new Chart(canvas, {
        type: "line",
        data: {
            labels,
            datasets: [{
                label: "누적 손익 ($)",
                data: pnlData,
                borderColor: "#22c55e",
                backgroundColor: "rgba(34, 197, 94, 0.14)",
                fill: true,
                tension: 0.35,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: "#c5d1df" } } },
            scales: {
                x: { ticks: { color: "#96a4b6" }, grid: { color: "rgba(255,255,255,0.05)" } },
                y: { ticks: { color: "#96a4b6" }, grid: { color: "rgba(255,255,255,0.05)" } },
            },
        },
    });
}

async function fetchJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
}

async function postJson(url, payload) {
    const response = await fetch(url, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || data.error || `${response.status} ${response.statusText}`);
    }
    return data;
}

async function updatePolldStatus() {
    const polldItem = document.getElementById("polld-status-item");
    const polldStatusEl = document.getElementById("polld-status");
    if (!polldStatusEl) return;
    try {
        const res = await fetch('/api/futures-signals/collector/status');
        if (!res.ok) throw new Error(res.statusText);
        const data = await res.json();
        if (polldItem) polldItem.classList.remove("ok", "warn");
        if (data.running || data.connected) {
            if (polldItem) polldItem.classList.add("ok");
            polldStatusEl.textContent = '실행 중';
        } else if (data.configured) {
            if (polldItem) polldItem.classList.add("warn");
            polldStatusEl.textContent = '설정됨 (미시작)';
        } else {
            if (polldItem) polldItem.classList.add("warn");
            polldStatusEl.textContent = '미설정';
        }
    } catch (e) {
        console.error('polld status error:', e);
        if (polldItem) { polldItem.classList.remove("ok"); polldItem.classList.add("warn"); }
        polldStatusEl.textContent = '확인 불가';
    }
}

async function refreshDashboard() {
    const refreshButton = document.getElementById("btn-refresh-futures");
    const lastUpdated = document.getElementById("last-updated");
    if (refreshButton) refreshButton.disabled = true;
    if (lastUpdated) lastUpdated.textContent = "갱신 중";

    try {
        const [summary, signalsPayload, balance, envData, mockData, futuresBalance, futuresPositions] = await Promise.all([
            fetchJson(endpoints.summary),
            fetchJson(endpoints.signals),
            fetchJson("/api/balance").catch(() => null),
            fetchJson("/api/env").catch(() => null),
            fetchJson("/api/mock-trading/summary").catch(() => null),
            fetchJson(endpoints.futuresBalance).catch(() => null),
            fetchJson(endpoints.futuresPositions).catch(() => null),
        ]);

        // Render account info
        // Get TOTAL_CAPITAL and KISTOCK_ACCOUNT from env
        let totalCapital = 10000000;
        let accountNumber = "-";
        const envItems = envData?.fields || envData?.items || [];
        const tcItem = envItems.find(i => i.key === "TOTAL_CAPITAL");
        if (tcItem?.value) totalCapital = Number(tcItem.value) || totalCapital;
        const acItem = envItems.find(i => i.key === "KISTOCK_ACCOUNT");
        if (acItem?.value) accountNumber = acItem.value;
        
        // Check if futures account (6开头 = 선물/옵션)
        const isFuturesAccount = accountNumber?.startsWith("6");
        
        // API connection status
        const apiStatusItem = document.getElementById("api-connection-status");
        const apiStatusEl = document.getElementById("account-api-status");
        
        if (isFuturesAccount) {
            const hasFuturesBalance = futuresBalance && !futuresBalance.error;
            if (apiStatusItem) {
                apiStatusItem.classList.remove("ok", "warn");
                apiStatusItem.classList.add(hasFuturesBalance ? "ok" : "warn");
            }
            if (apiStatusEl) apiStatusEl.textContent = hasFuturesBalance ? "연결됨" : "연결안됨";
            setText("account-balance", formatNumber(futuresBalance?.total_assets ?? 0));
            setText("account-cash", formatNumber(futuresBalance?.cash ?? 0));
            setText("account-stock-eval", formatNumber(futuresBalance?.margin ?? 0));
            setText("account-positions", futuresPositions?.positions ? futuresPositions.positions.length : 0);
        } else if (balance) {
            // 국내주식 계좌
            if (apiStatusItem) { apiStatusItem.classList.remove("warn"); apiStatusItem.classList.add("ok"); }
            if (apiStatusEl) apiStatusEl.textContent = "연결됨";
            setText("account-balance", formatNumber(balance.total_eval));
            setText("account-cash", formatNumber(balance.cash));
            setText("account-stock-eval", formatNumber(balance.stock_eval));
            setText("account-positions", balance.holdings ? balance.holdings.length : 0);
        } else {
            if (apiStatusItem) { apiStatusItem.classList.remove("ok"); apiStatusItem.classList.add("warn"); }
            if (apiStatusEl) apiStatusEl.textContent = "연결안됨";
        }
        
        // Trading status from env
        const tradingEnv = envItems.find(i => i.key === "TRADING_ENV");
        const dryRun = envItems.find(i => i.key === "DRY_RUN");
        const autoApproval = envItems.find(i => i.key === "REQUIRE_APPROVAL");
        
        const envStatus = (val) => val === true || val === "true" ? "ON" : "OFF";
        
        const setEnvStatus = (id, value, isWarn = false) => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = value;
                const item = el.closest(".status-item");
                if (item) {
                    item.classList.remove("ok", "warn");
                    item.classList.add(isWarn ? "warn" : "ok");
                }
            }
        };
        
        setEnvStatus("trading-env", tradingEnv?.value === "real" ? "실전" : "모의");
        setEnvStatus("dry-run", envStatus(dryRun?.value), dryRun?.value === true || dryRun?.value === "true");
        setEnvStatus("auto-approval", autoApproval?.value === "false" ? "ON" : "OFF", autoApproval?.value === "false");
        
        setText("account-number", accountNumber);
        setText("account-total-capital", formatNumber(totalCapital));

        updatePolldStatus();

        state.signals = normalizeList(signalsPayload).map(normalizeSignal);
        state.summary = summary;
        state.mockTrades = (mockData?.recent_trades || []);
        renderSummary(summary, state.signals);
        renderMockTrading(mockData);
        renderMockChart(mockData);
        renderChart(summary);
        renderSignals();
        selectSignal(state.signals[0]?.id ?? null);
        if (document.getElementById("qc-project-ready")) {
            loadQuantConnectTab();
        }
        if (lastUpdated) lastUpdated.textContent = `${new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit" }).format(new Date())} 갱신`;
    } catch (error) {
        state.signals = [];
        renderSummary({}, []);
        renderChart({});
        renderSignals();
        renderDetails(null);
        if (lastUpdated) lastUpdated.textContent = "API 대기";
        console.warn("Failed to load futures signals dashboard", error);
    } finally {
        if (refreshButton) refreshButton.disabled = false;
    }
}

document.getElementById("btn-refresh-futures")?.addEventListener("click", refreshDashboard);
document.addEventListener("click", (event) => {
    const signalOrderButton = event.target.closest("[data-qc-signal-id]");
    if (signalOrderButton) {
        event.preventDefault();
        event.stopPropagation();
        const signal = state.signals.find((item) => String(item.id) === String(signalOrderButton.dataset.qcSignalId));
        sendQuantConnectSignalOrder(signal, signalOrderButton.dataset.qcSide);
        return;
    }

    const detailOrderButton = event.target.closest("[data-qc-detail-side]");
    if (detailOrderButton) {
        event.preventDefault();
        const signalId = detailOrderButton.dataset.signalId || state.selectedId;
        const signal = state.signals.find((item) => String(item.id) === String(signalId));
        sendQuantConnectSignalOrder(signal, detailOrderButton.dataset.qcDetailSide);
        return;
    }

    const btn = event.target.closest("#btn-refresh-signals");
    if (!btn) {
        return;
    }
    if (btn) {
        btn.disabled = true;
        btn.textContent = "🔄 조회中...";
        setTimeout(() => {
            refreshDashboard();
            btn.disabled = false;
            btn.textContent = "🔄 재조회";
        }, 500);
    }
});
iconize();
refreshDashboard();

// Order Tab Functions
async function loadFuturesBalance() {
    try {
        const response = await fetchJson(endpoints.futuresBalance);
        if (response.error) {
            setText("order-cash", "연결안됨");
            setText("order-available", "-");
            return;
        }
        setText("order-cash", formatNumber(response.cash));
        setText("order-available", formatNumber(response.orderable_cash || response.cash));
    } catch (e) {
        setText("order-cash", "에러");
        setText("order-available", "-");
    }
}

async function loadPendingOrders() {
    try {
        const response = await fetchJson(endpoints.futuresPositions);
        const tbody = document.getElementById("pending-orders-body");
        const empty = document.getElementById("pending-empty");
        if (!tbody) return;
        
        tbody.innerHTML = "";
        const positions = response.positions || [];
        
        if (positions.length === 0) {
            if (empty) empty.classList.add("visible");
        } else {
            if (empty) empty.classList.remove("visible");
            positions.forEach(pos => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${pos.ord_tmd || "-"}</td>
                    <td>${pos.ovrs_futr_fx_pdno || "-"}</td>
                    <td class="${pos.sll_buy_dvsn_cd === "01" ? "text-sell" : "text-buy"}">${pos.sll_buy_dvsn_cd === "01" ? "매도" : "매수"}</td>
                    <td>${pos.ord_qty || "-"}</td>
                    <td>${pos.ord_prc || "시장가"}</td>
                    <td>${pos.ccld_qty ? "부분체결" : "미체결"}</td>
                    <td><button class="btn-cancel" data-order="${pos.ord_odno || ""}" data-order-date="${pos.ord_dt || pos.orgn_ord_dt || ""}">취소</button></td>
                `;
                tbody.appendChild(tr);
            });
        }

        tbody.querySelectorAll(".btn-cancel").forEach((button) => {
            button.addEventListener("click", async (event) => {
                event.stopPropagation();
                const orderNo = button.dataset.order;
                const orderDate = button.dataset.orderDate || "";
                if (!orderNo) return;
                button.disabled = true;
                try {
                    const result = await fetch(endpoints.futuresCancel, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({order_no: orderNo, order_date: orderDate}),
                    }).then((response) => response.json());
                    if (!result.success) {
                        console.warn("Failed to cancel futures order", result.error || result);
                    }
                    loadPendingOrders();
                    loadOrderHistory();
                } catch (error) {
                    console.error("Failed to cancel futures order", error);
                    button.disabled = false;
                }
            });
        });
    } catch (e) {
        console.error("Failed to load pending orders", e);
    }
}

async function loadOrderHistory() {
    const today = new Date().toISOString().split("T")[0].replace(/-/g, "");
    try {
        const response = await fetchJson(`${endpoints.futuresOrders}?start_date=${today}&end_date=${today}`);
        const tbody = document.getElementById("order-history-body");
        const empty = document.getElementById("orders-empty");
        if (!tbody) return;
        
        tbody.innerHTML = "";
        const orders = response.orders || [];
        
        if (orders.length === 0) {
            if (empty) empty.classList.add("visible");
        } else {
            if (empty) empty.classList.remove("visible");
            orders.forEach(order => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${order.ord_tmd || "-"}</td>
                    <td>${order.ovrs_futr_fx_pdno || "-"}</td>
                    <td class="${order.sll_buy_dvsn_cd === "01" ? "text-sell" : "text-buy"}">${order.sll_buy_dvsn_cd === "01" ? "매도" : "매수"}</td>
                    <td>${order.ord_qty || "-"}</td>
                    <td>${order.ord_prc || "시장가"}</td>
                    <td>${order.ccld_qty || "0"}</td>
                `;
                tbody.appendChild(tr);
            });
        }
    } catch (e) {
        console.error("Failed to load order history", e);
    }
}

async function placeOrder(type) {
    const symbol = document.getElementById("order-symbol")?.value?.trim().toUpperCase();
    const qty = document.getElementById("order-qty")?.value;
    const priceInput = document.getElementById("order-price")?.value;
    const price = priceInput === "시장가" ? "0" : (priceInput || "0");
    const resultDiv = document.getElementById("order-result");
    
    if (!symbol) {
        if (resultDiv) resultDiv.innerHTML = '<span style="color:#ef4444">종목코드를 입력하세요</span>';
        return;
    }
    if (!qty || parseInt(qty) <= 0) {
        if (resultDiv) resultDiv.innerHTML = '<span style="color:#ef4444">수량을 입력하세요</span>';
        return;
    }
    
    if (resultDiv) resultDiv.innerHTML = '<span style="color:#94a3b8">주문 전송中...</span>';
    
    try {
        const response = await fetch(endpoints.futuresOrder, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({symbol, type, qty, price})
        });
        const result = await response.json();
        
        if (result.success) {
            if (resultDiv) resultDiv.innerHTML = `<span style="color:#22c55e">주문성공! 주문번호: ${result.order_no}</span>`;
            loadPendingOrders();
            loadOrderHistory();
        } else {
            if (resultDiv) resultDiv.innerHTML = `<span style="color:#ef4444">주문실패: ${result.error}</span>`;
        }
    } catch (e) {
        if (resultDiv) resultDiv.innerHTML = `<span style="color:#ef4444">주문실패: ${e.message}</span>`;
    }
}

function initOrderTab() {
    const btnBuy = document.getElementById("btn-buy");
    const btnSell = document.getElementById("btn-sell");
    
    if (btnBuy) btnBuy.addEventListener("click", () => placeOrder("buy"));
    if (btnSell) btnSell.addEventListener("click", () => placeOrder("sell"));
    
    loadFuturesBalance();
    loadPendingOrders();
    loadOrderHistory();
}

// Make initOrderTab available globally
window.initOrderTab = initOrderTab;

// Performance Tab Functions
let perfChart = null;

async function loadPerformanceTab() {
    const endpoints = {
        mockSummary: "/api/mock-trading/summary",
        mockTrades: "/api/mock-trading/trades",
    };
    
    try {
        const [summary, trades] = await Promise.all([
            fetchJson(endpoints.mockSummary).catch(() => null),
            fetchJson(endpoints.mockTrades).catch(() => null),
        ]);
        
        const data = summary || {};
        const tradeList = trades?.trades || data.recent_trades || [];
        
        // Update metrics
        setText("perf-total-signals", tradeList.length);
        setText("perf-total-pnl", (data.total_pnl || 0) >= 0 ? `+$${(data.total_pnl || 0).toFixed(2)}` : `-$${Math.abs(data.total_pnl || 0).toFixed(2)}`);
        setText("perf-win-rate", (data.win_rate || 0) + "%");
        setText("perf-avg-pnl", `$${(data.avg_pnl || 0).toFixed(2)}`);
        
        // Verification stats
        const verifiedWin = tradeList.filter(t => (t.pnl || 0) > 0).length;
        const verifiedLoss = tradeList.filter(t => (t.pnl || 0) < 0).length;
        const pending = 0;
        
        setText("perf-verified-win", verifiedWin);
        setText("perf-verified-loss", verifiedLoss);
        setText("perf-pending", pending);
        
        // By symbol stats
        const bySymbol = {};
        tradeList.forEach(t => {
            if (!bySymbol[t.symbol]) bySymbol[t.symbol] = { count: 0, wins: 0, pnl: 0 };
            bySymbol[t.symbol].count++;
            if ((t.pnl || 0) > 0) bySymbol[t.symbol].wins++;
            bySymbol[t.symbol].pnl += t.pnl || 0;
        });
        
        const symbolBody = document.getElementById("perf-by-symbol-body");
        if (symbolBody) {
            symbolBody.innerHTML = Object.entries(bySymbol).map(([sym, s]) => `
                <tr>
                    <td>${sym}</td>
                    <td>${s.count}</td>
                    <td>${((s.wins / s.count) * 100).toFixed(0)}%</td>
                    <td style="color: ${s.pnl >= 0 ? "#22c55e" : "#ef4444"}">$${s.pnl.toFixed(2)}</td>
                </tr>
            `).join("");
        }
        
        // By provider stats
        const byProvider = {};
        tradeList.forEach(t => {
            const provider = t.provider || t.channel || "unknown";
            if (!byProvider[provider]) byProvider[provider] = { count: 0, wins: 0, pnl: 0 };
            byProvider[provider].count++;
            if ((t.pnl || 0) > 0) byProvider[provider].wins++;
            byProvider[provider].pnl += t.pnl || 0;
        });
        
        const providerBody = document.getElementById("perf-by-provider-body");
        if (providerBody) {
            providerBody.innerHTML = Object.entries(byProvider).map(([p, s]) => `
                <tr>
                    <td>${p}</td>
                    <td>${s.count}</td>
                    <td>${((s.wins / s.count) * 100).toFixed(0)}%</td>
                    <td style="color: ${s.pnl >= 0 ? "#22c55e" : "#ef4444"}">$${s.pnl.toFixed(2)}</td>
                </tr>
            `).join("");
        }
        
        // Trades table
        const tradesBody = document.getElementById("perf-trades-body");
        const tradesEmpty = document.getElementById("perf-trades-empty");
        
        if (tradesBody) {
            if (tradeList.length === 0) {
                if (tradesEmpty) tradesEmpty.classList.add("visible");
            } else {
                if (tradesEmpty) tradesEmpty.classList.remove("visible");
                tradesBody.innerHTML = tradeList.slice(0, 20).map(t => `
                    <tr>
                        <td>${t.time || t.date || "-"}</td>
                        <td>${t.symbol || "-"}</td>
                        <td><span class="direction-pill direction-${t.side === "sell" ? "short" : "long"}">${t.side?.toUpperCase() || "-"}</span></td>
                        <td>$${t.entry_price?.toFixed(2) || "-"}</td>
                        <td>$${t.exit_price?.toFixed(2) || "-"}</td>
                        <td style="color: ${(t.pnl || 0) >= 0 ? "#22c55e" : "#ef4444"}">${(t.pnl || 0) >= 0 ? "+" : ""}$${t.pnl?.toFixed(2) || "0.00"}</td>
                        <td><span class="status-badge status-${t.exit_reason?.includes("tp") || t.exit_reason === "take_profit" ? "verified" : t.exit_reason?.includes("sl") ? "rejected" : "pending"}">${t.exit_reason || "pending"}</span></td>
                    </tr>
                `).join("");
            }
        }
        
        // Daily chart
        const dailyData = {};
        tradeList.forEach(t => {
            const date = (t.time || t.date || "").substring(0, 10);
            if (!dailyData[date]) dailyData[date] = 0;
            dailyData[date] += t.pnl || 0;
        });
        
        const chartCanvas = document.getElementById("perf-daily-chart");
        if (chartCanvas && window.Chart) {
            const labels = Object.keys(dailyData).sort();
            const pnlData = labels.map(d => dailyData[d]);
            
            if (perfChart) perfChart.destroy();
            perfChart = new Chart(chartCanvas, {
                type: "bar",
                data: {
                    labels,
                    datasets: [{
                        label: "일별 손익 ($)",
                        data: pnlData,
                        backgroundColor: pnlData.map(v => v >= 0 ? "#22c55e" : "#ef4444"),
                        borderRadius: 4,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: "#c5d1df" } } },
                    scales: {
                        x: { ticks: { color: "#96a4b6" }, grid: { color: "rgba(255,255,255,0.05)" } },
                        y: { ticks: { color: "#96a4b6" }, grid: { color: "rgba(255,255,255,0.05)" } },
                    },
                },
            });
        }
        
    } catch (e) {
        console.error("Failed to load performance tab", e);
    }
}

window.loadPerformanceTab = loadPerformanceTab;
window.loadQuantConnectTab = loadQuantConnectTab;

// ===== Executor State (스위치) =====
async function loadExecutorState() {
    try {
        const res = await fetch('/api/futures-signals/executor/state');
        if (!res.ok) return;
        const state = await res.json();

        const setChecked = (id, val) => { const el = document.getElementById(id); if (el) el.checked = Boolean(val); };
        const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };

        setChecked('switch-live-trading', state.live_trading_enabled);
        setChecked('switch-bybit', state.bybit_enabled);
        setChecked('switch-mock', state.mock_enabled);
        setChecked('switch-kis-demo', state.kis_demo_enabled);
        setVal('default-qty', state.default_qty ?? 1);
        setVal('polling-interval', state.polling_interval_sec ?? 30);

        // Live badge labels
        const liveStatusEl = document.getElementById('live-status');
        const bybitStatusEl = document.getElementById('bybit-status');
        if (liveStatusEl) {
            liveStatusEl.textContent = state.live_trading_enabled ? '활성' : '비활성';
            liveStatusEl.className = 'status-badge ' + (state.live_trading_enabled ? 'on' : 'off');
        }
        if (bybitStatusEl) {
            bybitStatusEl.textContent = state.bybit_enabled ? '활성' : '비활성';
            bybitStatusEl.className = 'status-badge ' + (state.bybit_enabled ? 'on' : 'off');
        }

        // 실계좌 활성화 여부에 따라 실계좌 성과 영역 표시
        const livePerfContent = document.getElementById('live-perf-content');
        const liveDisabledMsg = document.getElementById('live-disabled-msg');
        if (livePerfContent && liveDisabledMsg) {
            if (state.live_trading_enabled) {
                livePerfContent.style.display = 'grid';
                liveDisabledMsg.style.display = 'none';
            } else {
                livePerfContent.style.display = 'none';
                liveDisabledMsg.style.display = 'block';
            }
        }
    } catch (e) {
        console.warn('Failed to load executor state', e);
    }
}

async function updateExecutorState(key, value) {
    try {
        await fetch('/api/futures-signals/executor/state', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [key]: value }),
        });
        await loadExecutorState();
        if (key === 'live_trading_enabled') loadLivePerformance();
    } catch (e) {
        console.warn('Failed to update executor state', e);
    }
}

// ===== 성과 로드 =====
async function loadMockPerformance() {
    try {
        const res = await fetch('/api/futures-signals/performance/mock');
        if (!res.ok) return;
        const data = await res.json();
        setText('mock-total', data.total_trades ?? '-');
        setText('mock-open', data.open_positions ?? '-');
        const pnl = data.total_pnl ?? null;
        setText('mock-pnl-stat', pnl !== null ? ((pnl >= 0 ? '+' : '') + '$' + Number(pnl).toFixed(2)) : '-');
        setText('mock-win-rate-stat', data.win_rate !== undefined ? data.win_rate + '%' : '-');
    } catch (e) {
        console.warn('Failed to load mock performance', e);
    }
}

async function loadPaperPerformance() {
    try {
        const res = await fetch('/api/futures-signals/performance/paper');
        if (!res.ok) return;
        const data = await res.json();
        if (data.balance !== undefined) {
            setText('paper-balance', typeof data.balance === 'object' ? JSON.stringify(data.balance) : formatNumber(data.balance));
        }
        if (data.cash !== undefined) setText('paper-cash', formatNumber(data.cash));
        if (data.positions !== undefined) {
            setText('paper-positions-count', Array.isArray(data.positions) ? data.positions.length : data.positions);
        }
    } catch (e) {
        console.warn('Failed to load paper performance', e);
    }
}

async function loadLivePerformance() {
    try {
        const res = await fetch('/api/futures-signals/performance/live');
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === 'disabled') return;
        if (data.balance !== undefined) {
            setText('live-balance', typeof data.balance === 'object' ? JSON.stringify(data.balance) : formatNumber(data.balance));
        }
        if (data.cash !== undefined) setText('live-cash', formatNumber(data.cash));
        if (data.positions !== undefined) {
            setText('live-positions-count', Array.isArray(data.positions) ? data.positions.length : data.positions);
        }
    } catch (e) {
        console.warn('Failed to load live performance', e);
    }
}

// ===== Telegram 설정 저장 =====
async function saveTelegramSettings() {
    const apiId = document.getElementById('tg-api-id')?.value?.trim();
    const apiHash = document.getElementById('tg-api-hash')?.value?.trim();
    const channels = document.getElementById('tg-channels')?.value?.trim();
    const statusEl = document.getElementById('auth-status');
    if (statusEl) { statusEl.textContent = '저장 중...'; statusEl.className = 'auth-status'; }
    try {
        const res = await fetch('/api/futures-signals/collector/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_id: apiId, api_hash: apiHash, channels }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && (data.ok !== false)) {
            if (statusEl) { statusEl.textContent = '설정이 저장되었습니다.'; statusEl.className = 'auth-status success'; }
        } else {
            if (statusEl) { statusEl.textContent = '저장 실패: ' + (data.error || res.statusText); statusEl.className = 'auth-status error'; }
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = '오류: ' + e.message; statusEl.className = 'auth-status error'; }
    }
}

// ===== Telegram 인증 =====
async function startTelegramAuth() {
    const phone = document.getElementById('tg-phone')?.value?.trim();
    const statusEl = document.getElementById('auth-status');
    if (!phone) {
        if (statusEl) { statusEl.textContent = '전화번호를 입력하세요.'; statusEl.className = 'auth-status error'; }
        return;
    }
    if (statusEl) { statusEl.textContent = '인증 코드 발송 중...'; statusEl.className = 'auth-status'; }
    try {
        const res = await fetch('/api/futures-signals/collector/auth/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone }),
        });
        const data = await res.json().catch(() => ({}));
        if (data.ok) {
            if (statusEl) { statusEl.textContent = data.message || '인증 코드가 발송되었습니다.'; statusEl.className = 'auth-status success'; }
            const step2 = document.getElementById('auth-step-2');
            if (step2) step2.style.display = 'block';
        } else {
            if (statusEl) { statusEl.textContent = '오류: ' + (data.error || '알 수 없는 오류'); statusEl.className = 'auth-status error'; }
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = '오류: ' + e.message; statusEl.className = 'auth-status error'; }
    }
}

async function verifyTelegramAuth() {
    const code = document.getElementById('tg-code')?.value?.trim();
    const statusEl = document.getElementById('auth-status');
    if (!code) {
        if (statusEl) { statusEl.textContent = '인증 코드를 입력하세요.'; statusEl.className = 'auth-status error'; }
        return;
    }
    if (statusEl) { statusEl.textContent = '인증 중...'; statusEl.className = 'auth-status'; }
    try {
        const res = await fetch('/api/futures-signals/collector/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        const data = await res.json().catch(() => ({}));
        if (data.ok) {
            if (statusEl) { statusEl.textContent = data.message || '인증이 완료되었습니다.'; statusEl.className = 'auth-status success'; }
            const step1 = document.getElementById('auth-step-1');
            const step2 = document.getElementById('auth-step-2');
            if (step1) step1.style.display = 'none';
            if (step2) step2.style.display = 'none';
        } else {
            if (statusEl) { statusEl.textContent = '오류: ' + (data.error || '인증 실패'); statusEl.className = 'auth-status error'; }
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = '오류: ' + e.message; statusEl.className = 'auth-status error'; }
    }
}

// ===== Overview 탭 데이터 로드 =====
async function loadOverviewData() {
    // 신호 요약 (이미 state에 있으면 재사용, 없으면 fetch)
    try {
        const res = await fetch('/api/futures-signals/summary');
        const data = await res.json();
        const totalEl = document.getElementById('overview-total-signals');
        if (totalEl) totalEl.textContent = data.total ?? data.collected_today ?? '-';
    } catch (e) { /* 요소 미존재 시 무시 */ }

    // Telegram 연결 상태
    try {
        const res = await fetch('/api/futures-signals/collector/status');
        const data = await res.json();
        const connEl = document.getElementById('telegram-conn-status');
        if (connEl) connEl.textContent = data.connected ? '연결됨' : '미연결';
        // polld 상태도 갱신
        updatePolldStatus();
    } catch (e) { /* 무시 */ }

    // Mock 성과 요약 (overview 전용 element ID)
    try {
        const res = await fetch('/api/futures-signals/performance/mock');
        const data = await res.json();
        const pnl = data.total_pnl || 0;
        const pnlEl = document.getElementById('overview-mock-pnl');
        if (pnlEl) {
            pnlEl.textContent = (pnl >= 0 ? "+" : "") + "$" + Number(pnl).toFixed(2);
            pnlEl.style.color = pnl >= 0 ? "#22c55e" : "#ef4444";
        }
        setText('overview-mock-win-rate', (data.win_rate ?? 0) + '%');
        setText('overview-mock-closed', data.closed_trades ?? data.total_trades ?? 0);
        setText('overview-mock-open', data.open_positions ?? 0);
    } catch (e) { /* 무시 */ }
}
window.loadOverviewData = loadOverviewData;

// Expose new functions globally
window.loadExecutorState = loadExecutorState;
window.updateExecutorState = updateExecutorState;
window.loadMockPerformance = loadMockPerformance;
window.loadPaperPerformance = loadPaperPerformance;
window.loadLivePerformance = loadLivePerformance;
window.saveTelegramSettings = saveTelegramSettings;
window.startTelegramAuth = startTelegramAuth;
window.verifyTelegramAuth = verifyTelegramAuth;
window.updatePolldStatus = updatePolldStatus;
