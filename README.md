# SwarmAtlas

**Map your share.** A lightweight, local-first desktop prototype for understanding network activity and the public endpoints your PC is connected to.

## Safety model: read-only by default

SwarmAtlas is a passive monitor. The current prototype only reads operating-system interface counters and the OS socket table.

It does not:
- install kernel drivers, NDIS filters, packet-capture drivers, services, scheduled tasks, or startup entries;
- install or configure WinDivert, Npcap, WinPcap, WFP filters/callouts, or firewall rules;
- change IP addresses, routes, DNS, proxy settings, adapter configuration, or Windows network services;
- open listening ports, create network tunnels, or send traffic on behalf of other applications;
- capture, inspect, or save packet payloads;
- require administrator privileges for interface counters;
- upload IP addresses, telemetry, or usage statistics to SwarmAtlas servers.

If Windows restricts access to the socket table, endpoint discovery may be incomplete. Interface byte counters continue to work. SwarmAtlas does not bypass OS permissions or elevate itself.

The optional GeoIP feature reads a user-selected local .mmdb database. The application does not download it.

Any future feature that needs packet capture or per-flow byte accounting must be separately reviewed, explicitly opt-in, and isolated from the default read-only monitor. No driver or network-filter installation will happen silently.

## Prototype status

| Feature | Status |
|---|---|
| Per-interface upload/download totals | Implemented |
| Current upload/download rate | Implemented |
| Persistent local history (SQLite) | Implemented |
| Active public remote endpoints | Implemented |
| TCP connection ownership (PID/process name) via the optional Rust core | Implemented |
| UDP local socket ownership (PID) via the optional Rust core | Implemented |
| Per-process destination history | Implemented |
| Rust-to-Python native bridge (PyO3) | Implemented; optional build |
| Offline city/country/ASN/ISP lookup using multiple local GeoIP databases | Optional |
| GeoIP readers warmed in the background on startup | Implemented |
| System-matched theme and language | Implemented |
| Native Windows 11 Mica backdrop | Supported on Windows 11 22H2+ |
| One-click project updater | Implemented |
| World map with approximate endpoint locations | Implemented when GeoIP is configured |
| Per-destination / per-country byte totals | Not implemented |
| Russian and English interface | Implemented (system language by default) |
| Dark and light themes | Implemented; preference saved locally |
| Torrent-client integrations | Planned |

**Measurement accuracy:** interface counters give accurate byte totals for the selected adapter, but they do not identify which remote IP received each byte. The current prototype separately discovers active remote IP/port endpoints; it does not assign interface bytes to those endpoints. Per-IP and per-country GB totals are deliberately not displayed until a trustworthy flow-accounting source is implemented.

## Resource-conscious collection

- Interface counters: sampled once per second.
- OS socket/owner tables: scanned every 15 seconds.
- When the optional Rust extension is installed, TCP IPv4/IPv6 ownership is read through Windows IP Helper APIs; UDP local bind ownership is read separately. The Python fallback remains available.
- Endpoint list: capped at 2,000 entries per scan.
- Traffic history: sampled every 10 seconds and retained for 90 days.
- GeoIP results: cached in memory (maximum 8,192 addresses) to avoid repeated disk lookups.
- Selected MMDB readers are opened and warmed on a background worker during startup; the UI remains responsive while they initialize.
- SQLite uses WAL mode and normal synchronous mode.

These are conservative defaults, not a promise of zero CPU usage. Actual overhead depends on the number of active sockets, Windows version, and hardware. The native collector reduces Python-side TCP-table parsing, but no CPU or RAM improvement percentage is claimed until it is benchmarked on real workloads.

## Requirements

- Windows 10/11 (first target)
- Python 3.11 or newer (the Windows launcher selects the newest installed Python 3 runtime)
- A local GeoIP City database in .mmdb format is optional

## Run on Windows

1. Install Python 3.11 or newer and make sure the Python Launcher (\`py\`) is available.
2. Double-click \`run_windows.bat\`.

On the first launch, the launcher creates \`.venv\` and installs SwarmAtlas with its optional GeoIP dependency. An internet connection is needed for this one-time package installation. Later launches reuse the existing environment.

The launcher keeps the console visible and mirrors setup output, application output, warnings, and Python errors to a log file. Logs are stored locally in `%LOCALAPPDATA%\SwarmAtlas\logs\`. The latest 10 log files are retained. No log is uploaded automatically.

When reporting a crash, inspect the log first and remove any private paths or details you do not want to disclose.

To set it up manually from PowerShell:

    py -3 -m venv .venv
    .\.venv\Scripts\python -m pip install -e ".[geoip]"
    .\.venv\Scripts\python -m swarmatlas

The manual command does not use the Windows launcher's log capture.

For development dependencies:

    .\.venv\Scripts\python -m pip install -e ".[geoip,dev]"

## Optional Rust performance core

The application remains Python-first: PySide6, Mica/Fluent styling, localization, settings, SQLite, GeoIP orchestration, and updating stay in Python.

A separate Rust extension handles the Windows connection-owner tables. It uses PyO3 to expose a small native module to Python and the documented read-only IP Helper APIs:

- TCP IPv4 and IPv6: remote/local endpoint, TCP state, and owning PID.
- UDP IPv4 and IPv6: local bind address, port, and owning PID.

The current Windows UDP ownership API reports local socket bindings; it does **not** reveal the remote IP of every datagram. SwarmAtlas only associates a UDP peer when the OS socket table exposes a connected remote endpoint.

The Rust module is optional. Without it, the existing psutil-based Python collector continues to run. The status bar identifies which backend is active.

To build the optional Rust module:

1. Install the stable Rust toolchain (Rust 1.85 or newer) from [rustup.rs](https://rustup.rs/). The Windows MSVC linker and Windows SDK must also be available.
2. Run `build_native.bat` from the SwarmAtlas folder.
3. Restart SwarmAtlas.

To compare the TCP table enumeration time on your own PC, run `\.venv\Scripts\python.exe benchmarks\benchmark_native.py --iterations 50`. The benchmark reports only aggregate timings and row counts; it does not print or save peer IPs or process names. Compare its local results rather than assuming Rust is faster on every machine.

The build uses PyO3 0.30.1 with the stable `abi3-py311` ABI and maturin 1.16.0. The Windows wheel is therefore compatible with regular CPython 3.11 and newer, instead of being tied to the CI Python 3.12 minor version. It compiles locally for the current Windows/Python environment; it does not install drivers or alter Windows networking. GitHub Actions also builds and tests a Windows x64 wheel and retains it as a workflow artifact.

Rust is being introduced incrementally. Interface byte counters, SQLite, UI and GeoIP orchestration remain Python-side for now. Per-IP byte accounting is not implemented by this TCP/UDP table reader.

## Appearance and language

By default, theme and language both follow the operating system. Use the left sidebar to override either setting:

- Theme: System, Dark, or Light.
- Language: System, Русский, or English.

On Windows 11 build 22621 or newer, SwarmAtlas asks Desktop Window Manager for the native Mica main-window backdrop. On unsupported Windows versions, it falls back to the regular dark/light palette. System theme changes are applied while the app is open when Qt reports the updated color scheme.

The dashboard avoids clearing and rebuilding its tables during normal sampling. Existing cells are updated in place, and metric changes use a short fade transition.

## Updating the project

Close SwarmAtlas and double-click `update_project.bat` in the project folder.

- In a Git clone, the updater checks that the working tree is clean and uses a fast-forward-only update.
- In a ZIP-extracted folder, it downloads the latest `main` archive from GitHub and safely overlays project files.
- The updater preserves `.venv`, local MMDB files, SQLite databases, logs, and files outside the repository.
- If a virtual environment already exists, it refreshes the editable package and optional GeoIP dependency.
- If the Rust extension is already installed and the Rust toolchain is available, it also rebuilds the native extension. If the toolchain is missing, it preserves the existing binary and reports that it was not rebuilt.
- It does not silently stash, reset, or rewrite local Git commits.

An internet connection is required. After a successful update, start the app with `run_windows.bat`.

## GeoIP setup

Open **GeoIP database** in the left sidebar and add one or more local `.mmdb` files. City, Country, ASN, and ISP database types can be combined. ASN/ISP details are shown in the recent-destinations table.

The selected databases are read locally. IP addresses are not sent to a remote geolocation provider. The reader files are opened and warmed in the background during cold start; SwarmAtlas does not copy entire databases into RAM.

The official 2ip.io documentation currently describes an online JSON geolocation API that requires a token, rather than a downloadable local MMDB database. Using that API would send each queried peer IP to 2ip and is rate-limited, so it is not enabled in this local-first build.

IP geolocation is approximate. It generally indicates an ISP's registered or egress area, not a person's exact location.

## Local data and privacy

All application data stays on this PC:

- Windows: %APPDATA%\SwarmAtlas\
- Other platforms: ~/.local/share/SwarmAtlas/

The SQLite database stores interface counters, traffic-rate samples, observed public endpoint metadata, and the process ID/name associated with observed connections. It never stores packet contents. No remote geolocation requests or runtime telemetry are sent. Old endpoint records are removed after 30 days; traffic-rate samples are retained for 90 days.

## Current measurement model

- **Upload / Download:** byte deltas from the selected network interface's OS counters.
- **Rate:** counter delta divided by elapsed time.
- **Destinations:** public remote IP/port endpoints visible in the local socket table.
- **Geography:** optional lookup against the local GeoIP database.
- **Per-destination GB:** unavailable until a separate flow-accounting backend is implemented.

## Roadmap

1. Validate passive collection on Windows across Ethernet, Wi-Fi, VPNs, sleep/resume, and heavy P2P workloads.
2. Add a safe, opt-in per-flow accounting backend after documenting its Windows APIs and failure modes.
3. Aggregate measured bytes by country, region, and city.
4. Add qBittorrent and other torrent-client adapters using their documented local APIs.
5. Add packaging and optional startup integration, always user-controlled.

## Development

    python -m pytest
    python -m compileall run_windows.py update_project.py src

## License

MIT. See LICENSE.
