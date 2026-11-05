pub(crate) type TcpConnection = (String, u16, String, u32, u16, String);
pub(crate) type UdpBinding = (String, u16, u32);
pub(crate) type TcpFlowCounter = (String, u16, u32, u16, u64, u64);

pub(crate) fn collect_tcp_flow_stats(_limit: usize) -> Result<Vec<TcpFlowCounter>, String> {
    Err("TCP flow statistics currently support Windows only".to_owned())
}

pub(crate) fn list_tcp_connections(_limit: usize) -> Result<Vec<TcpConnection>, String> {
    Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
}

pub(crate) fn list_udp_bindings(_limit: usize) -> Result<Vec<UdpBinding>, String> {
    Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
}

pub(crate) fn count_udp_bindings() -> Result<usize, String> {
    Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
}

pub(crate) type NetworkInterface = (String, bool, u64, u64);
pub(crate) type ProcessName = (u32, String);

pub(crate) fn list_network_interfaces() -> Result<Vec<NetworkInterface>, String> {
    Err("Native interface counters currently support Windows only".to_owned())
}

pub(crate) fn resolve_process_names(_process_ids: &[u32]) -> Result<Vec<ProcessName>, String> {
    Err("Native process lookup currently supports Windows only".to_owned())
}
