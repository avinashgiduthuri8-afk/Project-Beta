// ── HTML escape helper ─────────────────────────────────────────────────────
// Use this whenever inserting untrusted data into innerHTML to prevent XSS.
function escHtml(str) {
    if (str == null) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

// ── Currency formatter ─────────────────────────────────────────────────────
// All monetary display values should go through this. Uses Indian locale
// (en-IN) so lakhs/crores group correctly. Never pass raw "$" strings here.
function formatCurrency(value) {
    return "₹" + Number(value || 0).toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

// ── Authenticated fetch helper ─────────────────────────────────────────────
// All dashboard API calls must include X-API-Key. Use this instead of fetch().
function authenticatedFetch(url, options) {
    options = options || {};
    // Use Headers API so caller-supplied headers (plain object or Headers instance)
    // are preserved correctly alongside the injected X-API-Key.
    var headers = new Headers(options.headers || {});
    headers.set("X-API-Key", window.DASHBOARD_API_KEY || "");
    options.headers = headers;
    return fetch(url, options);
}

// ── I-04: Cleanup engine helpers ──────────────────────────────────────────
function _relativeTime(date) {
    const diff = Math.round((Date.now() - date.getTime()) / 1000);
    if (diff < 60)    return diff + "s ago";
    if (diff < 3600)  return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
}

function _countdownStr(targetMs) {
    const diff = Math.round((targetMs - Date.now()) / 1000);
    if (diff <= 0) return "Running…";
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    return m + "m " + String(s).padStart(2, "0") + "s";
}

window._nextCleanupTs = null;

// Tick the countdown every second
setInterval(function() {
    const el = document.getElementById("so-next-cleanup");
    if (!el) return;
    if (!window._nextCleanupTs) { el.textContent = "—"; return; }
    el.textContent = _countdownStr(window._nextCleanupTs);
}, 1000);

// Populate the shared CoinDCX datalist once on page load
(async function loadCoinDCXCoins() {
    try {
        const resp = await authenticatedFetch("/api/supported-coins");
        const data = await resp.json();
        const dl = document.getElementById("coindcx-coins");
        if (!dl || !Array.isArray(data.coins)) return;
        dl.innerHTML = data.coins
            .map(c => `<option value="${escHtml(c)}"></option>`)
            .join("");
    } catch (e) {
        console.warn("[ProjectA] Could not load CoinDCX coin list:", e.message);
    }
})();

// Market Assets link → jump to Watchlist Center
document.addEventListener("DOMContentLoaded", () => {
    const link = document.getElementById("market-assets-link");
    if (link) {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const target = document.querySelector('.nav-anchor[data-target="watchlist-center"]');
            if (target) target.click();
        });
    }
});

document.addEventListener("DOMContentLoaded", () => {
    // 1. Core Config State Payload Bridge Unpacking Pipeline
    const stateEngineData = JSON.parse(document.getElementById("dashboard-state-payload-json").textContent);

    // ── Mobile sidebar toggle ──────────────────────────────────────────────
    const sidebarToggleBtn = document.getElementById("sidebar-toggle");
    const sidebar = document.getElementById("sidebar");
    if (sidebarToggleBtn && sidebar) {
        sidebarToggleBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            sidebar.classList.toggle("open");
        });
        // Close sidebar when clicking outside on mobile
        document.addEventListener("click", (e) => {
            if (sidebar.classList.contains("open") &&
                !sidebar.contains(e.target) &&
                e.target !== sidebarToggleBtn) {
                sidebar.classList.remove("open");
            }
        });
    }

    // 2. Sidebar Dropdown Menu Toggle Engine
    const dropdownToggles = document.querySelectorAll(".nav-dropdown-toggle");
    dropdownToggles.forEach(toggle => {
        toggle.addEventListener("click", () => {
            const menuId = "menu-" + toggle.getAttribute("data-menu");
            const menu = document.getElementById(menuId);
            const isHidden = menu.classList.contains("view-hidden");
            // Close all other menus
            document.querySelectorAll(".nav-dropdown-menu").forEach(m => {
                if (m.id !== menuId) m.classList.add("view-hidden");
            });
            document.querySelectorAll(".nav-dropdown-toggle").forEach(t => {
                if (t !== toggle) t.classList.remove("active");
            });
            // Toggle current
            if (isHidden) {
                menu.classList.remove("view-hidden");
                toggle.classList.add("active");
            } else {
                menu.classList.add("view-hidden");
                toggle.classList.remove("active");
            }
        });
    });

    // 3. Multi-Module Navigation Tab Switch Engine (SPA View Layer Pipeline)
    const sidebarAnchors = document.querySelectorAll(".nav-anchor");
    const layoutViews = document.querySelectorAll(".spa-view-layer");

    sidebarAnchors.forEach(anchor => {
        anchor.addEventListener("click", (event) => {
            event.preventDefault();
            sidebarAnchors.forEach(el => el.classList.remove("active"));
            layoutViews.forEach(el => el.classList.add("view-hidden"));

            anchor.classList.add("active");
            const structuralTargetId = anchor.getAttribute("data-target");
            document.getElementById(structuralTargetId).classList.remove("view-hidden");

            // Close mobile sidebar after navigation
            if (sidebar) sidebar.classList.remove("open");
        });
    });

    // Alerts Popover Floating Panel
    const dropdownTrigger = document.getElementById("alert-dropdown-btn");
    const popoverOverlay = document.getElementById("alerts-popup-overlay");

    dropdownTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        popoverOverlay.classList.toggle("view-hidden");
    });

    document.addEventListener("click", (e) => {
        if (!popoverOverlay.classList.contains("view-hidden") && !popoverOverlay.contains(e.target)) {
            popoverOverlay.classList.add("view-hidden");
        }
    });

    // 4. Color Framework Application Core Theme Inversion Routine
    const themeControlBtn = document.getElementById("global-theme-toggle");
    const documentHtmlElement = document.documentElement;

    // Restore saved theme on page load
    const savedTheme = localStorage.getItem("pa-theme");
    if (savedTheme && (savedTheme === "dark" || savedTheme === "light")) {
        documentHtmlElement.setAttribute("data-theme", savedTheme);
    }

    themeControlBtn.addEventListener("click", () => {
        const currentlyActiveTheme = documentHtmlElement.getAttribute("data-theme");
        const inverseCalculatedTheme = currentlyActiveTheme === "dark" ? "light" : "dark";
        documentHtmlElement.setAttribute("data-theme", inverseCalculatedTheme);
        localStorage.setItem("pa-theme", inverseCalculatedTheme);
        refreshDashboardCharts(inverseCalculatedTheme);
    });

    // 5. Global Chart.js Subsystem Processing Loops Configuration Matrix
    let runtimeChartHandles = {};

    function initializeAllDashboardWidgets(themeContext) {
        if (typeof Chart === "undefined") {
            console.warn("Chart.js unavailable");
            return;
        }

        const isDarkThemeActive = themeContext === "dark";
        const gridBorderColor = isDarkThemeActive ? "#1e1e2e" : "#E2E8F0";
        const labelTextColor = isDarkThemeActive ? "#8b8ba0" : "#64748b";

        // Home Pie — Signal Distribution
        const ctxHomePie = document.getElementById("homePieChart");
        if (ctxHomePie) {
            runtimeChartHandles.homePie = new Chart(ctxHomePie.getContext("2d"), {
                type: "doughnut",
                data: {
                    labels: stateEngineData.charts.distribution.labels,
                    datasets: [{
                        data: stateEngineData.charts.distribution.data,
                        backgroundColor: ["#3b82f6", "#f59e0b", "#a78bfa"],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { color: labelTextColor, font: { family: "Inter", size: 10 }, padding: 12 }
                        }
                    }
                }
            });
        }

        // Home Gauge — Market Strength
        const ctxHomeGauge = document.getElementById("homeGaugeChart");
        if (ctxHomeGauge) {
            const innerStrengthDataValue = stateEngineData.market_state.market_strength;
            runtimeChartHandles.homeGauge = new Chart(ctxHomeGauge.getContext("2d"), {
                type: "doughnut",
                data: {
                    datasets: [{
                        data: [innerStrengthDataValue, 100 - innerStrengthDataValue],
                        backgroundColor: ["#00d4a0", isDarkThemeActive ? "#1e1e2e" : "#E2E8F0"],
                        circumference: 180,
                        rotation: 270,
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "85%",
                    plugins: { tooltip: { enabled: false }, legend: { display: false } }
                }
            });
        }

        // Home Line — Daily Signal Breakouts
        const ctxHomeLine = document.getElementById("homeLineChart");
        if (ctxHomeLine) {
            runtimeChartHandles.homeLine = new Chart(ctxHomeLine.getContext("2d"), {
                type: "line",
                data: {
                    labels: stateEngineData.charts.daily_signals.labels,
                    datasets: [{
                        data: stateEngineData.charts.daily_signals.data,
                        borderColor: "#3b82f6",
                        backgroundColor: "rgba(59,130,246,0.06)",
                        fill: true,
                        tension: 0.35,
                        borderWidth: 2,
                        pointRadius: 2,
                        pointBackgroundColor: "#3b82f6"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor, font: { size: 10 } } },
                        y: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor, font: { size: 10 } } }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }

        // Portfolio Pie
        const ctxPortfolioPie = document.getElementById("portfolioPieChart");
        if (ctxPortfolioPie) {
            runtimeChartHandles.portfolioPie = new Chart(ctxPortfolioPie.getContext("2d"), {
                type: "pie",
                data: {
                    labels: stateEngineData.charts.asset_allocation.labels,
                    datasets: [{
                        data: stateEngineData.charts.asset_allocation.data,
                        backgroundColor: ["#3b82f6", "#00d4a0", "#f59e0b", "#64748b"],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { color: labelTextColor, font: { family: "Inter", size: 10 } }
                        }
                    }
                }
            });
        }

        // Portfolio Growth Line
        const ctxPortfolioLine = document.getElementById("portfolioGrowthLineChart");
        if (ctxPortfolioLine) {
            runtimeChartHandles.portfolioLine = new Chart(ctxPortfolioLine.getContext("2d"), {
                type: "line",
                data: {
                    labels: stateEngineData.charts.portfolio_growth.labels,
                    datasets: [{
                        data: stateEngineData.charts.portfolio_growth.data,
                        borderColor: "#00d4a0",
                        backgroundColor: "rgba(0,212,160,0.06)",
                        fill: true,
                        tension: 0.2,
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } },
                        y: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }

        // VGX Equity Curve
        const vgxEquity = stateEngineData.vgx_overview && stateEngineData.vgx_overview.equity_curve
            ? stateEngineData.vgx_overview.equity_curve
            : { labels: ["Start"], data: [1000000] };

        const ctxVgxEquity = document.getElementById("vgxEquityChart");
        if (ctxVgxEquity) {
            runtimeChartHandles.vgxEquity = new Chart(ctxVgxEquity.getContext("2d"), {
                type: "line",
                data: {
                    labels: vgxEquity.labels,
                    datasets: [{
                        label: "Virtual Balance (₹)",
                        data: vgxEquity.data,
                        borderColor: "#3b82f6",
                        backgroundColor: "rgba(59,130,246,0.08)",
                        fill: true,
                        tension: 0.35,
                        borderWidth: 2.5,
                        pointRadius: vgxEquity.data.length > 30 ? 0 : 3,
                        pointHoverRadius: 5,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: "index", intersect: false },
                    scales: {
                        x: {
                            grid: { color: gridBorderColor },
                            ticks: { color: labelTextColor, maxTicksLimit: 8, maxRotation: 0, font: { size: 10 } }
                        },
                        y: {
                            grid: { color: gridBorderColor },
                            ticks: {
                                color: labelTextColor,
                                font: { size: 10 },
                                callback: v => "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })
                            }
                        }
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: ctx => "₹" + Number(ctx.parsed.y).toLocaleString("en-IN", { maximumFractionDigits: 2 })
                            }
                        }
                    }
                }
            });
        }

        // VGX Win/Loss Bar
        const vgxWL = stateEngineData.vgx_overview && stateEngineData.vgx_overview.win_loss_chart
            ? stateEngineData.vgx_overview.win_loss_chart
            : { labels: ["Wins", "Losses"], data: [0, 0] };

        const ctxVgxWL = document.getElementById("vgxWinLossChart");
        if (ctxVgxWL) {
            runtimeChartHandles.vgxWinLoss = new Chart(ctxVgxWL.getContext("2d"), {
                type: "bar",
                data: {
                    labels: vgxWL.labels,
                    datasets: [{
                        data: vgxWL.data,
                        backgroundColor: ["rgba(0,212,160,0.7)", "rgba(244,63,94,0.7)"],
                        borderColor:     ["#00d4a0", "#f43f5e"],
                        borderWidth: 1.5,
                        borderRadius: 6,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } },
                        y: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor, precision: 0 }, beginAtZero: true }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }
    }

    function refreshDashboardCharts(themeContext) {
        Object.keys(runtimeChartHandles).forEach(key => {
            if (runtimeChartHandles[key]) runtimeChartHandles[key].destroy();
        });
        runtimeChartHandles = {};
        initializeAllDashboardWidgets(themeContext);
    }

    const initialTheme = documentHtmlElement.getAttribute("data-theme") || "dark";
    initializeAllDashboardWidgets(initialTheme);

    // ═══════════════════════════════════════════════════════════════════════
    // Settings Persistence
    // ═══════════════════════════════════════════════════════════════════════
    const riskSelect = document.getElementById("settings-risk-profile");
    const refreshSelect = document.getElementById("settings-refresh-interval");
    const saveSettingsBtn = document.getElementById("settings-save-btn");

    if (localStorage.getItem("pa-risk-profile") && riskSelect) {
        riskSelect.value = localStorage.getItem("pa-risk-profile");
    }
    if (localStorage.getItem("pa-refresh-interval") && refreshSelect) {
        refreshSelect.value = localStorage.getItem("pa-refresh-interval");
    }

    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener("click", () => {
            if (riskSelect) localStorage.setItem("pa-risk-profile", riskSelect.value);
            if (refreshSelect) localStorage.setItem("pa-refresh-interval", refreshSelect.value);
            const originalText = saveSettingsBtn.textContent;
            saveSettingsBtn.textContent = "Saved ✓";
            saveSettingsBtn.style.backgroundColor = "var(--green)";
            setTimeout(() => {
                saveSettingsBtn.textContent = originalText;
                saveSettingsBtn.style.backgroundColor = "";
            }, 1500);
        });
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Live Data Refresh Engine — patches DOM from /api/v1/state
    // ─────────────────────────────────────────────────────────────────────────

    function findCardValueNode(labelText) {
        const cards = document.querySelectorAll(".metric-card h5");
        for (const h5 of cards) {
            if (h5.textContent.trim() === labelText) {
                return h5.parentElement.querySelector("h2, h3");
            }
        }
        return null;
    }

    function findCardFooterSpan(labelText) {
        const cards = document.querySelectorAll(".metric-card h5");
        for (const h5 of cards) {
            if (h5.textContent.trim() === labelText) {
                return h5.parentElement.querySelector(".card-footer-metric .text-white");
            }
        }
        return null;
    }

    function patchChart(handle, labels, data) {
        if (!handle) return;
        handle.data.labels = labels;
        handle.data.datasets[0].data = data;
        handle.update("none");
    }

    function patchGauge(handle, strength) {
        if (!handle) return;
        handle.data.datasets[0].data = [strength, 100 - strength];
        handle.update("none");
        const readout = document.querySelector(".gauge-readout-digits");
        if (readout) readout.textContent = strength + "%";
    }

    function timeAgo(isoString) {
        if (!isoString) return "—";
        try {
            const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
            if (diff < 0)    return "just now";
            if (diff < 60)   return diff + "s ago";
            if (diff < 3600) return Math.floor(diff / 60) + "m ago";
            if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
            return Math.floor(diff / 86400) + "d ago";
        } catch (e) { return "—"; }
    }

    // ── Scanner Center V2 — Monitoring panel (additive, read-only) ──────────
    // Polls GET /api/v1/scanner/monitoring on its own cycle. A failure here
    // is fully isolated — it never touches refreshDashboardData()/state and
    // can never affect the scanner loop or any existing endpoint.
    window.toggleAccordion = function(bodyId) {
        const body = document.getElementById(bodyId);
        const caret = document.getElementById(bodyId + "-caret");
        if (!body) return;
        body.classList.toggle("collapsed");
        if (caret) caret.classList.toggle("collapsed");
    };

    function setFunnelStage(key, value, maxValue) {
        const countEl = document.getElementById("funnel-" + key);
        const barEl   = document.getElementById("funnel-bar-" + key);
        if (countEl) countEl.textContent = value || 0;
        if (barEl) {
            const pct = maxValue > 0 ? Math.min(100, (value / maxValue) * 100) : 0;
            barEl.style.width = pct + "%";
        }
    }

    function setCacheRow(kind, hits, misses) {
        const total = (hits || 0) + (misses || 0);
        const ratio = total > 0 ? Math.round((hits / total) * 100) : 0;
        const bar   = document.getElementById("cache-bar-" + kind);
        const label = document.getElementById("cache-ratio-" + kind);
        if (bar) bar.style.width = ratio + "%";
        if (label) label.textContent = ratio + "% (" + (hits || 0) + "/" + total + ")";
    }

    function renderEventLog(events) {
        const scroller = document.getElementById("event-log-scroller");
        if (!scroller) return;
        if (!events || events.length === 0) {
            scroller.innerHTML = '<div class="event-log-empty">No events yet.</div>';
            return;
        }
        // column-reverse container: render oldest-first in markup so newest visually stays at bottom
        const ordered = events.slice().reverse();
        scroller.innerHTML = ordered.map(ev => `
            <div class="event-log-row level-${escHtml(ev.level || 'info')}">
                <span class="event-log-time">${escHtml(ev.time || '')}</span>
                <span class="event-log-text">${escHtml(ev.text || '')}</span>
            </div>
        `).join("");
    }

    async function refreshMonitoringPanel() {
        const scannerView = document.getElementById("scanner-view");
        if (!scannerView || scannerView.classList.contains("view-hidden")) return;
        try {
            const resp = await authenticatedFetch("/api/v1/scanner/monitoring");
            if (!resp.ok) return;
            const data = await resp.json();

            // Signal funnel
            const funnel = data.funnel || {};
            const maxVal = funnel.coins_scanned || 0;
            setFunnelStage("coins_scanned", funnel.coins_scanned, maxVal);
            setFunnelStage("passed_volume", funnel.passed_volume, maxVal);
            setFunnelStage("passed_trend", funnel.passed_trend, maxVal);
            setFunnelStage("passed_score", funnel.passed_score, maxVal);
            setFunnelStage("signals_generated", funnel.signals_generated, maxVal);

            // API monitoring
            const api = data.api || {};
            const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
            setText("api-total-calls", api.total_calls || 0);
            setText("api-calls-per-min", api.calls_per_minute || 0);
            setText("api-success", api.success || 0);
            setText("api-429", api.errors_429 || 0);
            setText("api-retries", api.retries || 0);
            setText("api-fallbacks", api.fallbacks || 0);

            // Cache monitoring
            const cache = data.cache || {};
            setCacheRow("ticker", cache.ticker_hits, cache.ticker_misses);
            setCacheRow("candle", cache.candle_hits, cache.candle_misses);
            setCacheRow("watchlist", cache.watchlist_hits, cache.watchlist_misses);

            // Performance monitoring
            const cycles = data.cycles || {};
            setText("perf-avg-ms", (cycles.avg_ms || 0) + " ms");
            setText("perf-max-ms", (cycles.max_ms || 0) + " ms");
            setText("perf-min-ms", (cycles.min_ms || 0) + " ms");
            setText("perf-slowest-coin", cycles.slowest_coin ? (cycles.slowest_coin.coin + " (" + cycles.slowest_coin.ms + "ms)") : "—");
            setText("perf-fastest-coin", cycles.fastest_coin ? (cycles.fastest_coin.coin + " (" + cycles.fastest_coin.ms + "ms)") : "—");
            setText("perf-last-error", cycles.last_error ? cycles.last_error.message : "None");

            // Event log
            renderEventLog(data.event_log || []);
        } catch (err) {
            console.warn("[ProjectA] Monitoring panel refresh failed (isolated, scanner unaffected):", err.message);
        }
    }

    // Rebuild scanner-view signals table
    function patchSignalTable(signals) {
        const scannerView = document.getElementById("scanner-view");
        if (!scannerView) return;
        const tbody = scannerView.querySelector("table tbody");
        if (!tbody) return;

        if (!signals || signals.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;opacity:0.5;">No active signals</td></tr>';
            return;
        }

        tbody.innerHTML = signals.map(trace => `
            <tr>
                <td><strong>${escHtml(trace.coin || "")}</strong></td>
                <td>${escHtml(trace.category || "")}</td>
                <td>${escHtml(trace.score || 0)}</td>
                <td>${escHtml(trace.signal_price || 0)}</td>
                <td>${escHtml(trace.market || "INR")}</td>
                <td>${escHtml(trace.timestamp || "")}</td>
                <td>${escHtml(trace.market_state || "")}</td>
                <td style="color:var(--text-secondary);font-size:0.85em;white-space:nowrap;">${escHtml(timeAgo(trace.timestamp))}</td>
            </tr>
        `).join("");
    }

    // Rebuild home-view signals preview table (top 8)
    function patchHomeSignalTable(signals) {
        const tbody = document.getElementById("home-signals-tbody");
        if (!tbody) return;
        if (!signals || signals.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:20px;">No active signals</td></tr>';
            return;
        }
        const tierClass = cat => {
            if (cat === "ELITE")  return "tier-elite";
            if (cat === "HIGH")   return "tier-high";
            if (cat === "MEDIUM") return "tier-medium";
            return "tier-other";
        };
        tbody.innerHTML = signals.slice(0, 8).map(t => `
            <tr>
                <td><strong>${escHtml(t.coin || "")}</strong></td>
                <td><span class="${escHtml(tierClass(t.category))}">${escHtml(t.category || "—")}</span></td>
                <td class="font-mono">${escHtml(t.score || 0)}</td>
                <td class="font-mono text-sm">${escHtml(t.signal_price || 0)}</td>
                <td class="text-muted text-sm">${escHtml(timeAgo(t.timestamp))}</td>
            </tr>
        `).join("");
    }

    // Status dot helper for topbar live update
    function _dotClass(val) {
        if (val === "ONLINE") return "dot-online";
        if (val === "LIVE")   return "dot-live";
        if (val === "PAPER")  return "dot-paper";
        return "dot-offline";
    }
    function _lblClass(val) {
        if (val === "ONLINE") return "st-online";
        if (val === "LIVE")   return "st-live";
        if (val === "PAPER")  return "st-paper";
        return "st-offline";
    }

    function patchStatusBadge(dotId, labelId, value) {
        const dot = document.getElementById(dotId);
        const lbl = document.getElementById(labelId);
        if (dot) { dot.className = "status-dot " + _dotClass(value); }
        if (lbl) { lbl.className = _lblClass(value); lbl.textContent = value; }
    }

    // Patch bot status pill (id = the pill element itself)
    function patchBotPill(pillId, svcStatus, mode) {
        const pill = document.getElementById(pillId);
        if (!pill) return;
        let cls = "bot-status-pill ";
        if (svcStatus === "OFFLINE") cls += "offline";
        else if (mode === "LIVE")    cls += "live";
        else if (mode === "PAPER")   cls += "paper";
        else                         cls += "online";
        pill.className = cls;
        // Update text (keep dot span)
        const dot = pill.querySelector("span");
        pill.textContent = " " + mode;
        if (dot) pill.prepend(dot);
    }

    // ── V2 Home View Patch ───────────────────────────────────────────────
    function updateHomeV2(data) {
        // KPI — Total AUM
        const kpiAum = document.getElementById("kpi-aum");
        if (kpiAum) kpiAum.textContent = formatCurrency(data.portfolio_overview.total_value);
        const kpiDelta = document.getElementById("kpi-aum-delta");
        if (kpiDelta) {
            kpiDelta.textContent = formatCurrency(data.portfolio_overview.daily_pnl);
            const pnlNum = parseFloat(data.portfolio_overview.daily_pnl) || 0;
            kpiDelta.className = pnlNum >= 0 ? "text-green" : "text-red";
        }

        // KPI — Combined PnL
        const kpiPnl = document.getElementById("kpi-pnl");
        if (kpiPnl && data.vgx_overview && data.pmb_overview && data.mtb_overview) {
            const combo = (data.vgx_overview.daily_pnl || 0) +
                          (data.pmb_overview.daily_pnl  || 0) +
                          (data.mtb_overview.daily_pnl  || 0);
            kpiPnl.textContent = formatCurrency(combo);
            kpiPnl.className = "kpi-value font-mono " + (combo >= 0 ? "text-green" : "text-red");
        }

        // KPI — Win Rate
        const kpiWr = document.getElementById("kpi-winrate");
        if (kpiWr && data.performance_stats) {
            kpiWr.textContent = (data.performance_stats.win_rate_pct || 0) + "%";
        }

        // KPI — Open Positions
        const kpiPos = document.getElementById("kpi-openpos");
        if (kpiPos && data.vgx_overview && data.pmb_overview && data.mtb_overview) {
            const total = (data.vgx_overview.open_positions || []).length +
                          (data.pmb_overview.open_positions  || []).length +
                          (data.mtb_overview.open_positions  || []).length;
            kpiPos.textContent = total;
            // Risk panel open pos mirror
            const riskOp = document.getElementById("risk-openpos");
            if (riskOp) riskOp.textContent = total;
        }

        // KPI — Capital Deployed
        const kpiCap = document.getElementById("kpi-capital");
        if (kpiCap && data.risk_engine) {
            kpiCap.textContent = formatCurrency(data.risk_engine.total_deployed);
        }

        // KPI — System Health
        const kpiHealth = document.getElementById("kpi-health");
        if (kpiHealth && data.system_meta) {
            kpiHealth.textContent = (data.system_meta.overall_health_pct || 0) + "%";
        }

        // ── VGX Bot Card ─────────────────────────────────────────────────
        if (data.vgx_overview && data.service_statuses) {
            patchBotPill("vbc-vgx-status", data.service_statuses.vgx, data.vgx_overview.status || "—");

            const vBal = document.getElementById("vbc-vgx-balance");
            if (vBal) vBal.textContent = formatCurrency(data.vgx_overview.virtual_balance);

            const vPnl = document.getElementById("vbc-vgx-pnl");
            if (vPnl) {
                vPnl.textContent = formatCurrency(data.vgx_overview.daily_pnl);
                vPnl.className = "bot-metric-value " + ((data.vgx_overview.daily_pnl || 0) >= 0 ? "text-green" : "text-red");
            }

            const vWr = document.getElementById("vbc-vgx-winrate");
            if (vWr) vWr.textContent = (data.vgx_overview.win_rate || 0) + "%";

            const vgxPosCnt = (data.vgx_overview.open_positions || []).length;
            const vPos = document.getElementById("vbc-vgx-positions");
            if (vPos) vPos.textContent = vgxPosCnt + " / 5";

            const vProg = document.getElementById("vbc-vgx-progress");
            const vProgLbl = document.getElementById("vbc-vgx-prog-label");
            if (vProg) {
                const pct = Math.min(100, vgxPosCnt * 20);
                vProg.style.width = pct + "%";
                if (vProgLbl) vProgLbl.textContent = pct + "%";
            }
        }

        // ── PMB Bot Card ─────────────────────────────────────────────────
        if (data.pmb_overview && data.service_statuses) {
            patchBotPill("vbc-pmb-status", data.service_statuses.pmb, data.pmb_overview.mode || "—");

            const pCash = document.getElementById("vbc-pmb-cash");
            if (pCash) pCash.textContent = formatCurrency(data.pmb_overview.cash_balance);

            const pPnl = document.getElementById("vbc-pmb-pnl");
            if (pPnl) {
                pPnl.textContent = formatCurrency(data.pmb_overview.daily_pnl);
                pPnl.className = "bot-metric-value " + ((data.pmb_overview.daily_pnl || 0) >= 0 ? "text-green" : "text-red");
            }

            const pPos = document.getElementById("vbc-pmb-positions");
            if (pPos) pPos.textContent = (data.pmb_overview.open_positions || []).length;

            const pmbPosCnt = (data.pmb_overview.open_positions || []).length;
            const pProg = document.getElementById("vbc-pmb-progress");
            if (pProg) pProg.style.width = Math.min(100, pmbPosCnt * 25) + "%";
        }

        // ── MTB Bot Card ─────────────────────────────────────────────────
        if (data.mtb_overview && data.service_statuses) {
            patchBotPill("vbc-mtb-status", data.service_statuses.mtb, data.mtb_overview.mode || "—");

            const mCash = document.getElementById("vbc-mtb-cash");
            if (mCash) mCash.textContent = formatCurrency(data.mtb_overview.cash_balance);

            const mPnl = document.getElementById("vbc-mtb-pnl");
            if (mPnl) {
                mPnl.textContent = formatCurrency(data.mtb_overview.daily_pnl);
                mPnl.className = "bot-metric-value " + ((data.mtb_overview.daily_pnl || 0) >= 0 ? "text-green" : "text-red");
            }

            const mPos = document.getElementById("vbc-mtb-positions");
            if (mPos) mPos.textContent = (data.mtb_overview.open_positions || []).length;

            const mtbPosCnt = (data.mtb_overview.open_positions || []).length;
            const mProg = document.getElementById("vbc-mtb-progress");
            if (mProg) mProg.style.width = Math.min(100, mtbPosCnt * 25) + "%";
        }

        // ── Home Signals Preview ─────────────────────────────────────────
        patchHomeSignalTable(data.recent_signals || []);

        // ── Risk Panel ───────────────────────────────────────────────────
        if (data.risk_engine) {
            const rEstop = document.getElementById("risk-estop");
            if (rEstop) {
                rEstop.textContent = data.risk_engine.emergency_stop ? "ACTIVE" : "INACTIVE";
                rEstop.className = "risk-item-value " + (data.risk_engine.emergency_stop ? "text-red" : "text-green");
            }
            const rDep = document.getElementById("risk-deployed");
            if (rDep) rDep.textContent = formatCurrency(data.risk_engine.total_deployed);
            const rUtil = document.getElementById("risk-util");
            if (rUtil) {
                const util = data.risk_engine.capital_utilisation_pct || 0;
                rUtil.textContent = util + "%";
                rUtil.className = "risk-item-value font-mono " + (util > 80 ? "text-red" : util > 50 ? "text-gold" : "text-green");
            }
            const rTrading = document.getElementById("risk-trading");
            if (rTrading) {
                rTrading.textContent = data.risk_engine.trading_enabled ? "ENABLED" : "HALTED";
                rTrading.className = "risk-item-value " + (data.risk_engine.trading_enabled ? "text-green" : "text-red");
            }
            const rBadge = document.getElementById("risk-trading-badge");
            if (rBadge) {
                rBadge.textContent = data.risk_engine.trading_enabled ? "TRADING ENABLED" : "TRADING HALTED";
                rBadge.className = "risk-panel-badge" + (data.risk_engine.trading_enabled ? "" : " danger");
            }
            const rUpdated = document.getElementById("risk-updated");
            if (rUpdated) rUpdated.textContent = data.risk_engine.last_updated || "—";
        }

        // ── Topbar status badges live update ──────────────────────────────
        if (data.service_statuses) {
            patchStatusBadge("dot-scanner", "sb-scanner", data.service_statuses.scanner || "OFFLINE");
            patchStatusBadge("dot-vgx", "sb-vgx", data.service_statuses.vgx || "OFFLINE");
            patchStatusBadge("dot-pmb", "sb-pmb", data.service_statuses.pmb || "OFFLINE");
            patchStatusBadge("dot-mtb", "sb-mtb", data.service_statuses.mtb || "OFFLINE");

            // Telegram: ONLINE if any relay is up
            const tgUp = [
                data.service_statuses.scanner_telegram,
                data.service_statuses.vgx_telegram,
                data.service_statuses.pmb_telegram,
                data.service_statuses.mtb_telegram,
            ].some(s => s === "ONLINE");
            patchStatusBadge("dot-telegram", "sb-telegram", tgUp ? "ONLINE" : "OFFLINE");
        }
        if (data.railway_monitoring) {
            patchStatusBadge("dot-railway", "sb-railway", data.railway_monitoring.status || "OFFLINE");
        }
    }

    // ── Portfolio View updater ────────────────────────────────────────────
    function updatePortfolioView(data) {
        const po = data.portfolio_overview || {};

        const pTotalVal = document.getElementById("port-total-value");
        if (pTotalVal) pTotalVal.textContent = formatCurrency(po.total_value);

        const pCash = document.getElementById("port-available-cash");
        if (pCash) pCash.textContent = formatCurrency(po.available_cash);

        const pInvested = document.getElementById("port-invested-amount");
        if (pInvested) pInvested.textContent = formatCurrency(po.invested_amount);

        const pTotalPnl = document.getElementById("port-total-pnl");
        if (pTotalPnl) {
            pTotalPnl.textContent = formatCurrency(po.total_pnl);
            pTotalPnl.className = (po.total_pnl || 0) >= 0 ? "text-green" : "text-red";
        }

        const pDailyPnl = document.getElementById("port-daily-pnl");
        if (pDailyPnl) {
            pDailyPnl.textContent = formatCurrency(po.daily_pnl);
            pDailyPnl.className = (po.daily_pnl || 0) >= 0 ? "text-green" : "text-red";
        }

        const pOpenPos = document.getElementById("port-open-positions");
        if (pOpenPos) pOpenPos.textContent = po.open_positions || 0;

        // ── Open Positions table ──────────────────────────────────────────
        const tbody = document.getElementById("open-positions-tbody");
        if (tbody && Array.isArray(data.open_positions)) {
            if (data.open_positions.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:20px;">No open positions</td></tr>';
            } else {
                tbody.innerHTML = data.open_positions.map(pos => {
                    const pnlClass = (pos.pnl_pct || 0) >= 0 ? "text-green" : "text-red";
                    return `<tr>
                        <td><span class="row-badge-pill signal-type-elite">${escHtml(pos.bot || "")}</span></td>
                        <td><strong>${escHtml(pos.coin || "")}/INR</strong></td>
                        <td class="font-mono">${escHtml(pos.quantity || 0)}</td>
                        <td class="font-mono">${formatCurrency(pos.buy_price)}</td>
                        <td><span class="${pnlClass} font-600">${escHtml(pos.pnl_pct || 0)}%</span></td>
                        <td><span class="row-badge-pill signal-type-elite">${escHtml(pos.status || "OPEN")}</span></td>
                    </tr>`;
                }).join("");
            }
        }
    }

    // ── Last Updated timestamp (browser-side IST clock) ────────────────
    // Frontend-only — uses the browser's own clock, converted to IST via
    // Intl, and refreshed every time refreshDashboardData() runs. No
    // backend/API data is involved; this is purely a "did my browser just
    // successfully poll the server" freshness indicator.
    function updateLastUpdatedTimestamp() {
        try {
            const el = document.getElementById("last-updated-label");
            if (!el) return;
            const now = new Date();
            const timeStr = now.toLocaleTimeString("en-GB", {
                timeZone: "Asia/Kolkata",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
            });
            el.textContent = "Last updated: " + timeStr + " IST";
        } catch (e) {
            const el = document.getElementById("last-updated-label");
            if (el) el.textContent = "Last updated: —";
        }
    }

    // ── Capital Allocation % widget ─────────────────────────────────────
    // Frontend-only calculation — no backend/API changes. Reads three
    // existing balance fields already present in the /api/v1/state payload:
    //   vgx_overview.virtual_balance, pmb_overview.cash_balance, mtb_overview.cash_balance
    // percentage(bot) = (bot_value / total_capital) * 100
    function renderCapitalAllocation(data) {
        const widget = document.getElementById("capital-allocation-widget");
        if (!widget) return;

        const vgx = Number((data.vgx_overview && data.vgx_overview.virtual_balance) || 0);
        const pmb = Number((data.pmb_overview && data.pmb_overview.cash_balance) || 0);
        const mtb = Number((data.mtb_overview && data.mtb_overview.cash_balance) || 0);
        const total = vgx + pmb + mtb;

        const content = document.getElementById("capital-allocation-content");
        const empty   = document.getElementById("capital-allocation-empty");

        if (!total || total <= 0) {
            if (content) content.style.display = "none";
            if (empty) empty.style.display = "block";
            return;
        }

        if (content) content.style.display = "flex";
        if (empty) empty.style.display = "none";

        const pct = (v) => (v / total) * 100;
        const setAlloc = (key, value) => {
            const bar = document.getElementById("alloc-bar-" + key);
            const label = document.getElementById("alloc-pct-" + key);
            const pctStr = value.toFixed(2) + "%";
            if (bar) bar.style.width = pctStr;
            if (label) label.textContent = pctStr;
        };

        setAlloc("vgx", pct(vgx));
        setAlloc("pmb", pct(pmb));
        setAlloc("mtb", pct(mtb));
    }

    async function refreshDashboardData() {
        try {
            const response = await authenticatedFetch("/api/v1/state");
            if (!response.ok) return;
            const data = await response.json();

            // ── Scanner stat cards ──────────────────────────────────────
            const marketCount = document.getElementById("market-assets-count");
            if (marketCount) marketCount.textContent = data.scanner_overview.coins_scanned || 0;

            const eliteNode  = document.getElementById("so-elite-signals")  || findCardValueNode("Elite Signals");
            const highNode   = document.getElementById("so-high-signals")   || findCardValueNode("High Signals");
            const mediumNode = document.getElementById("so-medium-signals") || findCardValueNode("Medium Signals");
            if (eliteNode)  eliteNode.textContent  = data.scanner_overview.elite_signals  || 0;
            if (highNode)   highNode.textContent   = data.scanner_overview.high_signals   || 0;
            if (mediumNode) mediumNode.textContent = data.scanner_overview.medium_signals || 0;

            // ── I-04: Cleanup engine cards ──────────────────────────────
            const activeNode   = document.getElementById("so-active-signals");
            const expiredNode  = document.getElementById("so-expired-signals");
            const lastCleanEl  = document.getElementById("so-last-cleanup");
            if (activeNode)  activeNode.textContent  = data.scanner_overview.active_signals  || 0;
            if (expiredNode) expiredNode.textContent = data.scanner_overview.expired_signals || 0;
            if (lastCleanEl) {
                const lct = data.scanner_overview.last_cleanup_time;
                lastCleanEl.textContent = lct
                    ? (() => { try { return _relativeTime(new Date(lct)); } catch(e) { return "—"; } })()
                    : "Pending";
            }
            if (data.scanner_overview.next_cleanup_time) {
                window._nextCleanupTs = new Date(data.scanner_overview.next_cleanup_time).getTime();
            }

            // ── Health monitor cards ─────────────────────────────────────
            if (data.scanner_overview) {
                const apiStatus = document.getElementById("sh-api-status");
                if (apiStatus) {
                    apiStatus.textContent = data.scanner_overview.api_status || "ONLINE";
                    apiStatus.className = "text-" + (data.scanner_overview.health_color || "green");
                }
                const hScore = document.getElementById("sh-health-score");
                if (hScore) {
                    hScore.textContent = (data.scanner_overview.health_score || 100) + "%";
                    hScore.className = "text-" + (data.scanner_overview.health_color || "green");
                }
                const tScan = document.getElementById("sh-total-scans");
                if (tScan) tScan.textContent = data.scanner_overview.total_scans || 0;
                const fScan = document.getElementById("sh-failed-scans");
                if (fScan) fScan.textContent = data.scanner_overview.failed_scans || 0;
                const cFail = document.getElementById("sh-consecutive-failures");
                if (cFail) cFail.textContent = data.scanner_overview.consecutive_failures || 0;
                const sDur = document.getElementById("sh-scan-duration");
                if (sDur) sDur.textContent = (data.scanner_overview.scan_duration_ms || 0) + " ms";
                const mStat = document.getElementById("sh-market-status");
                if (mStat) mStat.textContent = data.scanner_overview.current_market_status || "ACTIVE";
                const lScan = document.getElementById("sh-last-scan");
                if (lScan && data.scanner_overview.last_successful_scan) {
                    lScan.textContent = timeAgo(data.scanner_overview.last_successful_scan);
                }
                const rTime = document.getElementById("sh-restart-time");
                if (rTime) rTime.textContent = data.scanner_overview.last_restart_time || "—";
                const rSig = document.getElementById("sh-recovered-signals");
                if (rSig) rSig.textContent = data.scanner_overview.recovered_signals || 0;
                const rStat = document.getElementById("sh-recovery-status");
                if (rStat) {
                    rStat.textContent = data.scanner_overview.recovery_status || "SUCCESS";
                    rStat.className = (data.scanner_overview.recovery_status === "SUCCESS" ? "text-green" : "text-red");
                }
            }

            // ── Alerts badge ────────────────────────────────────────────
            const alertBadge = document.querySelector(".alert-counter-badge");
            if (alertBadge) alertBadge.textContent = (data.notifications || []).length;

            // ── Charts ──────────────────────────────────────────────────
            if (data.charts && data.charts.distribution) {
                patchChart(runtimeChartHandles.homePie, data.charts.distribution.labels, data.charts.distribution.data);
            }
            if (data.market_state) {
                patchGauge(runtimeChartHandles.homeGauge, data.market_state.market_strength || 0);
            }
            if (data.charts && data.charts.daily_signals) {
                patchChart(runtimeChartHandles.homeLine, data.charts.daily_signals.labels, data.charts.daily_signals.data);
            }
            if (data.charts && data.charts.asset_allocation) {
                patchChart(runtimeChartHandles.portfolioPie, data.charts.asset_allocation.labels, data.charts.asset_allocation.data);
            }
            if (data.charts && data.charts.portfolio_growth) {
                patchChart(runtimeChartHandles.portfolioLine, data.charts.portfolio_growth.labels, data.charts.portfolio_growth.data);
            }
            if (data.vgx_overview && data.vgx_overview.equity_curve) {
                patchChart(runtimeChartHandles.vgxEquity, data.vgx_overview.equity_curve.labels, data.vgx_overview.equity_curve.data);
            }
            if (data.vgx_overview && data.vgx_overview.win_loss_chart) {
                patchChart(runtimeChartHandles.vgxWinLoss, data.vgx_overview.win_loss_chart.labels, data.vgx_overview.win_loss_chart.data);
            }

            // ── VGX stat cards (vgx-view) ───────────────────────────────
            const vgxBalNode = findCardValueNode("Virtual Balance");
            if (vgxBalNode && data.vgx_overview) {
                vgxBalNode.textContent = formatCurrency(data.vgx_overview.virtual_balance);
            }
            const vgxWrNode = findCardValueNode("Win Rate");
            if (vgxWrNode && data.vgx_overview) {
                vgxWrNode.textContent = data.vgx_overview.win_rate + "%";
            }

            // ── Scanner-view signals table ──────────────────────────────
            patchSignalTable(data.recent_signals || []);

            // ── Watchlist count ─────────────────────────────────────────
            if (data.scanner_overview) {
                const scannerWlCount = document.getElementById("scanner-watchlist-count");
                if (scannerWlCount) scannerWlCount.textContent = data.scanner_overview.coins_scanned || 0;
            }

            // ── Performance Tracker cards ───────────────────────────────
            if (data.performance_stats) {
                const ps = data.performance_stats;
                const totalNode = findCardValueNode("Total Signals");
                if (totalNode) totalNode.textContent = ps.total_signals || 0;
                const winNode = findCardValueNode("Winning Signals");
                if (winNode) winNode.textContent = ps.winning_signals || 0;
                const lossNode = findCardValueNode("Losing Signals");
                if (lossNode) lossNode.textContent = ps.losing_signals || 0;
                const wrNode = findCardValueNode("Win Rate %");
                if (wrNode) {
                    wrNode.textContent = (ps.win_rate_pct || 0) + "%";
                    wrNode.className = (ps.win_rate_pct >= 50 ? "text-green" : "text-red");
                }
                const avgNode = findCardValueNode("Average Return %");
                if (avgNode) {
                    avgNode.textContent = (ps.avg_return_pct || 0) + "%";
                    avgNode.className = (ps.avg_return_pct >= 0 ? "text-green" : "text-red");
                }
                const bestNode = findCardValueNode("Best Signal");
                if (bestNode && ps.best_signal) {
                    bestNode.textContent = (ps.best_signal.coin || "—") + " " + (ps.best_signal.return_pct || 0) + "%";
                }
                const worstNode = findCardValueNode("Worst Signal");
                if (worstNode && ps.worst_signal) {
                    worstNode.textContent = (ps.worst_signal.coin || "—") + " " + (ps.worst_signal.return_pct || 0) + "%";
                }
            }

            // ── Performance Tracker table ───────────────────────────────
            if (data.performance_signals) {
                const tbody = document.getElementById("performance-table-body");
                if (tbody) {
                    const sigs = data.performance_signals;
                    if (!sigs || sigs.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="11" class="text-muted">No evaluated signals yet — signals will appear as 1H/4H/24H/3D/7D horizons mature.</td></tr>';
                    } else {
                        tbody.innerHTML = sigs.map(s => {
                            const color = pct => pct >= 0 ? 'text-green' : 'text-red';
                            const badge = s.result === 'WIN' ? 'signal-type-elite' : 'color-indicator-error';
                            return `<tr>
                                <td><strong>${escHtml(s.coin || "")}</strong></td>
                                <td class="text-muted text-sm">${escHtml(s.timestamp || "")}</td>
                                <td>${escHtml(s.signal_price || 0)}</td>
                                <td>${escHtml(s.current_price || 0)}</td>
                                <td class="${color(s['1h_pct'])}">${escHtml(s['1h_pct'])}%</td>
                                <td class="${color(s['4h_pct'])}">${escHtml(s['4h_pct'])}%</td>
                                <td class="${color(s['24h_pct'])}">${escHtml(s['24h_pct'])}%</td>
                                <td class="${color(s['3d_pct'])}">${escHtml(s['3d_pct'])}%</td>
                                <td class="${color(s['7d_pct'])}">${escHtml(s['7d_pct'])}%</td>
                                <td class="${color(s.return_pct)}">${escHtml(s.return_pct)}%</td>
                                <td><span class="row-badge-pill ${badge}">${escHtml(s.result)}</span></td>
                            </tr>`;
                        }).join("");
                    }
                }
            }

            // ── Signal History cards ─────────────────────────────────────
            if (data.signal_history_stats) {
                const hs = data.signal_history_stats;
                const totalNode = findCardValueNode("History Signals");
                if (totalNode) totalNode.textContent = hs.total || 0;
                const winNode = findCardValueNode("Winners");
                if (winNode) winNode.textContent = hs.winners || 0;
                const lossNode = findCardValueNode("Losers");
                if (lossNode) lossNode.textContent = hs.losers || 0;
                // Win Rate % — must be patched separately from Avg Return %
                const wrNode = findCardValueNode("Win Rate %");
                if (wrNode) {
                    wrNode.textContent = (hs.win_rate_pct || 0) + "%";
                    wrNode.className = (hs.win_rate_pct >= 50 ? "text-green" : "text-red");
                }
                const avgNode = findCardValueNode("Avg Return %");
                if (avgNode) {
                    avgNode.textContent = (hs.avg_return_pct || 0) + "%";
                    avgNode.className = (hs.avg_return_pct >= 0 ? "text-green" : "text-red");
                }
                const recNode = findCardValueNode("Records");
                if (recNode) recNode.textContent = hs.total || 0;
                const countLabel = document.getElementById("sh-count-label");
                if (countLabel && data.signal_history) countLabel.textContent = (data.signal_history.length || 0) + " records";
            }

            // ── Signal History table ─────────────────────────────────────
            if (data.signal_history) {
                const tbody = document.getElementById("signal-history-table-body");
                if (tbody) {
                    const all = data.signal_history || [];
                    let filtered = all;
                    const filterVal = document.getElementById("sh-filter")?.value || "ALL";
                    const searchVal = (document.getElementById("sh-search")?.value || "").trim().toUpperCase();
                    if (filterVal !== "ALL") filtered = filtered.filter(s => s.result === filterVal);
                    if (searchVal) filtered = filtered.filter(s => (s.coin || "").toUpperCase().includes(searchVal));
                    const sortVal = document.getElementById("sh-sort")?.value || "newest";
                    if (sortVal === "newest")  filtered.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
                    else if (sortVal === "oldest") filtered.sort((a, b) => (a.timestamp || "").localeCompare(b.timestamp || ""));
                    else if (sortVal === "return") filtered.sort((a, b) => (b.return_pct || 0) - (a.return_pct || 0));
                    else if (sortVal === "worst")  filtered.sort((a, b) => (a.return_pct || 0) - (b.return_pct || 0));

                    if (filtered.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="12" class="text-muted">No history matches the current filter.</td></tr>';
                    } else {
                        tbody.innerHTML = filtered.map(s => {
                            const color = pct => pct >= 0 ? 'text-green' : 'text-red';
                            const badge = s.result === 'WIN' ? 'signal-type-elite' : 'color-indicator-error';
                            return `<tr>
                                <td><strong>${escHtml(s.coin || "")}</strong></td>
                                <td class="text-muted text-sm">${escHtml(s.timestamp || "")}</td>
                                <td>${escHtml(s.score || 0)}</td>
                                <td>${escHtml(s.tier || "")}</td>
                                <td>${escHtml(s.signal_price || 0)}</td>
                                <td class="${color(s['1h_pct'])}">${escHtml(s['1h_pct'])}%</td>
                                <td class="${color(s['4h_pct'])}">${escHtml(s['4h_pct'])}%</td>
                                <td class="${color(s['24h_pct'])}">${escHtml(s['24h_pct'])}%</td>
                                <td class="${color(s['3d_pct'])}">${escHtml(s['3d_pct'])}%</td>
                                <td class="${color(s['7d_pct'])}">${escHtml(s['7d_pct'])}%</td>
                                <td class="${color(s.return_pct)}">${escHtml(s.return_pct)}%</td>
                                <td><span class="row-badge-pill ${badge}">${escHtml(s.result)}</span></td>
                            </tr>`;
                        }).join("");
                    }
                }
            }

            // ── Coin Performance cards ────────────────────────────────────
            if (data.coin_performance_stats) {
                const cps = data.coin_performance_stats;
                const ctNode = findCardValueNode("Coins Tracked");
                if (ctNode) ctNode.textContent = cps.coins_tracked || 0;
                const tsNode = findCardValueNode("Total Signals");
                if (tsNode) tsNode.textContent = cps.total_signals || 0;
                const winNode = findCardValueNode("Winning Signals");
                if (winNode) winNode.textContent = cps.winning_signals || 0;
                const lossNode = findCardValueNode("Losing Signals");
                if (lossNode) lossNode.textContent = cps.losing_signals || 0;
                // Win Rate % and Records must stay live in coin-performance view
                const cpWrNode = findCardValueNode("Win Rate %");
                if (cpWrNode) {
                    cpWrNode.textContent = (cps.win_rate_pct || 0) + "%";
                    cpWrNode.className = (cps.win_rate_pct >= 50 ? "text-green" : "text-red");
                }
                const cpRecNode = findCardValueNode("Records");
                if (cpRecNode) cpRecNode.textContent = cps.coins_tracked || 0;
                const countLabel = document.getElementById("cp-count-label");
                if (countLabel && data.coin_performance_data) countLabel.textContent = (data.coin_performance_data.length || 0) + " coins";
            }

            // ── Coin Performance table ────────────────────────────────────
            if (data.coin_performance_data) {
                const tbody = document.getElementById("coin-performance-table-body");
                if (tbody) {
                    let rows = data.coin_performance_data || [];
                    const searchVal = (document.getElementById("cp-search")?.value || "").trim().toUpperCase();
                    if (searchVal) rows = rows.filter(r => (r.coin || "").toUpperCase().includes(searchVal));
                    const sortVal = document.getElementById("cp-sort")?.value || "winrate";
                    if (sortVal === "winrate") rows.sort((a, b) => (b.win_rate_pct || 0) - (a.win_rate_pct || 0));
                    else if (sortVal === "signals") rows.sort((a, b) => (b.total_signals || 0) - (a.total_signals || 0));
                    else if (sortVal === "best")    rows.sort((a, b) => (b.best_return_pct || 0) - (a.best_return_pct || 0));
                    else if (sortVal === "worst")   rows.sort((a, b) => (a.worst_return_pct || 0) - (b.worst_return_pct || 0));

                    if (rows.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="9" class="text-muted">No coin data matches the current filter.</td></tr>';
                    } else {
                        tbody.innerHTML = rows.map(r => {
                            const color = pct => pct >= 0 ? 'text-green' : 'text-red';
                            return `<tr>
                                <td><strong>${escHtml(r.coin || "")}</strong></td>
                                <td>${escHtml(r.total_signals || 0)}</td>
                                <td class="text-green">${escHtml(r.winning_signals || 0)}</td>
                                <td class="text-red">${escHtml(r.losing_signals || 0)}</td>
                                <td class="${color(r.win_rate_pct)}">${escHtml(r.win_rate_pct)}%</td>
                                <td class="${color(r.avg_return_pct)}">${escHtml(r.avg_return_pct)}%</td>
                                <td class="text-green">${escHtml(r.best_return_pct)}%</td>
                                <td class="text-red">${escHtml(r.worst_return_pct)}%</td>
                                <td class="text-muted text-sm">${escHtml(r.last_signal_time || "—")}</td>
                            </tr>`;
                        }).join("");
                    }
                }
            }

            // ── V2 Home View patches ──────────────────────────────────────
            updateHomeV2(data);

            // ── Portfolio View patches ────────────────────────────────────
            updatePortfolioView(data);

            // ── Capital Allocation % widget ────────────────────────────────
            renderCapitalAllocation(data);

            // ── Last Updated timestamp ──────────────────────────────────────
            updateLastUpdatedTimestamp();

            // ── Live current price patch — trade history tables ───────────
            // Calls /api/v1/prices which reads only the scanner's in-memory
            // ticker cache — zero CoinDCX API calls.
            try {
                var _pr = await authenticatedFetch("/api/v1/prices");
                if (_pr.ok) {
                    var _pd     = await _pr.json();
                    var _prices = _pd.prices || {};
                    document.querySelectorAll("[data-cur-price]").forEach(function(_cell) {
                        var _row   = _cell.closest("tr");
                        if (!_row) return;
                        var _coin  = (_row.dataset.coin || "").toUpperCase();
                        if (!_coin) return;
                        var _price = _prices[_coin];
                        _cell.textContent = (_price != null)
                            ? parseFloat(_price).toFixed(4)
                            : "\u2014";
                    });
                }
            } catch (_e) { /* silent — never disrupt dashboard refresh */ }

        } catch (err) {
            console.warn("[ProjectA] Dashboard refresh failed:", err.message);
        }
    }

    // Auto-refresh with configurable interval
    function getRefreshIntervalMs() {
        const raw = localStorage.getItem("pa-refresh-interval");
        const ms = raw ? parseInt(raw, 10) : 10000;
        return (ms > 0) ? ms : 0;
    }
    // Show an immediate timestamp on load rather than leaving the label at
    // its static "—" placeholder until the first refresh cycle fires.
    updateLastUpdatedTimestamp();

    // Use a mutable variable so subsequent settings changes clear the correct timer
    let activeIntervalId = setInterval(refreshDashboardData, getRefreshIntervalMs());
    if (refreshSelect) {
        refreshSelect.addEventListener("change", () => {
            const newMs = parseInt(refreshSelect.value, 10);
            if (newMs > 0) {
                clearInterval(activeIntervalId);
                activeIntervalId = setInterval(refreshDashboardData, newMs);
                localStorage.setItem("pa-refresh-interval", newMs);
            }
        });
    }

    // Scanner Center V2 monitoring panel — independent polling loop.
    // Deliberately decoupled from refreshDashboardData()/state so a slow or
    // failing monitoring endpoint can never delay or break the main dashboard.
    refreshMonitoringPanel();
    let monitoringIntervalId = setInterval(refreshMonitoringPanel, getRefreshIntervalMs() || 10000);
    if (refreshSelect) {
        refreshSelect.addEventListener("change", () => {
            const newMs = parseInt(refreshSelect.value, 10);
            if (newMs > 0) {
                clearInterval(monitoringIntervalId);
                monitoringIntervalId = setInterval(refreshMonitoringPanel, newMs);
            }
        });
    }

    // ── Signal History interactive filter / sort / search ──────────────────
    const shSearch = document.getElementById("sh-search");
    const shFilter = document.getElementById("sh-filter");
    const shSort   = document.getElementById("sh-sort");
    function attachHistoryListeners() {
        if (!shSearch || !shFilter || !shSort) return;
        const rerender = () => refreshDashboardData();
        shSearch.addEventListener("input", rerender);
        shFilter.addEventListener("change", rerender);
        shSort.addEventListener("change", rerender);
    }
    attachHistoryListeners();

    // ── Coin Performance interactive search / sort ──────────────────────────
    const cpSearch = document.getElementById("cp-search");
    const cpSort   = document.getElementById("cp-sort");
    function attachCoinPerformanceListeners() {
        if (!cpSearch || !cpSort) return;
        const rerender = () => refreshDashboardData();
        cpSearch.addEventListener("input", rerender);
        cpSort.addEventListener("change", rerender);
    }
    attachCoinPerformanceListeners();

    // ── Watchlist Center — Add / Remove Coins ──────────────────────────────

    window.refreshScanner = async function() {
        const msg = document.getElementById("scanner-refresh-msg");
        if (msg) { msg.style.display = "none"; msg.textContent = ""; msg.style.color = "#f43f5e"; }
        try {
            const resp = await authenticatedFetch("/api/scanner/refresh", { method: "POST" });
            const data = await resp.json();
            if (data.success) {
                if (msg) {
                    msg.textContent = "Scanner Updated Successfully";
                    msg.style.color = "#00d4a0";
                    msg.style.display = "block";
                    setTimeout(() => { msg.style.display = "none"; }, 3000);
                }
                refreshDashboardData();
            } else {
                if (msg) {
                    msg.textContent = "Scanner Refresh Failed";
                    msg.style.display = "block";
                    setTimeout(() => { msg.style.display = "none"; }, 5000);
                }
            }
        } catch (e) {
            if (msg) {
                msg.textContent = "Scanner Refresh Failed";
                msg.style.display = "block";
                setTimeout(() => { msg.style.display = "none"; }, 5000);
            }
        }
    };

    window.addCoin = async function() {
        const input = document.getElementById("scanner-add-input");
        const errorDiv = document.getElementById("scanner-add-error");
        const coin = input.value.trim().toUpperCase();

        if (errorDiv) { errorDiv.style.display = "none"; errorDiv.textContent = ""; }
        if (!coin) {
            if (errorDiv) { errorDiv.textContent = "Enter a coin symbol"; errorDiv.style.display = "block"; }
            return;
        }

        try {
            const resp = await authenticatedFetch("/api/watchlist/add", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({coin}),
            });
            const data = await resp.json();
            if (data.success) {
                input.value = "";
                if (errorDiv) { errorDiv.style.display = "none"; errorDiv.textContent = ""; }
                const preview = document.getElementById("scanner-pair-preview");
                if (preview) { preview.style.display = "none"; preview.textContent = ""; }
                refreshWatchlistTable();
            } else {
                const reasonMap = {
                    "no_pair_found": "Coin not available on CoinDCX (no INR or USDT pair found)",
                    "invalid_symbol": "Invalid coin symbol",
                };
                const msg = data.error || reasonMap[data.reason] || "Add failed";
                if (errorDiv) {
                    errorDiv.textContent = msg;
                    errorDiv.style.display = "block";
                    setTimeout(() => { errorDiv.style.display = "none"; errorDiv.textContent = ""; }, 5000);
                }
            }
        } catch (e) {
            if (errorDiv) {
                errorDiv.textContent = "Network error — please try again";
                errorDiv.style.display = "block";
                setTimeout(() => { errorDiv.style.display = "none"; }, 5000);
            }
        }
    };

    window.removeCoin = async function(coin) {
        if (!confirm("Remove " + coin + " from Scanner Watchlist?")) return;
        try {
            const resp = await authenticatedFetch("/api/watchlist/remove", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({coin}),
            });
            const data = await resp.json();
            if (data.success) {
                refreshWatchlistTable();
            } else {
                alert("Remove failed: " + (data.error || "unknown"));
            }
        } catch (e) {
            alert("Network error: " + e.message);
        }
    };

    // ── Toast notifications ────────────────────────────────────────────────────
    (function ensureToastContainer() {
        if (!document.getElementById("toast-container")) {
            const el = document.createElement("div");
            el.id = "toast-container";
            el.className = "toast-container";
            document.body.appendChild(el);
        }
    })();

    window.showToast = function(message, type /* "success"|"warning"|"error" */) {
        const container = document.getElementById("toast-container");
        if (!container) return;
        const toast = document.createElement("div");
        toast.className = "toast toast-" + (type || "success");
        toast.textContent = message;
        container.appendChild(toast);
        // Trigger animation on next frame
        requestAnimationFrame(() => {
            requestAnimationFrame(() => toast.classList.add("toast-show"));
        });
        setTimeout(() => {
            toast.classList.remove("toast-show");
            toast.addEventListener("transitionend", () => toast.remove(), {once: true});
        }, 3000);
    };

    // ── Trading toggle ─────────────────────────────────────────────────────────
    window.toggleTrading = async function(checkboxEl) {
        const checkbox = checkboxEl || document.getElementById("trading-toggle-input");
        if (!checkbox) return;
        const nextEnabled = checkbox.checked;

        checkbox.disabled = true;
        try {
            const resp = await authenticatedFetch("/api/v1/trading/toggle", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({enabled: nextEnabled}),
            });
            const data = await resp.json();
            if (data.status === "ok") {
                applyTradingToggleState(data.trading_enabled);
                showToast(data.trading_enabled ? "Trading ENABLED" : "Trading DISABLED",
                          data.trading_enabled ? "success" : "warning");
                refreshDashboardData();
            } else if (data.status === "rejected") {
                checkbox.checked = !nextEnabled;   // revert
                showToast("Cannot enable — EMERGENCY_STOP is active", "error");
            } else {
                checkbox.checked = !nextEnabled;
                showToast("Toggle failed: " + (data.detail || data.error || "unknown error"), "error");
            }
        } catch (e) {
            checkbox.checked = !nextEnabled;
            showToast("Network error: " + e.message, "error");
        } finally {
            checkbox.disabled = false;
        }
    };

    // Sync toggle state from server on page load
    (async function initTradingToggle() {
        try {
            const resp = await authenticatedFetch("/api/v1/trading/status");
            if (resp && resp.ok) {
                const data = await resp.json();
                applyTradingToggleState(data.trading_enabled);
            }
        } catch (_) {
            // silent — server-rendered state is already shown
        }
    })();

    function applyTradingToggleState(enabled) {
        const checkbox = document.getElementById("trading-toggle-input");
        const label    = document.getElementById("trading-toggle-label");
        const badge    = document.getElementById("risk-trading-badge");
        const statusEl = document.getElementById("risk-trading");
        if (checkbox) checkbox.checked = enabled;
        if (label) {
            label.innerHTML = enabled
                ? '<span class="text-green">Trading Enabled</span>'
                : '<span class="text-red">Trading Disabled</span>';
        }
        if (badge) {
            badge.textContent = enabled ? "TRADING ENABLED" : "TRADING HALTED";
            badge.classList.toggle("danger", !enabled);
        }
        if (statusEl) {
            statusEl.textContent = enabled ? "ENABLED" : "HALTED";
            statusEl.classList.toggle("text-green", enabled);
            statusEl.classList.toggle("text-red", !enabled);
        }
    }

    document.addEventListener("click", function(e) {
        const btn = e.target.closest(".btn-remove-coin");
        if (!btn) return;
        const coin = btn.dataset.coin;
        if (coin) removeCoin(coin);
    });

    async function refreshWatchlistTable() {
        try {
            const resp = await authenticatedFetch("/api/watchlist");
            const data = await resp.json();
            const items = data.items || (data.coins || []).map(c => ({coin: c, pair: null, quote: null}));
            const countNode = document.getElementById("scanner-coin-count");
            if (countNode) countNode.textContent = items.length;
            const tbody = document.getElementById("scanner-coin-table");
            if (!tbody) return;
            tbody.innerHTML = items.length === 0
                ? '<tr><td colspan="2" class="text-muted">No coins</td></tr>'
                : items.map(item => {
                    const display = escHtml(item.coin) + '/' + escHtml(item.quote || 'INR');
                    return '<tr><td><strong>' + display + '</strong></td><td><button class="btn-remove-coin" data-coin="' + escHtml(item.coin) + '">REMOVE</button></td></tr>';
                  }).join("");
            const totalNode = document.getElementById("total-coin-count");
            if (totalNode) totalNode.textContent = items.length;
        } catch (err) {
            console.warn("[ProjectA] Watchlist refresh failed:", err.message);
        }
    }

    // ── Pair Preview (real-time resolution while typing) ───────────────────
    (function() {
        var CACHE_MAX      = 20;
        var previewCache   = new Map();   // coin → API result (last 20)
        var debounceTimer  = null;
        var inflightCoin   = "";          // used to discard stale responses

        var STATE_COLORS = {
            green: "#22c55e",
            amber: "#f59e0b",
            red:   "#ef4444",
            gray:  "#888888"
        };

        function showPreview(state, text) {
            var el = document.getElementById("scanner-pair-preview");
            if (!el) return;
            if (!text) { el.style.display = "none"; el.textContent = ""; return; }
            el.style.color   = STATE_COLORS[state] || STATE_COLORS.gray;
            el.textContent   = text;
            el.style.display = "block";
        }

        function renderResult(data) {
            if (!data) { showPreview("gray", ""); return; }
            if (data.resolved) {
                var quote = data.quote || "INR";
                var state = (quote === "USDT") ? "amber" : "green";
                showPreview(state, "\u2713 " + data.coin + " \u2192 " + data.coin + "/" + quote);
            } else {
                showPreview("red", "\u2717 No supported trading pair found");
            }
        }

        async function resolvePreview(coin) {
            if (!coin) { showPreview("gray", ""); return; }

            if (previewCache.has(coin)) {
                if (coin === inflightCoin) renderResult(previewCache.get(coin));
                return;
            }

            showPreview("gray", "Resolving pair\u2026");
            try {
                var resp = await authenticatedFetch(
                    "/api/v1/scanner/resolve-pair/" + encodeURIComponent(coin)
                );
                var data = await resp.json();

                if (previewCache.size >= CACHE_MAX) {
                    previewCache.delete(previewCache.keys().next().value);
                }
                previewCache.set(coin, data);

                if (coin === inflightCoin) renderResult(data);
            } catch (e) {
                if (coin === inflightCoin) showPreview("gray", "Unable to resolve pair.");
            }
        }

        var addInput = document.getElementById("scanner-add-input");
        if (addInput) {
            addInput.addEventListener("input", function() {
                var coin = this.value.trim().toUpperCase();
                inflightCoin = coin;
                clearTimeout(debounceTimer);
                if (!coin) { showPreview("gray", ""); return; }
                debounceTimer = setTimeout(function() { resolvePreview(coin); }, 300);
            });
        }
    })();
    // ── End Pair Preview ───────────────────────────────────────────────────

    // ── VGX Grid Management ────────────────────────────────────────────────
    // In-memory working copy of the coin list (editable tags).
    var _vgxCoins = [];

    function vgxMsg(id, text, isError) {
        var el = document.getElementById(id);
        if (!el) return;
        el.textContent = text;
        el.style.color = isError ? "var(--red, #f87171)" : "var(--green, #4ade80)";
        if (text) setTimeout(function() { if (el.textContent === text) el.textContent = ""; }, 4000);
    }

    function vgxRenderCoinTags() {
        var container = document.getElementById("vgx-coin-tags");
        var countEl   = document.getElementById("vgx-coin-count");
        if (!container) return;
        container.innerHTML = "";
        _vgxCoins.forEach(function(coin) {
            var tag = document.createElement("span");
            tag.style.cssText = "display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:20px;background:var(--accent-blue,#3b82f6);color:#fff;font-size:0.8rem;font-weight:600;";

            var label = document.createTextNode(coin);
            tag.appendChild(label);

            var btn = document.createElement("button");
            btn.type = "button";
            btn.textContent = "\u00d7";
            btn.style.cssText = "background:none;border:none;color:#fff;cursor:pointer;font-size:0.9rem;padding:0 0 0 4px;line-height:1;";
            // Capture coin in closure — no inline onclick, no string interpolation.
            (function(c) { btn.addEventListener("click", function() { vgxRemoveCoinTag(c); }); })(coin);
            tag.appendChild(btn);

            container.appendChild(tag);
        });
        if (countEl) countEl.textContent = _vgxCoins.length + " coin" + (_vgxCoins.length !== 1 ? "s" : "");
    }

    window.vgxAddCoinTag = function() {
        var input = document.getElementById("vgx-coin-input");
        if (!input) return;
        var coin = input.value.trim().toUpperCase();
        if (!coin || !/^[A-Z0-9]+$/.test(coin)) { vgxMsg("vgx-coins-msg", "Coin must be alphanumeric.", true); return; }
        if (coin.length > 10)                    { vgxMsg("vgx-coins-msg", "Max 10 characters.", true); return; }
        if (_vgxCoins.includes(coin))            { vgxMsg("vgx-coins-msg", coin + " already in list.", true); return; }
        if (_vgxCoins.length >= 20)              { vgxMsg("vgx-coins-msg", "Maximum 20 coins.", true); return; }
        _vgxCoins.push(coin);
        input.value = "";
        vgxRenderCoinTags();
        vgxMsg("vgx-coins-msg", "", false);
    };

    window.vgxRemoveCoinTag = function(coin) {
        _vgxCoins = _vgxCoins.filter(function(c) { return c !== coin; });
        vgxRenderCoinTags();
    };

    window.vgxSaveGridCoins = async function() {
        if (_vgxCoins.length === 0) { vgxMsg("vgx-coins-msg", "List cannot be empty.", true); return; }
        try {
            var resp = await authenticatedFetch("/api/vgx/grid-coins", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ coins: _vgxCoins }),
            });
            var data = await resp.json();
            if (data.status === "ok") {
                vgxMsg("vgx-coins-msg", "Saved — " + data.count + " coins active.", false);
            } else {
                vgxMsg("vgx-coins-msg", "Error: " + (data.reason || "unknown"), true);
            }
        } catch (e) {
            vgxMsg("vgx-coins-msg", "Network error.", true);
        }
    };

    // ── Base Price table ───────────────────────────────────────────────────
    function _vgxTd(text, cls, style) {
        var td = document.createElement("td");
        if (cls)   td.className = cls;
        if (style) td.style.cssText = style;
        td.textContent = text;
        return td;
    }

    function _vgxActionBtn(label, coin, handler, extraStyle) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-sm btn-outline";
        if (extraStyle) btn.style.cssText = extraStyle;
        btn.textContent = label;
        (function(c) { btn.addEventListener("click", function() { handler(c); }); })(coin);
        return btn;
    }

    function vgxRenderBasePriceTable(gridConfig, gridCoins) {
        var tbody = document.getElementById("vgx-base-price-tbody");
        if (!tbody) return;
        tbody.innerHTML = "";

        if (gridCoins.length === 0) {
            var emptyRow = document.createElement("tr");
            var emptyTd  = document.createElement("td");
            emptyTd.colSpan = 5;
            emptyTd.className = "text-muted";
            emptyTd.textContent = "No active coins.";
            emptyRow.appendChild(emptyTd);
            tbody.appendChild(emptyRow);
            return;
        }

        gridCoins.forEach(function(coin) {
            var entry = gridConfig[coin];
            var tr = document.createElement("tr");

            var coinTd = document.createElement("td");
            var strong = document.createElement("strong");
            strong.textContent = coin;
            coinTd.appendChild(strong);
            tr.appendChild(coinTd);

            if (entry && entry.base_price) {
                var setAt = entry.base_price_set_at
                    ? entry.base_price_set_at.slice(0, 16).replace("T", " ")
                    : "—";
                var priceStr = Number(entry.base_price).toLocaleString("en-IN", {maximumFractionDigits: 4});
                tr.appendChild(_vgxTd(priceStr, "text-gold font-mono", null));
                tr.appendChild(_vgxTd(setAt + " UTC", "text-muted", "font-size:0.78rem"));
                tr.appendChild(_vgxTd(entry.base_price_set_by || "—", "text-muted", "font-size:0.78rem"));

                var actTd = document.createElement("td");
                actTd.appendChild(_vgxActionBtn(
                    "Clear", coin, vgxClearBasePrice,
                    "color:var(--red,#f87171);border-color:var(--red,#f87171)"
                ));
                tr.appendChild(actTd);
            } else {
                var autoTd = document.createElement("td");
                autoTd.className = "text-muted";
                autoTd.style.fontStyle = "italic";
                autoTd.textContent = "Auto (live market)";
                tr.appendChild(autoTd);
                tr.appendChild(_vgxTd("—", null, null));
                tr.appendChild(_vgxTd("—", null, null));

                var actTd2 = document.createElement("td");
                actTd2.appendChild(_vgxActionBtn("Set", coin, vgxQuickSetPrice, null));
                tr.appendChild(actTd2);
            }
            tbody.appendChild(tr);
        });
    }

    async function vgxLoadGridConfig() {
        try {
            var resp = await authenticatedFetch("/api/vgx/grid-config");
            var data = await resp.json();
            _vgxCoins = data.grid_coins || [];
            vgxRenderCoinTags();
            vgxRenderBasePriceTable(data.grid_config || {}, _vgxCoins);
        } catch (e) {
            var tbody = document.getElementById("vgx-base-price-tbody");
            if (tbody) tbody.innerHTML = "<tr><td colspan='5' class='text-muted'>Failed to load grid config.</td></tr>";
        }
    }

    window.vgxSaveBasePrice = async function() {
        var coin  = (document.getElementById("vgx-bp-coin-input")  || {}).value || "";
        var price = (document.getElementById("vgx-bp-price-input") || {}).value || "";
        coin  = coin.trim().toUpperCase();
        price = parseFloat(price);
        if (!coin || !/^[A-Z0-9]+$/.test(coin)) { vgxMsg("vgx-bp-msg", "Enter a valid coin symbol.", true); return; }
        if (!price || price <= 0)                { vgxMsg("vgx-bp-msg", "Enter a price > 0.", true); return; }
        try {
            var resp = await authenticatedFetch("/api/vgx/base-price", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ coin: coin, base_price: price }),
            });
            var data = await resp.json();
            if (data.status === "ok") {
                vgxMsg("vgx-bp-msg", "Base price set for " + coin + ".", false);
                document.getElementById("vgx-bp-coin-input").value  = "";
                document.getElementById("vgx-bp-price-input").value = "";
                await vgxLoadGridConfig();
            } else {
                vgxMsg("vgx-bp-msg", "Error: " + (data.reason || "unknown"), true);
            }
        } catch (e) {
            vgxMsg("vgx-bp-msg", "Network error.", true);
        }
    };

    window.vgxClearBasePrice = async function(coin) {
        try {
            var resp = await authenticatedFetch("/api/vgx/base-price?coin=" + encodeURIComponent(coin), { method: "DELETE" });
            var data = await resp.json();
            if (data.status === "ok") {
                vgxMsg("vgx-bp-msg", "Cleared base price for " + coin + ".", false);
                await vgxLoadGridConfig();
            } else {
                vgxMsg("vgx-bp-msg", data.status === "not_found" ? coin + " had no manual price set." : "Error clearing price.", data.status !== "not_found");
            }
        } catch (e) {
            vgxMsg("vgx-bp-msg", "Network error.", true);
        }
    };

    window.vgxQuickSetPrice = function(coin) {
        var coinInput  = document.getElementById("vgx-bp-coin-input");
        var priceInput = document.getElementById("vgx-bp-price-input");
        if (coinInput)  coinInput.value  = coin;
        if (priceInput) priceInput.focus();
    };

    // Load grid config when the VGX view is first shown.
    document.querySelectorAll(".nav-anchor[data-target='vgx-view']").forEach(function(link) {
        link.addEventListener("click", function() {
            setTimeout(vgxLoadGridConfig, 100);
        });
    });
    // Also load on page init in case VGX view is active.
    if (document.getElementById("vgx-view") && !document.getElementById("vgx-view").classList.contains("view-hidden")) {
        vgxLoadGridConfig();
    }
    // ── End VGX Grid Management ────────────────────────────────────────────

    // ── End Pair Preview ───────────────────────────────────────────────────

});
