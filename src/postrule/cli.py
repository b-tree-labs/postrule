# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# Licensed under the Business Source License 1.1 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at LICENSE-BSL in the
# repository root, or at https://mariadb.com/bsl11/.
#
# Change Date:    2030-05-01
# Change License: Apache License, Version 2.0
#
# Additional Use Grant: see LICENSE-BSL. Production use is
# permitted; offering a competing hosted service is not.

"""``postrule`` CLI - minimum-viable entry point.

Subcommands:

- ``postrule bench <benchmark>`` - run the transition-curve experiment
  for a named public intent-classification dataset and print JSON-lines
  checkpoints to stdout.

Intentionally argparse-based; no third-party CLI deps.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import webbrowser
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from postrule import auth

# ---------------------------------------------------------------------------
# Account / login flow
#
# v1 scaffolding for relationship-building, not hard DRM. OSS classification
# works without an account; the bare ``postrule`` command is a soft entry
# point that points new users at ``postrule login`` if they want cloud features.
# ---------------------------------------------------------------------------

_DEFAULT_API_BASE = "https://api.postrule.ai/v1"
_DEFAULT_DASHBOARD = "https://app.postrule.ai"
_NUDGE_THRESHOLD = 3
_LOGIN_REQUEST_TIMEOUT = 10.0  # seconds; per-request HTTP timeout
_LOGIN_MIN_INTERVAL = 2.0  # safety floor on the server-suggested poll interval


def _state_file() -> Path:
    """Resolve the CLI state file lazily.

    Resolved on each call so that the test harness's HOME redirect
    (and any user-set ``HOME`` between invocations) takes effect
    instead of being captured at import time.
    """
    return Path.home() / ".postrule" / "state.toml"


def _strip_unsafe_chars(value: str) -> str:
    """Remove non-printable + ESC bytes from a string before printing it.

    Defense-in-depth: an attacker who manages to land an API key with
    embedded ANSI CSI sequences in the local credentials file (or
    POSTRULE_API_KEY env var) would otherwise see those bytes flow into
    a terminal verbatim through ``print()``, and the terminal would
    interpret them as color / cursor-control directives. We never
    pass keys through a shell, but the rendered bytes can still
    confuse log scrapers and any downstream pipe.

    Strategy: keep printable ASCII (``str.isprintable()``) and strip
    everything else (ESC=0x1b, BEL=0x07, BS=0x08, VT=0x0b, FF=0x0c,
    DEL=0x7f, all C0 controls). The underlying credential is
    unchanged; only the rendered form is sanitized.
    """
    return "".join(c for c in value if c.isprintable())


def _truncate_key(api_key: str) -> str:
    """Return a display-safe truncation of an API key.

    The truncation runs through :func:`_strip_unsafe_chars` so a key
    containing ANSI ESC sequences or other control bytes never leaks
    those bytes into stdout when ``postrule login`` / ``postrule whoami``
    print the abbreviation.
    """
    sanitized = _strip_unsafe_chars(api_key)
    if len(sanitized) <= 12:
        return sanitized
    return f"{sanitized[:8]}...{sanitized[-4:]}"


def _load_state() -> dict:
    """Read ~/.postrule/state.toml as a flat key/value dict.

    We hand-parse the small subset we write (int counts, bool flags) to
    avoid a tomllib dependency on Python 3.10. Unknown lines are ignored.
    """
    state_file = _state_file()
    if not state_file.exists():
        return {}
    state: dict = {}
    try:
        for raw in state_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if value.lower() in ("true", "false"):
                state[key] = value.lower() == "true"
            else:
                try:
                    state[key] = int(value)
                except ValueError:
                    state[key] = value.strip('"')
    except OSError:
        return {}
    return state


def _save_state(state: dict) -> None:
    state_file = _state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in sorted(state.items()):
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f'{key} = "{value}"')
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _maybe_nudge_signup() -> None:
    """One-time soft nudge after repeated unauthenticated use."""
    if auth.is_logged_in():
        return
    state = _load_state()
    if state.get("nudge_shown"):
        return
    count = int(state.get("analyze_count", 0))
    if count < _NUDGE_THRESHOLD:
        return
    print(
        "\nLoving Postrule? Sign up for a free account to enable shared team analysis:"
        " postrule login",
        file=sys.stderr,
    )
    state["nudge_shown"] = True
    _save_state(state)


def _bump_counter(key: str) -> None:
    state = _load_state()
    state[key] = int(state.get(key, 0)) + 1
    _save_state(state)


def _switch_class_name_for(func_name: str) -> str:
    """Mirror ``postrule.lifters.evidence._class_name_for`` so the CLI can
    point the benchmark stub at the same class the evidence lifter
    emits (``CamelCaseFuncName + "Switch"``).
    """
    parts = func_name.split("_")
    camel = "".join(p[:1].upper() + p[1:] for p in parts if p)
    return f"{camel}Switch"


def _try_emit_benchmarks(
    *,
    source_path: Path,
    function_name: str,
    original_source: str,
) -> None:
    """Emit the close-the-loop benchmark stub alongside the lifted Switch
    module. Called from ``cmd_init`` when ``--with-benchmarks`` is set.

    The benchmark stub has no runtime dependencies of its own beyond
    pytest (which only the generated file imports, never the runtime
    path). Failures here print to stderr and do not abort the wrap.
    """
    try:
        from postrule import __version__ as _postrule_version
        from postrule import refresh as refresh_mod
        from postrule.benchmarks import generate_benchmark_module
    except ImportError as e:
        print(f"--with-benchmarks: harness unavailable ({e})", file=sys.stderr)
        return

    gen_dir = source_path.parent / "__postrule_generated__"
    bench_path = gen_dir / f"{source_path.stem}__{function_name}_bench.py"
    switch_class_name = _switch_class_name_for(function_name)
    # The lifted module (sibling of the bench module) is imported as
    # ``__postrule_generated__.<file_stem>__<func>``. The exact import
    # path depends on the user's Python package layout; this default
    # matches the sibling-file convention the evidence lifter uses.
    switch_module = f"__postrule_generated__.{source_path.stem}__{function_name}"
    try:
        generate_benchmark_module(
            out_path=bench_path,
            source_module=source_path.stem,
            source_function=function_name,
            switch_class_name=switch_class_name,
            switch_module=switch_module,
            source_ast_hash=refresh_mod.ast_hash(original_source),
            postrule_version=_postrule_version,
            switch_name=function_name,
        )
    except Exception as e:  # noqa: BLE001 - defensive against codegen errors
        print(f"--with-benchmarks: failed to write bench stub ({e})", file=sys.stderr)
        return
    init_path = gen_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text("")
    print(
        f"--with-benchmarks: wrote {bench_path.relative_to(source_path.parent)}",
        file=sys.stderr,
    )


def _try_generate_hypothesis(
    *,
    switch_name: str,
    file_path: str,
    function_name: str,
    labels: list[str] | None,
) -> None:
    """Auto-generate postrule/hypotheses/<switch>.md after a successful init.

    Idempotent — if the file already exists, we don't overwrite. Failures
    here never fail the init; the hypothesis file is a nicety, not a
    correctness requirement.
    """
    try:
        from postrule.cloud.report.hypotheses import generate_hypothesis_file
    except ImportError:
        return

    # Try to fetch cohort-tuned defaults for the prediction interval.
    # This is best-effort; if the insights cache isn't warm, we fall
    # back to regime defaults inside generate_hypothesis_file.
    cohort_size = 0
    cohort_low = None
    cohort_high = None
    try:
        from postrule.insights import load_cached_or_baked_in

        defaults = load_cached_or_baked_in()
        cohort_size = defaults.cohort_size
        # Regime "unknown" until the analyzer has scored this site;
        # for now use narrow as the most-common shape for newly-init'd
        # sites. The user edits the hypothesis file if their site is
        # actually medium/high.
        narrow_median = defaults.median_outcomes_to_graduation.get("narrow")
        if narrow_median:
            cohort_low = int(narrow_median * 0.7)
            cohort_high = int(narrow_median * 1.4)
    except Exception:  # noqa: BLE001 — never fail init on insights errors
        pass

    label_count = len(labels) if labels else None

    try:
        out_path, content_hash, was_created = generate_hypothesis_file(
            switch_name=switch_name,
            file_location=file_path,
            function_name=function_name,
            label_cardinality=label_count,
            regime=_regime_from_cardinality(label_count),
            cohort_size=cohort_size,
            cohort_predicted_low=cohort_low,
            cohort_predicted_high=cohort_high,
        )
    except Exception as e:  # noqa: BLE001 — hypothesis file is best-effort
        # Common reasons: write blocked by sandbox (in tests),
        # filesystem permission, disk full. Print a one-line note and
        # continue — the wrap itself succeeded; hypothesis is a nicety.
        print(
            f"  (hypothesis file not written: {type(e).__name__}; the wrap is still in place)",
            file=sys.stderr,
        )
        return

    if was_created:
        print(
            f"  pre-registered hypothesis: {out_path} (content hash: {content_hash[:12]}…)",
            file=sys.stderr,
        )
        print(
            "  → review and commit before evidence accumulates "
            "(edits change the hash; the hash is recorded in every "
            "subsequent gate evaluation)",
            file=sys.stderr,
        )
    else:
        print(
            f"  (hypothesis file already exists at {out_path}; preserving)",
            file=sys.stderr,
        )


def _regime_from_cardinality(n: int | None) -> str:
    """Map a label count to a regime string (matches analyzer convention)."""
    if n is None or n == 0:
        return "unknown"
    if n < 30:
        return "narrow"
    if n <= 60:
        return "medium"
    return "high"


def _try_auto_lift(
    *,
    source_path: Path,
    function_name: str,
    original_source: str,
) -> None:
    """Run the branch + evidence lifters and emit a Switch subclass into
    ``__postrule_generated__/<file_stem>__<func>.py`` next to the source.

    On refusal: print the first hazard's reason + suggested fix to
    stderr; do NOT raise. The basic decorator was already applied by
    ``cmd_init`` so the user has a working classifier even when full
    lifting can't be done automatically.
    """
    try:
        from postrule import __version__ as _postrule_version
        from postrule import refresh as refresh_mod
        from postrule.lifters.branch import LiftRefused as BranchRefused
        from postrule.lifters.branch import lift_branches
        from postrule.lifters.evidence import LiftRefused as EvidenceRefused
        from postrule.lifters.evidence import lift_evidence
    except ImportError as e:
        print(f"--auto-lift: lifter modules unavailable ({e})", file=sys.stderr)
        return

    # Prefer the evidence lifter (richer output) when it accepts; fall
    # back to the branch lifter for branches without hidden state. Both
    # raise LiftRefused with a structured reason.
    lifted_source: str | None = None
    last_error: str | None = None
    for lifter_name, lifter, refused_cls in (
        ("evidence", lift_evidence, EvidenceRefused),
        ("branch", lift_branches, BranchRefused),
    ):
        try:
            lifted_source = lifter(original_source, function_name)
            lifter_used = lifter_name
            break
        except refused_cls as e:
            last_error = f"{lifter_name}: {e.reason} at line {e.line}"
            continue
        except Exception as e:  # noqa: BLE001 - defensive; lifters shouldn't crash
            last_error = f"{lifter_name}: unexpected error: {e}"
            continue

    if lifted_source is None:
        print(
            "--auto-lift: refused. The basic decorator was applied. "
            f"To extract per-branch handlers and hidden-state evidence, "
            f"resolve the issue and re-run with --auto-lift.\n"
            f"  Reason: {last_error}\n"
            "  Run `postrule analyze --suggest-refactors` for the full diagnostic.",
            file=sys.stderr,
        )
        return

    # Write the generated file. Convention: __postrule_generated__/ sits
    # next to the source file. The generated module is named
    # <source_stem>__<function>.py.
    gen_dir = source_path.parent / "__postrule_generated__"
    gen_path = gen_dir / f"{source_path.stem}__{function_name}.py"
    # Source module path: derive from the file's package position. For
    # v1 simplicity, use the file stem; the lifter-doctor pair already
    # tolerates this convention.
    source_module = source_path.stem
    # Hash the SAME thing the reader (`refresh.detect_drift`) hashes:
    # the post-decoration extracted function source. ``original_source``
    # is the pre-decoration full file; using it directly here would
    # guarantee a hash mismatch on first refresh because the reader
    # extracts just the function (with its newly-added decorator) and
    # hashes only that snippet. Re-read the file to capture the wrapper
    # state already written by ``cmd_init``.
    post_decoration_source = source_path.read_text(encoding="utf-8")
    fn_src = refresh_mod._extract_function_source(post_decoration_source, function_name)
    if fn_src is None:
        # Should not happen: cmd_init just wrapped this function. Fall
        # back to the original source so we at least write a header.
        fn_src = original_source
    refresh_mod.write_generated_file(
        gen_path,
        source_module=source_module,
        source_function=function_name,
        source_ast_hash=refresh_mod.ast_hash(fn_src),
        content=lifted_source,
        postrule_version=_postrule_version,
    )
    # Ensure __postrule_generated__/__init__.py exists so the package is
    # importable. (Empty file is enough.)
    init_path = gen_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text("")
    print(
        f"--auto-lift: wrote {gen_path.relative_to(source_path.parent)} "
        f"using the {lifter_used} lifter.",
        file=sys.stderr,
    )


def cmd_status(args: argparse.Namespace) -> int:
    """Bare ``postrule`` (no subcommand) - soft welcome / status."""
    creds = auth.load_credentials()
    if creds is None:
        print(
            "Postrule is the graduated-autonomy classification primitive. "
            "OSS features work without an account."
        )
        print()
        print("Run `postrule login` to create a free account and unlock cloud features:")
        print("  - cloud-synced switch configurations")
        print("  - shared team analyzer corpus")
        print("  - opt-in registry contribution")
        print()
        print("See `postrule --help` for the full command list.")
        return 0

    email = creds.get("email") or "unknown"
    print(f"Logged in as {email} ({_truncate_key(creds['api_key'])}).")
    print("Run `postrule --help` for available commands.")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """Browser-based OAuth 2.0 Device Authorization Grant (RFC 8628).

    1. POST /v1/device/code  → server returns (device_code, user_code,
       verification_uri_complete, expires_in, interval). The device_code
       is the long secret only this CLI process knows; the user_code is
       the short XXXX-XXXX string the user types in the dashboard.
    2. Display the URL + code, optionally launch the browser.
    3. Poll POST /v1/device/token at the server-suggested interval until
       the dashboard authorizes (or denies, or expires).
    4. On success the server mints a fresh prul_live_… key and returns
       it once. Save to ~/.postrule/credentials with mode 0600.

    Environment overrides:
        POSTRULE_API_BASE      — point the CLI at a non-prod api Worker
                               (e.g. http://localhost:8787/v1 for dev)
    """
    import requests  # lazy import; keeps `postrule --help` snappy

    api_base = _login_api_base()
    device_name = args.device_name or _detect_device_name()

    # Step 1: start the flow.
    try:
        start = requests.post(
            f"{api_base}/device/code",
            json={"device_name": device_name},
            timeout=_LOGIN_REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"Could not reach Postrule ({api_base}): {e}", file=sys.stderr)
        return 1

    if not start.ok:
        print(f"Failed to start device flow: HTTP {start.status_code}", file=sys.stderr)
        try:
            print(f"  {start.json().get('error', start.text)}", file=sys.stderr)
        except ValueError:
            pass
        return 1

    info = start.json()
    device_code = info["device_code"]
    user_code = info["user_code"]
    verification_uri_complete = info.get(
        "verification_uri_complete",
        f"{_DEFAULT_DASHBOARD}/cli-auth?user_code={user_code}",
    )
    interval = max(float(info.get("interval", 5)), _LOGIN_MIN_INTERVAL)
    expires_in = int(info.get("expires_in", 900))

    # Step 2: prompt + open the browser.
    print()
    print("To finish signing in:")
    print()
    print(f"  1. Open  {verification_uri_complete}")
    print(f"  2. Confirm the code:  {user_code}")
    print()

    if not args.no_browser:
        try:
            webbrowser.open(verification_uri_complete, new=1, autoraise=True)
        except Exception:
            # Headless environments / WSL / SSH without DISPLAY — silent fail.
            pass

    print("Waiting for confirmation (Ctrl+C to cancel)...", flush=True)

    # Step 3: poll.
    deadline = time.monotonic() + expires_in
    try:
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                poll = requests.post(
                    f"{api_base}/device/token",
                    json={"device_code": device_code},
                    timeout=_LOGIN_REQUEST_TIMEOUT,
                )
            except requests.RequestException:
                # Transient network blip — keep polling within the deadline.
                continue

            if poll.ok:
                data = poll.json()
                api_key = data["api_key"]
                email = data.get("email") or "unknown"
                # Fetch the server-side preference for default-on telemetry.
                # The dashboard's `/dashboard/settings` toggle persists the
                # user's choice; cache it locally so `maybe_install` can
                # short-circuit without an extra round-trip per process. If
                # the fetch fails (transient network, server hiccup), fall
                # back to True — the v1.0 default-on posture (Q4 decision).
                telemetry_enabled = _fetch_telemetry_preference(api_base, api_key)
                auth.save_credentials(
                    api_key,
                    email=email,
                    telemetry_enabled=telemetry_enabled,
                )
                print()
                print(f"Signed in as {email}.")
                print(f"Credentials saved to {auth.credentials_path()} (mode 0600).")
                if not telemetry_enabled:
                    print(
                        "Telemetry is OFF for this account "
                        "(toggle via postrule.ai/dashboard/settings)."
                    )
                return 0

            err = ""
            try:
                err = poll.json().get("error", "")
            except ValueError:
                pass

            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = min(interval + 5.0, 30.0)
                continue
            if err == "access_denied":
                print()
                print(
                    "Access denied in browser. Run `postrule login` again to retry.",
                    file=sys.stderr,
                )
                return 1
            if err == "expired_token":
                print()
                print("Login session expired. Run `postrule login` again.", file=sys.stderr)
                return 1
            # invalid_grant / invalid_request / unknown
            print()
            print(f"Login failed: {err or f'HTTP {poll.status_code}'}", file=sys.stderr)
            return 1
    except KeyboardInterrupt:
        print()
        print("Login cancelled.", file=sys.stderr)
        return 130

    print()
    print("Login timed out. Run `postrule login` again.", file=sys.stderr)
    return 1


def _login_api_base() -> str:
    """Resolve the api Worker's /v1 base URL for the device flow.

    Uses ``POSTRULE_API_BASE`` if set, falls back to the production Worker.
    """
    return os.environ.get("POSTRULE_API_BASE", _DEFAULT_API_BASE).rstrip("/")


def _fetch_telemetry_preference(api_base: str, api_key: str) -> bool:
    """Fetch ``users.telemetry_enabled`` via ``GET /v1/whoami``.

    Best-effort. Defaults to ``True`` (the v1.0 Q4 default-on posture)
    when the call fails or the server doesn't return the field. Tight
    timeout so a slow server doesn't make ``postrule login`` feel
    sluggish — the flag is purely a hint and ``maybe_install`` re-reads
    the credentials file each process start anyway.
    """
    import requests  # already imported above; this re-import is cheap

    try:
        r = requests.get(
            f"{api_base}/whoami",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=3.0,
        )
        if not r.ok:
            return True
        data = r.json()
    except (requests.RequestException, ValueError):
        return True
    value = data.get("telemetry_enabled")
    if value is None:
        return True
    return bool(value)


def _detect_device_name() -> str:
    """Best-effort device identifier the user will see in the dashboard.

    Prefers the OS hostname (the operator's chosen machine name) because
    it's what they recognize when confirming the device. Strips ``.local``
    / ``.lan`` suffixes added by network managers, caps at 64 chars to
    match the server-side cap.
    """
    try:
        host = socket.gethostname()
    except OSError:
        return "unknown"
    if not host:
        return "unknown"
    # Drop common DNS suffixes that aren't useful for human ID.
    host = host.split(".", 1)[0]
    return host[:64] or "unknown"


def cmd_logout(args: argparse.Namespace) -> int:
    """Clear local credentials."""
    if not auth.is_logged_in():
        print("Already signed out.")
        return 0
    auth.clear_credentials()
    print("Signed out. Local credentials removed.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    """Print the logged-in email + truncated API key."""
    creds = auth.load_credentials()
    if creds is None:
        print("not logged in")
        return 1
    email = creds.get("email") or "(email unknown)"
    print(f"{email}  {_truncate_key(creds['api_key'])}")
    return 0


def cmd_insights_enroll(args: argparse.Namespace) -> int:
    """Opt in to Postrule Insights — show disclosure, prompt, write flag."""
    from postrule.insights import (
        DISCLOSURE_TEXT,
        disclosure_text_sha256,
        is_enrolled,
        write_enrollment,
    )

    if is_enrolled():
        print("Already enrolled in Postrule Insights.")
        print(
            "Run `postrule insights status` for details, or `postrule insights leave` to opt out."
        )
        return 0

    print(DISCLOSURE_TEXT, end="")
    if getattr(args, "yes", False):
        answer = "y"
        print("y  (auto-confirmed via --yes)")
    else:
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""

    if answer not in ("y", "yes"):
        print("\nNot enrolled. The OSS path remains telemetry-free.")
        return 0

    creds = auth.load_credentials() if hasattr(auth, "load_credentials") else None
    account_hash = None
    if creds and creds.get("api_key"):
        # Hash the API key client-side so we never write the raw key
        # to the enrollment record. Server-side hashing is layered on
        # top by the collector.
        import hashlib

        account_hash = hashlib.sha256(creds["api_key"].encode("utf-8")).hexdigest()

    state = write_enrollment(
        account_hash=account_hash,
        consent_text_sha256=disclosure_text_sha256(),
    )
    print()
    print(f"Enrolled at {state.enrolled_at}.")
    print("Telemetry events will be queued and flushed best-effort on next CLI runs.")
    print("Leave at any time with: postrule insights leave")
    return 0


def cmd_insights_leave(args: argparse.Namespace) -> int:
    """Opt out of Postrule Insights — remove flag, stop emitting."""
    from postrule.insights import is_enrolled, write_unenrollment

    if not is_enrolled():
        print("Not currently enrolled in Postrule Insights.")
        return 0
    write_unenrollment()
    print("Unenrolled. No further telemetry will be queued.")
    print(
        "Any events still in the local queue at "
        "~/.postrule/insights-queue.jsonl have NOT been uploaded; "
        "delete that file to discard them."
    )
    return 0


def cmd_insights_status(args: argparse.Namespace) -> int:
    """Show enrollment state + cached cohort defaults; refresh if --refresh."""
    from postrule.insights import (
        get_tuned_defaults_url,
        load_cached_or_baked_in,
        read_enrollment,
        read_queue,
        refresh_if_stale,
    )
    from postrule.insights.tuned_defaults import cache_is_fresh

    # When the user invokes ``postrule insights status`` they want a real
    # answer, not the stale cache. Refresh synchronously unless the
    # cache is already fresh (within the freshness window).
    if not getattr(args, "no_fetch", False):
        refreshed = refresh_if_stale()
        refreshed_msg = "  (just-refreshed from cohort endpoint)" if refreshed is not None else ""
    else:
        refreshed_msg = "  (--no-fetch; cache only)"

    state = read_enrollment()
    defaults = load_cached_or_baked_in()
    queue = read_queue()

    print("Postrule Insights — status")
    print("=" * 40)
    if state.enrolled:
        print(f"Enrolled:        yes (since {state.enrolled_at})")
        print(f"Schema version:  {state.schema_version}")
        if state.account_hash:
            print(f"Account hash:    {state.account_hash[:16]}…")
    else:
        print("Enrolled:        no  (OSS path is telemetry-free)")
    print()
    print(f"Cohort defaults{refreshed_msg}:")
    print(f"  URL:           {get_tuned_defaults_url()}")
    print(f"  Version:       {defaults.version}")
    print(f"  Cohort size:   {defaults.cohort_size}")
    if defaults.generated_at:
        print(f"  Generated at:  {defaults.generated_at}")
    print(f"  Cache fresh:   {'yes' if cache_is_fresh() else 'no'}")
    if defaults.median_outcomes_to_graduation:
        print("  Median outcomes to graduation:")
        for regime, n in sorted(defaults.median_outcomes_to_graduation.items()):
            print(f"    {regime:>8}: {n}")
    print()
    print(f"Pending events in queue: {len(queue)}")
    return 0


_BENCHMARKS = {
    "banking77": "load_banking77",
    "clinc150": "load_clinc150",
    "hwu64": "load_hwu64",
    "atis": "load_atis",
    "snips": "load_snips",
    "trec6": "load_trec6",
    "ag_news": "load_ag_news",
    "codelangs": "load_codelangs",
}


def _load_bench(name: str):
    from postrule.benchmarks import loaders

    return getattr(loaders, _BENCHMARKS[name])()


def cmd_bench(args: argparse.Namespace) -> int:
    if args.benchmark not in _BENCHMARKS:
        print(
            f"unknown benchmark {args.benchmark!r}; choose from {sorted(_BENCHMARKS)}",
            file=sys.stderr,
        )
        return 2

    from postrule.benchmarks.rules import build_reference_rule
    from postrule.ml import SklearnTextHead
    from postrule.research import run_benchmark_experiment

    ds = _load_bench(args.benchmark)
    rule = build_reference_rule(
        ds.train,
        seed_size=args.seed_size,
        keywords_per_label=args.kw_per_label,
        shuffle=not args.no_shuffle,
        shuffle_seed=args.shuffle_seed,
    ).as_callable()
    head = SklearnTextHead(min_outcomes=args.min_train_for_ml)

    model = _build_lm(args) if args.lm else None
    checkpoints = run_benchmark_experiment(
        train=ds.train,
        test=ds.test,
        rule=rule,
        ml_head=head,
        checkpoint_every=args.checkpoint_every,
        min_train_for_ml=args.min_train_for_ml,
        max_train=args.max_train,
        model=model,
        lm_labels=ds.labels,
        lm_test_sample_size=args.lm_test_sample,
    )

    summary = {
        "benchmark": ds.name,
        "labels": len(ds.labels),
        "train_rows": len(ds.train),
        "test_rows": len(ds.test),
        "seed_size": args.seed_size,
        "kw_per_label": args.kw_per_label,
        "shuffle": not args.no_shuffle,
        "shuffle_seed": args.shuffle_seed,
        "checkpoint_every": args.checkpoint_every,
        "citation": ds.citation,
    }
    print(json.dumps({"kind": "summary", **summary}))
    for cp in checkpoints:
        print(json.dumps({"kind": "checkpoint", **asdict(cp)}))

    return 0


def _build_lm(args: argparse.Namespace):
    """Factory - map CLI args to an ModelClassifier instance."""
    from postrule.models import (
        AnthropicAdapter,
        LlamafileAdapter,
        OllamaAdapter,
        OpenAIAdapter,
    )

    kind = args.lm
    if kind == "ollama":
        return OllamaAdapter(model=args.lm_id or "qwen2.5:7b")
    if kind == "llamafile":
        return LlamafileAdapter(model=args.lm_id or "LLaMA_CPP")
    if kind == "openai":
        return OpenAIAdapter(model=args.lm_id or "gpt-4o-mini")
    if kind == "anthropic":
        return AnthropicAdapter(model=args.lm_id or "claude-haiku-4-5")
    raise ValueError(f"unknown llm backend {kind!r}")


def cmd_analyze(args: argparse.Namespace) -> int:
    """Scan a codebase for classification sites."""
    from postrule.analyzer import (
        analyze,
        project_savings,
        render_json,
        render_markdown,
        render_text,
    )

    _refresh_cohort_defaults_async()
    report = analyze(args.path)

    sort_key = getattr(args, "sort", "priority")
    reverse = bool(getattr(args, "reverse", False))

    if args.format == "json":
        print(render_json(report, sort_key=sort_key, reverse=reverse))
        _emit_analyze_event_if_enrolled(report)
        return 0

    if args.format == "markdown":
        projections = project_savings(report) if args.project_savings else None
        print(
            render_markdown(
                report,
                projections=projections,
                sort_key=sort_key,
                reverse=reverse,
            )
        )
        _emit_analyze_event_if_enrolled(report)
        return 0

    # Default: text.
    print(render_text(report, sort_key=sort_key, reverse=reverse))
    if args.project_savings and report.sites:
        print()
        print("Run with --format markdown for the savings projection table.")

    # --report writes a discovery markdown alongside the terminal output
    if getattr(args, "report", False):
        _write_discovery_report(report, args)

    _bump_counter("analyze_count")
    _maybe_nudge_signup()
    _emit_analyze_event_if_enrolled(report)
    return 0


def _write_discovery_report(report, args) -> None:
    """Write the initial-analysis discovery report. Best-effort."""
    try:
        from postrule.cloud.report import render_discovery_report

        cohort_size = 0
        try:
            from postrule.insights import load_cached_or_baked_in

            defaults = load_cached_or_baked_in()
            cohort_size = defaults.cohort_size
        except Exception:  # noqa: BLE001
            pass

        markdown = render_discovery_report(
            report,
            cost_per_call=getattr(args, "cost_per_call", None),
            llm_provider_hint=getattr(args, "llm_provider", "default"),
            cohort_size=cohort_size,
        )
        out_path = (
            Path(args.report_out)
            if getattr(args, "report_out", None)
            else Path("postrule/results/_initial-analysis.md")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print()
        print(f"Wrote discovery report to {out_path}")
    except Exception as e:  # noqa: BLE001 — discovery report is best-effort
        print(f"  (discovery report not written: {type(e).__name__})", file=sys.stderr)


def _refresh_cohort_defaults_async() -> None:
    """Kick off a background refresh of the cohort-tuned defaults.

    Non-blocking. The fetch happens on a daemon thread; if the cache
    is fresh the call no-ops in microseconds. Available to ALL users
    (not gated on enrollment) — receiving aggregate cohort wisdom
    doesn't require sharing data, only contributing back does.

    Disabled by ``POSTRULE_NO_INSIGHTS_FETCH=1`` for air-gapped or
    privacy-paranoid users. The flag is documented but not surfaced
    as a CLI arg (this is "configuration of last resort," not a UX).
    """
    import os

    if os.environ.get("POSTRULE_NO_INSIGHTS_FETCH"):
        return
    try:
        from postrule.insights import refresh_if_stale_async
    except ImportError:
        return
    try:
        refresh_if_stale_async()
    except Exception:  # noqa: BLE001 — telemetry must never break the CLI
        return


def _emit_analyze_event_if_enrolled(report) -> None:
    """Queue an analyze event for the cohort, no-op if not enrolled.

    Failures are silent — telemetry must never break the analyze command.
    Run-level histograms only; no per-site granularity (privacy posture).
    """
    try:
        from postrule.insights import flush_queue_async, is_enrolled, queue_event
    except ImportError:
        return
    if not is_enrolled():
        return
    try:
        pattern_hist: dict[str, int] = {}
        regime_hist: dict[str, int] = {}
        lift_status_hist: dict[str, int] = {}
        hazard_hist: dict[str, int] = {}
        for site in report.sites:
            pattern_hist[site.pattern] = pattern_hist.get(site.pattern, 0) + 1
            regime_hist[site.regime] = regime_hist.get(site.regime, 0) + 1
            lift_status_hist[site.lift_status] = lift_status_hist.get(site.lift_status, 0) + 1
            for h in site.hazards:
                hazard_hist[h.category] = hazard_hist.get(h.category, 0) + 1
        queue_event(
            "analyze",
            payload={
                "files_scanned": report.files_scanned,
                "total_sites": report.total_sites(),
                "already_dendrified_count": report.already_dendrified_count(),
                "pattern_histogram": pattern_hist,
                "regime_histogram": regime_hist,
                "lift_status_histogram": lift_status_hist,
                "hazard_category_histogram": hazard_hist,
            },
        )
        # Best-effort flush in the background. Daemon thread, no join.
        flush_queue_async()
    except Exception:  # noqa: BLE001 — telemetry must never break the CLI
        return


def cmd_report(args: argparse.Namespace) -> int:
    """Generate a graduation report card.

    Two modes:
      - ``postrule report <switch>``: per-switch card matching the
        locked sample at docs/working/sample-reports/triage_rule.md.
      - ``postrule report --summary``: project-level rollup matching
        docs/working/sample-reports/_summary.md.
    """
    from pathlib import Path

    from postrule.cloud.report import (
        aggregate_project,
        aggregate_switch,
        render_project_summary,
        render_switch_card,
    )

    if not args.summary and not args.switch:
        print(
            "postrule report: provide a switch name or pass --summary",
            file=sys.stderr,
        )
        return 2
    if args.summary and args.switch:
        print(
            "postrule report: --summary and <switch> are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    storage_path = Path(args.storage_path)
    if args.storage == "file":
        from postrule.storage import FileStorage

        storage_path.mkdir(parents=True, exist_ok=True)
        storage = FileStorage(storage_path)
    elif args.storage == "sqlite":
        from postrule.storage import SqliteStorage

        storage = SqliteStorage(storage_path)
    else:  # memory
        from postrule.storage import InMemoryStorage

        storage = InMemoryStorage()

    # ---- Project summary path -----------------------------------------
    if args.summary:
        try:
            summary = aggregate_project(storage, alpha=args.alpha)
        except AttributeError as e:
            print(
                f"postrule report --summary: {e}\n"
                f"Hint: --storage memory has no switch_names() method; "
                f"use file or sqlite storage.",
                file=sys.stderr,
            )
            return 2

        project_name = Path.cwd().name or "(this project)"
        markdown = render_project_summary(summary, project_name=project_name)

        out_path = Path(args.out) if args.out else Path("postrule/results/_summary.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"Wrote {out_path}")
        print(f"  switches:         {len(summary.switches)}")
        print(f"  graduated:        {summary.graduated_count}")
        print(f"  in-flight:        {summary.pre_graduation_count}")
        print(f"  drift events:     {summary.drift_count}")
        print(f"  total outcomes:   {summary.total_outcomes:,}")
        return 0

    # ---- Per-switch path ----------------------------------------------
    metrics = aggregate_switch(
        storage,
        args.switch,
        alpha=args.alpha,
    )

    out_path = Path(args.out) if args.out else Path("postrule/results") / f"{args.switch}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to generate PNG charts if matplotlib is installed and the
    # switch has at least one checkpoint. Failures fall back to the
    # text-only placeholders that render_switch_card emits when no
    # chart paths are supplied.
    transition_path: str | None = None
    pvalue_path: str | None = None
    cost_path: str | None = None
    if metrics.checkpoints:
        try:
            from postrule.cloud.report import charts

            base = out_path.parent / args.switch
            transition_path = str(
                charts.transition_curve(metrics, base.with_suffix(".transition.png")).name
            )
            pvalue_path = str(
                charts.pvalue_trajectory(
                    metrics, base.with_suffix(".pvalue.png"), alpha=args.alpha
                ).name
            )
            if args.cost_per_call is not None:
                cost_path = str(
                    charts.cost_trajectory(
                        metrics,
                        base.with_suffix(".cost.png"),
                        cost_per_call=args.cost_per_call,
                    ).name
                )
        except ImportError:
            print("  (install postrule[viz] to generate chart PNGs)")
        except Exception as e:  # noqa: BLE001 — never fail the report on chart errors
            print(f"  (chart rendering skipped: {e})")

    markdown = render_switch_card(
        metrics,
        alpha=args.alpha,
        cost_per_call=args.cost_per_call,
        estimated_calls_per_month=args.calls_per_month,
        transition_chart_path=transition_path,
        pvalue_chart_path=pvalue_path,
        cost_chart_path=cost_path,
    )

    out_path.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"  switch:           {args.switch}")
    print(f"  outcomes:         {metrics.total_outcomes}")
    print(f"  current phase:    {metrics.current_phase.value}")
    if metrics.gate_fire_outcome is not None:
        print(
            f"  gate fired at:    outcome {metrics.gate_fire_outcome} "
            f"(p = {metrics.gate_fire_p_value:.4g})"
        )
    elif metrics.total_outcomes == 0:
        print("  status:           wrapped, no outcomes yet — will fill in over time")
    else:
        print("  status:           accumulating evidence (gate not yet fired)")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Wrap a target function with @ml_switch via AST injection."""
    from postrule.wrap import WrapError, wrap_function

    try:
        file_path, function_name = args.target.rsplit(":", 1)
    except ValueError:
        print(
            f"target must be FILE:FUNCTION (got {args.target!r})",
            file=sys.stderr,
        )
        return 2

    path = Path(file_path)
    if not path.exists():
        print(f"file not found: {file_path}", file=sys.stderr)
        return 2

    source = path.read_text(encoding="utf-8")
    labels = None
    if args.labels:
        labels = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()]

    try:
        result = wrap_function(
            source,
            function_name,
            author=args.author,
            labels=labels,
            phase=args.phase,
            safety_critical=args.safety_critical,
        )
    except WrapError as e:
        print(f"postrule init: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(result.diff(filename=file_path))
        print(
            f"\n# would modify {file_path}: "
            f"{len(result.labels)} labels "
            f"({'inferred' if result.inferred_labels else 'supplied'}), "
            f"phase={args.phase}"
            f"{', safety_critical' if args.safety_critical else ''}",
            file=sys.stderr,
        )
        return 0

    path.write_text(result.modified_source, encoding="utf-8")
    print(
        f"wrapped {function_name} in {file_path} with "
        f"{len(result.labels)} labels "
        f"({'inferred' if result.inferred_labels else 'supplied'})",
        file=sys.stderr,
    )

    # Auto-generate the pre-registered hypothesis file. Idempotent —
    # if the user already has one for this switch, skip.
    _try_generate_hypothesis(
        switch_name=function_name,
        file_path=file_path,
        function_name=function_name,
        labels=result.labels,
    )

    # --auto-lift: run the lifters on the (now-wrapped) source and emit
    # a Switch subclass into __postrule_generated__/. The basic decorator
    # was already applied above so the user gets *something* even if
    # the lifter refuses.
    if getattr(args, "auto_lift", False):
        _try_auto_lift(
            source_path=path,
            function_name=function_name,
            original_source=source,
        )

    if getattr(args, "with_benchmarks", False):
        _try_emit_benchmarks(
            source_path=path,
            function_name=function_name,
            original_source=source,
        )

    _bump_counter("init_count")
    state = _load_state()
    # The first successful `init` is a teachable moment - show the nudge
    # once even if the analyze threshold has not been reached.
    if (
        int(state.get("init_count", 0)) == 1
        and not auth.is_logged_in()
        and not state.get("nudge_shown")
    ):
        print(
            "\nLoving Postrule? Sign up for a free account to enable shared "
            "team analysis: postrule login",
            file=sys.stderr,
        )
        state["nudge_shown"] = True
        _save_state(state)
    return 0


def cmd_roi(args: argparse.Namespace) -> int:
    from postrule.roi import (
        ROIAssumptions,
        compute_portfolio_roi,
        format_portfolio_report,
    )
    from postrule.storage import FileStorage

    storage = FileStorage(args.storage)
    overrides = {}
    if args.engineer_cost_per_week is not None:
        overrides["engineer_cost_per_week_usd"] = args.engineer_cost_per_week
    if args.monthly_value_low is not None:
        overrides["monthly_value_per_site_low_usd"] = args.monthly_value_low
    if args.monthly_value_high is not None:
        overrides["monthly_value_per_site_high_usd"] = args.monthly_value_high
    assumptions = ROIAssumptions(**overrides) if overrides else ROIAssumptions()
    rois = compute_portfolio_roi(storage=storage, assumptions=assumptions)
    if args.json:
        import json
        from dataclasses import asdict

        print(
            json.dumps(
                {
                    "assumptions": asdict(assumptions),
                    "switches": [asdict(r) for r in rois],
                },
                indent=2,
            )
        )
    else:
        print(format_portfolio_report(rois, assumptions=assumptions))
    return 0


_QUICKSTART_EXAMPLES = {
    "hello": ("01_hello_world.py", "smallest example - rule + dispatch"),
    "tournament": ("21_tournament.py", "pick among N candidates with statistical confidence"),
    "autoresearch": ("19_autoresearch_loop.py", "Karpathy-style propose / evaluate / reflect loop"),
    "verifier": ("20_verifier_default.py", "autonomous-verification default"),
    "exception": ("17_exception_handling.py", "Postrule as a try/except-tree replacement"),
}


def cmd_quickstart(args: argparse.Namespace) -> int:
    """Copy an example into the cwd (or path) and run it.

    The fastest path from `pip install postrule` to "I see it work."
    No git clone, no `cd examples/` - `postrule quickstart` and you're
    looking at output.
    """
    import shutil
    import subprocess
    from pathlib import Path

    if args.list:
        print("Available quickstart examples:")
        for key, (filename, desc) in _QUICKSTART_EXAMPLES.items():
            print(f"  {key:14s} - {desc}")
            print(f"  {'':14s}    ({filename})")
        return 0

    if args.example not in _QUICKSTART_EXAMPLES:
        print(
            f"unknown example {args.example!r}; choose from {sorted(_QUICKSTART_EXAMPLES)}",
            file=sys.stderr,
        )
        return 2

    filename, desc = _QUICKSTART_EXAMPLES[args.example]

    # Locate the example. Three cases, tried in order:
    #  1. Editable install / source checkout - examples/ sits next to src/
    #  2. Wheel install - examples bundled under postrule/_examples/
    #  3. Last-ditch fallback - fetch from public repo (only works
    #     post-launch, requires network)
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    local_example = repo_root / "examples" / filename
    bundled_example = here.parent / "_examples" / filename

    target_dir = Path(args.target).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / filename

    if local_example.exists():
        shutil.copy2(local_example, target_file)
        # Demo stubs file ships alongside several examples.
        local_stubs = repo_root / "examples" / "_stubs.py"
        if local_stubs.exists():
            shutil.copy2(local_stubs, target_dir / "_stubs.py")
        source = f"local source ({local_example})"
    elif bundled_example.exists():
        shutil.copy2(bundled_example, target_file)
        bundled_stubs = here.parent / "_examples" / "_stubs.py"
        if bundled_stubs.exists():
            shutil.copy2(bundled_stubs, target_dir / "_stubs.py")
        source = f"bundled with postrule package ({bundled_example})"
    else:
        import urllib.error
        import urllib.request

        # Last-ditch: fetch from public repo (requires repo to be public + network).
        url_base = "https://raw.githubusercontent.com/b-tree-labs/postrule/main/examples"
        url = f"{url_base}/{filename}"
        try:
            urllib.request.urlretrieve(url, target_file)
            try:
                urllib.request.urlretrieve(f"{url_base}/_stubs.py", target_dir / "_stubs.py")
            except urllib.error.URLError:
                # _stubs.py is optional for some examples; skip silently
                pass
            source = url
        except urllib.error.URLError as e:
            print(
                f"Could not fetch example from {url}: {e}\n"
                f"Recovery: clone the repo "
                f"(git clone https://github.com/b-tree-labs/postrule) "
                f"and run `python examples/{filename}` directly.",
                file=sys.stderr,
            )
            return 1

    print(f"copied {filename} from {source}")
    print(f"  → {target_file}")
    if args.no_run:
        print(f"\nrun with: python {target_file}")
        return 0
    print(f"\nrunning python {target_file} ...\n" + "-" * 60, flush=True)
    rc = subprocess.run([sys.executable, str(target_file)]).returncode
    print("-" * 60, flush=True)
    if rc == 0:
        print(f"done. The script lives at {target_file} - edit + re-run as you experiment.")
    return rc


def cmd_refresh(args: argparse.Namespace) -> int:
    """Walk a project for `__postrule_generated__/` files; check or
    regenerate them against the current source.

    Modes:
      --check : exit non-zero if anything would be regenerated (CI gate).
      (default): regenerate stale files; refuse user-edited ones unless
                 --force is also passed.
    """
    from postrule import refresh as refresh_mod

    root = Path(args.path).resolve() if args.path else Path.cwd()
    if not root.exists():
        sys.stderr.write(f"path does not exist: {root}\n")
        return 2

    # Find all generated files under root: any *.py in a directory named
    # __postrule_generated__.
    #
    # SECURITY: refuse to follow symlinks that escape the project root.
    # ``Path.rglob`` resolves directory entries by default, so a
    # ``__postrule_generated__`` symlink pointing outside ``root`` would
    # otherwise let the walker glob (and parse-read) arbitrary *.py
    # files on the filesystem. No code is executed - parse_generated_header
    # only reads the header - but presenting an out-of-tree path is
    # itself a leak. We require both the symlink itself AND each *.py
    # under it to resolve under ``root`` before honoring them.
    root_resolved = root.resolve()
    generated_files: list[Path] = []
    for gen_dir in root.rglob("__postrule_generated__"):
        if not gen_dir.is_dir():
            continue
        try:
            gen_resolved = gen_dir.resolve()
        except OSError:
            continue
        if not gen_resolved.is_relative_to(root_resolved):
            sys.stderr.write(
                f"refusing to walk {gen_dir}: resolves outside project root "
                f"{root} (symlink escape).\n"
            )
            continue
        for f in gen_dir.glob("*.py"):
            if f.name == "__init__.py":
                continue
            try:
                f_resolved = f.resolve()
            except OSError:
                continue
            if not f_resolved.is_relative_to(root_resolved):
                sys.stderr.write(
                    f"refusing to inspect {f}: resolves outside project root {root}.\n"
                )
                continue
            generated_files.append(f)

    if not generated_files:
        print(f"No Postrule-generated files found under {root}.")
        return 0

    counts = {
        refresh_mod.DriftStatus.UP_TO_DATE: 0,
        refresh_mod.DriftStatus.SOURCE_DRIFT: 0,
        refresh_mod.DriftStatus.USER_EDITED: 0,
        refresh_mod.DriftStatus.MISSING_GENERATED: 0,
        refresh_mod.DriftStatus.ORPHANED: 0,
    }
    drifted: list[tuple[Path, refresh_mod.DriftStatus, str]] = []

    for gen_path in generated_files:
        try:
            header = refresh_mod.parse_generated_header(gen_path.read_text())
        except ValueError as e:
            sys.stderr.write(f"{gen_path}: malformed header ({e})\n")
            continue
        # Resolve source path from module name. Convention: source lives
        # one directory up (sibling of __postrule_generated__/). If the
        # module's last segment matches a .py file in the parent dir,
        # use that. Otherwise skip with a diagnostic.
        parent = gen_path.parent.parent
        last_segment = header.source_module.rsplit(".", 1)[-1]
        candidate = parent / f"{last_segment}.py"
        if not candidate.exists():
            sys.stderr.write(
                f"{gen_path}: cannot locate source file for "
                f"{header.source_module}:{header.source_function}\n"
            )
            continue
        status = refresh_mod.detect_drift(candidate, header.source_function, gen_path)
        counts[status] += 1
        if status is not refresh_mod.DriftStatus.UP_TO_DATE:
            drifted.append((gen_path, status, header.source_function))

    print(
        f"Scanned {len(generated_files)} generated file(s) under {root}:\n"
        f"  up_to_date:        {counts[refresh_mod.DriftStatus.UP_TO_DATE]}\n"
        f"  source_drift:      {counts[refresh_mod.DriftStatus.SOURCE_DRIFT]}\n"
        f"  user_edited:       {counts[refresh_mod.DriftStatus.USER_EDITED]}\n"
        f"  orphaned:          {counts[refresh_mod.DriftStatus.ORPHANED]}\n"
        f"  missing_generated: {counts[refresh_mod.DriftStatus.MISSING_GENERATED]}\n"
    )
    if drifted:
        print("Drift details:")
        for gen_path, status, fn_name in drifted:
            print(f"  [{status.value}] {gen_path.relative_to(root)} (function {fn_name!r})")

    needs_regen = (
        counts[refresh_mod.DriftStatus.SOURCE_DRIFT]
        + counts[refresh_mod.DriftStatus.MISSING_GENERATED]
    )
    needs_attention = (
        needs_regen
        + counts[refresh_mod.DriftStatus.USER_EDITED]
        + counts[refresh_mod.DriftStatus.ORPHANED]
    )

    if args.check:
        return 0 if needs_attention == 0 else 1

    # Default mode: actually regenerate. v1 cuts this scope: print what
    # WOULD be regenerated and direct user to run the lifters explicitly.
    # Auto-regeneration (re-invoking the lifter) lands once the lifter
    # CLI surface is stable.
    if needs_regen:
        print(
            f"\nWould regenerate {needs_regen} file(s). Re-run "
            "`postrule init --auto-lift <file>:<func>` for each, or pass "
            "--check to use this command as a CI gate only."
        )
    if counts[refresh_mod.DriftStatus.USER_EDITED] and not args.force:
        print(
            f"\nRefused to touch {counts[refresh_mod.DriftStatus.USER_EDITED]} "
            "user-edited file(s). Pass --force to overwrite."
        )
        return 1
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnostic walk: report missing, orphaned, and corrupted generated
    files. Read-only; never modifies anything.
    """
    from postrule import refresh as refresh_mod

    root = Path(args.path).resolve() if args.path else Path.cwd()
    if not root.exists():
        sys.stderr.write(f"path does not exist: {root}\n")
        return 2

    print(f"Postrule doctor: scanning {root}\n")
    issues = 0

    for gen_dir in root.rglob("__postrule_generated__"):
        if not gen_dir.is_dir():
            continue
        for gen_path in gen_dir.glob("*.py"):
            if gen_path.name == "__init__.py":
                continue
            try:
                header = refresh_mod.parse_generated_header(gen_path.read_text())
            except ValueError as e:
                print(f"  [malformed] {gen_path.relative_to(root)}: {e}")
                issues += 1
                continue
            parent = gen_path.parent.parent
            last_segment = header.source_module.rsplit(".", 1)[-1]
            candidate = parent / f"{last_segment}.py"
            if not candidate.exists():
                print(
                    f"  [orphan-source] {gen_path.relative_to(root)}: "
                    f"source file for {header.source_module} not found"
                )
                issues += 1
                continue
            status = refresh_mod.detect_drift(candidate, header.source_function, gen_path)
            if status is refresh_mod.DriftStatus.UP_TO_DATE:
                continue
            print(
                f"  [{status.value}] {gen_path.relative_to(root)} "
                f"(function {header.source_function!r})"
            )
            issues += 1

    if issues == 0:
        print("All generated files are healthy.")
        return 0
    print(f"\n{issues} issue(s) found. Run `postrule refresh` to repair stale files.")
    return 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run the Postrule MCP server over stdio.

    Exposes Postrule's CLI surface (analyze, init, refresh, doctor) as
    Model Context Protocol tools so Claude Code (and other MCP-aware
    agents) can drive Postrule inside an existing codebase.

    The mcp Python package is an optional dependency; install it with
    ``pip install postrule[mcp]`` if it is missing.
    """
    try:
        from postrule.mcp_server import serve_stdio
    except ImportError as e:
        print(
            f"postrule mcp: {e}\nHint: pip install 'postrule[mcp]'",
            file=sys.stderr,
        )
        return 2
    serve_stdio()
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run the close-the-loop benchmark for one switch.

    Resolves the generated bench module under
    ``__postrule_generated__/<file_stem>__<function>_bench.py`` and
    shells out to pytest. The bench module persists one JSONL row to
    ``runtime/postrule/<switch>/benchmarks/<UTC-iso>.jsonl`` per run.

    On success: prints headline numbers (latest run vs. baseline run,
    or ``first run`` when no prior file exists). Exit code 0 on parity
    within tolerance; non-zero on regression.
    """
    from postrule.benchmarks import aggregate_report, run_benchmark_pytest

    try:
        file_path, function_name = args.target.rsplit(":", 1)
    except ValueError:
        print(
            f"target must be FILE:FUNCTION (got {args.target!r})",
            file=sys.stderr,
        )
        return 2

    src_path = Path(file_path)
    bench_path = (
        src_path.parent / "__postrule_generated__" / f"{src_path.stem}__{function_name}_bench.py"
    )
    if not bench_path.exists():
        print(
            f"postrule benchmark: no bench stub at {bench_path}.\n"
            f"Run `postrule init --auto-lift --with-benchmarks {args.target}` "
            f"to generate it.",
            file=sys.stderr,
        )
        return 2

    rc = run_benchmark_pytest(
        bench_module_path=bench_path,
        pytest_args=(["--tb=short"] if args.measure_real_cost else None),
    )

    # Surface the latest persisted line versus the prior baseline.
    runtime_root = Path(args.runtime) if args.runtime else Path("runtime")
    report = aggregate_report(runtime_root)
    target = next((s for s in report.switches if s.switch_name == function_name), None)
    if target is None:
        print("postrule benchmark: ran, but no JSONL files were persisted.", file=sys.stderr)
        return rc or 1
    if target.n_runs == 1:
        print(
            f"{function_name}: first run "
            f"(phase={target.latest_phase}, "
            f"cost ${target.cost_latest_low:.5f}-${target.cost_latest_high:.5f}/call)"
        )
    else:
        pct = target.cost_pct_change_low
        pct_label = f"{pct:+.1f}%" if pct is not None else "n/a"
        print(
            f"{function_name}: phase {target.first_phase} -> {target.latest_phase}, "
            f"cost ${target.cost_baseline_low:.5f} -> ${target.cost_latest_low:.5f}/call "
            f"({pct_label})"
        )
    return rc


def cmd_bench_report(args: argparse.Namespace) -> int:
    """Walk runtime/postrule/*/benchmarks/*.jsonl and print the report."""
    from postrule.benchmarks import aggregate_report, format_report

    runtime_root = Path(args.runtime) if args.runtime else Path("runtime")
    report = aggregate_report(runtime_root)
    print(format_report(report))
    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    from postrule.viz import load_run, plot_transition_curves

    runs = [load_run(p) for p in args.jsonl]
    plot_transition_curves(
        runs,
        output_path=args.output,
        title=args.title,
    )
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from postrule import __version__

    parser = argparse.ArgumentParser(prog="postrule")
    parser.add_argument(
        "--version",
        action="version",
        version=f"postrule {__version__}",
    )
    # Bare `postrule` (no subcommand) routes to cmd_status - the soft entry
    # point for new users. Subcommands are still discoverable via --help.
    parser.set_defaults(fn=cmd_status, cmd=None)
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_bench = sub.add_parser(
        "bench",
        help="Run the transition-curve experiment on a public benchmark.",
    )
    p_bench.add_argument(
        "benchmark",
        choices=sorted(_BENCHMARKS),
        help="Benchmark ID (banking77 | clinc150 | hwu64 | atis | snips).",
    )
    p_bench.add_argument(
        "--seed-size",
        type=int,
        default=100,
        help="Training examples used to construct the rule (paper §4.2).",
    )
    p_bench.add_argument("--kw-per-label", type=int, default=5, help="Keywords selected per label.")
    p_bench.add_argument(
        "--no-shuffle",
        action="store_true",
        help=(
            "Disable the deterministic shuffle of the training stream "
            "before the seed window is taken. The default shuffles with "
            "seed 0 so label-sorted upstream splits (Banking77, HWU64, "
            "CLINC150, Snips on HuggingFace) cannot collapse the rule "
            "to a single label. Pass --no-shuffle to reproduce the v0.x "
            "paper-as-shipped behavior."
        ),
    )
    p_bench.add_argument(
        "--shuffle-seed",
        type=int,
        default=0,
        help=(
            "Seed for the deterministic training-stream shuffle. "
            "Repeated runs with the same seed produce the same rule. "
            "Ignored when --no-shuffle is set."
        ),
    )
    p_bench.add_argument(
        "--checkpoint-every", type=int, default=250, help="Outcomes between checkpoints."
    )
    p_bench.add_argument(
        "--min-train-for-ml",
        type=int,
        default=100,
        help="Smallest training count at which the ML head is fit.",
    )
    p_bench.add_argument(
        "--max-train", type=int, default=None, help="Cap on training examples (smoke-test knob)."
    )
    p_bench.add_argument(
        "--lm",
        choices=["ollama", "llamafile", "openai", "anthropic"],
        default=None,
        help="Enable language model shadow evaluation with the named provider.",
    )
    p_bench.add_argument(
        "--lm-id", default=None, help="Model ID passed to the language-model provider."
    )
    p_bench.add_argument(
        "--lm-test-sample",
        type=int,
        default=None,
        help="Subsample size for language model test evaluation (default: full test set).",
    )
    p_bench.set_defaults(fn=cmd_bench)

    p_analyze = sub.add_parser(
        "analyze",
        help="Find classification sites in a codebase.",
    )
    p_analyze.add_argument(
        "path",
        help="Path to scan (file or directory).",
    )
    p_analyze.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format. Default: text (human-readable).",
    )
    p_analyze.add_argument(
        "--json",
        action="store_true",
        help="Shortcut for --format=json.",
    )
    p_analyze.add_argument(
        "--project-savings",
        action="store_true",
        help="Include per-site annual savings projection (uses postrule.roi default cost model).",
    )
    p_analyze.add_argument(
        "--report",
        action="store_true",
        help=(
            "Write a discovery report at postrule/results/_initial-analysis.md. "
            "Customer-facing opportunity assessment with ranked sites, "
            "cohort-predicted graduation times, projected savings, and "
            "recommended graduation sequence."
        ),
    )
    p_analyze.add_argument(
        "--report-out",
        default=None,
        help=(
            "Override the output path for --report "
            "(default: postrule/results/_initial-analysis.md)."
        ),
    )
    p_analyze.add_argument(
        "--cost-per-call",
        type=float,
        default=None,
        help=(
            "Estimated $/LLM call for cost projections in --report. "
            "Defaults to a frontier-LLM rate of $0.0042/call."
        ),
    )
    p_analyze.add_argument(
        "--llm-provider",
        default="default",
        choices=["openai", "anthropic", "haiku", "ollama", "default"],
        help=(
            "Hint for default --cost-per-call when not supplied explicitly. "
            "Default: 'default' (~frontier-LLM rate)."
        ),
    )
    p_analyze.add_argument(
        "--sort",
        choices=["priority", "location", "pattern", "regime", "lift"],
        default="priority",
        help=(
            "Sort detected sites by: priority (default — composite of "
            "graduation-fitness, volume estimate, lift status), location "
            "(file:line), pattern (P1..P6), regime, or lift status."
        ),
    )
    p_analyze.add_argument(
        "--reverse",
        action="store_true",
        help="Reverse the sort order.",
    )
    p_analyze.set_defaults(fn=cmd_analyze)

    # Back-compat: --json flag overrides --format.
    def _analyze_wrapper(args: argparse.Namespace) -> int:
        if args.json:
            args.format = "json"
        return cmd_analyze(args)

    p_analyze.set_defaults(fn=_analyze_wrapper)

    p_report = sub.add_parser(
        "report",
        help=(
            "Generate a graduation report card for a switch. Reads the "
            "switch's audit chain, computes transition-curve metrics, "
            "writes markdown to postrule/results/<switch>.md. "
            "Pass --summary instead of <switch> for a project-level rollup."
        ),
    )
    p_report.add_argument(
        "switch",
        nargs="?",
        default=None,
        help=(
            "Switch name (matches LearnedSwitch(name=...) at construction). "
            "Omit when using --summary."
        ),
    )
    p_report.add_argument(
        "--summary",
        action="store_true",
        help=(
            "Generate a project-level rollup across all switches in the "
            "configured storage backend; writes postrule/results/_summary.md."
        ),
    )
    p_report.add_argument(
        "--storage",
        default="file",
        choices=["file", "sqlite", "memory"],
        help="Storage backend to read from (default: file).",
    )
    p_report.add_argument(
        "--storage-path",
        default=".postrule/storage",
        help="Path to the storage backend (default: .postrule/storage).",
    )
    p_report.add_argument(
        "--out",
        default=None,
        help="Output path (default: postrule/results/<switch>.md).",
    )
    p_report.add_argument(
        "--cost-per-call",
        type=float,
        default=None,
        help="Estimated $ per pre-graduation LLM call (for cost section).",
    )
    p_report.add_argument(
        "--calls-per-month",
        type=int,
        default=None,
        help="Estimated monthly call count (for monthly-savings row).",
    )
    p_report.add_argument(
        "--alpha",
        type=float,
        default=0.01,
        help="Gate threshold (default: 0.01, matches paper §3.2).",
    )
    p_report.set_defaults(fn=cmd_report)

    p_init = sub.add_parser(
        "init",
        help="Wrap a target function with @ml_switch via AST injection.",
    )
    p_init.add_argument(
        "target",
        help="FILE:FUNCTION - e.g., src/triage.py:triage_ticket",
    )
    p_init.add_argument(
        "--author",
        required=True,
        help="Principal identifier in Matrix style, e.g., @triage:support",
    )
    p_init.add_argument(
        "--labels",
        help="Comma-separated labels. Inferred from return statements if omitted.",
    )
    p_init.add_argument(
        "--phase",
        default="RULE",
        choices=[
            "RULE",
            "MODEL_SHADOW",
            "MODEL_PRIMARY",
            "ML_SHADOW",
            "ML_WITH_FALLBACK",
            "ML_PRIMARY",
        ],
        help="Initial phase. Default: RULE.",
    )
    p_init.add_argument(
        "--safety-critical",
        action="store_true",
        help="Set safety_critical=True (caps graduation at Phase 4).",
    )
    p_init.add_argument(
        "--dry-run",
        action="store_true",
        help="Print unified diff instead of modifying the file.",
    )
    p_init.add_argument(
        "--auto-lift",
        action="store_true",
        help=(
            "Also run the branch + evidence lifters and emit a Switch "
            "subclass into __postrule_generated__/ next to the source. "
            "On REFUSAL, prints a specific diagnostic and exits 0 with "
            "the basic decorator still applied."
        ),
    )
    p_init.add_argument(
        "--with-benchmarks",
        action="store_true",
        help=(
            "Also emit a per-switch benchmark stub at "
            "__postrule_generated__/<file>__<func>_bench.py. The stub "
            "persists label-parity, latency, and per-call cost to "
            "runtime/postrule/<switch>/benchmarks/. Use `postrule benchmark` "
            "to run it; `postrule report` to aggregate across switches."
        ),
    )
    p_init.set_defaults(fn=cmd_init)

    p_quick = sub.add_parser(
        "quickstart",
        help="Copy a runnable example into the current directory and run it.",
    )
    p_quick.add_argument(
        "example",
        nargs="?",
        default="tournament",
        help=(
            "Which example to copy. Default: 'tournament' (picks among "
            "N candidates with statistical confidence). "
            "Use --list to see all options."
        ),
    )
    p_quick.add_argument(
        "--target",
        default=".",
        help="Directory to copy the example into. Default: current directory.",
    )
    p_quick.add_argument(
        "--no-run",
        action="store_true",
        help="Copy the file but don't execute it.",
    )
    p_quick.add_argument(
        "--list",
        action="store_true",
        help="List the available examples and exit.",
    )
    p_quick.set_defaults(fn=cmd_quickstart)

    p_roi = sub.add_parser(
        "roi",
        help="Self-measured ROI report from outcome logs under a FileStorage root.",
    )
    p_roi.add_argument("storage", help="FileStorage base path (dir containing per-switch subdirs).")
    p_roi.add_argument("--json", action="store_true", help="Emit JSON instead of a text report.")
    p_roi.add_argument(
        "--engineer-cost-per-week",
        type=float,
        default=None,
        help="Override fully-loaded eng cost in USD/week.",
    )
    p_roi.add_argument(
        "--monthly-value-low",
        type=float,
        default=None,
        help="Override monthly value per site (low bound).",
    )
    p_roi.add_argument(
        "--monthly-value-high",
        type=float,
        default=None,
        help="Override monthly value per site (high bound).",
    )
    p_roi.set_defaults(fn=cmd_roi)

    p_benchmark = sub.add_parser(
        "benchmark",
        help=(
            "Run the close-the-loop benchmark for one switch (label parity "
            "across phases + latency + per-call cost). Persists one JSONL "
            "row to runtime/postrule/<switch>/benchmarks/."
        ),
    )
    p_benchmark.add_argument(
        "target",
        help="FILE:FUNCTION - same shape as `postrule init`.",
    )
    p_benchmark.add_argument(
        "--runtime",
        default=None,
        help="Project runtime root (default: ./runtime).",
    )
    p_benchmark.add_argument(
        "--measure-real-cost",
        action="store_true",
        help=(
            "Calls the configured model adapter and records actual "
            "per-call spend. Off by default to avoid surprise charges."
        ),
    )
    p_benchmark.set_defaults(fn=cmd_benchmark)

    p_bench_report = sub.add_parser(
        "bench-report",
        help=(
            "Aggregate runtime/postrule/*/benchmarks/*.jsonl across all "
            "switches and print a human-readable summary."
        ),
    )
    p_bench_report.add_argument(
        "--runtime",
        default=None,
        help="Project runtime root (default: ./runtime).",
    )
    p_bench_report.set_defaults(fn=cmd_bench_report)

    p_plot = sub.add_parser(
        "plot",
        help="Plot transition curves from one or more `postrule bench` JSONL files.",
    )
    p_plot.add_argument(
        "jsonl", nargs="+", help="One or more JSONL files produced by `postrule bench`."
    )
    p_plot.add_argument(
        "-o", "--output", required=True, help="Output image path (.png / .svg / .pdf)."
    )
    p_plot.add_argument("--title", default="Postrule transition curves", help="Figure title.")
    p_plot.set_defaults(fn=cmd_plot)

    p_login = sub.add_parser(
        "login",
        help="Sign in to Postrule (browser-based, free account).",
    )
    p_login.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't try to open the browser. For headless / SSH / WSL "
        "environments — copy the URL manually.",
    )
    p_login.add_argument(
        "--device-name",
        default=None,
        help="Label shown to you in the dashboard ('which device is asking?'). "
        "Defaults to the OS hostname.",
    )
    p_login.set_defaults(fn=cmd_login)

    p_logout = sub.add_parser(
        "logout",
        help="Clear local Postrule credentials.",
    )
    p_logout.set_defaults(fn=cmd_logout)

    p_whoami = sub.add_parser(
        "whoami",
        help="Show the signed-in account email and a truncated API key.",
    )
    p_whoami.set_defaults(fn=cmd_whoami)

    p_insights = sub.add_parser(
        "insights",
        help="Manage Postrule Insights — opt-in cohort flywheel (enroll/leave/status).",
    )
    p_insights.set_defaults(fn=lambda _a: p_insights.print_help() or 0)
    insights_sub = p_insights.add_subparsers(dest="insights_cmd")

    p_insights_enroll = insights_sub.add_parser(
        "enroll",
        help="Show the disclosure and opt in to Insights.",
    )
    p_insights_enroll.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Auto-confirm the disclosure prompt (for scripted installs on owner-controlled infra)."
        ),
    )
    p_insights_enroll.set_defaults(fn=cmd_insights_enroll)

    p_insights_leave = insights_sub.add_parser(
        "leave",
        help="Opt out of Insights. Local queue is NOT auto-deleted.",
    )
    p_insights_leave.set_defaults(fn=cmd_insights_leave)

    p_insights_status = insights_sub.add_parser(
        "status",
        help="Show Insights enrollment + cohort defaults + pending queue size.",
    )
    p_insights_status.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip the cohort-defaults fetch; show local cache only.",
    )
    p_insights_status.set_defaults(fn=cmd_insights_status)

    p_mcp = sub.add_parser(
        "mcp",
        help=(
            "Run the Postrule MCP server over stdio (for Claude Code et al.). "
            "Requires the mcp extra: pip install 'postrule[mcp]'."
        ),
    )
    p_mcp.set_defaults(fn=cmd_mcp)

    p_refresh = sub.add_parser(
        "refresh",
        help=("Check or regenerate Postrule-generated files in __postrule_generated__/."),
    )
    p_refresh.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Project root to scan (default: current directory).",
    )
    p_refresh.add_argument(
        "--check",
        action="store_true",
        help="Don't regenerate; exit non-zero if any drift found (CI mode).",
    )
    p_refresh.add_argument(
        "--force",
        action="store_true",
        help="Overwrite user-edited generated files (default: refuse).",
    )
    p_refresh.set_defaults(fn=cmd_refresh)

    p_doctor = sub.add_parser(
        "doctor",
        help="Diagnose missing/orphaned/corrupted Postrule-generated files (read-only).",
    )
    p_doctor.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Project root to scan (default: current directory).",
    )
    p_doctor.set_defaults(fn=cmd_doctor)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
