from __future__ import annotations

import errno
import os
import secrets
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import HostPathEscape, StorageFull


@dataclass(frozen=True, slots=True)
class RootIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _WindowsParent:
    handle: int
    path: Path


def canonical_grant_root(
    path: Path,
    *,
    home: Path | None = None,
    forbidden_roots: tuple[Path, ...] = (),
) -> Path:
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("granted path must be a directory")
    forbidden = [Path(root.anchor).resolve(strict=True)]
    if home is not None:
        forbidden.append(home.expanduser().resolve(strict=False))
    forbidden.extend(item.expanduser().resolve(strict=False) for item in forbidden_roots)
    if any(_same_path(root, item) for item in forbidden):
        raise ValueError("whole home or filesystem root cannot be granted")
    if _is_reparse_point(root):
        raise HostPathEscape()
    return root


def root_identity(root: Path) -> RootIdentity:
    info = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or _is_reparse_point(root):
        raise HostPathEscape()
    return RootIdentity(info.st_dev, info.st_ino)


def validate_root_identity(root: Path, expected: RootIdentity) -> Path:
    canonical = root.expanduser().resolve(strict=True)
    current = root_identity(canonical)
    if current != expected:
        raise HostPathEscape()
    return canonical


def safe_open_under_root(
    root: Path,
    relative: str,
    *,
    flags: int,
    mode: int = 0o600,
    expected_identity: RootIdentity | None = None,
    allow_windows_managed_root: bool = False,
) -> int:
    """Open a path beneath root without following a reparse component."""
    parts = _safe_relative_parts(relative)
    canonical = _validated_root(root, expected_identity)
    if _supports_secure_dir_fd():
        with _open_parent_fd(
            canonical, parts, expected_identity=expected_identity
        ) as parent_fd:
            try:
                return os.open(
                    parts[-1],
                    flags | os.O_NOFOLLOW,
                    mode,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                _translate_escape(exc)
                raise

    if os.name == "nt":
        if expected_identity is not None and not allow_windows_managed_root:
            raise HostPathEscape()
        with _open_parent_path_windows(
            canonical,
            parts,
            expected_identity=expected_identity,
        ) as parent:
            return _open_final_windows(parent.handle, parts[-1], flags=flags, mode=mode)
    if expected_identity is not None:
        raise HostPathEscape()

    target = _validated_path(canonical, parts, allow_missing_final=bool(flags & os.O_CREAT))
    try:
        descriptor = os.open(target, flags, mode)
    except OSError as exc:
        _translate_escape(exc)
        raise
    try:
        if _is_reparse_point(target) or not _within(canonical, target.resolve(strict=True)):
            raise HostPathEscape()
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def read_bytes_under_root(
    root: Path,
    relative: str,
    *,
    expected_identity: RootIdentity | None = None,
    allow_windows_managed_root: bool = False,
) -> bytes:
    descriptor = safe_open_under_root(
        root,
        relative,
        flags=os.O_RDONLY,
        expected_identity=expected_identity,
        allow_windows_managed_root=allow_windows_managed_root,
    )
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def atomic_write(path: Path, data: bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        _write_descriptor(descriptor, data)
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
        _sync_directory(path.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise StorageFull() from exc
        raise


def atomic_write_under_root(
    root: Path,
    relative: str,
    data: bytes,
    *,
    overwrite: bool = True,
    expected_identity: RootIdentity | None = None,
    allow_windows_managed_root: bool = False,
) -> None:
    parts = _safe_relative_parts(relative)
    canonical = _validated_root(root, expected_identity)
    if _supports_secure_dir_fd():
        with _open_parent_fd(
            canonical,
            parts,
            create=True,
            expected_identity=expected_identity,
        ) as parent_fd:
            _atomic_write_at(parent_fd, parts[-1], data, overwrite=overwrite)
        return
    if os.name == "nt":
        if expected_identity is not None and not allow_windows_managed_root:
            raise HostPathEscape()
        with _open_parent_path_windows(
            canonical,
            parts,
            create=True,
            expected_identity=expected_identity,
        ) as parent:
            _atomic_write_windows(parent.handle, parts[-1], data, overwrite=overwrite)
        return
    if expected_identity is not None:
        raise HostPathEscape()

    destination = _validated_destination_path(canonical, parts)
    if destination.exists() and (_is_reparse_point(destination) or not overwrite):
        if _is_reparse_point(destination):
            raise HostPathEscape()
        raise FileExistsError(destination)
    atomic_write(destination, data, overwrite=overwrite)
    _validated_root(root, expected_identity)


def unlink_under_root(
    root: Path,
    relative: str,
    *,
    missing_ok: bool = False,
    expected_identity: RootIdentity | None = None,
    allow_windows_managed_root: bool = False,
) -> None:
    """Remove a file beneath root without following a reparse component."""
    parts = _safe_relative_parts(relative)
    canonical = _validated_root(root, expected_identity)
    if _supports_secure_dir_fd():
        with _open_parent_fd(
            canonical, parts, expected_identity=expected_identity
        ) as parent_fd:
            try:
                os.unlink(parts[-1], dir_fd=parent_fd)
            except FileNotFoundError:
                if not missing_ok:
                    raise
            else:
                os.fsync(parent_fd)
        return
    if os.name == "nt":
        if expected_identity is not None and not allow_windows_managed_root:
            raise HostPathEscape()
        with _open_parent_path_windows(
            canonical,
            parts,
            expected_identity=expected_identity,
        ) as parent:
            try:
                handle = _nt_open_relative_windows(
                    parent.handle,
                    parts[-1],
                    directory=False,
                    create=False,
                    desired_access=0x00010000,  # DELETE
                )
            except FileNotFoundError:
                if not missing_ok:
                    raise
                return
            try:
                _mark_handle_delete_windows(handle)
            finally:
                _close_handle_windows(handle)
        return
    if expected_identity is not None:
        raise HostPathEscape()

    target = _validated_path(canonical, parts)
    try:
        target.unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise
    _validated_root(root, expected_identity)


def rename_under_root(
    root: Path,
    source: str,
    destination: str,
    *,
    overwrite: bool = False,
    expected_identity: RootIdentity | None = None,
    allow_windows_managed_root: bool = False,
) -> None:
    source_parts = _safe_relative_parts(source)
    destination_parts = _safe_relative_parts(destination)
    canonical = _validated_root(root, expected_identity)
    if _supports_secure_dir_fd():
        with _open_parent_fd(
            canonical, source_parts, expected_identity=expected_identity
        ) as source_fd, _open_parent_fd(
            canonical,
            destination_parts,
            expected_identity=expected_identity,
        ) as destination_fd:
            try:
                if overwrite:
                    os.replace(
                        source_parts[-1],
                        destination_parts[-1],
                        src_dir_fd=source_fd,
                        dst_dir_fd=destination_fd,
                    )
                else:
                    _rename_noreplace_at(
                        source_fd,
                        source_parts[-1],
                        destination_fd,
                        destination_parts[-1],
                    )
            except OSError as exc:
                _translate_escape(exc)
                raise
            os.fsync(destination_fd)
        return
    if os.name == "nt":
        if expected_identity is not None and not allow_windows_managed_root:
            raise HostPathEscape()
        with _open_parent_path_windows(
            canonical,
            source_parts,
            expected_identity=expected_identity,
        ) as source_parent, _open_parent_path_windows(
            canonical,
            destination_parts,
            expected_identity=expected_identity,
        ) as destination_parent:
            _rename_relative_windows(
                source_parent.handle,
                source_parts[-1],
                destination_parent.handle,
                destination_parts[-1],
                overwrite=overwrite,
            )
        return
    if expected_identity is not None:
        raise HostPathEscape()

    source_path = _validated_path(canonical, source_parts)
    destination_path = _validated_path(canonical, destination_parts, allow_missing_final=True)
    if destination_path.exists() and not overwrite:
        raise FileExistsError(destination_path)
    os.replace(source_path, destination_path)
    _validated_root(root, expected_identity)


def _rename_noreplace_at(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unsupported")
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(destination_name),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), destination_name)
    raise OSError(error, os.strerror(error), destination_name)


@contextmanager
def _open_parent_path_windows(
    root: Path,
    parts: tuple[str, ...],
    *,
    create: bool = False,
    expected_identity: RootIdentity | None = None,
) -> Iterator[_WindowsParent]:
    if os.name != "nt":
        raise HostPathEscape()
    handles: list[int] = []
    current = root
    try:
        handles.append(_open_directory_handle_windows(current))
        if expected_identity is not None:
            handle_identity = _identity_from_handle_windows(handles[-1])
            # CPython's Windows st_dev is a volume-name hash, while Win32
            # exposes the volume serial here. The 64-bit file ID (st_ino)
            # is the common stable identity and changes on replacement.
            if handle_identity.inode != expected_identity.inode:
                raise HostPathEscape()
        for component in parts[:-1]:
            current = current / component
            try:
                handle = _nt_open_relative_windows(
                    handles[-1],
                    component,
                    directory=True,
                    create=False,
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    handle = _nt_open_relative_windows(
                        handles[-1],
                        component,
                        directory=True,
                        create=True,
                    )
                except FileExistsError:
                    handle = _nt_open_relative_windows(
                        handles[-1],
                        component,
                        directory=True,
                        create=False,
                    )
            handles.append(handle)
        yield _WindowsParent(handles[-1], current)
    finally:
        for handle in reversed(handles):
            _close_handle_windows(handle)


def _open_directory_handle_windows(path: Path) -> int:
    handle = _create_file_windows(
        path,
        desired_access=0x00000002 | 0x00000080 | 0x00000020,
        # ADD_FILE | READ_ATTRIBUTES | TRAVERSE
        creation_disposition=3,  # OPEN_EXISTING
        flags=0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
    )
    if _handle_is_reparse_windows(handle):
        _close_handle_windows(handle)
        raise HostPathEscape()
    return handle


def _open_final_windows(parent_handle: int, name: str, *, flags: int, mode: int) -> int:
    del mode
    access_mode = flags & (os.O_WRONLY | os.O_RDWR)
    desired_access = 0x80000000  # GENERIC_READ
    if access_mode == os.O_WRONLY:
        desired_access = 0x40000000  # GENERIC_WRITE
    elif access_mode == os.O_RDWR:
        desired_access = 0x80000000 | 0x40000000
    handle = _nt_open_relative_windows(
        parent_handle,
        name,
        directory=False,
        create=bool(flags & os.O_CREAT),
        desired_access=desired_access,
        exclusive=bool(flags & os.O_EXCL),
        truncate=bool(flags & os.O_TRUNC),
    )
    if _handle_is_reparse_windows(handle):
        _close_handle_windows(handle)
        raise HostPathEscape()
    import msvcrt

    try:
        return msvcrt.open_osfhandle(handle, flags)
    except Exception:
        _close_handle_windows(handle)
        raise


def _atomic_write_windows(
    parent_handle: int,
    name: str,
    data: bytes,
    *,
    overwrite: bool,
) -> None:
    temporary_name = f".{name}.{secrets.token_hex(8)}"
    handle = _nt_open_relative_windows(
        parent_handle,
        temporary_name,
        directory=False,
        create=True,
        exclusive=True,
        desired_access=0x40000000 | 0x00010000,  # GENERIC_WRITE | DELETE
    )
    try:
        _write_handle_windows(handle, data)
        _rename_open_handle_windows(
            handle,
            parent_handle,
            name,
            overwrite=overwrite,
        )
    except OSError as exc:
        _mark_handle_delete_windows(handle)
        if exc.errno == errno.ENOSPC:
            raise StorageFull() from exc
        raise
    finally:
        _close_handle_windows(handle)


def _rename_relative_windows(
    source_parent: int,
    source_name: str,
    destination_parent: int,
    destination_name: str,
    *,
    overwrite: bool,
) -> None:
    handle = _nt_open_relative_windows(
        source_parent,
        source_name,
        directory=False,
        create=False,
        desired_access=0x00010000,  # DELETE
    )
    try:
        _rename_open_handle_windows(
            handle,
            destination_parent,
            destination_name,
            overwrite=overwrite,
        )
    finally:
        _close_handle_windows(handle)


def _create_file_windows(
    path: Path,
    *,
    desired_access: int,
    creation_disposition: int,
    flags: int,
) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        desired_access,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        creation_disposition,
        flags,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        _raise_windows_error(ctypes.get_last_error(), str(path))
    return int(handle)


def _nt_open_relative_windows(
    parent_handle: int,
    name: str,
    *,
    directory: bool,
    create: bool,
    desired_access: int | None = None,
    exclusive: bool = False,
    truncate: bool = False,
) -> int:
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        )

    class ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_quality_of_service", wintypes.LPVOID),
        )

    class IoStatusBlock(ctypes.Structure):
        _fields_ = (("status", ctypes.c_void_p), ("information", ctypes.c_size_t))

    name_buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(name) * ctypes.sizeof(ctypes.c_wchar),
        (len(name) + 1) * ctypes.sizeof(ctypes.c_wchar),
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        wintypes.HANDLE(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040,  # OBJ_CASE_INSENSITIVE
        None,
        None,
    )
    status_block = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    nt_create_file = ntdll.NtCreateFile
    nt_create_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    )
    nt_create_file.restype = ctypes.c_long
    handle = wintypes.HANDLE()
    if desired_access is None:
        desired_access = 0x00000001 | 0x00000002 | 0x00000080 | 0x00000020
    desired_access |= 0x00100000 | 0x00000080  # SYNCHRONIZE | READ_ATTRIBUTES
    if create:
        if truncate:
            disposition = 5  # FILE_OVERWRITE_IF
        elif exclusive:
            disposition = 2  # FILE_CREATE
        else:
            disposition = 3  # FILE_OPEN_IF
    elif truncate:
        disposition = 4  # FILE_OVERWRITE
    else:
        disposition = 1  # FILE_OPEN
    options = 0x00200000 | 0x00000020  # OPEN_REPARSE_POINT | SYNCHRONOUS_IO_NONALERT
    options |= 0x00000001 if directory else 0x00000040
    status = nt_create_file(
        ctypes.byref(handle),
        desired_access,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000080,
        0x00000001 | 0x00000002 | 0x00000004,
        disposition,
        options,
        None,
        0,
    )
    if status < 0:
        rtl_error = ntdll.RtlNtStatusToDosError
        rtl_error.argtypes = (ctypes.c_long,)
        rtl_error.restype = wintypes.ULONG
        _raise_windows_error(int(rtl_error(status)), name)
    value = int(handle.value)
    if _handle_is_reparse_windows(value):
        _close_handle_windows(value)
        raise HostPathEscape()
    return value


def _identity_from_handle_windows(handle: int) -> RootIdentity:
    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = (
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        )

    information = ByHandleFileInformation()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.GetFileInformationByHandle(
        wintypes.HANDLE(handle), ctypes.byref(information)
    ):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))
    inode = (information.file_index_high << 32) | information.file_index_low
    return RootIdentity(information.volume_serial_number, inode)


def _write_handle_windows(handle: int, data: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + (1 << 20)]
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(chunk)
        if not kernel32.WriteFile(
            wintypes.HANDLE(handle),
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            error = ctypes.get_last_error()
            if error == 112:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            _raise_windows_error(error, "temporary workspace file")
        offset += written.value
    if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
        error = ctypes.get_last_error()
        _raise_windows_error(error, "temporary workspace file")


def _rename_open_handle_windows(
    handle: int,
    destination_parent: int,
    destination_name: str,
    *,
    overwrite: bool,
) -> None:
    import ctypes
    from ctypes import wintypes

    class FileRenameInformation(ctypes.Structure):
        _fields_ = (
            ("replace_if_exists", wintypes.BOOLEAN),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * len(destination_name)),
        )

    information = FileRenameInformation(
        overwrite,
        wintypes.HANDLE(destination_parent),
        len(destination_name) * ctypes.sizeof(ctypes.c_wchar),
        destination_name,
    )
    class IoStatusBlock(ctypes.Structure):
        _fields_ = (("status", ctypes.c_void_p), ("information", ctypes.c_size_t))

    ntdll = ctypes.WinDLL("ntdll")
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    )
    set_information.restype = ctypes.c_long
    status_block = IoStatusBlock()
    status = set_information(
        wintypes.HANDLE(handle),
        ctypes.byref(status_block),
        ctypes.byref(information),
        FileRenameInformation.file_name.offset
        + information.file_name_length,
        10,  # FileRenameInformation
    )
    if status < 0:
        rtl_error = ntdll.RtlNtStatusToDosError
        rtl_error.argtypes = (ctypes.c_long,)
        rtl_error.restype = wintypes.ULONG
        _raise_windows_error(int(rtl_error(status)), destination_name)


def _mark_handle_delete_windows(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    class FileDispositionInformation(ctypes.Structure):
        _fields_ = (("delete_file", wintypes.BOOLEAN),)

    information = FileDispositionInformation(True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    if not set_information(
        wintypes.HANDLE(handle),
        4,  # FileDispositionInfo
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        _raise_windows_error(error, "workspace file")


def _raise_windows_error(error: int, path: str) -> None:
    if error in {2, 3}:
        raise FileNotFoundError(error, os.strerror(error), path)
    if error in {5}:
        raise PermissionError(error, os.strerror(error), path)
    if error in {80, 183}:
        raise FileExistsError(error, os.strerror(error), path)
    raise OSError(error, os.strerror(error), path)


def _handle_is_reparse_windows(handle: int) -> bool:
    import ctypes
    from ctypes import wintypes

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (("file_attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD))

    information = FileAttributeTagInfo()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    result = kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle),
        9,  # FileAttributeTagInfo
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    if not result:
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))
    return bool(information.file_attributes & 0x00000400)


def _close_handle_windows(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.CloseHandle(wintypes.HANDLE(handle)):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))


def _safe_relative_parts(relative: str) -> tuple[str, ...]:
    normalized = relative.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts:
        raise HostPathEscape()
    if any(part in ("", ".", "..") for part in path.parts):
        raise HostPathEscape()
    if ":" in path.parts[0]:
        raise HostPathEscape()
    return tuple(path.parts)


def validate_relative_path(relative: str) -> PurePosixPath:
    """Return the canonical spelling accepted by workspace operations."""
    return PurePosixPath(*_safe_relative_parts(relative))


def _validated_root(root: Path, expected: RootIdentity | None) -> Path:
    canonical = root.expanduser().resolve(strict=True)
    if expected is not None:
        validate_root_identity(canonical, expected)
    elif _is_reparse_point(canonical) or not canonical.is_dir():
        raise HostPathEscape()
    return canonical


def _supports_secure_dir_fd() -> bool:
    return (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
    )


def secure_handle_relative_supported() -> bool:
    return _supports_secure_dir_fd() or os.name == "nt"


@contextmanager
def _open_parent_fd(
    root: Path,
    parts: tuple[str, ...],
    *,
    create: bool = False,
    expected_identity: RootIdentity | None = None,
) -> Iterator[int]:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current_fd = root_fd
    owned: list[int] = []
    try:
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise HostPathEscape()
        if expected_identity is not None and RootIdentity(
            root_info.st_dev, root_info.st_ino
        ) != expected_identity:
            raise HostPathEscape()
        for component in parts[:-1]:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                _translate_escape(exc)
                raise
            owned.append(next_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        for descriptor in reversed(owned):
            os.close(descriptor)
        os.close(root_fd)


def _atomic_write_at(parent_fd: int, name: str, data: bytes, *, overwrite: bool) -> None:
    temporary_name = f".{name}.{secrets.token_hex(8)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            _write_descriptor(descriptor, data)
        finally:
            descriptor = -1
        if overwrite:
            os.replace(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        else:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        if exc.errno == errno.ENOSPC:
            raise StorageFull() from exc
        _translate_escape(exc)
        raise


def _write_descriptor(descriptor: int, data: bytes) -> None:
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _validated_path(
    root: Path,
    parts: tuple[str, ...],
    *,
    allow_missing_final: bool = False,
) -> Path:
    current = root
    for index, component in enumerate(parts):
        current = current / component
        final = index == len(parts) - 1
        if final and allow_missing_final and not current.exists():
            break
        try:
            info = current.lstat()
        except FileNotFoundError:
            if final and allow_missing_final:
                break
            if final:
                raise
            raise HostPathEscape() from None
        if stat.S_ISLNK(info.st_mode) or _is_reparse_point(current):
            raise HostPathEscape()
        if not final and not stat.S_ISDIR(info.st_mode):
            raise HostPathEscape()
    parent = current.parent.resolve(strict=True)
    if not _within(root, parent):
        raise HostPathEscape()
    return current


def _validated_destination_path(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for component in parts[:-1]:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse_point(current):
            raise HostPathEscape()
        if not _within(root, current.resolve(strict=True)):
            raise HostPathEscape()
    destination = current / parts[-1]
    if destination.exists() and _is_reparse_point(destination):
        raise HostPathEscape()
    return destination


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _within(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(root), os.path.normcase(candidate))) == os.path.normcase(root)
    except ValueError:
        return False


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(left) == os.path.normcase(right)


def _translate_escape(exc: OSError) -> None:
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        raise HostPathEscape() from exc


def _sync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
