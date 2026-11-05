# Security and safety policy

## Default behavior

SwarmAtlas is a passive, read-only network monitor. Its default GeoIP source is the local DB-IP Lite MMDB.

Allowed operations:
- Read network-interface counters and interface state through psutil.
- Read OS TCP/UDP connection-owner tables through the optional Rust extension (Windows IP Helper APIs), or through the Python psutil fallback.
- Read process IDs and, when permitted, process names for observed connections.
- Read local GeoIP MMDB files selected by the user.
- Download the public DB-IP Lite City database during first-run setup or scheduled DB-IP refresh. This reference-data download does not contain observed peer IPs, process names, PIDs or traffic statistics.
- Load the pinned MapLibre GL JavaScript/CSS assets from unpkg and request the OpenFreeMap Dark style, vector tiles and required glyph resources for the visible map viewport. Qt WebEngine uses a disk cache capped at 128 MiB. These requests expose the user's public source IP and viewed tile coordinates to the respective services, but do not include observed destination IPs, process metadata or traffic counters.
- If the user explicitly selects an online GeoIP provider, send the public destination IP being looked up to that selected provider. No process names, PIDs, traffic totals or packet contents are included in the lookup.
- Install the project's declared Python dependencies and, when available, a verified prebuilt Rust wheel from the project's public GitHub release during Windows first-run setup.
- Write local settings, provider quota counters, traffic history, endpoint metadata and diagnostics to local application-data/storage directories.

Operations out of scope for the default monitor:
- Installing or loading kernel drivers, services, scheduled tasks, or startup hooks.
- Installing Npcap, WinPcap, WinDivert, NDIS filters, or packet-capture components.
- Adding/removing Windows Filtering Platform filters or firewall rules.
- Editing routes, DNS, proxy, IP addresses, adapter settings, or Winsock configuration.
- Capturing packet payloads or transmitting traffic on behalf of other applications.
- Automatically requesting elevation or bypassing access restrictions.

The Rust extension is a user-mode Python module, not a Windows kernel driver. It uses read-only Windows IP Helper APIs.

## Privileges

The app should run as a normal Windows user. It must not request administrator privileges. If Windows denies access to some socket metadata, show an informative status and continue interface-level monitoring.

## Network behavior and GeoIP privacy

The default DB-IP Lite mode performs local address lookups after its static MMDB file has been downloaded. The observed destination IPs are not sent to DB-IP for those local lookups.

The map loads MapLibre GL assets from the pinned unpkg release and the OpenFreeMap Dark style and vector tiles for the visible viewport. The relevant services see the user's public source IP and requested style/tile coordinates. These requests do not contain the application's observed destination IP list, process metadata or traffic counters. Attribution is displayed in the map. The application does not prefetch or bulk-download map tiles. For recognized network-owner ASNs only, the map lazily requests a fixed brand SVG asset when that company has an icon in the pinned Simple Icons catalog (slug only, no endpoint IP, process name, PID or traffic data) from the pinned Simple Icons CDN on jsDelivr. Other recognized companies use a locally drawn initials badge. jsDelivr therefore sees the user's public source IP and the requested public brand asset. Unknown ASNs do not trigger brand-icon requests. An ICMP echo request is sent to an endpoint IP only when the user opens that endpoint's individual map popup (one echo, 1.2-second timeout); SwarmAtlas does not ping endpoints in the background or on its regular collection cycle.

When the user explicitly enables Application traffic statistics and the optional Rust TCP ESTATS capability is present, Windows TCP payload byte counters are read locally for established public TCP connections and stored in the local SQLite database. These counters contain byte totals, not packet contents. The resulting per-endpoint rates and process metadata are rendered locally in the map; they are not sent to OpenFreeMap, unpkg, GeoIP providers, or a SwarmAtlas server by map rendering.

Online GeoIP is opt-in and sends the selected public destination IP to exactly one configured provider. The provider also sees the user's public source IP as the normal source of the HTTPS/HTTP request (and the account token when required):
- IP-API.com (free endpoint: HTTP, non-commercial use only);
- ipdata.co;
- ipapi.is;
- IPinfo Lite (requires a token);
- IP2Location.io (keyless or optional API token; attribution is required on its free plan);
- 2ip.io (requires a token).

The provider terms, quotas, and transport security differ. IP-API's free endpoint is HTTP and does not provide HTTPS transport. Users should check the provider's current terms before selecting a service. Private, loopback and link-local addresses are filtered out before remote lookups. Lookups run in a background thread with an in-memory cache and provider-specific request throttling. The application keeps conservative daily and monthly request counters locally per installation for free-tier budget protection. Other applications or devices sharing the same public IP or API key may also consume the provider's quota.

Online provider API keys are stored separately for each provider in the local settings file. They are not encrypted by SwarmAtlas. A token for one provider is not reused for another. A user who can read that settings file can read the key. Do not publish settings.json or application logs.

The monitor itself does not bind/listen on a port, create tunnels, change firewall settings, or capture payloads. Package installation and updater operations contact their configured package/GitHub sources only when setup or update is explicitly run. The Windows launcher downloads a published Rust wheel only from the project's GitHub release, validates the SHA-256 against the release manifest, then installs the local wheel. If that fails, it leaves the Python fallback available.

## Local data and retention

The SQLite database stores interface totals, raw traffic samples, compact hourly history, observed public endpoints, approximate GeoIP metadata, and process ownership metadata. It does not store packet payloads. Database files may be stored in the default application-data directory or in a user-selected folder.

Raw traffic samples are retained for 7 days. Older samples are compacted into hourly aggregates and retained for up to 5 years. Destination records are retained for up to 5 years, with a cap of 200,000 records. The app checkpoints and optimizes SQLite when the monitor exits.

Observed public IPs, process IDs/names, settings, API tokens, GeoIP metadata and traffic history are not uploaded to a SwarmAtlas service. However, an online GeoIP provider receives the destination IP it is asked to look up.

SQLite history, diagnostic logs, settings and GeoIP API tokens are not encrypted at rest by SwarmAtlas. They rely on the current Windows user's file permissions. Anyone with read access to those files may inspect the contents. Do not publish the database, settings file or logs publicly.

## Measurement limits

Interface counters account for all bytes reported by the selected adapter. The Windows socket table separately exposes observed public TCP connections and some connected UDP endpoints. These sources do not provide reliable per-destination byte attribution. SwarmAtlas must not label endpoint counts as bandwidth or invent per-IP/per-country transfer volumes.

## Future features

Optional per-flow TCP byte accounting is implemented using Windows TCP ESTATS through the Rust backend. It is disabled by default and requires explicit user opt-in; Windows may require the application to run elevated for the OS to expose these counters. It records TCP payload-byte counters by connection and owning process, without packet payload capture, packet filters or drivers. It does not account for UDP or every TCP connection, and it must not be represented as complete per-application network usage. Adapter-wide interface counters remain independent and available without enabling this feature. Packet capture is not implemented.

## Reporting

Please report security or safety issues through GitHub Issues without posting private IPs, peer lists, API tokens, settings files or GeoIP database files.
