use std::collections::HashSet;
use std::mem::size_of;

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};

pub(crate) type ProcessName = (u32, String);

struct ProcessSnapshot(*mut core::ffi::c_void);

impl Drop for ProcessSnapshot {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != INVALID_HANDLE_VALUE {
            unsafe { CloseHandle(self.0); }
        }
    }
}

fn decode_process_name(value: &[u16]) -> String {
    let length = value.iter().position(|character| *character == 0).unwrap_or(value.len());
    String::from_utf16_lossy(&value[..length])
}

/// Resolve only PIDs requested by the current TCP socket snapshot.
pub(crate) fn resolve_process_names(process_ids: &[u32]) -> Result<Vec<ProcessName>, String> {
    const MAX_PROCESS_IDS: usize = 4096;

    let mut missing: HashSet<u32> = process_ids.iter().copied().filter(|pid| *pid > 0).collect();
    if missing.is_empty() {
        return Ok(Vec::new());
    }
    if missing.len() > MAX_PROCESS_IDS {
        return Err(format!("process lookup is limited to {MAX_PROCESS_IDS} unique PIDs per call"));
    }

    let raw_snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if raw_snapshot == INVALID_HANDLE_VALUE {
        let error = unsafe { GetLastError() };
        return Err(format!("CreateToolhelp32Snapshot failed with Windows error {error}"));
    }
    let snapshot = ProcessSnapshot(raw_snapshot);

    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    if unsafe { Process32FirstW(snapshot.0, &mut entry) } == 0 {
        let error = unsafe { GetLastError() };
        return Err(format!("Process32FirstW failed with Windows error {error}"));
    }

    let mut result = Vec::with_capacity(missing.len());
    loop {
        let process_id = entry.th32ProcessID;
        if missing.remove(&process_id) {
            let name = decode_process_name(&entry.szExeFile);
            if !name.is_empty() {
                result.push((process_id, name));
            }
        }

        if missing.is_empty() || unsafe { Process32NextW(snapshot.0, &mut entry) } == 0 {
            break;
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::decode_process_name;

    #[test]
    fn process_executable_name_decodes_utf16() {
        assert_eq!(
            decode_process_name(&[98, 114, 111, 119, 115, 101, 114, 46, 101, 120, 101, 0]),
            "browser.exe"
        );
    }
}
