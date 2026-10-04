// ITMS Traffic Operations Center Dashboard JavaScript
let currentIntersection = 'INT-101';
let soundEnabled = true;
let vehicleChart = null;
let map = null;
let markers = {};
let ws = null;
let lastAlertTimestamp = 0;

// Intersection Details
const intersectionConfig = {
    'INT-101': { name: 'Downtown Central Square', lat: 12.9716, lon: 77.5946 },
    'INT-102': { name: 'Tech Corridor Expressway', lat: 12.9352, lon: 77.6946 },
    'INT-103': { name: 'Metro Ring Road Cross', lat: 12.9279, lon: 77.6271 },
    'INT-104': { name: 'Harbor Freight Boulevard', lat: 13.0112, lon: 77.5551 }
};

document.addEventListener('DOMContentLoaded', () => {
    initClock();
    initMap();
    initChart();
    initWebSocket();
    fetchAnalytics();
    fetchIncidents();
    setInterval(fetchAnalytics, 3000);
});

// Live Header Clock
function initClock() {
    const clockEl = document.getElementById('live-clock');
    setInterval(() => {
        const now = new Date();
        clockEl.textContent = now.toLocaleTimeString() + ' UTC+5:30';
    }, 1000);
}

// Interactive Dark GIS Map
function initMap() {
    map = L.map('city-map', {
        zoomControl: false,
        attributionControl: false
    }).setView([12.9716, 77.6246], 12);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19
    }).addTo(map);

    // Place Markers for all 4 intersections
    for (const [id, info] of Object.entries(intersectionConfig)) {
        const marker = L.circleMarker([info.lat, info.lon], {
            radius: 10,
            fillColor: '#06b6d4',
            color: '#ffffff',
            weight: 2,
            opacity: 1,
            fillOpacity: 0.85
        }).addTo(map);

        marker.bindPopup(`
            <div style="font-family:'JetBrains Mono'; font-size:12px; color:#1e293b;">
                <strong>${info.name}</strong><br/>
                ID: ${id}<br/>
                <button onclick="switchIntersection('${id}')" style="margin-top:6px; padding:4px 8px; background:#06b6d4; color:#fff; border:none; border-radius:4px; cursor:pointer;">
                    View Camera & Signals
                </button>
            </div>
        `);

        marker.on('click', () => switchIntersection(id));
        markers[id] = marker;
    }
}

// Vehicle Classification Donut Chart
function initChart() {
    const ctx = document.getElementById('vehicleChart').getContext('2d');
    vehicleChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Cars', 'Buses', 'Trucks', 'Motorcycles'],
            datasets: [{
                data: [45, 12, 8, 22],
                backgroundColor: [
                    '#06b6d4', // Cyan
                    '#10b981', // Emerald
                    '#f59e0b', // Amber
                    '#8b5cf6'  // Purple
                ],
                borderWidth: 2,
                borderColor: '#111726'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#94a3b8',
                        font: { family: 'JetBrains Mono', size: 11 }
                    }
                }
            },
            cutout: '70%'
        }
    });
}

// Switch Active Camera Feed
function switchIntersection(intId) {
    currentIntersection = intId;
    
    // Update active tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('bg-cyan-600', 'text-white');
        btn.classList.add('text-slate-400');
    });
    const activeTab = document.getElementById(`tab-${intId}`);
    if (activeTab) {
        activeTab.classList.add('bg-cyan-600', 'text-white');
        activeTab.classList.remove('text-slate-400');
    }

    // Update Stream image source
    const streamImg = document.getElementById('cctv-stream');
    streamImg.src = `/api/stream/${intId}`;

    // Update labels
    const conf = intersectionConfig[intId];
    document.getElementById('current-cam-label').textContent = `[${intId}] ${conf.name}`;
    document.getElementById('hud-camera-id').textContent = `CAM-${intId.split('-')[1]}`;

    // Pan map smoothly to intersection
    if (map && conf) {
        map.panTo([conf.lat, conf.lon]);
    }
}

// WebSocket Live Telemetry Handler
function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleTelemetry(data);
        } catch (e) {
            console.error('WS Parse Error', e);
        }
    };

    ws.onclose = () => {
        setTimeout(initWebSocket, 2000);
    };
}

function handleTelemetry(data) {
    const curData = data.intersections[currentIntersection];
    if (curData) {
        updateSignalHeads(curData.signal_ns, curData.signal_ew, curData.countdown, curData.phase_name, curData.mode);
        updateQueues(curData.queues);
    }

    // Update Map Marker colors based on congestion & incidents
    for (const [id, iData] of Object.entries(data.intersections)) {
        if (markers[id]) {
            if (iData.has_accident) {
                markers[id].setStyle({ fillColor: '#ef4444', radius: 14 });
            } else if (iData.has_illegal_parking || iData.has_emergency) {
                markers[id].setStyle({ fillColor: '#f59e0b', radius: 12 });
            } else {
                markers[id].setStyle({ fillColor: '#06b6d4', radius: 10 });
            }
        }
    }

    // Update Active Incidents & SLA timers
    renderIncidents(data.active_incidents);
}

// Update Visual 4-Way Traffic Signal Bulbs
function updateSignalHeads(sig_ns, sig_ew, countdown, phase_name, mode) {
    const nsRed = document.getElementById('bulb-ns-red');
    const nsYellow = document.getElementById('bulb-ns-yellow');
    const nsGreen = document.getElementById('bulb-ns-green');

    const ewRed = document.getElementById('bulb-ew-red');
    const ewYellow = document.getElementById('bulb-ew-yellow');
    const ewGreen = document.getElementById('bulb-ew-green');

    // Reset classes
    [nsRed, nsYellow, nsGreen, ewRed, ewYellow, ewGreen].forEach(el => {
        el.className = el.className.replace(/bulb-\w+-on/g, '').trim();
    });

    // NS Corridor
    if (sig_ns === 'GREEN') {
        nsGreen.classList.add('bulb-green-on');
    } else if (sig_ns === 'YELLOW') {
        nsYellow.classList.add('bulb-yellow-on');
    } else {
        nsRed.classList.add('bulb-red-on');
    }
    document.getElementById('label-ns-status').textContent = sig_ns;
    document.getElementById('label-ns-status').className = `mt-2 text-xs font-mono font-bold ${sig_ns === 'GREEN' ? 'text-emerald-400' : (sig_ns === 'YELLOW' ? 'text-amber-400' : 'text-red-400')}`;

    // EW Corridor
    if (sig_ew === 'GREEN') {
        ewGreen.classList.add('bulb-green-on');
    } else if (sig_ew === 'YELLOW') {
        ewYellow.classList.add('bulb-yellow-on');
    } else {
        ewRed.classList.add('bulb-red-on');
    }
    document.getElementById('label-ew-status').textContent = sig_ew;
    document.getElementById('label-ew-status').className = `mt-2 text-xs font-mono font-bold ${sig_ew === 'GREEN' ? 'text-emerald-400' : (sig_ew === 'YELLOW' ? 'text-amber-400' : 'text-red-400')}`;

    // Digital Countdown
    document.getElementById('signal-countdown').innerHTML = `${countdown}<span class="text-xs font-normal text-slate-400">s</span>`;
    document.getElementById('signal-phase-name').textContent = phase_name;
    document.getElementById('badge-controller-mode').textContent = mode;
    document.getElementById('header-rl-mode').textContent = mode;
}

// Update Queue counts
function updateQueues(queues) {
    if (!queues) return;
    document.getElementById('q-north').textContent = queues.north || 0;
    document.getElementById('q-south').textContent = queues.south || 0;
    document.getElementById('q-east').textContent = queues.east || 0;
    document.getElementById('q-west').textContent = queues.west || 0;
}

// Fetch Macro Analytics
async function fetchAnalytics() {
    try {
        const res = await fetch('/api/analytics');
        const data = await res.json();

        document.getElementById('stat-efficiency').textContent = `+${data.delay_reduction_pct}%`;
        document.getElementById('stat-rl-wait').textContent = `${data.avg_rl_delay_sec}s`;
        document.getElementById('stat-fixed-wait').textContent = `${data.avg_fixed_delay_sec}s`;
        document.getElementById('stat-co2').textContent = `${data.co2_saved_kg} kg`;
        document.getElementById('stat-cleared').textContent = data.total_vehicles_processed;

        const rlPct = Math.min(100, Math.round((data.avg_rl_delay_sec / (data.avg_fixed_delay_sec + 0.1)) * 100));
        document.getElementById('bar-rl-wait').style.width = `${rlPct}%`;

        // Update counts
        const vb = data.vehicle_breakdown;
        document.getElementById('cnt-cars').textContent = vb.car || 0;
        document.getElementById('cnt-buses').textContent = vb.bus || 0;
        document.getElementById('cnt-trucks').textContent = vb.truck || 0;
        document.getElementById('cnt-bikes').textContent = vb.motorcycle || 0;

        if (vehicleChart) {
            vehicleChart.data.datasets[0].data = [
                Math.max(10, vb.car),
                Math.max(3, vb.bus),
                Math.max(2, vb.truck),
                Math.max(5, vb.motorcycle)
            ];
            vehicleChart.update();
        }
    } catch (e) {
        console.error('Analytics Fetch Error', e);
    }
}

// Render Incidents and 30s SLA Tracker
function renderIncidents(activeList) {
    const tbody = document.getElementById('incident-table-body');
    const badge = document.getElementById('active-incidents-badge');
    badge.textContent = activeList ? activeList.length : 0;

    const banner = document.getElementById('critical-alert-banner');
    const critical = activeList ? activeList.find(x => x.severity === 'CRITICAL' && x.status === 'DETECTED') : null;

    if (critical) {
        banner.classList.remove('hidden');
        document.getElementById('alert-banner-title').textContent = `CRITICAL ${critical.incident_type} ALERT`;
        document.getElementById('alert-banner-desc').textContent = `${critical.description} (${critical.intersection_name})`;
        document.getElementById('alert-banner-sla').textContent = `${critical.sla_seconds_remaining}s`;

        document.getElementById('alert-banner-dispatch-btn').onclick = () => dispatchIncident(critical.incident_id);

        // Sound alert
        if (soundEnabled && (Date.now() - lastAlertTimestamp > 8000)) {
            playSiren();
            lastAlertTimestamp = Date.now();
        }
    } else {
        banner.classList.add('hidden');
    }

    if (!activeList || activeList.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="py-6 text-center text-slate-500">
                    No active incidents detected. Traffic corridors operating normally.
                </td>
            </tr>
        `;
        return;
    }

    let rowsHtml = '';
    activeList.forEach(inc => {
        let slaClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
        let slaText = `${inc.sla_seconds_remaining}s SLA`;
        if (inc.status === 'DISPATCHED') {
            slaClass = 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30';
            slaText = `DISPATCHED (<30s)`;
        } else if (inc.sla_breached) {
            slaClass = 'bg-red-500/20 text-red-400 border border-red-500/30 font-bold';
            slaText = `SLA BREACHED`;
        } else if (inc.sla_seconds_remaining < 10) {
            slaClass = 'bg-red-500/30 text-red-300 border border-red-500/50 animate-pulse font-bold';
            slaText = `⚠️ ${inc.sla_seconds_remaining}s SLA WARN`;
        }

        const sevClass = inc.severity === 'CRITICAL' ? 'bg-red-600/30 text-red-400 border-red-600/50' :
                         (inc.severity === 'HIGH' ? 'bg-amber-600/30 text-amber-400 border-amber-600/50' : 'bg-blue-600/30 text-blue-400 border-blue-600/50');

        rowsHtml += `
            <tr class="hover:bg-slate-800/40 transition">
                <td class="py-2.5 px-3 font-bold text-white">${inc.incident_id}</td>
                <td class="py-2.5 px-3">
                    <span class="px-2 py-0.5 rounded text-[10px] font-bold ${sevClass} border">
                        ${inc.incident_type}
                    </span>
                </td>
                <td class="py-2.5 px-3 text-slate-300">${inc.intersection_name}</td>
                <td class="py-2.5 px-3 font-semibold text-slate-200">${inc.severity}</td>
                <td class="py-2.5 px-3">
                    <span class="px-2 py-0.5 rounded text-[11px] font-mono ${slaClass}">
                        ${slaText}
                    </span>
                </td>
                <td class="py-2.5 px-3 text-slate-300 font-semibold">${inc.status}</td>
                <td class="py-2.5 px-3 text-right space-x-1.5 whitespace-nowrap">
                    ${inc.status === 'DETECTED' ? `
                        <button onclick="dispatchIncident('${inc.incident_id}')" class="px-2.5 py-1 bg-red-600 hover:bg-red-500 text-white rounded text-[11px] font-semibold transition">
                            🚨 Dispatch Police
                        </button>
                    ` : `
                        <button onclick="resolveIncident('${inc.incident_id}')" class="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-[11px] font-semibold transition">
                            ✅ Resolve
                        </button>
                    `}
                    ${inc.snapshot_url ? `
                        <button onclick="openSnapshotModal('${inc.snapshot_url}', '${inc.description}', '${inc.created_at}', '${inc.dispatched_unit || 'Unassigned'}')" class="px-2 py-1 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-[11px] transition">
                            📷 Evidence
                        </button>
                    ` : ''}
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = rowsHtml;
}

// Fetch historical & active incidents
async function fetchIncidents() {
    try {
        const res = await fetch('/api/incidents');
        const data = await res.json();
        renderIncidents(data.active);
    } catch (e) {
        console.error('Fetch Incidents Error', e);
    }
}

// Dispatch Police Patrol Unit Action
async function dispatchIncident(incidentId) {
    try {
        const formData = new FormData();
        formData.append('patrol_unit', 'Police Rapid Patrol Unit #Alpha-4');
        const res = await fetch(`/api/incidents/${incidentId}/dispatch`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (data.status === 'success') {
            fetchIncidents();
        }
    } catch (e) {
        console.error('Dispatch Error', e);
    }
}

// Resolve Incident Action
async function resolveIncident(incidentId) {
    try {
        const res = await fetch(`/api/incidents/${incidentId}/resolve`, {
            method: 'POST'
        });
        const data = await res.json();
        if (data.status === 'success') {
            fetchIncidents();
        }
    } catch (e) {
        console.error('Resolve Error', e);
    }
}

// Simulation Quick Event Injections
async function triggerSimEvent(type) {
    try {
        const formData = new FormData();
        formData.append('intersection_id', currentIntersection);
        const res = await fetch(`/api/simulate/${type}`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        console.log(`Simulated ${type}:`, data.message);
        setTimeout(fetchIncidents, 400);
    } catch (e) {
        console.error('Sim Event Error', e);
    }
}

// Change Controller Mode (RL / Fixed / Manual)
async function setControllerMode(mode) {
    try {
        const formData = new FormData();
        formData.append('intersection_id', currentIntersection);
        formData.append('mode', mode);
        await fetch('/api/controller/mode', {
            method: 'POST',
            body: formData
        });

        // Toggle Manual Controls display
        const manualCtrl = document.getElementById('manual-controls');
        if (mode === 'MANUAL') {
            manualCtrl.classList.remove('hidden');
        } else {
            manualCtrl.classList.add('hidden');
        }

        // Update button active states
        ['rl', 'fixed', 'manual'].forEach(m => {
            const btn = document.getElementById(`btn-mode-${m}`);
            btn.className = 'py-2 text-xs font-mono font-medium rounded-lg bg-slate-800 text-slate-300 hover:bg-slate-700 transition';
        });

        if (mode === 'RL_DQN') {
            document.getElementById('btn-mode-rl').className = 'py-2 text-xs font-mono font-medium rounded-lg bg-purple-600 text-white shadow-lg shadow-purple-600/30 transition';
        } else if (mode === 'FIXED_TIME') {
            document.getElementById('btn-mode-fixed').className = 'py-2 text-xs font-mono font-medium rounded-lg bg-cyan-600 text-white shadow-lg shadow-cyan-600/30 transition';
        } else {
            document.getElementById('btn-mode-manual').className = 'py-2 text-xs font-mono font-medium rounded-lg bg-amber-600 text-white shadow-lg shadow-amber-600/30 transition';
        }
    } catch (e) {
        console.error('Set Mode Error', e);
    }
}

// Manual Override
async function overrideSignal(corridor) {
    try {
        const formData = new FormData();
        formData.append('intersection_id', currentIntersection);
        formData.append('phase', corridor);
        await fetch('/api/controller/override', {
            method: 'POST',
            body: formData
        });
    } catch (e) {
        console.error('Manual Override Error', e);
    }
}

// Evidence Snapshot Modal
function openSnapshotModal(url, desc, time, unit) {
    document.getElementById('modal-image').src = url;
    document.getElementById('modal-desc').textContent = desc;
    document.getElementById('modal-time').textContent = time;
    document.getElementById('modal-unit').textContent = unit;
    document.getElementById('snapshot-modal').classList.remove('hidden');
}

function closeSnapshotModal() {
    document.getElementById('snapshot-modal').classList.add('hidden');
}

// Police Siren Synthesizer
function playSiren() {
    try {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.type = 'sawtooth';
        
        let freq = 700;
        osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
        
        // Pitch modulation for siren effect
        const now = audioCtx.currentTime;
        osc.frequency.linearRampToValueAtTime(1100, now + 0.35);
        osc.frequency.linearRampToValueAtTime(650, now + 0.7);
        osc.frequency.linearRampToValueAtTime(1100, now + 1.05);

        gain.gain.setValueAtTime(0.12, now);
        gain.gain.linearRampToValueAtTime(0.01, now + 1.2);

        osc.start(now);
        osc.stop(now + 1.2);
    } catch (e) {
        // Fallback
    }
}

function toggleSound() {
    soundEnabled = !soundEnabled;
    const icon = document.getElementById('icon-sound-on');
    if (soundEnabled) {
        icon.classList.remove('text-slate-500');
        icon.classList.add('text-emerald-400');
    } else {
        icon.classList.remove('text-emerald-400');
        icon.classList.add('text-slate-500');
    }
}
