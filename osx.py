import objc
import types, ctypes

"""
Load the IOBluetooth framework.
"""
IOBluetooth = types.ModuleType('IOBluetooth')
objc.loadBundle('IOBluetooth', IOBluetooth.__dict__, '/System/Library/Frameworks/IOBluetooth.framework')

"""
Configure foreign function interface for Grand Central Dispatch.
"""
libSystem = ctypes.CDLL('libSystem.dylib')
dispatch_function_t = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
dispatch_queue_t = objc.createOpaquePointerType('dispatch_queue_t', b'^{dispatch_queue_s=}')
dispatch_time_t = ctypes.c_uint64
DISPATCH_SOURCE_TYPE_TIMER = ctypes.cast(libSystem._dispatch_source_type_timer, ctypes.c_void_p)
libSystem.dispatch_get_global_queue.argtypes = (ctypes.c_long, ctypes.c_ulong)
libSystem.dispatch_get_global_queue.restype = ctypes.c_void_p
libSystem.dispatch_queue_create.argtypes = (ctypes.c_char_p, ctypes.c_void_p)
libSystem.dispatch_queue_create.restype = ctypes.c_void_p
libSystem.dispatch_async_f.argtypes = (ctypes.c_void_p, ctypes.c_void_p, dispatch_function_t)
libSystem.dispatch_async_f.restype = None
libSystem.dispatch_source_set_event_handler_f.argtypes = (ctypes.c_void_p, dispatch_function_t)
libSystem.dispatch_source_set_event_handler_f.restype = None
libSystem.dispatch_source_create.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)
libSystem.dispatch_source_create.restype = ctypes.c_void_p
libSystem.dispatch_source_cancel.argtypes = (ctypes.c_void_p,)
libSystem.dispatch_source_cancel.restype = ctypes.c_void_p
libSystem.dispatch_source_set_timer.argtypes = (ctypes.c_void_p, dispatch_time_t, ctypes.c_uint64, ctypes.c_uint64)
libSystem.dispatch_source_set_timer.restype = None
libSystem.dispatch_source_cancel.argtypes = (ctypes.c_void_p,)
libSystem.dispatch_source_cancel.restype = None
libSystem.dispatch_time.argtypes = (dispatch_time_t, ctypes.c_int64)
libSystem.dispatch_time.restype = dispatch_time_t
libSystem.dispatch_release.argtypes = (ctypes.c_void_p,)
libSystem.dispatch_release.restype = None
libSystem.dispatch_resume.argtypes = (ctypes.c_void_p,)
libSystem.dispatch_resume.restype = None
DISPATCH_TIME_NOW = dispatch_time_t(0)
NSEC_PER_SEC = 1000000000

class DispatchTimer:
    """
    Encapsulate a timer-type dispatch source.
    """
    def __init__(self, interval, queue, func):
        self.timer = libSystem.dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, queue.__c_void_p__())
        self.callback = dispatch_function_t(func)
        if self.timer:
            libSystem.dispatch_source_set_timer(
                self.timer,
                libSystem.dispatch_time(DISPATCH_TIME_NOW, interval * NSEC_PER_SEC),
                interval * NSEC_PER_SEC,
                int(NSEC_PER_SEC / 10)
            )
            libSystem.dispatch_source_set_event_handler_f(self.timer, self.callback)
            libSystem.dispatch_resume(self.timer)
    def __del__(self):
        libSystem.dispatch_source_cancel(self.timer)
        libSystem.dispatch_release(self.timer)

def dispatch_async(queue, func):
    """
    Submit a Python callable to a dispatch queue.
    """
    cb = None
    @dispatch_function_t
    def cb(context):
        cb # close over the function pointer object to extend its lifetime
        func()
    libSystem.dispatch_async_f(queue.__c_void_p__(), None, cb)

def dispatch_get_global_queue(identifier, flags):
    return objc.objc_object(c_void_p=libSystem.dispatch_get_global_queue(identifier, flags))

def dispatch_queue_from_id(queue):
    return dispatch_queue_t(c_void_p=queue.__c_void_p__())

QOS_CLASS_USER_INTERACTIVE = 0x21
QOS_CLASS_USER_INITIATED = 0x19
QOS_CLASS_DEFAULT = 0x15
QOS_CLASS_UTILITY = 0x11
QOS_CLASS_BACKGROUND = 0x09
QOS_CLASS_UNSPECIFIED = 0x00

def setBluetoothPowerState(value=1):
    prefs = IOBluetooth.IOBluetoothPreferences.alloc().init()
    prefs.setPoweredOn_(value)
