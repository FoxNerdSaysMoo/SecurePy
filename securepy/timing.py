import multiprocessing
import multiprocessing.pool
import typing as t
from functools import wraps


class TimedFunctionError(Exception):
    def __init__(self, message: str, inner_exception: Exception):
        super().__init__(message)
        self.inner_exception = inner_exception


class TimedFunction:
    """
    This class is used as a decorator to limit function
    execution of the decorated function by specified amount
    of seconds.

    Exceptions:
    - If a function takes more time to execute than the specified
    limit, it will be terminated and `TimeoutError` will be raised.
    - If a function has internall error, `TimedFunctionError` will be raised
    with the internal exception stored: `exc.internal_exception`.

    Return:
    The return value from decorated function will be the same as the
    return value from the original function (unless it raises exception).
    Returning is handeled by `TimedFunction._value_return` which is passed
    the received values from chiled using multiprocessing pipe, sending these
    values is handeled by `TimedFunction._capture_return`.

    Functionality:
    This is achieved by running the given function in a separate process,
    using `multiprocessing` and waiting for the process to finish with `join`,
    which will either get called once the time limit expires, or once the given
    function ends.

    NOTICE: Since the given function is executed from a separate process, certain
    context managers or other things might not work properly, you should consider
    either subclassing this and making a wrapper around given function
    (overriding `__call__`) to use that context manager or passing already-wrapped
    function. If you need to also obtain return values from your context manager,
    you'll need to subclass and override `_capture_return` method and `_value_return`.
    """

    def __init__(self, time_limit: int):
        self.time_limit = time_limit

    def _capture_return(self, func: t.Callable) -> t.Callable:
        """
        Decorate given function and capture it's return value,
        in case an exception happens during it's execution,
        capture it too, after that, send the captured values
        via multiprocessing pipe to the main (parent) process.

        Send values:
            - If exception was raised: tuple: ("exc", exception)
            - Otherwise: tuple: ("ret", return_value)
        The reason behind sending a header "ret"/"exc" is

        NOTICE: You might want to subclass and override this method
        in case you want to return some additional information about
        your function.
        """
        @wraps(func)
        def inner(*args, **kwargs) -> None:
            try:
                ret = func(*args, **kwargs)
            except BaseException as exc:
                self.parent.send(("exc", exc))
                return
            self.parent.send(("ret", ret))
        return inner

    def _value_return(self, ret_info: t.Tuple[t.Literal["exc", "ret"], t.Any], func: t.Callable) -> t.Any:
        """
        This function handles the returning proper values from the already
        finished timed function execution. Return value of this function will
        be used as a return in `TimedFunction.run_timed`.
        Passed `ret_info` is the received tuple sent from `TimedFunction._capture_return`,

        Return values:
            - If tuple starts with "ret", meaning exception wasn't raised,
            return the original return value of the executed function.

            - If tuple starts with "exc", meaning exception was raised,
            return a `TimedFunctionError` with internal representation of this exception
            stored in `TimedFunctionError.inner_exception`.

            This doesn't handle raising of the `TimeoutError`, that is handeled
            internally by `TimedFunction.run_timed`.

        NOTICE: You might want to subclass and override this method
        in case you want to return some additional information about
        your function.
        """
        if ret_info[0] == "ret":
            return ret_info[1]
        elif ret_info[0] == "exc":
            raise TimedFunctionError(
                f"Error occurred while executing timed function `{func.__name__}`, ended with `{type(ret_info[1])}`.",
                inner_exception=ret_info[1]
            )

    def __call__(self, func: t.Callable) -> t.Callable:
        """
        Decorate given function and run it concurrently with a timer,
        using `multiprocessing`. This function simply wraps the original
        as a decorator which will run `TimedFunction.run_timed`
        from where the actual functionality is executed.
        """
        @wraps(func)
        def inner(*args, **kwargs) -> None:
            return self.run_timed(func, args, kwargs)
        return inner

    def run_timed(self, func: t.Callable, args=None, kwargs=None) -> None:
        """
        This is the method which actually handles the logic of running the given
        `func` for some specified time limit, using passed `args` and `kwargs` as
        direct args/kwargs to the original function. This method can be used directly
        or as a part of `__call__` which will run this as a decorator.

        Exceptions:
        - If a function takes more time to execute than the specified
        limit, it will be terminated and `TimeoutError` will be raised.
        - If a function has internall error, `TimedFunctionError` will be raised
        with the internal exception stored: `exc.internal_exception`.

        Return:
        The return value will be the same as the return value from the original
        function (unless it raises exception).
        Returning is handeled by `TimedFunction._value_return` which is passed
        the received values from chiled using multiprocessing pipe, sending these
        values is handeled by `TimedFunction._capture_return`.

        Functionality:
        This is achieved by running the given function in a separate process,
        using `multiprocessing` and waiting for the process to finish with `join`,
        which will either get called once the time limit expires, or once the given
        function ends.
        """
        capturing = self._capture_return(func)
        self.child, self.parent = multiprocessing.Pipe()
        proc = multiprocessing.Process(target=capturing, args=args, kwargs=kwargs)
        proc.start()
        # Wait for `self.time_limit` and join
        # The joining will happen sooner, in case the process ends before the time limit
        proc.join(self.time_limit)

        if proc.is_alive():
            proc.terminate()
            raise TimeoutError(f"Function `{func.__name__}` took longer than the allowed time limit ({self.time_limit})")

        ret_info = self.child.recv()
        return self._value_return(ret_info, func)