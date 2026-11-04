use super::api::{decode_port, query_udp_rows, AF_INET, AF_INET6, UDP_TABLE_OWNER_PID};
use std::net::{Ipv4Addr, Ipv6Addr};

#[repr(C)]
#[derive(Clone, Copy)]
struct UdpRowV4 {
    local_addr: u32,
    local_port: u32,
    owning_pid: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct UdpRowV6 {
    local_addr: [u8; 16],
    local_scope_id: u32,
    local_port: u32,
    owning_pid: u32,
}

pub(super) type UdpBinding = (String, u16, u32);

pub(super) fn list_udp_bindings(limit: usize) -> Result<Vec<UdpBinding>, String> {
    let limit = limit.min(100_000);
    if limit == 0 {
        return Ok(Vec::new());
    }
    let mut rows = Vec::with_capacity(limit.min(2048));
    let mut successful_tables = 0_usize;
    let mut errors = Vec::new();

    unsafe {
        match query_udp_rows::<UdpRowV4>(
            AF_INET,
            UDP_TABLE_OWNER_PID,
            limit,
            "GetExtendedUdpTable(AF_INET)",
        ) {
            Ok(table) => {
                successful_tables += 1;
                for row in table {
                    let local = Ipv4Addr::from(row.local_addr.to_ne_bytes());
                    rows.push((local.to_string(), decode_port(row.local_port), row.owning_pid));
                    if rows.len() >= limit {
                        break;
                    }
                }
            }
            Err(error) => errors.push(error),
        }

        let remaining = limit.saturating_sub(rows.len());
        if remaining > 0 {
            match query_udp_rows::<UdpRowV6>(
                AF_INET6,
                UDP_TABLE_OWNER_PID,
                remaining,
                "GetExtendedUdpTable(AF_INET6)",
            ) {
                Ok(table) => {
                    successful_tables += 1;
                    for row in table {
                        let local = Ipv6Addr::from(row.local_addr);
                        rows.push((local.to_string(), decode_port(row.local_port), row.owning_pid));
                        if rows.len() >= limit {
                            break;
                        }
                    }
                }
                Err(error) => errors.push(error),
            }
        }
    }

    if successful_tables == 0 {
        return Err(errors.join("; "));
    }
    Ok(rows)
}
