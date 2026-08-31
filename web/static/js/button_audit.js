(function () {
    'use strict';

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

    function auditClick(button) {
        const name = buttonName(button);
        const payload = {
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
            )
        };
        const body = JSON.stringify(payload);
        if (navigator.sendBeacon) {
            navigator.sendBeacon('/api/ui/button-click', new Blob([body], { type: 'application/json' }));
            return;
        }
        fetch('/api/ui/button-click', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
            credentials: 'same-origin',
            keepalive: true
        }).catch(() => {});
    }

    document.addEventListener('click', (event) => {
        const button = event.target instanceof Element ? event.target.closest('button') : null;
        if (!button || button.disabled) return;
        auditClick(button);
    }, true);
}());
