// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

// Custom drag-handle for resizing code blocks vertically. Replaces the
// native `resize: vertical` corner handle with a full-width bar that
// sits below each <pre class="code-snippet">. Document-level event
// delegation so dynamically-rendered blocks (analyzer-demo expansion
// panels) work without explicit hookup.
//
// Contract:
//   - The handle element has class .code-resize-handle and lives as a
//     sibling immediately after the .code-snippet to resize.
//   - Mouse + touch supported. Keyboard support: ArrowUp / ArrowDown
//     in 24px steps when the handle has focus.
//
// Height bounds match the CSS: min-height 96px, max-height per viewport
// (we cap at 90vh so the user can grow past the 60vh default but not
// completely cover the page).
(function () {
  "use strict";

  const MIN_PX = 96;
  const KEY_STEP_PX = 24;

  function snippetFor(handle) {
    // Prefer previousElementSibling but fall back to a class lookup
    // inside the same wrap, in case markup order ever shifts.
    const prev = handle.previousElementSibling;
    if (prev && prev.classList.contains("code-snippet")) return prev;
    const wrap = handle.closest(".code-snippet-wrap");
    return wrap ? wrap.querySelector(".code-snippet") : null;
  }

  function maxPx() {
    return Math.round(window.innerHeight * 0.9);
  }

  function clamp(h) {
    return Math.max(MIN_PX, Math.min(maxPx(), h));
  }

  function startDrag(handle, startY, startH) {
    handle.dataset.dragging = "true";
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";
    const snippet = snippetFor(handle);
    if (!snippet) return null;

    function onMove(clientY) {
      const next = clamp(startH + (clientY - startY));
      snippet.style.height = next + "px";
      // Drop the CSS max-height once the user has driven height
      // explicitly; otherwise the cap reasserts on smaller drags.
      snippet.style.maxHeight = "none";
    }

    function end() {
      delete handle.dataset.dragging;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }

    return { onMove, end };
  }

  // Mouse drag
  document.addEventListener("mousedown", (ev) => {
    const handle = ev.target.closest(".code-resize-handle");
    if (!handle) return;
    ev.preventDefault();
    const snippet = snippetFor(handle);
    if (!snippet) return;
    const startY = ev.clientY;
    const startH = snippet.getBoundingClientRect().height;
    const drag = startDrag(handle, startY, startH);
    if (!drag) return;
    function onMove(e) {
      drag.onMove(e.clientY);
    }
    function onUp() {
      drag.end();
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  // Touch drag
  document.addEventListener(
    "touchstart",
    (ev) => {
      const handle = ev.target.closest(".code-resize-handle");
      if (!handle || ev.touches.length !== 1) return;
      ev.preventDefault();
      const snippet = snippetFor(handle);
      if (!snippet) return;
      const startY = ev.touches[0].clientY;
      const startH = snippet.getBoundingClientRect().height;
      const drag = startDrag(handle, startY, startH);
      if (!drag) return;
      function onMove(e) {
        if (e.touches.length !== 1) return;
        drag.onMove(e.touches[0].clientY);
      }
      function onEnd() {
        drag.end();
        document.removeEventListener("touchmove", onMove);
        document.removeEventListener("touchend", onEnd);
        document.removeEventListener("touchcancel", onEnd);
      }
      document.addEventListener("touchmove", onMove, { passive: false });
      document.addEventListener("touchend", onEnd);
      document.addEventListener("touchcancel", onEnd);
    },
    { passive: false },
  );

  // Keyboard a11y: ArrowUp / ArrowDown nudges the height in 24px steps.
  document.addEventListener("keydown", (ev) => {
    const handle = ev.target.closest && ev.target.closest(".code-resize-handle");
    if (!handle) return;
    if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
    const snippet = snippetFor(handle);
    if (!snippet) return;
    ev.preventDefault();
    const cur = snippet.getBoundingClientRect().height;
    const delta = ev.key === "ArrowDown" ? KEY_STEP_PX : -KEY_STEP_PX;
    snippet.style.height = clamp(cur + delta) + "px";
    snippet.style.maxHeight = "none";
  });
})();
