(function () {
  const DEFAULT_DATA = window.__FINANCE_DASHBOARD_DATA__ || {};

  function boot() {
    const app = document.getElementById("finance-app");
    if (!app || app.dataset.ready === "1") return;
    app.dataset.ready = "1";

    const data = normalizeData(DEFAULT_DATA);
    window.financeDashboardApp = { data, rerender: () => renderAll(app, window.financeDashboardApp.data) };

    initNavigation(app);
    initStages(app);
    initUploads(app, data);
    initBackendStatusMonitor(app);
    initSmartTable(app, data);
    initDownloads(app, data);
    initModal(app, data);
    renderAll(app, data);
    initCountups(app);
    initRevealAnimations(app);
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
        legalReport: [],
        counterpartyRegistry: [],
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
      legalReport: Array.isArray(raw.legalReport) ? raw.legalReport : [],
      counterpartyRegistry: Array.isArray(raw.counterpartyRegistry) ? raw.counterpartyRegistry : [],
      modal: raw.modal || {}
    };
  }

  function renderAll(app, data) {
    applyAnalysisState(app, data);
    renderSummary(app, data.summary || {}, data.statementSummary || {}, data.outputSummary || {});
    renderDocuments(app, data.documents);
    renderSignals(app, data.signals);
    renderRegistry(app, data.counterpartyRegistry);
    renderLegalReport(app, data.legalReport);
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
        setTimeout(() => {
          renderCharts(app, window.financeDashboardApp?.data?.charts || {});
          initCountups(app);
          initRevealAnimations(app);
        }, 30);
      });
    });
  }

  function initStages(app) {
    const stageContent = {
      import: {
        title: "Что делает Агент на шаге импорта",
        bullets: [
          "Считывает формат и выравнивает колонки выписки, чтобы не терялась структура.",
          "Находит потенциальные ошибки в данных до запуска аналитики.",
          "Подготавливает быстрый предпросмотр, чтобы подтвердить корректность файла."
        ]
      },
      analysis: {
        title: "Что делает Агент на шаге анализа транзакций",
        bullets: [
          "Нормализует назначения платежей и приводит суммы к единому формату.",
          "Классифицирует операции по типам: поступления, списания, налоги, зарплата, подрядчики.",
          "Считает распределения, резкие отклонения и повторяющиеся денежные паттерны."
        ]
      },
      counterparties: {
        title: "Как оцениваем контрагентов",
        bullets: [
          "Отслеживаем динамику взаимодействий и изменения в поведении.",
          "Считаем риск-профиль на основе открытых источников и внутренних тегов.",
          "Выделяем сигналы и события, которые требуют внимания аналитика."
        ]
      },
      legal: {
        title: "Формирование юридического отчета",
        bullets: [
          "Собираем обязательства, дедлайны и статусы документов.",
          "Присваиваем уровень риска и подготавливаем рекомендации по действиям.",
          "Готовим материалы для юридического отдела и службы безопасности."
        ]
      }
    };

    function paintStageProgress(card, activeIndex) {
      const items = Array.from(card.querySelectorAll(".stage-item"));
      const lines = Array.from(card.querySelectorAll(".stage-line"));
      items.forEach((item, index) => {
        item.classList.toggle("done", index < activeIndex);
        item.classList.toggle("active", index === activeIndex);
        item.classList.toggle("current", index === activeIndex);
        item.classList.toggle("muted", index > activeIndex);
        item.setAttribute("aria-current", index === activeIndex ? "step" : "false");
      });
      lines.forEach((line, index) => {
        line.classList.toggle("done", index < activeIndex);
        line.classList.toggle("pending", index >= activeIndex);
      });
      card.style.setProperty("--progress", `${items.length > 1 ? (activeIndex / (items.length - 1)) * 100 : 0}%`);
    }

    app.querySelectorAll(".stages-card").forEach((card) => {
      const items = Array.from(card.querySelectorAll(".stage-item"));
      if (!items.length) return;
      items.forEach((item, index) => {
        item.dataset.stageIndex = String(index);
        if (item.tagName !== "BUTTON") {
          item.setAttribute("role", "button");
          item.setAttribute("tabindex", "0");
        }
        const activate = () => {
          paintStageProgress(card, index);
          const stageKey = item.dataset.stage;
          const stage = stageKey ? stageContent[stageKey] : null;
          const stageTitle = app.querySelector("#stage-text-card h2");
          const stageBullets = app.querySelector("#stage-bullets");
          if (stage && stageTitle && stageBullets) {
            stageTitle.textContent = stage.title;
            stageBullets.innerHTML = stage.bullets.map((text) => `<li>${escapeHtml(text)}</li>`).join("");
          }
        };
        item.addEventListener("click", activate);
        item.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            activate();
          }
        });
      });
      const initialIndex = Math.max(0, items.findIndex((item) => item.classList.contains("active") || item.classList.contains("current")));
      paintStageProgress(card, initialIndex);
    });
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
          initCountups(app);
          initRevealAnimations(app);
          showAnalysisPreview(app, readBridgeText("agent-run-preview"), readBridgeText("agent-run-status"));
        } catch (error) {
          console.warn("Не удалось применить dashboard_payload из backend", error);
        }
      }

      if (text && text !== lastStatus) {
        lastStatus = text;
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

  let agentLoadingTimer = null;
  let agentLoadingProgress = 0;
  let agentLoadingFinalized = false;

  function openAgentLoadingModal(app, message, isWarning) {
    const modal = app.querySelector("#agent-loading-modal");
    const text = app.querySelector("#agent-loading-text");
    const fill = app.querySelector("#agent-progress-fill");
    const percent = app.querySelector("#agent-progress-percent");
    if (!modal || !text || !fill || !percent) return;

    agentLoadingFinalized = false;
    agentLoadingProgress = isWarning ? 100 : 4;
    text.textContent = message || "Инициализируем агента.";
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    modal.classList.toggle("warning", Boolean(isWarning));
    fill.style.width = `${agentLoadingProgress}%`;
    percent.textContent = `${Math.round(agentLoadingProgress)}%`;

    if (agentLoadingTimer) window.clearInterval(agentLoadingTimer);
    if (isWarning) return;

    const phrases = [
      "Загружаем файл в runtime агента.",
      "Читаем Excel/CSV и проверяем структуру.",
      "Приводим данные к контракту тетрадки.",
      "Формируем таблицы, графики и KPI.",
      "Ожидаем завершения backend pipeline."
    ];
    let phraseIndex = 0;

    agentLoadingTimer = window.setInterval(() => {
      if (agentLoadingFinalized) return;
      agentLoadingProgress = Math.min(92, agentLoadingProgress + Math.max(0.6, (92 - agentLoadingProgress) * 0.075));
      fill.style.width = `${agentLoadingProgress}%`;
      percent.textContent = `${Math.round(agentLoadingProgress)}%`;
      if (Math.round(agentLoadingProgress) % 12 === 0) {
        phraseIndex = Math.min(phrases.length - 1, phraseIndex + 1);
        text.textContent = phrases[phraseIndex];
      }
    }, 260);
  }

  function setAgentLoadingComplete(app, message) {
    if (agentLoadingFinalized) return;
    agentLoadingFinalized = true;
    const modal = app.querySelector("#agent-loading-modal");
    const text = app.querySelector("#agent-loading-text");
    const fill = app.querySelector("#agent-progress-fill");
    const percent = app.querySelector("#agent-progress-percent");
    if (agentLoadingTimer) window.clearInterval(agentLoadingTimer);
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
    if (agentLoadingTimer) window.clearInterval(agentLoadingTimer);
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
    if (agentLoadingTimer) window.clearInterval(agentLoadingTimer);
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

    render();
  }

  function initDownloads(app, data) {
    app.querySelector("#download-transactions")?.addEventListener("click", () => {
      const rows = window.financeDashboardApp?.getFilteredTransactions?.() || data.transactions || [];
      downloadCsv("transactions.csv", rows, ["idx", "date", "cluster_id", "amount", "transaction_category", "counterparty", "inn", "risk_level", "connection_basis", "legal_qualification", "challenge_readiness", "recommendation"]);
    });

    app.querySelector("#download-legal")?.addEventListener("click", () => {
      downloadCsv("legal_report.csv", data.legalReport || [], ["sum", "operations", "risk"]);
    });
  }

  function initModal(app, data) {
    const modal = app.querySelector("#detail-modal");
    const modalTitle = app.querySelector("#modal-title");
    const modalText = app.querySelector("#modal-text");
    if (!modal || !modalTitle || !modalText) return;

    function closeModal() {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
    }

    function openModal(detailKey) {
      const fallback = data.modal.low || { title: "Детализация риска", text: "Нет данных для выбранного блока." };
      const item = data.modal[detailKey] || fallback;
      modalTitle.textContent = item.title || fallback.title;
      modalText.textContent = item.text || fallback.text;
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
      app.querySelector("#modal-close")?.focus();
    }

    app.addEventListener("click", (event) => {
      const target = getElementTarget(event);
      const detailBtn = target?.closest(".details-btn[data-detail]");
      if (!detailBtn || !app.contains(detailBtn)) return;
      event.preventDefault();
      openModal(detailBtn.dataset.detail);
    });

    app.querySelectorAll("[data-modal-close]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        closeModal();
      });
    });

    modal.addEventListener("click", (event) => {
      if (getElementTarget(event) === modal) closeModal();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) closeModal();
    });
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

    const metric = app.querySelector(".metric-card strong");
    if (metric) {
      metric.textContent = summary.riskAmount || out.riskAmount || metric.textContent || "0 ₽";
      metric.dataset.finalText = metric.textContent;
      metric.dataset.countupLocked = "1";
    }
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
    list.innerHTML = (signals || []).map((signal) => `
      <div class="signal-pill ${escapeClass(signal.className)}">
        <span>${escapeHtml(signal.label)}</span><b>${escapeHtml(signal.count)}</b>
      </div>`).join("");
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
      </tr>`).join("");
  }

  function renderLegalReport(app, rows) {
    const tbody = app.querySelector("#legal-table tbody");
    if (!tbody) return;
    tbody.innerHTML = (rows || []).map((row) => `
      <tr>
        <td title="${escapeAttr(row.sum)}">${escapeHtml(row.sum)}</td>
        <td title="${escapeAttr(row.operations)}">${escapeHtml(row.operations)}</td>
        <td><span class="badge ${escapeClass(row.riskClass)}">${escapeHtml(row.risk)}</span></td>
        <td><button class="details-btn" type="button" data-detail="${escapeClass(row.detail)}">Подробнее</button></td>
      </tr>`).join("");
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
    animateChart(el);
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
    const useRevealClip = !withArea;
    const clipId = uniqueChartId("lineClip");
    const clip = useRevealClip ? `<clipPath id="${clipId}"><rect class="chart-reveal-clip" x="${margin.left}" y="0" width="0" height="${height}" data-final-width="${innerW}" data-duration="1650"></rect></clipPath>` : "";
    const drawn = series.map((s, sIdx) => {
      const points = rows.map((d, i) => [xFor(i), yFor(d[s.key]), Number(d[s.key]) || 0, d[xKey]]);
      const path = smoothPath(points);
      const area = withArea && sIdx === 0 ? `<path class="area-green chart-area" d="${path} L ${points[points.length - 1][0].toFixed(1)} ${margin.top + innerH} L ${points[0][0].toFixed(1)} ${margin.top + innerH} Z"></path>` : "";
      const cls = sIdx === 0 ? "green" : "dark";
      const circles = points.map((p, pIdx) => `<circle class="point-${cls} chart-point" style="--i:${pIdx + sIdx}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4.2" data-tooltip="${escapeAttr(p[3])}: ${escapeAttr(s.label)} — ${formatValue(p[2], unit)}"></circle>`).join("");
      return `<g class="chart-series chart-series-${sIdx}">${area}<path class="line-${cls} chart-line" d="${path}"></path>${circles}</g>`;
    }).join("");
    const drawnGroup = useRevealClip ? `<g class="chart-drawn" clip-path="url(#${clipId})">${drawn}</g>` : `<g class="chart-drawn">${drawn}</g>`;
    el.innerHTML = svgWrap(width, height, `${defs()}${clip}${grid}${legend}${drawnGroup}${labels}${makeAxis(margin, innerW, innerH)}`);
    attachTooltip(el);
    animateChart(el);
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
    const clipId = uniqueChartId("areaClip");
    const clip = `<clipPath id="${clipId}"><rect class="chart-reveal-clip" x="${margin.left}" y="0" width="0" height="${height}" data-final-width="${innerW}" data-duration="1500"></rect></clipPath>`;
    const area = `<path class="area-green chart-area" d="${path} L ${points[points.length - 1][0].toFixed(1)} ${margin.top + innerH} L ${points[0][0].toFixed(1)} ${margin.top + innerH} Z"></path>`;
    const circles = points.map((p, pIdx) => `<circle class="point-green chart-point" style="--i:${pIdx}" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4" data-tooltip="${escapeAttr(p[3])}: ${p[2]}"></circle>`).join("");
    const labels = rows.map((d, i) => `<text class="axis-label" x="${xFor(i).toFixed(1)}" y="${height - 12}" text-anchor="middle">${escapeSvg(d[xKey])}</text>`).join("");
    el.innerHTML = svgWrap(width, height, `${defs()}${clip}${makeHorizontalGrid(margin, innerW, innerH, maxVal, "")}<g class="chart-drawn" clip-path="url(#${clipId})">${area}<path class="line-green chart-line" d="${path}"></path>${circles}</g>${labels}${makeAxis(margin, innerW, innerH)}`);
    attachTooltip(el);
    animateChart(el);
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
    animateChart(el);
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

  let chartObserver = null;
  let countupObserver = null;
  let revealObserver = null;

  function initRevealAnimations(app) {
    const nodes = Array.from(app.querySelectorAll(".page.active .card, .page.active .two-col-grid"));
    nodes.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      node.classList.add("reveal-item", "reveal-visible");
      node.style.removeProperty("--reveal-delay");
      queueNestedAnimations(node, 0);
    });
  }

  function getRevealObserver() {
    return null;
  }

  function queueNestedAnimations(container, delay = 0) {
    if (!(container instanceof HTMLElement)) return;
    const charts = Array.from(container.querySelectorAll(".chart-shell"));
    charts.forEach((chart) => {
      if (chart instanceof HTMLElement) startChartAnimation(chart);
    });

    const numbers = Array.from(container.querySelectorAll("[data-countup-prepared=\"1\"]"));
    numbers.forEach((node) => {
      if (node instanceof HTMLElement) animateCountup(node);
    });
  }

  function getChartObserver() {
    return null;
  }

  function animateChart(root) {
    if (!(root instanceof HTMLElement)) return;
    root.dataset.animationPrepared = "1";
    root.dataset.chartAnimated = "1";
    preparePathDraw(root);
    startChartAnimation(root);
  }

  function preparePathDraw(root) {
    root.querySelectorAll(".chart-reveal-clip").forEach((clip) => {
      const finalWidth = Number(clip.dataset.finalWidth || clip.getAttribute("width") || 0);
      clip.dataset.finalWidth = String(finalWidth);
      clip.setAttribute("width", String(finalWidth));
    });
    root.querySelectorAll(".chart-line, .donut-arc").forEach((path, i) => {
      if (path.style) {
        path.style.setProperty("stroke-dasharray", "none", "important");
        path.style.setProperty("stroke-dashoffset", "0", "important");
        path.style.setProperty("--i", String(i));
      }
    });
    root.classList.add("chart-ready");
  }

  function startChartAnimation(root) {
    if (!(root instanceof HTMLElement)) return;
    root.dataset.chartAnimated = "1";
    root.querySelectorAll(".chart-reveal-clip").forEach((clip) => {
      clip.setAttribute("width", clip.dataset.finalWidth || clip.getAttribute("width") || "0");
      clip.dataset.clipAnimated = "1";
    });
    root.querySelectorAll(".chart-line, .donut-arc").forEach((path) => {
      path.style.setProperty("stroke-dasharray", "none", "important");
      path.style.setProperty("stroke-dashoffset", "0", "important");
    });
    root.classList.add("chart-ready");
  }

  function initCountups(app) {
    const nodes = app.querySelectorAll("[data-countup], .metric-card strong");
    nodes.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      if (!node.dataset.finalText || node.dataset.countupLocked !== "1") node.dataset.finalText = node.textContent.trim();
      const parsed = parseCountupTarget(node);
      if (!parsed) return;
      node.dataset.countupValue = String(parsed.value);
      node.dataset.countupPrefix = parsed.prefix;
      node.dataset.countupSuffix = parsed.suffix;
      node.dataset.countupDecimals = String(parsed.decimals);
      node.dataset.countupPrepared = "1";
      node.dataset.countupLocked = "1";
      node.textContent = `${parsed.prefix}${formatCountupNumber(parsed.value, parsed.decimals)}${parsed.suffix}`;
      node.dataset.countupAnimated = "1";
    });
  }

  function parseCountupTarget(node) {
    const text = node.dataset.finalText || node.textContent || "";
    const valueFromData = node.dataset.value ? Number(String(node.dataset.value).replace(",", ".")) : NaN;
    if (Number.isFinite(valueFromData)) {
      return {
        value: valueFromData,
        prefix: node.dataset.prefix || "",
        suffix: node.dataset.suffix || "",
        decimals: Number(node.dataset.decimals ?? (String(node.dataset.value).includes(".") || String(node.dataset.value).includes(",") ? 1 : 0)) || 0
      };
    }
    const match = text.match(/(-?\d+(?:[\s\u00a0]?\d{3})*(?:[,.]\d+)?|-?\d+(?:[,.]\d+)?)/);
    if (!match) return null;
    const rawNumber = match[0];
    const numeric = Number(rawNumber.replace(/[\s\u00a0]/g, "").replace(",", "."));
    if (!Number.isFinite(numeric)) return null;
    return {
      value: numeric,
      prefix: text.slice(0, match.index),
      suffix: text.slice((match.index || 0) + rawNumber.length),
      decimals: rawNumber.includes(",") || rawNumber.includes(".") ? rawNumber.split(/[,.]/)[1].length : 0
    };
  }

  function animateCountup(node) {
    if (!(node instanceof HTMLElement)) return;
    const value = Number(node.dataset.countupValue || 0);
    const prefix = node.dataset.countupPrefix || "";
    const suffix = node.dataset.countupSuffix || "";
    const decimals = Number(node.dataset.countupDecimals || 0);
    node.textContent = `${prefix}${formatCountupNumber(value, decimals)}${suffix}`;
    node.dataset.countupAnimated = "1";
  }

  function formatCountupNumber(value, decimals) {
    return Number(value).toFixed(decimals).replace(".", ",");
  }

  function formatInteger(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return Math.round(number).toLocaleString("ru-RU");
  }

  function animateClipRects(root) {
    root.querySelectorAll(".chart-reveal-clip").forEach((clip) => {
      const targetWidth = Number(clip.dataset.finalWidth || 0);
      if (Number.isFinite(targetWidth) && targetWidth > 0) clip.setAttribute("width", String(targetWidth));
      clip.dataset.clipAnimated = "1";
    });
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function uniqueChartId(prefix) {
    uniqueChartId.counter = (uniqueChartId.counter || 0) + 1;
    return `${prefix}-${Date.now().toString(36)}-${uniqueChartId.counter}`;
  }

  function isElementActuallyVisible(el) {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
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
  // Fixed10: low-end stable mode. Animations are intentionally disabled; all elements render immediately.
})();
