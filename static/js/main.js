/* DataGenius — main client script */
(function () {
    'use strict';

    // ---- State ----
    let currentDatasetId = null;

    // ---- DOM refs ----
    const $ = (id) => document.getElementById(id);
    const form = $('upload-form');
    const fileInput = $('file-input');
    const browseBtn = $('browse-btn');
    const dzFilename = $('dz-filename');
    const statusBox = $('upload-status');
    const statusText = $('upload-status-text');
    const errorBox = $('upload-error');
    const results = $('results');

    // ---- Drag / drop / browse ----
    browseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });
    form.addEventListener('click', () => fileInput.click());

    ['dragenter', 'dragover'].forEach(ev => {
        form.addEventListener(ev, (e) => {
            e.preventDefault();
            form.classList.add('dragover');
        });
    });
    ['dragleave', 'drop'].forEach(ev => {
        form.addEventListener(ev, (e) => {
            e.preventDefault();
            form.classList.remove('dragover');
        });
    });
    form.addEventListener('drop', (e) => {
        if (e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleUpload(e.dataTransfer.files[0]);
        }
    });
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            handleUpload(fileInput.files[0]);
        }
    });

    // ---- Upload + analyze ----
    async function handleUpload(file) {
        errorBox.hidden = true;
        results.hidden = true;
        dzFilename.textContent = file.name;
        statusBox.hidden = false;
        statusText.textContent = `Analyzing ${file.name}…`;

        const fd = new FormData();
        fd.append('file', file);

        try {
            const res = await fetch('/upload', { method: 'POST', body: fd });
            const data = await res.json();

            if (!res.ok || !data.success) {
                showError(data.error || 'Unknown server error.');
                return;
            }
            currentDatasetId = data.dataset_id;
            renderResults(data);
            addBotMessage(
                `I've finished analyzing **${data.filename}**. Ask me about its rows, columns, ` +
                `missing values, or statistics of any column!`
            );
        } catch (err) {
            showError(`Network error: ${err.message}`);
        } finally {
            statusBox.hidden = true;
        }
    }

    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.hidden = false;
    }

    // ---- Render results ----
    function renderResults(data) {
        $('dataset-filename').innerHTML =
            `File: <code>${escapeHtml(data.filename)}</code>`;
        renderStats(data.summary);
        renderInsights(data.narrative);
        renderAIInsights(data.ai_insights);
        renderPreview(data.summary);
        renderDtypes(data.summary);
        renderCharts(data.charts);

        results.hidden = false;
        results.classList.add('fade-in');
        // Smooth scroll
        setTimeout(() => {
            document.getElementById('summary').scrollIntoView({ behavior: 'smooth' });
        }, 120);
    }

    // Inline SVG icons for the AI insight group headers
    const AI_ICONS = {
        shield: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
        chart: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
        link: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
        grid: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    };

    function renderAIInsights(ai) {
        const headlineEl = $('ai-headline');
        const groupsEl = $('ai-groups');
        if (!ai || !ai.groups || ai.groups.length === 0) {
            headlineEl.innerHTML = mdToHtml((ai && ai.headline) ||
                'No notable statistical patterns surfaced for this dataset.');
            groupsEl.innerHTML = '';
            return;
        }
        headlineEl.innerHTML = mdToHtml(ai.headline);
        groupsEl.innerHTML = ai.groups.map(g => {
            const icon = AI_ICONS[g.icon] || AI_ICONS.grid;
            const items = g.items.map(it => {
                const badge = (it.severity === 'warning' || it.severity === 'critical')
                    ? `<span class="ai-badge">${it.severity}</span>` : '';
                return `<div class="ai-item sev-${escapeAttr(it.severity)}">
                            <span class="ai-dot"></span>
                            <span>${mdToHtml(it.text)}${badge}</span>
                        </div>`;
            }).join('');
            return `<div class="ai-group">
                        <div class="ai-group-head">
                            <span class="ai-group-icon">${icon}</span>
                            <span class="ai-group-title">${escapeHtml(g.title)}</span>
                        </div>
                        <div class="ai-items">${items}</div>
                    </div>`;
        }).join('');
    }

    function renderStats(s) {
        const grid = $('stats-grid');
        const items = [
            { icon: '📊', label: 'Rows', value: s.rows.toLocaleString() },
            { icon: '📁', label: 'Columns', value: s.columns },
            { icon: '🔢', label: 'Numeric', value: s.numeric_columns.length },
            { icon: '🏷️', label: 'Categorical', value: s.categorical_columns.length },
            { icon: '⚠️', label: 'Missing', value: s.total_missing.toLocaleString() },
            { icon: '🔁', label: 'Duplicates', value: s.duplicate_rows.toLocaleString() },
            { icon: '💾', label: 'Memory', value: s.memory_usage_kb.toFixed(1) + ' KB' },
        ];
        grid.innerHTML = items.map(it => `
            <div class="stat">
                <div class="stat-icon">${it.icon}</div>
                <div class="stat-label">${it.label}</div>
                <div class="stat-value">${it.value}</div>
            </div>
        `).join('');
    }

    function renderInsights(narrative) {
        const list = $('insights-list');
        list.innerHTML = narrative.map(text => `<li>${mdToHtml(text)}</li>`).join('');
    }

    function renderPreview(s) {
        const table = $('preview-table');
        if (!s.preview || s.preview.length === 0) {
            table.innerHTML = '<tr><td>No preview available.</td></tr>';
            return;
        }
        const cols = s.column_names;
        const head = `<thead><tr>${cols.map(c =>
            `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>`;
        const body = '<tbody>' + s.preview.map(row =>
            '<tr>' + cols.map(c =>
                `<td>${escapeHtml(String(row[c] ?? ''))}</td>`
            ).join('') + '</tr>'
        ).join('') + '</tbody>';
        table.innerHTML = head + body;
    }

    function renderDtypes(s) {
        const grid = $('dtype-grid');
        grid.innerHTML = Object.entries(s.dtypes).map(([col, type]) => `
            <div class="dtype-item">
                <span class="dtype-name">${escapeHtml(col)}</span>
                <span class="dtype-value">${escapeHtml(type)}</span>
            </div>
        `).join('');
    }

    function renderCharts(charts) {
        const grid = $('charts-grid');
        if (!charts || charts.length === 0) {
            grid.innerHTML = '<p style="color:var(--text-muted)">No charts generated for this dataset.</p>';
            return;
        }
        grid.innerHTML = charts.map(c => `
            <div class="chart-card">
                <h3>${escapeHtml(c.title)}</h3>
                <p>${mdToHtml(c.description)}</p>
                <img src="${escapeAttr(c.url)}" alt="${escapeAttr(c.title)}" loading="lazy" />
            </div>
        `).join('');
    }

    // ---- Tiny markdown → HTML (bold + inline code) ----
    function mdToHtml(text) {
        let safe = escapeHtml(text);
        safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');
        safe = safe.replace(/\n/g, '<br>');
        return safe;
    }
    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function escapeAttr(s) { return escapeHtml(s); }

    // ---- Cleaning ----
    const cleanBtn = $('clean-btn');
    const cleanStatus = $('clean-status');
    const cleanError = $('clean-error');
    const cleanResults = $('clean-results');

    cleanBtn.addEventListener('click', async () => {
        if (!currentDatasetId) {
            cleanError.textContent = 'Please upload a dataset first.';
            cleanError.hidden = false;
            return;
        }
        const opts = {};
        document.querySelectorAll('#clean-options input[type="checkbox"]').forEach(cb => {
            opts[cb.dataset.opt] = cb.checked;
        });
        document.querySelectorAll('#clean-options select[data-opt]').forEach(sel => {
            opts[sel.dataset.opt] = sel.value;
        });
        cleanError.hidden = true;
        cleanResults.hidden = true;
        cleanStatus.hidden = false;
        cleanBtn.disabled = true;

        try {
            const res = await fetch('/clean', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    dataset_id: currentDatasetId,
                    options: opts,
                }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                cleanError.textContent = data.error || 'Cleaning failed.';
                cleanError.hidden = false;
                return;
            }
            // Switch the active dataset to the cleaned one so chat queries it
            currentDatasetId = data.dataset_id;
            renderCleaningResults(data);
            // Update charts grid to show cleaned-dataset charts
            renderCharts(data.charts);
            // Refresh the summary + AI insights to reflect the cleaned data
            renderStats(data.after_summary);
            renderAIInsights(data.ai_insights);
            // Update the file label
            $('dataset-filename').innerHTML =
                `File: <code>${escapeHtml(data.filename)}</code>`;
            addBotMessage(
                `Cleaning complete! 🧹 The dataset is now **${data.after_summary.rows.toLocaleString()} ` +
                `rows × ${data.after_summary.columns} columns** with ` +
                `**${data.after_summary.total_missing.toLocaleString()} missing values**. ` +
                `I've refreshed the AI Insights too — ask me anything about the cleaned data.`
            );
        } catch (err) {
            cleanError.textContent = `Network error: ${err.message}`;
            cleanError.hidden = false;
        } finally {
            cleanStatus.hidden = true;
            cleanBtn.disabled = false;
        }
    });

    function renderCleaningResults(data) {
        const report = data.report;
        const before = data.before_summary;
        const after = data.after_summary;

        // Before / After grid
        const baGrid = $('ba-grid');
        const metrics = [
            { key: 'rows', label: 'Rows', better: 'either' },
            { key: 'columns', label: 'Columns', better: 'either' },
            { key: 'total_missing', label: 'Missing values', better: 'down' },
            { key: 'duplicate_rows', label: 'Duplicate rows', better: 'down' },
            { key: 'memory_usage_kb', label: 'Memory (KB)', better: 'down', fmt: v => v.toFixed(1) },
        ];
        const beforeRows = metrics.map(m => {
            const v = before[m.key];
            return `<div class="ba-row"><span class="label">${m.label}</span>
                    <span class="value">${m.fmt ? m.fmt(v) : v.toLocaleString()}</span></div>`;
        }).join('');
        const afterRows = metrics.map(m => {
            const vb = before[m.key];
            const va = after[m.key];
            let deltaCls = 'delta-same', deltaText = '=';
            if (va < vb) {
                deltaCls = m.better === 'down' ? 'delta-down' : 'delta-up';
                deltaText = `−${(vb - va).toLocaleString()}`;
            } else if (va > vb) {
                deltaCls = m.better === 'down' ? 'delta-up' : 'delta-down';
                deltaText = `+${(va - vb).toLocaleString()}`;
            }
            return `<div class="ba-row"><span class="label">${m.label}</span>
                    <span class="value">${m.fmt ? m.fmt(va) : va.toLocaleString()}
                    <span class="delta ${deltaCls}">${deltaText}</span></span></div>`;
        }).join('');
        baGrid.innerHTML = `
            <div class="ba-col before">
                <h4>Before <span class="pill">original</span></h4>
                <div class="ba-rows">${beforeRows}</div>
            </div>
            <div class="ba-col after">
                <h4>After <span class="pill">cleaned</span></h4>
                <div class="ba-rows">${afterRows}</div>
            </div>
        `;

        // Cleaning insights
        $('cleaning-insights').innerHTML =
            data.cleaning_insights.map(t => `<li>${mdToHtml(t)}</li>`).join('');

        // Action log
        const log = $('action-log');
        if (!report.actions || report.actions.length === 0) {
            log.innerHTML = '<div class="action-empty">No cleaning operations were necessary — your dataset was already tidy. ✨</div>';
        } else {
            log.innerHTML = report.actions.map(a => `
                <div class="action-item">
                    <div class="action-icon">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="20 6 9 17 4 12"/>
                        </svg>
                    </div>
                    <div class="action-text">
                        <strong>${escapeHtml(a.action)}</strong>
                        <small>${mdToHtml(a.detail)}</small>
                    </div>
                    <div class="action-count">${a.count.toLocaleString()}</div>
                </div>
            `).join('');
        }

        // Cleaned-dataset narrative (insights AFTER cleaning)
        $('cleaned-narrative').innerHTML =
            data.after_narrative.map(t => `<li>${mdToHtml(t)}</li>`).join('');

        // Download link
        const dl = $('download-link');
        if (data.download_url) {
            dl.href = data.download_url;
            dl.style.display = '';
        } else {
            dl.style.display = 'none';
        }

        cleanResults.hidden = false;
        cleanResults.classList.add('fade-in');
        setTimeout(() => {
            document.getElementById('cleaning').scrollIntoView({ behavior: 'smooth' });
        }, 100);
    }

    // ---- Generate PDF report ----
    const reportBtn = $('report-btn');
    const reportStatus = $('report-status');
    const reportError = $('report-error');
    const reportLink = $('report-link');

    reportBtn.addEventListener('click', async () => {
        if (!currentDatasetId) {
            reportError.textContent = 'Please upload (and optionally clean) a dataset first.';
            reportError.hidden = false;
            return;
        }
        reportError.hidden = true;
        reportLink.hidden = true;
        reportStatus.hidden = false;
        reportBtn.disabled = true;

        try {
            const res = await fetch('/report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dataset_id: currentDatasetId }),
            });
            const data = await res.json();
            if (!res.ok || !data.success) {
                reportError.textContent = data.error || 'Report generation failed.';
                reportError.hidden = false;
                return;
            }
            reportLink.href = data.report_url;
            reportLink.download = data.filename;
            reportLink.hidden = false;
            // Auto-open in a new tab
            window.open(data.report_url, '_blank');
            addBotMessage(
                `Your analysis report is ready! 📄 Open it via the **Open report** button ` +
                `in the report card.`
            );
        } catch (err) {
            reportError.textContent = `Network error: ${err.message}`;
            reportError.hidden = false;
        } finally {
            reportStatus.hidden = true;
            reportBtn.disabled = false;
        }
    });

    // ---- Chat widget ----
    const chatToggle = $('chat-toggle');
    const chatPanel = $('chat-panel');
    const chatClose = $('chat-close');
    const chatForm = $('chat-form');
    const chatInput = $('chat-input');
    const chatMessages = $('chat-messages');

    chatToggle.addEventListener('click', () => {
        chatPanel.hidden = !chatPanel.hidden;
        if (!chatPanel.hidden) chatInput.focus();
    });
    chatClose.addEventListener('click', () => { chatPanel.hidden = true; });

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = chatInput.value.trim();
        if (!text) return;
        chatInput.value = '';
        addUserMessage(text);
        showTyping();
        try {
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, dataset_id: currentDatasetId }),
            });
            const data = await res.json();
            removeTyping();
            addBotMessage(data.reply || 'Sorry, I had trouble responding.');
        } catch (err) {
            removeTyping();
            addBotMessage(`Connection error: ${err.message}`);
        }
    });

    function addUserMessage(text) {
        const div = document.createElement('div');
        div.className = 'msg msg-user';
        div.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
        chatMessages.appendChild(div);
        scrollChat();
    }
    function addBotMessage(text) {
        const div = document.createElement('div');
        div.className = 'msg msg-bot';
        div.innerHTML = `<div class="msg-bubble">${mdToHtml(text)}</div>`;
        chatMessages.appendChild(div);
        scrollChat();
    }
    function showTyping() {
        const div = document.createElement('div');
        div.className = 'msg msg-bot';
        div.id = 'typing-msg';
        div.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;
        chatMessages.appendChild(div);
        scrollChat();
    }
    function removeTyping() {
        const t = $('typing-msg');
        if (t) t.remove();
    }
    function scrollChat() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
})();
