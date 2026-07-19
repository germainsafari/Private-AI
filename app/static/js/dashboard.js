(() => {
  "use strict";

  const MAX_POINTS = 60; // ~60s of history at 1s sampling

  const $ = (id) => document.getElementById(id);
  const els = {
    connPill: $("connPill"),
    connLabel: $("connLabel"),
    gpuName: $("gpuName"),
    gpuUtil: $("gpuUtil"),
    gpuMem: $("gpuMem"),
    gpuPower: $("gpuPower"),
    gpuTemp: $("gpuTemp"),
    tokensPerSec: $("tokensPerSec"),
    totalRequests: $("totalRequests"),
    totalErrors: $("totalErrors"),
    totalRejected: $("totalRejected"),
    queueDepth: $("queueDepth"),
    activeRequests: $("activeRequests"),
    maxConcurrency: $("maxConcurrency"),
    maxQueueDepth: $("maxQueueDepth"),
    ttftP50: $("ttftP50"), ttftP95: $("ttftP95"), ttftP99: $("ttftP99"),
    waitP50: $("waitP50"), waitP95: $("waitP95"), waitP99: $("waitP99"),
    procP50: $("procP50"), procP95: $("procP95"), procP99: $("procP99"),
    totalP50: $("totalP50"), totalP95: $("totalP95"), totalP99: $("totalP99"),
  };

  const fmt = (v, digits = 1, suffix = "") =>
    v === null || v === undefined || Number.isNaN(v) ? "—" : `${Number(v).toFixed(digits)}${suffix}`;

  const pushPoint = (chart, label, value) => {
    const data = chart.data;
    data.labels.push(label);
    data.datasets[0].data.push(value);
    if (data.labels.length > MAX_POINTS) {
      data.labels.shift();
      data.datasets[0].data.shift();
    }
    chart.update("none");
  };

  const makeLineChart = (canvasId, color) => {
    const ctx = document.getElementById(canvasId).getContext("2d");
    return new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            data: [],
            borderColor: color,
            backgroundColor: color + "22",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.3,
            fill: true,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { display: false },
          y: { beginAtZero: true, grid: { color: "#23262f" }, ticks: { color: "#6b7080", font: { size: 10 } } },
        },
        plugins: { legend: { display: false } },
      },
    });
  };

  let gpuChart, throughputChart, queueChart;

  const initCharts = () => {
    gpuChart = makeLineChart("gpuChart", "#ffd24a");
    throughputChart = makeLineChart("throughputChart", "#ff8a3d");
    queueChart = makeLineChart("queueChart", "#5a9bff");
  };

  const setConnected = (ok) => {
    els.connPill.classList.toggle("offline", !ok);
    els.connLabel.textContent = ok ? "live" : "disconnected";
  };

  const render = (data) => {
    const gpu = data.gpu || {};
    if (gpu.available) {
      els.gpuName.textContent = gpu.name || "GPU";
      els.gpuUtil.textContent = fmt(gpu.gpu_util_pct, 0, "%");
      els.gpuMem.textContent = `${fmt(gpu.mem_used_mb, 0)} / ${fmt(gpu.mem_total_mb, 0)} MB`;
      els.gpuPower.textContent = gpu.power_w != null ? fmt(gpu.power_w, 0, " W") : "—";
      els.gpuTemp.textContent = gpu.temp_c != null ? fmt(gpu.temp_c, 0, " °C") : "—";
      pushPoint(gpuChart, "", gpu.gpu_util_pct ?? 0);
    } else {
      els.gpuName.textContent = "GPU telemetry unavailable" + (gpu.error ? ` (${gpu.error})` : "");
    }

    const throughput = data.throughput || {};
    const requests = data.requests || {};
    els.tokensPerSec.textContent = fmt(throughput.tokens_per_second, 1);
    els.totalRequests.textContent = requests.total_requests ?? "—";
    els.totalErrors.textContent = requests.total_errors ?? "—";
    els.totalRejected.textContent = requests.total_rejected ?? "—";
    pushPoint(throughputChart, "", throughput.tokens_per_second ?? 0);

    const orch = data.orchestrator || {};
    els.queueDepth.textContent = orch.queue_depth ?? "—";
    els.activeRequests.textContent = orch.active_requests ?? "—";
    els.maxConcurrency.textContent = orch.max_concurrency ?? "—";
    els.maxQueueDepth.textContent = orch.max_queue_depth ?? "—";
    pushPoint(queueChart, "", orch.queue_depth ?? 0);

    const lat = data.latency_ms || {};
    const setRow = (prefix, obj) => {
      els[`${prefix}P50`].textContent = fmt(obj?.p50);
      els[`${prefix}P95`].textContent = fmt(obj?.p95);
      els[`${prefix}P99`].textContent = fmt(obj?.p99);
    };
    setRow("ttft", lat.ttft);
    setRow("wait", lat.wait);
    setRow("proc", lat.processing);
    setRow("total", lat.total);
  };

  let es = null;
  let pollTimer = null;

  const startPolling = () => {
    if (pollTimer) return;
    const poll = async () => {
      try {
        const res = await fetch("/api/metrics");
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.json();
        setConnected(true);
        render(data);
      } catch (e) {
        setConnected(false);
      }
    };
    poll();
    pollTimer = setInterval(poll, 1000);
  };

  const startStream = () => {
    try {
      es = new EventSource("/api/metrics/stream");
      es.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          setConnected(true);
          render(data);
        } catch {
          /* ignore malformed frame */
        }
      };
      es.onerror = () => {
        setConnected(false);
        es.close();
        es = null;
        startPolling();
      };
    } catch {
      startPolling();
    }
  };

  const init = () => {
    initCharts();
    if (typeof EventSource !== "undefined") {
      startStream();
    } else {
      startPolling();
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
