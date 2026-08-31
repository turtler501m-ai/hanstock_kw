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
        if (activeClick) flushAudit(activeClick);
        const name = buttonName(button);
        const payload = {
            phase: 'summary',
            audit_id: `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`,
            page: clean(document.title || window.location.pathname, 100),
            path: clean(window.location.pathname, 160),
            button_id: clean(button.id, 100),
            button_class: clean(button.className, 160),
            button_name: name,
            function: buttonFunction(button, name),
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
            request_count: 0,
            results: [],
            flush_timer: null
        };
        activeClick = payload;
        window.setTimeout(() => {
            if (activeClick?.audit_id === payload.audit_id) flushAudit(payload);
        }, 120000);
    }

    function scheduleFlush(context) {
        if (context.flush_timer) window.clearTimeout(context.flush_timer);
        context.flush_timer = window.setTimeout(() => flushAudit(context), 2500);
    }

    function flushAudit(context) {
        if (!context || context.request_count === 0 || context.results.length === 0) return;
        if (context.flush_timer) window.clearTimeout(context.flush_timer);
        const failed = context.results.find((item) => ['실패', '통신 실패'].includes(item.result));
        const accepted = context.results.find((item) => item.result === '접수');
        const finalItem = failed || accepted || context.results[context.results.length - 1];
        sendAudit({
            phase: 'summary',
            audit_id: context.audit_id,
            button_id: context.button_id,
            button_name: context.button_name,
            target: context.target,
            result: failed ? failed.result : (accepted ? '접수' : '성공'),
            api_count: context.results.length,
            detail: clean(finalItem.detail === '-' ? '정상 처리' : finalItem.detail, 80)
        });
        context.results = [];
        context.request_count = 0;
        if (activeClick?.audit_id === context.audit_id) activeClick = null;
    }

    function responseSummary(data) {
        if (!data || typeof data !== 'object') return '-';
        const candidates = [data.detail, data.error, data.message, data.summary, data.status];
        const scalar = candidates.find((value) => ['string', 'number', 'boolean'].includes(typeof value));
        return clean(scalar || (data.ok === true ? '정상 처리' : ''), 240) || '-';
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
                const status = String(data?.status || '').toLowerCase();
                const businessFailed = ['failed', 'error', 'blocked', 'rejected'].includes(status);
                const accepted = ['running', 'pending', 'started', 'accepted'].includes(status);
                context.results.push({
                    result: response.ok && !businessFailed ? (accepted ? '접수' : '성공') : '실패',
                    detail: responseSummary(data)
                });
                scheduleFlush(context);
            }
            return response;
        } catch (error) {
            if (isAuditableApi) {
                context.results.push({
                    result: '통신 실패',
                    detail: clean(error?.message || error, 240)
                });
                scheduleFlush(context);
            }
            throw error;
        }
    };

    document.addEventListener('click', (event) => {
        const button = event.target instanceof Element ? event.target.closest('button') : null;
        if (!button || button.disabled) return;
        if (button.matches('[role="tab"], .dashboard-tab, .dashboard-tab-sub, .pipeline-tab, .pb-tab, .narrative-tab, .env-tab-button, .close-modal, .sortable-header, .clickable-reason, .trade-sync-run-button')) return;
        auditClick(button);
    }, true);
}());
