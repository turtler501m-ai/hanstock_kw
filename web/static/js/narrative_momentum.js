(function () {
    const state = {
        status: null,
        latest: null,
        history: [],
        themes: [],
        schedule: null,
        scheduleHistory: [],
        activeTab: 'summary',
        selectedResultIndex: 0,
        selectedResultDate: null,
    };

    function $(id) {
        return document.getElementById(id);
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    async function fetchJson(url, options) {
        const res = await fetch(url, options || {});
        const text = await res.text();
        let data = {};
        if (text) {
            try {
                data = JSON.parse(text);
            } catch (err) {
                throw new Error(text.slice(0, 300));
            }
        }
        if (!res.ok) {
            const detail = data.detail || data.message || res.statusText;
            throw new Error(detail);
        }
        return data;
    }

    function setActionStatus(kind, title, message, detail) {
        const box = $('narrative-action-status');
        if (!box) return;
        box.className = 'narrative-action-status ' + (kind || 'idle');
        box.innerHTML = '<strong>' + escapeHtml(title || '') + '</strong>'
            + '<span>' + escapeHtml(message || '') + '</span>'
            + (detail ? '<span class="narrative-action-detail">' + escapeHtml(detail) + '</span>' : '');
    }

    function setButtonBusy(id, busy, busyLabel) {
        const button = $(id);
        if (!button) return;
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = button.textContent;
        }
        button.disabled = !!busy;
        button.textContent = busy ? busyLabel : button.dataset.defaultLabel;
    }

    function describeTopSignals(signals) {
        const list = Array.isArray(signals) ? signals : [];
        if (!list.length) return '상위 후보 없음';
        return list.slice(0, 5).map(function (item) {
            const score = Number(item.final_score || item.score || 0);
            return (item.name || item.ticker || '-') + ' ' + score.toFixed(1) + '점';
        }).join(', ');
    }

    function describeCollection(collection) {
        if (!collection) return '기존 최신 내러티브를 사용했습니다.';
        if (collection.generated) {
            return '뉴스 기사 ' + (collection.article_count || 0) + '건에서 내러티브 '
                + (collection.narrative_count || 0) + '건을 자동 생성했습니다.';
        }
        return '자동 생성 없음: ' + ((collection.errors || []).join(', ') || '이미 최신 데이터입니다.');
    }

    function showActionError(title, err) {
        const message = err && err.message ? err.message : String(err || '알 수 없는 오류');
        setActionStatus('error', title, message);
    }

    function resultSummary(row) {
        const summary = row && row.summary ? row.summary : {};
        return {
            state: summary.state || (row && row.ok ? 'ok' : 'error'),
            candidate_count: Number(summary.candidate_count || 0),
            saved_count: Number(summary.saved_count || 0),
            top_signals: Array.isArray(summary.top_signals) ? summary.top_signals : [],
        };
    }

    function resultDate(row) {
        return String((row && row.recorded_at) || '').slice(0, 10) || '-';
    }

    function groupResultsByDate(rows) {
        const groups = [];
        const byDate = {};
        (Array.isArray(rows) ? rows : []).forEach(function (row) {
            const date = resultDate(row);
            if (!byDate[date]) {
                byDate[date] = {
                    date,
                    rows: [],
                    run_count: 0,
                    candidate_count: 0,
                    saved_count: 0,
                    latest: row,
                };
                groups.push(byDate[date]);
            }
            const group = byDate[date];
            const summary = resultSummary(row);
            group.rows.push(row);
            group.run_count += 1;
            group.candidate_count += summary.candidate_count;
            group.saved_count += summary.saved_count;
        });
        return groups;
    }

    function renderStatusCards() {
        const status = state.status || {};
        const items = [
            ['상태', status.state || '-'],
            ['최신 날짜', status.latest_date || '-'],
            ['시장 분위기', status.market_mood || '-'],
            ['후보 수', status.candidate_count || 0],
            ['내러티브 수', status.narrative_count || 0],
            ['테마 수', status.theme_count || 0],
            ['미매칭', status.unmatched_count || 0],
            ['승인 기준', status.approval_score_min || 75],
        ];
        $('narrative-status-grid').innerHTML = items.map(function (item) {
            return '<div class="narrative-stat"><span>' + escapeHtml(item[0]) + '</span><strong>' + escapeHtml(item[1]) + '</strong></div>';
        }).join('');
    }

    function renderReadiness() {
        const box = $('narrative-readiness');
        if (!box) return;
        const status = state.status || {};
        const stateName = status.state || '-';
        const themeCount = Number(status.theme_count || 0);
        const candidateCount = Number(status.candidate_count || 0);
        const historyPath = status.history_path || '.runtime/narrative_history.json';
        const latestDate = status.latest_date || '-';
        box.className = 'narrative-readiness ' + escapeHtml(stateName);
        if (stateName === 'missing') {
            box.innerHTML = '<strong>입력 내러티브 이력이 없습니다.</strong>'
                + '<span>현재 테마맵 ' + escapeHtml(themeCount) + '개는 정상 로드됐지만, '
                + escapeHtml(historyPath) + ' 파일이 없어 후보 값은 0개입니다. '
                + '내러티브 이력 탭에 오늘 날짜 JSON을 저장한 뒤 스캔 또는 즉시 실행을 누르세요.</span>';
            return;
        }
        if (stateName === 'stale') {
            box.innerHTML = '<strong>내러티브 날짜가 오래됐습니다.</strong>'
                + '<span>최신 입력 날짜가 ' + escapeHtml(latestDate) + '라서 오늘 기준 후보를 생성하지 않습니다. '
                + '오늘 날짜 JSON으로 갱신하세요.</span>';
            return;
        }
        if (stateName === 'fresh') {
            box.innerHTML = '<strong>내러티브 입력이 준비됐습니다.</strong>'
                + '<span>테마맵 ' + escapeHtml(themeCount) + '개 기준으로 후보 '
                + escapeHtml(candidateCount) + '개를 계산했습니다.</span>';
            return;
        }
        box.innerHTML = '<strong>상태 확인 필요</strong><span>현재 상태: ' + escapeHtml(stateName) + '</span>';
    }

    function renderSummary() {
        const status = state.status || {};
        const safety = status.safety || {};
        const badge = $('narrative-state-badge');
        badge.className = 'narrative-badge ' + escapeHtml(status.state || '');
        badge.textContent = status.state || '-';
        const rows = [
            ['전략 ID', status.strategy_id],
            ['오늘 기준일', status.today],
            ['최신 내러티브 날짜', status.latest_date],
            ['시장 분위기 점수', status.mood_score],
            ['후보 저장 파일', status.latest_result_path],
            ['히스토리 파일', status.history_path],
            ['테마 매핑 파일', status.theme_map_path],
            ['DRY_RUN', safety.dry_run],
            ['TRADING_ENV', safety.trading_env],
            ['REQUIRE_APPROVAL', safety.require_approval],
            ['ONLINE_ACCESS_BLOCKED', safety.online_access_blocked],
        ];
        $('narrative-summary-body').innerHTML = rows.map(function (row) {
            return '<tr><th>' + escapeHtml(row[0]) + '</th><td>' + escapeHtml(row[1] == null ? '-' : row[1]) + '</td></tr>';
        }).join('');
        const errors = status.errors || [];
        $('narrative-errors').textContent = errors.length ? errors.join('\n') : '오류 없음';
    }

    function tagList(values) {
        const list = Array.isArray(values) ? values : [];
        if (!list.length) return '<span class="narrative-muted">-</span>';
        return '<div class="narrative-tags">' + list.map(function (value) {
            return '<span class="narrative-tag">' + escapeHtml(value) + '</span>';
        }).join('') + '</div>';
    }

    function renderSignals() {
        const latest = state.latest || {};
        const signals = latest.signals || [];
        $('narrative-signal-count').textContent = signals.length + '개 후보';
        if (!signals.length) {
            $('narrative-signals-body').innerHTML = '<tr><td colspan="7" class="narrative-muted">표시할 시그널이 없습니다.</td></tr>';
            return;
        }
        const fresh = (latest.status || {}).state === 'fresh';
        $('narrative-signals-body').innerHTML = signals.map(function (signal, idx) {
            const score = Number(signal.final_score || signal.score || 0);
            const price = Number(signal.current_price || signal.price || 0);
            const disabled = !fresh || score < 75 || price <= 0 ? ' disabled' : '';
            const title = !fresh
                ? '최신 내러티브 데이터가 아닙니다'
                : (score < 75 ? '승인 기준 점수 미만입니다' : (price <= 0 ? '가격 조회 후 승인요청이 가능합니다' : '승인 대기열에 추가'));
            return '<tr>'
                + '<td>' + (idx + 1) + '</td>'
                + '<td><strong>' + escapeHtml(signal.name || signal.ticker) + '</strong><br><span class="narrative-muted">' + escapeHtml(signal.ticker) + '</span></td>'
                + '<td><span class="narrative-score">' + escapeHtml(score.toFixed(1)) + '</span></td>'
                + '<td>' + tagList(signal.themes) + '</td>'
                + '<td><div class="narrative-reasons">' + escapeHtml((signal.reasons || []).join('\n')) + '</div></td>'
                + '<td><div class="narrative-order-inputs">'
                + '<input class="narrative-price" type="number" min="1" step="1" inputmode="numeric" value="' + (price > 0 ? escapeHtml(price) : '') + '" placeholder="지정가">'
                + '<input class="narrative-qty" type="number" min="1" step="1" inputmode="numeric" value="1" placeholder="수량">'
                + '</div></td>'
                + '<td><button type="button" class="button-ghost compact-button narrative-approval" data-ticker="' + escapeHtml(signal.ticker) + '" data-name="' + escapeHtml(signal.name || signal.ticker) + '" data-score="' + escapeHtml(score) + '"' + disabled + ' title="' + escapeHtml(title) + '">승인요청</button></td>'
                + '</tr>';
        }).join('');
    }

    function renderHistory() {
        const rows = [];
        state.history.forEach(function (entry) {
            (entry.dominant_narratives || []).forEach(function (narrative) {
                rows.push({
                    date: entry.date,
                    theme: narrative.theme,
                    strength: narrative.strength,
                    sentiment: narrative.sentiment,
                    direction: narrative.direction,
                    affected_sectors: narrative.affected_sectors || [],
                    key_facts: narrative.key_facts || [],
                });
            });
        });
        $('narrative-history-count').textContent = rows.length + '개';
        if (!rows.length) {
            $('narrative-history-body').innerHTML = '<tr><td colspan="7" class="narrative-muted">내러티브 이력이 없습니다.</td></tr>';
            return;
        }
        $('narrative-history-body').innerHTML = rows.map(function (row) {
            return '<tr>'
                + '<td>' + escapeHtml(row.date) + '</td>'
                + '<td>' + escapeHtml(row.theme) + '</td>'
                + '<td>' + escapeHtml(row.strength) + '</td>'
                + '<td>' + escapeHtml(row.sentiment) + '</td>'
                + '<td>' + escapeHtml(row.direction) + '</td>'
                + '<td>' + tagList(row.affected_sectors) + '</td>'
                + '<td>' + escapeHtml(row.key_facts.join(', ')) + '</td>'
                + '</tr>';
        }).join('');
    }

    function renderThemes() {
        const themes = state.themes || [];
        if (!themes.length) {
            $('narrative-theme-body').innerHTML = '<tr><td colspan="3" class="narrative-muted">테마 매핑이 없습니다.</td></tr>';
            return;
        }
        $('narrative-theme-body').innerHTML = themes.map(function (theme) {
            const stocks = (theme.stocks || []).map(function (stock) {
                return (stock.name || stock.ticker) + ' ' + stock.ticker;
            });
            return '<tr>'
                + '<td><strong>' + escapeHtml(theme.theme) + '</strong></td>'
                + '<td>' + escapeHtml(theme.stock_count) + '</td>'
                + '<td>' + tagList(stocks) + '</td>'
                + '</tr>';
        }).join('');
    }

    function renderSchedule() {
        const schedule = state.schedule || {};
        const status = state.status || {};
        if ($('narrative-schedule-enabled')) {
            $('narrative-schedule-enabled').checked = !!schedule.enabled;
            $('narrative-schedule-interval').value = schedule.interval_minutes || 30;
            $('narrative-schedule-start').value = schedule.start_hm || '0900';
            $('narrative-schedule-end').value = schedule.end_hm || '1530';
            $('narrative-schedule-weekdays').value = schedule.weekdays || '1-5';
            $('narrative-schedule-mode').value = schedule.mode || 'execute';
        }
        const rows = state.scheduleHistory || [];
        const body = $('narrative-schedule-history-body');
        const summaryBox = $('narrative-schedule-summary');
        if (summaryBox) {
            if (status.state === 'missing') {
                summaryBox.textContent = '내러티브 이력 파일이 없어 스케줄은 실행됐지만 후보가 생성되지 않았습니다. 내러티브 이력 탭에서 오늘 날짜 narrative_history JSON을 저장한 뒤 다시 실행하세요.';
            } else if (!rows.length) {
                summaryBox.textContent = '최근 스케줄 실행 결과가 없습니다.';
            } else {
                const latest = rows[0];
                const summary = latest.summary || {};
                const top = (summary.top_signals || []).map(function (item) {
                    return (item.name || item.ticker || '-') + ' ' + (item.score == null ? '' : item.score);
                }).join(', ');
                summaryBox.textContent = '최근 실행 ' + (latest.recorded_at || '-')
                    + ' | 상태 ' + (summary.state || '-')
                    + ' | 후보 ' + (summary.candidate_count || 0) + '개'
                    + ' | 저장 ' + (summary.saved_count || 0) + '개'
                    + ' | 상위 ' + (top || '-');
            }
        }
        if (!body) return;
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="6" class="narrative-muted">스케줄 실행 이력이 없습니다.</td></tr>';
            return;
        }
        body.innerHTML = rows.map(function (row) {
            const summary = row.summary || {};
            const top = (summary.top_signals || []).map(function (item) {
                return (item.name || item.ticker || '-') + ' ' + (item.score == null ? '' : item.score);
            }).join(', ');
            return '<tr>'
                + '<td>' + escapeHtml(row.recorded_at || '-') + '</td>'
                + '<td>' + escapeHtml(row.mode || '-') + '</td>'
                + '<td>' + escapeHtml(summary.state || (row.ok ? 'ok' : 'error')) + '</td>'
                + '<td>' + escapeHtml(summary.candidate_count || 0) + '</td>'
                + '<td>' + escapeHtml(summary.saved_count || 0) + '</td>'
                + '<td>' + escapeHtml(top || '-') + '</td>'
                + '</tr>';
        }).join('');
    }

    function loadEditorFromState() {
        $('narrative-history-editor').value = JSON.stringify(state.history || [], null, 2);
        $('narrative-editor-status').textContent = '현재 이력을 편집기에 불러왔습니다.';
    }

    async function saveHistoryFromEditor() {
        let parsed;
        try {
            parsed = JSON.parse($('narrative-history-editor').value || '[]');
        } catch (err) {
            $('narrative-editor-status').textContent = 'JSON 파싱 실패: ' + err.message;
            return;
        }
        if (!Array.isArray(parsed)) {
            $('narrative-editor-status').textContent = '최상위 JSON은 배열이어야 합니다.';
            return;
        }
        try {
            const result = await fetchJson('/api/narrative-momentum/history', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({history: parsed}),
            });
            $('narrative-editor-status').textContent = result.count + '개 내러티브 날짜를 저장했습니다.';
            await loadAll();
        } catch (err) {
            $('narrative-editor-status').textContent = err.message || String(err);
        }
    }

    function renderAll() {
        renderReadiness();
        renderStatusCards();
        renderSummary();
        renderSignals();
        renderHistory();
        renderThemes();
        renderSchedule();
        renderResults();
    }

    async function loadAll() {
        try {
            const results = await Promise.all([
                fetchJson('/api/narrative-momentum/status'),
                fetchJson('/api/narrative-momentum/latest'),
                fetchJson('/api/narrative-momentum/history'),
                fetchJson('/api/narrative-momentum/theme-map'),
                fetchJson('/api/narrative-momentum/schedule'),
                fetchJson('/api/narrative-momentum/schedule-history?limit=100'),
            ]);
            state.status = results[0];
            state.latest = results[1];
            state.history = results[2].history || [];
            state.themes = results[3].themes || [];
            state.schedule = (results[4] || {}).schedule || {};
            state.scheduleHistory = (results[5] || {}).history || [];
            const groups = groupResultsByDate(state.scheduleHistory);
            if (!groups.some(function (group) { return group.date === state.selectedResultDate; })) {
                state.selectedResultDate = groups.length ? groups[0].date : null;
            }
            renderAll();
        } catch (err) {
            $('narrative-errors').textContent = err.message || String(err);
        }
    }

    function renderResults() {
        const rows = state.scheduleHistory || [];
        const groups = groupResultsByDate(rows);
        const count = $('narrative-result-count');
        const body = $('narrative-results-body');
        if (count) count.textContent = groups.length + '일 / ' + rows.length + '회';
        if (!body) return;
        if (!groups.length) {
            body.innerHTML = '<tr><td colspan="5" class="narrative-muted">아직 실행 결과가 없습니다. 상단의 자동 실행 버튼을 누르세요.</td></tr>';
            renderResultDetail(null);
            return;
        }
        body.innerHTML = groups.map(function (group) {
            const summary = resultSummary(group.latest);
            return '<tr class="narrative-result-row ' + (group.date === state.selectedResultDate ? 'active' : '') + '" data-result-date="' + escapeHtml(group.date) + '">'
                + '<td><strong>' + escapeHtml(group.date) + '</strong></td>'
                + '<td>' + escapeHtml(group.run_count) + '회</td>'
                + '<td>' + escapeHtml(summary.state || '-') + '</td>'
                + '<td>' + escapeHtml(group.candidate_count) + '</td>'
                + '<td>' + escapeHtml(group.saved_count) + '</td>'
                + '</tr>';
        }).join('');
        renderResultDetail(groups.find(function (group) { return group.date === state.selectedResultDate; }) || groups[0]);
    }

    function renderResultDetail(group) {
        const box = $('narrative-result-detail');
        if (!box) return;
        if (!group) {
            box.innerHTML = '날짜를 선택하면 세부목록이 표시됩니다.';
            return;
        }
        const row = group.latest;
        const summary = resultSummary(row);
        const detail = row.detail || {};
        const collection = detail.collection || null;
        const signals = Array.isArray(detail.signals) ? detail.signals : [];
        const errors = Array.isArray(row.errors) ? row.errors : [];
        const collectionText = collection
            ? describeCollection(collection)
            : '기존 최신 내러티브를 사용했습니다.';
        const runItems = group.rows.map(function (item) {
            const itemSummary = resultSummary(item);
            return '<div class="narrative-detail-signal">'
                + '<strong>' + escapeHtml(item.recorded_at || '-') + '</strong>'
                + '<p>모드: ' + escapeHtml(item.mode || '-') + ' / 상태: ' + escapeHtml(itemSummary.state || '-')
                + ' / 후보: ' + escapeHtml(itemSummary.candidate_count) + '개 / 저장: ' + escapeHtml(itemSummary.saved_count) + '개</p>'
                + '</div>';
        }).join('');
        const signalItems = signals.slice(0, 12).map(function (signal, idx) {
            const score = Number(signal.final_score || signal.score || 0);
            const themes = Array.isArray(signal.themes) ? signal.themes.join(', ') : '-';
            const reasons = Array.isArray(signal.reasons) ? signal.reasons.join(' / ') : '';
            return '<div class="narrative-detail-signal">'
                + '<strong>' + (idx + 1) + '. ' + escapeHtml(signal.name || signal.ticker || '-') + ' '
                + '<span class="narrative-muted">' + escapeHtml(signal.ticker || '') + '</span>'
                + ' · ' + escapeHtml(score.toFixed(1)) + '점</strong>'
                + '<p>테마: ' + escapeHtml(themes || '-') + '</p>'
                + '<p>' + escapeHtml(reasons || '근거 없음') + '</p>'
                + '</div>';
        }).join('');
        box.innerHTML = '<h3>' + escapeHtml(group.date) + ' 세부목록</h3>'
            + '<div class="narrative-detail-grid">'
            + '<div class="narrative-detail-item"><span>상태</span><strong>' + escapeHtml(summary.state || '-') + '</strong></div>'
            + '<div class="narrative-detail-item"><span>실행</span><strong>' + escapeHtml(group.run_count) + '회</strong></div>'
            + '<div class="narrative-detail-item"><span>후보 합계</span><strong>' + escapeHtml(group.candidate_count) + '개</strong></div>'
            + '<div class="narrative-detail-item"><span>저장 합계</span><strong>' + escapeHtml(group.saved_count) + '개</strong></div>'
            + '</div>'
            + '<h4>자동 생성</h4>'
            + '<p class="narrative-muted">' + escapeHtml(collectionText) + '</p>'
            + (errors.length ? '<h4>오류</h4><p class="narrative-muted">' + escapeHtml(errors.join('\n')) + '</p>' : '')
            + '<h4>실행 목록</h4>'
            + '<div class="narrative-detail-list">' + (runItems || '<span class="narrative-muted">실행 목록이 없습니다.</span>') + '</div>'
            + '<h4>최신 실행 후보 세부정보</h4>'
            + '<div class="narrative-detail-list">' + (signalItems || '<span class="narrative-muted">저장된 세부 후보가 없습니다.</span>') + '</div>';
    }

    async function runOneClick() {
        setButtonBusy('btn-narrative-oneclick', true, '자동 실행 중...');
        setActionStatus('running', '자동 실행 중', '뉴스 자동생성, 스캔, 후보저장을 한 번에 실행합니다.');
        try {
            const result = await fetchJson('/api/narrative-momentum/run-scheduled', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({save_candidates: true, auto_collect: true}),
            });
            const message = '후보 ' + (result.total_scanned || 0) + '개, 저장 ' + (result.saved_count || 0) + '개';
            setActionStatus('success', '자동 실행 완료', message, describeCollection(result.collection) + ' 상위 후보: ' + describeTopSignals(result.signals));
            state.selectedResultDate = null;
            await loadAll();
            activateTab('results');
        } catch (err) {
            showActionError('자동 실행 실패', err);
        } finally {
            setButtonBusy('btn-narrative-oneclick', false);
        }
    }

    async function runScan(saveCandidates) {
        const buttonId = saveCandidates ? 'btn-narrative-save-scan' : 'btn-narrative-scan';
        const title = saveCandidates ? '스캔과 후보저장 실행 중' : '스캔 실행 중';
        setButtonBusy(buttonId, true, '실행 중...');
        setActionStatus('running', title, '뉴스 자동생성 필요 여부를 확인한 뒤 후보를 계산합니다.');
        try {
            const result = await fetchJson('/api/narrative-momentum/scan', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({save_candidates: !!saveCandidates, auto_collect: true}),
            });
            state.latest = {
                status: result.status,
                signals: result.signals,
                unmatched: [],
            };
            setActionStatus(
                'success',
                saveCandidates ? '후보저장 완료' : '스캔 완료',
                '후보 ' + (result.total_scanned || 0) + '개를 계산했고, 저장 ' + (result.saved_count || 0) + '개를 처리했습니다.',
                describeCollection(result.collection) + ' 상위 후보: ' + describeTopSignals(result.signals)
            );
            await loadAll();
            activateTab('signals');
        } catch (err) {
            showActionError(saveCandidates ? '후보저장 실패' : '스캔 실패', err);
        } finally {
            setButtonBusy(buttonId, false);
        }
    }

    async function collectNarratives() {
        const statusEl = $('narrative-editor-status');
        setButtonBusy('btn-narrative-collect', true, '생성 중...');
        setActionStatus('running', '뉴스 자동생성 실행 중', '뉴스 RSS를 읽고 오늘 날짜 내러티브 JSON을 생성합니다.');
        if (statusEl) statusEl.textContent = '뉴스에서 내러티브 JSON을 자동 생성하는 중입니다.';
        try {
            const result = await fetchJson('/api/narrative-momentum/collect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({}),
            });
            const message = '기사 ' + (result.article_count || 0) + '건, 내러티브 '
                + (result.narrative_count || 0) + '건을 생성했습니다.';
            setActionStatus('success', '뉴스 자동생성 완료', message, '저장 위치: ' + (result.history_path || '.runtime/narrative_history.json'));
            if (statusEl) statusEl.textContent = '자동 생성 완료: ' + message;
            await loadAll();
            activateTab('history');
        } catch (err) {
            if (statusEl) statusEl.textContent = '자동 생성 실패: ' + (err.message || String(err));
            showActionError('뉴스 자동생성 실패', err);
        } finally {
            setButtonBusy('btn-narrative-collect', false);
        }
    }

    async function saveSchedule() {
        const payload = {
            enabled: $('narrative-schedule-enabled').checked,
            interval_minutes: Number($('narrative-schedule-interval').value || 30),
            start_hm: $('narrative-schedule-start').value || '0900',
            end_hm: $('narrative-schedule-end').value || '1530',
            weekdays: $('narrative-schedule-weekdays').value || '1-5',
            mode: $('narrative-schedule-mode').value || 'execute',
            auto_approve: false,
        };
        const result = await fetchJson('/api/narrative-momentum/schedule', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        state.schedule = result.schedule || {};
        $('narrative-schedule-status').textContent = '스케줄을 저장했습니다.';
        renderSchedule();
    }

    async function runScheduledNow() {
        const saveCandidates = ($('narrative-schedule-mode')?.value || 'execute') !== 'analysis_only';
        setButtonBusy('btn-narrative-run-scheduled', true, '실행 중...');
        setActionStatus('running', '스케줄 즉시 실행 중', '자동 생성, 스캔, 저장을 스케줄러와 같은 방식으로 실행합니다.');
        $('narrative-schedule-status').textContent = '실행 중...';
        try {
            const result = await fetchJson('/api/narrative-momentum/run-scheduled', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({save_candidates: saveCandidates, auto_collect: true}),
            });
            const message = '후보 ' + (result.total_scanned || 0) + '개, 저장 ' + (result.saved_count || 0) + '개';
            $('narrative-schedule-status').textContent = '완료: ' + message;
            setActionStatus('success', '스케줄 즉시 실행 완료', message, describeCollection(result.collection) + ' 상위 후보: ' + describeTopSignals(result.signals));
            await loadAll();
        } catch (err) {
            $('narrative-schedule-status').textContent = '실패: ' + (err.message || String(err));
            showActionError('스케줄 즉시 실행 실패', err);
        } finally {
            setButtonBusy('btn-narrative-run-scheduled', false);
        }
    }

    async function queueApproval(button) {
        const row = button.closest('tr');
        const ticker = button.dataset.ticker;
        const name = button.dataset.name;
        const score = Number(button.dataset.score || 0);
        const price = Number(row.querySelector('.narrative-price')?.value || 0);
        const qty = Number(row.querySelector('.narrative-qty')?.value || 0);
        if (price <= 0 || qty <= 0) {
            alert('지정가와 수량을 먼저 입력하세요.');
            return;
        }
        const reason = '내러티브 모멘텀 ' + score.toFixed(1) + '점: ' + name + ' ' + ticker;
        button.disabled = true;
        try {
            const result = await fetchJson('/api/narrative-momentum/queue-approval', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ticker, name, score, qty, price, reason}),
            });
            button.textContent = '대기 #' + result.id;
        } catch (err) {
            button.disabled = false;
            alert(err.message || String(err));
        }
    }

    function activateTab(tabName) {
        state.activeTab = tabName;
        document.querySelectorAll('.narrative-tab').forEach(function (btn) {
            const active = btn.dataset.tab === tabName;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        document.querySelectorAll('.narrative-panel').forEach(function (panel) {
            const active = panel.id === 'narrative-tab-' + tabName;
            panel.classList.toggle('active', active);
            panel.hidden = !active;
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.narrative-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                activateTab(btn.dataset.tab);
            });
        });
        $('btn-narrative-refresh').addEventListener('click', loadAll);
        $('btn-narrative-oneclick').addEventListener('click', runOneClick);
        $('btn-narrative-collect').addEventListener('click', collectNarratives);
        $('btn-narrative-scan').addEventListener('click', function () { runScan(false); });
        $('btn-narrative-save-scan').addEventListener('click', function () { runScan(true); });
        $('btn-narrative-save-schedule').addEventListener('click', function () { saveSchedule().catch(alert); });
        $('btn-narrative-run-scheduled').addEventListener('click', runScheduledNow);
        $('btn-narrative-theme-reload').addEventListener('click', function () {
            fetchJson('/api/narrative-momentum/theme-map/reload', {method: 'POST'}).then(function (data) {
                state.themes = data.themes || [];
                renderThemes();
            }).catch(alert);
        });
        $('btn-narrative-load-editor').addEventListener('click', loadEditorFromState);
        $('btn-narrative-save-history').addEventListener('click', saveHistoryFromEditor);
        document.body.addEventListener('click', function (event) {
            const resultRow = event.target.closest('.narrative-result-row');
            if (resultRow) {
                state.selectedResultDate = resultRow.dataset.resultDate || null;
                renderResults();
                return;
            }
            const button = event.target.closest('.narrative-approval');
            if (button) queueApproval(button);
        });
        document.body.addEventListener('input', function (event) {
            if (!event.target.classList.contains('narrative-price') && !event.target.classList.contains('narrative-qty')) return;
            const row = event.target.closest('tr');
            const button = row && row.querySelector('.narrative-approval');
            if (!button) return;
            const latest = state.latest || {};
            const fresh = (latest.status || {}).state === 'fresh';
            const score = Number(button.dataset.score || 0);
            const price = Number(row.querySelector('.narrative-price')?.value || 0);
            const qty = Number(row.querySelector('.narrative-qty')?.value || 0);
            button.disabled = !(fresh && score >= 75 && price > 0 && qty > 0);
        });
        loadAll();
    });
})();
