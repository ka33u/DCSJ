"""One resource budget shared by interactive calculations and background work."""
import threading
from .engine import calculate_design

_slots = threading.BoundedSemaphore(2)


class BusyError(RuntimeError):
    pass


def compute(parameters, wait=False, stop_event=None, *, refine_torque=False, continuous_torque=False):
    while not _slots.acquire(blocking=False):
        if not wait:
            raise BusyError("计算资源已占满，请稍后重试")
        if stop_event is not None and stop_event.wait(.2):
            raise BusyError("计算任务已停止")
        if stop_event is None:
            raise BusyError("后台等待必须提供取消信号")
    try:
        if stop_event is not None and stop_event.is_set():
            raise BusyError("计算任务已停止")
        return calculate_design(parameters, **({'refine_torque': True} if refine_torque else {}),
                                **({'continuous_torque': True} if continuous_torque else {}))
    finally:
        _slots.release()
