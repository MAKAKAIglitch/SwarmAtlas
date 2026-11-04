use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

#[cfg(windows)]
mod windows_ip_helper {
    use std::ffi::c_void;
    use std::mem::size_of;
    use std::net::{Ipv4Addr, Ipv6Addr};
    use std::ptr::{null_mut, read};
    use std::slice;

    const AF_INET: u32 = 2;
    const AF_INET6: u32 = 23;
    const TCP_TABLE_OWNER_PID_ALL: i32 = 5;
    const UDP_TABLE_OWNER_PID: i32 = 1;
    const ERROR_INSUFFICIENT_BUFFER: u32 = 122;
    const MAX_TABLE_BYTES: u32 = 128 * 1024 * 1024;
    const MAX_RESIZE_ATTEMPTS: usize = 5;

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

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct TcpRowV4 {
        state: u32,
        local_addr: u32,
        local_port: u32,
        remote_addr: u32,
        remote_port: u32,
        owning_pid: u32,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct TcpRowV6 {
        local_addr: [u8; 16],
        local_scope_id: u32,
        local_port: u32,
        remote_addr: [u8; 16],
        remote_scope_id: u32,
        remote_port: u32,
        state: u32,
        owning_pid: u32,
    }

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

    fn decode_port(raw: u32) -> u16 {
        u16::from_be((raw & 0xffff) as u16)
    }

    fn tcp_state(state: u32) -> &'static str {
        match state {
            1 => "CLOSED",
            2 => "LISTEN",
            3 => "SYN_SENT",
            4 => "SYN_RECEIVED",
            5 => "ESTABLISHED",
            6 => "FIN_WAIT_1",
            7 => "FIN_WAIT_2",
            8 => "CLOSE_WAIT",
            9 => "CLOSING",
            10 => "LAST_ACK",
            11 => "TIME_WAIT",
            12 => "DELETE_TCB",
            _ => "UNKNOWN",
        }
    }

    fn api_error(name: &str, code: u32) -> String {
        format!("{name} failed with Windows error {code}")
    }

    unsafe fn query_rows<T: Copy>(
        query: QueryTable,
        family: u32,
        table_class: i32,
        name: &str,
    ) -> Result<Vec<T>, String> {
        let mut required_bytes = 0_u32;
        let first_status = query(
            null_mut(),
            &mut required_bytes,
            0,
            family,
            table_class,
            0,
        );

        if first_status != ERROR_INSUFFICIENT_BUFFER && first_status != 0 {
            return Err(api_error(name, first_status));
        }
        if required_bytes == 0 {
            return Ok(Vec::new());
        }

        for _ in 0..MAX_RESIZE_ATTEMPTS {
            if required_bytes > MAX_TABLE_BYTES {
                return Err(format!(
                    "{name} requested an unexpectedly large table ({required_bytes} bytes)"
                ));
            }

            // u32 storage gives the Win32 table buffer DWORD alignment.
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

            let rows_ptr = base.add(1).cast::<T>();
            return Ok(slice::from_raw_parts(rows_ptr, row_count).to_vec());
        }

        Err(format!(
            "{name} table kept changing size after {MAX_RESIZE_ATTEMPTS} attempts"
        ))
    }

    pub(super) type TcpConnection = (String, u16, String, u32, u16, String);
    pub(super) type UdpBinding = (String, u16, u32);

    pub(super) fn list_tcp_connections() -> Result<Vec<TcpConnection>, String> {
        let mut rows = Vec::new();
        let mut successful_tables = 0_usize;
        let mut errors = Vec::new();

        unsafe {
            match query_rows::<TcpRowV4>(
                GetExtendedTcpTable,
                AF_INET,
                TCP_TABLE_OWNER_PID_ALL,
                "GetExtendedTcpTable(AF_INET)",
            ) {
                Ok(table) => {
                    successful_tables += 1;
                    rows.reserve(table.len());
                    for row in table {
                        let remote = Ipv4Addr::from(row.remote_addr.to_ne_bytes());
                        if remote.is_unspecified() {
                            continue;
                        }
                        rows.push((
                            remote.to_string(),
                            decode_port(row.remote_port),
                            "TCP".to_owned(),
                            row.owning_pid,
                            decode_port(row.local_port),
                            tcp_state(row.state).to_owned(),
                        ));
                    }
                }
                Err(error) => errors.push(error),
            }

            match query_rows::<TcpRowV6>(
                GetExtendedTcpTable,
                AF_INET6,
                TCP_TABLE_OWNER_PID_ALL,
                "GetExtendedTcpTable(AF_INET6)",
            ) {
                Ok(table) => {
                    successful_tables += 1;
                    rows.reserve(table.len());
                    for row in table {
                        let remote = Ipv6Addr::from(row.remote_addr);
                        if remote.is_unspecified() {
                            continue;
                        }
                        rows.push((
                            remote.to_string(),
                            decode_port(row.remote_port),
                            "TCP".to_owned(),
                            row.owning_pid,
                            decode_port(row.local_port),
                            tcp_state(row.state).to_owned(),
                        ));
                    }
                }
                Err(error) => errors.push(error),
            }
        }

        if successful_tables == 0 {
            return Err(errors.join("; "));
        }
        Ok(rows)
    }

    pub(super) fn list_udp_bindings() -> Result<Vec<UdpBinding>, String> {
        let mut rows = Vec::new();
        let mut successful_tables = 0_usize;
        let mut errors = Vec::new();

        unsafe {
            match query_rows::<UdpRowV4>(
                GetExtendedUdpTable,
                AF_INET,
                UDP_TABLE_OWNER_PID,
                "GetExtendedUdpTable(AF_INET)",
            ) {
                Ok(table) => {
                    successful_tables += 1;
                    rows.reserve(table.len());
                    for row in table {
                        let local = Ipv4Addr::from(row.local_addr.to_ne_bytes());
                        rows.push((
                            local.to_string(),
                            decode_port(row.local_port),
                            row.owning_pid,
                        ));
                    }
                }
                Err(error) => errors.push(error),
            }

            match query_rows::<UdpRowV6>(
                GetExtendedUdpTable,
                AF_INET6,
                UDP_TABLE_OWNER_PID,
                "GetExtendedUdpTable(AF_INET6)",
            ) {
                Ok(table) => {
                    successful_tables += 1;
                    rows.reserve(table.len());
                    for row in table {
                        let local = Ipv6Addr::from(row.local_addr);
                        rows.push((
                            local.to_string(),
                            decode_port(row.local_port),
                            row.owning_pid,
                        ));
                    }
                }
                Err(error) => errors.push(error),
            }
        }

        if successful_tables == 0 {
            return Err(errors.join("; "));
        }
        Ok(rows)
    }

    #[cfg(test)]
    mod tests {
        use super::{decode_port, tcp_state};

        #[test]
        fn network_port_is_converted_to_host_order() {
            assert_eq!(decode_port(0xBB01), 443);
            assert_eq!(decode_port(0x5000), 80);
        }

        #[test]
        fn tcp_state_names_are_stable() {
            assert_eq!(tcp_state(5), "ESTABLISHED");
            assert_eq!(tcp_state(2), "LISTEN");
            assert_eq!(tcp_state(99), "UNKNOWN");
        }
    }
}

#[cfg(not(windows))]
mod windows_ip_helper {
    pub(super) type TcpConnection = (String, u16, String, u32, u16, String);
    pub(super) type UdpBinding = (String, u16, u32);

    pub(super) fn list_tcp_connections() -> Result<Vec<TcpConnection>, String> {
        Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
    }

    pub(super) fn list_udp_bindings() -> Result<Vec<UdpBinding>, String> {
        Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
    }
}

#[pyfunction]
fn list_tcp_connections(
    py: Python<'_>,
) -> PyResult<Vec<windows_ip_helper::TcpConnection>> {
    py.detach(windows_ip_helper::list_tcp_connections)
        .map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn list_udp_bindings(
    py: Python<'_>,
) -> PyResult<Vec<windows_ip_helper::UdpBinding>> {
    py.detach(windows_ip_helper::list_udp_bindings)
        .map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn capabilities() -> Vec<&'static str> {
    vec![
        "windows-ip-helper",
        "tcp-owner-pid",
        "udp-owner-pid",
        "ipv4",
        "ipv6",
        "read-only",
    ]
}

#[pymodule]
fn _swarmatlas_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(list_tcp_connections, module)?)?;
    module.add_function(wrap_pyfunction!(list_udp_bindings, module)?)?;
    module.add_function(wrap_pyfunction!(capabilities, module)?)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
