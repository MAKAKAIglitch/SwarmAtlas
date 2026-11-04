use std::ffi::c_void;
use std::mem::size_of;
use std::ptr::{null_mut, read};
use std::slice;

pub(super) const AF_INET: u32 = 2;
pub(super) const AF_INET6: u32 = 23;
pub(super) const TCP_TABLE_OWNER_PID_ALL: i32 = 5;
pub(super) const UDP_TABLE_OWNER_PID: i32 = 1;

const ERROR_INSUFFICIENT_BUFFER: u32 = 122;
const MAX_TABLE_BYTES: u32 = 128 * 1024 * 1024;
const MAX_RESIZE_ATTEMPTS: usize = 5;
pub(super) const MAX_TABLE_ROWS: usize = 100_000;

type QueryTable = unsafe extern "system" fn(
    *mut c_void,
    *mut u32,
    i32,
    u32,
    i32,
    u32,
) -> u32;

#[link(name = "iphlpapi")]
unsafe extern "system" {
    fn GetExtendedTcpTable(
        table: *mut c_void,
        size: *mut u32,
        order: i32,
        family: u32,
        table_class: i32,
        reserved: u32,
    ) -> u32;

    fn GetExtendedUdpTable(
        table: *mut c_void,
        size: *mut u32,
        order: i32,
        family: u32,
        table_class: i32,
        reserved: u32,
    ) -> u32;
}

pub(super) fn decode_port(raw: u32) -> u16 {
    u16::from_be((raw & 0xffff) as u16)
}

fn api_error(name: &str, code: u32) -> String {
    format!("{name} failed with Windows error {code}")
}

unsafe fn query_rows_with<T: Copy, R>(
    query: QueryTable,
    family: u32,
    table_class: i32,
    limit: usize,
    name: &str,
    visitor: impl FnOnce(&[T]) -> R,
) -> Result<R, String> {
    if limit == 0 {
        return Ok(visitor(&[]));
    }
    let mut required_bytes = 0_u32;
    let first_status = query(null_mut(), &mut required_bytes, 0, family, table_class, 0);

    if first_status != ERROR_INSUFFICIENT_BUFFER && first_status != 0 {
        return Err(api_error(name, first_status));
    }
    if required_bytes == 0 {
        return Ok(visitor(&[]));
    }

    for _ in 0..MAX_RESIZE_ATTEMPTS {
        if required_bytes > MAX_TABLE_BYTES {
            return Err(format!(
                "{name} requested an unexpectedly large table ({required_bytes} bytes)"
            ));
        }

        // DWORD-aligned storage matches the alignment required by IP Helper tables.
        let words = ((required_bytes as usize).saturating_add(3) / 4).max(1);
        let mut storage = vec![0_u32; words];
        let mut actual_bytes = (storage.len() * size_of::<u32>()) as u32;
        let status = query(
            storage.as_mut_ptr().cast::<c_void>(),
            &mut actual_bytes,
            0,
            family,
            table_class,
            0,
        );

        if status == ERROR_INSUFFICIENT_BUFFER {
            required_bytes = actual_bytes.max(required_bytes.saturating_mul(2));
            continue;
        }
        if status != 0 {
            return Err(api_error(name, status));
        }
        if actual_bytes < size_of::<u32>() as u32 {
            return Err(format!("{name} returned a truncated table header"));
        }

        let base = storage.as_ptr();
        let row_count = read(base) as usize;
        let safe_bytes = (actual_bytes as usize).min(storage.len() * size_of::<u32>());
        let available_bytes = safe_bytes - size_of::<u32>();
        let available_rows = available_bytes / size_of::<T>();
        if row_count > available_rows {
            return Err(format!(
                "{name} returned {row_count} rows but only {available_rows} fit in the buffer"
            ));
        }

        // Visit the bounded OS-owned row slice while its aligned buffer is alive.
        // TCP can filter and collect only public results here, avoiding a second
        // allocation and copy of up to 100,000 MIB rows.
        let rows_to_visit = row_count.min(limit);
        let rows_ptr = base.add(1).cast::<T>();
        let rows = slice::from_raw_parts(rows_ptr, rows_to_visit);
        return Ok(visitor(rows));
    }

    Err(format!(
        "{name} table kept changing size after {MAX_RESIZE_ATTEMPTS} attempts"
    ))
}

unsafe fn query_rows<T: Copy>(
    query: QueryTable,
    family: u32,
    table_class: i32,
    limit: usize,
    name: &str,
) -> Result<Vec<T>, String> {
    query_rows_with(query, family, table_class, limit, name, |rows| rows.to_vec())
}

pub(super) unsafe fn query_tcp_rows_with<T: Copy, R>(
    family: u32,
    table_class: i32,
    limit: usize,
    name: &str,
    visitor: impl FnOnce(&[T]) -> R,
) -> Result<R, String> {
    query_rows_with(GetExtendedTcpTable, family, table_class, limit, name, visitor)
}

pub(super) unsafe fn query_udp_rows<T: Copy>(
    family: u32,
    table_class: i32,
    limit: usize,
    name: &str,
) -> Result<Vec<T>, String> {
    query_rows(GetExtendedUdpTable, family, table_class, limit, name)
}

pub(super) unsafe fn query_udp_rows_with<T: Copy, R>(
    family: u32,
    table_class: i32,
    limit: usize,
    name: &str,
    visitor: impl FnOnce(&[T]) -> R,
) -> Result<R, String> {
    query_rows_with(GetExtendedUdpTable, family, table_class, limit, name, visitor)
}

#[cfg(test)]
mod tests {
    use super::decode_port;

    #[test]
    fn network_port_is_converted_to_host_order() {
        assert_eq!(decode_port(0xBB01), 443);
        assert_eq!(decode_port(0x5000), 80);
    }
}
