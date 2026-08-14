const formatNumber = (value) => Number(value || 0).toLocaleString();

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

async function fetchJson(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.detail || `Request failed: ${response.status}`);
    }
    return data;
}

function boundaryFor(vendor) {
    if (vendor.license === 'GPL-3.0') {
        return 'Isolated vendor source; no direct code merge';
    }
    return 'Vendored source available for adapters';
}

function nextStepFor(slug) {
    const steps = {
        finrl: 'Trainable DRL model runner after data normalization',
        qlib: 'Factor dataset cache and model score endpoint',
        pyportfolioopt: 'Efficient frontier optimizer for target weights',
        freqtrade: 'Strategy lifecycle and dry-run UX concepts only',
    };
    return steps[slug] || 'Adapter design';
}

async function renderVendors() {
    const data = await fetchJson('/api/vendors');
    document.getElementById('vendor-status').textContent =
        `${data.vendors.length} AI trading repositories loaded under vendor/`;

    const totalFiles = data.vendors.reduce((sum, vendor) => sum + vendor.file_count, 0);
    const totalPython = data.vendors.reduce((sum, vendor) => sum + vendor.python_file_count, 0);
    const gplCount = data.vendors.filter((vendor) => vendor.license === 'GPL-3.0').length;
    document.getElementById('vendor-summary').innerHTML = `
        <div class="card glass">
            <h3>Repositories</h3>
            <div class="value">${data.vendors.length}</div>
        </div>
        <div class="card glass">
            <h3>Total Files</h3>
            <div class="value">${formatNumber(totalFiles)}</div>
        </div>
        <div class="card glass">
            <h3>Python Files</h3>
            <div class="value">${formatNumber(totalPython)}</div>
        </div>
        <div class="card glass">
            <h3>Copyleft</h3>
            <div class="value">${gplCount}</div>
        </div>
    `;

    const tbody = document.querySelector('#table-vendors tbody');
    tbody.innerHTML = '';
    data.vendors.forEach((vendor) => {
        const tr = document.createElement('tr');
        const licenseKind = vendor.license === 'GPL-3.0' ? 'warn' : 'buy';
        tr.innerHTML = `
            <td>
                <div class="symbol-name">${escapeHtml(vendor.name)}</div>
                <div class="symbol-code">${escapeHtml(vendor.path)}</div>
            </td>
            <td>${pill(vendor.license, licenseKind)}</td>
            <td>${formatNumber(vendor.file_count)}</td>
            <td>${formatNumber(vendor.python_file_count)}</td>
            <td>${formatNumber(vendor.notebook_count)}</td>
            <td>${escapeHtml(vendor.adapter)}</td>
        `;
        tbody.appendChild(tr);
    });

    const mapBody = document.querySelector('#table-vendor-map tbody');
    mapBody.innerHTML = '';
    data.vendors.forEach((vendor) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><span class="symbol-name">${escapeHtml(vendor.name)}</span></td>
            <td>${escapeHtml(vendor.adapter)}</td>
            <td>${escapeHtml(boundaryFor(vendor))}</td>
            <td>${escapeHtml(nextStepFor(vendor.slug))}</td>
        `;
        mapBody.appendChild(tr);
    });
}

renderVendors().catch((err) => {
    document.getElementById('vendor-status').textContent = err.message;
});
