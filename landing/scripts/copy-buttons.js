// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

// Copy-to-clipboard for any code block on the page. Two-attribute
// contract:
//   [data-copy-target] wraps the source <pre><code>command</code></pre>
//   [data-copy-source] is the button to click
// Uses document-level event delegation so dynamically-created buttons
// (e.g., inside the analyzer-demo expanded panels rendered after page
// load) get wired without explicit hookup calls.
(function () {
  "use strict";
  if (!navigator.clipboard) return;

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy-source]");
    if (!btn) return;
    const wrap = btn.closest("[data-copy-target]");
    if (!wrap) return;
    const code = wrap.querySelector("code");
    if (!code) return;
    // Gutter-mode blocks render each source line as a <span class="cs-line">
    // joined with no whitespace; reconstruct line breaks for clipboard.
    // No-gutter blocks have plain text content already.
    const lines = code.querySelectorAll(".cs-line");
    let text;
    if (lines.length > 0) {
      text = Array.from(lines).map((n) => n.textContent).join("\n").trim();
    } else {
      text = code.textContent.trim();
    }
    // Icon-only buttons (.copy-btn--code) preserve their inner SVG and
    // swap it for a check on success; text buttons swap textContent.
    const isIcon = btn.classList.contains("copy-btn--code");
    const checkSvg =
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true"><path d="M3 8.5L7 12l6-7"/></svg>';
    try {
      await navigator.clipboard.writeText(text);
      const originalHtml = btn.innerHTML;
      const originalText = btn.textContent;
      if (isIcon) {
        btn.innerHTML = checkSvg;
      } else {
        btn.textContent = "Copied";
      }
      btn.dataset.copied = "true";
      setTimeout(() => {
        if (isIcon) {
          btn.innerHTML = originalHtml;
        } else {
          btn.textContent = originalText;
        }
        delete btn.dataset.copied;
      }, 1500);
    } catch (_err) {
      if (!isIcon) {
        btn.textContent = "Copy failed";
        setTimeout(() => { btn.textContent = "Copy"; }, 1500);
      }
    }
  });
})();
