// CloudVault Recycle Bin management (Stage 6, Part 1).
// Vanilla JavaScript only; minimal DOM manipulation and fetch API.
"use strict";

(function () {
    var page = document.querySelector("[data-recycle-bin-page]");
    if (!page) {
        return;
    }

    var loadingEl = page.querySelector("[data-loading-state]");
    var errorEl = page.querySelector("[data-error-state]");
    var errorTextEl = page.querySelector("[data-error-text]");
    var emptyEl = page.querySelector("[data-empty-state]");
    var tableWrapEl = page.querySelector("[data-table-wrap]");
    var tbodyEl = page.querySelector("[data-items-tbody]");
    var statusEl = page.querySelector("[data-status-message]");
    var purgeBtn = page.querySelector("[data-purge-btn]");
    var refreshBtn = page.querySelector("[data-refresh-btn]");
    var retryBtn = page.querySelector("[data-retry-btn]");

    // Dialog elements
    var dialog = document.getElementById("recycle-confirm-dialog");
    var dialogTitle = dialog ? dialog.querySelector("#recycle-dialog-title") : null;
    var dialogDesc = dialog ? dialog.querySelector("#recycle-dialog-desc") : null;
    var dialogWarning = dialog ? dialog.querySelector("[data-dialog-warning]") : null;
    var dialogWarningText = dialog ? dialog.querySelector("[data-dialog-warning-text]") : null;
    var dialogConfirmBtn = dialog ? dialog.querySelector("[data-dialog-confirm]") : null;
    var dialogCancelBtn = dialog ? dialog.querySelector("[data-dialog-cancel]") : null;

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

    var pendingAction = null; // { type: 'restore' | 'delete_permanent' | 'purge', file?: object }
    var isBusy = false;

    function setStatus(message, type) {
        if (!statusEl) return;
        if (!message) {
            statusEl.style.display = "none";
            statusEl.textContent = "";
            statusEl.className = "recycle-status-message";
            return;
        }
        statusEl.textContent = message;
        statusEl.className = "recycle-status-message status-" + (type || "info");
        statusEl.style.display = "block";
    }

    function formatBytes(bytes) {
        if (typeof bytes !== "number" || isNaN(bytes) || bytes < 0) {
            return "0 B";
        }
        if (bytes === 0) {
            return "0 B";
        }
        var k = 1024;
        var sizes = ["B", "KB", "MB", "GB", "TB"];
        var i = Math.floor(Math.log(bytes) / Math.log(k));
        if (i >= sizes.length) {
            i = sizes.length - 1;
        }
        var val = bytes / Math.pow(k, i);
        return (i === 0 ? val : val.toFixed(1)) + " " + sizes[i];
    }

    function formatDate(isoString) {
        if (!isoString) return "—";
        try {
            var d = new Date(isoString);
            if (isNaN(d.getTime())) return isoString;
            return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) {
            return isoString;
        }
    }

    function computeRetentionInfo(deletedAtIso) {
        if (!deletedAtIso) return { days: 30, text: "30 days left", isExpired: false };
        try {
            var deletedDate = new Date(deletedAtIso);
            if (isNaN(deletedDate.getTime())) return { days: 30, text: "30 days left", isExpired: false };

            // 30 days retention policy: retention deadline is deleted_at + 30 days
            var expireDate = new Date(deletedDate.getTime() + (30 * 24 * 60 * 60 * 1000));
            var now = new Date();
            var diffMs = expireDate.getTime() - now.getTime();

            if (diffMs <= 0) {
                return { days: 0, text: "Expired (> 30 days)", isExpired: true };
            }
            var days = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
            var hours = Math.ceil(diffMs / (1000 * 60 * 60));
            if (days > 1) {
                return { days: days, text: days + " days remaining", isExpired: false };
            } else if (hours > 1) {
                return { days: 1, text: hours + " hours remaining", isExpired: false };
            } else {
                return { days: 0, text: "Less than 1 hr left", isExpired: false };
            }
        } catch (e) {
            return { days: 30, text: "30 days left", isExpired: false };
        }
    }

    function openConfirmModal(config) {
        pendingAction = config;
        if (!dialog) {
            if (window.confirm(config.title + "\n\n" + config.description)) {
                executePendingAction();
            }
            return;
        }

        if (dialogTitle) dialogTitle.textContent = config.title;
        if (dialogDesc) dialogDesc.textContent = config.description;
        if (dialogWarning && dialogWarningText) {
            if (config.warning) {
                dialogWarning.style.display = "block";
                dialogWarningText.textContent = config.warning;
            } else {
                dialogWarning.style.display = "none";
            }
        }

        if (dialogConfirmBtn) {
            dialogConfirmBtn.textContent = config.confirmText || "Confirm";
            dialogConfirmBtn.className = "btn " + (config.btnClass || "btn-danger");
        }

        if (typeof dialog.showModal === "function") {
            dialog.showModal();
        } else {
            dialog.setAttribute("open", "true");
        }
    }

    function closeConfirmModal() {
        pendingAction = null;
        if (!dialog) return;
        if (typeof dialog.close === "function") {
            dialog.close();
        } else {
            dialog.removeAttribute("open");
        }
    }

    if (dialogCancelBtn) {
        dialogCancelBtn.addEventListener("click", closeConfirmModal);
    }

    if (dialogConfirmBtn) {
        dialogConfirmBtn.addEventListener("click", function () {
            executePendingAction();
        });
    }

    function loadRecycleBin() {
        if (isBusy) return;
        loadingEl.style.display = "block";
        errorEl.style.display = "none";
        emptyEl.style.display = "none";
        tableWrapEl.style.display = "none";
        if (purgeBtn) purgeBtn.style.display = "none";

        fetch("/api/recycle-bin", {
            headers: { "Accept": "application/json" }
        })
        .then(function (res) {
            if (res.status === 401) {
                return res.json().then(function (body) {
                    throw new Error(body.error || "Authentication required. Please log in.");
                }).catch(function (err) {
                    throw new Error(err.message || "Authentication required. Please log in.");
                });
            }
            if (!res.ok) {
                throw new Error("HTTP error " + res.status);
            }
            return res.json();
        })
        .then(function (data) {
            loadingEl.style.display = "none";
            renderRecycleBin(data);
        })
        .catch(function (err) {
            loadingEl.style.display = "none";
            errorEl.style.display = "block";
            if (errorTextEl) {
                errorTextEl.textContent = "Could not load recycle bin: " + (err.message || "Network error");
            }
        });
    }

    function renderRecycleBin(data) {
        var files = (data && (data.files || data.items)) || [];
        tbodyEl.innerHTML = "";

        if (files.length === 0) {
            emptyEl.style.display = "block";
            tableWrapEl.style.display = "none";
            if (purgeBtn) purgeBtn.style.display = "none";
            return;
        }

        files.forEach(function (file) {
            var tr = document.createElement("tr");

            // This countdown is informational only. The server decides purge eligibility.
            var retention = computeRetentionInfo(file.deleted_at);

            // Filename
            var tdName = document.createElement("td");
            tdName.setAttribute("data-label", "Filename");
            var nameSpan = document.createElement("span");
            nameSpan.className = "item-name";
            nameSpan.textContent = file.original_filename || "Untitled File";
            tdName.appendChild(nameSpan);

            // MIME Type
            var tdType = document.createElement("td");
            tdType.setAttribute("data-label", "MIME Type");
            tdType.className = "col-meta";
            tdType.textContent = file.mime_type || "application/octet-stream";

            // Size
            var tdSize = document.createElement("td");
            tdSize.setAttribute("data-label", "Size");
            tdSize.className = "col-meta";
            tdSize.textContent = formatBytes(file.size_bytes);

            // Deleted Date
            var tdDeleted = document.createElement("td");
            tdDeleted.setAttribute("data-label", "Deleted date");
            tdDeleted.textContent = formatDate(file.deleted_at);

            // Retention Remaining
            var tdRemaining = document.createElement("td");
            tdRemaining.setAttribute("data-label", "Retention remaining");
            var remBadge = document.createElement("span");
            remBadge.className = "status-badge status-" + (retention.isExpired ? "error" : (retention.days <= 3 ? "warning" : "neutral"));
            remBadge.textContent = retention.text;
            tdRemaining.appendChild(remBadge);

            // Actions (Restore and Permanent Delete)
            var tdActions = document.createElement("td");
            tdActions.setAttribute("data-label", "Actions");
            var actionGroup = document.createElement("div");
            actionGroup.className = "action-buttons-group";

            var restoreBtn = document.createElement("button");
            restoreBtn.type = "button";
            restoreBtn.className = "btn btn-secondary btn-sm";
            restoreBtn.textContent = "Restore";
            restoreBtn.setAttribute("aria-label", "Restore " + (file.original_filename || "file"));
            restoreBtn.addEventListener("click", function () {
                promptRestore(file);
            });

            var deleteBtn = document.createElement("button");
            deleteBtn.type = "button";
            deleteBtn.className = "btn btn-danger btn-sm";
            deleteBtn.textContent = "Delete permanently";
            deleteBtn.setAttribute("aria-label", "Permanently delete " + (file.original_filename || "file"));
            deleteBtn.addEventListener("click", function () {
                promptPermanentDelete(file);
            });

            actionGroup.appendChild(restoreBtn);
            actionGroup.appendChild(deleteBtn);
            tdActions.appendChild(actionGroup);

            tr.appendChild(tdName);
            tr.appendChild(tdType);
            tr.appendChild(tdSize);
            tr.appendChild(tdDeleted);
            tr.appendChild(tdRemaining);
            tr.appendChild(tdActions);

            tbodyEl.appendChild(tr);
        });

        tableWrapEl.style.display = "block";
        // Show the action whenever deleted files exist; the server enforces the 30-day rule.
        if (purgeBtn) {
            purgeBtn.style.display = "inline-block";
        }
    }

    function promptRestore(file) {
        var name = file.original_filename || "this file";
        openConfirmModal({
            type: "restore",
            file: file,
            title: "Restore File",
            description: "Are you sure you want to restore '" + name + "' to your active files?",
            warning: null,
            confirmText: "Restore File",
            btnClass: "btn-primary"
        });
    }

    function promptPermanentDelete(file) {
        var name = file.original_filename || "this file";
        openConfirmModal({
            type: "delete_permanent",
            file: file,
            title: "Permanently Delete File",
            description: "Are you sure you want to permanently delete '" + name + "'?",
            warning: "This file and all its versions will be completely deleted from storage. This action cannot be undone, and its storage space will be deducted from your used quota.",
            confirmText: "Delete Permanently",
            btnClass: "btn-danger"
        });
    }

    function promptPurge() {
        openConfirmModal({
            type: "purge",
            title: "Purge Expired Files",
            description: "Purge all files in the recycle bin that have been deleted for more than 30 days?",
            warning: "All files deleted more than 30 days ago will be permanently removed from disk storage. This cannot be undone.",
            confirmText: "Purge All Expired",
            btnClass: "btn-danger"
        });
    }

    function executePendingAction() {
        if (!pendingAction || isBusy) return;
        var act = pendingAction;
        closeConfirmModal();

        isBusy = true;
        setStatus("Processing...", "info");

        var endpoint = "";
        var method = "POST";
        var payload = {};

        if (act.type === "restore") {
            endpoint = "/api/recycle-bin/files/" + act.file.id + "/restore";
        } else if (act.type === "delete_permanent") {
            endpoint = "/api/recycle-bin/files/" + act.file.id + "/delete";
        } else if (act.type === "purge") {
            endpoint = "/api/recycle-bin/purge";
        }

        fetch(endpoint, {
            method: method,
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken,
                "Accept": "application/json"
            },
            body: JSON.stringify(payload)
        })
        .then(function (res) {
            return res.json().then(function (body) {
                if (!res.ok) {
                    throw new Error(body.error || "Action failed (HTTP " + res.status + ")");
                }
                return body;
            });
        })
        .then(function (data) {
            isBusy = false;
            setStatus(data.message || "Action completed successfully.", "success");
            loadRecycleBin();
        })
        .catch(function (err) {
            isBusy = false;
            setStatus(err.message || "An unexpected error occurred.", "error");
        });
    }

    if (purgeBtn) {
        purgeBtn.addEventListener("click", promptPurge);
    }

    if (refreshBtn) {
        refreshBtn.addEventListener("click", function () {
            setStatus("", null);
            loadRecycleBin();
        });
    }

    if (retryBtn) {
        retryBtn.addEventListener("click", loadRecycleBin);
    }

    // Initial load
    loadRecycleBin();
})();
