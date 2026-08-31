(function () {
    'use strict';

    const nativeFetch = window.fetch.bind(window);
    let activeClick = null;

    function clean(value, limit) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
    }

    function buttonName(button) {
        return clean(
            button.dataset.auditName
            || button.getAttribute('aria-label')
            || button.getAttribute('title')
            || button.innerText
            || button.textContent
            || button.id
            || '이름 없는 버튼',
            100
        );
    }

    function buttonFunction(button, name) {
        const explicit = clean(button.dataset.auditFunction, 160);
        if (explicit) return explicit;
        if (button.matches('[role="tab"], .dashboard-tab, .dashboard-tab-sub, .pipeline-tab, .pb-tab, .narrative-tab, .env-tab-button')) {
            return `${name} 화면을 엽니다`;
        }
        const form = button.closest('form');
        if ((button.type || '').toLowerCase() === 'submit' && form) {
            return `${name} 입력 내용을 저장하거나 실행합니다`;
        }
        return `${name} 기능을 실행합니다`;
    }

    function sendAudit(payload) {
        const body = JSON.stringify(payload);
        return nativeFetch('/api/ui/button-click', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
            credentials: 'same-origin',
            keepalive: true
        }).catch(() => {
            if (navigator.sendBeacon) {
                navigator.sendBeacon('/api/ui/button-click', new Blob([body], { type: 'application/json' }));
            }
        });
    }

    function auditClick(button) {
        const name = buttonName(button);
        const payload = {
            phase: 'click',
            audit_id: `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`,
            page: clean(document.title || window.location.pathname, 100),
            path: clean(window.location.pathname, 160),
            button_id: clean(button.id, 100),
            button_class: clean(button.className, 160),
            button_name: name,
            function: buttonFunction(button, name),
            result: '클릭 접수',
            target: clean(
                button.dataset.symbol
                || button.dataset.id
                || button.dataset.ticker
                || button.dataset.strategyId
                || button.dataset.dashboardTab
                || button.dataset.tab
                || button.dataset.view,
                100
            ),
            request_count: 0
        };
        activeClick = payload;
        window.setTimeout(() => {
            if (activeClick?.audit_id === payload.audit_id) activeClick = null;
        }, 120000);
        sendAudit(payload);
    }

    function responseSummary(data) {
        if (!data || typeof data !== 'object') return '-';
        return clean(
            data.detail
            || data.error
            || data.message
            || data.summary
            || data.status
            || (data.ok === true ? '정상 처리' : ''),
            240
        ) || '-';
    }

    window.fetch = async function auditedFetch(input, init) {
        const url = typeof input === 'string' ? input : String(input?.url || '');
        const context = activeClick;
        const isAuditableApi = context && /(^|\/)api\//.test(url) && !url.includes('/api/ui/button-click');
        if (isAuditableApi) context.request_count += 1;
        try {
            const response = await nativeFetch(input, init);
            if (isAuditableApi) {
                let data = null;
                try {
                    data = await response.clone().json();
                } catch (_error) {
                    data = null;
                }
                const businessFailed = data && (
                    data.ok === false
                    || ['failed', 'error', 'blocked'].includes(String(data.status || '').toLowerCase())
                );
                sendAudit({
                    ...context,
                    phase: 'result',
                    api: clean(url, 200),
                    http_status: response.status,
                    result: response.ok && !businessFailed ? '성공' : '실패',
                    detail: responseSummary(data)
                });
            }
            return response;
        } catch (error) {
            if (isAuditableApi) {
                sendAudit({
                    ...context,
                    phase: 'result',
                    api: clean(url, 200),
                    http_status: 0,
                    result: '통신 실패',
                    detail: clean(error?.message || error, 240)
                });
            }
            throw error;
        }
    };

    document.addEventListener('click', (event) => {
        const button = event.target instanceof Element ? event.target.closest('button') : null;
        if (!button || button.disabled) return;
        auditClick(button);
    }, true);
}());
