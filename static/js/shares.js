// CloudVault Shared Links management (Stage 6, Part 1).
// Vanilla JavaScript only; minimal DOM manipulation and fetch API.
"use strict";

(function () {
    var page = document.querySelector("[data-shares-page]");
    if (!page) {
        return;
    }

    var form = page.querySelector("[data-create-share-form]");
    var fileSelect = page.querySelector("[data-share-file-select]");
    var expiryPreset = page.querySelector("[data-expiry-preset]");
    var customExpiryWrap = page.querySelector("[data-custom-expiry-wrap]");
    var customExpiryInput = page.querySelector("[data-custom-expiry-input]");
    var submitBtn = page.querySelector("[data-create-submit-btn]");

    var loadingEl = page.querySelector("[data-shares-loading]");
    var errorEl = page.querySelector("[data-shares-error]");
    var errorTextEl = page.querySelector("[data-shares-error-text]");
    var emptyEl = page.querySelector("[data-shares-empty]");
    var tableWrapEl = page.querySelector("[data-shares-table-wrap]");
    var tbodyEl = page.querySelector("[data-shares-tbody]");
    var statusEl = page.querySelector("[data-shares-status]");
    var refreshBtn = page.querySelector("[data-refresh-shares-btn]");
    var retryBtn = page.querySelector("[data-shares-retry-btn]");

    // Dialog elements
    var dialog = document.getElementById("share-confirm-dialog");
    var dialogConfirmBtn = dialog ? dialog.querySelector("[data-dialog-confirm]") : null;
    var dialogCancelBtn = dialog ? dialog.querySelector("[data-dialog-cancel]") : null;

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

    var pendingRevokeShare = null;
    var isBusy = false;

    function setStatus(message, type) {
        if (!statusEl) return;
        if (!message) {
            statusEl.style.display = "none";
            statusEl.textContent = "";
            statusEl.className = "shares-status-message";
            return;
        }
        statusEl.textContent = message;
        statusEl.className = "shares-status-message status-" + (type || "info");
        statusEl.style.display = "block";
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

    function determineStatus(share) {
        if (share.revoked) {
            return { key: "revoked", label: "Revoked", badgeClass: "status-error" };
        }
        if (share.is_expired) {
            return { key: "expired", label: "Expired", badgeClass: "status-warning" };
        }
        try {
            var expiry = new Date(share.expires_at);
            if (!isNaN(expiry.getTime()) && expiry <= new Date()) {
                return { key: "expired", label: "Expired", badgeClass: "status-warning" };
            }
        } catch (e) {}
        return { key: "active", label: "Active", badgeClass: "status-success" };
    }

    function calculateExpiresAtUTC(preset, customValue) {
        var now = new Date();
        if (preset === "1h") {
            return new Date(now.getTime() + 1 * 60 * 60 * 1000).toISOString();
        } else if (preset === "24h") {
            return new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
        } else if (preset === "7d") {
            return new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000).toISOString();
        } else if (preset === "30d") {
            return new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString();
        } else if (preset === "custom") {
            if (!customValue) return null;
            var parsed = new Date(customValue);
            if (isNaN(parsed.getTime())) return null;
            // new Date(...).toISOString() always produces UTC ISO-8601 string: YYYY-MM-DDTHH:mm:ss.sssZ
            return parsed.toISOString();
        }
        return new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
    }

    // Toggle custom expiration input
    if (expiryPreset) {
        expiryPreset.addEventListener("change", function () {
            if (expiryPreset.value === "custom") {
                if (customExpiryWrap) customExpiryWrap.style.display = "block";
                if (customExpiryInput) {
                    customExpiryInput.required = true;
                    // Default to 1 day from now
                    var defaultDate = new Date(Date.now() + 24 * 60 * 60 * 1000);
                    // Format for datetime-local: YYYY-MM-DDThh:mm
                    var pad = function(n) { return n < 10 ? '0' + n : n; };
                    customExpiryInput.min = new Date().toISOString().slice(0, 16);
                    customExpiryInput.value = defaultDate.getFullYear() + '-' +
                        pad(defaultDate.getMonth() + 1) + '-' +
                        pad(defaultDate.getDate()) + 'T' +
                        pad(defaultDate.getHours()) + ':' +
                        pad(defaultDate.getMinutes());
                }
            } else {
                if (customExpiryWrap) customExpiryWrap.style.display = "none";
                if (customExpiryInput) customExpiryInput.required = false;
            }
        });
    }

    // Load active files into dropdown
    function loadUserFiles() {
        if (!fileSelect) return;
        fetch("/api/files/active", {
            headers: { "Accept": "application/json" }
        })
        .then(function (res) {
            if (res.status === 401) {
                return res.json().then(function (body) {
                    throw new Error(body.error || "Authentication required.");
                }).catch(function (err) {
                    throw new Error(err.message || "Authentication required.");
                });
            }
            if (!res.ok) throw new Error("Could not load active files");
            return res.json();
        })
        .then(function (data) {
            fileSelect.innerHTML = '<option value="">-- Choose a file to share --</option>';
            var files = (data && data.files) || [];
            if (files.length === 0) {
                fileSelect.innerHTML = '<option value="">No files available to share</option>';
                return;
            }
            files.forEach(function (f) {
                var opt = document.createElement("option");
                opt.value = f.id;
                opt.textContent = f.original_filename;
                fileSelect.appendChild(opt);
            });
        })
        .catch(function (err) {
            fileSelect.innerHTML = '<option value="">Select a file...</option>';
        });
    }

    // Load shares list
    function loadShares() {
        if (isBusy) return;
        loadingEl.style.display = "block";
        errorEl.style.display = "none";
        emptyEl.style.display = "none";
        tableWrapEl.style.display = "none";

        fetch("/api/shares", {
            headers: { "Accept": "application/json" }
        })
        .then(function (res) {
            if (res.status === 401) {
                return res.json().then(function (body) {
                    throw new Error(body.error || "Authentication required.");
                }).catch(function (err) {
                    throw new Error(err.message || "Authentication required.");
                });
            }
            if (!res.ok) throw new Error("HTTP error " + res.status);
            return res.json();
        })
        .then(function (data) {
            loadingEl.style.display = "none";
            renderShares(data);
        })
        .catch(function (err) {
            loadingEl.style.display = "none";
            errorEl.style.display = "block";
            if (errorTextEl) {
                errorTextEl.textContent = "Could not load share links: " + (err.message || "Network error");
            }
        });
    }

    function renderShares(data) {
        var shares = (data && data.shares) || [];
        tbodyEl.innerHTML = "";

        if (shares.length === 0) {
            emptyEl.style.display = "block";
            tableWrapEl.style.display = "none";
            return;
        }

        shares.forEach(function (share) {
            var tr = document.createElement("tr");
            var st = determineStatus(share);

            // File Name
            var tdFile = document.createElement("td");
            tdFile.setAttribute("data-label", "File");
            var nameLink = document.createElement("a");
            nameLink.href = "/files/" + share.file_id;
            nameLink.textContent = share.original_filename || ("File #" + share.file_id);
            tdFile.appendChild(nameLink);

            // Share Link + Copy Button
            var tdLink = document.createElement("td");
            tdLink.setAttribute("data-label", "Share Link");
            var linkWrap = document.createElement("div");
            linkWrap.className = "share-link-group";

            var fullUrl = window.location.origin + "/s/" + encodeURIComponent(share.token);
            var linkInput = document.createElement("input");
            linkInput.type = "text";
            linkInput.readOnly = true;
            linkInput.value = fullUrl;
            linkInput.className = "share-url-input";
            linkInput.setAttribute("aria-label", "Public link for " + (share.original_filename || "file"));

            var copyBtn = document.createElement("button");
            copyBtn.type = "button";
            copyBtn.className = "btn btn-secondary btn-sm copy-btn";
            copyBtn.textContent = "Copy";
            copyBtn.setAttribute("aria-label", "Copy link to clipboard");
            copyBtn.addEventListener("click", function () {
                copyToClipboard(fullUrl, copyBtn);
            });

            linkWrap.appendChild(linkInput);
            linkWrap.appendChild(copyBtn);
            tdLink.appendChild(linkWrap);

            // Created At
            var tdCreated = document.createElement("td");
            tdCreated.setAttribute("data-label", "Created");
            tdCreated.className = "col-meta";
            tdCreated.textContent = formatDate(share.created_at);

            // Expires At
            var tdExpires = document.createElement("td");
            tdExpires.setAttribute("data-label", "Expires");
            tdExpires.textContent = formatDate(share.expires_at);

            // Status Badge
            var tdStatus = document.createElement("td");
            tdStatus.setAttribute("data-label", "Status");
            var badge = document.createElement("span");
            badge.className = "status-badge " + st.badgeClass;
            badge.textContent = st.label;
            tdStatus.appendChild(badge);

            // Actions (Revoke)
            var tdActions = document.createElement("td");
            tdActions.setAttribute("data-label", "Actions");

            if (st.key === "active") {
                var revokeBtn = document.createElement("button");
                revokeBtn.type = "button";
                revokeBtn.className = "btn btn-danger btn-sm";
                revokeBtn.textContent = "Revoke";
                revokeBtn.setAttribute("aria-label", "Revoke share for " + (share.original_filename || "file"));
                revokeBtn.addEventListener("click", function () {
                    promptRevoke(share);
                });
                tdActions.appendChild(revokeBtn);
            } else {
                var noAction = document.createElement("span");
                noAction.className = "muted";
                noAction.textContent = "—";
                tdActions.appendChild(noAction);
            }

            tr.appendChild(tdFile);
            tr.appendChild(tdLink);
            tr.appendChild(tdCreated);
            tr.appendChild(tdExpires);
            tr.appendChild(tdStatus);
            tr.appendChild(tdActions);

            tbodyEl.appendChild(tr);
        });

        tableWrapEl.style.display = "block";
    }

    function copyToClipboard(text, buttonEl) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () {
                showCopiedFeedback(buttonEl);
            }).catch(function () {
                fallbackCopy(text, buttonEl);
            });
        } else {
            fallbackCopy(text, buttonEl);
        }
    }

    function fallbackCopy(text, buttonEl) {
        var input = document.createElement("textarea");
        input.value = text;
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        try {
            document.execCommand("copy");
            showCopiedFeedback(buttonEl);
        } catch (e) {
            setStatus("Could not copy automatically. Please copy the URL manually.", "error");
        }
        document.body.removeChild(input);
    }

    function showCopiedFeedback(buttonEl) {
        var origText = buttonEl.textContent;
        buttonEl.textContent = "Copied!";
        buttonEl.classList.add("btn-copied");
        setTimeout(function () {
            buttonEl.textContent = origText;
            buttonEl.classList.remove("btn-copied");
        }, 2000);
    }

    // Create share submit
    if (form) {
        form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            if (isBusy) return;

            var fileId = fileSelect ? fileSelect.value : "";
            if (!fileId) {
                setStatus("Please select a file to share.", "error");
                return;
            }

            var preset = expiryPreset ? expiryPreset.value : "24h";
            var customVal = customExpiryInput ? customExpiryInput.value : "";
            var expiresAt = calculateExpiresAtUTC(preset, customVal);

            if (!expiresAt) {
                setStatus("Please select a valid expiration date and time in the future.", "error");
                return;
            }

            if (new Date(expiresAt) <= new Date()) {
                setStatus("Expiration date must be in the future.", "error");
                return;
            }

            isBusy = true;
            if (submitBtn) submitBtn.disabled = true;
            setStatus("Generating share link...", "info");

            // Server-derived ownership: no client-supplied user ID is sent
            fetch("/api/shares", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken,
                    "Accept": "application/json"
                },
                body: JSON.stringify({
                    file_id: parseInt(fileId, 10),
                    expires_at: expiresAt,
                    expiry_preset: preset
                })
            })
            .then(function (res) {
                if (res.status === 401) {
                    return res.json().then(function (body) {
                        throw new Error(body.error || "Authentication required.");
                    }).catch(function (err) {
                        throw new Error(err.message || "Authentication required.");
                    });
                }
                return res.json().then(function (body) {
                    if (!res.ok) {
                        throw new Error(body.error || "Failed to create share link (HTTP " + res.status + ")");
                    }
                    return body;
                });
            })
            .then(function (data) {
                isBusy = false;
                if (submitBtn) submitBtn.disabled = false;
                setStatus("Share link created successfully!", "success");
                form.reset();
                if (customExpiryWrap) customExpiryWrap.style.display = "none";
                loadShares();
            })
            .catch(function (err) {
                isBusy = false;
                if (submitBtn) submitBtn.disabled = false;
                setStatus(err.message || "An unexpected error occurred.", "error");
            });
        });
    }

    // Revocation Dialog
    function promptRevoke(share) {
        pendingRevokeShare = share;
        if (!dialog) {
            if (window.confirm("Are you sure you want to revoke access to '" + (share.original_filename || "this file") + "'?")) {
                executeRevoke();
            }
            return;
        }

        if (typeof dialog.showModal === "function") {
            dialog.showModal();
        } else {
            dialog.setAttribute("open", "true");
        }
    }

    function closeDialog() {
        pendingRevokeShare = null;
        if (!dialog) return;
        if (typeof dialog.close === "function") {
            dialog.close();
        } else {
            dialog.removeAttribute("open");
        }
    }

    if (dialogCancelBtn) {
        dialogCancelBtn.addEventListener("click", closeDialog);
    }

    if (dialogConfirmBtn) {
        dialogConfirmBtn.addEventListener("click", function () {
            executeRevoke();
        });
    }

    function executeRevoke() {
        if (!pendingRevokeShare || isBusy) return;
        var share = pendingRevokeShare;
        closeDialog();

        isBusy = true;
        setStatus("Revoking link...", "info");

        fetch("/api/shares/" + share.id + "/revoke", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrfToken,
                "Accept": "application/json"
            }
        })
        .then(function (res) {
            if (res.status === 401) {
                return res.json().then(function (body) {
                    throw new Error(body.error || "Authentication required.");
                }).catch(function (err) {
                    throw new Error(err.message || "Authentication required.");
                });
            }
            return res.json().then(function (body) {
                if (!res.ok) {
                    throw new Error(body.error || "Failed to revoke share (HTTP " + res.status + ")");
                }
                return body;
            });
        })
        .then(function (data) {
            isBusy = false;
            setStatus(data.message || "Share link revoked successfully.", "success");
            loadShares();
        })
        .catch(function (err) {
            isBusy = false;
            setStatus(err.message || "An unexpected error occurred.", "error");
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener("click", function () {
            setStatus("", null);
            loadShares();
        });
    }

    if (retryBtn) {
        retryBtn.addEventListener("click", loadShares);
    }

    // Initial load
    loadUserFiles();
    loadShares();
})();
