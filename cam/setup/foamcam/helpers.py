import os

import winreg
import adsk.cam
from pathlib import Path

def get_cam_product(app, ui, doc):
    # Ensure Manufacture (CAM) workspace is active so the CAM product exists.
    try:
        ws = ui.workspaces.itemById('CAMEnvironment')
        if ws and ui.activeWorkspace and ui.activeWorkspace.id != 'CAMEnvironment':
            ws.activate()
            adsk.doEvents()
    except:
        # If workspace activation fails, we still try to get the CAM product below.
        pass

    # Try activeProduct first (often works once Manufacture is active)
    cam = adsk.cam.CAM.cast(app.activeProduct)
    if cam:
        return cam

    # Fallback: try from document products
    try:
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
        if cam:
            return cam
    except:
        pass

    return None

def get_desktop_path(as_path_object: bool = False):
    """
    Returns the user's Desktop path, respecting OneDrive redirection.
    Uses Windows registry when available, then falls back.
    """
    desktop = None

    # 1) Preferred: Windows 'User Shell Folders' registry value (OneDrive-aware)
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        ) as key:
            val, _typ = winreg.QueryValueEx(key, "Desktop")
            # REG_EXPAND_SZ often contains %USERPROFILE% or %OneDrive%
            desktop = os.path.expandvars(val)
    except Exception:
        desktop = None

    # 2) Validate / normalize
    if desktop:
        desktop = os.path.normpath(desktop)
        if os.path.isdir(desktop):
            if as_path_object:
                return Path(desktop)
            return desktop

    # 3) Fallbacks
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")

    # Common: OneDrive Desktop (sometimes not reflected in registry in locked-down envs)
    od = os.environ.get("OneDrive")
    if od:
        od_desktop = os.path.normpath(os.path.join(od, "Desktop"))
        if os.path.isdir(od_desktop):
            if as_path_object:
                return Path(od_desktop)
            return od_desktop

    # Classic: %USERPROFILE%\Desktop
    fallback = os.path.normpath(os.path.join(home, "Desktop"))
    if os.path.isdir(fallback):
        if as_path_object:
            return Path(fallback)
        return fallback

    # Last resort: home
    if as_path_object:
        return Path(home)
    return home
