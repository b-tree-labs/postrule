// Pricing renderer — fetches landing/data/pricing-tiers.json and
// landing/data/llm-prices.json, renders the tier table and the
// "find your tier" calculator. Single source of truth: edit the
// JSON files and the page updates on next load. No data is uploaded.

(function () {
  "use strict";

  const TIERS_URL = "./data/pricing-tiers.json";
  const PRICES_URL = "./data/llm-prices.json";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c];
    });
  }

  function fmtUSD(n) {
    if (n == null || isNaN(n)) return "—";
    if (n >= 1000) {
      return "$" + Math.round(n).toLocaleString("en-US");
    }
    if (n >= 100) {
      return "$" + n.toFixed(0);
    }
    if (n >= 10) {
      return "$" + n.toFixed(2);
    }
    return "$" + n.toFixed(2);
  }

  function fmtCount(n) {
    if (n == null) return "—";
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
    return String(n);
  }

  // ---------- TIER TABLE ----------------------------------------------------

  function renderTiers(container, tiersData) {
    const tiers = tiersData.tiers || [];

    const rows = tiers
      .map((t) => {
        const headline = t.headline
          ? `<small class="pricing-tier__headline">${escapeHtml(t.headline)}</small>`
          : "";
        const features = t.key_features
          ? `<span class="pricing-tier__features">${escapeHtml(t.key_features)}</span>`
          : "";
        const overage =
          t.overage_per_call_usd != null && t.overage_per_call_usd > 0
            ? `<small class="dim">+ $${t.overage_per_call_usd.toFixed(4)}/call overage</small>`
            : "";
        // Status indicator surfaces tiers not yet open for direct signup.
        // coming_soon → waitlist label with target quarter (e.g. "Coming
        // 2026 Q3"). sales_led → "Talk to sales" label. available tiers
        // render no label (default state).
        const statusBadge = (() => {
          if (t.status === "coming_soon") {
            const when = t.available_from ? `Coming ${t.available_from}` : "Coming soon";
            return `<small class="dim">${escapeHtml(when)}</small>`;
          }
          if (t.status === "sales_led") {
            return `<small class="dim">Talk to sales</small>`;
          }
          return "";
        })();
        const cta = t.cta_href
          ? `<a class="pricing-tier__cta" href="${escapeHtml(t.cta_href)}">${escapeHtml(t.cta_label || "Learn more")} →</a>`
          : "";

        return `
          <tr class="${t.is_enterprise ? "pricing-tier--enterprise" : ""}">
            <td>
              <strong>${escapeHtml(t.label)}</strong>
              ${headline}
              ${features ? "<br />" + features : ""}
            </td>
            <td>
              ${escapeHtml(t.price_display || "")}
              ${statusBadge ? "<br />" + statusBadge : ""}
              ${overage ? "<br />" + overage : ""}
            </td>
            <td>${escapeHtml(t.classifications_display || "")}</td>
            <td>${cta}</td>
          </tr>
        `;
      })
      .join("");

    const meteringLabel = tiersData.metering_unit_label || "Verdicts / mo";
    const meteringGloss = tiersData.metering_unit_gloss
      ? `<p class="pricing-table__metering-gloss caption dim">${escapeHtml(tiersData.metering_unit_gloss)}</p>`
      : "";

    container.innerHTML = `
      <table class="pricing-table pricing-table--tiers">
        <thead>
          <tr>
            <th>Tier</th>
            <th>Price</th>
            <th>${escapeHtml(meteringLabel)}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      ${meteringGloss}
    `;
  }

  // ---------- CALCULATOR ----------------------------------------------------

  function pickRecommendedTier(tiers, callsPerMonth) {
    // Skip pro_services (decoupled from license); recommend the smallest
    // tier whose included verdict cap covers requested volume. If nothing
    // fits, fall back to the largest hosted tier (overage will apply) or
    // the contact-sales row.
    const hosted = tiers.filter(
      (t) =>
        t.id !== "pro_services" &&
        !t.is_enterprise &&
        t.classifications_per_month != null,
    );
    hosted.sort(
      (a, b) => (a.classifications_per_month || 0) - (b.classifications_per_month || 0),
    );

    for (const t of hosted) {
      if ((t.classifications_per_month || 0) >= callsPerMonth) return t;
    }
    // Above the largest hosted bucket → recommend Business + show
    // overage projection if a per-call overage exists; otherwise the
    // first enterprise tier.
    const last = hosted[hosted.length - 1];
    if (last) return last;
    return tiers.find((t) => t.is_enterprise) || null;
  }

  function fmtCallsLabel(n) {
    if (n >= 1_000_000) {
      const m = n / 1_000_000;
      return (m % 1 ? m.toFixed(1) : m.toFixed(0)) + "M";
    }
    if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
    return String(n);
  }

  function setupCalculator(root, tiersData, pricesData) {
    const calls = root.querySelector('[data-pc-input="calls"]');
    const callsOut = root.querySelector('[data-pc-input-output="calls"]');
    const provider = root.querySelector('[data-pc-input="provider"]');

    const tierOut = root.querySelector('[data-pc-output="tier"]');
    const tierDetailOut = root.querySelector('[data-pc-output="tier-detail"]');
    const baselineOut = root.querySelector('[data-pc-output="baseline"]');
    const postruleOut = root.querySelector('[data-pc-output="postrule"]');
    const savingsOut = root.querySelector('[data-pc-output="savings"]');

    if (!calls || !provider || !tierOut) return;

    // Populate provider dropdown.
    const providers = pricesData.providers || [];
    provider.innerHTML = providers
      .map((p) => {
        const selected = p.id === tiersData.default_provider_id ? " selected" : "";
        return `<option value="${escapeHtml(p.id)}"${selected}>${escapeHtml(p.label)} — $${Number(p.per_call_usd).toFixed(6)}/call</option>`;
      })
      .join("");

    // Default classifications/mo from tier data.
    if (tiersData.default_assumed_classifications_per_month) {
      calls.value = String(tiersData.default_assumed_classifications_per_month);
    }

    const graduationFraction = Number(tiersData.default_graduation_fraction || 0.5);
    const postGraduationCost = Number(tiersData.post_graduation_cost_per_call_usd || 3e-6);

    function recompute() {
      const callsPerMonth = Number(calls.value);
      callsOut.textContent = fmtCallsLabel(callsPerMonth);

      const selectedProvider = providers.find((p) => p.id === provider.value);
      if (!selectedProvider) return;
      const perCallUsd = Number(selectedProvider.per_call_usd);

      const baseline = callsPerMonth * perCallUsd;

      const tier = pickRecommendedTier(tiersData.tiers || [], callsPerMonth);

      // Post-graduation cost: half the calls now cost ~$3/M, half still
      // hit the LLM (the rules + LLM head — sites that didn't graduate).
      const postLlm = callsPerMonth * (1 - graduationFraction) * perCallUsd;
      const postMl = callsPerMonth * graduationFraction * postGraduationCost;

      let tierFee = 0;
      let overage = 0;
      if (tier) {
        tierFee = Number(tier.price_per_month_usd || 0);
        if (
          tier.overage_per_call_usd != null &&
          tier.classifications_per_month != null &&
          callsPerMonth > tier.classifications_per_month
        ) {
          overage =
            (callsPerMonth - tier.classifications_per_month) *
            Number(tier.overage_per_call_usd);
        }
      }

      const total = postLlm + postMl + tierFee + overage;
      const savings = baseline - total;

      tierOut.textContent = tier ? tier.label : "—";
      tierDetailOut.textContent = tier
        ? `${tier.price_display || ""}${overage > 0 ? " · " + fmtUSD(overage) + " overage" : ""}`
        : "";
      baselineOut.textContent = fmtUSD(baseline);
      postruleOut.textContent = fmtUSD(total);
      savingsOut.textContent = (savings >= 0 ? "" : "−") + fmtUSD(Math.abs(savings));
      savingsOut.classList.toggle("pricing-calculator__savings--positive", savings > 0);
      savingsOut.classList.toggle("pricing-calculator__savings--negative", savings <= 0);
    }

    calls.addEventListener("input", recompute);
    provider.addEventListener("change", recompute);
    recompute();

    root.hidden = false;
  }

  // ---------- BOOTSTRAP -----------------------------------------------------

  async function init() {
    const tiersContainer = document.querySelector("[data-pricing-tiers]");
    const calculatorRoot = document.querySelector("[data-pricing-calculator]");
    if (!tiersContainer && !calculatorRoot) return;

    let tiersData = null;
    let pricesData = null;
    try {
      const [tiersResp, pricesResp] = await Promise.all([
        fetch(TIERS_URL),
        fetch(PRICES_URL),
      ]);
      tiersData = await tiersResp.json();
      pricesData = await pricesResp.json();
    } catch (err) {
      if (tiersContainer) {
        tiersContainer.innerHTML =
          '<p class="caption dim">Pricing data unavailable. Email <a href="mailto:sales@postrule.ai">sales@postrule.ai</a> for current rates.</p>';
      }
      console.error("[pricing] failed to load JSON sources:", err);
      return;
    }

    if (tiersContainer && tiersData) {
      renderTiers(tiersContainer, tiersData);
    }
    if (calculatorRoot && tiersData && pricesData) {
      setupCalculator(calculatorRoot, tiersData, pricesData);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
