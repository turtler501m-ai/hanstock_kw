(function () {
    const BADGE_ID = "dashboard-runtime-badge";
    const ONLINE_BADGE_ID = "online-access-blocked-badge";

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function installStyles() {
        if (document.getElementById("dashboard-runtime-badge-style")) {
            return;
        }
        const style = document.createElement("style");
        style.id = "dashboard-runtime-badge-style";
        style.textContent = `
            #${BADGE_ID} {
                position: fixed;
                top: 10px;
                right: 10px;
                z-index: 9999;
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 8px 11px;
                border-radius: 8px;
                border: 1px solid rgba(251, 191, 36, 0.75);
                background: rgba(120, 53, 15, 0.94);
                color: #fffbeb;
                box-shadow: 0 14px 32px rgba(15, 23, 42, 0.28);
                font-family: "Noto Sans KR", "Inter", system-ui, sans-serif;
                font-size: 12px;
                line-height: 1.2;
                letter-spacing: 0;
            }
            #${BADGE_ID} strong {
                font-size: 12px;
                font-weight: 800;
            }
            #${BADGE_ID} span {
                color: #fde68a;
                font-size: 11px;
            }
            #${ONLINE_BADGE_ID} {
                position: fixed;
                top: 10px;
                left: 10px;
                z-index: 9999;
                display: inline-flex;
                align-items: center;
                gap: 8px;
                max-width: min(420px, calc(100vw - 20px));
                padding: 8px 11px;
                border-radius: 8px;
                border: 1px solid rgba(239, 68, 68, 0.72);
                background: rgba(69, 10, 10, 0.94);
                color: #fee2e2;
                box-shadow: 0 14px 32px rgba(15, 23, 42, 0.28);
                font-family: "Noto Sans KR", "Inter", system-ui, sans-serif;
                font-size: 12px;
                line-height: 1.2;
                letter-spacing: 0;
            }
            #${ONLINE_BADGE_ID} strong {
                font-size: 12px;
                font-weight: 800;
            }
            #${ONLINE_BADGE_ID} span {
                color: #fecaca;
                font-size: 11px;
            }
            @media (max-width: 760px) {
                #${BADGE_ID} {
                    top: auto;
                    right: 8px;
                    bottom: 8px;
                    max-width: calc(100vw - 16px);
                }
                #${ONLINE_BADGE_ID} {
                    top: auto;
                    left: 8px;
                    bottom: 48px;
                    max-width: calc(100vw - 16px);
                }
            }
        `;
        document.head.appendChild(style);
    }

    function renderOnlineBlockedBadge(blocked) {
        const existing = document.getElementById(ONLINE_BADGE_ID);
        if (existing) {
            existing.remove();
        }
        if (!blocked) {
            return;
        }
        installStyles();
        const badge = document.createElement("div");
        badge.id = ONLINE_BADGE_ID;
        badge.innerHTML = `
            <strong>온라인 접속 차단</strong>
            <span>외부 API, 웹소켓, 주문 실행 중지</span>
        `;
        document.body.appendChild(badge);
    }

    function renderBadge(runtime) {
        if (!runtime || (!runtime.is_vm && window.location.port !== "18000")) {
            return;
        }
        installStyles();
        const existing = document.getElementById(BADGE_ID);
        if (existing) {
            existing.remove();
        }
        const badge = document.createElement("div");
        badge.id = BADGE_ID;
        badge.innerHTML = `
            <strong>${escapeHtml(runtime.label || "VM DASHBOARD")}</strong>
            <span>${escapeHtml(runtime.hostname || "vm")}</span>
        `;
        document.body.appendChild(badge);
        document.title = `[VM] ${document.title.replace(/^\[VM\]\s*/, "")}`;
    }

    async function loadRuntime() {
        try {
            const response = await fetch("/api/health", { cache: "no-store" });
            if (!response.ok) {
                return;
            }
            const health = await response.json();
            renderBadge(health.dashboard_runtime);
            renderOnlineBlockedBadge(Boolean(health.online_access_blocked));
        } catch (error) {
            if (window.location.port === "18000") {
                renderBadge({ is_vm: true, label: "VM DASHBOARD", hostname: "tunnel" });
            }
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", loadRuntime);
    } else {
        loadRuntime();
    }
})();
