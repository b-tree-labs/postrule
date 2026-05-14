# Postrule test sandbox

This directory's `conftest.py` installs a sandbox harness so that
`pytest tests/` (including the chaos / red-team / perf suites that
land later) cannot escape into the developer's real local
environment.

## What is blocked by default

Every test runs with all three guards active. They are autouse, so
no test has to opt in to be protected.

1. **HOME redirect.** `Path.home()` returns the test's `tmp_path`.
   `HOME`, `USERPROFILE`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, and
   `XDG_DATA_HOME` are pointed at directories under `tmp_path`. A
   test that writes to `~/.postrule/...` ends up writing under
   `tmp_path/.postrule/...` and is cleaned up automatically.

2. **Outbound network block.** `socket.socket.connect` raises
   `RuntimeError("network access blocked in sandbox; ...")` for any
   address that is not loopback. Loopback (`127.0.0.1`, `::1`,
   `localhost`) and unix-socket paths pass through, so in-process
   tests that use a local HTTP server keep working.

3. **External-write block.** `Path.write_text`, `Path.write_bytes`,
   `Path.open` (write modes), and `builtins.open` (write modes)
   raise if the resolved target lies outside `tmp_path`. The
   following targets are explicitly allowed:
   - any path under `tmp_path/**`,
   - any path under the OS temp dir (`tempfile.gettempdir()`,
     `/tmp`, `/private/tmp`, `/var/folders`, `/private/var/folders`),
   - `/dev/null`,
   - in-memory file-likes (`io.StringIO`, `io.BytesIO`, integer
     file descriptors).

   Read modes (`"r"`, `"rb"`) always pass through.

## Opt-in fixtures

Three fixtures relax exactly one guard for one test. Add the
fixture name to the test signature; you do not need to use the
returned value.

| Fixture | Effect |
| --- | --- |
| `network_enabled` | Disable the outbound-network guard for this test. The connect itself still has to succeed; the guard simply does not raise its own `RuntimeError` on top. |
| `home_writable` | Disable the HOME redirect for this test. `Path.home()` and the env vars revert to the developer's real home. |
| `external_writes_allowed` | Disable the external-write guard for this test. Use sparingly; most tests that reach for this actually want `tmp_path`. |

The chaos / red-team / perf suites should never use opt-in fixtures
unless the test explicitly requires unsandboxed behavior, and the
inline comment in the test must say why.

## Correct patterns

```python
def test_writes_use_tmp_path(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("ok")  # allowed

def test_local_http_server():
    # Loopback connect: passes through the network guard.
    s = socket.create_connection(("127.0.0.1", 8080), timeout=0.1)
    s.close()

def test_reads_real_repo_files():
    # Reads are not blocked, only writes.
    Path("pyproject.toml").read_text()
```

## Incorrect patterns (the harness will catch these)

```python
def test_leaks_to_real_home():
    # Raises RuntimeError: external write blocked in sandbox.
    Path.home().joinpath(".postrule/state.toml").write_text("oops")

def test_calls_real_internet():
    # Raises RuntimeError: network access blocked in sandbox.
    socket.create_connection(("api.example.com", 443))

def test_writes_to_arbitrary_path():
    # Raises RuntimeError: external write blocked in sandbox.
    Path("/etc/passwd-attempt").write_text("nope")
```

## When you really do need the unsandboxed behavior

```python
def test_login_with_real_keychain(home_writable):
    # Reading the developer's real keyring is the point of the test.
    creds = auth.load_credentials()
    ...

def test_cdn_smoke(network_enabled):
    # Pinned to a public CDN; documented as integration-only.
    httpx.get("https://example.com/healthz", timeout=2.0)

def test_devshm_perf_probe(external_writes_allowed):
    # Latency probe against /dev/shm; tmp_path is on a slower fs
    # on this CI box and would skew the measurement.
    Path("/dev/shm/probe").write_bytes(b"\x00" * 4096)
```

## What is intentionally not in scope

- **Subprocess sandboxing.** A test that spawns `python -c "..."`
  starts a fresh interpreter that does not load this conftest. Any
  guard the parent test relies on must be re-applied in the child,
  or the child has to be trusted (existing pattern: tests that
  drive subprocesses point them at `tmp_path` explicitly).
- **Environment-variable rollback.** `monkeypatch` already rolls
  env vars back at the end of each test, so the harness does not
  install its own env-var guard. Tests that mutate `os.environ`
  directly without `monkeypatch` are the ones at risk; that is a
  test-style issue, not a sandbox issue.
- **Filesystem cleanup.** `tmp_path` is cleaned up by pytest. The
  harness does not add its own cleanup pass.
