//! Windows 11 first-menu command "Empty with Gorilla Trash..." (P41, owner 2026-11-23).
//!
//! The modern right-click menu only lists commands from a package with identity, so Fieldkit registers a signed
//! sparse MSIX whose manifest points at this COM server. Invoke starts `gorilla-trash.exe empty` in its own console:
//! the confirmation stays interactive, nothing is emptied here. Windows only; on other systems the crate is empty.
#![cfg(windows)]

use std::ffi::c_void;

use windows::core::{implement, w, Ref, Result, BOOL, GUID, HRESULT, IUnknown, Interface, PCWSTR, PWSTR};
use windows::Win32::Foundation::{CLASS_E_CLASSNOTAVAILABLE, CLASS_E_NOAGGREGATION, E_NOTIMPL, E_POINTER, S_FALSE};
use windows::Win32::System::Com::{IBindCtx, IClassFactory, IClassFactory_Impl};
use windows::Win32::UI::Shell::{
    IEnumExplorerCommand, IExplorerCommand, IExplorerCommand_Impl, IShellItemArray, SHStrDupW, ShellExecuteW,
    ECF_DEFAULT, ECS_ENABLED,
};
use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

/// CLSID of the command; the manifest (`fieldkit/desk/trashmenu.py`) uses the same value.
const CLSID_TRASH_MENU: GUID = GUID::from_u128(0x7c3a1f52_9d4b_4e8a_b2c6_1f0e5a9d3b71);

fn trash_exe() -> Vec<u16> {
    let base = std::env::var("LOCALAPPDATA").unwrap_or_default();
    format!("{base}\\Fieldkit\\bin\\gorilla-trash.exe\0").encode_utf16().collect()
}

#[implement(IExplorerCommand)]
struct TrashCommand;

#[allow(non_snake_case)]
impl IExplorerCommand_Impl for TrashCommand_Impl {
    fn GetTitle(&self, _items: Ref<'_, IShellItemArray>) -> Result<PWSTR> {
        unsafe { SHStrDupW(w!("Empty with Gorilla Trash...")) }
    }

    fn GetIcon(&self, _items: Ref<'_, IShellItemArray>) -> Result<PWSTR> {
        let mut path = trash_exe();
        path.pop();
        path.extend(",0\0".encode_utf16());
        unsafe { SHStrDupW(PCWSTR(path.as_ptr())) }
    }

    fn GetToolTip(&self, _items: Ref<'_, IShellItemArray>) -> Result<PWSTR> {
        unsafe { SHStrDupW(w!("Lists what is in the Recycle Bin and asks before emptying it")) }
    }

    fn GetCanonicalName(&self) -> Result<GUID> {
        Ok(CLSID_TRASH_MENU)
    }

    fn GetState(&self, _items: Ref<'_, IShellItemArray>, _slow: BOOL) -> Result<u32> {
        Ok(ECS_ENABLED.0 as u32)
    }

    fn Invoke(&self, _items: Ref<'_, IShellItemArray>, _ctx: Ref<'_, IBindCtx>) -> Result<()> {
        let exe = trash_exe();
        unsafe {
            ShellExecuteW(None, w!("open"), PCWSTR(exe.as_ptr()), w!("empty"), PCWSTR::null(), SW_SHOWNORMAL);
        }
        Ok(())
    }

    fn GetFlags(&self) -> Result<u32> {
        Ok(ECF_DEFAULT.0 as u32)
    }

    fn EnumSubCommands(&self) -> Result<IEnumExplorerCommand> {
        Err(E_NOTIMPL.into())
    }
}

#[implement(IClassFactory)]
struct Factory;

#[allow(non_snake_case)]
impl IClassFactory_Impl for Factory_Impl {
    fn CreateInstance(&self, outer: Ref<'_, IUnknown>, iid: *const GUID, object: *mut *mut c_void) -> Result<()> {
        if object.is_null() || iid.is_null() {
            return Err(E_POINTER.into());
        }
        unsafe { *object = std::ptr::null_mut() };
        if !outer.is_null() {
            return Err(CLASS_E_NOAGGREGATION.into());
        }
        let command: IExplorerCommand = TrashCommand.into();
        unsafe { command.query(iid, object).ok() }
    }

    fn LockServer(&self, _lock: BOOL) -> Result<()> {
        Ok(())
    }
}

/// # Safety
/// Called by COM with valid pointers.
#[no_mangle]
pub unsafe extern "system" fn DllGetClassObject(clsid: *const GUID, iid: *const GUID, object: *mut *mut c_void) -> HRESULT {
    if clsid.is_null() || iid.is_null() || object.is_null() {
        return E_POINTER;
    }
    if *clsid != CLSID_TRASH_MENU {
        return CLASS_E_CLASSNOTAVAILABLE;
    }
    let factory: IClassFactory = Factory.into();
    factory.query(iid, object)
}

#[no_mangle]
pub extern "system" fn DllCanUnloadNow() -> HRESULT {
    S_FALSE
}
