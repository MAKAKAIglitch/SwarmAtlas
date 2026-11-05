# SwarmAtlas

**Network traffic atlas.** A lightweight, local-first Windows desktop monitor that shows whole-adapter traffic rates, observed public network endpoints, their approximate geography, and long-term traffic history. It monitors ordinary application traffic, not only peer-to-peer connections.

## What it measures

SwarmAtlas combines two different Windows data sources:

- **Interface counters:** exact upload/download byte deltas and live rates for the selected network adapter. These include all traffic visible to that adapter.
- **OS connection tables:** public TCP connections and UDP remote endpoints only when Windows exposes a connected remote address, with process ownership where permitted. The map and country/region summaries are built from these endpoints.

**UDP visibility boundary:** Windows' `GetExtendedUdpTable` owner-PID table contains the local IP, local port and owning PID; it does not contain a remote peer IP. Most UDP sockets are unconnected, so Windows and psutil report an empty remote address for them. SwarmAtlas therefore reports local UDP socket counts separately and does not invent UDP peer markers. A future UDP-flow collector may use supported Windows event metadata to identify remote endpoints without saving packet payloads.

The dashboard shows upload and download independently, updates the live rates every second, and saves the traffic history locally.

**Important accuracy boundary:** Whole-adapter counters do not identify which remote IP received each byte. The default connection-table monitor is a socket snapshot, not a per-flow byte counter. On supported Windows builds with the optional Rust `tcp-flow-estats-v1` capability, enabling **Application traffic statistics** collects TCP payload counters per established public TCP connection through Windows TCP ESTATS. Those counters feed per-endpoint upload/download totals and recent rates, which control map marker size and glow. Windows may deny ESTATS access unless SwarmAtlas is run as Administrator; if unavailable, the map clearly has no per-flow traffic data and the ordinary interface monitor continues working. This optional feature is TCP-only: it does not infer UDP byte totals, packet payloads, or bandwidth for every country. The map shows observed endpoints and approximate IP geolocation; its traffic glow is based only on measured TCP flow counters, never guessed values.

## Read-only safety model

The default monitor only reads OS counters, socket tables and process metadata. It does not:

- install kernel drivers, NDIS filters, packet-capture drivers, services, scheduled tasks, or startup entries;
- install or configure WinDivert, Npcap, WinPcap, WFP filters/callouts, or firewall rules;
- change IP addresses, routes, DNS, proxy settings, adapter configuration, or Windows network services;
- open listening ports, create network tunnels, or send traffic on behalf of other applications;
- capture, inspect, or save packet payloads;
- require administrator privileges for interface counters;
- upload telemetry or traffic statistics to SwarmAtlas servers.

The Rust core is an ordinary user-mode Python extension using Windows IP Helper APIs. It is **not** a Windows kernel driver. If it cannot be installed or loaded, the Python/psutil collector remains available. Restricted socket-table access may make endpoint discovery incomplete; interface counters continue to work.

## GeoIP sources

Open **GeoIP provider** in the sidebar to choose one of the following. The default is DB-IP Lite offline mode. The five online choices are opt-in; when one is selected, the public destination IP being looked up is sent to that provider. The provider also sees the user's public source IP as with any online request (and the account token if required). Process names, PIDs, traffic totals and packet contents are not sent. Each provider's API token is stored separately in the local settings file and is not encrypted by SwarmAtlas. A token entered for one provider is not reused for another.

| Source | Mode and useful data | Important terms / limits |
|---|---|---|
| **DB-IP Lite** | Offline City MMDB: country, first-level region, city and approximate coordinates | Default and most private option. CC BY 4.0; attribution is shown in the map. |
| **IP-API.com** | Online country, region, city, coordinates, ISP and ASN/organization | Free endpoint is non-commercial only, HTTP (not HTTPS), and limited to 45 requests/minute. |
| **ipdata.co / ipdata.free** | Online country code, region, city, coordinates and ASN/organization | 1,000 city-level lookups/day per public calling IP without a key; after the cap country/ASN data still works. A free key adds unlimited country/ASN queries. Commercial use requires attribution. SwarmAtlas caps at 950/day per installation. |
| **ipapi.is** | Online country, region, city, coordinates and network/ASN details | Anonymous tier: 30 lookups/day; a free account/key raises the documented allowance to 1,000/day. |
| **IPinfo Lite** | Online country and ASN/AS name | Requires an IPinfo token. Lite is country/ASN-level data; it does not provide city coordinates. Public use requires attribution. |
| **IP2Location.io** | Online country, region, city, coordinates and ASN | Keyless free tier: 1,000 lookups/day; free API key: up to 50,000/month. Attribution is required in both free modes. SwarmAtlas caps at 950/day or 47,500/month per installation. |
| **2ip.io** | Online country, city and network/ASN data | Requires an API token. The provider advertises 10,000 free requests/month after registration and a free-token rate limit of 3 requests/second. SwarmAtlas stops at 9,500 requests/month to leave a safety margin. |

The online providers are intentionally not queried in parallel: select one source at a time. Lookups run in a background worker, public IPs are cached in memory, provider request rates are limited, and conservative daily and monthly free-tier budgets are tracked locally per installation; other applications or devices sharing the same public IP or API key may consume the provider's quota too. Private, loopback and link-local addresses are never sent to a remote GeoIP API. Switching back to DB-IP Lite returns to local lookups.

**Privacy note:** online GeoIP necessarily discloses the looked-up destination IP to the selected provider. IP-API's free endpoint is unencrypted HTTP, so it is not a privacy-equivalent alternative to the HTTPS providers. Do not select it unless its non-commercial terms and transport characteristics suit your use. DB-IP Lite is recommended for private/offline use.

On first launch, DB-IP City Lite is downloaded from the official DB-IP host when offline mode is selected and no valid local copy exists. The file is checked against the publisher's checksum and validated as an MMDB before being installed. A valid cached file remains usable if a later refresh fails. The database refreshes after 30 days. The online map uses OpenFreeMap Dark vector tiles through MapLibre GL. Qt WebEngine keeps a shared HTTP cache capped at 128 MiB. When offline, only previously cached style and tile resources may remain available; there is no complete offline basemap fallback.

You can also add local City, Country, ASN or ISP MMDB files using **Local MMDB files…**. Official compatible download sources:

- [DB-IP Lite downloads](https://db-ip.com/db/lite.php) — choose the **MMDB** format for City, Country or ASN. DB-IP Lite is licensed under CC BY 4.0 and requires attribution.
- [MaxMind GeoLite2 sign-up](https://www.maxmind.com/en/geolite2/signup) — create a free account, generate a license key, then download the **GeoLite2 City**, **Country** or **ASN** MMDB from the account portal. See the [official download guide](https://support.maxmind.com/knowledge-base/articles/download-and-update-maxmind-databases).

Choose MMDB binary files, not CSV. Extract the `.mmdb` file from `.gz` or `.zip` archives before adding it. Add a City database for region/city names and map coordinates; Country and ASN databases can be combined with it. The default DB-IP City Lite database and the shared Qt WebEngine map cache are stored in the standard application-data directory.

## Map and regions

The dashboard uses MapLibre GL with the OpenFreeMap **Dark** vector style. The map fills its rectangular panel instead of cropping the world to a square. Vector tiles are rendered asynchronously by Qt WebEngine; its shared disk cache is capped at 128 MiB. Markers show observed public endpoints with approximate GeoIP coordinates. Nearby endpoints are clustered at low zoom levels, and cluster glow reflects the combined measured traffic of its members. When optional TCP ESTATS counters are available, marker radius and glow intensity scale with the measured recent upload/download rate: orange indicates upload-heavy, cyan download-heavy, and violet mixed traffic. Click a cluster to zoom in or an individual marker to open its detail card with process, PID, ports, protocol, connection state, ASN/ISP, location, transferred bytes and directional rates. Map geometry is only rebuilt when an endpoint, its location, or its quantized traffic-rate bucket changes; exact byte totals and timestamps are refreshed separately for popup details. Geometry updates are batched to one animation frame. For a curated set of known network-owner ASNs (Google, Microsoft, AWS, Cloudflare, Meta, Apple, Netflix, Akamai, Fastly, DigitalOcean, OVHcloud, Hetzner, Oracle and IBM), markers also display the owner's Simple Icons brand mark. This identifies the network operator registered for the IP range, not necessarily the website or application using that infrastructure. Unknown networks keep the standard marker. Brand assets are fetched lazily, once per recognized company, from the pinned Simple Icons CDN version and cached by the WebEngine profile. The main dashboard uses eased, interruptible wheel scrolling and animated scroll-to-section navigation.

OpenFreeMap does not require an API key. The map requests its style and vector tiles from OpenFreeMap infrastructure; MapLibre GL JavaScript and CSS are loaded from the pinned unpkg CDN release; recognized network-owner brand SVGs are loaded from the pinned Simple Icons release on jsDelivr. These map requests disclose the user's public source IP and viewed tile coordinates to those services, but SwarmAtlas does not include observed destination IPs, process names, PIDs or traffic counters in map requests. The map credits OpenFreeMap, OpenMapTiles and OpenStreetMap contributors. The basemap is online; endpoint collection and local traffic history remain separate.


## History and database storage

The **History** chart offers 1 hour, 24 hours, 7 days, 30 days, 1 year and 5 years.

To keep long retention compact:

- traffic is sampled every 10 seconds;
- individual samples are kept for the most recent 7 days;
- older samples are folded into hourly average/peak summaries for up to 5 years;
- old endpoint records are retained for up to 5 years;
- endpoint history is deduplicated by IP, port, protocol and process instead of treating every temporary local source port as a new destination;
- endpoint history is capped at 200,000 rows;
- SQLite uses WAL mode, a bounded page cache, and checkpoint/optimization when the monitor closes.

The database file is named swarmatlas.sqlite3. Use **Database location…** in the sidebar to select another folder. SwarmAtlas stops the collector cleanly, checkpoints the SQLite WAL, copies the current database if the destination does not already contain one, then reopens it from the selected folder. Settings and diagnostic logs remain in the normal application-data directories.

Windows default application data: %APPDATA%\SwarmAtlas\. The launcher logs are in %LOCALAPPDATA%\SwarmAtlas\logs\. Logs are local; the latest 10 launcher logs are retained.

SQLite history, peer IPs, process names and diagnostic logs are not encrypted at rest by SwarmAtlas. They rely on the file permissions of the Windows user account. API tokens for online GeoIP services are also stored in the local settings file.

## Language and module architecture

SwarmAtlas deliberately uses more than one language, but each language has a defined responsibility. The goal is not to rewrite every file in Rust or to add languages for their own sake. A module should move only when profiling, platform integration or maintainability demonstrates a real benefit.

### Current architecture

```text
Python application (PySide6 UI, settings, orchestration, provider logic)
                    |
             batched PyO3 calls
                    |
             Rust native core
       Windows APIs + map geometry
                    |
          Python data/application layer
                    |
               SQLite / SQL
```

| Area | Current implementation | Language decision |
|---|---|---|
| Desktop UI and Qt widgets | `app.py`, `ui.py`, PySide6 | **Keep Python + PySide6.** Qt already performs painting and widget work in its native implementation; rewriting UI code in Rust or C++ would add complexity without proving a gain. |
| Application flow, settings, themes and localization | `app.py`, `config.py`, `theme.py`, `i18n.py` | **Keep Python.** These are application logic and user-facing configuration, not CPU-heavy loops. |
| Collector scheduling and presentation snapshots | `collector.py` | **Keep orchestration in Python.** It coordinates Qt signals, the active/background sampling profile, GeoIP and storage. Move only measured expensive OS queries or data-processing loops to Rust. |
| Windows network tables and IP classification | `native/src/platform/windows/`, `native/src/network/` | **Rust — already implemented.** This is the right boundary for low-level Windows APIs, address normalization and bounded native table processing. |
| Map projection and polygon simplification | `native/src/geo/geometry.rs` | **Rust — already implemented.** Numeric work is batched through PyO3 and runs without holding the Python GIL. Qt path objects remain on the Python/Qt side. |
| Map downloads, archive validation and cached files | `map_data.py` | **Keep Python.** This is mostly file/network I/O and validation. Consider moving shapefile decoding only if profiling shows that parsing, rather than download or Qt path construction, dominates startup time. |
| Offline GeoIP and MMDB cache | `geoip.py` | **Keep Python for now.** A Rust MMDB reader is a possible later optimization only if real lookup benchmarks show it is a bottleneck. The Rust `maxminddb` reader supports GeoIP2/GeoLite2 data and optional memory-mapped access; it is a candidate, not a dependency selected for the current build. |
| Online GeoIP providers | `geoip_remote.py` | **Keep Python.** Request latency, provider quotas, retries and response normalization dominate; Rust would not make remote servers answer faster. |
| History and endpoint database | `storage.py`, Python `sqlite3` and SQL | **Keep Python + SQL.** SQLite executes database operations in its native engine. Rewriting the wrapper in Rust is unlikely to improve query speed by itself. |
| Qt rendering and interaction | `WorldMap`, `TrafficChart` in `ui.py` | **Keep Qt/PySide6.** Profile paint time and Python-to-Qt call counts first. If building `QPainterPath` objects becomes a proven bottleneck, try batched Qt operations before considering a small C++/Qt helper. Do not rewrite the full UI. |

### What should move to Rust next

These are candidates, not claims that the work is already implemented:

1. **Network adapter counters and interface enumeration — moved to Rust.** The native core now reads `GetIfTable2` once per sample and returns only interface alias, operational state and byte counters. The collector reuses that same snapshot for adapter selection and live rates, instead of separately enumerating interfaces and counters through psutil. The psutil path remains as a compatibility fallback.
2. **Process-name resolution — moved to Rust.** The native core takes one documented Tool Help process snapshot per endpoint scan and returns names only for PIDs present in the current socket snapshot. Python retains its 30-second PID cache and uses psutil only when the native feature is unavailable. The resolver has a hard 4,096-PID input bound.
3. **Offline MMDB lookup.** Benchmark cold and warm lookups over realistic endpoint batches. Only if Python lookup time is significant, evaluate the Rust `maxminddb` reader, including mmap, locale handling and compatibility with the user's City/Country/ASN/ISP files.
4. **Shapefile parsing.** Measure Natural Earth file parsing separately from archive download, antimeridian splitting, projection, simplification and Qt path construction. Move only the expensive parsing stage if it is a demonstrated startup bottleneck.
5. **Optional ETW network-event collector.** If reliable UDP destination visibility is added, implement the Windows event consumer as a separate Rust subsystem. Keep it read-only, cancellable and opt-in; expose its CPU cost and sampling state, and do not capture or store packet payloads.

### Languages reserved for future product changes

- **C++** is reserved for a small Qt-native helper only if profiling proves the Python/Qt object boundary is the bottleneck. It is not a planned replacement for the current UI.
- **TypeScript** makes sense only if SwarmAtlas gains a separate browser-based dashboard or web companion. It is not needed inside the current Windows desktop application.
- **Go** may be considered for a separate headless server, multi-host collector coordinator or network service if that becomes a real product requirement. It does not belong in the current local-first desktop client while Rust already handles native Windows work.

For the current product, the intended core is therefore **Python + Rust + SQL**. Batch calls across the Python/Rust boundary; never call Rust once per coordinate, table row or UI item. Keep one source of truth for each behavior, retain a Python fallback for optional native features, and require correctness tests plus before/after benchmarks for every port. A language change is accepted only when it improves measured performance, power use, OS integration or long-term maintainability.

Useful project references: [PyO3](https://github.com/PyO3/pyo3) and [Maturin](https://github.com/PyO3/maturin) for Rust/Python integration, [Microsoft windows-rs](https://github.com/microsoft/windows-rs) for Windows API bindings, [Qt for Python](https://doc.qt.io/qtforpython-6/) for the desktop UI, and the [Rust MaxMind DB reader](https://github.com/oschwald/maxminddb-rust) as a possible MMDB benchmark candidate.

## Install and run on Windows

Requirements: Windows 10/11, Python 3.11 or newer, and the Python Launcher (py). Double-click run_windows.bat.

On first launch, the launcher:

1. creates the local Python virtual environment;
2. installs SwarmAtlas and its GeoIP/MMDB dependencies;
3. checks GitHub Releases for the tested Windows x64 Rust wheel;
4. downloads and verifies the wheel's SHA-256 manifest, then installs it into the virtual environment;
5. starts the application.

The Rust wheel is prebuilt, so a Rust compiler is not needed. The wheel is published by GitHub Actions after the Windows Rust API smoke test, Python tests and compile checks pass. If a tested release is not available, GitHub is unreachable, or checksum/installation verification fails, the launcher logs a warning and starts in Python compatibility mode instead. It does not install a kernel driver or change Windows networking. Later launches reuse the virtual environment and skip the native download when the extension is already installed.

An internet connection is required for the initial Python package setup and first-run reference-data downloads. The launcher keeps its console visible and mirrors setup output, application output, warnings and Python errors to a local log.

For a manual setup from PowerShell:

    py -3 -m venv .venv
    .\.venv\Scripts\python -m pip install -e ".[geoip]"
    .\.venv\Scripts\python -m swarmatlas

The manual command does not run the Windows launcher or its automatic native-wheel setup. For development dependencies:

    .\.venv\Scripts\python -m pip install -e ".[geoip,dev]"
    python -m pytest
    python -m compileall run_windows.py update_project.py src benchmarks

## Optional local Rust build

The Python application, UI, localization, GeoIP orchestration, settings and SQLite store remain Python-side. The optional PyO3/Rust extension is split into platform, network and geometry modules:

- `native/src/platform/windows/api.rs` owns bounded Windows IP Helper table queries and port decoding.
- `native/src/platform/windows/tcp.rs` and `udp.rs` decode the IPv4/IPv6 owner-PID table layouts separately.
- `native/src/platform/windows/interfaces.rs` reads interface names, operational state and byte counters through `GetIfTable2`; the same snapshot is used for adapter selection and the live rate sample.
- `native/src/platform/windows/processes.rs` resolves only requested connection-owner PIDs from one Tool Help process snapshot; the Python collector caches results for 30 seconds.
- `native/src/network/address.rs` canonicalizes IPv4-mapped IPv6 addresses and filters non-public/special-purpose destinations before Python objects are created.
- `native/src/geo/geometry.rs` performs Web Mercator projection, radial filtering and iterative Douglas-Peucker simplification in bounded batches, with the GIL released while the numeric core runs.
- `native/src/lib.rs` is only the PyO3 boundary; it releases the GIL while Windows tables, interface counters and process names are read and while map geometry is processed.

For TCP, Rust scans a bounded OS-table prefix (up to 100,000 rows per address family), filters non-public addresses natively, and returns no more than the requested endpoint limit (2,000 in the dashboard). It visits the aligned Win32 buffer directly, so it does not allocate and copy a second vector of all raw rows. Python receives only usable public destinations and does not repeat the address classification for the current native capability.

For UDP, Rust counts IPv4 and IPv6 owner-PID table rows without materializing per-socket Python tuples. The collector no longer reads the same Windows UDP binding table a second time through psutil. Interface counters and connection-owner process names also use compact Rust APIs, while psutil remains the compatibility fallback. This count is deliberately distinct from remote UDP peers: Windows' owner-PID table contains local bindings and does not reveal arbitrary remote destinations.

Map geometry is sent to Rust in batches bounded by ring count and input vertex count. The Rust implementation preserves the Python algorithm's tolerance split and endpoint handling. Qt `QPainterPath` construction stays on the Python side because it is a Qt object boundary, not a worthwhile target for a language rewrite by itself. If the optional extension is unavailable, the original Python geometry implementation remains the compatibility fallback.

Use `benchmarks/benchmark_map_geometry.py` to compare the Python reference against the installed native implementation. The benchmark uses deterministic synthetic rings, includes Python/Rust conversion overhead, does not contact the network, and reports median and p95 timings. Timing is machine- and dataset-dependent; run it on the same machine and build to determine whether the native path helps your map data.

The prebuilt Rust extension is installed automatically by run_windows.bat when a verified release is available. To build locally instead, install Rust 1.85 or newer and the Windows MSVC linker/SDK, then run build_native.bat. The Python collector remains the fallback.

## Appearance, language and interface

- Theme: System, Dark or Light.
- Language: System, Русский or English.
- Network adapter: automatic selection or a specific active interface.
- When SwarmAtlas is not the active foreground application, or its window is minimized, the collector automatically switches to a lower-power background cadence: interface counters every 5 seconds, interface checks every 20 seconds and endpoint scans every 15 seconds. As soon as SwarmAtlas becomes the active application again, it immediately returns to the responsive foreground cadence (1-second counters and 5-second endpoint scans). Ten-second history sampling remains enabled.
- The live header shows upload and download rates separately.
- Native Windows 11 Mica backdrop is supported on Windows 11 22H2+.

## Updating

Close SwarmAtlas and run update_project.bat.

- Git clones are updated only when the working tree is clean and a fast-forward update is possible.
- ZIP-extracted folders are safely overlaid from the GitHub main-branch archive.
- The updater preserves the Python virtual environment, local MMDB files, SQLite databases, logs and files outside the repository.
- If a Rust extension is already installed, the updater checks for a refreshed tested wheel or local release build and verifies its SHA-256 before replacing it.
- The updater does not silently stash, reset or rewrite local Git commits.

## Planned roadmap (future work, not implemented yet)

The following ideas are intentionally recorded here so they are not forgotten. They are design goals, not features available in the current build.

- **iperf3 speed test:** add an optional, manually started bandwidth test against public iperf3 servers. Discover and verify servers from a maintained server directory, show country/provider/latency and server availability, and let the user choose a server and test duration. Respect each server's published usage policy, use conservative defaults, and clearly separate the speed-test result from SwarmAtlas' passive traffic counters. Public servers can disappear, become busy or restrict testing, so availability must be checked rather than assumed.
- **Ping diagnostics:** allow one-shot and continuous ping to a chosen IP/hostname; show RTT, min/average/max, jitter, packet loss and timeouts. Keep the test cancellable and distinguish ICMP filtering from a host being offline.
- **Traceroute:** trace a route to a chosen destination with selectable ICMP/UDP/TCP probing where supported; list hop number, address, reverse-DNS name, RTT and unanswered hops. Support IPv4 and IPv6 and explain that routers may hide or rate-limit replies.
- **Route visualization on the map:** draw the measured hop sequence, place hop markers using approximate GeoIP coordinates when available, show per-hop latency and missing replies, and keep the route visually distinct from ordinary observed peer connections. GeoIP locations are estimates, not physical router positions; routes can change between tests.
- **Map layers and test history:** allow switching between peer activity, ping targets, traceroute paths and speed-test server/results. Save diagnostic history locally only when the user enables it.
- **Better UDP destination visibility:** investigate Windows ETW/network-event metadata or another documented, read-only source that can expose UDP source/destination tuples. Keep local UDP socket counts separate from verified remote peers, avoid fabricating endpoints, and do not capture or store packet payloads.

## Per-application TCP traffic (optional)

Enable **Track per-app TCP traffic** in the sidebar to collect Windows TCP Extended Statistics (ESTATS) through the Rust backend. SwarmAtlas aggregates the measured TCP payload-byte counters by owning process and shows upload, download, total bytes and distinct peers. This feature is opt-in and may require launching SwarmAtlas as Administrator; Windows can deny access to TCP ESTATS otherwise.

This is not a complete accounting of all application network usage: UDP, unsupported TCP connections, traffic not exposed by Windows ESTATS and connections without a reliable process owner are not included. Interface totals remain the authoritative adapter-wide counters. Per-application figures must be presented as measured TCP ESTATS bytes, not as total network usage. The feature does not capture packet payloads or install a packet filter or driver.

## Development and license

    python -m pytest
    python -m compileall run_windows.py update_project.py src benchmarks

SwarmAtlas application code is MIT-licensed. Third-party datasets and providers retain their own licenses and terms; see their linked terms before using an online service or redistributing downloaded GeoIP data.
