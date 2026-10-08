/**
 * End-Link Forensics Dashboard - Client Application Logic
 */

document.addEventListener('DOMContentLoaded', () => {
    // App State
    const state = {
        activeJobId: null,
        eventSource: null,
        timerInterval: null,
        startTime: null,
        autoScroll: true,
        hops: [],
        currentCrawlId: null,
        historyList: [],
    };

    // DOM Elements - Navigation
    const navTabs = document.querySelectorAll('.nav-tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');

    // DOM Elements - Launch & Stream
    const crawlForm = document.getElementById('crawlForm');
    const targetUrlInput = document.getElementById('targetUrlInput');
    const btnStartCrawl = document.getElementById('btnStartCrawl');
    const btnCancelCrawl = document.getElementById('btnCancelCrawl');
    const btnSpinner = document.getElementById('btnSpinner');
    const btnToggleSettings = document.getElementById('btnToggleSettings');
    const settingsDrawer = document.getElementById('settingsDrawer');
    const headlessToggle = document.getElementById('headlessToggle');
    const challengeTimeoutInput = document.getElementById('challengeTimeoutInput');
    const maxPagesInput = document.getElementById('maxPagesInput');
    const brandSelect = document.getElementById('brandSelect');

    // DOM Elements - Metrics & Monitor
    const liveStatusText = document.getElementById('liveStatusText');
    const livePageTypeText = document.getElementById('livePageTypeText');
    const liveBrandText = document.getElementById('liveBrandText');
    const liveTimerText = document.getElementById('liveTimerText');
    const hopFlowContainer = document.getElementById('hopFlowContainer');
    const hopCountBadge = document.getElementById('hopCountBadge');
    const terminalLog = document.getElementById('terminalLog');
    const btnToggleScroll = document.getElementById('btnToggleScroll');
    const btnClearLog = document.getElementById('btnClearLog');

    // Milestones
    const milestones = {
        doorway: document.getElementById('msDoorway'),
        cloudflare: document.getElementById('msCloudflare'),
        classify: document.getElementById('msClassify'),
        action: document.getElementById('msAction'),
        moneySite: document.getElementById('msMoneySite'),
        forensics: document.getElementById('msForensics'),
    };

    // Header Status
    const aiStatusBadge = document.getElementById('aiStatusBadge');
    const brandsCountText = document.getElementById('brandsCountText');
    const crawlsCountText = document.getElementById('crawlsCountText');

    // History Elements
    const historyTableBody = document.getElementById('historyTableBody');
    const historySearchInput = document.getElementById('historySearchInput');
    const historyFilterClassification = document.getElementById('historyFilterClassification');
    const btnRefreshHistory = document.getElementById('btnRefreshHistory');

    // Tab Navigation Logic
    navTabs.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.getAttribute('data-tab');
            switchTab(tabId);
        });
    });

    function switchTab(tabId) {
        navTabs.forEach(b => b.classList.toggle('active', b.getAttribute('data-tab') === tabId));
        tabPanes.forEach(p => p.classList.toggle('active', p.id === tabId));
    }

    // Toggle Settings Drawer
    btnToggleSettings.addEventListener('click', () => {
        settingsDrawer.classList.toggle('open');
    });

    // Auto-scroll toggle
    btnToggleScroll.addEventListener('click', () => {
        state.autoScroll = !state.autoScroll;
        btnToggleScroll.textContent = `Auto-Scroll: ${state.autoScroll ? 'ON' : 'OFF'}`;
    });

    // Clear log
    btnClearLog.addEventListener('click', () => {
        terminalLog.innerHTML = '<div class="term-line term-dim">Console log cleared.</div>';
    });

    // Initial Data Fetch
    fetchStatus();
    fetchBrands();
    fetchHistory();

    // 1. Fetch Environment Status
    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            
            const dot = aiStatusBadge.querySelector('.status-dot');
            const text = aiStatusBadge.querySelector('.status-text');
            if (data.ai_verifier_online) {
                dot.className = 'status-dot online';
                text.textContent = 'AI Verifier: Online (8000)';
            } else {
                dot.className = 'status-dot offline';
                text.textContent = 'AI Verifier: Offline';
            }

            brandsCountText.textContent = `Brands: ${data.brands_count || 0}`;
            crawlsCountText.textContent = `Saved Crawls: ${data.total_crawls || 0}`;
        } catch (e) {
            console.error('Failed to fetch status', e);
        }
    }

    // 2. Fetch Brand List
    async function fetchBrands() {
        try {
            const res = await fetch('/api/brands');
            const brands = await res.json();
            brandSelect.innerHTML = '<option value="">Auto-Detect from Page Content</option>';
            brands.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b;
                opt.textContent = b;
                brandSelect.appendChild(opt);
            });
        } catch (e) {
            console.error('Failed to fetch brands', e);
        }
    }

    // 3. Crawl Form Submit (Start Crawl)
    crawlForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = targetUrlInput.value.trim();
        if (!url) return;

        // Reset UI State
        resetCrawlMonitor(url);
        setRunningState(true);

        const payload = {
            url: url,
            headless: headlessToggle.checked,
            challenge_timeout: parseInt(challengeTimeoutInput.value, 10) || 15,
            max_pages: parseInt(maxPagesInput.value, 10) || 0,
            expected_brand: brandSelect.value || null,
        };

        try {
            const res = await fetch('/api/crawl/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();

            if (!res.ok) {
                appendLogLine(`[Error] Failed to start crawl: ${data.error || 'Unknown error'}`, 'term-red');
                setRunningState(false);
                return;
            }

            state.activeJobId = data.job_id;
            connectEventStream(data.job_id);
        } catch (err) {
            appendLogLine(`[Error] Network error starting crawl: ${err.message}`, 'term-red');
            setRunningState(false);
        }
    });

    // 4. Cancel Crawl
    btnCancelCrawl.addEventListener('click', async () => {
        if (!state.activeJobId) return;
        try {
            btnCancelCrawl.disabled = true;
            await fetch(`/api/crawl/cancel/${state.activeJobId}`, { method: 'POST' });
            appendLogLine('[System] Cancellation signal transmitted...', 'term-yellow');
        } catch (err) {
            console.error('Cancel failed', err);
        }
    });

    // Reset Monitor
    function resetCrawlMonitor(initialUrl) {
        terminalLog.innerHTML = '';
        state.hops = [{ type: 'START', url: initialUrl }];
        renderHopChain();

        liveStatusText.textContent = 'RUNNING';
        liveStatusText.className = 'metric-value status-running';
        livePageTypeText.textContent = 'CONNECTING...';
        liveBrandText.textContent = brandSelect.value || 'DETECTING...';

        state.startTime = Date.now();
        if (state.timerInterval) clearInterval(state.timerInterval);
        state.timerInterval = setInterval(updateTimer, 1000);
        updateTimer();

        // Reset Milestones
        Object.values(milestones).forEach(m => m.className = 'milestone-item');
        setMilestone('doorway', 'active');
    }

    function updateTimer() {
        if (!state.startTime) return;
        const diff = Math.floor((Date.now() - state.startTime) / 1000);
        const mins = String(Math.floor(diff / 60)).padStart(2, '0');
        const secs = String(diff % 60).padStart(2, '0');
        liveTimerText.textContent = `${mins}:${secs}`;
    }

    function setRunningState(running) {
        btnStartCrawl.disabled = running;
        btnSpinner.style.display = running ? 'inline-block' : 'none';
        btnStartCrawl.querySelector('.btn-text').textContent = running ? 'Crawling...' : 'Start Crawl';
        btnCancelCrawl.classList.toggle('hidden', !running);
        btnCancelCrawl.disabled = false;
        targetUrlInput.disabled = running;
    }

    function setMilestone(key, status) {
        if (milestones[key]) {
            milestones[key].className = `milestone-item ${status}`;
        }
    }

    // 5. Connect Server-Sent Events (SSE) Stream
    function connectEventStream(jobId) {
        if (state.eventSource) {
            state.eventSource.close();
        }

        const source = new EventSource(`/api/crawl/stream/${jobId}`);
        state.eventSource = source;

        source.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleStreamEvent(data);
            } catch (e) {
                console.error('Error parsing SSE event', e);
            }
        };

        source.onerror = () => {
            // Stream disconnected or completed
            source.close();
            state.eventSource = null;
        };
    }

    // Handle incoming line from stream
    function handleStreamEvent(data) {
        if (data.type === 'connected') {
            appendLogLine(`[Stream Connected] Attached to job session #${data.job_id}`, 'term-dim');
            return;
        }

        if (data.type === 'log') {
            const line = data.message;
            processLogLine(line);
            return;
        }

        if (data.type === 'info') {
            appendLogLine(data.message, 'term-dim');
            return;
        }

        if (data.type === 'error') {
            appendLogLine(`[Error] ${data.message}`, 'term-red');
            setRunningState(false);
            liveStatusText.textContent = 'FAILED';
            liveStatusText.className = 'metric-value status-danger';
            if (state.timerInterval) clearInterval(state.timerInterval);
            return;
        }

        if (data.type === 'finished') {
            setRunningState(false);
            if (state.timerInterval) clearInterval(state.timerInterval);

            const isSuccess = data.return_code === 0;
            liveStatusText.textContent = isSuccess ? 'COMPLETED' : 'STOPPED';
            liveStatusText.className = `metric-value ${isSuccess ? 'status-success' : 'status-danger'}`;

            appendLogLine(`\n[System] Crawl finished with code ${data.return_code}.`, isSuccess ? 'term-green' : 'term-yellow');

            fetchHistory(); // Refresh history table
            fetchStatus();

            if (data.crawl_id) {
                state.currentCrawlId = data.crawl_id;
                appendLogLine(`[Report] Generated crawl output: ${data.crawl_id}`, 'term-cyan');
                // Auto switch to Forensics tab and load report
                setTimeout(() => {
                    loadForensicReport(data.crawl_id);
                    switchTab('tabForensics');
                }, 800);
            }
        }
    }

    // Intelligent Log Parsing for Milestones & Hop Visualization
    function processLogLine(line) {
        let lineClass = '';

        // Strip ANSI escape codes
        const cleanLine = line.replace(/\x1b\[[0-9;]*m/g, '');

        if (cleanLine.includes('[Cloudflare]')) {
            lineClass = 'term-yellow';
            setMilestone('doorway', 'completed');
            setMilestone('cloudflare', 'active');
            if (cleanLine.includes('Turnstile token received') || cleanLine.includes('cleared')) {
                setMilestone('cloudflare', 'completed');
            }
        } else if (cleanLine.includes('[PageType]')) {
            lineClass = 'term-cyan';
            setMilestone('classify', 'completed');
            const ptMatch = cleanLine.match(/\[PageType\]\s+([A-Z_]+):/i);
            const detectedType = ptMatch ? ptMatch[1].toUpperCase() : null;

            if (detectedType === 'MONEY_SITE') {
                livePageTypeText.textContent = 'MONEY_SITE';
                setMilestone('action', 'completed');
                setMilestone('moneySite', 'completed');
                setMilestone('forensics', 'active');
            } else if (detectedType === 'LANDING_PAGE') {
                livePageTypeText.textContent = 'LANDING_PAGE';
                setMilestone('action', 'active');
            } else if (detectedType === 'AMP') {
                livePageTypeText.textContent = 'AMP';
                setMilestone('action', 'active');
            } else if (detectedType === 'NORMAL_PAGE') {
                livePageTypeText.textContent = 'NORMAL_PAGE';
                setMilestone('action', 'active');
            } else if (detectedType) {
                livePageTypeText.textContent = detectedType;
            }

            if (detectedType && state.hops.length > 0) {
                state.hops[state.hops.length - 1].type = detectedType;
                renderHopChain();
            }
        } else if (cleanLine.includes('[Redirect]')) {
            lineClass = 'term-yellow';
            const m = cleanLine.match(/\[Redirect\]\s+(https?:\/\/\S+)\s+->\s+(https?:\/\/\S+)/);
            if (m) {
                addHop('REDIRECT', m[2]);
            }
        } else if (cleanLine.includes('[Open') || cleanLine.includes('[Page]')) {
            const m = cleanLine.match(/(https?:\/\/\S+)/);
            if (m) {
                const url = m[1];
                addHop('VISIT', url);
            }
        } else if (cleanLine.includes('BRAND FOUND:')) {
            lineClass = 'term-green';
            const m = cleanLine.match(/BRAND FOUND:\s+(\w+)/);
            if (m) {
                liveBrandText.textContent = m[1];
            }
        } else if (cleanLine.includes('[AI Verifier]')) {
            lineClass = cleanLine.includes('MATCH') ? 'term-green' : 'term-yellow';
            setMilestone('forensics', 'completed');
        } else if (cleanLine.includes('[Verification] Y confirmed')) {
            lineClass = 'term-green';
        } else if (cleanLine.includes('PHISHING')) {
            lineClass = 'term-red';
        }

        appendLogLine(cleanLine, lineClass);
    }

    function addHop(type, url) {
        if (state.hops.some(h => h.url === url)) return;
        state.hops.push({ type, url });
        renderHopChain();
    }

    function renderHopChain() {
        if (!state.hops.length) {
            hopFlowContainer.innerHTML = '<div class="hop-placeholder">No active hops.</div>';
            hopCountBadge.textContent = '0 Hops';
            return;
        }

        hopCountBadge.textContent = `${state.hops.length} Hops`;
        hopFlowContainer.innerHTML = state.hops.map((hop, idx) => {
            let badgeClass = 'badge-redirect';
            if (idx === 0 && hop.type === 'START') badgeClass = 'badge-start';
            else if (hop.type === 'MONEY_SITE' || hop.type === 'MONEY') badgeClass = 'badge-money';
            else if (hop.type === 'AMP') badgeClass = 'badge-amp';
            else if (hop.type === 'LANDING_PAGE' || hop.type === 'LANDING') badgeClass = 'badge-landing';
            else if (hop.type === 'NORMAL_PAGE') badgeClass = 'badge-start';

            const isLast = idx === state.hops.length - 1;
            return `
                <div class="hop-step-item">
                    <span class="hop-step-badge ${badgeClass}">${hop.type}</span>
                    <span class="hop-step-url ${isLast ? 'current' : ''}">${escapeHtml(hop.url)}</span>
                </div>
            `;
        }).join('');

        hopFlowContainer.scrollTop = hopFlowContainer.scrollHeight;
    }

    function appendLogLine(text, cssClass = '') {
        const div = document.createElement('div');
        div.className = `term-line ${cssClass}`;
        div.textContent = text;
        terminalLog.appendChild(div);

        if (state.autoScroll) {
            terminalLog.scrollTop = terminalLog.scrollHeight;
        }
    }

    // 6. Forensic Report Loader
    async function loadForensicReport(crawlId) {
        state.currentCrawlId = crawlId;
        try {
            const res = await fetch(`/api/report/${crawlId}`);
            if (!res.ok) throw new Error('Failed to load report');
            const data = await res.json();
            renderForensicReport(data, crawlId);
        } catch (e) {
            console.error('Failed to load forensic report', e);
        }
    }

    function renderForensicReport(report, crawlId) {
        const pages = report.pages || [];
        const lastPage = pages[pages.length - 1] || {};
        const moneyPage = pages.find(p => p.page_type === 'MONEY_SITE') || lastPage;
        const classification = report.classification || 'UNKNOWN';

        // Verdict Banner
        const verdictBanner = document.getElementById('verdictBanner');
        const verdictTitle = document.getElementById('verdictTitle');
        const verdictDetails = document.getElementById('verdictDetails');
        const verdictIcon = document.getElementById('verdictIcon');
        const badgeBrand = document.getElementById('badgeBrand');
        const badgePageType = document.getElementById('badgePageType');

        const dominantBrand = report.selected_brand || report.dominant_brand || moneyPage.selected_brand || 'UNKNOWN';
        badgeBrand.textContent = `Brand: ${dominantBrand}`;
        badgePageType.textContent = `Type: ${moneyPage.page_type || 'NORMAL_PAGE'}`;

        verdictBanner.className = 'verdict-banner';
        if (classification.includes('OUR SITE') || classification.includes('LOGO CONFIRMED')) {
            verdictBanner.classList.add('verdict-our-site');
            verdictIcon.textContent = '✅';
            verdictTitle.textContent = `${dominantBrand} - AUTHENTIC OUR SITE`;
            verdictDetails.textContent = 'Dual-asset forensics and live chat verification confirm full brand authenticity.';
        } else if (classification.includes('PHISHING')) {
            verdictBanner.classList.add('verdict-phishing');
            verdictIcon.textContent = '🚨';
            verdictTitle.textContent = `${dominantBrand} - PHISHING DETECTED`;
            verdictDetails.textContent = classification;
        } else {
            verdictIcon.textContent = '⚠️';
            verdictTitle.textContent = classification;
            verdictDetails.textContent = `Traversal terminated: ${moneyPage.stop_reason || 'Destination reached'}`;
        }

        // Logo Forensics
        const logoData = (moneyPage.logo_forensics && moneyPage.logo_forensics.logo) || moneyPage.logo_forensics || {};
        const logoImg = document.getElementById('logoPreviewImg');
        const logoNoImg = document.getElementById('logoNoImg');
        const logoStatusPill = document.getElementById('logoStatusPill');
        const logoBrandVal = document.getElementById('logoBrandVal');
        const logoConfidenceVal = document.getElementById('logoConfidenceVal');
        const logoMeterBar = document.getElementById('logoMeterBar');
        const logoIsOursVal = document.getElementById('logoIsOursVal');

        if (moneyPage.logo_asset_url) {
            logoImg.src = moneyPage.logo_asset_url;
            logoImg.classList.remove('hidden');
            logoNoImg.classList.add('hidden');
        } else {
            logoImg.classList.add('hidden');
            logoNoImg.classList.remove('hidden');
        }

        const isOurLogo = logoData.is_our_logo;
        logoStatusPill.textContent = isOurLogo ? 'MATCH' : (logoData.brand ? 'MISMATCH' : 'UNKNOWN');
        logoStatusPill.className = `asset-status-pill ${isOurLogo ? 'pill-match' : (logoData.brand ? 'pill-mismatch' : 'pill-unknown')}`;
        logoBrandVal.textContent = logoData.brand || 'Unknown';
        const logoConf = Math.round((logoData.confidence || 0) * 100);
        logoConfidenceVal.textContent = `${logoConf}%`;
        logoMeterBar.style.width = `${logoConf}%`;
        logoIsOursVal.textContent = isOurLogo ? 'YES (Verified Brand)' : 'NO / Mismatch';

        // Favicon Forensics
        const favData = (moneyPage.logo_forensics && moneyPage.logo_forensics.favicon) || {};
        const favImg = document.getElementById('favPreviewImg');
        const favNoImg = document.getElementById('favNoImg');
        const favStatusPill = document.getElementById('favStatusPill');
        const favBrandVal = document.getElementById('favBrandVal');
        const favConfidenceVal = document.getElementById('favConfidenceVal');
        const favMeterBar = document.getElementById('favMeterBar');
        const favIsOursVal = document.getElementById('favIsOursVal');

        if (moneyPage.favicon_asset_url) {
            favImg.src = moneyPage.favicon_asset_url;
            favImg.classList.remove('hidden');
            favNoImg.classList.add('hidden');
        } else {
            favImg.classList.add('hidden');
            favNoImg.classList.remove('hidden');
        }

        const isOurFav = favData.is_our_logo;
        favStatusPill.textContent = isOurFav ? 'MATCH' : (favData.brand ? 'MISMATCH' : 'UNKNOWN');
        favStatusPill.className = `asset-status-pill ${isOurFav ? 'pill-match' : (favData.brand ? 'pill-mismatch' : 'pill-unknown')}`;
        favBrandVal.textContent = favData.brand || 'Unknown';
        const favConf = Math.round((favData.confidence || 0) * 100);
        favConfidenceVal.textContent = `${favConf}%`;
        favMeterBar.style.width = `${favConf}%`;
        favIsOursVal.textContent = isOurFav ? 'YES (Verified Icon)' : 'NO';

        // Live Chat Forensics
        const liveChatInfo = report.live_chat_info || moneyPage.live_chat_info || null;
        const liveChatResult = report.live_chat_verification || moneyPage.live_chat_verification || null;
        const chatStatusPill = document.getElementById('chatStatusPill');
        const chatProviderBadge = document.getElementById('chatProviderBadge');
        const chatVerificationNote = document.getElementById('chatVerificationNote');
        const chatProviderVal = document.getElementById('chatProviderVal');
        const chatIdVal = document.getElementById('chatIdVal');
        const chatUrlVal = document.getElementById('chatUrlVal');
        const chatResultVal = document.getElementById('chatResultVal');

        if (liveChatInfo) {
            chatProviderBadge.textContent = (liveChatInfo.provider || 'UNKNOWN').toUpperCase();
            chatProviderVal.textContent = liveChatInfo.provider || '--';
            chatIdVal.textContent = liveChatInfo.chat_id || '--';
            chatUrlVal.textContent = liveChatInfo.constructed_url || '--';
            chatUrlVal.href = liveChatInfo.constructed_url || '#';

            const chatStatus = (liveChatResult && liveChatResult.status) || 'FOUND';
            chatStatusPill.textContent = chatStatus;
            chatStatusPill.className = `asset-status-pill ${chatStatus === 'VERIFIED' ? 'pill-match' : 'pill-unknown'}`;
            chatResultVal.textContent = chatStatus;
            chatVerificationNote.textContent = (liveChatResult && liveChatResult.details) || 'Pattern extracted from Money Site.';
        } else {
            chatProviderBadge.textContent = 'NOT DETECTED';
            chatStatusPill.textContent = 'NOT FOUND';
            chatStatusPill.className = 'asset-status-pill pill-unknown';
            chatProviderVal.textContent = 'None';
            chatIdVal.textContent = 'None';
            chatUrlVal.textContent = 'None';
            chatUrlVal.removeAttribute('href');
            chatResultVal.textContent = 'NOT FOUND';
            chatVerificationNote.textContent = 'No Live Chat scripts identified.';
        }

        // Structural Signals
        const signals = moneyPage.page_type_signals || {};
        document.getElementById('sigGameCards').textContent = signals.gameCardsCount || 0;
        document.getElementById('sigProviders').textContent = signals.providerControlsCount || 0;
        document.getElementById('sigCategories').textContent = signals.categoryControlsCount || 0;
        document.getElementById('sigImageTiles').textContent = signals.linkedImageTilesCount || 0;
        document.getElementById('sigInternalLinks').textContent = signals.internalLinksCount || 0;
        document.getElementById('sigBanner').textContent = signals.hasRotatingBanner ? 'Yes' : 'No';

        // Raw JSON
        document.getElementById('rawReportJson').textContent = JSON.stringify(report, null, 2);
    }

    // 7. History Loader & Filter
    async function fetchHistory() {
        try {
            const res = await fetch('/api/history');
            const data = await res.json();
            state.historyList = data;
            renderHistoryTable();
        } catch (e) {
            console.error('Failed to load history', e);
        }
    }

    function renderHistoryTable() {
        const query = (historySearchInput.value || '').toLowerCase();
        const filterVal = historyFilterClassification.value;

        const filtered = state.historyList.filter(item => {
            const matchesQuery = !query || 
                item.start_url.toLowerCase().includes(query) || 
                item.final_url.toLowerCase().includes(query) || 
                item.brand.toLowerCase().includes(query);

            let matchesFilter = true;
            if (filterVal === 'OUR SITE') {
                matchesFilter = item.classification.includes('OUR SITE') || item.classification.includes('LOGO CONFIRMED');
            } else if (filterVal === 'PHISHING') {
                matchesFilter = item.classification.includes('PHISHING');
            } else if (filterVal === 'BLOCKED') {
                matchesFilter = item.classification.includes('BLOCKED');
            }

            return matchesQuery && matchesFilter;
        });

        if (!filtered.length) {
            historyTableBody.innerHTML = '<tr><td colspan="7" class="text-center">No crawls match criteria.</td></tr>';
            return;
        }

        historyTableBody.innerHTML = filtered.map(item => {
            let pillClass = 'pill-unknown';
            if (item.classification.includes('OUR SITE') || item.classification.includes('LOGO CONFIRMED')) {
                pillClass = 'pill-match';
            } else if (item.classification.includes('PHISHING')) {
                pillClass = 'pill-mismatch';
            }

            return `
                <tr>
                    <td>${item.formatted_time}</td>
                    <td><strong>${escapeHtml(item.brand)}</strong></td>
                    <td class="link-truncate" title="${escapeHtml(item.start_url)}">${escapeHtml(item.start_url)}</td>
                    <td class="link-truncate" title="${escapeHtml(item.final_url)}">${escapeHtml(item.final_url)}</td>
                    <td><span class="asset-status-pill ${pillClass}">${escapeHtml(item.classification)}</span></td>
                    <td>${item.page_count}</td>
                    <td>
                        <button class="btn-view-report" data-crawlid="${item.crawl_id}">Inspect</button>
                    </td>
                </tr>
            `;
        }).join('');

        // Attach inspect buttons
        historyTableBody.querySelectorAll('.btn-view-report').forEach(btn => {
            btn.addEventListener('click', () => {
                const cId = btn.getAttribute('data-crawlid');
                loadForensicReport(cId);
                switchTab('tabForensics');
            });
        });
    }

    historySearchInput.addEventListener('input', renderHistoryTable);
    historyFilterClassification.addEventListener('change', renderHistoryTable);
    btnRefreshHistory.addEventListener('click', fetchHistory);

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});
