(function () {
    const fileInput = document.getElementById('video-file');
    const pickButton = document.getElementById('btn-pick-video');
    const transcribeButton = document.getElementById('btn-transcribe');
    const clearButton = document.getElementById('btn-clear');
    const copyButton = document.getElementById('btn-copy');
    const startStreamButton = document.getElementById('btn-start-stream');
    const stopStreamButton = document.getElementById('btn-stop-stream');
    const startSystemButton = document.getElementById('btn-start-system');
    const stopSystemButton = document.getElementById('btn-stop-system');
    const dropzone = document.getElementById('voice-dropzone');
    const preview = document.getElementById('video-preview');
    const statusEl = document.getElementById('voice-status');
    const fileSummary = document.getElementById('file-summary');
    const transcriptEl = document.getElementById('transcript-live');
    const meter = document.querySelector('.voice-meter');
    const languageSelect = document.getElementById('voice-language');
    const copyBuffer = document.getElementById('copy-buffer');

    const CHUNK_MS = 15000;

    let selectedFile = null;
    let transcriptText = '';
    let liveSegments = [];
    let displayStream = null;
    let audioStream = null;
    let mediaRecorder = null;
    let isStreaming = false;
    let recordingStartedAt = 0;
    let chunkIndex = 0;
    let activeUploads = 0;
    let systemPollTimer = null;

    function setStatus(message, state) {
        statusEl.textContent = message;
        statusEl.classList.toggle('ok', state === 'ok');
        statusEl.classList.toggle('error', state === 'error');
    }

    function selectedLanguage() {
        return languageSelect ? languageSelect.value : 'auto';
    }

    function languageQuery() {
        return `language=${encodeURIComponent(selectedLanguage())}`;
    }

    function formatBytes(bytes) {
        if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let value = bytes;
        let unitIndex = 0;
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }
        return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
    }

    function formatTime(seconds) {
        const safeSeconds = Math.max(0, Number(seconds) || 0);
        const minutes = Math.floor(safeSeconds / 60);
        const rest = Math.floor(safeSeconds % 60);
        return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function renderSegments(segments) {
        liveSegments = segments || [];
        transcriptText = liveSegments.map((segment) => segment.text).join('\n');
        if (copyBuffer) {
            copyBuffer.value = transcriptText;
        }

        if (liveSegments.length === 0) {
            transcriptEl.innerHTML = '<p class="empty-message">아직 표시할 음성이 없습니다.</p>';
            return;
        }

        transcriptEl.innerHTML = liveSegments.map((segment) => `
            <div class="transcript-segment">
                <div class="segment-time">${formatTime(segment.start)}-${formatTime(segment.end)}</div>
                <div class="segment-text">${escapeHtml(segment.text)}</div>
                <button type="button" class="segment-copy-btn" data-text="${escapeHtml(segment.text)}">복사</button>
            </div>
        `).join('');
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
    }

    function appendSegments(segments, baseSeconds) {
        const normalized = (segments || []).map((segment) => ({
            start: Math.round((baseSeconds + Number(segment.start || 0)) * 100) / 100,
            end: Math.round((baseSeconds + Number(segment.end || 0)) * 100) / 100,
            text: segment.text,
        })).filter((segment) => segment.text);

        if (normalized.length === 0) return;
        renderSegments(liveSegments.concat(normalized));
    }

    function currentTranscriptText() {
        if (copyBuffer && copyBuffer.value.trim()) {
            return copyBuffer.value.trim();
        }

        if (transcriptText && transcriptText.trim()) {
            return transcriptText.trim();
        }

        return Array.from(transcriptEl.querySelectorAll('.transcript-segment'))
            .map((row) => {
                const time = row.querySelector('.segment-time')?.textContent?.trim() || '';
                const text = row.querySelector('.segment-text')?.textContent?.trim() || '';
                return time ? `[${time}] ${text}` : text;
            })
            .filter(Boolean)
            .join('\n');
    }

    async function copyTranscript() {
        const text = currentTranscriptText();
        if (!text) {
            setStatus('복사할 인식 결과가 없습니다.', 'error');
            return;
        }

        if (copyBuffer) {
            copyBuffer.value = text;
            copyBuffer.removeAttribute('readonly');
            copyBuffer.focus();
            copyBuffer.select();
            copyBuffer.setSelectionRange(0, copyBuffer.value.length);
        }

        let copied = false;
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
                copied = true;
            } else {
                throw new Error('Clipboard API unavailable');
            }
        } catch (error) {
            const textarea = copyBuffer || document.createElement('textarea');
            if (!copyBuffer) {
                textarea.value = text;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
            }
            textarea.focus();
            textarea.select();
            textarea.setSelectionRange(0, textarea.value.length);
            copied = document.execCommand('copy');
            if (!copyBuffer) {
                document.body.removeChild(textarea);
            }
        }

        if (copyBuffer) {
            copyBuffer.setAttribute('readonly', '');
        }

        if (copied) {
            setStatus('인식 결과를 클립보드에 복사했습니다.', 'ok');
            const prev = copyButton.textContent;
            copyButton.textContent = '복사됨';
            copyButton.classList.add('btn-copied');
            setTimeout(() => {
                copyButton.textContent = prev;
                copyButton.classList.remove('btn-copied');
            }, 1500);
        } else {
            setStatus('자동 복사가 막혔습니다. 아래 텍스트가 선택되어 있으니 Ctrl+C를 누르세요.', 'error');
        }
    }

    function setProcessing(isProcessing) {
        transcribeButton.disabled = isProcessing || !selectedFile;
        clearButton.disabled = isProcessing || !selectedFile;
        pickButton.disabled = isProcessing;
        meter.classList.toggle('processing', isProcessing || Boolean(mediaRecorder));
    }

    async function refreshSystemAudioStatus() {
        try {
            const response = await fetch('/api/voice/system-audio/status');
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'PC 소리 상태를 확인하지 못했습니다.');

            renderSegments(data.segments || []);
            startSystemButton.disabled = Boolean(data.running);
            stopSystemButton.disabled = !data.running;
            clearButton.disabled = false;
            meter.classList.toggle('processing', Boolean(data.running));

            if (data.error) {
                setStatus(data.error, 'error');
            } else if (data.running) {
                setStatus(`PC 전체 소리를 읽는 중입니다. 언어=${selectedLanguage()} / ${data.chunk_seconds}초 단위`, 'ok');
            } else if ((data.segments || []).length > 0) {
                setStatus(`PC 소리 캡처 중지됨: ${data.segments.length}개 구간`, 'ok');
            }
        } catch (error) {
            setStatus(error.message, 'error');
        }
    }

    async function startSystemAudio() {
        stopStreamCapture();
        renderSegments([]);
        setStatus('PC 스피커 출력 캡처를 시작합니다.', null);

        try {
            const response = await fetch(`/api/voice/system-audio/start?${languageQuery()}`, { method: 'POST' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'PC 소리 캡처를 시작하지 못했습니다.');

            startSystemButton.disabled = true;
            stopSystemButton.disabled = false;
            clearButton.disabled = false;
            meter.classList.add('processing');
            renderSegments(data.segments || []);
            setStatus(`PC 전체 소리를 캡처 중입니다. 온디스크 플레이어를 재생해두세요. 언어=${selectedLanguage()}`, 'ok');

            if (systemPollTimer) clearInterval(systemPollTimer);
            systemPollTimer = setInterval(refreshSystemAudioStatus, 3000);
        } catch (error) {
            setStatus(error.message, 'error');
        }
    }

    async function stopSystemAudio() {
        try {
            const response = await fetch('/api/voice/system-audio/stop', { method: 'POST' });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'PC 소리 캡처를 중지하지 못했습니다.');

            if (systemPollTimer) {
                clearInterval(systemPollTimer);
                systemPollTimer = null;
            }
            startSystemButton.disabled = false;
            stopSystemButton.disabled = true;
            meter.classList.remove('processing');
            renderSegments(data.segments || []);
            setStatus('PC 소리 캡처를 중지했습니다.', 'ok');
        } catch (error) {
            setStatus(error.message, 'error');
        }
    }

    function selectFile(file) {
        if (!file) return;
        selectedFile = file;
        renderSegments([]);
        fileSummary.textContent = `${file.name} / ${formatBytes(file.size)}`;
        transcribeButton.disabled = false;
        clearButton.disabled = false;
        setStatus('파일이 준비되었습니다.', 'ok');

        const objectUrl = URL.createObjectURL(file);
        preview.srcObject = null;
        preview.src = objectUrl;
        preview.controls = true;
        preview.onloadeddata = () => URL.revokeObjectURL(objectUrl);
    }

    async function transcribeFile() {
        if (!selectedFile) return;
        setProcessing(true);
        setStatus('선택 파일에서 음성을 추출하고 인식 중입니다.', null);

        const formData = new FormData();
        formData.append('file', selectedFile);

        try {
            const response = await fetch(`/api/voice/transcribe?${languageQuery()}`, { method: 'POST', body: formData });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || '음성 인식에 실패했습니다.');

            renderSegments(data.segments);
            setStatus(`완료: ${data.segments.length}개 구간 / ${data.elapsed_seconds}초`, 'ok');
        } catch (error) {
            setStatus(error.message, 'error');
        } finally {
            setProcessing(false);
        }
    }

    async function uploadStreamChunk(blob, index) {
        activeUploads += 1;
        setStatus(`스트림 전사 중입니다. 처리 대기: ${activeUploads}`, null);

        try {
            const response = await fetch(`/api/voice/transcribe-stream-chunk?${languageQuery()}`, {
                method: 'POST',
                headers: { 'Content-Type': blob.type || 'audio/webm' },
                body: blob,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || '스트림 음성 인식에 실패했습니다.');

            appendSegments(data.segments, index * (CHUNK_MS / 1000));
            setStatus(`실시간 수신 중: ${liveSegments.length}개 구간`, 'ok');
        } catch (error) {
            setStatus(error.message, 'error');
        } finally {
            activeUploads = Math.max(0, activeUploads - 1);
        }
    }

    function pickSupportedMimeType() {
        const candidates = [
            'audio/webm;codecs=opus',
            'video/webm;codecs=opus',
            'audio/webm',
            'video/webm',
        ];
        return candidates.find((type) => window.MediaRecorder && MediaRecorder.isTypeSupported(type)) || '';
    }

    async function startStreamCapture() {
        if (systemPollTimer) {
            await stopSystemAudio();
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
            setStatus('이 브라우저는 탭/창 오디오 캡처를 지원하지 않습니다.', 'error');
            return;
        }

        try {
            displayStream = await navigator.mediaDevices.getDisplayMedia({
                video: true,
                audio: {
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                },
            });

            const audioTracks = displayStream.getAudioTracks();
            if (audioTracks.length === 0) {
                stopStreamCapture();
                setStatus('공유 선택 창에서 오디오 공유를 켜야 합니다.', 'error');
                return;
            }

            audioStream = new MediaStream(audioTracks);
            preview.controls = false;
            preview.src = '';
            preview.srcObject = displayStream;

            renderSegments([]);
            chunkIndex = 0;
            recordingStartedAt = Date.now();
            isStreaming = true;
            const mimeType = pickSupportedMimeType();

            // stop/restart 패턴: 매 청크마다 새 MediaRecorder를 생성해
            // WebM 헤더(EBML 초기화 세그먼트)가 항상 포함된 완전한 파일을 만든다.
            function startChunk() {
                if (!isStreaming || !audioStream) return;
                const recorder = new MediaRecorder(audioStream, mimeType ? { mimeType } : undefined);
                const chunks = [];
                recorder.addEventListener('dataavailable', (e) => {
                    if (e.data && e.data.size > 0) chunks.push(e.data);
                });
                recorder.addEventListener('stop', () => {
                    const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
                    if (blob.size >= 512) {
                        const index = chunkIndex;
                        chunkIndex += 1;
                        uploadStreamChunk(blob, index);
                    }
                    if (isStreaming) {
                        startChunk();
                    } else {
                        mediaRecorder = null;
                        const seconds = Math.round((Date.now() - recordingStartedAt) / 1000);
                        setStatus(`스트림 캡처 중지됨: ${seconds}초`, 'ok');
                    }
                });
                recorder.start();
                mediaRecorder = recorder;
                setTimeout(() => {
                    if (recorder.state !== 'inactive') recorder.stop();
                }, CHUNK_MS);
            }

            displayStream.getTracks().forEach((track) => {
                track.addEventListener('ended', stopStreamCapture, { once: true });
            });

            startChunk();
            startStreamButton.disabled = true;
            stopStreamButton.disabled = false;
            clearButton.disabled = false;
            setProcessing(false);
            meter.classList.add('processing');
            setStatus('스트림 음성을 캡처 중입니다. 15초 단위로 화면에 표시됩니다.', 'ok');
        } catch (error) {
            stopStreamCapture();
            setStatus(error.message || '스트림 캡처를 시작하지 못했습니다.', 'error');
        }
    }

    function stopStreamCapture() {
        isStreaming = false;
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }
        mediaRecorder = null;

        [displayStream, audioStream].forEach((stream) => {
            if (stream) stream.getTracks().forEach((track) => track.stop());
        });
        displayStream = null;
        audioStream = null;
        preview.srcObject = null;

        startStreamButton.disabled = false;
        stopStreamButton.disabled = true;
        meter.classList.remove('processing');
    }

    function clearAll() {
        stopStreamCapture();
        stopSystemAudio();
        selectedFile = null;
        transcriptText = '';
        liveSegments = [];
        if (copyBuffer) {
            copyBuffer.value = '';
        }
        fileInput.value = '';
        preview.removeAttribute('src');
        preview.load();
        fileSummary.textContent = '스트림 캡처가 안 될 때만 영상 또는 음성 파일로 확인합니다.';
        renderSegments([]);
        setStatus('스트림 음성 시작을 누르세요.', null);
        transcribeButton.disabled = true;
        clearButton.disabled = true;
    }

    transcriptEl.addEventListener('click', async (e) => {
        const btn = e.target.closest('.segment-copy-btn');
        if (!btn) return;
        const text = btn.dataset.text || '';
        if (!text) return;

        let copied = false;
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
                copied = true;
            } else {
                throw new Error('unavailable');
            }
        } catch (_err) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            copied = document.execCommand('copy');
            document.body.removeChild(ta);
        }

        if (copied) {
            btn.textContent = '완료';
            btn.classList.add('copied');
            setTimeout(() => {
                btn.textContent = '복사';
                btn.classList.remove('copied');
            }, 1200);
        }
    });

    startStreamButton.addEventListener('click', startStreamCapture);
    stopStreamButton.addEventListener('click', stopStreamCapture);
    startSystemButton.addEventListener('click', startSystemAudio);
    stopSystemButton.addEventListener('click', stopSystemAudio);
    pickButton.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => selectFile(fileInput.files[0]));
    transcribeButton.addEventListener('click', transcribeFile);
    clearButton.addEventListener('click', clearAll);
    copyButton.addEventListener('click', copyTranscript);

    ['dragenter', 'dragover'].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.add('dragging');
        });
    });

    ['dragleave', 'drop'].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.remove('dragging');
        });
    });

    dropzone.addEventListener('drop', (event) => {
        const file = event.dataTransfer.files[0];
        if (file) selectFile(file);
    });
})();
