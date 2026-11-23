// CloudVault Public Share download page script (Stage 6, Part 1).
// Vanilla JavaScript only; minimal DOM manipulation and fetch API.
"use strict";

(function () {
    var page = document.querySelector("[data-public-share-page]");
    if (!page) {
        return;
    }

    var dynamicContainer = page.querySelector("[data-public-share-container]");
    if (!dynamicContainer) {
        // Page was already statically rendered by the backend (server-side template)
        return;
    }

    var loadingEl = dynamicContainer.querySelector("[data-share-loading]");
    var errorEl = dynamicContainer.querySelector("[data-share-error]");
    var errorTitleEl = dynamicContainer.querySelector("[data-error-title]");
    var errorMsgEl = dynamicContainer.querySelector("[data-error-message]");
    var contentEl = dynamicContainer.querySelector("[data-share-content]");
    var fileNameEl = dynamicContainer.querySelector("[data-file-name]");
    var fileSizeEl = dynamicContainer.querySelector("[data-file-size]");
    var fileTypeEl = dynamicContainer.querySelector("[data-file-type]");
    var fileExpiresEl = dynamicContainer.querySelector("[data-file-expires]");
    var downloadBtn = dynamicContainer.querySelector("[data-download-btn]");

    // Extract token from current URL pathname: /s/<token>
    var pathParts = window.location.pathname.split("/");
    var token = "";
    for (var i = 0; i < pathParts.length; i++) {
        if (pathParts[i] === "s" && i + 1 < pathParts.length) {
            token = pathParts[i + 1];
            break;
        }
    }

    if (!token) {
        showError("Invalid Link", "The shared link URL is missing a valid token.", "not_found");
        return;
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

    function showError(title, message, state) {
        if (loadingEl) loadingEl.style.display = "none";
        if (contentEl) contentEl.style.display = "none";
        if (errorEl) {
            errorEl.style.display = "block";
            errorEl.className = "error-state panel-error status-card status-card-" + (state || "not_found");
            if (errorTitleEl) errorTitleEl.textContent = title;
            if (errorMsgEl) errorMsgEl.textContent = message;
        }
    }

    function showContent(data) {
        if (loadingEl) loadingEl.style.display = "none";
        if (errorEl) errorEl.style.display = "none";
        if (contentEl) {
            contentEl.style.display = "block";
            // Data contract fields: file.original_filename, file.size_bytes, file.mime_type, share.expires_at
            if (fileNameEl) fileNameEl.textContent = data.file.original_filename || "Untitled File";
            if (fileSizeEl) fileSizeEl.textContent = formatBytes(data.file.size_bytes);
            if (fileTypeEl) fileTypeEl.textContent = data.file.mime_type || "application/octet-stream";
            if (fileExpiresEl) fileExpiresEl.textContent = formatDate(data.share.expires_at);

            if (downloadBtn) {
                // Endpoint matching GET /s/<token>/download
                downloadBtn.href = "/s/" + encodeURIComponent(token) + "/download";
            }
        }
    }

    // Fetch public metadata: GET /api/s/<token>
    fetch("/api/s/" + encodeURIComponent(token), {
        headers: { "Accept": "application/json" }
    })
    .then(function (res) {
        return res.json().then(function (body) {
            if (!res.ok) {
                var errObj = new Error(body.error || "Unable to access share.");
                errObj.status = res.status;
                errObj.state = body.state; // Standardised states: 'expired', 'revoked', 'not_found'
                throw errObj;
            }
            return body;
        });
    })
    .then(function (data) {
        showContent(data);
    })
    .catch(function (err) {
        if (err.state === "expired") {
            showError("Link Expired", "This shared link expired and is no longer available for download.", "expired");
        } else if (err.state === "revoked") {
            showError("Link Revoked", "This shared link was revoked by the file owner.", "revoked");
        } else if (err.status === 404 || err.state === "not_found") {
            showError("Link Not Found", "The requested shared file does not exist or has been removed.", "not_found");
        } else {
            showError("Link Unavailable", err.message || "An error occurred while loading this link.", "not_found");
        }
    });
})();
