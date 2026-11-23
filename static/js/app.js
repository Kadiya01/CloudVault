// CloudVault main script. Minimal vanilla JavaScript only; no libraries.
"use strict";

document.addEventListener("DOMContentLoaded", function () {
    var toggles = document.querySelectorAll("[data-password-toggle]");

    toggles.forEach(function (toggle) {
        toggle.addEventListener("click", function () {
            var input = document.getElementById(toggle.getAttribute("data-password-toggle"));
            if (!input) {
                return;
            }
            var show = input.type === "password";
            input.type = show ? "text" : "password";
            toggle.textContent = show ? "Hide" : "Show";
            toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
        });
    });
});

document.addEventListener("DOMContentLoaded", function () {
    var SAVER_KEY = "cloudvault:dataSaver";
    var COMPACT_KEY = "cloudvault:compactView";

    function readFlag(key) {
        try {
            return window.localStorage.getItem(key) === "1";
        } catch (error) {
            return false;
        }
    }

    function writeFlag(key, value) {
        try {
            window.localStorage.setItem(key, value ? "1" : "0");
        } catch (error) {
            /* Storage may be unavailable; the toggle still works this session. */
        }
    }

    var saverOn = readFlag(SAVER_KEY);

    function renderSaver() {
        document.documentElement.setAttribute("data-saver", saverOn ? "on" : "off");
        document.querySelectorAll("[data-saver-toggle]").forEach(function (button) {
            button.textContent = "Data Saver: " + (saverOn ? "on" : "off");
            button.setAttribute("aria-pressed", saverOn ? "true" : "false");
        });
    }

    document.querySelectorAll("[data-saver-toggle]").forEach(function (button) {
        button.addEventListener("click", function () {
            saverOn = !saverOn;
            writeFlag(SAVER_KEY, saverOn);
            renderSaver();
        });
    });

    var filesPage = document.querySelector("[data-files-page]");
    var compactOn = readFlag(COMPACT_KEY);

    function renderCompact() {
        if (!filesPage) {
            return;
        }
        filesPage.classList.toggle("data-compact", compactOn);
        document.querySelectorAll("[data-view-toggle]").forEach(function (button) {
            button.textContent = "Compact view: " + (compactOn ? "on" : "off");
            button.setAttribute("aria-pressed", compactOn ? "true" : "false");
        });
    }

    document.querySelectorAll("[data-view-toggle]").forEach(function (button) {
        button.addEventListener("click", function () {
            compactOn = !compactOn;
            writeFlag(COMPACT_KEY, compactOn);
            renderCompact();
        });
    });

    renderSaver();
    renderCompact();

    document.addEventListener("submit", function (event) {
        var message = event.target.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });
});
