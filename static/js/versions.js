// CloudVault file versions and restore handling (Stage 5, Part 1).
// Vanilla JavaScript only; minimal DOM manipulation and fetch API.
"use strict";

(function () {
    var section = document.querySelector("[data-versions-section]");
    if (!section) {
        return;
    }

    var fileId = section.getAttribute("data-file-id");
    if (!fileId) {
        return;
    }

    var listContainer = section.querySelector("[data-versions-list]");
    var statusEl = section.querySelector("[data-versions-status]");
    var countBadge = section.querySelector("[data-versions-count]");

    // Modal dialog elements
    var dialog = document.getElementById("restore-confirm-dialog");
    var dialogVersionNumber = dialog ? dialog.querySelector("[data-dialog-version-number]") : null;
    var dialogVersionDate = dialog ? dialog.querySelector("[data-dialog-version-date]") : null;
    var dialogConfirmBtn = dialog ? dialog.querySelector("[data-dialog-confirm]") : null;
    var dialogCancelBtn = dialog ? dialog.querySelector("[data-dialog-cancel]") : null;

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

    var pendingRestoreVersion = null;
    var isRestoring = false;

    function setStatus(message, state) {
        if (!statusEl) {
            return;
        }
        statusEl.textContent = message || "";
        if (state) {
            statusEl.setAttribute("data-state", state);
        } else {
            statusEl.removeAttribute("data-state");
        }
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
        if (!isoString) {
            return "—";
        }
        try {
            var d = new Date(isoString);
            if (isNaN(d.getTime())) {
                return isoString;
            }
            return d.toLocaleString();
        } catch (e) {
            return isoString;
        }
    }

    function createBadge(text, variant) {
        var span = document.createElement("span");
        span.className = "status-badge status-" + (variant || "neutral");
        span.textContent = text;
        return span;
    }

    function renderVersions(data) {
        var versions = (data && data.versions) || [];
        var currentFile = (data && data.file) || null;

        if (countBadge) {
            countBadge.textContent = versions.length + (versions.length === 1 ? " version" : " versions");
        }

        listContainer.innerHTML = "";

        if (versions.length === 0) {
            var empty = document.createElement("div");
            empty.className = "empty-state";
            var p = document.createElement("p");
            p.textContent = "No versions recorded for this file yet.";
            empty.appendChild(p);
            listContainer.appendChild(empty);
            return;
        }

        var tableWrap = document.createElement("div");
        tableWrap.className = "table-wrap";

        var table = document.createElement("table");
        table.className = "file-table versions-table";

        var thead = document.createElement("thead");
        thead.innerHTML =
            "<tr>" +
            "<th>Version</th>" +
            "<th>Original filename</th>" +
            "<th>Size</th>" +
            "<th>Created</th>" +
            "<th>Backup status</th>" +
            "<th>Verification</th>" +
            "<th>Action</th>" +
            "</tr>";
        table.appendChild(thead);

        var tbody = document.createElement("tbody");

        // The versions are typically sorted descending by version_number (latest first)
        versions.forEach(function (v, idx) {
            var tr = document.createElement("tr");

            var isCurrent = v.is_current === true || (idx === 0 && !versions.some(function (x) { return x.is_current; }));
            var isRestored = v.is_restored === true;

            if (isCurrent) {
                tr.classList.add("version-row-current");
            }

            // Version number + role badge
            var tdVersion = document.createElement("td");
            tdVersion.setAttribute("data-label", "Version");
            var vNumSpan = document.createElement("strong");
            vNumSpan.textContent = "v" + (v.version_number || "—");
            tdVersion.appendChild(vNumSpan);

            if (isCurrent) {
                var curBadge = createBadge("Current", "active");
                curBadge.classList.add("version-tag-pill");
                tdVersion.appendChild(curBadge);
            } else if (isRestored) {
                var resBadge = createBadge("Restored", "neutral");
                resBadge.classList.add("version-tag-pill");
                tdVersion.appendChild(resBadge);
            } else {
                var prevBadge = createBadge("Previous", "neutral");
                prevBadge.classList.add("version-tag-pill");
                tdVersion.appendChild(prevBadge);
            }
            tr.appendChild(tdVersion);

            // Filename
            var tdFilename = document.createElement("td");
            tdFilename.setAttribute("data-label", "Filename");
            tdFilename.textContent = v.original_filename || (currentFile && currentFile.original_filename) || "—";
            tr.appendChild(tdFilename);

            // Size
            var tdSize = document.createElement("td");
            tdSize.setAttribute("data-label", "Size");
            tdSize.className = "col-meta";
            tdSize.textContent = formatBytes(v.size_bytes);
            tr.appendChild(tdSize);

            // Created
            var tdCreated = document.createElement("td");
            tdCreated.setAttribute("data-label", "Created");
            tdCreated.className = "col-meta";
            tdCreated.textContent = formatDate(v.created_at);
            tr.appendChild(tdCreated);

            // Backup status
            var tdBackup = document.createElement("td");
            tdBackup.setAttribute("data-label", "Backup status");
            var backupStatus = (v.backup_status || (v.has_backup ? "backed_up" : "active") || "active").toLowerCase();
            if (backupStatus === "backed_up" || backupStatus === "stored" || backupStatus === "ok") {
                tdBackup.appendChild(createBadge("Backed up", "active"));
            } else {
                tdBackup.appendChild(createBadge(backupStatus, "neutral"));
            }
            tr.appendChild(tdBackup);

            // Checksum verification
            var tdChecksum = document.createElement("td");
            tdChecksum.setAttribute("data-label", "Verification");
            var chkStatus = (v.checksum_status || (v.checksum_sha256 ? "valid" : "unverified")).toLowerCase();
            if (chkStatus === "ok" || chkStatus === "valid" || chkStatus === "verified") {
                tdChecksum.appendChild(createBadge("Checksum OK", "active"));
            } else if (chkStatus === "damaged" || chkStatus === "mismatch") {
                tdChecksum.appendChild(createBadge("Damaged", "deleted"));
            } else if (chkStatus === "missing") {
                tdChecksum.appendChild(createBadge("Missing", "deleted"));
            } else {
                tdChecksum.appendChild(createBadge("Unverified", "neutral"));
            }
            tr.appendChild(tdChecksum);

            // Actions
            var tdActions = document.createElement("td");
            tdActions.setAttribute("data-label", "Action");

            if (isCurrent) {
                var curText = document.createElement("span");
                curText.className = "muted text-sm";
                curText.textContent = "Current version";
                tdActions.appendChild(curText);
            } else {
                var restoreBtn = document.createElement("button");
                restoreBtn.type = "button";
                restoreBtn.className = "btn btn-secondary btn-sm";
                restoreBtn.textContent = "Restore";
                restoreBtn.setAttribute("data-version-id", v.id || v.version_number);
                restoreBtn.setAttribute("data-version-number", v.version_number);
                restoreBtn.setAttribute("data-version-date", formatDate(v.created_at));

                restoreBtn.addEventListener("click", function () {
                    openRestoreDialog(v);
                });

                tdActions.appendChild(restoreBtn);
            }
            tr.appendChild(tdActions);

            tbody.appendChild(tr);
        });

        table.appendChild(tbody);
        tableWrap.appendChild(table);
        listContainer.appendChild(tableWrap);
    }

    function loadVersions() {
        setStatus("Loading version history...", null);

        fetch("/api/files/" + encodeURIComponent(fileId) + "/versions", {
            method: "GET",
            headers: {
                "Accept": "application/json"
            }
        })
        .then(function (res) {
            if (!res.ok) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    var msg = data.error || ("Failed to load versions (HTTP " + res.status + ").");
                    throw new Error(msg);
                });
            }
            return res.json();
        })
        .then(function (data) {
            setStatus("", null);
            renderVersions(data);
        })
        .catch(function (err) {
            setStatus(err.message || "Failed to load version history.", "error");
            if (countBadge) {
                countBadge.textContent = "Unavailable";
            }
        });
    }

    function openRestoreDialog(version) {
        pendingRestoreVersion = version;
        if (dialogVersionNumber) {
            dialogVersionNumber.textContent = "v" + (version.version_number || "—");
        }
        if (dialogVersionDate) {
            dialogVersionDate.textContent = formatDate(version.created_at);
        }

        if (dialog && typeof dialog.showModal === "function") {
            dialog.showModal();
        } else if (dialog) {
            dialog.setAttribute("open", "");
        } else {
            // Fallback confirmation if dialog element is unavailable
            var confirmed = window.confirm(
                "Restoring version v" + version.version_number + " will replace the current file contents.\n\nDo you want to proceed?"
            );
            if (confirmed) {
                performRestore(version);
            }
        }
    }

    function closeRestoreDialog() {
        if (!dialog) {
            return;
        }
        if (typeof dialog.close === "function") {
            dialog.close();
        } else {
            dialog.removeAttribute("open");
        }
        pendingRestoreVersion = null;
    }

    function performRestore(version) {
        if (isRestoring || !version) {
            return;
        }

        var versionId = version.id || version.version_number;
        var restoreUrl = "/api/files/" + encodeURIComponent(fileId) + "/versions/" + encodeURIComponent(versionId) + "/restore";

        isRestoring = true;
        setStatus("Restoring version v" + version.version_number + "...", "warn");

        if (dialogConfirmBtn) {
            dialogConfirmBtn.disabled = true;
            dialogConfirmBtn.textContent = "Restoring...";
        }
        if (dialogCancelBtn) {
            dialogCancelBtn.disabled = true;
        }

        fetch(restoreUrl, {
            method: "POST",
            headers: {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken
            }
        })
        .then(function (res) {
            if (!res.ok) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    var msg = data.error || ("Restore failed (HTTP " + res.status + ").");
                    throw new Error(msg);
                });
            }
            return res.json();
        })
        .then(function (data) {
            closeRestoreDialog();
            setStatus(data.message || "File version successfully restored. Reloading...", "success");
            // Reload page to update both main file detail table and version history
            window.setTimeout(function () {
                window.location.reload();
            }, 1000);
        })
        .catch(function (err) {
            setStatus(err.message || "Failed to restore file version.", "error");
        })
        .finally(function () {
            isRestoring = false;
            if (dialogConfirmBtn) {
                dialogConfirmBtn.disabled = false;
                dialogConfirmBtn.textContent = "Confirm Restore";
            }
            if (dialogCancelBtn) {
                dialogCancelBtn.disabled = false;
            }
        });
    }

    if (dialogConfirmBtn) {
        dialogConfirmBtn.addEventListener("click", function () {
            if (pendingRestoreVersion) {
                performRestore(pendingRestoreVersion);
            }
        });
    }

    if (dialogCancelBtn) {
        dialogCancelBtn.addEventListener("click", function () {
            closeRestoreDialog();
        });
    }

    if (dialog) {
        dialog.addEventListener("cancel", function (e) {
            if (isRestoring) {
                e.preventDefault();
            } else {
                pendingRestoreVersion = null;
            }
        });
    }

    // Initial load
    loadVersions();
})();
