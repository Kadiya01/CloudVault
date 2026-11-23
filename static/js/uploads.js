// CloudVault resumable uploads. Vanilla JavaScript only; no libraries.
"use strict";

(function () {
    var panel = document.querySelector("[data-transfer-panel]");
    var form = document.querySelector("[data-upload-form]");
    if (!panel || !form) {
        return;
    }

    var fileInput = form.querySelector("[data-upload-input]");
    var submitButton = form.querySelector("[data-upload-submit]");
    var filenameEl = panel.querySelector("[data-transfer-filename]");
    var percentEl = panel.querySelector("[data-transfer-percent]");
    var progressEl = panel.querySelector("[data-transfer-progress]");
    var barEl = panel.querySelector("[data-transfer-bar]");
    var statusEl = panel.querySelector("[data-transfer-status]");
    var resumeButton = panel.querySelector("[data-transfer-resume]");
    var retryButton = panel.querySelector("[data-transfer-retry]");
    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

    var API = "/api/uploads";
    var STORAGE_KEY = "cloudvault:activeUpload";
    var current = null;
    var uploading = false;

    function selectedFolderId() {
        var field = form.querySelector('input[name="folder_id"]');
        if (!field || !field.value) {
            return null;
        }
        var value = parseInt(field.value, 10);
        return value > 0 ? value : null;
    }

    function setStatus(message, state) {
        statusEl.textContent = message;
        if (state) {
            statusEl.setAttribute("data-state", state);
        } else {
            statusEl.removeAttribute("data-state");
        }
    }

    function setProgress(value) {
        var percent = Math.max(0, Math.min(100, Number(value) || 0));
        var rounded = Math.round(percent);
        barEl.style.width = percent + "%";
        percentEl.textContent = rounded + "%";
        progressEl.setAttribute("aria-valuenow", String(rounded));
    }

    function setBusy(busy) {
        uploading = busy;
        if (submitButton) {
            submitButton.disabled = busy;
        }
        if (fileInput) {
            fileInput.disabled = busy;
        }
    }

    function showPanel() {
        panel.hidden = false;
    }

    function showResume(show) {
        resumeButton.hidden = !show;
    }

    function showRetry(show) {
        retryButton.hidden = !show;
    }

    function delay(milliseconds) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, milliseconds);
        });
    }

    function readSaved() {
        try {
            var raw = window.localStorage.getItem(STORAGE_KEY);
            if (!raw) {
                return null;
            }
            var data = JSON.parse(raw);
            if (!data || typeof data.sessionId !== "string") {
                return null;
            }
            if (!/^[0-9a-f]{32}$/.test(data.sessionId)) {
                return null;
            }
            return data;
        } catch (error) {
            return null;
        }
    }

    function persist() {
        if (!current) {
            clearSaved();
            return;
        }
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
                sessionId: current.sessionId,
                filename: current.filename,
                clientName: current.clientName,
                clientLastModified: current.clientLastModified,
                size: current.size,
                folderId: current.folderId,
                chunkSize: current.chunkSize,
                totalChunks: current.totalChunks
            }));
        } catch (error) {
            /* Storage can be unavailable; the server keeps the session. */
        }
    }

    function clearSaved() {
        current = null;
        try {
            window.localStorage.removeItem(STORAGE_KEY);
        } catch (error) {
            /* Nothing to do. */
        }
    }

    function sameUpload(file, session) {
        var expectedName = session.clientName || session.filename;
        var lastModifiedMatches = (
            session.clientLastModified === undefined ||
            session.clientLastModified === null ||
            file.lastModified === session.clientLastModified
        );
        return (
            Boolean(session) &&
            file.name === expectedName &&
            file.size === session.size &&
            lastModifiedMatches &&
            selectedFolderId() === session.folderId
        );
    }

    function parseError(response, data) {
        var message = data && data.error ? data.error : "Request failed (" + response.status + ").";
        var error = new Error(message);
        error.status = response.status;
        return error;
    }

    async function parseJson(response) {
        try {
            return await response.json();
        } catch (error) {
            return null;
        }
    }

    async function requestJson(url, options) {
        var response;
        try {
            response = await fetch(url, options);
        } catch (error) {
            var networkError = new Error("Network error. Check your connection and retry.");
            networkError.network = true;
            throw networkError;
        }
        var data = await parseJson(response);
        if (!response.ok) {
            throw parseError(response, data);
        }
        return data;
    }

    function statusRequest() {
        return requestJson(API + "/" + current.sessionId + "/status", {
            method: "GET",
            cache: "no-store",
            headers: { "Accept": "application/json" }
        });
    }

    function applyStatus(status) {
        if (status.original_filename) {
            current.filename = status.original_filename;
            filenameEl.textContent = status.original_filename;
        }
        current.size = status.total_size;
        current.chunkSize = status.chunk_size;
        current.totalChunks = status.total_chunks;
        current.received = new Set(status.received_chunks || []);
        current.receivedBytes = status.received_bytes || 0;
        setProgress(current.size ? (current.receivedBytes / current.size) * 100 : 0);
    }

    function markComplete(filename, reloadPage) {
        setProgress(100);
        setStatus("Upload complete: " + filename, "success");
        clearSaved();
        setBusy(false);
        showResume(false);
        showRetry(false);
        if (reloadPage) {
            window.setTimeout(function () {
                window.location.reload();
            }, 1200);
        }
    }

    function handleFailure(error) {
        setBusy(false);
        showPanel();
        showResume(Boolean(current));
        showRetry(Boolean(current));
        if (current) {
            persist();
        }
        setStatus(error.message || "Upload failed. Please retry.", "error");
    }

    async function uploadChunk(index, file, attempt) {
        var tryNumber = attempt || 1;
        setStatus(
            "Uploading chunk " + (index + 1) + " of " + current.totalChunks + "...",
            "busy"
        );

        var start = index * current.chunkSize;
        var end = Math.min(start + current.chunkSize, current.size);
        var blob = file.slice(start, end);

        try {
            var response = await fetch(
                API + "/" + current.sessionId + "/chunk/" + index,
                {
                    method: "PUT",
                    headers: {
                        "X-CSRF-Token": csrfToken,
                        "Content-Type": "application/octet-stream"
                    },
                    body: blob
                }
            );
            var data = await parseJson(response);
            if (!response.ok) {
                throw parseError(response, data);
            }
            current.received.add(index);
            if (data && typeof data.received_bytes === "number") {
                current.receivedBytes = data.received_bytes;
            } else {
                current.receivedBytes += blob.size;
            }
            setProgress((current.receivedBytes / current.size) * 100);
        } catch (error) {
            var retryable = !error.status || error.status >= 500;
            if (retryable && tryNumber < 3) {
                setStatus(
                    "Retrying chunk " + (index + 1) + " (attempt " +
                        (tryNumber + 1) + " of 3)...",
                    "warn"
                );
                await delay(700 * tryNumber);
                return uploadChunk(index, file, tryNumber + 1);
            }
            throw error;
        }
    }

    async function finalizeUpload() {
        setProgress(99);
        setStatus("Finalizing upload...", "busy");
        try {
            var data = await requestJson(
                API + "/" + current.sessionId + "/complete",
                {
                    method: "POST",
                    headers: { "X-CSRF-Token": csrfToken }
                }
            );
            markComplete(data.original_filename, true);
        } catch (error) {
            if (error.status === 409) {
                var status = null;
                try {
                    status = await statusRequest();
                } catch (statusError) {
                    status = null;
                }
                if (status && status.completed) {
                    markComplete(status.original_filename, true);
                    return;
                }
            }
            throw error;
        }
    }

    async function continueUpload(file) {
        var status = await statusRequest();
        if (status.completed) {
            markComplete(status.original_filename, false);
            return;
        }
        if (status.expired || status.status === "aborted") {
            clearSaved();
            setStatus("Upload session expired. Select the file and start a new upload.", "error");
            setProgress(0);
            setBusy(false);
            showResume(false);
            showRetry(false);
            return;
        }

        applyStatus(status);
        var missing = status.missing_chunks || [];
        if (!missing.length) {
            await finalizeUpload();
            return;
        }

        for (var index = 0; index < missing.length; index += 1) {
            await uploadChunk(missing[index], file);
        }
        await finalizeUpload();
    }

    async function startUpload(file) {
        setBusy(true);
        showPanel();
        showResume(false);
        showRetry(false);
        setProgress(0);
        filenameEl.textContent = file.name;
        setStatus("Starting upload...", "busy");

        try {
            var data = await requestJson(API + "/start", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken
                },
                body: JSON.stringify({
                    filename: file.name,
                    size: file.size,
                    folder_id: selectedFolderId()
                })
            });

            current = {
                sessionId: data.upload_id,
                filename: data.original_filename,
                clientName: file.name,
                clientLastModified: file.lastModified,
                size: data.total_size,
                folderId: data.folder_id,
                chunkSize: data.chunk_size,
                totalChunks: data.total_chunks,
                received: new Set(),
                receivedBytes: 0
            };
            persist();
            await continueUpload(file);
        } catch (error) {
            handleFailure(error);
        }
    }

    async function resumeUpload() {
        if (uploading || !current) {
            return;
        }
        if (!fileInput || !fileInput.files || !fileInput.files.length) {
            setStatus("Select the same file to resume the interrupted upload.", "error");
            return;
        }
        var file = fileInput.files[0];
        if (!sameUpload(file, current)) {
            setStatus("The selected file does not match this interrupted upload.", "error");
            return;
        }

        setBusy(true);
        showPanel();
        showResume(false);
        showRetry(false);
        filenameEl.textContent = current.filename || file.name;
        setStatus("Resuming upload...", "busy");

        try {
            await continueUpload(file);
        } catch (error) {
            handleFailure(error);
        }
    }

    function restoreSession() {
        var saved = readSaved();
        if (!saved) {
            return;
        }
        current = saved;
        current.received = new Set();
        current.receivedBytes = 0;
        showPanel();
        filenameEl.textContent = current.filename || current.clientName || "Upload";
        setProgress(0);
        setStatus("Checking interrupted upload...", "busy");

        statusRequest().then(function (status) {
            if (status.completed) {
                markComplete(status.original_filename, false);
                return;
            }
            if (status.expired || status.status === "aborted") {
                clearSaved();
                setStatus(
                    "Interrupted upload expired. Select the file again to start over.",
                    "error"
                );
                return;
            }
            applyStatus(status);
            showResume(true);
            setStatus(
                "Interrupted upload found. Select the same file and click Resume, " +
                    "or select it and press Upload.",
                "warn"
            );
        }).catch(function (error) {
            if (error.status === 401 || error.status === 404 || error.status === 410) {
                clearSaved();
                setStatus("Interrupted upload is no longer available.", "error");
                return;
            }
            setStatus("Could not check the interrupted upload. " + error.message, "error");
            showRetry(true);
        });
    }

    form.addEventListener("submit", function (event) {
        if (!window.fetch || !fileInput || !fileInput.files || !fileInput.files.length) {
            return;
        }
        event.preventDefault();
        if (uploading) {
            setStatus("An upload is already in progress.", "warn");
            return;
        }
        var file = fileInput.files[0];
        if (current && sameUpload(file, current)) {
            resumeUpload();
            return;
        }
        startUpload(file);
    });

    resumeButton.addEventListener("click", resumeUpload);
    retryButton.addEventListener("click", resumeUpload);

    restoreSession();
})();
