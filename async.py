import contextlib
import asyncio
import threading

loop = None


def set_default_loop(l):
    global loop
    loop = l


def call_soon(cb, block=False):
    """
    Submit a Python callable to an event loop; thread-safe.
    """
    if threading.current_thread() is threading.main_thread():
        return cb() if block else loop.call_soon(cb)
    if block:
        mutex = threading.Lock()
        cond = threading.Condition(mutex)
        mutex.acquire()
    result, exception = None, None

    def wrapped_cb():
        nonlocal result, exception
        try:
            result = cb()
        except Exception as e:
            exception = e
        finally:
            if block:
                mutex.acquire()
                cond.notify()
                mutex.release()
    loop.call_soon_threadsafe(wrapped_cb)
    if block:
        cond.wait()
        mutex.release()
    if exception is not None:
        raise exception
    return result


def complete(future, result=None, exception=None, block=False):
    """
    Set completion status (either result or exception) of future; thread-safe.
    If block, return success or failure.
    """
    if future is None:
        return False

    def cb():
        try:
            if exception is not None:
                future.set_exception(exception)
            else:
                future.set_result(result)
            return True
        except asyncio.futures.InvalidStateError:
            return False
    return call_soon(cb, block)


class Fuse:
    """
    A Fuse represents an atomic boolean condition that is initially False and
    may later be triggered.  Calls to listen() and unlisten() register futures
    to become completed if the fuse is triggered -- either in the past or the
    future.  A context manager is available via __call__; it optionally accepts
    an existing Future to register, and otherwise it yields a new Future.  It
    is safe to trigger a fuse more than once; nothing will happen.  An
    optimistic check for the state of the fuse is available via bool().

    The caller of trigger() chooses whether to signal a result or an exception
    on present- and later-registered listeners.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = set()
        self.triggered = False

    def trigger(self, result=None, exception=None, block=True):
        with self.lock:
            if not self.triggered:
                self.triggered = True
                self.result = result
                self.exception = exception
                for f in self.listeners:
                    complete(f, result, exception, block)
                self.listeners.clear()

    def listen(self, f):
        with self.lock:
            if self.triggered:
                complete(f, self.result, self.exception, True)
            else:
                self.listeners.add(f)

    def unlisten(self, f):
        with self.lock:
            try:
                self.listeners.remove(f)
            except KeyError:
                pass

    @contextlib.contextmanager
    def __call__(self, f=None):
        if f is None:
            f = asyncio.Future()
        self.listen(f)
        yield f
        self.unlisten(f)

    def __bool__(self):
        return self.triggered


class FutureStream:
    """
    FutureStream interfaces between an asyncio loop and another asynchronous
    source of results/exceptions.  Calls to claim() return futures which yield
    the results/exceptions passed to post() in order.  Calling set_exception()
    causes all pending and subsequent claim futures to complete with the
    provided exception.
    """
    def __init__(self, futureFactory=asyncio.Future):
        self.lock = threading.Lock()
        self.factory = futureFactory
        self.early = []  # FIFO of futures: claims that arrived before posts
        self.late = []   # FIFO of futures: posts that arrived before claims
        self.exception = None

    def new_future(self):
        """ Caller must hold self.lock. """
        x = self.factory()
        if self.exception is not None:
            x.set_exception(self.exception)
        return x

    def set_exception(self, exception):
        with self.lock:
            self.exception = exception
            for f in self.early:
                complete(f, exception=exception)
            self.early.clear()

    def claim(self):
        with self.lock:
            if self.late:
                x = self.late[0]
                del self.late[0]
            else:
                x = self.new_future()
                if not x.done():
                    self.early.append(x)
        return x

    def post(self, result=None, exception=None):
        with self.lock:
            while self.early:
                x = self.early[0]
                del self.early[0]
                if complete(x, result, exception, True):
                    break
            else:
                if (exception is not None) or (self.exception is None):
                    x = self.new_future()
                    complete(x, result, exception, True)
                    self.late.append(x)
                else:
                    # results posted to a closed future stream are lost
                    raise self.exception


class KeyedEvent:
    """
    KeyedEvent represents a multimap of listening futures.  A context manager
    is available via __call__; it optionally accepts an existing Future to
    register, and otherwise it yields a new Future.
    """
    def __init__(self):
        self.d = {}

    def trigger(self, key, result=None, exception=None):
        for l in self.d.get(key, ()):
            complete(l, result, exception)

    def listen(self, key, f):
        s = self.d.setdefault(key, set())
        s.add(f)

    def unlisten(self, key, f):
        self.d[key].remove(f)

    @contextlib.contextmanager
    def __call__(self, key, f=None):
        if f is None:
            f = asyncio.Future()
        self.listen(key, f)
        yield f
        self.unlisten(key, f)
