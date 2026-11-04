use std::ffi::c_void;
use std::ptr::{addr_of, null_mut};

use windows_sys::Win32::NetworkManagement::IpHelper::{
    FreeMibTable, GetIfTable2, MIB_IF_ROW2, MIB_IF_TABLE2,
};
use windows_sys::Win32::NetworkManagement::Ndis::IfOperStatusUp;

pub(crate) type NetworkInterface = (String, bool, u64, u64);

struct MibTable(*mut MIB_IF_TABLE2);

impl Drop for MibTable {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { FreeMibTable(self.0.cast::<c_void>()) };
        }
    }
}

fn decode_alias(value: &[u16]) -> String {
    let length = value
        .iter()
        .position(|character| *character == 0)
        .unwrap_or(value.len());
    String::from_utf16_lossy(&value[..length])
}

/// Read all interface aliases and counters in one Windows IP Helper call.
pub(crate) fn list_network_interfaces() -> Result<Vec<NetworkInterface>, String> {
    let mut raw_table = null_mut();
    let status = unsafe { GetIfTable2(&mut raw_table) };
    if status != 0 {
        return Err(format!("GetIfTable2 failed with Windows error {status}"));
    }
    if raw_table.is_null() {
        return Err("GetIfTable2 returned a null table".to_owned());
    }

    let table = MibTable(raw_table);
    let count = unsafe { (*table.0).NumEntries as usize };
    if count > 4096 {
        return Err(format!(
            "GetIfTable2 returned an unexpected interface count ({count})"
        ));
    }

    // Taking the flexible-array field address preserves the padding required by the Windows ABI.
    let first_row = unsafe { addr_of!((*table.0).Table).cast::<MIB_IF_ROW2>() };
    let mut result = Vec::with_capacity(count);
    for index in 0..count {
        let row = unsafe { &*first_row.add(index) };
        let alias = decode_alias(&row.Alias);
        if alias.is_empty() {
            continue;
        }
        result.push((alias, row.OperStatus == IfOperStatusUp, row.OutOctets, row.InOctets));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::decode_alias;

    #[test]
    fn interface_alias_decodes_utf16_and_stops_at_null() {
        assert_eq!(decode_alias(&[87, 105, 45, 70, 105, 0, 88]), "Wi-Fi");
    }

    #[test]
    fn empty_interface_alias_is_preserved_as_empty() {
        assert_eq!(decode_alias(&[0, 87, 105]), "");
    }
}
