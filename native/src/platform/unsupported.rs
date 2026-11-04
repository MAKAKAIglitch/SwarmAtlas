pub(crate) type TcpConnection = (String, u16, String, u32, u16, String);
pub(crate) type UdpBinding = (String, u16, u32);

pub(crate) fn list_tcp_connections(_limit: usize) -> Result<Vec<TcpConnection>, String> {
    Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
}

pub(crate) fn list_udp_bindings(_limit: usize) -> Result<Vec<UdpBinding>, String> {
    Err("The native SwarmAtlas monitor currently supports Windows only".to_owned())
}
