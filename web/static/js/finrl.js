const formatCurrency = (value) => {
    return new Intl.NumberFormat('ko-KR', {
        style: 'currency',
        currency: 'KRW',
        maximumFractionDigits: 0
    }).format(Number(value || 0));
};

const formatNumber = (value, digits = 0) => {
    const numeric = Number(value || 0);
    return numeric.toLocaleString(undefined, { maximumFractionDigits: digits });
};

const escapeHtml = (value) => {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
};

const pill = (value, kind = 'hold') => {
    return `<span class="pill pill-${kind}">${escapeHtml(value)}</span>`;
};

const setTableMessage = (selector, colspan, message) => {
    document.querySelector(selector).innerHTML =
        `<tr><td colspan="${colspan}" class="empty-state">${escapeHtml(message)}</td></tr>`;
};

async function fetchJson(url, timeoutMs = 6000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, { signal: controller.signal });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || `Request failed: ${response.status}`);
        }
        return data;
    } catch (err) {
        if (err.name === 'AbortError') {
            throw new Error(`Request timed out: ${url}`);
        }
        throw err;
    } finally {
        clearTimeout(timeoutId);
    }
}

async function postJson(url, payload = {}) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.detail || `Request failed: ${response.status}`);
    }
    return data;
}

async function renderStatus() {
    const data = await fetchJson('/api/finrl/status');
    document.getElementById('finrl-path').textContent = data.path;
    document.getElementById('finrl-license').textContent = data.license;
    document.getElementById('finrl-files').textContent =
        `${formatNumber(data.file_count)} files`;
    document.getElementById('finrl-status').textContent =
        data.exists
            ? `FinRL vendor source loaded: ${data.python_file_count} Python files, ${data.notebook_count} notebooks`
            : 'FinRL vendor source is missing';

    document.getElementById('finrl-modules').innerHTML = data.modules.map((name) => `
        <div class="setting-item">
            <span class="label">Module</span>
            <strong>${escapeHtml(name)}</strong>
        </div>
    `).join('');
}

async function renderPipeline() {
    const data = await fetchJson('/api/finrl/pipeline');
    const tbody = document.querySelector('#table-finrl-pipeline tbody');
    tbody.innerHTML = '';
    data.pipeline.forEach((row) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><span class="symbol-name">${escapeHtml(row.stage)}</span></td>
            <td>${escapeHtml(row.source)}</td>
            <td><span class="symbol-code">${escapeHtml(row.finrl_reference)}</span></td>
            <td>${pill(row.status, row.status === 'adapted' ? 'buy' : 'warn')}</td>
        `;
        tbody.appendChild(tr);
    });
}

async function createApproval(row) {
    const reason = `FinRL allocation target ${formatNumber(row.target_weight * 100, 1)}%; ${((row.reasons || []).slice(0, 3)).join(', ')}`;
    await postJson('/api/approvals', {
        symbol: row.symbol,
        name: row.name,
        action: row.rebalance_action,
        qty: Number(row.rebalance_qty || 0),
        price: Number(row.price || 0),
        reason,
        source: 'finrl-dashboard'
    });
    document.getElementById('finrl-status').textContent = `Queued ${row.rebalance_action.toUpperCase()} ${row.symbol}`;
}

async function renderAllocation() {
    const button = document.getElementById('btn-finrl-allocation');
    button.disabled = true;
    setTableMessage('#table-finrl-allocation tbody', 7, 'Generating FinRL-style allocation...');
    try {
        const data = await fetchJson('/api/ai-allocation', 45000);
        const tbody = document.querySelector('#table-finrl-allocation tbody');
        tbody.innerHTML = '';
        if (!data.positions.length) {
            setTableMessage('#table-finrl-allocation tbody', 7, 'No positions');
            return;
        }

        data.positions.forEach((row) => {
            const action = String(row.rebalance_action || 'hold').toLowerCase();
            const kind = action === 'buy' ? 'buy' : (action === 'sell' ? 'sell' : 'hold');
            const tr = document.createElement('tr');
            const queueCell = action === 'hold'
                ? ''
                : `<button type="button" class="button-ghost finrl-queue" data-symbol="${escapeHtml(row.symbol)}">Queue</button>`;
            tr.innerHTML = `
                <td>
                    <div class="symbol-name">${escapeHtml(row.name)}</div>
                    <div class="symbol-code">${escapeHtml(row.symbol)}</div>
                </td>
                <td>${pill(formatNumber(row.score, 2), Number(row.score || 0) > 0 ? 'buy' : 'hold')}</td>
                <td>${formatNumber(row.current_weight * 100, 1)}%</td>
                <td>${formatNumber(row.target_weight * 100, 1)}%</td>
                <td>${formatCurrency(row.delta_value)}</td>
                <td>${pill(action.toUpperCase(), kind)}</td>
                <td>${queueCell}</td>
            `;
            tbody.appendChild(tr);

            const queueButton = tr.querySelector('.finrl-queue');
            if (queueButton) {
                queueButton.addEventListener('click', async () => {
                    queueButton.disabled = true;
                    try {
                        await createApproval(row);
                    } catch (err) {
                        document.getElementById('finrl-status').textContent = `Queue failed: ${err.message}`;
                        queueButton.disabled = false;
                    }
                });
            }
        });
    } catch (err) {
        setTableMessage('#table-finrl-allocation tbody', 7, err.message);
    } finally {
        button.disabled = false;
    }
}

document.getElementById('btn-finrl-allocation').addEventListener('click', renderAllocation);
setTableMessage('#table-finrl-allocation tbody', 7, 'Click Generate to calculate target weights');
Promise.all([renderStatus(), renderPipeline()]).catch((err) => {
    document.getElementById('finrl-status').textContent = err.message;
});
