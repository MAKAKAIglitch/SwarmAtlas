use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

fn in_ipv4_range(address: Ipv4Addr, network: [u8; 4], prefix: u8) -> bool {
    let address = u32::from_be_bytes(address.octets());
    let network = u32::from_be_bytes(network);
    let mask = if prefix == 0 { 0 } else { u32::MAX << (32 - prefix) };
    address & mask == network & mask
}

fn is_public_ipv4(address: Ipv4Addr) -> bool {
    let octets = address.octets();
    if address.is_unspecified()
        || address.is_loopback()
        || address.is_private()
        || address.is_link_local()
        || address.is_multicast()
        || octets == [255, 255, 255, 255]
    {
        return false;
    }

    // Special-purpose ranges are deliberately excluded from the map. This
    // mirrors the conservative intent of Python ipaddress.is_global and avoids
    // presenting documentation, CGNAT or benchmarking addresses as public peers.
    let excluded = [
        ([0, 0, 0, 0], 8),       // This network
        ([100, 64, 0, 0], 10),   // Shared address space / CGNAT
        ([192, 0, 0, 0], 24),    // IETF protocol assignments
        ([192, 0, 2, 0], 24),    // TEST-NET-1
        ([192, 88, 99, 0], 24),  // Deprecated 6to4 relay anycast
        ([198, 18, 0, 0], 15),   // Benchmarking
        ([198, 51, 100, 0], 24), // TEST-NET-2
        ([203, 0, 113, 0], 24),  // TEST-NET-3
        ([240, 0, 0, 0], 4),     // Reserved for future use
    ];

    !excluded
        .iter()
        .any(|(network, prefix)| in_ipv4_range(address, *network, *prefix))
}

fn is_public_ipv6(address: Ipv6Addr) -> bool {
    let segments = address.segments();
    // Globally routable unicast currently lives in 2000::/3. This excludes
    // unspecified, loopback, link-local, unique-local and multicast ranges.
    let global_unicast = segments[0] & 0xe000 == 0x2000;
    let documentation = segments[0] == 0x2001 && segments[1] == 0x0db8;
    let teredo = segments[0] == 0x2001 && segments[1] == 0x0000;
    let benchmarking = segments[0] == 0x2001
        && segments[1] == 0x0002
        && segments[2] == 0x0000;
    let orchid = segments[0] == 0x2001 && segments[1] & 0xfff0 == 0x0010;
    let six_to_four = segments[0] == 0x2002;
    let documentation_v2 = segments[0] == 0x3fff && segments[1] & 0xf000 == 0;

    global_unicast
        && !documentation
        && !teredo
        && !benchmarking
        && !orchid
        && !six_to_four
        && !documentation_v2
}

pub(crate) fn canonical_ip(address: IpAddr) -> IpAddr {
    match address {
        IpAddr::V6(value) => value
            .to_ipv4_mapped()
            .map(IpAddr::V4)
            .unwrap_or(IpAddr::V6(value)),
        value => value,
    }
}

pub(crate) fn is_public_ip(address: IpAddr) -> bool {
    match canonical_ip(address) {
        IpAddr::V4(value) => is_public_ipv4(value),
        IpAddr::V6(value) => is_public_ipv6(value),
    }
}

#[cfg(test)]
mod tests {
    use super::{canonical_ip, is_public_ip};
    use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

    #[test]
    fn public_ipv4_addresses_are_kept() {
        assert!(is_public_ip("8.8.8.8".parse().unwrap()));
        assert!(is_public_ip("1.1.1.1".parse().unwrap()));
    }

    #[test]
    fn non_public_ipv4_ranges_are_rejected() {
        for address in [
            "0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1",
            "169.254.1.1", "172.16.0.1", "192.0.2.1", "192.168.1.1",
            "198.18.0.1", "198.51.100.1", "203.0.113.1", "224.0.0.1",
            "240.0.0.1", "255.255.255.255",
        ] {
            assert!(!is_public_ip(address.parse().unwrap()), "{address} must not be public");
        }
    }

    #[test]
    fn ipv6_global_unicast_and_special_ranges_are_classified() {
        assert!(is_public_ip("2001:4860:4860::8888".parse().unwrap()));
        assert!(!is_public_ip(Ipv6Addr::UNSPECIFIED.into()));
        assert!(!is_public_ip(Ipv6Addr::LOCALHOST.into()));
        assert!(!is_public_ip("fe80::1".parse().unwrap()));
        assert!(!is_public_ip("fc00::1".parse().unwrap()));
        assert!(!is_public_ip("ff02::1".parse().unwrap()));
        assert!(!is_public_ip("2001:db8::1".parse().unwrap()));
        assert!(!is_public_ip("2001::1".parse().unwrap()));
        assert!(!is_public_ip("2001:2::1".parse().unwrap()));
        assert!(!is_public_ip("2001:10::1".parse().unwrap()));
        assert!(!is_public_ip("2002::1".parse().unwrap()));
        assert!(!is_public_ip("3fff::1".parse().unwrap()));
        assert!(is_public_ip("2001:1::1".parse().unwrap()));
    }

    #[test]
    fn ipv4_mapped_ipv6_is_normalized_before_classification() {
        let mapped: IpAddr = "::ffff:8.8.8.8".parse().unwrap();
        assert_eq!(canonical_ip(mapped), IpAddr::V4(Ipv4Addr::new(8, 8, 8, 8)));
        assert!(is_public_ip(mapped));
        assert!(!is_public_ip("::ffff:192.168.1.1".parse().unwrap()));
    }
}
