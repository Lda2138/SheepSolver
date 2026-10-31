"""Use the native current-user certificate store, without a shell or UI."""
import ctypes
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def api():
    dll = ctypes.WinDLL('crypt32', use_last_error=True)
    dll.CertOpenSystemStoreW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    dll.CertOpenSystemStoreW.restype = ctypes.c_void_p
    dll.CertOpenStore.argtypes = [ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    dll.CertOpenStore.restype = ctypes.c_void_p
    dll.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    dll.CertFindCertificateInStore.argtypes = [ctypes.c_void_p, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    dll.CertFindCertificateInStore.restype = ctypes.c_void_p
    dll.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    dll.CertDeleteCertificateFromStore.argtypes = [ctypes.c_void_p]
    dll.CertAddEncodedCertificateToStore.argtypes = [ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    return dll


def find(dll, store, thumb):
    digest = bytes.fromhex(thumb)
    if len(digest) != 20:
        raise ValueError('Expected a SHA-1 certificate fingerprint')
    array = (ctypes.c_ubyte * len(digest)).from_buffer_copy(digest)
    blob = Blob(len(digest), array)
    return dll.CertFindCertificateInStore(store, 0x10001, 0, 0x10000, ctypes.byref(blob), None)


def store_operation(action, thumb, der=None):
    dll = api()
    if action in ('add','remove'):
        # The user already approved this exact temporary CA. The default ROOT
        # provider can open a confirmation dialog in a pythonw background job.
        # This flag suppresses that dialog for this store handle only; it does
        # not change system policy or the scope (current user, this CA only).
        name=ctypes.c_wchar_p('ROOT')
        store=dll.CertOpenStore(ctypes.c_void_p(10),0,None,0x40014000,name)
    else:
        store = dll.CertOpenSystemStoreW(None, 'ROOT')
    if not store:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if action == 'add':
            buffer = ctypes.create_string_buffer(der)
            if not dll.CertAddEncodedCertificateToStore(store, 0x10001, buffer, len(der), 2, None):
                raise ctypes.WinError(ctypes.get_last_error())
        context = find(dll, store, thumb)
        if action == 'remove' and context:
            # Delete consumes and frees this exact context, including on failure.
            if not dll.CertDeleteCertificateFromStore(context):
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        if context:
            dll.CertFreeCertificateContext(context)
        return bool(context)
    finally:
        dll.CertCloseStore(store, 0)


def trusted(thumb):
    return store_operation('find', thumb)


def add(thumb, der):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    if x509.load_der_x509_certificate(der).fingerprint(hashes.SHA1()).hex().upper() != thumb.upper():
        raise ValueError('Certificate fingerprint mismatch')
    store_operation('add', thumb, der)
    if not trusted(thumb):
        raise RuntimeError('当前用户证书写入后未能确认，代理未启用')


def remove(thumb):
    store_operation('remove', thumb)
    if trusted(thumb):
        raise RuntimeError('当前用户临时证书移除后仍存在')
