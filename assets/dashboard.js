(function () {
  const DEFAULT_DATA = window.__FINANCE_DASHBOARD_DATA__ || {};

  function boot() {
    const app = document.getElementById("finance-app");
    if (!app || app.dataset.ready === "1") return;
    app.dataset.ready = "1";

    const data = normalizeData(DEFAULT_DATA);
    window.financeDashboardApp = { data, rerender: () => renderAll(app, window.financeDashboardApp.data) };

    initNavigation(app);
    initUploads(app, data);
    initBackendStatusMonitor(app);
    initSmartTable(app, data);
    initMainTables(app);
    initDownloads(app, data);
    initRecommendationCards(app, data);
    initRecommendationsPager(app);
    renderAll(app, data);
  }

  function normalizeData(raw) {
    raw = raw || {};
    const meta = raw.meta || {};
    const analysisCompleted = meta.analysisCompleted === true || meta.analysisCompleted === "true";
    if (!analysisCompleted) {
      return {
        meta: { ...meta, analysisCompleted: false },
        summary: {},
        statementSummary: {},
        outputSummary: {},
        documents: [],
        transactions: [],
        charts: {},
        signals: [],
        connectionHighlights: [],
        reviewQueue: [],
        riskMemory: [],
        counterpartyRegistry: [],
        legalConclusion: { kpis: {}, normativeBase: [], courtPractice: [], operations: [], processingStats: {} },
        mainTables: { original: { columns: [], rows: [] }, finalAnalysis: { columns: [], rows: [] } },
        modal: {}
      };
    }
    const charts = raw.charts || {};
    return {
      meta: { ...meta, analysisCompleted: true },
      summary: raw.summary || {},
      statementSummary: raw.statementSummary || {},
      outputSummary: raw.outputSummary || {},
      documents: Array.isArray(raw.documents) ? raw.documents : [],
      transactions: Array.isArray(raw.transactions) ? raw.transactions : [],
      charts,
      signals: Array.isArray(raw.signals) ? raw.signals : [],
      connectionHighlights: Array.isArray(raw.connectionHighlights) ? raw.connectionHighlights : [],
      reviewQueue: Array.isArray(raw.reviewQueue) ? raw.reviewQueue : [],
      riskMemory: Array.isArray(raw.riskMemory) ? raw.riskMemory : [],
      counterpartyRegistry: Array.isArray(raw.counterpartyRegistry) ? raw.counterpartyRegistry : [],
      legalConclusion: raw.legalConclusion || { kpis: {}, normativeBase: [], courtPractice: [], operations: [], processingStats: {} },
      mainTables: raw.mainTables || { original: { columns: [], rows: [] }, finalAnalysis: { columns: [], rows: [] } },
      modal: raw.modal || {}
    };
  }

  function renderAll(app, data) {
    applyAnalysisState(app, data);
    renderSummary(app, data.summary || {}, data.statementSummary || {}, data.outputSummary || {});
    renderMainSummary(app, data);
    renderDocuments(app, data.documents);
    renderSignals(app, data.signals);
    renderConnectionHighlights(app, data.connectionHighlights);
    renderReviewQueue(app, data.reviewQueue);
    renderRegistry(app, data.counterpartyRegistry);
    renderRiskMemory(app, data.riskMemory);
    renderLegalConclusion(app, data.legalConclusion);
    renderMainTables(app, data.mainTables);
    renderRecommendationCards(app, data);
    renderCharts(app, data.charts);
  }

  function hasCompletedAnalysis(data) {
    return Boolean(data && data.meta && data.meta.analysisCompleted === true);
  }

  function applyAnalysisState(app, data) {
    const done = hasCompletedAnalysis(data);
    app.classList.toggle("has-analysis", done);
    app.classList.toggle("no-analysis", !done);
    const statsPlaceholder = app.querySelector("#stats-empty-card");
    if (statsPlaceholder instanceof HTMLElement) statsPlaceholder.hidden = done;
    ["#main-original-table-card", "#main-final-table-card", "#recommendations-card", "#main-summary-grid"].forEach((selector) => {
      const node = app.querySelector(selector);
      if (node instanceof HTMLElement) node.hidden = !done;
    });
    const hint = app.querySelector("#main-summary-hint");
    if (hint) hint.textContent = done ? "Данные обновляются сразу после каждого анализа" : "Загрузите выписку, чтобы увидеть сводку по операциям";
  }

  function initNavigation(app) {
    const pages = app.querySelectorAll(".page");
    const navButtons = app.querySelectorAll(".nav-btn");
    navButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.page;
        navButtons.forEach((b) => b.classList.toggle("active", b === btn));
        pages.forEach((page) => page.classList.toggle("active", page.dataset.view === target));
        window.scrollTo(0, 0);
        renderCharts(app, window.financeDashboardApp?.data?.charts || {});
      });
    });
  }

  function renderMainSummary(app, data) {
    const conclusion = data.legalConclusion || {};
    const kpis = conclusion.kpis || {};
    setText(app, "#main-kpi-total", formatInteger(kpis.totalAnalyzed || 0));
    setText(app, "#main-kpi-clusters", `Кластеров: ${formatInteger((conclusion.processingStats || {}).clustersTotal || 0)}`);
    setText(app, "#main-kpi-flagged", formatInteger(kpis.flaggedCount || 0));
    setText(app, "#main-kpi-flagged-amount", `Сумма: ${kpis.flaggedAmount || "0 ₽"}`);
    setText(app, "#main-kpi-review", formatInteger(kpis.needsReviewCount || 0));
    setText(app, "#main-kpi-recommend", formatInteger(getFlaggedOperations(data).length));
  }

  function initUploads(app, data) {
    const dropzone = app.querySelector("#dropzone");
    const fallbackFileInput = app.querySelector("#file-picker");
    const chooseFile = app.querySelector("#choose-file-btn");
    const runAnalysis = app.querySelector("#run-analysis-btn");
    const selectedLabel = app.querySelector("#selected-file-label");

    function bridgeInput() {
      return document.querySelector('#agent-file-upload input[type="file"]') || document.querySelector('#agent-file-upload input');
    }

    function bridgeStartButton(testMode) {
      const selector = testMode ? '#agent-start-test-button' : '#agent-start-button';
      return document.querySelector(`${selector} button`) || document.querySelector(selector);
    }

    function rememberSelectedFiles(files) {
      if (!files || !files.length) return;
      window.__financeDashboardSelectedFiles = Array.from(files);
      if (selectedLabel) {
        const first = files[0];
        selectedLabel.textContent = files.length === 1 ? `Выбран файл: ${first.name}` : `Выбрано файлов: ${files.length}`;
      }
    }

    function syncFilesToBackend(files) {
      if (!files || !files.length) return false;
      const input = bridgeInput();
      if (!input) return false;
      try {
        const transfer = new DataTransfer();
        Array.from(files).forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        rememberSelectedFiles(files);
        return true;
      } catch (error) {
        console.warn("Не удалось синхронизировать файл с Gradio bridge", error);
        return false;
      }
    }

    chooseFile?.addEventListener("click", () => {
      const input = bridgeInput();
      if (input) input.click();
      else fallbackFileInput?.click();
    });

    document.addEventListener("change", (event) => {
      const target = getElementTarget(event);
      if (!(target instanceof HTMLInputElement) || target.type !== "file") return;
      if (target.closest("#agent-file-upload")) {
        rememberSelectedFiles(target.files);
        addFilesToHistory(app, data, target.files);
      }
    });

    fallbackFileInput?.addEventListener("change", () => {
      rememberSelectedFiles(fallbackFileInput.files);
      syncFilesToBackend(fallbackFileInput.files);
      addFilesToHistory(app, data, fallbackFileInput.files);
    });

    if (dropzone) {
      ["dragenter", "dragover"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          dropzone.classList.add("dragover");
        });
      });
      ["dragleave", "drop"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          dropzone.classList.remove("dragover");
        });
      });
      dropzone.addEventListener("drop", (event) => {
        const files = event.dataTransfer.files;
        rememberSelectedFiles(files);
        syncFilesToBackend(files);
        addFilesToHistory(app, data, files);
      });
    }

    runAnalysis?.addEventListener("click", () => {
      const selected = window.__financeDashboardSelectedFiles;
      const testMode = Boolean(app.querySelector("#test-mode-checkbox")?.checked);
      if ((!selected || !selected.length) && !testMode) {
        openAgentLoadingModal(app, "Сначала загрузите Excel/CSV-файл или включите тестовый режим", true);
        window.setTimeout(() => closeAgentLoadingModal(app), 1700);
        return;
      }
      openAgentLoadingModal(app, testMode ? "Тестовый режим: агент отключен, загружаем mock-результат." : "Передаем файл агенту и запускаем backend pipeline.");
      const button = bridgeStartButton(testMode);
      if (button instanceof HTMLElement) {
        window.setTimeout(() => button.click(), 160);
      } else {
        setAgentLoadingError(app, testMode ? "Не найден backend bridge тестового режима. Проверьте app.py." : "Не найден backend bridge реального режима. Проверьте app.py.");
      }
    });
  }

  function initBackendStatusMonitor(app) {
    let lastStatus = "";
    let lastPayload = "";

    const poll = () => {
      const statusNode = document.getElementById("agent-run-status");
      const payloadNode = document.getElementById("agent-run-payload");
      const rawText = statusNode ? (statusNode.textContent || "") : "";
      const textareaValue = statusNode?.querySelector?.("textarea")?.value || "";
      const text = `${rawText} ${textareaValue}`.trim();

      const payloadText = payloadNode?.querySelector?.("textarea")?.value || payloadNode?.textContent || "";
      if (payloadText && payloadText !== lastPayload && payloadText.trim().startsWith("{")) {
        lastPayload = payloadText;
        try {
          const parsedPayload = JSON.parse(payloadText);
          parsedPayload.meta = parsedPayload.meta || {};
          parsedPayload.meta.analysisCompleted = true;
          const nextData = normalizeData(parsedPayload);
          window.financeDashboardApp.data = nextData;
          renderAll(app, nextData);
          initSmartTable(app, nextData);
          initDownloads(app, nextData);
          showAnalysisPreview(app, readBridgeText("agent-run-preview"), readBridgeText("agent-run-status"));
        } catch (error) {
          console.warn("Не удалось применить dashboard_payload из backend", error);
        }
      }

      if (text && text !== lastStatus) {
        lastStatus = text;
        const progressMatch = text.match(/ANALYSIS_PROGRESS:(\d+):(.*)$/s);
        if (progressMatch) {
          setAgentLoadingProgress(app, Number(progressMatch[1]), progressMatch[2].trim());
        }
        if (text.includes("ANALYSIS_DONE")) {
          setAgentLoadingComplete(app, text.replace(/^.*ANALYSIS_DONE:/s, "Готово:"));
        }
        if (text.includes("ANALYSIS_ERROR")) {
          setAgentLoadingError(app, text.replace(/^.*ANALYSIS_ERROR:/s, "Ошибка:"));
        }
      }
    };

    const observer = new MutationObserver(poll);
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    window.setInterval(() => {
      const modal = app.querySelector("#agent-loading-modal");
      if ((modal && !modal.hidden) || document.getElementById("agent-run-payload")) poll();
    }, 700);
  }

  let agentLoadingFinalized = false;
  let agentLoadingLog = [];
  const AGENT_LOADING_LOG_MAX = 6;

  function openAgentLoadingModal(app, message, isWarning) {
    const modal = app.querySelector("#agent-loading-modal");
    const text = app.querySelector("#agent-loading-text");
    const fill = app.querySelector("#agent-progress-fill");
    const percent = app.querySelector("#agent-progress-percent");
    if (!modal || !text || !fill || !percent) return;

    agentLoadingFinalized = false;
    agentLoadingLog = [];
    renderAgentLoadingLog(app);
    const initialPercent = isWarning ? 100 : 0;
    text.textContent = message || "Инициализируем агента.";
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    modal.classList.toggle("warning", Boolean(isWarning));
    fill.style.width = `${initialPercent}%`;
    percent.textContent = `${initialPercent}%`;
  }

  function setAgentLoadingProgress(app, percentValue, message) {
    if (agentLoadingFinalized) return;
    const modal = app.querySelector("#agent-loading-modal");
    const text = app.querySelector("#agent-loading-text");
    const fill = app.querySelector("#agent-progress-fill");
    const percent = app.querySelector("#agent-progress-percent");
    if (!modal) return;
    const clamped = Math.max(0, Math.min(100, Number(percentValue) || 0));
    if (fill) fill.style.width = `${clamped}%`;
    if (percent) percent.textContent = `${Math.round(clamped)}%`;
    if (!message) return;
    if (text) text.textContent = message;
    if (agentLoadingLog[0] !== message) {
      agentLoadingLog = [message, ...agentLoadingLog].slice(0, AGENT_LOADING_LOG_MAX);
      renderAgentLoadingLog(app);
    }
  }

  function renderAgentLoadingLog(app) {
    const log = app.querySelector("#agent-loading-log");
    if (!log) return;
    log.innerHTML = agentLoadingLog
      .map((message, index) => `<li class="${index === 0 ? "current" : ""}">${escapeHtml(message)}</li>`)
      .join("");
  }

  function setAgentLoadingComplete(app, message) {
    if (agentLoadingFinalized) return;
    agentLoadingFinalized = true;
    const modal = app.querySelector("#agent-loading-modal");
    const text = app.querySelector("#agent-loading-text");
    const fill = app.querySelector("#agent-progress-fill");
    const percent = app.querySelector("#agent-progress-percent");
    if (text) text.textContent = message || "Анализ завершен. Шапка таблицы выведена в консоль.";
    if (fill) fill.style.width = "100%";
    if (percent) percent.textContent = "100%";
    modal?.classList.add("complete");
    showAnalysisPreview(app, readBridgeText("agent-run-preview"), message || "Анализ завершен");
    window.setTimeout(() => {
      closeAgentLoadingModal(app);
      app.querySelector('#analysis-preview-card')?.scrollIntoView({ block: "start" });
    }, 800);
  }

  function setAgentLoadingError(app, message) {
    agentLoadingFinalized = true;
    const modal = app.querySelector("#agent-loading-modal");
    const text = app.querySelector("#agent-loading-text");
    const fill = app.querySelector("#agent-progress-fill");
    const percent = app.querySelector("#agent-progress-percent");
    modal?.classList.add("warning");
    if (text) text.textContent = message || "Ошибка запуска агента.";
    if (fill) fill.style.width = "100%";
    if (percent) percent.textContent = "ошибка";
    window.setTimeout(() => closeAgentLoadingModal(app), 2400);
  }

  function closeAgentLoadingModal(app) {
    const modal = app.querySelector("#agent-loading-modal");
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    modal.classList.remove("complete", "warning");
  }

  function readBridgeText(elementId) {
    const node = document.getElementById(elementId);
    if (!node) return "";
    const textarea = node.querySelector?.("textarea");
    if (textarea && typeof textarea.value === "string") return textarea.value.trim();
    return (node.textContent || "").trim();
  }

  function showAnalysisPreview(app, previewText, statusText) {
    const card = app.querySelector("#analysis-preview-card");
    const table = app.querySelector("#analysis-preview-table");
    const status = app.querySelector("#analysis-preview-status");
    if (!(card instanceof HTMLElement) || !(table instanceof HTMLTableElement) || !status) return;

    const cleanPreview = String(previewText || "").trim();
    const parsed = parsePreviewTable(cleanPreview);
    renderPreviewTable(table, parsed);

    status.textContent = String(statusText || "Анализ завершен").replace(/^.*ANALYSIS_DONE:\s*/s, "Готово: ").slice(0, 180);
    card.hidden = false;
    app.classList.add("has-analysis");
    app.classList.remove("no-analysis");
  }

  function parsePreviewTable(raw) {
    const fallback = { columns: ["Сообщение"], rows: [{ "Сообщение": "Шапка файла не получена из backend. Проверьте agent_runner.py и callback start_agent_analysis." }] };
    if (!raw) return fallback;

    try {
      const parsed = JSON.parse(raw);
      const columns = Array.isArray(parsed.columns) ? parsed.columns.map(String) : [];
      const rows = Array.isArray(parsed.rows) ? parsed.rows : [];
      if (columns.length && rows.length) return { columns, rows };
      if (columns.length) return { columns, rows: [] };
    } catch (_) {
      // Фолбэк для старого backend: если пришел df.head().to_string(), показываем его
      // внутри одной табличной колонки, а не бесформенным pre-блоком.
    }

    const lines = raw.split(/\r?\n/).filter(Boolean);
    if (!lines.length) return fallback;
    return {
      columns: ["Шапка файла"],
      rows: lines.map((line) => ({ "Шапка файла": line }))
    };
  }

  function renderPreviewTable(table, preview) {
    const columns = Array.isArray(preview.columns) && preview.columns.length ? preview.columns : ["Сообщение"];
    const rows = Array.isArray(preview.rows) ? preview.rows : [];
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    if (!thead || !tbody) return;

    thead.innerHTML = `<tr>${columns.map((col) => `<th title="${escapeAttr(col)}">${escapeHtml(col)}</th>`).join("")}</tr>`;
    if (!rows.length) {
      tbody.innerHTML = `<tr>${columns.map((_, index) => `<td>${index === 0 ? "Нет строк для предпросмотра" : ""}</td>`).join("")}</tr>`;
      return;
    }
    tbody.innerHTML = rows.map((row) => `<tr>${columns.map((col) => {
      const value = row && Object.prototype.hasOwnProperty.call(row, col) ? row[col] : "";
      return `<td title="${escapeAttr(value)}">${escapeHtml(value)}</td>`;
    }).join("")}</tr>`).join("");
  }

  function addFilesToHistory(app, data, files) {
    if (!files || !files.length) return;
    Array.from(files).forEach((file) => {
      const ext = (file.name.split(".").pop() || "file").toUpperCase();
      const now = new Date();
      data.documents.unshift({
        document: file.name,
        type: ext,
        uploaded: `${now.toLocaleDateString("ru-RU")}, ${now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`,
        status: "в очереди",
        statusClass: "yellow"
      });
    });
    renderDocuments(app, data.documents);
  }

  function initSmartTable(app, data) {
    const table = app.querySelector("#transactions-table");
    const tbody = table ? table.querySelector("tbody") : null;
    const pageSizeSelect = app.querySelector("#page-size");
    const pageInfo = app.querySelector("#page-info");
    const pageLabel = app.querySelector("#page-label");
    if (!table || !tbody || !pageSizeSelect || !pageInfo || !pageLabel) return;

    let sortKey = null;
    let sortDir = 1;
    let page = 1;
    const filters = {};
    const keys = ["idx", "date", "cluster_id", "amount", "transaction_category", "counterparty", "risk_level", "connection_basis", "legal_qualification", "challenge_readiness", "recommendation"];

    table.querySelectorAll("th[data-key]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (sortKey === key) sortDir *= -1;
        else { sortKey = key; sortDir = 1; }
        page = 1;
        render();
      });
    });

    table.querySelectorAll("input[data-filter]").forEach((input) => {
      input.addEventListener("input", () => {
        filters[input.dataset.filter] = input.value.trim().toLowerCase();
        page = 1;
        render();
      });
    });

    pageSizeSelect.addEventListener("change", () => { page = 1; render(); });
    app.querySelectorAll(".pager-btn[data-pager]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const filtered = getFilteredRows();
        const pageSize = parseInt(pageSizeSelect.value, 10) || 15;
        const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
        if (btn.dataset.pager === "first") page = 1;
        if (btn.dataset.pager === "prev") page = Math.max(1, page - 1);
        if (btn.dataset.pager === "next") page = Math.min(totalPages, page + 1);
        if (btn.dataset.pager === "last") page = totalPages;
        render();
      });
    });

    function getFilteredRows() {
      let rows = (data.transactions || []).filter((row) => {
        return Object.entries(filters).every(([key, value]) => !value || String(row[key] ?? "").toLowerCase().includes(value));
      });
      if (sortKey) {
        rows = rows.slice().sort((a, b) => {
          const av = normalizeForSort(a[sortKey], sortKey);
          const bv = normalizeForSort(b[sortKey], sortKey);
          if (av < bv) return -1 * sortDir;
          if (av > bv) return 1 * sortDir;
          return 0;
        });
      }
      return rows;
    }

    function render() {
      const rows = getFilteredRows();
      const pageSize = parseInt(pageSizeSelect.value, 10) || 15;
      const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
      if (page > totalPages) page = totalPages;
      const start = (page - 1) * pageSize;
      const visible = rows.slice(start, start + pageSize);
      tbody.innerHTML = visible.map((row) => `<tr>${keys.map((key) => {
        const value = row[key] ?? "";
        return `<td title="${escapeAttr(value)}">${escapeHtml(value)}</td>`;
      }).join("")}</tr>`).join("");
      const from = rows.length ? start + 1 : 0;
      const to = Math.min(start + pageSize, rows.length);
      pageInfo.textContent = `${from} to ${to} of ${rows.length}`;
      pageLabel.textContent = `Page ${page} of ${totalPages}`;
      window.financeDashboardApp.getFilteredTransactions = getFilteredRows;
    }

    window.financeDashboardApp.refreshSmartTable = render;
    render();
  }

  function initDownloads(app, data) {
    app.querySelector("#download-transactions")?.addEventListener("click", () => {
      const rows = window.financeDashboardApp?.getFilteredTransactions?.() || data.transactions || [];
      downloadCsv("transactions.csv", rows, ["idx", "date", "cluster_id", "amount", "transaction_category", "counterparty", "inn", "risk_level", "connection_basis", "legal_qualification", "challenge_readiness", "recommendation", "analysis_source", "propagation_confidence"]);
    });

    app.querySelector("#download-legal")?.addEventListener("click", () => {
      const currentData = window.financeDashboardApp?.data || data;
      const ops = (currentData.legalConclusion && currentData.legalConclusion.operations) || [];
      downloadCsv("legal_conclusion.csv", ops, ["idx", "date", "amount", "counterparty", "inn", "riskLabel", "legalQualification", "legalRoute", "decisionArgumentation", "riskExplanation", "recommendation"]);
    });

    app.querySelector("#download-all-zip")?.addEventListener("click", () => {
      const zipContainer = document.getElementById("agent-export-zip");
      const zipLink = zipContainer?.querySelector('a[href]') || zipContainer?.querySelector("a[download]");
      if (zipLink instanceof HTMLAnchorElement) {
        zipLink.click();
      } else {
        window.alert("Архив таблиц еще не готов. Запустите анализ в рабочем режиме (не тестовом) и дождитесь завершения.");
      }
    });

    app.querySelector("#open-full-table-btn")?.addEventListener("click", () => {
      const statsNavBtn = app.querySelector('.nav-btn[data-page="stats"]');
      if (statsNavBtn instanceof HTMLElement) statsNavBtn.click();
    });
  }

  let mainOriginalTableController = null;
  let mainFinalTableController = null;

  function initMainTables(app) {
    const originalTable = app.querySelector("#main-original-table");
    if (originalTable instanceof HTMLTableElement) {
      mainOriginalTableController = createDynamicTable(app, originalTable, "main-original-table");
    }
    const finalTable = app.querySelector("#main-final-table");
    if (finalTable instanceof HTMLTableElement) {
      mainFinalTableController = createDynamicTable(app, finalTable, "main-final-table");
    }
  }

  function renderMainTables(app, mainTables) {
    mainTables = mainTables || {};
    mainOriginalTableController?.setData(mainTables.original || { columns: [], rows: [] });
    mainFinalTableController?.setData(mainTables.finalAnalysis || { columns: [], rows: [] });
  }

  // Динамическая таблица-превью: сортировка по клику на заголовок, фильтр по
  // каждой колонке, постраничный вывод — тот же уровень интерактивности, что и
  // у таблицы транзакций на странице «Статистика», но с колонками, которые
  // определяются на лету (структура исходного файла заранее не известна).
  function createDynamicTable(app, table, idPrefix) {
    const tbody = table.querySelector("tbody");
    const thead = table.querySelector("thead");
    const pageInfo = app.querySelector(`#${idPrefix}-page-info`);
    const pageLabel = app.querySelector(`#${idPrefix}-page-label`);
    const pagerRow = app.querySelector(`#${idPrefix}-pager`);
    const pageSize = 10;

    let columns = [];
    let rows = [];
    let sortKey = null;
    let sortDir = 1;
    let page = 1;
    const filters = {};

    function renderHeader() {
      if (!thead) return;
      if (!columns.length) {
        thead.innerHTML = "";
        return;
      }
      thead.innerHTML = `
        <tr>${columns.map((col) => `<th data-key="${escapeAttr(col)}" title="${escapeAttr(col)}">${escapeHtml(col)} <span class="sort-mark">↕</span></th>`).join("")}</tr>
        <tr class="filter-row">${columns.map((col) => `<td><input data-filter="${escapeAttr(col)}" aria-label="Фильтр ${escapeAttr(col)}"></td>`).join("")}</tr>`;
      thead.querySelectorAll("th[data-key]").forEach((th) => {
        th.addEventListener("click", () => {
          const key = th.dataset.key;
          if (sortKey === key) sortDir *= -1;
          else { sortKey = key; sortDir = 1; }
          page = 1;
          render();
        });
      });
      thead.querySelectorAll("input[data-filter]").forEach((input) => {
        input.addEventListener("input", () => {
          filters[input.dataset.filter] = input.value.trim().toLowerCase();
          page = 1;
          render();
        });
      });
    }

    function getFilteredRows() {
      let out = rows.filter((row) => Object.entries(filters).every(([key, value]) => !value || String(row[key] ?? "").toLowerCase().includes(value)));
      if (sortKey) {
        out = out.slice().sort((a, b) => {
          const av = smartCellValue(a[sortKey]);
          const bv = smartCellValue(b[sortKey]);
          if (av < bv) return -1 * sortDir;
          if (av > bv) return 1 * sortDir;
          return 0;
        });
      }
      return out;
    }

    function render() {
      if (!tbody) return;
      if (!columns.length) {
        tbody.innerHTML = `<tr><td>Нет данных для отображения.</td></tr>`;
        if (pageInfo) pageInfo.textContent = "0 to 0 of 0";
        if (pageLabel) pageLabel.textContent = "Page 1 of 1";
        return;
      }
      const filtered = getFilteredRows();
      const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
      if (page > totalPages) page = totalPages;
      const start = (page - 1) * pageSize;
      const visible = filtered.slice(start, start + pageSize);
      tbody.innerHTML = visible.length
        ? visible.map((row) => `<tr>${columns.map((col) => {
            const value = row && Object.prototype.hasOwnProperty.call(row, col) ? row[col] : "";
            return `<td title="${escapeAttr(value)}">${escapeHtml(value)}</td>`;
          }).join("")}</tr>`).join("")
        : `<tr><td>${filtered.length === 0 && rows.length > 0 ? "Нет строк, соответствующих фильтру." : "Нет строк для предпросмотра"}</td></tr>`;
      const from = filtered.length ? start + 1 : 0;
      const to = Math.min(start + pageSize, filtered.length);
      if (pageInfo) pageInfo.textContent = `${from} to ${to} of ${filtered.length}`;
      if (pageLabel) pageLabel.textContent = `Page ${page} of ${totalPages}`;
    }

    if (pagerRow) {
      pagerRow.querySelectorAll(".pager-btn[data-pager]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const filtered = getFilteredRows();
          const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
          if (btn.dataset.pager === "first") page = 1;
          if (btn.dataset.pager === "prev") page = Math.max(1, page - 1);
          if (btn.dataset.pager === "next") page = Math.min(totalPages, page + 1);
          if (btn.dataset.pager === "last") page = totalPages;
          render();
        });
      });
    }

    function setData(preview) {
      columns = Array.isArray(preview?.columns) ? preview.columns : [];
      rows = Array.isArray(preview?.rows) ? preview.rows : [];
      sortKey = null;
      sortDir = 1;
      page = 1;
      Object.keys(filters).forEach((key) => delete filters[key]);
      renderHeader();
      render();
    }

    function patchRow(matchKey, matchValue, patch) {
      const row = rows.find((r) => String(r?.[matchKey]) === String(matchValue));
      if (!row) return;
      Object.assign(row, patch);
      render();
    }

    return { setData, patchRow };
  }

  function smartCellValue(value) {
    const text = String(value ?? "").trim();
    if (!text) return "";
    const numeric = Number(text.replace(/[\s ]/g, "").replace(",", "."));
    if (Number.isFinite(numeric) && /^[-+]?[\d\s ]*[.,]?\d*\s*(₽|%)?$/.test(text)) return numeric;
    const asDate = text.match(/^(\d{2})\.(\d{2})\.(\d{4})/);
    if (asDate) return `${asDate[3]}-${asDate[2]}-${asDate[1]}`;
    return text.toLowerCase();
  }

  function getFlaggedOperations(data) {
    const ops = (data && data.legalConclusion && data.legalConclusion.operations) || [];
    return ops.filter((op) => Number(op.riskLevel) >= 2);
  }

  const RECOMMENDATIONS_PAGE_SIZE = 4;
  let recommendationsPage = 1;

  function renderRecommendationCards(app, data) {
    const grid = app.querySelector("#recommendations-grid");
    if (!grid) return;
    const ops = getFlaggedOperations(data);
    const pagerRow = app.querySelector("#recommendations-pager");
    if (!ops.length) {
      grid.innerHTML = `<div class="muted-text">Операций высокого риска не найдено — редактировать пока нечего.</div>`;
      if (pagerRow) pagerRow.hidden = true;
      setText(app, "#recommendations-count", "");
      return;
    }
    if (pagerRow) pagerRow.hidden = false;
    recommendationsPage = 1;
    renderRecommendationsPage(app, ops);
  }

  function renderRecommendationsPage(app, ops) {
    const grid = app.querySelector("#recommendations-grid");
    if (!grid) return;
    const totalPages = Math.max(1, Math.ceil(ops.length / RECOMMENDATIONS_PAGE_SIZE));
    if (recommendationsPage > totalPages) recommendationsPage = totalPages;
    const start = (recommendationsPage - 1) * RECOMMENDATIONS_PAGE_SIZE;
    const visible = ops.slice(start, start + RECOMMENDATIONS_PAGE_SIZE);
    grid.innerHTML = visible.map(renderRecommendationCard).join("");
    const from = ops.length ? start + 1 : 0;
    const to = Math.min(start + RECOMMENDATIONS_PAGE_SIZE, ops.length);
    setText(app, "#recommendations-page-info", `${from} to ${to} of ${ops.length}`);
    setText(app, "#recommendations-page-label", `Page ${recommendationsPage} of ${totalPages}`);
    setText(app, "#recommendations-count", `Всего операций высокого риска: ${ops.length}`);
  }

  function initRecommendationsPager(app) {
    const pagerRow = app.querySelector("#recommendations-pager");
    if (!pagerRow) return;
    pagerRow.querySelectorAll(".pager-btn[data-pager]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const ops = getFlaggedOperations(window.financeDashboardApp?.data);
        if (!ops.length) return;
        const totalPages = Math.max(1, Math.ceil(ops.length / RECOMMENDATIONS_PAGE_SIZE));
        if (btn.dataset.pager === "first") recommendationsPage = 1;
        if (btn.dataset.pager === "prev") recommendationsPage = Math.max(1, recommendationsPage - 1);
        if (btn.dataset.pager === "next") recommendationsPage = Math.min(totalPages, recommendationsPage + 1);
        if (btn.dataset.pager === "last") recommendationsPage = totalPages;
        renderRecommendationsPage(app, ops);
      });
    });
  }

  function renderRecommendationCard(op) {
    return `
      <article class="recommendation-card" data-rec-idx="${escapeAttr(op.idx)}">
        <div class="conclusion-card-head">
          <div>
            <b>${escapeHtml(op.amount)}</b>
            <span class="muted-inline">${escapeHtml(op.date)} · ${escapeHtml(op.counterparty)} (ИНН ${escapeHtml(op.inn || "не определен")})</span>
          </div>
          <span class="badge ${escapeClass(op.riskClass)}">${escapeHtml(op.riskLabel)}</span>
        </div>
        <div class="conclusion-card-tags">
          <span class="tag">${escapeHtml(op.transactionCategory)}</span>
          <span class="tag">${escapeHtml(op.legalRoute)}</span>
          <span class="tag">${escapeHtml(op.connectionStrength)}</span>
        </div>
        <p class="conclusion-field">${escapeHtml(op.riskExplanation)}</p>
        <div class="recommendation-edit-block">
          <label class="recommendation-label" for="rec-input-${escapeAttr(op.idx)}">✎ Рекомендация — поле для правки сотрудником</label>
          <textarea class="recommendation-input" id="rec-input-${escapeAttr(op.idx)}" data-original="${escapeAttr(op.recommendation)}" rows="3">${escapeHtml(op.recommendation)}</textarea>
          <div class="recommendation-card-actions">
            <span class="recommendation-status" data-rec-status="${escapeAttr(op.idx)}"></span>
            <button class="tiny-btn" type="button" data-reset-rec="${escapeAttr(op.idx)}">Сбросить к варианту агента</button>
          </div>
        </div>
      </article>`;
  }

  function initRecommendationCards(app) {
    app.addEventListener("input", (event) => {
      const target = getElementTarget(event);
      if (!(target instanceof HTMLTextAreaElement) || !target.classList.contains("recommendation-input")) return;
      const card = target.closest("[data-rec-idx]");
      const idx = card?.dataset.recIdx;
      if (!idx) return;
      patchRecommendation(app, window.financeDashboardApp.data, idx, target.value);
      const status = app.querySelector(`[data-rec-status="${cssEscape(idx)}"]`);
      if (status) status.textContent = "Изменено вручную";
    });

    app.addEventListener("click", (event) => {
      const target = getElementTarget(event);
      const resetBtn = target?.closest("[data-reset-rec]");
      if (!resetBtn) return;
      const idx = resetBtn.dataset.resetRec;
      const textarea = app.querySelector(`#rec-input-${cssEscape(idx)}`);
      if (textarea instanceof HTMLTextAreaElement) {
        const original = textarea.dataset.original || "";
        textarea.value = original;
        patchRecommendation(app, window.financeDashboardApp.data, idx, original);
        const status = app.querySelector(`[data-rec-status="${cssEscape(idx)}"]`);
        if (status) status.textContent = "";
      }
    });
  }

  function patchRecommendation(app, data, idx, text) {
    if (!data) return;
    let changed = false;
    (data.transactions || []).forEach((row) => {
      if (String(row.idx) === String(idx)) {
        row.recommendation = text;
        changed = true;
      }
    });
    ((data.legalConclusion && data.legalConclusion.operations) || []).forEach((op) => {
      if (String(op.idx) === String(idx)) {
        op.recommendation = text;
        changed = true;
      }
    });
    if (!changed) return;

    const viewEl = app.querySelector(`[data-recommendation-view="${cssEscape(idx)}"]`);
    if (viewEl) viewEl.innerHTML = `<b>Рекомендация:</b> ${escapeHtml(text)}`;

    mainFinalTableController?.patchRow?.("idx", idx, { recommendation: text });
    window.financeDashboardApp?.refreshSmartTable?.();
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(String(value));
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function renderSummary(app, summary, statementSummary, outputSummary) {
    const stmt = statementSummary || {};
    const out = outputSummary || {};

    setText(app, "#summary-input-rows", formatInteger(summary.inputRows ?? stmt.inputRows ?? summary.preparedRows ?? 0));
    setText(app, "#summary-input-columns", formatInteger(summary.inputColumns ?? stmt.inputColumns ?? 0));
    setText(app, "#summary-date-period", `Период: ${summary.datePeriod || stmt.datePeriod || "не определен"}`);
    setText(app, "#summary-missing-fields", `Недостающие поля: ${summary.missingCoreFields || stmt.missingCoreFields || "нет"}`);

    const incoming = summary.incomingAmount || stmt.incomingAmount || "0 ₽";
    const outgoing = summary.outgoingAmount || stmt.outgoingAmount || "0 ₽";
    setText(app, "#summary-input-turnover", `${incoming} / ${outgoing}`);
    setText(app, "#summary-net-flow", `Чистый поток: ${summary.netAmount || stmt.netAmount || "0 ₽"}`);

    setText(app, "#summary-unique-counterparties", formatInteger(summary.uniqueCounterparties ?? stmt.uniqueCounterparties ?? 0));
    setText(app, "#summary-known-inn", `ИНН определены: ${formatInteger(summary.knownInnCount ?? stmt.knownInnCount ?? 0)}`);

    setText(app, "#summary-clusters", formatInteger(summary.clusters ?? stmt.clusters ?? 0));
    setText(app, "#summary-sampled-rows", `Выбрано для анализа: ${formatInteger(summary.sampledRows ?? stmt.sampledRows ?? 0)}`);
    const riskCount = Number(summary.highRisk || 0) + Number(summary.mediumRisk || 0);
    setText(app, "#summary-risk-count", formatInteger(riskCount));
    setText(app, "#summary-risk-amount", summary.riskAmount || out.riskAmount || "0 ₽");
    setText(app, "#summary-legal-routes", `Правовые маршруты: ${formatInteger(summary.legalRoutes ?? out.legalRoutes ?? 0)}`);
    setText(app, "#summary-strong-connections", formatInteger(summary.strongConnections ?? out.strongConnections ?? 0));
    setText(app, "#summary-documents-required", `Документы к запросу: ${formatInteger(summary.documentsRequired ?? out.documentsRequired ?? 0)}`);
    setText(app, "#summary-top-amount", summary.topAmountSum || out.topAmountSum || "0 ₽");
  }

  function setText(app, selector, value) {
    const node = app.querySelector(selector);
    if (node) node.textContent = String(value ?? "");
  }

  function renderDocuments(app, documents) {
    const tbody = app.querySelector("#docs-table tbody");
    if (!tbody) return;
    tbody.innerHTML = (documents || []).map((doc) => `
      <tr>
        <td title="${escapeAttr(doc.document)}">${escapeHtml(doc.document)}</td>
        <td title="${escapeAttr(doc.type)}">${escapeHtml(doc.type)}</td>
        <td title="${escapeAttr(doc.uploaded)}">${escapeHtml(doc.uploaded)}</td>
        <td><span class="badge ${escapeClass(doc.statusClass)}">${escapeHtml(doc.status)}</span></td>
      </tr>`).join("");
  }

  function renderSignals(app, signals) {
    const list = app.querySelector("#signals-list");
    if (!list) return;
    if (!signals || !signals.length) {
      list.innerHTML = `<li class="info-list-empty">Нет сигналов для отображения.</li>`;
      return;
    }
    list.innerHTML = signals.map((signal) => `
      <li class="info-list-item signal-item ${escapeClass(signal.className)}">
        <span class="info-list-dot" aria-hidden="true"></span>
        <div class="info-list-body">
          <div class="info-list-title-row"><span>${escapeHtml(signal.label)}</span><b>${escapeHtml(signal.count)}</b></div>
          <p class="info-list-desc">${escapeHtml(signal.description || "")}</p>
        </div>
      </li>`).join("");
  }

  function renderConnectionHighlights(app, rows) {
    const list = app.querySelector("#connections-list");
    if (!list) return;
    if (!rows || !rows.length) {
      list.innerHTML = `<li class="info-list-empty">Заметных связей между операциями не обнаружено.</li>`;
      return;
    }
    list.innerHTML = rows.map((row) => `
      <li class="info-list-item">
        <span class="info-list-dot" aria-hidden="true"></span>
        <div class="info-list-body">
          <div class="info-list-title-row"><span>${escapeHtml(row.counterparty)} <span class="muted-inline">(ИНН ${escapeHtml(row.inn || "не определен")})</span></span><b class="badge ${escapeClass(row.riskClass)}">${escapeHtml(row.risk)}</b></div>
          <p class="info-list-desc">${escapeHtml(row.summary)}</p>
        </div>
      </li>`).join("");
  }

  function renderReviewQueue(app, rows) {
    const list = app.querySelector("#review-queue-list");
    if (!list) return;
    if (!rows || !rows.length) {
      list.innerHTML = `<li class="info-list-empty">Все LLM-релевантные операции получили уверенный анализ.</li>`;
      return;
    }
    list.innerHTML = rows.map((row) => `
      <li class="info-list-item">
        <span class="info-list-dot yellow" aria-hidden="true"></span>
        <div class="info-list-body">
          <div class="info-list-title-row"><span>${escapeHtml(row.counterparty)}</span><b>${escapeHtml(row.amount)}</b></div>
          <p class="info-list-desc">${escapeHtml(row.reason)}</p>
        </div>
      </li>`).join("");
  }

  function renderRiskMemory(app, rows) {
    const tbody = app.querySelector("#risk-memory-table tbody");
    if (!tbody) return;
    if (!rows || !rows.length) {
      tbody.innerHTML = `<tr><td colspan="6">История риска по контрагентам этой выписки пока не накоплена.</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((row) => `
      <tr>
        <td title="${escapeAttr(row.counterparty)}">${escapeHtml(row.counterparty)}</td>
        <td>${escapeHtml(row.inn)}</td>
        <td>${escapeHtml(row.riskScore)}%</td>
        <td><span class="badge ${escapeClass(riskGradeClass(row.riskGrade))}">${escapeHtml(riskGradeLabel(row.riskGrade))}</span></td>
        <td>${escapeHtml(row.eventsCount)}</td>
        <td>${escapeHtml(row.lastEventDate || "—")}</td>
      </tr>`).join("");
  }

  function riskGradeLabel(grade) {
    const map = { NO_HISTORY: "нет истории", LOW: "низкий", MEDIUM: "средний", HIGH: "высокий", CRITICAL: "критический" };
    return map[grade] || grade || "—";
  }

  function riskGradeClass(grade) {
    const map = { NO_HISTORY: "gray", LOW: "green", MEDIUM: "yellow", HIGH: "red", CRITICAL: "red" };
    return map[grade] || "gray";
  }

  function renderRegistry(app, rows) {
    const tbody = app.querySelector("#registry-table tbody");
    if (!tbody) return;
    tbody.innerHTML = (rows || []).map((row) => `
      <tr>
        <td title="${escapeAttr(row.counterparty)}">${escapeHtml(row.counterparty)}</td>
        <td title="${escapeAttr(row.inn)}">${escapeHtml(row.inn)}</td>
        <td title="${escapeAttr(row.segment)}">${escapeHtml(row.segment)}</td>
        <td title="${escapeAttr(row.operations)}">${escapeHtml(row.operations)}</td>
        <td title="${escapeAttr(row.risk)}">${escapeHtml(row.risk)}</td>
        <td>${row.riskGrade && row.riskGrade !== "—" ? `<span class="badge ${escapeClass(riskGradeClass(row.riskGrade))}">${escapeHtml(riskGradeLabel(row.riskGrade))}</span>` : "—"}</td>
      </tr>`).join("");
  }

  function renderLegalConclusion(app, conclusion) {
    conclusion = conclusion || {};
    const kpis = conclusion.kpis || {};
    setText(app, "#legal-kpi-total", formatInteger(kpis.totalAnalyzed || 0));
    setText(app, "#legal-kpi-clusters", `Кластеров: ${formatInteger((conclusion.processingStats || {}).clustersTotal || 0)}`);
    setText(app, "#legal-kpi-flagged", formatInteger(kpis.flaggedCount || 0));
    setText(app, "#legal-kpi-flagged-amount", `Сумма: ${kpis.flaggedAmount || "0 ₽"}`);
    setText(app, "#legal-kpi-review", formatInteger(kpis.needsReviewCount || 0));
    setText(app, "#legal-kpi-second-pass", formatInteger(kpis.secondPassCount || 0));

    const normativeList = app.querySelector("#normative-base-list");
    if (normativeList) {
      const items = conclusion.normativeBase || [];
      normativeList.innerHTML = items.length
        ? items.map((item) => `<li class="info-list-item"><span class="info-list-dot" aria-hidden="true"></span><div class="info-list-body"><div class="info-list-title-row"><span>${escapeHtml(item.label)}</span><b>${escapeHtml(item.count)}</b></div></div></li>`).join("")
        : `<li class="info-list-empty">Нормативная база не выделена по операциям риска 2-3.</li>`;
    }

    const practiceList = app.querySelector("#court-practice-list");
    if (practiceList) {
      const items = conclusion.courtPractice || [];
      practiceList.innerHTML = items.length
        ? items.map((item) => `<li class="info-list-item"><span class="info-list-dot" aria-hidden="true"></span><div class="info-list-body"><div class="info-list-title-row"><span>${escapeHtml(item.label)}</span><b>${escapeHtml(item.count)}</b></div></div></li>`).join("")
        : `<li class="info-list-empty">Судебная практика не выделена по операциям риска 2-3.</li>`;
    }

    const opsContainer = app.querySelector("#legal-operations-list");
    if (opsContainer) {
      const ops = conclusion.operations || [];
      opsContainer.innerHTML = ops.length ? ops.map(renderLegalOperationCard).join("") : `<div class="muted-text">Операций риска 2-3 не найдено — итоговое заключение по выписке благоприятное.</div>`;
    }
  }

  function renderLegalOperationCard(op) {
    const basisList = (op.legalBasis || []).map((b) => `<li>${escapeHtml(b)}</li>`).join("");
    const courtList = (op.courtBasis || []).map((b) => `<li>${escapeHtml(b)}</li>`).join("");
    return `
      <article class="conclusion-card" data-op-idx="${escapeAttr(op.idx)}">
        <div class="conclusion-card-head">
          <div>
            <b>${escapeHtml(op.amount)}</b>
            <span class="muted-inline">${escapeHtml(op.date)} · ${escapeHtml(op.counterparty)} (ИНН ${escapeHtml(op.inn || "не определен")})</span>
          </div>
          <span class="badge ${escapeClass(op.riskClass)}">${escapeHtml(op.riskLabel)}</span>
        </div>
        <div class="conclusion-card-tags">
          <span class="tag">${escapeHtml(op.transactionCategory)}</span>
          <span class="tag">${escapeHtml(op.legalRoute)}</span>
          <span class="tag">${escapeHtml(op.connectionStrength)}</span>
          <span class="tag">готовность: ${escapeHtml(op.challengeReadiness)}</span>
        </div>
        <p class="conclusion-field"><b>Квалификация:</b> ${escapeHtml(op.legalQualification)}</p>
        <p class="conclusion-field"><b>Аргументация:</b> ${escapeHtml(op.decisionArgumentation)}</p>
        <p class="conclusion-field"><b>Объяснение риска:</b> ${escapeHtml(op.riskExplanation)}</p>
        <p class="conclusion-field"><b>Связь сторон:</b> ${escapeHtml(op.connectionSummary)}</p>
        ${op.overallRiskAssessment ? `<p class="conclusion-field"><b>Оценка кластера операций:</b> ${escapeHtml(op.overallRiskAssessment)}</p>` : ""}
        ${basisList ? `<div class="conclusion-field"><b>Нормативная база:</b><ul>${basisList}</ul></div>` : ""}
        ${courtList ? `<div class="conclusion-field"><b>Судебная практика:</b><ul>${courtList}</ul></div>` : ""}
        <p class="conclusion-field conclusion-recommendation" data-recommendation-view="${escapeAttr(op.idx)}"><b>Рекомендация:</b> ${escapeHtml(op.recommendation)}</p>
        ${op.verificationGoal ? `<p class="conclusion-field"><b>Цель проверки:</b> ${escapeHtml(op.verificationGoal)}</p>` : ""}
        ${op.riskChangeConditions ? `<p class="conclusion-field"><b>Что изменит риск:</b> ${escapeHtml(op.riskChangeConditions)}</p>` : ""}
      </article>`;
  }

  function renderCharts(app, chartData) {
    app.querySelectorAll(".chart-shell").forEach((el) => {
      const source = el.dataset.source;
      const rows = chartData[source] || [];
      if (!rows.length) {
        el.innerHTML = `<div class="muted-text">Нет данных для графика</div>`;
        return;
      }
      const type = el.dataset.chart;
      el.classList.remove("chart-type-bar", "chart-type-line", "chart-type-cashflow", "chart-type-area", "chart-type-donut");
      el.classList.add(`chart-type-${type}`);
      if (type === "bar") renderBarChart(el, rows);
      if (type === "line") renderLineChart(el, rows, false);
      if (type === "cashflow") renderLineChart(el, rows, true);
      if (type === "area") renderAreaChart(el, rows);
      if (type === "donut") renderDonutChart(el, rows);
    });
  }

  function renderBarChart(el, rows) {
    const { width, height } = measureChart(el, 760, 260);
    const margin = { top: 20, right: 18, bottom: 56, left: 55 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const xKey = el.dataset.x || "name";
    const yKey = el.dataset.y || "value";
    const unit = el.dataset.unit || "";
    const dataMax = Math.max(1, ...rows.map((d) => Math.abs(Number(d[yKey]) || 0)));
    const configuredMax = Number(el.dataset.ymax) || 0;
    const maxVal = niceMax(Math.max(dataMax, configuredMax));
    const step = innerW / rows.length;
    const barW = Math.min(118, step * 0.66);
    const grid = makeHorizontalGrid(margin, innerW, innerH, maxVal, unit);
    const bars = rows.map((d, i) => {
      const value = Number(d[yKey]) || 0;
      const safeRatio = maxVal > 0 ? Math.min(Math.abs(value) / maxVal, 1) : 0;
      const x = margin.left + i * step + (step - barW) / 2;
      const y = margin.top + innerH - safeRatio * innerH;
      const h = Math.max(0, safeRatio * innerH);
      const labelX = margin.left + i * step + step / 2;
      return `
        <g class="bar-item" style="--i:${i}">
          <rect class="bar-bg" x="${x.toFixed(1)}" y="${margin.top}" width="${barW.toFixed(1)}" height="${innerH}" rx="13"></rect>
          <rect class="bar-green chart-bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="13" data-tooltip="${escapeAttr(d[xKey])}: ${formatValue(value, unit)}"></rect>
          <text class="axis-label" x="${labelX.toFixed(1)}" y="${height - 20}" text-anchor="middle" transform="rotate(-10 ${labelX.toFixed(1)} ${height - 20})">${escapeSvg(shortLabel(d[xKey], 20))}</text>
        </g>`;
    }).join("");
    el.innerHTML = svgWrap(width, height, `${defs()}${grid}${bars}${makeAxis(margin, innerW, innerH)}`);
    attachTooltip(el);
  }

  function renderLineChart(el, rows, withArea) {
    const { width, height } = measureChart(el, 930, el.classList.contains("chart-tall") ? 285 : 260);
    const margin = { top: 24, right: 24, bottom: 44, left: 56 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const xKey = el.dataset.x || "month";
    const unit = el.dataset.unit || "";
    const series = parseSeries(el.dataset.series || "incoming:Поступления,outgoing:Расходные операции");
    const maxByData = Math.max(1, ...rows.flatMap((row) => series.map((s) => Math.abs(Number(row[s.key]) || 0))));
    const configuredMax = Number(el.dataset.ymax) || 0;
    const maxVal = niceMax(Math.max(maxByData, configuredMax));
    const xFor = (i) => margin.left + (rows.length === 1 ? innerW / 2 : (i / (rows.length - 1)) * innerW);
    const yFor = (v) => margin.top + innerH - Math.min(Math.abs(Number(v) || 0) / maxVal, 1) * innerH;
    const grid = makeHorizontalGrid(margin, innerW, innerH, maxVal, unit) + makeVerticalGrid(margin, innerW, innerH, rows.length);
    const labels = rows.map((d, i) => `<text class="axis-label" x="${xFor(i).toFixed(1)}" y="${height - 16}" text-anchor="middle">${escapeSvg(d[xKey])}</text>`).join("");
    const legend = makeLegend(width, series);
    const drawn = series.map((s, sIdx) => {
      const points = rows.map((d, i) => [xFor(i), yFor(d[s.key]), Number(d[s.key]) || 0, d[xKey]]);
      const path = smoothPath(points);
      const area = withArea && sIdx === 0 ? `<path class="area-green chart-area" d="${path} L ${points[points.length - 1][0].toFixed(1)} ${margin.top + innerH} L ${points[0][0].toFixed(1)} ${margin.top + innerH} Z"></path>` : "";
      const cls = sIdx === 0 ? "green" : "dark";
      const circles = points.map((p, pIdx) => `<circle class="point-${cls} chart-point" style="--i:${pIdx + sIdx}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4.2" data-tooltip="${escapeAttr(p[3])}: ${escapeAttr(s.label)} — ${formatValue(p[2], unit)}"></circle>`).join("");
      return `<g class="chart-series chart-series-${sIdx}">${area}<path class="line-${cls} chart-line" d="${path}"></path>${circles}</g>`;
    }).join("");
    el.innerHTML = svgWrap(width, height, `${defs()}${grid}${legend}<g class="chart-drawn">${drawn}</g>${labels}${makeAxis(margin, innerW, innerH)}`);
    attachTooltip(el);
  }

  function renderAreaChart(el, rows) {
    const { width, height } = measureChart(el, 720, 260);
    const margin = { top: 28, right: 22, bottom: 38, left: 54 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const xKey = el.dataset.x || "bucket";
    const yKey = el.dataset.y || "value";
    const dataMax = Math.max(1, ...rows.map((d) => Math.abs(Number(d[yKey]) || 0)));
    const configuredMax = Number(el.dataset.ymax) || 0;
    const maxVal = niceMax(Math.max(dataMax, configuredMax));
    const xFor = (i) => margin.left + (rows.length === 1 ? innerW / 2 : (i / (rows.length - 1)) * innerW);
    const yFor = (v) => margin.top + innerH - Math.min(Math.abs(Number(v) || 0) / maxVal, 1) * innerH;
    const points = rows.map((d, i) => [xFor(i), yFor(d[yKey]), Number(d[yKey]) || 0, d[xKey]]);
    const path = smoothPath(points);
    const area = `<path class="area-green chart-area" d="${path} L ${points[points.length - 1][0].toFixed(1)} ${margin.top + innerH} L ${points[0][0].toFixed(1)} ${margin.top + innerH} Z"></path>`;
    const circles = points.map((p, pIdx) => `<circle class="point-green chart-point" style="--i:${pIdx}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4" data-tooltip="${escapeAttr(p[3])}: ${p[2]}"></circle>`).join("");
    const labels = rows.map((d, i) => `<text class="axis-label" x="${xFor(i).toFixed(1)}" y="${height - 12}" text-anchor="middle">${escapeSvg(d[xKey])}</text>`).join("");
    el.innerHTML = svgWrap(width, height, `${defs()}${makeHorizontalGrid(margin, innerW, innerH, maxVal, "")}<g class="chart-drawn">${area}<path class="line-green chart-line" d="${path}"></path>${circles}</g>${labels}${makeAxis(margin, innerW, innerH)}`);
    attachTooltip(el);
  }

  function renderDonutChart(el, rows) {
    const measured = measureChart(el, 360, 220);
    const width = Math.max(300, measured.width);
    const height = 205;
    const cx = width / 2;
    const cy = 92;
    const r = Math.min(68, Math.max(54, width * 0.18));
    const stroke = Math.min(30, Math.max(22, r * 0.44));
    const nameKey = el.dataset.name || "level";
    const valueKey = el.dataset.y || "value";
    const total = rows.reduce((sum, d) => sum + (Number(d[valueKey]) || 0), 0) || 1;
    const palette = ["#55bea4", "#242b33", "#8ee1d1"];
    let start = -90;
    const arcs = rows.map((d, i) => {
      const label = d[nameKey] ?? "";
      const val = Number(d[valueKey]) || 0;
      const angle = (val / total) * 360;
      const arc = describeArc(cx, cy, r, start, start + Math.max(angle - 1.5, 0));
      start += angle;
      return `<path class="donut-arc" style="--i:${i}" d="${arc}" fill="none" stroke="${palette[i % palette.length]}" stroke-width="${stroke}" stroke-linecap="round" data-tooltip="${escapeAttr(label)}: ${val}"></path>`;
    }).join("");
    const legend = rows.map((d) => `<div><span>${escapeHtml(d[nameKey])}</span><b>${escapeHtml(d[valueKey])}</b></div>`).join("");
    el.innerHTML = `<div class="donut-grid">${svgWrap(width, height, arcs + `<circle class="donut-hole" cx="${cx}" cy="${cy}" r="${r - stroke / 2}" fill="#fff"></circle>`)}<div class="donut-legend">${legend}</div></div>`;
    attachTooltip(el);
  }

  function makeHorizontalGrid(margin, innerW, innerH, maxVal, unit) {
    const ticks = 4;
    let out = `<g class="chart-grid">`;
    for (let i = 0; i <= ticks; i++) {
      const y = margin.top + innerH - (i / ticks) * innerH;
      const value = (maxVal / ticks) * i;
      out += `<line x1="${margin.left}" y1="${y.toFixed(1)}" x2="${margin.left + innerW}" y2="${y.toFixed(1)}"></line>`;
      out += `<text class="axis-label" x="${margin.left - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end">${formatTick(value, unit)}</text>`;
    }
    return `${out}</g>`;
  }

  function makeVerticalGrid(margin, innerW, innerH, count) {
    let out = `<g class="chart-grid">`;
    for (let i = 0; i < count; i++) {
      const x = margin.left + (count === 1 ? innerW / 2 : (i / (count - 1)) * innerW);
      out += `<line x1="${x.toFixed(1)}" y1="${margin.top}" x2="${x.toFixed(1)}" y2="${margin.top + innerH}"></line>`;
    }
    return `${out}</g>`;
  }

  function makeAxis(margin, innerW, innerH) {
    return `<line class="axis-line" x1="${margin.left}" y1="${margin.top + innerH}" x2="${margin.left + innerW}" y2="${margin.top + innerH}"></line><line class="axis-line" x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + innerH}"></line>`;
  }

  function makeLegend(width, series) {
    const totalWidth = series.length * 170;
    let x = (width - totalWidth) / 2;
    return `<g>${series.map((s, i) => {
      const cls = i === 0 ? "green" : "dark";
      const item = `<circle class="${cls === "green" ? "bar-green" : ""}" cx="${x}" cy="10" r="7" fill="${cls === "green" ? "#64bdad" : "#242b33"}"></circle><text class="chart-title-label" x="${x + 12}" y="15">${escapeSvg(s.label)}</text>`;
      x += 170;
      return item;
    }).join("")}</g>`;
  }

  function measureChart(el, fallbackWidth, fallbackHeight) {
    const rect = el.getBoundingClientRect();
    const width = Math.round(rect.width || el.clientWidth || fallbackWidth);
    const height = Math.round(rect.height || el.clientHeight || fallbackHeight);
    return {
      width: Math.max(280, width),
      height: Math.max(190, height || fallbackHeight)
    };
  }

  function svgWrap(width, height, content) {
    return `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img">${content}</svg>`;
  }

  function defs() {
    return `<defs>
      <linearGradient id="areaFillGreen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5ec7ad" stop-opacity=".42"/><stop offset="1" stop-color="#5ec7ad" stop-opacity=".07"/></linearGradient>
      <linearGradient id="areaFillDark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#252d35" stop-opacity=".30"/><stop offset="1" stop-color="#252d35" stop-opacity=".05"/></linearGradient>
    </defs>`;
  }

  function smoothPath(points) {
    if (!points.length) return "";
    if (points.length === 1) return `M ${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
    let d = `M ${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
    for (let i = 0; i < points.length - 1; i++) {
      const p1 = points[i];
      const p2 = points[i + 1];
      const midX = (p1[0] + p2[0]) / 2;
      d += ` C ${midX.toFixed(1)} ${p1[1].toFixed(1)}, ${midX.toFixed(1)} ${p2[1].toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
    }
    return d;
  }

  function describeArc(cx, cy, r, startAngle, endAngle) {
    const start = polarToCartesian(cx, cy, r, endAngle);
    const end = polarToCartesian(cx, cy, r, startAngle);
    const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
    return [`M`, start.x, start.y, `A`, r, r, 0, largeArcFlag, 0, end.x, end.y].join(" ");
  }

  function polarToCartesian(cx, cy, r, angleInDegrees) {
    const angleInRadians = (angleInDegrees - 90) * Math.PI / 180.0;
    return { x: (cx + (r * Math.cos(angleInRadians))).toFixed(3), y: (cy + (r * Math.sin(angleInRadians))).toFixed(3) };
  }

  function attachTooltip(root) {
    const tooltip = document.getElementById("chart-tooltip");
    if (!tooltip) return;
    root.querySelectorAll("[data-tooltip]").forEach((node) => {
      node.addEventListener("mouseenter", (event) => {
        tooltip.textContent = event.currentTarget.dataset.tooltip;
        tooltip.hidden = false;
      });
      node.addEventListener("mousemove", (event) => {
        tooltip.style.left = `${event.clientX + 12}px`;
        tooltip.style.top = `${event.clientY + 12}px`;
      });
      node.addEventListener("mouseleave", () => {
        tooltip.hidden = true;
      });
    });
  }

  function formatInteger(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return Math.round(number).toLocaleString("ru-RU");
  }

  function getElementTarget(event) {
    const target = event.target;
    if (target instanceof Element) return target;
    return target?.parentElement || null;
  }

  function parseSeries(raw) {
    return raw.split(",").map((part) => {
      const [key, label] = part.split(":");
      return { key: key.trim(), label: (label || key).trim() };
    });
  }

  function normalizeForSort(value, key) {
    if (key === "date") {
      const parts = String(value).split(".");
      if (parts.length === 3) return `${parts[2]}-${parts[1]}-${parts[0]}`;
    }
    if (key === "time") return String(value || "");
    if (["cluster_id", "risk_level"].includes(key)) {
      const number = String(value ?? "").replace(/[^0-9.-]/g, "");
      if (number) return Number(number);
    }
    if (key === "amount") {
      const number = String(value ?? "").replace(/[^0-9,.-]/g, "").replace(",", ".");
      if (number) return Number(number);
    }
    const number = String(value ?? "").replace(/\D/g, "");
    if (["inn", "kpp"].includes(key) && number) return Number(number);
    return String(value ?? "").toLowerCase();
  }

  function downloadCsv(filename, rows, keys) {
    const header = keys.join(";");
    const body = (rows || []).map((row) => keys.map((key) => csvCell(row[key])).join(";")).join("\n");
    const blob = new Blob(["\ufeff" + header + "\n" + body], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function csvCell(value) {
    const text = String(value ?? "").replaceAll('"', '""');
    return /[;"\n]/.test(text) ? `"${text}"` : text;
  }

  function niceMax(value) {
    if (!Number.isFinite(value) || value <= 0) return 10;
    return Math.ceil(value / 4) * 4;
  }

  function formatTick(value, unit) {
    const rounded = Math.abs(value) >= 10 ? Math.round(value) : Number(value.toFixed(1));
    return `${rounded}${unit || ""}`;
  }

  function formatValue(value, unit) {
    const rounded = Math.abs(value) >= 10 ? Math.round(value) : Number(value.toFixed(1));
    return `${rounded}${unit || ""}`;
  }

  function shortLabel(value, length) {
    const text = String(value ?? "");
    return text.length > length ? text.slice(0, length - 1) + "…" : text;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeSvg(value) {
    return escapeHtml(value);
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function escapeClass(value) {
    return String(value ?? "").replace(/[^a-zA-Z0-9_-]/g, "");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  const observer = new MutationObserver(() => boot());
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
